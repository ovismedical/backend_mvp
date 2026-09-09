"""The inference gateway: decide, leak-check, run, audit. Every model call goes through here.

`complete()`:
  1. asks the `Router` whether the task may run (policy flags, scrubbed marker,
     provider configured and healthy) and raises `InferenceRefused` if not;
  2. runs the scrubber's leak check over the outbound messages when the call site
     supplied `known_identifiers`, refusing with `leak_check_failed` on a hit;
  3. calls the provider, feeding the circuit breaker (`Health`);
  4. writes a content-free audit line and an `AuditEvent` to the sink.

`ProviderUnavailable` (nothing configured at all) is kept distinct from a refusal
so call sites can keep today's "skipped" behaviour for the no-key deployment.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .audit import AuditEvent, AuditSink, NullAuditSink, scrub_summary
from .providers import InferenceProvider, providers_from_env
from .router import REASON_LEAK_CHECK_FAILED, REASON_PROVIDER_UNCONFIGURED, Decision, Health, Policy, Router

logger = logging.getLogger("ovis.inference")

TASKS = ("chat_turn", "symptom_assessment", "triage", "pii_detect")

# Env var per task; value is a provider name. Overrides the YAML policy's `tasks.<task>.provider`
# only when set and non-empty. Constructor `routes` override both.
ROUTE_ENV = {
    "chat_turn": "INFERENCE_ROUTE_CHAT",
    "symptom_assessment": "INFERENCE_ROUTE_ASSESSMENT",
    "triage": "INFERENCE_ROUTE_TRIAGE",
}

# Optional per-task model override for the "openai" provider (Azure: deployment names).
# e.g. OPENAI_TRIAGE_MODEL=gpt-5 while OPENAI_MODEL=gpt-5-mini serves chat + assessment.
MODEL_ENV = {
    "chat_turn": "OPENAI_CHAT_MODEL",
    "symptom_assessment": "OPENAI_ASSESSMENT_MODEL",
    "triage": "OPENAI_TRIAGE_MODEL",
}


class ProviderUnavailable(RuntimeError):
    """No provider is configured at all (fallback mode)."""


class InferenceRefused(RuntimeError):
    """The routing policy refused this call. `.decision` says why and what the call site should do."""

    def __init__(self, decision: Decision):
        super().__init__(
            f"inference refused: task={decision.task} provider={decision.provider} reason={decision.reason}"
        )
        self.decision = decision

    @property
    def reason(self) -> str:
        return self.decision.reason

    @property
    def on_refuse(self) -> str | None:
        return self.decision.on_refuse


@dataclass
class InferenceRequest:
    task: str
    messages: list[dict] = field(repr=False)
    instructions: str | None = field(default=None, repr=False)
    schema: type[BaseModel] | None = None
    language: str = "en"
    effort: str = "low"          # reasoning effort for reasoning models
    temperature: float = 0.7     # for non-reasoning models
    metadata: dict[str, Any] = field(default_factory=dict)  # opaque refs only (patient_ref, session_ref, task_source)
    scrubbed: bool = False       # the call site de-identified `messages`; required by providers with `requires: [scrubbed]`
    # Whole identifier strings the scrubber should have removed; the gateway's leak check refuses if any
    # still appears in `messages`. Never logged, never audited, never sent anywhere.
    known_identifiers: list[str] | None = field(default=None, repr=False)
    # ScrubReport (or dict) from the call site; only counts/linkage_score/ner_backend reach the audit event.
    scrub_report: Any = field(default=None, repr=False)

    def __post_init__(self):
        if self.task not in TASKS:
            raise ValueError(f"Unknown inference task {self.task!r}; expected one of {TASKS}")


@dataclass
class InferenceResult:
    provider: str
    model: str
    latency_ms: int
    text: str | None = None
    parsed: BaseModel | None = None
    audit_id: Any = None         # id returned by the audit sink, for post-hoc annotate(); None for NullAuditSink


def routes_from_env() -> dict[str, str]:
    """Only the tasks whose INFERENCE_ROUTE_* var is set and non-empty; everything else follows the YAML policy."""
    return {task: value for task, env_var in ROUTE_ENV.items() if (value := (os.getenv(env_var) or "").strip())}


def task_models_from_env() -> dict[str, dict[str, str]]:
    """{provider_name: {task: model}} overrides; only the openai provider is env-configurable today."""
    overrides = {task: os.getenv(env_var) for task, env_var in MODEL_ENV.items()}
    overrides = {task: model for task, model in overrides.items() if model}
    return {"openai": overrides} if overrides else {}


class InferenceGateway:
    def __init__(self, providers: dict[str, InferenceProvider], routes: dict[str, str] | None = None,
                 task_models: dict[str, dict[str, str]] | None = None, router: Router | None = None,
                 audit_sink: AuditSink | None = None):
        self.providers = providers
        # Provider precedence per task: constructor routes > INFERENCE_ROUTE_* (set, non-empty) > YAML policy.
        self.routes = dict(routes) if routes is not None else routes_from_env()
        self.task_models = task_models if task_models is not None else task_models_from_env()
        self.router = router if router is not None else Router(Policy.load())
        self.audit_sink: AuditSink = audit_sink if audit_sink is not None else NullAuditSink()
        self.health = Health()

    def model_for(self, provider: InferenceProvider, task: str) -> str:
        return self.task_models.get(provider.name, {}).get(task) or provider.model

    # -- routing -----------------------------------------------------------
    def available(self) -> bool:
        return bool(self.providers)

    def effective_provider(self, task: str) -> str | None:
        """Provider name a task resolves to after overrides (may be unconfigured or None)."""
        return self.routes.get(task) or self.router.policy.provider_for(task)

    def effective_routes(self) -> dict[str, str | None]:
        tasks = list(TASKS) + [t for t in (*self.router.policy.tasks, *self.routes) if t not in TASKS]
        return {task: self.effective_provider(task) for task in dict.fromkeys(tasks)}

    def decide(self, task: str, *, scrubbed: bool = True) -> Decision:
        """The policy decision for `task` as things stand now (used by /health and by complete())."""
        return self.router.decide(task, scrubbed=scrubbed, providers=self.providers, health=self.health,
                                  provider=self.routes.get(task))

    def policy_warnings(self) -> list[str]:
        return self.router.warnings(self.effective_routes())

    def log_policy_state(self) -> list[str]:
        """Emit the rollout warnings (e.g. dpa_ok=false) at WARNING; called from the app lifespan."""
        warnings = self.policy_warnings()
        for message in warnings:
            logger.warning(message)
        return warnings

    # -- execution ---------------------------------------------------------
    async def complete(self, request: InferenceRequest) -> InferenceResult:
        if not self.providers:
            raise ProviderUnavailable(f"No inference provider configured for task {request.task!r}")

        started = time.perf_counter()
        decision = self.decide(request.task, scrubbed=request.scrubbed)
        provider = self.providers.get(decision.provider) if decision.provider else None
        model = self.model_for(provider, request.task) if provider is not None else None

        if decision.allow and provider is None:  # defensive: the router only allows configured providers
            decision = decision.refused(REASON_PROVIDER_UNCONFIGURED)
        if decision.allow and self._leaks(request):
            decision = decision.refused(REASON_LEAK_CHECK_FAILED)
        if not decision.allow or provider is None or model is None:
            self._audit(request, decision, provider_name=decision.provider, model=model, outcome="refused", started=started)
            raise InferenceRefused(decision)

        outcome = "ok"
        audit_id = None
        try:
            if request.schema is not None:
                parsed = await provider.parse(request, model=model)
                result = InferenceResult(provider=provider.name, model=model, latency_ms=_ms(started), parsed=parsed)
            else:
                text = await provider.chat(request, model=model)
                result = InferenceResult(provider=provider.name, model=model, latency_ms=_ms(started), text=text)
            self.health.record_success(provider.name)
        except Exception as e:
            outcome = f"error:{type(e).__name__}"
            self.health.record_failure(provider.name)
            raise
        finally:
            audit_id = self._audit(request, decision, provider_name=provider.name, model=model, outcome=outcome, started=started)
        result.audit_id = audit_id
        return result

    def _leaks(self, request: InferenceRequest) -> bool:
        """Defence in depth: refuse if a known identifier survived scrubbing. Content never leaves this method."""
        if not request.known_identifiers:
            return False
        try:
            from .scrub import leak_check  # lazy: the scrubber package ships separately
        except ImportError:
            logger.warning("inference leak check skipped: app.inference.scrub is not available")
            return False
        try:
            found = leak_check(request.messages, request.known_identifiers)
        except Exception as e:  # noqa: BLE001 - fail closed: an unverifiable request does not leave the building
            logger.warning("inference leak check raised %s task=%s; refusing", type(e).__name__, request.task)
            return True
        if found:
            logger.warning("inference leak check failed task=%s identifiers=%d", request.task, len(found))
            return True
        return False

    def _audit(self, request: InferenceRequest, decision: Decision, *, provider_name: str | None, model: str | None,
               outcome: str, started: float) -> Any:
        """Content-free audit line + sink event: what ran where and why, never what was said."""
        latency_ms = _ms(started)
        verdict = "allow" if decision.allow else "refuse"
        log = logger.info if decision.allow else logger.warning
        log(
            "inference task=%s provider=%s model=%s lang=%s msgs=%d decision=%s reason=%s outcome=%s latency_ms=%d %s",
            request.task, provider_name, model, request.language, len(request.messages),
            verdict, decision.reason, outcome, latency_ms,
            " ".join(f"{k}={v}" for k, v in request.metadata.items()),
        )
        event = AuditEvent(
            task=request.task, decision=verdict, reason=decision.reason, outcome=outcome,
            provider=provider_name, model=model, latency_ms=latency_ms, lang=request.language,
            msgs=len(request.messages),
            patient_ref=_opaque(request.metadata.get("patient_ref")),
            session_ref=_opaque(request.metadata.get("session_ref")),
            task_source=_opaque(request.metadata.get("task_source")),
            scrub=scrub_summary(request.scrub_report),
        )
        try:
            return self.audit_sink.record(event)
        except Exception as e:  # noqa: BLE001 - a sink must never fail the call
            logger.warning("audit sink raised %s; event dropped", type(e).__name__)
            return None

    def describe(self) -> dict:
        """Operator summary for the startup log and /florence/test. No URLs, no secrets, no content."""
        health = self.health.snapshot(self.providers)
        return {
            "providers": {name: {"model": p.model, "healthy": health[name]} for name, p in self.providers.items()},
            "routes": {
                task: {"provider": name, "model": self.model_for(self.providers[name], task) if name in self.providers else None}
                for task, name in self.effective_routes().items()
            },
            "policy": self.router.policy.describe(),
        }


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _opaque(value: Any) -> str | None:
    return None if value is None else str(value)


_gateway: InferenceGateway | None = None


def get_gateway() -> InferenceGateway:
    global _gateway
    if _gateway is None:
        _gateway = InferenceGateway(providers_from_env())
    return _gateway


def reset_gateway(gateway: InferenceGateway | None = None) -> None:
    """Replace (or clear) the singleton; used by tests and by settings reloads."""
    global _gateway
    _gateway = gateway
