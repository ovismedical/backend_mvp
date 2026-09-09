"""Inference gateway: the single seam every LLM call goes through.

Call sites de-identify their messages with `app.inference.scrub` (a per-session
`ScrubContext`), build an `InferenceRequest` with `scrubbed=True`, opaque
`metadata` (`patient_ref`, `session_ref`, `task_source` from `app.inference.refs`)
and `known_identifiers=ctx.leak_forms()`, then call `get_gateway().complete(request)`.

The gateway asks the `Router` (YAML policy in `routing_policy.yaml`, flags read
from the environment at decision time) whether the task may run on its provider,
runs the scrubber's leak check as defence in depth, calls the provider behind a
circuit breaker, and writes a content-free audit line plus an `AuditEvent` to the
configured sink. A refused call raises `InferenceRefused` (its `.decision` says
why and what the call site should do); `ProviderUnavailable` means nothing is
configured at all (fallback mode).
"""

from .gateway import (  # noqa: F401
    InferenceGateway,
    InferenceRefused,
    InferenceRequest,
    InferenceResult,
    ProviderUnavailable,
    get_gateway,
    reset_gateway,
)
from .router import Decision  # noqa: F401

__all__ = [
    "Decision",
    "InferenceGateway",
    "InferenceRefused",
    "InferenceRequest",
    "InferenceResult",
    "ProviderUnavailable",
    "get_gateway",
    "reset_gateway",
]
