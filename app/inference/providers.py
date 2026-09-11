"""Inference providers. Anything that speaks the OpenAI Responses API is one class."""

from __future__ import annotations

import os
import re
from typing import Protocol, TYPE_CHECKING

from openai import AsyncOpenAI, BadRequestError

from .tools import ToolCall

if TYPE_CHECKING:
    from .gateway import InferenceRequest


DEFAULT_OPENAI_MODEL = "gpt-5-mini"

# Preference order when a model doesn't support the requested reasoning effort.
EFFORT_FALLBACKS = {
    "minimal": ["none", "low", "medium"],
    "none": ["minimal", "low", "medium"],
    "low": ["minimal", "none", "medium"],
    "medium": ["low", "high", "minimal", "none"],
    "high": ["medium", "xhigh", "max", "low"],
}
_SUPPORTED_RE = re.compile(r"Supported values are:\s*([^.\"}]+)", re.IGNORECASE)


def _supported_efforts_from_error(message: str) -> set[str] | None:
    match = _SUPPORTED_RE.search(message or "")
    if not match:
        return None
    values = re.findall(r"'([a-z]+)'", match.group(1))
    return set(values) or None


def choose_effort(requested: str, supported: set[str] | None) -> str:
    if not supported or requested in supported:
        return requested
    for candidate in EFFORT_FALLBACKS.get(requested, []):
        if candidate in supported:
            return candidate
    return sorted(supported)[0]


def is_reasoning_model(model: str) -> bool:
    name = model.lower()
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


class InferenceProvider(Protocol):
    name: str
    model: str

    async def chat(self, request: "InferenceRequest", model: str | None = None) -> str: ...

    async def chat_with_tools(self, request: "InferenceRequest", model: str | None = None): ...

    async def parse(self, request: "InferenceRequest", model: str | None = None): ...

    async def healthy(self) -> bool: ...


class OpenAICompatibleProvider:
    """OpenAI, or any OpenAI-compatible server (Ollama, vLLM, LiteLLM) via `base_url`.

    The same class therefore serves both the hosted OpenAI route and a local
    MedGemma route; only `base_url`/`model` differ.
    """

    def __init__(self, name: str, model: str, api_key: str, base_url: str | None = None,
                 timeout: float = 90.0, max_retries: int = 2):
        self.name = name
        self.model = model
        self.base_url = base_url
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        # Learned per model from the API's own error message ("Supported values are: ...")
        self._supported_efforts: dict[str, set[str]] = {}

    def _params(self, request: "InferenceRequest", model: str | None = None) -> dict:
        """Reasoning models take an effort; older/local models take a temperature."""
        model = model or self.model
        if is_reasoning_model(model):
            effort = choose_effort(request.effort, self._supported_efforts.get(model))
            return {"reasoning": {"effort": effort}}
        return {"temperature": request.temperature}

    async def _call(self, method, request: "InferenceRequest", model: str, **kwargs):
        """Run a Responses API call; if the model rejects our reasoning effort, learn its list and retry once."""
        try:
            return await method(model=model, store=False, **kwargs, **self._params(request, model))
        except BadRequestError as e:
            detail = f"{e} {getattr(e, 'body', '') or ''}"
            supported = _supported_efforts_from_error(detail)
            if "reasoning.effort" not in detail or not supported or model in self._supported_efforts:
                raise
            self._supported_efforts[model] = supported
            return await method(model=model, store=False, **kwargs, **self._params(request, model))

    async def chat(self, request: "InferenceRequest", model: str | None = None) -> str:
        model = model or self.model
        response = await self._call(
            self.client.responses.create, request, model,
            instructions=request.instructions,
            input=request.messages,
        )
        text = (response.output_text or "").strip()
        if not text:
            raise RuntimeError(f"{self.name}: empty response")
        return text

    async def chat_with_tools(self, request: "InferenceRequest", model: str | None = None):
        """One turn that may call tools. Returns (text, tool_calls); either may be empty, and the
        gateway decides what to do next. Tool results are fed back as `function_call_output` items
        in `request.messages`, so nothing is carried between hops inside this method."""
        model = model or self.model
        kwargs: dict = {"instructions": request.instructions, "input": request.messages}
        if request.tools:
            kwargs["tools"] = request.tools.wire_format()
            if request.tool_choice:
                kwargs["tool_choice"] = request.tool_choice
        response = await self._call(self.client.responses.create, request, model, **kwargs)
        calls = tuple(
            ToolCall(name=item.name, arguments=getattr(item, "arguments", "") or "{}", call_id=item.call_id)
            for item in (getattr(response, "output", None) or [])
            if getattr(item, "type", None) == "function_call"
        )
        text = (getattr(response, "output_text", "") or "").strip()
        if not text and not calls:
            raise RuntimeError(f"{self.name}: empty response")
        return text, calls

    async def parse(self, request: "InferenceRequest", model: str | None = None):
        if request.schema is None:
            raise ValueError("parse() requires request.schema")
        model = model or self.model
        response = await self._call(
            self.client.responses.parse, request, model,
            instructions=request.instructions,
            input=request.messages,
            text_format=request.schema,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError(f"{self.name}: model returned no parsed output (refusal or truncation)")
        return parsed

    async def healthy(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False


def providers_from_env() -> dict[str, InferenceProvider]:
    """Build the provider registry from environment variables.

    OPENAI_API_KEY / OPENAI_MODEL              -> provider "openai"
    LOCAL_INFERENCE_URL / LOCAL_INFERENCE_MODEL -> provider "local" (e.g. Ollama serving MedGemma)
    """
    providers: dict[str, InferenceProvider] = {}

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        # OPENAI_BASE_URL lets the same provider talk to Azure OpenAI / Foundry
        # (https://<resource>.openai.azure.com/openai/v1), OpenRouter, or any other
        # OpenAI-compatible host. On Azure, OPENAI_MODEL must be the *deployment* name.
        providers["openai"] = OpenAICompatibleProvider(
            name="openai",
            model=os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
            api_key=openai_key,
            base_url=os.getenv("OPENAI_BASE_URL") or None,
        )

    local_url = os.getenv("LOCAL_INFERENCE_URL")
    if local_url:
        providers["local"] = OpenAICompatibleProvider(
            name="local",
            model=os.getenv("LOCAL_INFERENCE_MODEL") or "medgemma",
            api_key=os.getenv("LOCAL_INFERENCE_API_KEY") or "local",
            base_url=local_url,
            timeout=float(os.getenv("LOCAL_INFERENCE_TIMEOUT", "120")),
            max_retries=0,
        )

    return providers
