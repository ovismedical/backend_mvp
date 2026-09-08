"""Unit tests for the inference gateway: routing, fallback, audit, env construction."""

import logging
import pytest

from app.inference import InferenceGateway, InferenceRequest, ProviderUnavailable
from app.inference.gateway import routes_from_env, task_models_from_env
from app.inference.providers import (
    providers_from_env, is_reasoning_model, OpenAICompatibleProvider, choose_effort, _supported_efforts_from_error,
)
from app.florence_utils import TriageAssessmentOutput
from tests.mock_openai import FakeProvider


def req(task="chat_turn", **kw):
    return InferenceRequest(task=task, messages=[{"role": "user", "content": "hi"}], **kw)


class TestRouting:

    async def test_routes_task_to_configured_provider(self):
        openai, local = FakeProvider(name="openai"), FakeProvider(name="local", chat_reply="local says hi")
        gw = InferenceGateway({"openai": openai, "local": local},
                              routes={"chat_turn": "local", "symptom_assessment": "openai", "triage": "openai"})
        result = await gw.complete(req("chat_turn"))
        assert result.provider == "local" and result.text == "local says hi"
        result = await gw.complete(req("triage", schema=TriageAssessmentOutput))
        assert result.provider == "openai" and result.parsed.alert_level == "GREEN"
        assert len(local.requests) == 1 and len(openai.requests) == 1

    async def test_falls_back_to_available_provider_when_route_missing(self, caplog):
        openai = FakeProvider(name="openai")
        gw = InferenceGateway({"openai": openai}, routes={"chat_turn": "local", "symptom_assessment": "openai", "triage": "openai"})
        with caplog.at_level(logging.WARNING, logger="ovis.inference"):
            result = await gw.complete(req("chat_turn"))
        assert result.provider == "openai"
        assert "falling back" in caplog.text

    async def test_no_provider_raises(self):
        gw = InferenceGateway({})
        assert gw.available() is False
        with pytest.raises(ProviderUnavailable):
            await gw.complete(req())

    def test_unknown_task_rejected(self):
        with pytest.raises(ValueError):
            InferenceRequest(task="summarise_everything", messages=[])


class TestPerTaskModels:

    async def test_triage_can_run_on_a_bigger_model_than_chat(self):
        openai = FakeProvider(name="openai", model="gpt-5-mini")
        gw = InferenceGateway({"openai": openai}, routes={t: "openai" for t in ("chat_turn", "symptom_assessment", "triage")},
                              task_models={"openai": {"triage": "gpt-5"}})
        chat = await gw.complete(req("chat_turn"))
        triage = await gw.complete(req("triage", schema=TriageAssessmentOutput))
        assert chat.model == "gpt-5-mini" and triage.model == "gpt-5"
        assert openai.models == ["gpt-5-mini", "gpt-5"]
        assert gw.describe()["routes"]["triage"] == {"provider": "openai", "model": "gpt-5"}

    def test_task_models_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_TRIAGE_MODEL", "gpt-5")
        monkeypatch.delenv("OPENAI_CHAT_MODEL", raising=False)
        monkeypatch.delenv("OPENAI_ASSESSMENT_MODEL", raising=False)
        assert task_models_from_env() == {"openai": {"triage": "gpt-5"}}
        monkeypatch.delenv("OPENAI_TRIAGE_MODEL")
        assert task_models_from_env() == {}

    def test_generation_params_follow_the_resolved_model(self):
        provider = OpenAICompatibleProvider("x", "gpt-4.1-mini", api_key="k")
        r = req(effort="medium", temperature=0.4)
        assert provider._params(r) == {"temperature": 0.4}
        assert provider._params(r, "gpt-5") == {"reasoning": {"effort": "medium"}}


class TestAudit:

    async def test_audit_line_has_no_content(self, caplog):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        secret = "my HKID is A123456(7) and I feel awful"
        with caplog.at_level(logging.INFO, logger="ovis.inference"):
            await gw.complete(InferenceRequest(task="chat_turn", messages=[{"role": "user", "content": secret}],
                                               metadata={"session_id": "s1"}))
        line = [r.getMessage() for r in caplog.records if "inference task=" in r.getMessage()][0]
        assert "provider=openai" in line and "session_id=s1" in line and "outcome=ok" in line
        assert "HKID" not in line and "awful" not in line

    async def test_errors_are_audited_and_reraised(self, caplog):
        gw = InferenceGateway({"openai": FakeProvider(name="openai", fail=True)})
        with caplog.at_level(logging.INFO, logger="ovis.inference"), pytest.raises(RuntimeError):
            await gw.complete(req())
        assert "outcome=error:RuntimeError" in caplog.text


