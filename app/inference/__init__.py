"""Inference gateway: the single seam every LLM call goes through.

Call sites build an `InferenceRequest` and call `get_gateway().complete(request)`.
The gateway picks a provider per task (env-configured for now; a policy engine
later), runs the call, and emits a content-free audit log line.
"""

from .gateway import (  # noqa: F401
    InferenceGateway,
    InferenceRequest,
    InferenceResult,
    ProviderUnavailable,
    get_gateway,
    reset_gateway,
)
