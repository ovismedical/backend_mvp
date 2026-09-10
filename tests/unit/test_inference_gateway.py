"""Unit tests for the inference gateway: policy gate, routing precedence, breaker, leak check, audit, env construction."""

import logging
import sys
import types

import pytest

from app.inference import InferenceGateway, InferenceRequest, ProviderUnavailable
from app.inference.audit import AuditEvent
from app.inference.gateway import InferenceRefused, TASKS, routes_from_env, task_models_from_env
from app.inference.router import Health, Policy, Router
from app.inference.providers import (
    providers_from_env, is_reasoning_model, OpenAICompatibleProvider, choose_effort, _supported_efforts_from_error,
)
from app.florence_utils import TriageAssessmentOutput
from tests.mock_openai import FakeProvider


def req(task="chat_turn", scrubbed=True, **kw):
    return InferenceRequest(task=task, messages=[{"role": "user", "content": "hi"}], scrubbed=scrubbed, **kw)


ALL_OPENAI = {t: "openai" for t in TASKS}


class RecordingSink:
    """Audit sink double: keeps every event and can be told to blow up."""

    def __init__(self, fail=False):
        self.events: list[AuditEvent] = []
        self.annotations: list[tuple] = []
        self.fail = fail

    def record(self, event):
        if self.fail:
            raise RuntimeError("sink down")
        self.events.append(event)
        return len(self.events)

    def annotate(self, event_id, **fields):
        self.annotations.append((event_id, fields))


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

    async def test_missing_routed_provider_refuses_instead_of_falling_back(self, caplog):
        openai = FakeProvider(name="openai")
        gw = InferenceGateway({"openai": openai}, routes={"chat_turn": "local", "symptom_assessment": "openai", "triage": "openai"})
        with caplog.at_level(logging.INFO, logger="ovis.inference"), pytest.raises(InferenceRefused) as exc:
            await gw.complete(req("chat_turn"))
        assert exc.value.decision.reason == "provider_unconfigured"
        assert exc.value.decision.provider == "local" and exc.value.decision.on_refuse == "scripted_fallback"
        assert openai.requests == []
        assert "falling back" not in caplog.text
        assert "decision=refuse reason=provider_unconfigured outcome=refused" in caplog.text

    async def test_refusal_is_not_a_provider_unavailable(self):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        with pytest.raises(InferenceRefused) as exc:
            await gw.complete(req(scrubbed=False))
        assert not isinstance(exc.value, ProviderUnavailable)
        assert isinstance(exc.value, RuntimeError)
        assert exc.value.reason == "not_scrubbed" and exc.value.on_refuse == "scripted_fallback"
        assert "hi" not in str(exc.value)

    async def test_env_route_overrides_yaml_only_when_set(self, monkeypatch):
        openai, local = FakeProvider(name="openai"), FakeProvider(name="local", chat_reply="local says hi")
        monkeypatch.setenv("INFERENCE_ROUTE_CHAT", "local")
        monkeypatch.setenv("INFERENCE_ROUTE_TRIAGE", "")  # empty = unset, so triage follows the YAML (openai)
        gw = InferenceGateway({"openai": openai, "local": local})
        assert (await gw.complete(req("chat_turn"))).provider == "local"
        assert (await gw.complete(req("triage", schema=TriageAssessmentOutput))).provider == "openai"
        assert gw.effective_routes() == {"chat_turn": "local", "symptom_assessment": "openai", "triage": "openai", "pii_detect": "local"}

    async def test_constructor_routes_beat_env_routes(self, monkeypatch):
        openai, local = FakeProvider(name="openai"), FakeProvider(name="local")
        monkeypatch.setenv("INFERENCE_ROUTE_CHAT", "local")
        gw = InferenceGateway({"openai": openai, "local": local}, routes={"chat_turn": "openai"})
        assert (await gw.complete(req("chat_turn"))).provider == "openai"

    async def test_task_absent_everywhere_refuses_policy_missing_task(self, tmp_path):
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text("version: 1\nflags: {}\nproviders: {openai: {requires: []}}\n"
                               "tasks: {chat_turn: {provider: openai, on_refuse: scripted_fallback}}\n")
        gw = InferenceGateway({"openai": FakeProvider(name="openai")}, routes={}, router=Router(Policy.load(policy_file)))
        assert (await gw.complete(req("chat_turn"))).provider == "openai"
        with pytest.raises(InferenceRefused) as exc:
            await gw.complete(req("triage", schema=TriageAssessmentOutput))
        assert exc.value.reason == "policy_missing_task" and exc.value.decision.provider is None

    def test_pii_detect_is_a_known_task(self):
        assert "pii_detect" in TASKS
        assert req("pii_detect").task == "pii_detect"

    def test_request_repr_hides_content(self):
        r = InferenceRequest(task="chat_turn", messages=[{"role": "user", "content": "my HKID is A123456(7)"}],
                             instructions="secret system prompt", known_identifiers=["Grace Tam"])
        text = repr(r)
        assert "A123456" not in text and "secret system prompt" not in text and "Grace Tam" not in text

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
                                               metadata={"session_ref": "s1"}, scrubbed=True))
        line = [r.getMessage() for r in caplog.records if "inference task=" in r.getMessage()][0]
        assert "provider=openai" in line and "session_ref=s1" in line and "outcome=ok" in line
        assert "decision=allow" in line and "reason=ok" in line
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

    def test_routes_from_env_returns_only_set_tasks(self, monkeypatch):
        monkeypatch.setenv("INFERENCE_ROUTE_CHAT", "local")
        monkeypatch.setenv("INFERENCE_ROUTE_ASSESSMENT", "   ")
        monkeypatch.delenv("INFERENCE_ROUTE_TRIAGE", raising=False)
        assert routes_from_env() == {"chat_turn": "local"}
        monkeypatch.delenv("INFERENCE_ROUTE_CHAT")
        assert routes_from_env() == {}

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


