"""The gateway's bounded tool loop: gating, execution, replay, refusals, hop cap and audit.

Every hop is leak-checked and audited here rather than at the call site, so these tests are what
stop a future call site from re-implementing (and mis-implementing) any of it.
"""

import json

import pytest

from app.inference import InferenceRequest, ToolArgumentError, ToolRegistry, ToolSpec
from app.inference.gateway import InferenceGateway, InferenceRefused
from app.inference.router import Policy, Router
from tests.mock_openai import FakeProvider, permissive_policy
from tests.unit.test_inference_gateway import RecordingSink

ARGS = {"type": "object", "properties": {"symptom": {"type": "string"}}, "required": ["symptom"]}


def spec(name="record_symptom", executor=None):
    return ToolSpec(name=name, description="record one symptom", parameters=ARGS,
                    executor=executor or (lambda args: {"recorded": args.get("symptom")}))


def gateway(provider, policy=None, sink=None):
    return InferenceGateway({"openai": provider}, {}, router=Router(policy or permissive_policy(["openai"])),
                            audit_sink=sink)


def req(tools=None, messages=None, **kw):
    return InferenceRequest(task="chat_turn", messages=messages or [{"role": "user", "content": "hi"}],
                            scrubbed=True, tools=tools, **kw)


class TestLoopMechanics:

    @pytest.mark.asyncio
    async def test_no_tools_takes_the_single_shot_path(self):
        provider = FakeProvider(name="openai", chat_reply="How are you?")
        result = await gateway(provider).complete(req())
        assert result.text == "How are you?"
        assert result.tool_calls == ()
        assert provider.tools_offered == []  # chat(), not chat_with_tools()

    @pytest.mark.asyncio
    async def test_call_is_executed_and_the_answer_comes_from_the_next_hop(self):
        seen = []
        provider = FakeProvider(name="openai", tool_script=[
            ("", [("record_symptom", '{"symptom": "fatigue"}')]),
            ("And how has your appetite been?", []),
        ])
        registry = ToolRegistry([spec(executor=lambda args: seen.append(args) or {"recorded": args["symptom"]})])
        result = await gateway(provider).complete(req(tools=registry))
        assert seen == [{"symptom": "fatigue"}]
        assert result.text == "And how has your appetite been?"

    @pytest.mark.asyncio
    async def test_call_and_result_are_replayed_to_the_model(self):
        provider = FakeProvider(name="openai", tool_script=[
            ("", [("record_symptom", '{"symptom": "fatigue"}')]),
            ("Thanks.", []),
        ])
        await gateway(provider).complete(req(tools=ToolRegistry([spec()])))
        replayed = provider.requests[1].messages
        assert replayed[-2]["type"] == "function_call" and replayed[-2]["name"] == "record_symptom"
        assert replayed[-1]["type"] == "function_call_output"
        assert replayed[-2]["call_id"] == replayed[-1]["call_id"]
        assert json.loads(replayed[-1]["output"]) == {"recorded": "fatigue"}

    @pytest.mark.asyncio
    async def test_several_calls_in_one_hop_all_run(self):
        provider = FakeProvider(name="openai", tool_script=[
            ("", [("record_symptom", '{"symptom": "fatigue"}'), ("record_symptom", '{"symptom": "cough"}')]),
            ("Thanks.", []),
        ])
        await gateway(provider).complete(req(tools=ToolRegistry([spec()])))
        outputs = [m for m in provider.requests[1].messages if m.get("type") == "function_call_output"]
        assert [json.loads(o["output"])["recorded"] for o in outputs] == ["fatigue", "cough"]

    @pytest.mark.asyncio
    async def test_hop_cap_terminates_even_if_the_provider_keeps_calling(self):
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", '{"symptom": "fatigue"}')])])
        result = await gateway(provider).complete(req(tools=ToolRegistry([spec()]), max_tool_hops=2))
        assert len(provider.requests) == 3            # hops 0, 1, then the capped hop
        assert provider.tools_offered[-1] is None     # the last hop is offered no tools
        assert result is not None

    @pytest.mark.asyncio
    async def test_zero_hops_means_no_tools_are_ever_offered(self):
        provider = FakeProvider(name="openai", tool_script=[("Hello.", [])])
        await gateway(provider).complete(req(tools=ToolRegistry([spec()]), max_tool_hops=0))
        assert provider.tools_offered == [None]


