"""Unit tests for the routing policy gate: policy loading/validation, decisions, flags, breaker."""

import logging

import pytest

from app.inference.router import (
    DEFAULT_POLICY_PATH, FLAG_REASONS, REASONS, Decision, Health, Policy, PolicyError, Router,
)

PROVIDERS = {"openai": object(), "local": object()}


def write_policy(tmp_path, text, name="policy.yaml"):
    path = tmp_path / name
    path.write_text(text)
    return path


# The shipped policy deliberately does not gate the openai provider on a flag (see
# routing_policy.yaml). Flag-gating is still a supported mechanism, so the tests that exercise it
# build their own policy rather than depending on what the deployment happens to require today.
GATED_POLICY = """
version: 1
flags:
  dpa_ok: {env: COMPLIANCE_DPA_OK, description: test gate}
providers:
  openai: {requires: [dpa_ok, scrubbed]}
  local:  {requires: []}
tasks:
  chat_turn: {provider: openai, on_refuse: scripted_fallback}
  triage:    {provider: openai, on_refuse: pending_clinician_review}
  pii_detect: {provider: local, on_refuse: skip}
"""


def gated_policy(tmp_path):
    """A policy whose openai provider is gated on `dpa_ok`, for testing the flag mechanism."""
    return Policy.load(write_policy(tmp_path, GATED_POLICY, name="gated.yaml"))


class TestPolicyLoad:

    def test_default_path_is_the_yaml_next_to_the_module(self):
        policy = Policy.load()
        assert policy.source == str(DEFAULT_POLICY_PATH) and DEFAULT_POLICY_PATH.name == "routing_policy.yaml"
        assert policy.version == 1
        assert set(policy.flags) == {"dpa_ok"} and policy.flags["dpa_ok"].env == "COMPLIANCE_DPA_OK"
        assert "processors.md" in policy.flags["dpa_ok"].description
        # The flag stays declared so it can be re-required, but the demo deployment does not gate on it.
        assert policy.providers["openai"].requires == ("scrubbed",)
        assert policy.providers["local"].requires == ()
        assert {t: (p.provider, p.on_refuse) for t, p in policy.tasks.items()} == {
            "chat_turn": ("openai", "scripted_fallback"),
            "symptom_assessment": ("openai", "pending_clinician_review"),
            "triage": ("openai", "pending_clinician_review"),
            "pii_detect": ("local", "skip"),
            "memory_extraction": ("openai", "skip"),
        }

    def test_custom_path_argument(self, tmp_path):
        path = write_policy(tmp_path, "version: 1\nflags: {}\nproviders: {local: {requires: []}}\n"
                                      "tasks: {chat_turn: {provider: local, on_refuse: skip}}\n")
        policy = Policy.load(path)
        assert policy.source == str(path) and policy.provider_for("chat_turn") == "local"
        assert policy.flags == {} and policy.on_refuse_for("chat_turn") == "skip"

    def test_env_var_overrides_default_path(self, tmp_path, monkeypatch):
        path = write_policy(tmp_path, "version: 1\nflags: {}\nproviders: {local: {}}\n"
                                      "tasks: {triage: {provider: local, on_refuse: pending_clinician_review}}\n")
        monkeypatch.setenv("INFERENCE_POLICY_PATH", str(path))
        policy = Policy.load()
        assert policy.source == str(path) and list(policy.tasks) == ["triage"]
        # an explicit argument still wins over the env var
        assert Policy.load(DEFAULT_POLICY_PATH).source == str(DEFAULT_POLICY_PATH)

    def test_missing_file_is_a_policy_error(self, tmp_path):
        with pytest.raises(PolicyError, match="cannot read routing policy"):
            Policy.load(tmp_path / "nope.yaml")

    def test_invalid_yaml_is_a_policy_error(self, tmp_path):
        path = write_policy(tmp_path, "version: 1\nflags: [unclosed\n  - :\n")
        with pytest.raises(PolicyError, match="not valid YAML"):
            Policy.load(path)

    @pytest.mark.parametrize("text, message", [
        ("version: 2\nproviders: {}\ntasks: {}\n", "unsupported policy version"),
        ("- not\n- a\n- mapping\n", "must be a mapping"),
        ("version: 1\nflags: {dpa_ok: {description: x}}\nproviders: {}\ntasks: {}\n", "needs a non-empty 'env'"),
        ("version: 1\nflags: {}\nproviders: {openai: {requires: [dpa_ok]}}\ntasks: {}\n", "undeclared flag 'dpa_ok'"),
        ("version: 1\nflags: {}\nproviders: {openai: {requires: dpa_ok}}\ntasks: {}\n", "must be a list of names"),
        ("version: 1\nflags: {}\nproviders: {openai: {}}\ntasks: {chat_turn: {provider: local, on_refuse: skip}}\n",
         "undeclared provider 'local'"),
        ("version: 1\nflags: {}\nproviders: {openai: {}}\ntasks: {chat_turn: {provider: openai, on_refuse: explode}}\n",
         "on_refuse must be one of"),
    ])
    def test_validation_fails_closed(self, tmp_path, text, message):
        with pytest.raises(PolicyError, match=message):
            Policy.load(write_policy(tmp_path, text))

    def test_from_dict_round_trip_and_describe(self, monkeypatch):
        policy = Policy.from_dict({
            "version": 1,
            "flags": {"dpa_ok": {"env": "COMPLIANCE_DPA_OK"}},
            "providers": {"openai": {"requires": ["dpa_ok", "scrubbed"]}},
            "tasks": {"chat_turn": {"provider": "openai", "on_refuse": "scripted_fallback"}},
        }, source="inline")
        monkeypatch.setenv("COMPLIANCE_DPA_OK", "true")
        assert policy.describe() == {"flags": {"dpa_ok": True},
                                     "tasks": {"chat_turn": {"provider": "openai", "on_refuse": "scripted_fallback"}},
                                     "tools": {}}
        monkeypatch.delenv("COMPLIANCE_DPA_OK")
        assert policy.describe()["flags"] == {"dpa_ok": False}