class TestPolicyGate:
    """Every (scrubbed, dpa_ok, provider present, healthy) combination against the real YAML."""

    @pytest.mark.parametrize("scrubbed", [True, False])
    @pytest.mark.parametrize("dpa_ok", [True, False])
    @pytest.mark.parametrize("present", [True, False])
    @pytest.mark.parametrize("healthy", [True, False])
    async def test_openai_route_matrix(self, monkeypatch, scrubbed, dpa_ok, present, healthy):
        if dpa_ok:
            monkeypatch.setenv("COMPLIANCE_DPA_OK", "true")
        else:
            monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        openai = FakeProvider(name="openai")
        providers = {"openai": openai} if present else {"local": FakeProvider(name="local")}
        gw = InferenceGateway(providers, routes={"chat_turn": "openai"})
        if not healthy:
            for _ in range(3):
                gw.health.record_failure("openai")

        if not present:
            expected = "provider_unconfigured"
        elif not healthy:
            expected = "provider_unhealthy"
        elif not dpa_ok:
            expected = "dpa_not_confirmed"
        elif not scrubbed:
            expected = "not_scrubbed"
        else:
            expected = None

        if expected is None:
            result = await gw.complete(req(scrubbed=scrubbed))
            assert result.provider == "openai" and len(openai.requests) == 1
        else:
            with pytest.raises(InferenceRefused) as exc:
                await gw.complete(req(scrubbed=scrubbed))
            assert exc.value.decision.reason == expected
            assert exc.value.decision.allow is False and exc.value.decision.task == "chat_turn"
            assert openai.requests == []

    async def test_local_provider_needs_neither_flag_nor_scrubbing(self, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        local = FakeProvider(name="local", chat_reply="local")
        gw = InferenceGateway({"local": local}, routes={"chat_turn": "local"})
        assert (await gw.complete(req(scrubbed=False))).text == "local"

    def test_on_refuse_mapping_follows_the_yaml(self, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        expected = {"chat_turn": "scripted_fallback", "symptom_assessment": "pending_clinician_review",
                    "triage": "pending_clinician_review"}
        for task, on_refuse in expected.items():
            decision = gw.decide(task, scrubbed=True)
            assert decision.allow is False and decision.reason == "dpa_not_confirmed" and decision.on_refuse == on_refuse
        pii = gw.decide("pii_detect", scrubbed=False)
        assert pii.reason == "provider_unconfigured" and pii.on_refuse == "skip"  # no local provider configured

    def test_flag_is_read_at_decision_time(self, monkeypatch):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        assert gw.decide("chat_turn").allow is True
        monkeypatch.setenv("COMPLIANCE_DPA_OK", "no")
        assert gw.decide("chat_turn").reason == "dpa_not_confirmed"
        monkeypatch.setenv("COMPLIANCE_DPA_OK", "YES")
        assert gw.decide("chat_turn").allow is True

    def test_policy_warnings_name_the_affected_provider(self, monkeypatch, caplog):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        assert gw.policy_warnings() == []
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        with caplog.at_level(logging.WARNING, logger="ovis.inference"):
            emitted = gw.log_policy_state()
        assert emitted == ["inference policy dpa_ok=false: openai-routed tasks will refuse"]
        assert "inference policy dpa_ok=false: openai-routed tasks will refuse" in caplog.text

    def test_no_warning_when_nothing_routes_to_the_gated_provider(self, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        gw = InferenceGateway({"local": FakeProvider(name="local")}, routes={t: "local" for t in TASKS})
        assert gw.policy_warnings() == []

    def test_warning_when_a_route_names_an_unconfigured_provider(self, monkeypatch):
        """Every call to a task routed at a provider that does not exist refuses; say so at startup."""
        monkeypatch.setenv("INFERENCE_ROUTE_CHAT", "ollama")
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        assert gw.policy_warnings() == [
            "inference route chat_turn -> ollama is not configured/declared; calls will refuse"
        ]

    def test_no_warning_for_the_unconfigured_skip_task(self):
        """pii_detect routes at `local` in most deployments and its on_refuse is "skip"."""
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        assert gw.policy_warnings() == []


class TestBreaker:

    async def test_three_consecutive_failures_open_the_breaker_for_60s(self):
        now = [1000.0]
        openai = FakeProvider(name="openai", fail=True)
        gw = InferenceGateway({"openai": openai})
        gw.health = Health(clock=lambda: now[0])
        for _ in range(3):
            with pytest.raises(RuntimeError, match="provider down"):
                await gw.complete(req())
        assert len(openai.requests) == 3
        with pytest.raises(InferenceRefused) as exc:
            await gw.complete(req())
        assert exc.value.reason == "provider_unhealthy" and len(openai.requests) == 3  # not called
        now[0] += 59.9
        with pytest.raises(InferenceRefused):
            await gw.complete(req())
        now[0] += 0.2
        openai.fail = False
        assert (await gw.complete(req())).provider == "openai"  # cooldown over: the probe call goes through
        assert gw.health.failures("openai") == 0  # success resets

    async def test_success_resets_the_failure_count(self):
        openai = FakeProvider(name="openai", fail=True)
        gw = InferenceGateway({"openai": openai})
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await gw.complete(req())
        openai.fail = False
        await gw.complete(req())
        openai.fail = True
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await gw.complete(req())
        assert gw.health.is_healthy("openai") is True  # 2 + reset + 2 never reached 3 in a row

    async def test_refusals_do_not_count_as_failures(self):
        openai = FakeProvider(name="openai")
        gw = InferenceGateway({"openai": openai})
        for _ in range(5):
            with pytest.raises(InferenceRefused):
                await gw.complete(req(scrubbed=False))
        assert gw.health.failures("openai") == 0 and gw.decide("chat_turn").allow is True


class TestLeakCheck:

    @pytest.fixture
    def fake_scrub(self, monkeypatch):
        """Stand-in for app.inference.scrub until the scrubber package lands: flags case-insensitive whole strings."""
        calls = []

        def leak_check(messages, known):
            calls.append((messages, list(known)))
            text = " ".join(str(m.get("content", "")) for m in messages).lower()
            return [k for k in known if k.lower() in text]

        module = types.ModuleType("app.inference.scrub")
        module.leak_check = leak_check
        monkeypatch.setitem(sys.modules, "app.inference.scrub", module)
        return calls

    async def test_known_identifier_in_messages_refuses_with_leak_check_failed(self, fake_scrub, caplog):
        openai = FakeProvider(name="openai")
        gw = InferenceGateway({"openai": openai})
        request = InferenceRequest(task="chat_turn", messages=[{"role": "user", "content": "my name is Grace Tam"}],
                                   scrubbed=True, known_identifiers=["Grace Tam", "demo"])
        with caplog.at_level(logging.INFO, logger="ovis.inference"), pytest.raises(InferenceRefused) as exc:
            await gw.complete(request)
        assert exc.value.reason == "leak_check_failed" and exc.value.decision.provider == "openai"
        assert openai.requests == []
        assert "Grace" not in caplog.text and "demo" not in caplog.text  # identifiers never logged
        assert "reason=leak_check_failed" in caplog.text

    async def test_clean_messages_pass_the_leak_check(self, fake_scrub):
        openai = FakeProvider(name="openai")
        gw = InferenceGateway({"openai": openai})
        request = InferenceRequest(task="chat_turn", messages=[{"role": "user", "content": "I went to the hospital"}],
                                   scrubbed=True, known_identifiers=["Grace Tam", "demo"])
        assert (await gw.complete(request)).provider == "openai"
        assert fake_scrub and fake_scrub[0][1] == ["Grace Tam", "demo"]

    async def test_no_known_identifiers_skips_the_check(self, fake_scrub):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        await gw.complete(req())
        await gw.complete(req(known_identifiers=[]))
        assert fake_scrub == []

    async def test_missing_scrub_package_fails_closed(self, monkeypatch, caplog):
        """An unverifiable request does not leave the building, like every other leak-check failure."""
        monkeypatch.setitem(sys.modules, "app.inference.scrub", None)  # forces ImportError on the lazy import
        openai = FakeProvider(name="openai")
        gw = InferenceGateway({"openai": openai})
        with caplog.at_level(logging.WARNING, logger="ovis.inference"), pytest.raises(InferenceRefused) as exc:
            await gw.complete(req(known_identifiers=["Grace Tam"]))
        assert exc.value.reason == "leak_check_failed" and openai.requests == []
        assert "leak check unavailable" in caplog.text and "Grace" not in caplog.text

    async def test_leak_check_exception_fails_closed(self, monkeypatch, caplog):
        def exploding(messages, known):
            raise ValueError("A123456(7) 08/22/1966")
        module = types.ModuleType("app.inference.scrub")
        module.leak_check = exploding
        monkeypatch.setitem(sys.modules, "app.inference.scrub", module)
        openai = FakeProvider(name="openai")
        gw = InferenceGateway({"openai": openai})
        with caplog.at_level(logging.WARNING, logger="ovis.inference"), pytest.raises(InferenceRefused) as exc:
            await gw.complete(req(known_identifiers=["Grace Tam"]))
        assert exc.value.reason == "leak_check_failed" and openai.requests == []
        assert "leak check raised ValueError" in caplog.text
        assert "A123456" not in caplog.text and "1966" not in caplog.text and "Grace" not in caplog.text

    async def test_trusted_tail_is_exempt_from_the_leak_check(self, fake_scrub):
        """The appended prompt template is static call-site text: an identifier that collides with a
        template word (醫生, 翻譯) must not refuse the call, while the transcript is still checked."""
        openai = FakeProvider(name="openai")
        gw = InferenceGateway({"openai": openai})
        template = {"role": "user", "content": "Summarise the conversation for the 醫生 on duty."}
        assert (await gw.complete(InferenceRequest(
            task="chat_turn", messages=[{"role": "user", "content": "I feel tired"}, template],
            scrubbed=True, known_identifiers=["醫生"], trusted_tail=1,
        ))).provider == "openai"
        assert fake_scrub[-1][0] == [{"role": "user", "content": "I feel tired"}]  # template not checked

        with pytest.raises(InferenceRefused) as exc:
            await gw.complete(InferenceRequest(
                task="chat_turn", messages=[{"role": "user", "content": "my 醫生 said so"}, template],
                scrubbed=True, known_identifiers=["醫生"], trusted_tail=1,
            ))
        assert exc.value.reason == "leak_check_failed"

    async def test_trusted_tail_is_clamped_and_only_trusts_the_tail(self, fake_scrub):
        """A negative tail cannot shrink the checked slice; an oversized one cannot index past it.
        Only a call site that sends nothing but its own static template ends up checking nothing."""
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        with pytest.raises(InferenceRefused):
            await gw.complete(req(known_identifiers=["hi"], trusted_tail=-3))   # clamped to 0: full check
        assert fake_scrub[-1][0] == [{"role": "user", "content": "hi"}]
        await gw.complete(req(known_identifiers=["hi"], trusted_tail=9))        # clamped to len(messages)
        assert fake_scrub[-1][0] == []

    async def test_refused_requests_are_not_leak_checked(self, fake_scrub):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        with pytest.raises(InferenceRefused) as exc:
            await gw.complete(req(scrubbed=False, known_identifiers=["Grace Tam"]))
        assert exc.value.reason == "not_scrubbed" and fake_scrub == []


class TestAuditSink:

    async def test_sink_receives_allow_event_without_content(self):
        sink = RecordingSink()
        gw = InferenceGateway({"openai": FakeProvider(name="openai", model="gpt-5-mini")}, task_models={}, audit_sink=sink)
        secret = "my HKID is A123456(7) and I feel awful"
        result = await gw.complete(InferenceRequest(
            task="triage", messages=[{"role": "user", "content": secret}], schema=TriageAssessmentOutput, language="zh",
            scrubbed=True, known_identifiers=["Grace Tam"],
            metadata={"patient_ref": "p" * 16, "session_ref": "s" * 12, "task_source": "florence"},
            scrub_report={"counts": {"PERSON": 1}, "linkage_score": 2, "ner_backend": "none", "layers": {"known": 1}, "ms": 3},
        ))
        assert len(sink.events) == 1 and result.audit_id == 1
        event = sink.events[0].to_dict()
        assert event["kind"] == "inference" and event["decision"] == "allow" and event["reason"] == "ok"
        assert event["outcome"] == "ok" and event["task"] == "triage" and event["provider"] == "openai"
        # `language` is audited only as one of the supported values, never raw call-site text.
        assert event["model"] == "gpt-5-mini" and event["lang"] == "other" and event["msgs"] == 1
        assert event["patient_ref"] == "p" * 16 and event["session_ref"] == "s" * 12 and event["task_source"] == "florence"
        assert event["scrub"] == {"counts": {"PERSON": 1}, "linkage_score": 2, "ner_backend": "none"}
        assert isinstance(event["latency_ms"], int) and event["ts"] is not None
        dumped = repr(event)
        assert "HKID" not in dumped and "awful" not in dumped and "Grace" not in dumped

    async def test_sink_receives_refuse_event(self, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        sink = RecordingSink()
        gw = InferenceGateway({"openai": FakeProvider(name="openai")}, audit_sink=sink)
        with pytest.raises(InferenceRefused):
            await gw.complete(req(metadata={"session_ref": "abc"}))
        assert len(sink.events) == 1
        event = sink.events[0]
        assert event.decision == "refuse" and event.reason == "dpa_not_confirmed" and event.outcome == "refused"
        assert event.provider == "openai" and event.model == "fake-model" and event.session_ref == "abc"
        assert event.patient_ref is None and event.scrub is None

    async def test_sink_receives_error_event(self):
        sink = RecordingSink()
        gw = InferenceGateway({"openai": FakeProvider(name="openai", fail=True)}, audit_sink=sink)
        with pytest.raises(RuntimeError):
            await gw.complete(req())
        assert sink.events[0].outcome == "error:RuntimeError" and sink.events[0].decision == "allow"

    async def test_failing_sink_never_breaks_the_call(self, caplog):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")}, audit_sink=RecordingSink(fail=True))
        with caplog.at_level(logging.WARNING, logger="ovis.inference"):
            result = await gw.complete(req())
        assert result.text == "Hello! How are you feeling?" and result.audit_id is None
        assert "audit sink raised RuntimeError" in caplog.text

    async def test_default_sink_is_null(self):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        result = await gw.complete(req())
        assert result.audit_id is None


class TestAuditLanguage:

    async def test_unsupported_language_is_audited_as_other_and_never_logged(self, caplog):
        """`language` reaches the audit document and the log line, so free text (and newlines
        that would forge a second log record) never pass through raw."""
        sink = RecordingSink()
        gw = InferenceGateway({"openai": FakeProvider(name="openai")}, audit_sink=sink)
        forged = "en\nWARNING ovis.inference: patient Grace Tam HKID A123456(7)"
        with caplog.at_level(logging.INFO, logger="ovis.inference"):
            await gw.complete(req(language=forged))
        assert sink.events[0].to_dict()["lang"] == "other"
        assert "Grace" not in caplog.text and "A123456" not in caplog.text
        assert "lang=other" in caplog.text
        assert all("\n" not in record.getMessage() for record in caplog.records)

    async def test_supported_languages_are_audited_verbatim(self):
        sink = RecordingSink()
        gw = InferenceGateway({"openai": FakeProvider(name="openai")}, audit_sink=sink)
        await gw.complete(req(language="zh-HK"))
        assert sink.events[0].to_dict()["lang"] == "zh-HK"


class TestDescribe:

    def test_describe_has_policy_and_no_base_url(self, monkeypatch):
        provider = OpenAICompatibleProvider("openai", "gpt-5-mini", api_key="k", base_url="https://x.openai.azure.com/openai/v1")
        gw = InferenceGateway({"openai": provider})
        info = gw.describe()
        assert "base_url" not in repr(info) and "azure" not in repr(info)
        assert info["providers"] == {"openai": {"model": "gpt-5-mini", "healthy": True}}
        assert info["routes"]["chat_turn"] == {"provider": "openai", "model": "gpt-5-mini"}
        assert info["routes"]["pii_detect"] == {"provider": "local", "model": None}
        assert info["policy"]["flags"] == {"dpa_ok": True}
        assert info["policy"]["tasks"]["triage"] == {"provider": "openai", "on_refuse": "pending_clinician_review"}
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        assert gw.describe()["policy"]["flags"] == {"dpa_ok": False}