class TestRefusals:

    @pytest.mark.asyncio
    async def test_tool_the_policy_does_not_declare_is_refused(self):
        provider = FakeProvider(name="openai", tool_script=[("", [("get_recent_checkins", "{}")]), ("hi", [])])
        registry = ToolRegistry([spec(name="get_recent_checkins")])
        with pytest.raises(InferenceRefused) as excinfo:
            await gateway(provider).complete(req(tools=registry))
        assert excinfo.value.reason == "policy_missing_tool"

    @pytest.mark.asyncio
    async def test_tool_the_registry_does_not_hold_is_refused(self):
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", "{}")]), ("hi", [])])
        with pytest.raises(InferenceRefused) as excinfo:
            await gateway(provider).complete(req(tools=ToolRegistry([spec(name="note_unassessable")])))
        assert excinfo.value.reason == "tool_not_registered"

    @pytest.mark.asyncio
    async def test_refused_tool_never_runs(self):
        ran = []
        provider = FakeProvider(name="openai", tool_script=[("", [("get_recent_checkins", "{}")]), ("hi", [])])
        registry = ToolRegistry([spec(name="get_recent_checkins", executor=lambda a: ran.append(a))])
        with pytest.raises(InferenceRefused):
            await gateway(provider).complete(req(tools=registry))
        assert ran == []

    @pytest.mark.asyncio
    async def test_stored_phi_tool_without_a_scrubber_is_refused(self):
        """`discloses: stored_phi` is load-bearing, not documentation."""
        policy = permissive_policy(["openai"], tools={"get_symptom_trend": {"requires": [], "discloses": "stored_phi"}})
        provider = FakeProvider(name="openai", tool_script=[("", [("get_symptom_trend", "{}")]), ("hi", [])])
        registry = ToolRegistry([spec(name="get_symptom_trend")])
        with pytest.raises(InferenceRefused) as excinfo:
            await gateway(provider, policy=policy).complete(req(tools=registry))
        assert excinfo.value.reason == "tool_output_unscrubbed"

    @pytest.mark.asyncio
    async def test_stored_phi_tool_runs_when_output_is_scrubbed(self):
        policy = permissive_policy(["openai"], tools={"get_symptom_trend": {"requires": [], "discloses": "stored_phi"}})
        provider = FakeProvider(name="openai", tool_script=[("", [("get_symptom_trend", "{}")]), ("hi", [])])
        registry = ToolRegistry([spec(name="get_symptom_trend", executor=lambda a: {"note": "Test Patient"})])
        result = await gateway(provider, policy=policy).complete(
            req(tools=registry, scrub_tool_output=lambda t: t.replace("Test Patient", "[PERSON_1]")))
        assert result.text == "hi"
        output = provider.requests[1].messages[-1]["output"]
        assert "Test Patient" not in output and "[PERSON_1]" in output


