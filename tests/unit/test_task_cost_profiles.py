"""Cost tiering: the conversation stays cheap, triage is allowed to think.

Chat runs on every patient message with someone watching a typing indicator; triage runs once per
session in the background. These are the assertions that stop that split from quietly eroding -- a
call site raising chat effort, or handing tools to a task that should never pay for a second hop.
"""

import pytest

from app.inference import InferenceRequest, ToolRegistry, ToolSpec
from app.inference.gateway import CHEAP_EFFORTS, MODEL_ENV, TASK_PROFILES, TASKS, InferenceGateway
from app.inference.router import Router
from app.florence_utils import TriageAssessmentOutput
from tests.mock_openai import FakeProvider, permissive_policy


class TestTheTable:

    def test_every_task_declares_a_profile(self):
        assert set(TASK_PROFILES) == set(TASKS)

    def test_conversation_is_cheap(self):
        assert TASK_PROFILES["chat_turn"].effort in CHEAP_EFFORTS

    def test_triage_is_not_cheap(self):
        assert TASK_PROFILES["triage"].effort == "high"
        assert TASK_PROFILES["triage"].effort not in CHEAP_EFFORTS

    def test_only_the_conversation_may_call_tools(self):
        """A background task paying for extra hops would be a cost bug with no upside."""
        assert TASK_PROFILES["chat_turn"].max_tool_hops >= 1
        for task in ("symptom_assessment", "triage", "pii_detect", "memory_extraction"):
            assert TASK_PROFILES[task].max_tool_hops == 0

    def test_a_chat_turn_is_capped_at_one_tool_hop(self):
        """Two model calls is the worst case for a recording turn, not four."""
        assert TASK_PROFILES["chat_turn"].max_tool_hops == 1

    def test_only_the_conversation_settles_tools_without_a_second_call(self):
        """text_ends_turn is only safe for write tools whose result the model need not read."""
        assert TASK_PROFILES["chat_turn"].text_ends_turn is True
        for task in ("symptom_assessment", "triage", "pii_detect", "memory_extraction"):
            assert TASK_PROFILES[task].text_ends_turn is False

    def test_each_task_can_be_pointed_at_its_own_model(self):
        """Tier by model, not just by effort: chat and triage read different env vars."""
        assert MODEL_ENV["chat_turn"] != MODEL_ENV["triage"]
        assert {"chat_turn", "triage"} <= set(MODEL_ENV)


class TestRequestDefaults:

    def test_a_request_takes_its_cost_from_the_task(self):
        assert InferenceRequest(task="chat_turn", messages=[]).effort == "low"
        assert InferenceRequest(task="triage", messages=[]).effort == "high"

    def test_an_explicit_effort_still_wins(self):
        assert InferenceRequest(task="chat_turn", messages=[], effort="high").effort == "high"

    def test_hops_default_from_the_task(self):
        assert InferenceRequest(task="chat_turn", messages=[]).max_tool_hops == 1
        assert InferenceRequest(task="triage", messages=[]).max_tool_hops == 0


def gateway(provider):
    return InferenceGateway({"openai": provider}, {}, router=Router(permissive_policy(["openai"])))


def registry(calls):
    return ToolRegistry([ToolSpec(name="record_symptom", description="d", parameters={"type": "object"},
                                  executor=lambda args: calls.append(args) or {"ok": True})])


class TestARecordingTurnCostsOneRoundTrip:

    @pytest.mark.asyncio
    async def test_answering_and_recording_together_does_not_pay_a_second_call(self):
        ran = []
        provider = FakeProvider(name="openai", tool_script=[
            ("And how has your appetite been?", [("record_symptom", '{"symptom": "fatigue"}')]),
        ])
        result = await gateway(provider).complete(
            InferenceRequest(task="chat_turn", messages=[{"role": "user", "content": "tired"}],
                             scrubbed=True, tools=registry(ran)))
        assert len(provider.requests) == 1           # the whole point
        assert ran == [{"symptom": "fatigue"}]       # the tool still ran
        assert result.text == "And how has your appetite been?"
        assert len(result.tool_items) == 2           # and is replayed next turn

    @pytest.mark.asyncio
    async def test_recording_without_answering_falls_back_to_a_second_call(self):
        provider = FakeProvider(name="openai", tool_script=[
            ("", [("record_symptom", '{"symptom": "fatigue"}')]),
            ("And your appetite?", []),
        ])
        result = await gateway(provider).complete(
            InferenceRequest(task="chat_turn", messages=[{"role": "user", "content": "tired"}],
                             scrubbed=True, tools=registry([])))
        assert len(provider.requests) == 2
        assert result.text == "And your appetite?"

    @pytest.mark.asyncio
    async def test_a_chat_turn_never_exceeds_two_model_calls(self):
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", "{}")])])
        await gateway(provider).complete(
            InferenceRequest(task="chat_turn", messages=[{"role": "user", "content": "hi"}],
                             scrubbed=True, tools=registry([])))
        assert len(provider.requests) == 2


