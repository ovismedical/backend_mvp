"""The inference gateway: route a request to a provider, run it, audit it."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .providers import InferenceProvider, providers_from_env

logger = logging.getLogger("ovis.inference")

TASKS = ("chat_turn", "symptom_assessment", "triage")

# Env var per task; value is a provider name. Later replaced by a policy engine.
ROUTE_ENV = {
    "chat_turn": "INFERENCE_ROUTE_CHAT",
    "symptom_assessment": "INFERENCE_ROUTE_ASSESSMENT",
    "triage": "INFERENCE_ROUTE_TRIAGE",
}
DEFAULT_ROUTE = "openai"

# Optional per-task model override for the "openai" provider (Azure: deployment names).
# e.g. OPENAI_TRIAGE_MODEL=gpt-5 while OPENAI_MODEL=gpt-5-mini serves chat + assessment.
MODEL_ENV = {
    "chat_turn": "OPENAI_CHAT_MODEL",
    "symptom_assessment": "OPENAI_ASSESSMENT_MODEL",
    "triage": "OPENAI_TRIAGE_MODEL",
}


class ProviderUnavailable(RuntimeError):
    """No provider is configured (or healthy) for this task."""


@dataclass
class InferenceRequest:
    task: str
    messages: list[dict]
    instructions: str | None = None
    schema: type[BaseModel] | None = None
    language: str = "en"
    effort: str = "low"          # reasoning effort for reasoning models
    temperature: float = 0.7     # for non-reasoning models
    metadata: dict[str, Any] = field(default_factory=dict)  # ids only, never content

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


def routes_from_env() -> dict[str, str]:
    return {task: (os.getenv(env_var) or DEFAULT_ROUTE) for task, env_var in ROUTE_ENV.items()}


def task_models_from_env() -> dict[str, dict[str, str]]:
    """{provider_name: {task: model}} overrides; only the openai provider is env-configurable today."""
    overrides = {task: os.getenv(env_var) for task, env_var in MODEL_ENV.items()}
    overrides = {task: model for task, model in overrides.items() if model}
    return {"openai": overrides} if overrides else {}


class InferenceGateway:
    def __init__(self, providers: dict[str, InferenceProvider], routes: dict[str, str] | None = None,
                 task_models: dict[str, dict[str, str]] | None = None):
        self.providers = providers
        self.routes = routes or routes_from_env()
        self.task_models = task_models if task_models is not None else task_models_from_env()

    def model_for(self, provider: InferenceProvider, task: str) -> str:
        return self.task_models.get(provider.name, {}).get(task) or provider.model

    # -- routing -----------------------------------------------------------
    def available(self) -> bool:
        return bool(self.providers)

    def provider_for(self, task: str) -> InferenceProvider:
        wanted = self.routes.get(task, DEFAULT_ROUTE)
        provider = self.providers.get(wanted)
        if provider is None and self.providers:
            # Configured route isn't available; fall back to whatever is, but say so.
            fallback_name, provider = next(iter(self.providers.items()))
            logger.warning("inference route %s -> %s unavailable, falling back to %s", task, wanted, fallback_name)
        if provider is None:
            raise ProviderUnavailable(f"No inference provider configured for task {task!r}")
        return provider

    # -- execution ---------------------------------------------------------
    async def complete(self, request: InferenceRequest) -> InferenceResult:
        provider = self.provider_for(request.task)
        model = self.model_for(provider, request.task)
        started = time.perf_counter()
        outcome = "ok"
        try:
            if request.schema is not None:
                parsed = await provider.parse(request, model=model)
                return InferenceResult(provider=provider.name, model=model,
                                       latency_ms=_ms(started), parsed=parsed)
            text = await provider.chat(request, model=model)
            return InferenceResult(provider=provider.name, model=model,
                                   latency_ms=_ms(started), text=text)
        except Exception as e:
            outcome = f"error:{type(e).__name__}"
            raise
        finally:
            # Content-free audit line: what ran where, never what was said.
            logger.info(
                "inference task=%s provider=%s model=%s lang=%s msgs=%d outcome=%s latency_ms=%d %s",
                request.task, provider.name, model, request.language, len(request.messages),
                outcome, _ms(started),
                " ".join(f"{k}={v}" for k, v in request.metadata.items()),
            )

    def describe(self) -> dict:
        return {
            "providers": {name: {"model": p.model, "base_url": getattr(p, "base_url", None)} for name, p in self.providers.items()},
            "routes": {
                task: {"provider": name, "model": self.model_for(self.providers[name], task) if name in self.providers else None}
                for task, name in self.routes.items()
            },
        }


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


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
