"""/health reflects the routing policy; the app never writes request paths to the access log."""

import logging
from unittest.mock import MagicMock

import pytest

import app.api as api_module
from app.inference import InferenceGateway, reset_gateway
from app.inference.router import Policy
from tests.mock_openai import FakeProvider, fake_gateway


@pytest.fixture
def mongo_ping(monkeypatch):
    """Replace the real Mongo client used by /health with a stub whose ping succeeds."""
    fake_client = MagicMock()
    fake_client.admin.command.return_value = {"ok": 1}
    monkeypatch.setattr(api_module, "get_client", lambda: fake_client)
    return fake_client


class TestHealthFlorenceState:

    async def test_fallback_without_providers(self, client, mongo_ping):
        reset_gateway(InferenceGateway({}))
        body = (await client.get("/health")).json()
        assert body["status"] == "healthy" and body["database"] == "connected"
        assert body["florence_ai"] == "fallback"

    async def test_ready_when_policy_allows(self, client, mongo_ping):
        reset_gateway(fake_gateway(openai=FakeProvider(name="openai"), policy=Policy.load()))  # dpa_ok=true from conftest
        body = (await client.get("/health")).json()
        assert body["florence_ai"] == "ready"

    async def test_refusing_when_dpa_flag_is_off(self, client, mongo_ping, monkeypatch, gated_policy_env):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        reset_gateway(fake_gateway(openai=FakeProvider(name="openai"), policy=Policy.load()))
        body = (await client.get("/health")).json()
        assert body["florence_ai"] == "refusing"
        monkeypatch.setenv("COMPLIANCE_DPA_OK", "true")  # flags are read per request
        assert (await client.get("/health")).json()["florence_ai"] == "ready"

    async def test_refusing_when_routed_provider_is_missing(self, client, mongo_ping):
        gw = InferenceGateway({"local": FakeProvider(name="local")}, routes={})  # YAML routes chat_turn -> openai
        reset_gateway(gw)
        assert (await client.get("/health")).json()["florence_ai"] == "refusing"

    async def test_permissive_test_gateway_reports_ready(self, client, mongo_ping):
        reset_gateway(fake_gateway())
        assert (await client.get("/health")).json()["florence_ai"] == "ready"

    async def test_database_disconnected_does_not_hide_florence_state(self, client, monkeypatch):
        def broken_client():
            raise ConnectionError("no mongo")
        monkeypatch.setattr(api_module, "get_client", broken_client)
        reset_gateway(fake_gateway())
        body = (await client.get("/health")).json()
        assert body["database"] == "disconnected" and body["florence_ai"] == "ready"


class TestPolicyStartupWarning:
    """The lifespan calls gateway.log_policy_state(); the message must be exact so ops can alert on it."""

    def test_warning_when_dpa_flag_is_false(self, monkeypatch, caplog, gated_policy_env):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        with caplog.at_level(logging.WARNING, logger="ovis.inference"):
            gw.log_policy_state()
        records = [r for r in caplog.records if r.name == "ovis.inference" and r.levelno == logging.WARNING]
        assert [r.getMessage() for r in records] == ["inference policy dpa_ok=false: openai-routed tasks will refuse"]

    def test_silent_when_dpa_flag_is_true(self, caplog):
        gw = InferenceGateway({"openai": FakeProvider(name="openai")})
        with caplog.at_level(logging.WARNING, logger="ovis.inference"):
            assert gw.log_policy_state() == []
        assert "will refuse" not in caplog.text


class TestAccessLog:

    def test_uvicorn_access_logger_is_disabled_after_importing_the_app(self):
        assert logging.getLogger("uvicorn.access").disabled is True
        assert logging.getLogger("ovis").disabled is False
