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

import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from typing import Any
from collections.abc import Callable

from pydantic import BaseModel

from .audit import AuditEvent, AuditSink, NullAuditSink, scrub_summary
from .providers import InferenceProvider, providers_from_env
from .router import (
    REASON_LEAK_CHECK_FAILED, REASON_PROVIDER_UNCONFIGURED, REASON_TOOL_NOT_REGISTERED,
    REASON_TOOL_OUTPUT_UNSCRUBBED, Decision, Health, Policy, Router,
)
from .tools import ToolArgumentError, ToolCall, ToolRegistry

logger = logging.getLogger("ovis.inference")

TASKS = ("chat_turn", "symptom_assessment", "triage", "pii_detect", "memory_extraction")


@dataclass(frozen=True)
class TaskProfile:
    """What a task is allowed to cost. One table so the tiering is answerable in one place instead
    of being three magic strings in three call sites.

    The split that matters: `chat_turn` runs on every patient message with someone waiting on it,
    so it stays at the cheapest effort and is capped to a single tool hop. `triage` runs once per
    session in the background where nobody is watching, so it is allowed to think. Per-task *models*
    are separate and come from the OPENAI_*_MODEL env vars (see MODEL_ENV).
    """
    effort: str
    max_tool_hops: int = 0          # 0 = this task never calls tools
    text_ends_turn: bool = False


CHEAP_EFFORTS = frozenset({"minimal", "none", "low"})

TASK_PROFILES = {
    # `low`, not `none`: the chat turn has to judge severity, keep to one question at a time and
    # decide whether to record, all in the same call, and none of that survives a zero reasoning
    # budget. Still the cheap tier. (Note `minimal` is not an option here - gpt-5.6-sol rejects it
    # and the provider negotiates it away, paying a failed round trip per process to find out.)
    "chat_turn":          TaskProfile(effort="low", max_tool_hops=1, text_ends_turn=True),
    "symptom_assessment": TaskProfile(effort="low"),
    "triage":             TaskProfile(effort="high"),
    "pii_detect":         TaskProfile(effort="minimal"),
    # Background, once per check-in, and a wrong note is only ever a missed or dropped one.
    "memory_extraction":  TaskProfile(effort="low"),
}

# Languages the product offers; anything else is audited as "other" (never the raw value).
SUPPORTED_LANGUAGES = ("en", "zh-HK")

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
    "memory_extraction": "OPENAI_MEMORY_MODEL",
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
    effort: str | None = None    # reasoning effort; None takes the task's TaskProfile value
    temperature: float = 0.7     # for non-reasoning models
    metadata: dict[str, Any] = field(default_factory=dict)  # opaque refs only (patient_ref, session_ref, task_source)
    scrubbed: bool = False       # the call site de-identified `messages`; required by providers with `requires: [scrubbed]`
    # Whole identifier strings the scrubber should have removed; the gateway's leak check refuses if any
    # still appears in `messages`. Never logged, never audited, never sent anywhere.
    known_identifiers: list[str] | None = field(default=None, repr=False)
    # ScrubReport (or dict) from the call site; only counts/linkage_score/ner_backend reach the audit event.
    scrub_report: Any = field(default=None, repr=False)
    # Number of trailing messages that are static, non-transcript prompt text supplied by the call
    # site and therefore exempt from the leak check: a session original that also occurs in a prompt
    # template (醫生, 翻譯) must not refuse the call.
    trusted_tail: int = 0
    # Tools this call may use. The policy's `tools` block still has to allow each one.
    tools: ToolRegistry | None = None
    tool_choice: str | None = "auto"
    max_tool_hops: int | None = None    # None takes the task's TaskProfile value
    # When the model answers and calls tools in the same response, run the tools and keep that
    # answer rather than paying a second round trip. Safe only for write tools whose result the
    # model does not need to read - which is exactly the chat path's coverage tools.
    text_ends_turn: bool | None = None
    # Scrubs one tool result before it re-enters the model's context. Required for any tool the
    # policy marks `discloses: stored_phi`; call sites pass a closure over the session ScrubContext
    # so a name in a tool result becomes the same token it already has in the transcript.
    scrub_tool_output: Callable[[str], str] | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.task not in TASKS:
            raise ValueError(f"Unknown inference task {self.task!r}; expected one of {TASKS}")
        profile = TASK_PROFILES[self.task]
        if self.effort is None:
            self.effort = profile.effort
        if self.max_tool_hops is None:
            self.max_tool_hops = profile.max_tool_hops
        if self.text_ends_turn is None:
            self.text_ends_turn = profile.text_ends_turn