class TestBadArgumentsKeepTheTurnAlive:

    @pytest.mark.asyncio
    async def test_malformed_json_comes_back_as_a_typed_error(self):
        provider = FakeProvider(name="openai", tool_script=[
            ("", [("record_symptom", "{not json")]),
            ("Sorry, could you say that again?", []),
        ])
        result = await gateway(provider).complete(req(tools=ToolRegistry([spec()])))
        assert result.text == "Sorry, could you say that again?"
        assert "error" in json.loads(provider.requests[1].messages[-1]["output"])

    @pytest.mark.asyncio
    async def test_arguments_that_are_not_an_object_are_rejected(self):
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", '"fatigue"')]), ("ok", [])])
        await gateway(provider).complete(req(tools=ToolRegistry([spec()])))
        assert "JSON object" in json.loads(provider.requests[1].messages[-1]["output"])["error"]

    @pytest.mark.asyncio
    async def test_executor_rejection_reaches_the_model_verbatim(self):
        def picky(args):
            raise ToolArgumentError("severity must be 1-5")
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", "{}")]), ("ok", [])])
        await gateway(provider).complete(req(tools=ToolRegistry([spec(executor=picky)])))
        assert json.loads(provider.requests[1].messages[-1]["output"])["error"] == "severity must be 1-5"

    @pytest.mark.asyncio
    async def test_a_broken_tool_does_not_end_the_turn(self):
        def broken(args):
            raise RuntimeError("mongo is down")
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", "{}")]), ("ok", [])])
        result = await gateway(provider).complete(req(tools=ToolRegistry([spec(executor=broken)])))
        assert result.text == "ok"
        # The model is told it failed, but never why: an exception message may carry anything.
        assert json.loads(provider.requests[1].messages[-1]["output"]) == {"error": "the tool could not run"}


class TestLeakCheckOnLaterHops:

    @pytest.mark.asyncio
    async def test_identifier_in_a_tool_result_refuses_the_turn(self):
        """Fail closed: the loop re-checks the outbound messages it just grew."""
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", "{}")]), ("hi", [])])
        registry = ToolRegistry([spec(executor=lambda a: {"note": "Test Patient was tired"})])
        with pytest.raises(InferenceRefused) as excinfo:
            await gateway(provider).complete(req(tools=registry, known_identifiers=["Test Patient"]))
        assert excinfo.value.reason == "leak_check_failed"
        assert len(provider.requests) == 1  # never reached the model a second time

    @pytest.mark.asyncio
    async def test_clean_tool_result_passes_the_check(self):
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", "{}")]), ("hi", [])])
        registry = ToolRegistry([spec(executor=lambda a: {"recorded": "fatigue"})])
        result = await gateway(provider).complete(req(tools=registry, known_identifiers=["Test Patient"]))
        assert result.text == "hi"


class TestAudit:

    @pytest.mark.asyncio
    async def test_one_event_per_hop_and_per_tool_call(self):
        sink = RecordingSink()
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", "{}")]), ("done", [])])
        await gateway(provider, sink=sink).complete(req(tools=ToolRegistry([spec()])))
        kinds = [(e.tool, e.hop, e.decision) for e in sink.events]
        assert kinds == [(None, 0, "allow"), ("record_symptom", 0, "allow"), (None, 1, "allow")]

    @pytest.mark.asyncio
    async def test_a_refused_tool_is_its_own_audit_row(self):
        sink = RecordingSink()
        provider = FakeProvider(name="openai", tool_script=[("", [("record_symptom", "{}")]), ("hi", [])])
        with pytest.raises(InferenceRefused):
            await gateway(provider, sink=sink).complete(req(tools=ToolRegistry([spec(name="note_unassessable")])))
        refusals = [e for e in sink.events if e.decision == "refuse"]
        assert len(refusals) == 1
        assert refusals[0].tool == "record_symptom" and refusals[0].reason == "tool_not_registered"

    @pytest.mark.asyncio
    async def test_audit_never_carries_arguments_or_results(self):
        sink = RecordingSink()
        provider = FakeProvider(name="openai", tool_script=[
            ("", [("record_symptom", '{"evidence": "I have been very tired"}')]), ("hi", [])])
        await gateway(provider, sink=sink).complete(req(tools=ToolRegistry([spec()])))
        blob = json.dumps([e.to_dict() for e in sink.events], default=str)
        assert "very tired" not in blob and "evidence" not in blob