class TestEnvConstruction:

    def test_providers_from_env_builds_openai_and_local(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini")
        monkeypatch.setenv("LOCAL_INFERENCE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("LOCAL_INFERENCE_MODEL", "medgemma")
        providers = providers_from_env()
        assert set(providers) == {"openai", "local"}
        assert providers["local"].base_url == "http://localhost:11434/v1"
        assert providers["local"].model == "medgemma"
        assert providers["openai"].model == "gpt-5-mini"

    def test_openai_base_url_passthrough_for_azure(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "azure-key")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://ovis-oai.openai.azure.com/openai/v1")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-5-mini-deploy")
        monkeypatch.delenv("LOCAL_INFERENCE_URL", raising=False)
        providers = providers_from_env()
        assert providers["openai"].base_url == "https://ovis-oai.openai.azure.com/openai/v1"
        assert str(providers["openai"].client.base_url).startswith("https://ovis-oai.openai.azure.com/openai/v1")
        assert providers["openai"].model == "gpt-5-mini-deploy"

    def test_providers_from_env_empty_without_keys(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LOCAL_INFERENCE_URL", raising=False)
        assert providers_from_env() == {}

    def test_routes_from_env(self, monkeypatch):
        monkeypatch.setenv("INFERENCE_ROUTE_CHAT", "local")
        monkeypatch.delenv("INFERENCE_ROUTE_TRIAGE", raising=False)
        routes = routes_from_env()
        assert routes["chat_turn"] == "local" and routes["triage"] == "openai"

    def test_generation_params_by_model_family(self):
        assert is_reasoning_model("gpt-5-mini") and is_reasoning_model("o3") and not is_reasoning_model("gpt-4.1-mini")
        reasoning = OpenAICompatibleProvider("x", "gpt-5-mini", api_key="k")
        classic = OpenAICompatibleProvider("y", "medgemma", api_key="k", base_url="http://localhost:11434/v1")
        r = req(effort="medium", temperature=0.4)
        assert reasoning._params(r) == {"reasoning": {"effort": "medium"}}
        assert classic._params(r) == {"temperature": 0.4}


class TestReasoningEffortNegotiation:

    def test_parses_supported_values_from_azure_error(self):
        msg = ("Error code: 400 - {'error': {'message': \"Unsupported value: 'minimal' is not supported with the "
               "'gpt-5.6-sol-2026-07-09' model. Supported values are: 'none', 'low', 'medium', 'high', 'xhigh', and 'max'.\", "
               "'type': 'invalid_request_error', 'param': 'reasoning.effort'}}")
        assert _supported_efforts_from_error(msg) == {"none", "low", "medium", "high", "xhigh", "max"}
        assert _supported_efforts_from_error("some other error") is None

    def test_choose_effort_prefers_closest_supported(self):
        supported = {"none", "low", "medium", "high", "xhigh", "max"}
        assert choose_effort("minimal", supported) == "none"
        assert choose_effort("medium", supported) == "medium"
        assert choose_effort("minimal", {"low", "medium"}) == "low"
        assert choose_effort("high", {"low", "medium"}) == "medium"
        assert choose_effort("minimal", None) == "minimal"

    async def test_provider_learns_efforts_and_retries_once(self):
        from unittest.mock import AsyncMock
        from openai import BadRequestError
        import httpx
        provider = OpenAICompatibleProvider("openai", "gpt-5.6-sol", api_key="k")
        err_body = {"error": {"message": "Unsupported value: 'minimal' is not supported with the 'gpt-5.6-sol' model. "
                                         "Supported values are: 'none', 'low', 'medium'.", "param": "reasoning.effort"}}
        response = httpx.Response(400, request=httpx.Request("POST", "http://x"), json=err_body)
        good = type("R", (), {"output_text": "hello"})()
        provider.client.responses.create = AsyncMock(side_effect=[BadRequestError("bad", response=response, body=err_body), good])
        text = await provider.chat(req(effort="minimal"))
        assert text == "hello"
        calls = provider.client.responses.create.await_args_list
        assert calls[0].kwargs["reasoning"] == {"effort": "minimal"}
        assert calls[1].kwargs["reasoning"] == {"effort": "none"}
        # learned: next call goes straight to the supported value
        provider.client.responses.create = AsyncMock(return_value=good)
        await provider.chat(req(effort="minimal"))
        assert provider.client.responses.create.await_args.kwargs["reasoning"] == {"effort": "none"}