@dataclass
class InferenceResult:
    provider: str
    model: str
    latency_ms: int
    text: str | None = None
    parsed: BaseModel | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    # The function_call / function_call_output items the loop appended, in order. Call sites
    # persist these so the next turn can replay the model's own call history.
    tool_items: tuple[dict, ...] = ()
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
        return self.router.warnings(self.effective_routes(), providers=self.providers)

    def cost_profile(self) -> dict:
        """What each task costs right now: its model, its effort, its tool-hop cap.

        Model tiering is opt-in infrastructure, not a target state: point OPENAI_CHAT_MODEL (or
        ASSESSMENT / TRIAGE) at a smaller deployment and the split takes effect with no code change.
        Running every task on one deployment is a legitimate configuration, so `model_tiering`
        reports it as a fact for /health rather than warning about it on every boot.
        """
        tasks: dict[str, dict] = {}
        for task, provider_name in self.effective_routes().items():
            if task not in TASK_PROFILES:
                continue
            provider = self.providers.get(provider_name) if provider_name else None
            profile = TASK_PROFILES[task]
            tasks[task] = {
                "provider": provider_name,
                "model": self.model_for(provider, task) if provider is not None else None,
                "effort": profile.effort,
                "max_tool_hops": profile.max_tool_hops,
                "tier": "cheap" if profile.effort in CHEAP_EFFORTS else "deep",
                "model_env": MODEL_ENV.get(task),
            }
        models = {t["model"] for t in tasks.values() if t["model"]}
        tiers = {t["tier"] for t in tasks.values()}
        return {
            "tasks": tasks,
            # "split" once a cheap task runs somewhere other than a deep one; "shared" while one
            # deployment serves them all, which is what a single-deployment resource looks like.
            "model_tiering": "shared" if len(models) <= 1 and len(tiers) > 1 else "split",
        }

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

        if request.tools:
            return await self._with_tools(request, provider, model, decision, started)
        return await self._once(request, provider, model, decision, started)

    async def _once(self, request: InferenceRequest, provider, model: str, decision: Decision,
                    started: float) -> InferenceResult:
        """One model call, no tools: the original path."""
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

    async def _with_tools(self, request: InferenceRequest, provider, model: str, decision: Decision,
                          started: float) -> InferenceResult:
        """Bounded tool loop, owned by the gateway so every hop is leak-checked and audited.

        Each hop appends the model's calls and their results to the working message list, which is
        leak-checked again before it goes back out. The last hop is offered no tools, so the model
        has to answer in text and the loop provably terminates.
        """
        working = list(request.messages)
        appended = 0
        last_hop = max(0, int(request.max_tool_hops))
        for hop in range(last_hop + 1):
            final = hop == last_hop
            hop_request = replace(request, messages=working, tools=None if final else request.tools)
            hop_started = started if hop == 0 else time.perf_counter()
            if hop and self._leaks(hop_request):
                refused = decision.refused(REASON_LEAK_CHECK_FAILED)
                self._audit(hop_request, refused, provider_name=provider.name, model=model,
                            outcome="refused", started=hop_started, hop=hop)
                raise InferenceRefused(refused)

            outcome = "ok"
            audit_id = None
            try:
                text, calls = await provider.chat_with_tools(hop_request, model=model)
                self.health.record_success(provider.name)
            except Exception as e:
                outcome = f"error:{type(e).__name__}"
                self.health.record_failure(provider.name)
                raise
            finally:
                audit_id = self._audit(hop_request, decision, provider_name=provider.name, model=model,
                                       outcome=outcome, started=hop_started, hop=hop)

            def settled() -> InferenceResult:
                return InferenceResult(provider=provider.name, model=model, latency_ms=_ms(started),
                                       text=text, tool_calls=tuple(calls), audit_id=audit_id,
                                       tool_items=tuple(working[len(working) - appended:]) if appended else ())

            if not calls:
                return settled()
            if final:
                # The last hop was offered no tools, so a call here is a protocol violation: the
                # model is answering a question it was not asked. Do not act on it.
                logger.warning("inference task=%s hop cap reached with %d unresolved calls",
                               request.task, len(calls))
                return settled()

            for call in calls:
                working.append({"type": "function_call", "call_id": call.call_id,
                                "name": call.name, "arguments": call.arguments})
                working.append({"type": "function_call_output", "call_id": call.call_id,
                                "output": self._run_tool(request, call, decision, provider, model, hop)})
                appended += 2

            if text and request.text_ends_turn:
                # It answered and recorded in the same breath. The results are appended (so the next
                # turn replays them) but nothing here needs reading back, so the turn is done and a
                # recording turn costs the same single round trip as any other.
                return settled()
        raise AssertionError("tool loop did not terminate")  # unreachable: the final hop always returns

    def _run_tool(self, request: InferenceRequest, call: ToolCall, decision: Decision, provider,
                  model: str, hop: int) -> str:
        """Run one call and return the JSON text the model sees.

        A tool the policy or the registry does not name is refused and never executed. Arguments the
        executor cannot use come back to the model as a typed error, which costs a hop but keeps the
        patient's turn alive.
        """
        started = time.perf_counter()
        spec = request.tools.get(call.name) if request.tools else None
        verdict = self.router.decide_tool(call.name, scrubbed=request.scrubbed, task=request.task,
                                          provider=decision.provider, on_refuse=decision.on_refuse)
        if verdict.allow and spec is None:
            verdict = verdict.refused(REASON_TOOL_NOT_REGISTERED)
        declared = self.router.policy.tools.get(call.name)
        if verdict.allow and declared is not None and declared.reads_stored_phi and request.scrub_tool_output is None:
            # `discloses: stored_phi` is load-bearing: a tool that reads patient data may not run
            # unless the call site gave us a way to scrub what it returns.
            verdict = verdict.refused(REASON_TOOL_OUTPUT_UNSCRUBBED)
        if not verdict.allow:
            self._audit(request, verdict, provider_name=provider.name, model=model, outcome="refused",
                        started=started, tool=call.name, hop=hop)
            raise InferenceRefused(verdict)

        outcome = "ok"
        try:
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                raise ToolArgumentError("arguments were not valid JSON")
            if not isinstance(args, dict):
                raise ToolArgumentError("arguments must be a JSON object")
            text = json.dumps(spec.executor(args), ensure_ascii=False, default=str)
        except ToolArgumentError as e:
            outcome = "error:ToolArgumentError"
            text = json.dumps({"error": str(e)}, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001 - a broken tool must not end the patient's turn
            outcome = f"error:{type(e).__name__}"
            logger.warning("tool %s raised %s task=%s", call.name, type(e).__name__, request.task)
            text = json.dumps({"error": "the tool could not run"}, ensure_ascii=False)
        finally:
            self._audit(request, verdict, provider_name=provider.name, model=model, outcome=outcome,
                        started=started, tool=call.name, hop=hop)

        if request.scrub_tool_output is not None:
            text = request.scrub_tool_output(text)
        return text

    def _leaks(self, request: InferenceRequest) -> bool:
        """Defence in depth: refuse if a known identifier survived scrubbing. Content never leaves this method."""
        if not request.known_identifiers:
            return False
        try:
            from .scrub import leak_check  # lazy: keeps `import app.inference` free of the scrubber
        except ImportError:
            logger.warning("inference leak check unavailable: app.inference.scrub did not import; refusing")
            return True
        try:
            found = leak_check(self._checked_messages(request), request.known_identifiers)
        except Exception as e:  # noqa: BLE001 - fail closed: an unverifiable request does not leave the building
            logger.warning("inference leak check raised %s task=%s; refusing", type(e).__name__, request.task)
            return True
        if found:
            logger.warning("inference leak check failed task=%s identifiers=%d", request.task, len(found))
            return True
        return False

    @staticmethod
    def _checked_messages(request: InferenceRequest) -> list[dict]:
        """The outbound messages the leak check looks at: everything except a trusted tail of
        static prompt text the call site appended itself."""
        tail = max(0, min(int(request.trusted_tail or 0), len(request.messages)))
        return request.messages[:len(request.messages) - tail] if tail else request.messages

    def _audit(self, request: InferenceRequest, decision: Decision, *, provider_name: str | None, model: str | None,
               outcome: str, started: float, tool: str | None = None, hop: int | None = None) -> Any:
        """Content-free audit line + sink event: what ran where and why, never what was said.

        `tool` is a declared tool name and `hop` the loop index, so a refused tool call is a visible
        row of its own rather than being invisible inside a turn that audits as allowed. Tool
        arguments and results never reach this method."""
        latency_ms = _ms(started)
        verdict = "allow" if decision.allow else "refuse"
        log = logger.info if decision.allow else logger.warning
        # Only the two languages the product speaks reach the log line and the audit document: a
        # free-text `language` would put patient content (and newlines) into the audit trail.
        lang = request.language if request.language in SUPPORTED_LANGUAGES else "other"
        log(
            "inference task=%s provider=%s model=%s lang=%s msgs=%d decision=%s reason=%s outcome=%s "
            "latency_ms=%d%s%s %s",
            request.task, provider_name, model, lang, len(request.messages),
            verdict, decision.reason, outcome, latency_ms,
            f" tool={tool}" if tool else "", f" hop={hop}" if hop is not None else "",
            " ".join(f"{k}={v}" for k, v in request.metadata.items()),
        )
        event = AuditEvent(
            task=request.task, decision=verdict, reason=decision.reason, outcome=outcome,
            provider=provider_name, model=model, latency_ms=latency_ms, lang=lang,
            msgs=len(request.messages),
            patient_ref=_opaque(request.metadata.get("patient_ref")),
            session_ref=_opaque(request.metadata.get("session_ref")),
            task_source=_opaque(request.metadata.get("task_source")),
            tool=tool, hop=hop,
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
            "cost": self.cost_profile(),
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
