"""Symptom coverage tools end to end through /florence/*.

Shadow mode: the tools record what Florence judged during the conversation, while the after-the-fact
extraction still produces `structured_assessment`. Both land on the record so they can be compared.
"""

import json

import pytest

from app.inference import InferenceGateway, reset_gateway
from app.florence import wait_for_background
from app.florence_tools import SYMPTOMS
from app.inference.gateway import CHEAP_EFFORTS
from app.florence_utils import REFUSED_MESSAGES
from tests.integration.test_florence_endpoints import START, finish, install, say, start
from tests.mock_openai import FakeProvider


def record(symptom, severity=3, evidence="I have been very tired", present=True):
    return ("record_symptom", json.dumps({"symptom": symptom, "present": present,
                                          "severity": severity, "evidence": evidence}))


def unassessable(symptom, reason="patient had to stop"):
    return ("note_unassessable", json.dumps({"symptom": symptom, "reason": reason}))


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_gateway(InferenceGateway({}))


class TestRecordingDuringAChat:

    async def test_a_call_records_coverage_and_reports_what_remains(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", tool_script=[
            ("", [record("fatigue")]),
            ("And how has your appetite been?", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        turn = await say(client, patient_headers, session_id, "I've been lying down most afternoons")
        assert turn["response"] == "And how has your appetite been?"
        assert turn["coverage"]["recorded"] == ["fatigue"]
        assert turn["coverage"]["missing"] == ["appetite", "nausea", "cough", "discomfort"]
        assert turn["coverage"]["complete"] is False

    async def test_the_patient_never_sees_the_tool(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", tool_script=[("", [record("fatigue")]), ("How is your appetite?", [])]))
        session_id = (await start(client, patient_headers))["session_id"]
        turn = await say(client, patient_headers, session_id, "very tired")
        assert "record_symptom" not in turn["response"]
        session = seeded_db["florence_sessions"].find_one({"session_id": session_id})
        # Tool items stay off the transcript: the clinician view and analytics both read this list.
        assert all("role" in m and m.get("type") is None for m in session["conversation_history"])

    async def test_evidence_is_stored_with_the_rating(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", tool_script=[
            ("", [record("fatigue", severity=2, evidence="only a bit tired after lunch")]), ("ok", [])]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "only a bit tired after lunch")
        session = seeded_db["florence_sessions"].find_one({"session_id": session_id})
        stored = session["symptom_state"]["records"]["fatigue"]
        assert stored["severity"] == 2 and stored["evidence"] == "only a bit tired after lunch"

    async def test_unassessable_settles_a_symptom_without_a_rating(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", tool_script=[("", [unassessable("cough")]), ("Take care.", [])]))
        session_id = (await start(client, patient_headers))["session_id"]
        turn = await say(client, patient_headers, session_id, "I need to go now")
        assert turn["coverage"]["unassessable"] == ["cough"]
        assert "cough" not in turn["coverage"]["missing"]


class TestCoveragePersistsAcrossTurns:

    async def test_state_accumulates_over_several_turns(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", tool_script=[
            ("", [record("fatigue")]), ("And appetite?", []),
            ("", [record("appetite")]), ("And nausea?", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "tired")
        second = await say(client, patient_headers, session_id, "not eating much")
        assert second["coverage"]["recorded"] == ["fatigue", "appetite"]

    async def test_the_model_gets_its_own_call_history_back(self, client, patient_headers, seeded_db):
        """The correctness half of the scrub work: without this Florence re-asks what she recorded."""
        provider = install(FakeProvider(name="openai", tool_script=[
            ("", [record("fatigue")]), ("And appetite?", []), ("Anything else?", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "tired")
        await say(client, patient_headers, session_id, "eating fine")
        replayed = provider.requests[-1].messages
        kinds = [m.get("type") for m in replayed if m.get("type")]
        assert kinds == ["function_call", "function_call_output"]
        call = next(m for m in replayed if m.get("type") == "function_call")
        assert call["name"] == "record_symptom"

    async def test_tool_items_sit_in_conversation_order(self, client, patient_headers, seeded_db):
        provider = install(FakeProvider(name="openai", tool_script=[
            ("", [record("fatigue")]), ("And appetite?", []), ("Anything else?", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "tired")
        await say(client, patient_headers, session_id, "eating fine")
        replayed = provider.requests[-1].messages
        types = [m.get("type") or m.get("role") for m in replayed]
        # opening, the patient turn that triggered it, the call + its result, then the reply
        assert types[:5] == ["assistant", "user", "function_call", "function_call_output", "assistant"]

    async def test_two_recording_turns_replay_in_the_right_places(self, client, patient_headers, seeded_db):
        """Each turn's items go back where they happened, not all after the first one."""
        provider = install(FakeProvider(name="openai", tool_script=[
            ("", [record("fatigue")]), ("And appetite?", []),
            ("", [record("appetite", evidence="half a bowl")]), ("And nausea?", []),
            ("Anything else?", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "tired")
        await say(client, patient_headers, session_id, "half a bowl")
        await say(client, patient_headers, session_id, "no nausea")
        types = [m.get("type") or m.get("role") for m in provider.requests[-1].messages]
        assert types == [
            "assistant", "user", "function_call", "function_call_output",   # opening, turn 1
            "assistant", "user", "function_call", "function_call_output",   # turn 2
            "assistant", "user",                                            # turn 3
        ]

    async def test_recording_the_same_symptom_twice_keeps_the_later_rating(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", tool_script=[
            ("", [record("fatigue", severity=2)]), ("Tell me more?", []),
            ("", [record("fatigue", severity=4, evidence="actually worse than I said")]), ("I see.", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "a bit tired")
        await say(client, patient_headers, session_id, "actually worse than I said")
        session = seeded_db["florence_sessions"].find_one({"session_id": session_id})
        assert session["symptom_state"]["records"]["fatigue"]["severity"] == 4


class TestBadCallsDoNotBreakTheChat:

    async def test_a_rating_without_evidence_is_rejected_and_the_turn_continues(self, client, patient_headers, seeded_db):
        provider = install(FakeProvider(name="openai", tool_script=[
            ("", [("record_symptom", json.dumps({"symptom": "fatigue", "present": True, "severity": 3}))]),
            ("Could you tell me a little more?", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        turn = await say(client, patient_headers, session_id, "tired")
        assert turn["response"] == "Could you tell me a little more?"
        assert turn["coverage"]["recorded"] == []
        result = json.loads(provider.requests[-1].messages[-1]["output"])
        assert "evidence" in result["error"]

    async def test_an_unknown_symptom_is_rejected(self, client, patient_headers, seeded_db):
        provider = install(FakeProvider(name="openai", tool_script=[
            ("", [("record_symptom", json.dumps({"symptom": "headache", "present": True, "severity": 3}))]),
            ("I see.", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        turn = await say(client, patient_headers, session_id, "my head hurts")
        assert turn["coverage"]["recorded"] == []
        assert "must be one of" in json.loads(provider.requests[-1].messages[-1]["output"])["error"]

    async def test_a_tool_that_is_not_declared_falls_back_gracefully(self, client, patient_headers, seeded_db):
        """A refused tool takes chat_turn's scripted_fallback, so the patient still gets a reply
        and the session stays open rather than erroring out."""
        install(FakeProvider(name="openai", tool_script=[("", [("get_recent_checkins", "{}")]), ("hi", [])]))
        session_id = (await start(client, patient_headers))["session_id"]
        turn = await say(client, patient_headers, session_id, "how was I last week?")
        assert turn["response"] == REFUSED_MESSAGES["en"]
        assert turn["coverage"]["recorded"] == []


class TestTheRecord:

    async def test_coverage_lands_on_the_assessment_record(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", tool_script=[
            ("", [record("fatigue"), record("appetite", severity=2, evidence="eating half a bowl")]),
            ("Thank you.", []),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "tired and not eating")
        await finish(client, patient_headers, session_id)
        stored = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        coverage = stored["symptom_coverage"]
        assert coverage["recorded"] == ["fatigue", "appetite"]
        assert coverage["missing"] == ["nausea", "cough", "discomfort"]
        assert coverage["records"]["appetite"]["evidence"] == "eating half a bowl"

    async def test_shadow_mode_leaves_the_extraction_in_charge(self, client, patient_headers, seeded_db):
        """The tools do not yet own the record: `structured_assessment` still comes from the pass."""
        install(FakeProvider(name="openai", tool_script=[("", [record("fatigue", severity=5)]), ("Thanks.", [])]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "exhausted")
        await finish(client, patient_headers, session_id)
        stored = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert stored["structured_assessment"] is not None
        assert stored["structured_assessment"]["symptoms"]["fatigue"]["severity_rating"] == 3   # the canned extraction
        assert stored["symptom_coverage"]["records"]["fatigue"]["severity"] == 5                # what Florence judged

    async def test_the_extraction_never_sees_tool_items(self, client, patient_headers, seeded_db):
        provider = install(FakeProvider(name="openai", tool_script=[("", [record("fatigue")]), ("Thanks.", [])]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "tired")
        await finish(client, patient_headers, session_id)
        for request in [r for r in provider.requests if r.task in ("symptom_assessment", "triage")]:
            assert all(m.get("type") is None for m in request.messages)

    async def test_a_session_with_no_tool_calls_has_no_coverage_block(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", chat_reply="How are you feeling?"))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "fine thanks")
        await finish(client, patient_headers, session_id)
        stored = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert stored["symptom_coverage"] is None


class TestSymptomVocabulary:

    def test_the_five_match_what_the_prompt_asks_for(self):
        assert SYMPTOMS == ("fatigue", "appetite", "nausea", "cough", "discomfort")


class TestCostTiering:
    """The conversation is the hot path; triage is the one allowed to think."""

    async def test_chat_stays_cheap_and_triage_does_not(self, client, patient_headers, seeded_db):
        provider = install(FakeProvider(name="openai", tool_script=[
            ("How are you?", [record("fatigue")]), ("Thanks.", [])]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "tired")
        await finish(client, patient_headers, session_id)
        for request in provider.requests:
            if request.task == "chat_turn":
                assert request.effort in CHEAP_EFFORTS
            if request.task == "triage":
                assert request.effort == "high"

    async def test_triage_and_assessment_are_never_given_tools(self, client, patient_headers, seeded_db):
        provider = install(FakeProvider(name="openai", tool_script=[
            ("How are you?", [record("fatigue")]), ("Thanks.", [])]))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "tired")
        await finish(client, patient_headers, session_id)
        for request in provider.requests:
            if request.task in ("symptom_assessment", "triage"):
                assert request.tools is None
                assert request.max_tool_hops == 0

    async def test_a_recording_turn_is_one_model_call(self, client, patient_headers, seeded_db):
        """Florence answers and records together, so coverage costs no extra round trip."""
        provider = install(FakeProvider(name="openai", tool_script=[
            ("And how has your appetite been?", [record("fatigue")]),
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        before = len([r for r in provider.requests if r.task == "chat_turn"])
        turn = await say(client, patient_headers, session_id, "lying down most afternoons")
        chat_calls = len([r for r in provider.requests if r.task == "chat_turn"]) - before
        assert chat_calls == 1
        assert turn["response"] == "And how has your appetite been?"
        assert turn["coverage"]["recorded"] == ["fatigue"]