class TestFlags:

    @pytest.mark.parametrize("value, expected", [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), (" Yes ", True),
        ("0", False), ("false", False), ("no", False), ("", False), ("on", False), ("maybe", False),
    ])
    def test_truthy_values(self, monkeypatch, value, expected):
        monkeypatch.setenv("COMPLIANCE_DPA_OK", value)
        assert Policy.load().flag_value("dpa_ok") is expected

    def test_unset_and_unknown_flags_are_false(self, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        policy = Policy.load()
        assert policy.flag_value("dpa_ok") is False and policy.flag_value("nonexistent") is False
        assert policy.flag_values() == {"dpa_ok": False}

    def test_flags_are_resolved_at_decision_time_not_load_time(self, monkeypatch, tmp_path):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        router = Router(gated_policy(tmp_path))
        assert router.decide("chat_turn", scrubbed=True, providers=PROVIDERS).reason == "dpa_not_confirmed"
        monkeypatch.setenv("COMPLIANCE_DPA_OK", "true")
        assert router.decide("chat_turn", scrubbed=True, providers=PROVIDERS).allow is True


class TestDecide:

    @pytest.fixture
    def router(self):
        return Router(Policy.load())

    def test_allowed_decision(self, router):
        decision = router.decide("chat_turn", scrubbed=True, providers=PROVIDERS, health=Health())
        assert decision == Decision(allow=True, provider="openai", reason="ok", on_refuse="scripted_fallback", task="chat_turn")

    def test_not_scrubbed(self, router):
        decision = router.decide("triage", scrubbed=False, providers=PROVIDERS)
        assert (decision.allow, decision.reason, decision.on_refuse) == (False, "not_scrubbed", "pending_clinician_review")

    def test_dpa_not_confirmed_is_checked_before_scrubbed(self, monkeypatch, tmp_path):
        """A flag requirement is reported before `scrubbed` when both fail (gated policy, not the shipped one)."""
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        router = Router(gated_policy(tmp_path))
        assert router.decide("triage", scrubbed=False, providers=PROVIDERS).reason == "dpa_not_confirmed"

    def test_local_provider_has_no_requirements(self, router, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        decision = router.decide("pii_detect", scrubbed=False, providers=PROVIDERS)
        assert decision.allow is True and decision.provider == "local" and decision.on_refuse == "skip"

    def test_provider_unconfigured(self, router):
        decision = router.decide("chat_turn", scrubbed=True, providers={"local": object()})
        assert (decision.allow, decision.provider, decision.reason) == (False, "openai", "provider_unconfigured")
        assert router.decide("chat_turn", scrubbed=True, providers={}).reason == "provider_unconfigured"

    def test_provider_override_takes_precedence_over_yaml(self, router):
        decision = router.decide("chat_turn", scrubbed=False, providers=PROVIDERS, provider="local")
        assert decision.allow is True and decision.provider == "local"
        assert decision.on_refuse == "scripted_fallback"  # on_refuse still comes from the task's YAML entry

    def test_override_to_undeclared_provider_refuses(self, router, caplog):
        with caplog.at_level(logging.WARNING, logger="ovis.inference.router"):
            decision = router.decide("chat_turn", scrubbed=True, providers={"ollama": object()}, provider="ollama")
        assert decision.reason == "provider_unconfigured" and decision.provider == "ollama"
        assert "not declared in the routing policy" in caplog.text

    def test_missing_task(self, router):
        decision = router.decide("summarise", scrubbed=True, providers=PROVIDERS)
        assert decision == Decision(allow=False, provider=None, reason="policy_missing_task", on_refuse=None, task="summarise")

    def test_missing_task_with_override_still_runs(self, router):
        decision = router.decide("summarise", scrubbed=True, providers=PROVIDERS, provider="local")
        assert decision.allow is True and decision.on_refuse is None

    def test_unhealthy_provider(self, router):
        health = Health()
        for _ in range(3):
            health.record_failure("openai")
        decision = router.decide("chat_turn", scrubbed=True, providers=PROVIDERS, health=health)
        assert decision.reason == "provider_unhealthy" and decision.provider == "openai"
        assert router.decide("pii_detect", scrubbed=True, providers=PROVIDERS, health=health).allow is True

    def test_no_health_means_healthy(self, router):
        assert router.decide("chat_turn", scrubbed=True, providers=PROVIDERS, health=None).allow is True

    def test_refused_copy_keeps_routing(self, router):
        decision = router.decide("chat_turn", scrubbed=True, providers=PROVIDERS)
        refused = decision.refused("leak_check_failed")
        assert refused.allow is False and refused.reason == "leak_check_failed"
        assert (refused.provider, refused.on_refuse, refused.task) == ("openai", "scripted_fallback", "chat_turn")

    def test_every_reason_is_in_the_catalogue(self, router, monkeypatch, tmp_path):
        seen = set()
        seen.add(router.decide("chat_turn", scrubbed=True, providers=PROVIDERS).reason)
        seen.add(router.decide("chat_turn", scrubbed=False, providers=PROVIDERS).reason)
        seen.add(router.decide("chat_turn", scrubbed=True, providers={}).reason)
        seen.add(router.decide("nope", scrubbed=True, providers=PROVIDERS).reason)
        health = Health()
        for _ in range(3):
            health.record_failure("openai")
        seen.add(router.decide("chat_turn", scrubbed=True, providers=PROVIDERS, health=health).reason)
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        # The shipped policy gates on no flag, so the flag reason comes from a gated policy.
        seen.add(Router(gated_policy(tmp_path)).decide("chat_turn", scrubbed=True, providers=PROVIDERS).reason)
        seen.add("leak_check_failed")
        # Tool reasons come from decide_tool, which the task-level decide() never returns.
        tool_kwargs = dict(scrubbed=True, task="chat_turn", provider="openai", on_refuse="scripted_fallback")
        seen.add(router.decide_tool("never_declared", **tool_kwargs).reason)
        seen.add("tool_not_registered")
        seen.add("tool_output_unscrubbed")
        assert seen == set(REASONS)
        assert FLAG_REASONS == {"dpa_ok": "dpa_not_confirmed"}

    def test_unknown_flag_reason_falls_back_to_generic_name(self, monkeypatch):
        policy = Policy.from_dict({
            "version": 1,
            "flags": {"baa_ok": {"env": "COMPLIANCE_BAA_OK"}},
            "providers": {"openai": {"requires": ["baa_ok"]}},
            "tasks": {"chat_turn": {"provider": "openai", "on_refuse": "scripted_fallback"}},
        })
        monkeypatch.delenv("COMPLIANCE_BAA_OK", raising=False)
        assert Router(policy).decide("chat_turn", scrubbed=True, providers=PROVIDERS).reason == "baa_ok_not_confirmed"


class TestWarnings:

    def test_warns_only_for_routed_gated_providers(self, monkeypatch, tmp_path):
        router = Router(gated_policy(tmp_path))
        monkeypatch.setenv("COMPLIANCE_DPA_OK", "true")
        assert router.warnings({"chat_turn": "openai"}) == []
        monkeypatch.delenv("COMPLIANCE_DPA_OK")
        assert router.warnings({"chat_turn": "openai", "pii_detect": "local"}) == [
            "inference policy dpa_ok=false: openai-routed tasks will refuse"]
        assert router.warnings({"chat_turn": "local", "pii_detect": "local"}) == []
        assert router.warnings({"chat_turn": None}) == []


class TestHealth:

    def test_threshold_and_cooldown_with_fake_clock(self):
        now = [0.0]
        health = Health(threshold=3, cooldown_s=60.0, clock=lambda: now[0])
        health.record_failure("openai")
        health.record_failure("openai")
        assert health.is_healthy("openai") is True and health.failures("openai") == 2
        health.record_failure("openai")
        assert health.is_healthy("openai") is False
        now[0] = 59.999
        assert health.is_healthy("openai") is False
        now[0] = 60.0
        assert health.is_healthy("openai") is True  # cooldown elapsed: one probe allowed
        health.record_failure("openai")  # probe failed: re-trips immediately (4 >= 3)
        assert health.is_healthy("openai") is False

    def test_success_resets(self):
        health = Health()
        for _ in range(3):
            health.record_failure("openai")
        assert health.is_healthy("openai") is False
        health.record_success("openai")
        assert health.is_healthy("openai") is True and health.failures("openai") == 0

    def test_providers_are_independent(self):
        health = Health()
        for _ in range(3):
            health.record_failure("openai")
        assert health.is_healthy("local") is True and health.is_healthy("openai") is False
        assert health.snapshot(["openai", "local"]) == {"openai": False, "local": True}

    def test_marking_unhealthy_is_logged_without_content(self, caplog):
        health = Health()
        with caplog.at_level(logging.WARNING, logger="ovis.inference.router"):
            for _ in range(3):
                health.record_failure("openai")
        assert "marked unhealthy after 3 consecutive failures" in caplog.text