class TestModelTieringIsReadyOnDemand:
    """Only gpt-5.6-sol is deployed on the Azure resource today (probed 2026-09-10), so every task
    shares a model and just the effort split is live. That is a fine configuration - what has to
    hold is that switching to a second deployment is config, never code."""

    def _gateway(self, chat_model=None, triage_model="gpt-5.6-sol"):
        overrides = {"triage": triage_model}
        if chat_model:
            overrides["chat_turn"] = chat_model
        return InferenceGateway({"openai": FakeProvider(name="openai", model=triage_model)}, {},
                                task_models={"openai": overrides},
                                router=Router(permissive_policy(["openai"])))

    def test_one_deployment_reports_shared_tiering(self):
        profile = self._gateway().cost_profile()
        assert profile["model_tiering"] == "shared"
        assert profile["tasks"]["chat_turn"]["tier"] == "cheap"
        assert profile["tasks"]["triage"]["tier"] == "deep"

    def test_pointing_chat_elsewhere_splits_it_with_no_code_change(self):
        profile = self._gateway(chat_model="gpt-5-mini").cost_profile()
        assert profile["model_tiering"] == "split"
        assert profile["tasks"]["chat_turn"]["model"] == "gpt-5-mini"
        assert profile["tasks"]["triage"]["model"] == "gpt-5.6-sol"

    def test_the_profile_names_the_knob_for_each_task(self):
        tasks = self._gateway().cost_profile()["tasks"]
        assert tasks["chat_turn"]["model_env"] == "OPENAI_CHAT_MODEL"
        assert tasks["triage"]["model_env"] == "OPENAI_TRIAGE_MODEL"

    def test_a_shared_deployment_is_not_a_startup_warning(self):
        """It is an accepted configuration; warning about it every boot just trains people to
        ignore warnings. It belongs in /health, which is where cost_profile() surfaces."""
        assert self._gateway().log_policy_state() == []

    def test_it_is_visible_on_health(self):
        assert "cost" in self._gateway().describe()

    @pytest.mark.asyncio
    async def test_the_override_actually_reaches_the_provider(self):
        """The seam end to end: a per-task model must land on the call, not just in a dict."""
        provider = FakeProvider(name="openai", model="gpt-5.6-sol")
        gateway = InferenceGateway({"openai": provider}, {},
                                   task_models={"openai": {"chat_turn": "gpt-5-mini"}},
                                   router=Router(permissive_policy(["openai"])))
        await gateway.complete(InferenceRequest(task="chat_turn", messages=[{"role": "user", "content": "hi"}],
                                                scrubbed=True))
        await gateway.complete(InferenceRequest(task="triage", messages=[{"role": "user", "content": "hi"}],
                                                scrubbed=True, schema=TriageAssessmentOutput))
        assert provider.models == ["gpt-5-mini", "gpt-5.6-sol"]


class TestChatAsksForAnEffortTheDeploymentAccepts:
    """gpt-5.6-sol rejects `minimal` and reports none/low/medium/high/max/xhigh (probed live), so
    `low` is asked for directly: cheap tier, accepted first time, and enough budget for the turn to
    judge severity and decide whether to record."""

    def test_chat_asks_for_low(self):
        assert TASK_PROFILES["chat_turn"].effort == "low"

    def test_low_is_still_a_cheap_tier(self):
        assert TASK_PROFILES["chat_turn"].effort in CHEAP_EFFORTS

    def test_chat_stays_below_triage(self):
        assert TASK_PROFILES["chat_turn"].effort != TASK_PROFILES["triage"].effort

    def test_it_is_not_an_effort_the_deployment_rejects(self):
        """`minimal` costs a failed round trip per process against gpt-5.6-sol."""
        assert TASK_PROFILES["chat_turn"].effort != "minimal"

    def test_it_still_negotiates_for_a_model_without_low(self):
        from app.inference.providers import choose_effort
        assert choose_effort("low", {"minimal", "medium"}) == "minimal"
