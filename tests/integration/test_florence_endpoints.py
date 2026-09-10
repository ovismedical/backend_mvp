"""
Integration tests for Florence AI endpoints (/florence/*).
Runs the real code paths against a FakeProvider installed in the inference gateway, under the
real routing policy (openai requires dpa_ok + scrubbed), so every request the provider records is
exactly what would leave the building.
"""

import logging

import pytest

from app.inference import InferenceGateway, reset_gateway
from app.inference.audit import MongoAuditSink
from app.inference.refs import patient_ref, session_ref
from app.inference.scrub import ScrubContext, ScrubError
from app.florence import wait_for_background
from app.florence_utils import REFUSED_MESSAGES, generate_fallback_response
from tests.factories import make_user
from tests.mock_openai import FakeProvider, all_message_text, fake_gateway

START = {"language": "en", "input_mode": "keyboard", "treatment_status": "undergoing_treatment"}


@pytest.fixture
def mock_florence_ai():
    """Install a gateway backed by a FakeProvider so Florence runs its real code paths."""
    provider = FakeProvider(name="openai")
    reset_gateway(fake_gateway(openai=provider))
    yield provider
    reset_gateway(InferenceGateway({}))


def install(provider: FakeProvider, **kwargs) -> FakeProvider:
    reset_gateway(fake_gateway(openai=provider, **kwargs))
    return provider


async def start(client, headers, language="en"):
    resp = await client.post("/florence/start_session", json={**START, "language": language}, headers=headers)
    assert resp.status_code == 200
    return resp.json()


async def say(client, headers, session_id, message):
    resp = await client.post("/florence/send_message", json={"session_id": session_id, "message": message}, headers=headers)
    assert resp.status_code == 200
    return resp.json()


async def finish(client, headers, session_id):
    resp = await client.post(f"/florence/finish_session/{session_id}", headers=headers)
    assert resp.status_code == 200
    await wait_for_background()
    return (await client.get(f"/florence/result/{session_id}", headers=headers)).json()


def by_task(provider, task):
    return [r for r in provider.requests if r.task == task]


def messages_text(requests) -> str:
    """Outbound message contents only (the system prompt legitimately shows example placeholders)."""
    return "\n".join(m["content"] for r in requests for m in r.messages)


class TestFlorenceTest:

    async def test_florence_test_endpoint(self, client, patient_headers):
        response = await client.get("/florence/test", headers=patient_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "active_sessions" in body
        assert "policy" in body["inference"]

    async def test_florence_test_requires_auth(self, client):
        """Deployment/model names, per-provider health and the compliance flags are not public."""
        response = await client.get("/florence/test")
        assert response.status_code == 401


class TestStartSession:

    async def test_start_session(self, client, patient_headers, mock_florence_ai):
        response = await client.post("/florence/start_session", json=START, headers=patient_headers)
        assert response.status_code == 200
        body = response.json()
        assert "session_id" in body
        assert body["status"] == "active"
        assert len(body["message"]) > 0

    async def test_start_session_no_auth(self, client):
        response = await client.post("/florence/start_session", json={"language": "en"})
        assert response.status_code == 401

    async def test_opening_turn_introduces_the_patient_as_a_placeholder(self, client, patient_headers, mock_florence_ai):
        await start(client, patient_headers)
        [request] = mock_florence_ai.requests
        assert request.task == "chat_turn" and request.scrubbed is True
        assert request.messages == [{"role": "user", "content": "Hello, I'm [PERSON_1]. I'm here for my health check-in."}]
        assert "Test Patient" not in all_message_text([request]) and "testpatient" not in all_message_text([request])
        assert request.metadata["patient_ref"] == patient_ref("testpatient")
        assert request.metadata["task_source"] == "florence"
        assert "Test Patient" in request.known_identifiers and "testpatient" in request.known_identifiers

    async def test_reply_placeholder_is_reidentified_for_the_patient(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", chat_reply="Hello [PERSON_1], lovely to meet you!"))
        body = await start(client, patient_headers)
        assert body["message"] == "Hello Test Patient, lovely to meet you!"
        session = seeded_db["florence_sessions"].find_one({"session_id": body["session_id"]})
        assert session["conversation_history"][0]["content"] == "Hello Test Patient, lovely to meet you!"
        assert session["token_map"]["entries"][0] == ["[PERSON_1]", "PERSON", "Test Patient"]
        assert "user_info" not in session

    async def test_cjk_bracket_placeholder_is_reidentified(self, client, patient_headers, seeded_db):
        install(FakeProvider(name="openai", chat_reply="你好，【PERSON_1】！今日點呀？"))
        body = await start(client, patient_headers, language="zh-HK")
        assert "Test Patient" in body["message"] and "PERSON" not in body["message"]
        session = seeded_db["florence_sessions"].find_one({"session_id": body["session_id"]})
        assert "PERSON" not in session["conversation_history"][0]["content"]

    async def test_user_without_full_name_sees_their_username(self, client, patient_headers, seeded_db):
        # OTP-registered users never get a full_name key
        seeded_db["users"].update_one({"username": "testpatient"}, {"$unset": {"full_name": ""}})
        install(FakeProvider(name="openai", chat_reply="Hello [PERSON_1]!"))
        body = await start(client, patient_headers)
        assert body["message"] == "Hello testpatient!"
        assert "[PERSON_1]" not in body["message"] and "None" not in body["message"]

    async def test_user_with_blank_full_name_sees_their_username(self, client, patient_headers, seeded_db):
        seeded_db["users"].update_one({"username": "testpatient"}, {"$set": {"full_name": ""}})
        install(FakeProvider(name="openai", chat_reply="Hello [PERSON_1]!"))
        body = await start(client, patient_headers)
        assert body["message"] == "Hello testpatient!"

    async def test_session_endpoint_never_returns_the_token_map(self, client, patient_headers, mock_florence_ai):
        body = await start(client, patient_headers)
        session = (await client.get(f"/florence/session/{body['session_id']}", headers=patient_headers)).json()
        assert "token_map" not in session and "patient_ref" not in session


class TestSendMessage:

    async def _start_session(self, client, headers, mock_florence_ai):
        return (await start(client, headers))["session_id"]

    async def test_send_message(self, client, patient_headers, mock_florence_ai):
        session_id = await self._start_session(client, patient_headers, mock_florence_ai)
        response = await client.post(
            "/florence/send_message",
            json={"session_id": session_id, "message": "I feel tired today"},
            headers=patient_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert len(body["response"]) > 0

    async def test_send_message_invalid_session(self, client, patient_headers):
        response = await client.post(
            "/florence/send_message",
            json={"session_id": "nonexistent_123", "message": "Hello"},
            headers=patient_headers,
        )
        assert response.status_code == 404

    async def test_send_message_wrong_user(self, client, patient_headers, doctor_headers, mock_florence_ai):
        session_id = await self._start_session(client, patient_headers, mock_florence_ai)
        # Doctor tries to send message to patient's session
        response = await client.post(
            "/florence/send_message",
            json={"session_id": session_id, "message": "Hello"},
            headers=doctor_headers,
        )
        assert response.status_code == 403

    async def test_patient_identifiers_never_reach_the_provider(self, client, patient_headers, mock_florence_ai):
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "Hi, I'm Test Patient, username testpatient, email patient@test.com. I feel tired.")
        await say(client, patient_headers, session_id, "Dr. Test at Test Hospital told me to rest.")
        outbound = all_message_text(mock_florence_ai.requests)
        for identifier in ("Test Patient", "testpatient", "patient@test.com", "Dr. Test", "Test Hospital"):
            assert identifier not in outbound
        last = mock_florence_ai.requests[-1].messages
        assert last[-1]["role"] == "user" and "[PERSON_1]" in last[-3]["content"]
        assert "[PERSON_2]" in last[-1]["content"] and "[FACILITY_1]" in last[-1]["content"]

    async def test_patient_ref_never_appears_in_message_content(self, client, patient_headers, mock_florence_ai):
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "I feel tired")
        await finish(client, patient_headers, session_id)
        pref = patient_ref("testpatient")
        sref = session_ref(session_id)
        assert all(r.metadata["patient_ref"] == pref and r.metadata["session_ref"] == sref for r in mock_florence_ai.requests)
        assert pref not in all_message_text(mock_florence_ai.requests)
        assert sref not in all_message_text(mock_florence_ai.requests)
        assert {r.task for r in mock_florence_ai.requests} == {"chat_turn", "symptom_assessment", "triage"}

    async def test_common_words_in_the_patients_name_survive(self, client, patient_headers, mock_florence_ai):
        # "Test Patient" + "Dr. Test": the words test/patient are ordinary clinical vocabulary (E.4 / E.11)
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "I had a blood test and the patient leaflet says pain 7/10.")
        sent = mock_florence_ai.requests[-1].messages[-1]["content"]
        assert sent == "I had a blood test and the patient leaflet says pain 7/10."
        # ...and the symptom assessment is not refused by the leak check either
        result = await finish(client, patient_headers, session_id)
        assert result["triage_status"] == "completed"

    async def test_third_party_name_stays_scrubbed_on_later_turns(self, client, patient_headers, seeded_db):
        """E8: a name learned from a kinship cue on turn 1 is re-identified in the reply the patient sees,
        yet never reaches the provider again - not in later turns, not in the assessment/triage calls."""
        provider = install(FakeProvider(name="openai", chat_reply=[
            "Hello [PERSON_1]! How are you today?",
            "How kind of [PERSON_2]!",
            "That sounds lovely.",
        ]))
        session_id = (await start(client, patient_headers))["session_id"]
        turn1 = await say(client, patient_headers, session_id, "My daughter Mei Ling brought me soup")
        assert turn1["response"] == "How kind of Mei Ling!"
        turn2 = await say(client, patient_headers, session_id, "Mei Ling is visiting again tomorrow")
        assert turn2["response"] == "That sounds lovely."
        await finish(client, patient_headers, session_id)

        assert "Mei Ling" not in all_message_text(provider.requests[1:])
        chat_requests = by_task(provider, "chat_turn")
        # the stored (re-identified) assistant turn goes back out as [PERSON_2], the same token every time
        assert any("How kind of [PERSON_2]!" == m["content"] for m in chat_requests[-1].messages)
        assert "[PERSON_2] is visiting again tomorrow" == chat_requests[-1].messages[-1]["content"]
        assert "Mei Ling" not in all_message_text(by_task(provider, "symptom_assessment") + by_task(provider, "triage"))
        # the patient-visible transcript keeps the name; the token map persisted on the session
        session = seeded_db["florence_sessions"].find_one({"session_id": session_id})
        assert "Mei Ling" in session["conversation_history"][2]["content"]
        assert ["[PERSON_2]", "PERSON", "Mei Ling"] in session["token_map"]["entries"]
        # the gateway's leak check was armed with the learned name too
        assert "Mei Ling" in provider.requests[-1].known_identifiers

    async def test_date_of_birth_never_reaches_the_provider(self, client, patient_headers, mock_florence_ai):
        """make_user() carries birthdate only (legacy field) as 01/01/1990."""
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "I was born on 01/01/1990.")
        await say(client, patient_headers, session_id, "My date of birth is 1990-01-01, written the other way.")
        await say(client, patient_headers, session_id, "That is 1 January 1990 and I was born in 1990.")
        outbound = messages_text(mock_florence_ai.requests)
        assert "1990" not in outbound and "01/01" not in outbound and "days ago" not in outbound
        assert "[DOB]" in outbound and "[AGE · 30s]" in outbound
        assert "1990" not in all_message_text(mock_florence_ai.requests)

    async def test_token_map_is_persisted_and_reused_across_turns(self, client, patient_headers, seeded_db, mock_florence_ai):
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "My friend Mary drove me to Queen Mary Hospital")
        first = seeded_db["florence_sessions"].find_one({"session_id": session_id})["token_map"]
        assert ["[PERSON_2]", "PERSON", "Mary"] in first["entries"]
        assert any(cls == "FACILITY" for _, cls, _ in (e[:3] for e in first["entries"]))
        await say(client, patient_headers, session_id, "Mary says hi")
        second = seeded_db["florence_sessions"].find_one({"session_id": session_id})["token_map"]
        assert second["next_index"]["PERSON"] == 2  # Mary reused [PERSON_2]; no new person token
        assert mock_florence_ai.requests[-1].messages[-1]["content"] == "[PERSON_2] says hi"

    async def test_scrub_failure_is_a_refusal_not_a_500(self, client, patient_headers, mock_florence_ai, monkeypatch, caplog):
        session_id = (await start(client, patient_headers))["session_id"]

        def boom(self, *args, **kwargs):
            raise ScrubError("patterns", "ValueError")

        monkeypatch.setattr(ScrubContext, "scrub_messages", boom)
        secret = "my HKID is A123456(7) and I live at Flat 3B Tai Koo Shing"
        with caplog.at_level(logging.DEBUG):
            body = await say(client, patient_headers, session_id, secret)
        assert body["response"] == REFUSED_MESSAGES["en"]
        assert len(mock_florence_ai.requests) == 1  # only the opening turn reached the provider
        for fragment in ("A123456", "Tai Koo", "Test Patient", "testpatient", session_id):
            assert fragment not in caplog.text
        assert "ScrubError" in caplog.text
        # the session is still open
        status = (await client.get(f"/florence/session/{session_id}", headers=patient_headers)).json()
        assert status["status"] == "active"


class TestGetSession:

    async def test_get_session(self, client, patient_headers, mock_florence_ai):
        session_id = (await start(client, patient_headers))["session_id"]
        response = await client.get(f"/florence/session/{session_id}", headers=patient_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == session_id
        assert "conversation_history" in body

    async def test_get_nonexistent_session(self, client, patient_headers):
        response = await client.get("/florence/session/fake_session_123", headers=patient_headers)
        assert response.status_code == 404


class TestFinishSession:

    async def _start(self, client, headers, language="en"):
        return (await start(client, headers, language=language))["session_id"]

    async def test_finish_returns_immediately_then_analysis_lands_in_background(self, client, patient_headers, seeded_db, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        await client.post("/florence/send_message", json={"session_id": session_id, "message": "I feel very tired"},
                          headers=patient_headers)

        response = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["triage_status"] == "generating"
        assert body["alert_level"] is None          # not known yet
        assert body["ai_available"] is True

        # the conversation is already persisted, marked pending; no display name on the record, only the opaque ref
        saved = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert saved["triage_status"] == "generating" and saved["alert_level"] == "PENDING"
        assert saved["structured_assessment"] is None
        assert "user_info" not in saved and saved["patient_ref"] == patient_ref("testpatient")
        assert "token_map" not in saved
        assert seeded_db["florence_sessions"].find_one({"session_id": session_id})["status"] == "completed"

        # polling: with the instant FakeProvider the background task may already have landed
        pending = (await client.get(f"/florence/result/{session_id}", headers=patient_headers)).json()
        assert pending["triage_status"] in ("generating", "completed")
        if pending["triage_status"] == "generating":
            assert pending["alert_level"] is None

        await wait_for_background()

        done = (await client.get(f"/florence/result/{session_id}", headers=patient_headers)).json()
        assert done["triage_status"] == "completed"
        assert done["alert_level"] == "GREEN"
        assert done["structured_assessment"]["symptoms"]["fatigue"]["severity_rating"] == 3
        assert done["triage_assessment"]["alert_level"] == "GREEN"

        saved = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert saved["alert_level"] == "GREEN" and saved["triage_status"] == "completed"
        tasks = [r.task for r in mock_florence_ai.requests]
        assert tasks.count("symptom_assessment") == 1 and tasks.count("triage") == 1
        for r in mock_florence_ai.requests:
            assert r.scrubbed is True and "Test Patient" in r.known_identifiers
            assert set(r.scrub_report) == {"counts", "linkage_score", "ner_backend"}
        assert mock_florence_ai.requests[0].scrub_report["counts"] == {"PERSON": 1}  # the [PERSON_1] opening turn

    async def test_generator_templates_refer_to_the_placeholder_not_the_username(self, client, patient_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        await say(client, patient_headers, session_id, "tired")
        await finish(client, patient_headers, session_id)
        for task in ("symptom_assessment", "triage"):
            [request] = by_task(mock_florence_ai, task)
            template = request.messages[-1]["content"]
            assert "[PERSON_1]" in template and "testpatient" not in template and patient_ref("testpatient") not in template
            assert "{" not in template  # every format field was filled

    async def test_finishing_twice_returns_the_saved_result(self, client, patient_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        await client.post("/florence/send_message", json={"session_id": session_id, "message": "tired"}, headers=patient_headers)
        await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        await wait_for_background()
        again = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert again.status_code == 200 and again.json()["alert_level"] == "GREEN"

    async def test_result_is_owner_only(self, client, patient_headers, doctor_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        await client.post("/florence/send_message", json={"session_id": session_id, "message": "tired"}, headers=patient_headers)
        await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert (await client.get(f"/florence/result/{session_id}", headers=doctor_headers)).status_code == 403
        assert (await client.get("/florence/result/nope", headers=patient_headers)).status_code == 404

    async def test_finish_session_is_tracked_per_session_language(self, client, patient_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers, language="zh-HK")
        await client.post("/florence/send_message", json={"session_id": session_id, "message": "我好攰"}, headers=patient_headers)
        await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        await wait_for_background()
        languages = {r.task: r.language for r in mock_florence_ai.requests}
        assert languages["chat_turn"] == "zh-HK"
        assert languages["triage"] == "zh-HK"
        outbound = all_message_text(mock_florence_ai.requests)
        assert "我好攰" in outbound and "Test Patient" not in outbound and "testpatient" not in outbound

    async def test_cjk_patient_and_doctor_names_never_reach_the_provider(self, client, patient_headers, seeded_db, mock_florence_ai):
        seeded_db["users"].update_one({"username": "testpatient"}, {"$set": {"full_name": "陳大文"}})
        seeded_db["doctors"].update_one({"username": "testdoctor"}, {"$set": {"full_name": "李志明", "hospital": "瑪麗醫院"}})
        session_id = await self._start(client, patient_headers, language="zh-HK")
        await say(client, patient_headers, session_id, "叫我大文得喇。我好攰，李生話要覆診。")
        reply = await say(client, patient_headers, session_id, "陳生今日去瑪麗醫院覆診，阿文冇胃口。")
        assert reply["success"] is True
        await finish(client, patient_headers, session_id)
        outbound = all_message_text(mock_florence_ai.requests)
        for fragment in ("陳大文", "大文", "陳生", "阿文", "李志明", "李生", "瑪麗醫院"):
            assert fragment not in outbound
        assert "[PERSON_1]" in outbound and "[PERSON_2]" in outbound and "[FACILITY_1]" in outbound

    async def test_finish_session_wrong_user(self, client, patient_headers, doctor_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        response = await client.post(f"/florence/finish_session/{session_id}", headers=doctor_headers)
        assert response.status_code == 403


class TestReidentifiedOutputs:

    async def test_assessment_and_triage_outputs_are_reidentified_before_storage(self, client, patient_headers, seeded_db):
        from app.florence_utils import SymptomAssessmentOutput, TriageAssessmentOutput
        from tests.mock_openai import sample_assessment, sample_triage

        class TokenEchoingProvider(FakeProvider):
            """A model that quotes the placeholders it saw, as a real one would."""

            async def parse(self, request, model=None):
                self.requests.append(request)
                if request.schema is SymptomAssessmentOutput:
                    return sample_assessment().model_copy(update={
                        "conversation_notes": "[PERSON_1] says [PERSON_2] cooks; seen at [FACILITY_1].",
                    })
                if request.schema is TriageAssessmentOutput:
                    return sample_triage().model_copy(update={"key_symptoms": ["fatigue reported by [PERSON_1]"]})
                raise ValueError(request.schema)

        install(TokenEchoingProvider(name="openai"))
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "My daughter Mei Ling cooks for me; I go to Queen Mary Hospital.")
        result = await finish(client, patient_headers, session_id)

        notes = result["structured_assessment"]["conversation_notes"]
        assert notes == "Test Patient says Mei Ling cooks; seen at Queen Mary Hospital."
        assert result["triage_assessment"]["key_symptoms"] == ["fatigue reported by Test Patient"]
        saved = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert saved["structured_assessment"]["conversation_notes"] == notes
        assert saved["structured_assessment"]["patient_id"] == patient_ref("testpatient")

    async def test_unresolved_placeholder_is_counted_not_leaked(self, client, patient_headers, seeded_db, caplog):
        sink = MongoAuditSink(seeded_db)
        install(FakeProvider(name="openai", chat_reply="Hello [PATIENT_1], how are you?"), audit_sink=sink)
        with caplog.at_level(logging.WARNING, logger="ovis.florence"):
            body = await start(client, patient_headers)
        assert body["message"] == "Hello [PATIENT_1], how are you?"  # left alone, never guessed
        assert "unresolved placeholders in model output" in caplog.text and "count=1" in caplog.text
        assert "PATIENT_1" not in caplog.text
        [event] = list(seeded_db["audit_events"].find({}))
        assert event["unresolved_tokens"] == 1


class TestAuditTrail:

    async def test_audit_events_carry_refs_and_no_content(self, client, patient_headers, seeded_db):
        sink = MongoAuditSink(seeded_db)
        install(FakeProvider(name="openai"), audit_sink=sink)
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "My daughter Mei Ling brought soup and I feel tired")
        await finish(client, patient_headers, session_id)
        events = list(seeded_db["audit_events"].find({}))
        assert [e["task"] for e in events] == ["chat_turn", "chat_turn", "symptom_assessment", "triage"]
        for event in events:
            assert event["decision"] == "allow" and event["outcome"] == "ok"
            assert event["patient_ref"] == patient_ref("testpatient")
            assert event["session_ref"] == session_ref(session_id)
            assert event["task_source"] == "florence"
            assert event["scrub"]["counts"]["PERSON"] >= 1 and "ner_backend" in event["scrub"]
            dumped = repr(event)
            for fragment in ("Test Patient", "testpatient", "Mei Ling", "tired", session_id):
                assert fragment not in dumped


class TestRefusal:
    """COMPLIANCE_DPA_OK unset: the policy refuses every openai call. Chat stays usable with a scripted
    reply, nothing is fabricated, and the finished check-in waits for a clinician."""

    @pytest.fixture
    def refusing(self, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        provider = FakeProvider(name="openai")
        reset_gateway(fake_gateway(openai=provider))
        yield provider
        reset_gateway(InferenceGateway({}))

    async def test_start_send_finish_under_refusal(self, client, patient_headers, seeded_db, refusing, caplog):
        with caplog.at_level(logging.INFO):
            body = await start(client, patient_headers)
        assert body["ai_available"] is True
        assert body["message"] == REFUSED_MESSAGES["en"]
        session_id = body["session_id"]
        session = seeded_db["florence_sessions"].find_one({"session_id": session_id})
        assert session["refusal_reason"] == "dpa_not_confirmed" and session["ai_available"] is True

        turn = await say(client, patient_headers, session_id, "I feel tired and my name is Test Patient")
        assert turn["response"] == REFUSED_MESSAGES["en"]
        assert (await client.get(f"/florence/session/{session_id}", headers=patient_headers)).json()["status"] == "active"

        resp = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert resp.status_code == 200 and resp.json()["triage_status"] == "generating"
        await wait_for_background()

        result = (await client.get(f"/florence/result/{session_id}", headers=patient_headers)).json()
        assert result["triage_status"] == "pending_clinician_review"
        assert result["alert_level"] is None and result["structured_assessment"] is None and result["triage_assessment"] is None

        saved = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert saved["triage_status"] == "pending_clinician_review"
        assert saved["alert_level"] == "PENDING_REVIEW"
        assert saved["refusal_reason"] == "dpa_not_confirmed"
        assert saved["structured_assessment"] is None and saved["triage_assessment"] is None
        assert saved["conversation_history"][1]["content"] == "I feel tired and my name is Test Patient"  # clear at rest
        assert refusing.requests == []  # nothing ever reached the provider
        for fragment in ("Test Patient", "testpatient", session_id):
            assert fragment not in caplog.text

    async def test_refused_opening_uses_the_session_language(self, client, patient_headers, refusing):
        body = await start(client, patient_headers, language="zh-HK")
        assert body["message"] == REFUSED_MESSAGES["zh-HK"]
        assert body["message"] == generate_fallback_response("refused", "zh-HK")

    async def test_pending_record_is_visible_to_the_doctor(self, client, patient_headers, doctor_headers, refusing):
        session_id = (await start(client, patient_headers))["session_id"]
        await say(client, patient_headers, session_id, "I feel tired")
        await finish(client, patient_headers, session_id)
        listing = (await client.get("/doctor/patient/testpatient/assessments", headers=doctor_headers)).json()
        [item] = [a for a in listing["assessments"] if a["session_id"] == session_id]
        assert item["triage_status"] == "pending_clinician_review"
        assert item["effective_alert_level"] == "PENDING_REVIEW"
        patients = (await client.get("/doctor/patients/details", headers=doctor_headers)).json()["patients"]
        [me] = [p for p in patients if p["username"] == "testpatient"]
        assert me["latest_alert_level"] == "PENDING_REVIEW"
        alerts = (await client.get("/doctor/alerts", headers=doctor_headers)).json()
        assert any(a["session_id"] == session_id and a["alert_level"] == "PENDING_REVIEW" for a in alerts["alerts"])

    async def test_flag_flipped_mid_session_lets_the_next_turn_through(self, client, patient_headers, refusing, monkeypatch):
        session_id = (await start(client, patient_headers))["session_id"]
        monkeypatch.setenv("COMPLIANCE_DPA_OK", "true")
        turn = await say(client, patient_headers, session_id, "I feel tired")
        assert turn["response"] == "Hello! How are you feeling?"
        assert len(refusing.requests) == 1 and refusing.requests[0].task == "chat_turn"


class TestAbandonedSession:
    """Ending a chat before the patient says anything must not create an assessment or an alert."""

    async def test_no_patient_turns_means_no_record_and_no_triage(self, client, patient_headers, seeded_db, mock_florence_ai):
        session_id = (await client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers)).json()["session_id"]
        resp = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["abandoned"] is True and body["triage_status"] == "skipped" and body["alert_level"] is None
        await wait_for_background()
        assert seeded_db["florence_assessments"].find_one({"session_id": session_id}) is None
        assert seeded_db["florence_sessions"].find_one({"session_id": session_id})["status"] == "abandoned"
        assert not [r for r in mock_florence_ai.requests if r.task in ("symptom_assessment", "triage")]
        # polling an abandoned session is a clean "skipped", not a 404
        result = (await client.get(f"/florence/result/{session_id}", headers=patient_headers)).json()
        assert result["triage_status"] == "skipped" and result["abandoned"] is True
        # and the doctor sees no alert from it
        alerts = (await client.get("/doctor/alerts", headers=patient_headers)).status_code  # patient can't, sanity
        assert alerts == 401


class TestFallbackWithoutProvider:
    """No inference provider configured: chat still works with placeholder text and nothing 500s."""

    async def test_start_and_finish_in_fallback_mode(self, client, patient_headers, seeded_db):
        reset_gateway(InferenceGateway({}))
        resp = await client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ai_available"] is False
        session_id = body["session_id"]

        resp = await client.post("/florence/send_message", json={"session_id": session_id, "message": "hi"}, headers=patient_headers)
        assert resp.status_code == 200

        resp = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert resp.status_code == 200
        assert resp.json()["triage_status"] == "skipped" and resp.json()["alert_level"] is None
        saved = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert saved["structured_assessment"] is None
        assert saved["ai_powered"] is False and saved["alert_level"] == "UNKNOWN"

    async def test_provider_failure_falls_back_conservatively(self, client, patient_headers, seeded_db):
        provider = FakeProvider(name="openai")
        reset_gateway(fake_gateway(openai=provider))
        session_id = (await client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers)).json()["session_id"]
        provider.fail = True  # provider dies mid-session
        resp = await client.post("/florence/send_message", json={"session_id": session_id, "message": "hi"}, headers=patient_headers)
        assert resp.status_code == 200  # placeholder reply, no crash
        resp = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert resp.status_code == 200
        await wait_for_background()
        result = (await client.get(f"/florence/result/{session_id}", headers=patient_headers)).json()
        assert result["alert_level"] == "YELLOW"  # conservative triage fallback (a provider error is not a refusal)
        assert result["triage_status"] == "completed"
        reset_gateway(InferenceGateway({}))

    async def test_misconfigured_ner_backend_fails_closed(self, client, patient_headers, seeded_db, monkeypatch):
        monkeypatch.setenv("SCRUB_NER_BACKEND", "not-a-real-backend")
        provider = FakeProvider(name="openai")
        reset_gateway(fake_gateway(openai=provider))
        body = await start(client, patient_headers)
        assert body["message"] == REFUSED_MESSAGES["en"] and body["ai_available"] is True
        await say(client, patient_headers, provider_session := body["session_id"], "tired")
        result = await finish(client, patient_headers, provider_session)
        assert result["triage_status"] == "pending_clinician_review"
        assert seeded_db["florence_assessments"].find_one({"session_id": provider_session})["refusal_reason"] == "scrub_failed"
        assert provider.requests == []
        reset_gateway(InferenceGateway({}))


class TestSessionIds:

    async def test_rapid_double_start_yields_distinct_sessions(self, client, patient_headers, mock_florence_ai):
        import asyncio
        a, b = await asyncio.gather(
            client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers),
            client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers),
        )
        assert a.status_code == 200 and b.status_code == 200
        assert a.json()["session_id"] != b.json()["session_id"]
        assert a.json()["session_id"].startswith("testpatient_")


class TestLogsCarryRefsOnly:

    async def test_florence_logs_never_contain_username_or_session_id(self, client, patient_headers, mock_florence_ai, caplog):
        with caplog.at_level(logging.DEBUG):
            session_id = (await start(client, patient_headers))["session_id"]
            await say(client, patient_headers, session_id, "I feel tired, I'm Test Patient")
            await finish(client, patient_headers, session_id)
        records = [r for r in caplog.records if r.name.startswith("ovis.")]
        assert records, "expected application log lines"
        text = "\n".join(r.getMessage() for r in records)
        for fragment in ("testpatient", "Test Patient", session_id, "I feel tired"):
            assert fragment not in text
        assert session_ref(session_id) in text
        assert make_user()["username"] not in text


class TestLifespanWiring:
    """app.api's lifespan points the gateway's audit trail at Mongo, prepares the audit indexes and
    emits the rollout warning; /configure_db re-creates the audit indexes on demand."""

    async def test_lifespan_wires_audit_sink_indexes_and_policy_warning(self, mock_db, monkeypatch, caplog):
        import app.api as api_module

        gateway = fake_gateway(openai=FakeProvider(name="openai"))
        reset_gateway(gateway)
        monkeypatch.setattr(api_module, "get_db", lambda: mock_db)
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        indexed = []
        monkeypatch.setattr(api_module, "ensure_audit_indexes", lambda db: indexed.append(db) or True)
        with caplog.at_level(logging.INFO):
            async with api_module.lifespan(api_module.app):
                pass
        assert isinstance(gateway.audit_sink, MongoAuditSink) and gateway.audit_sink.db is mock_db
        assert indexed == [mock_db]
        assert "inference policy dpa_ok=false: openai-routed tasks will refuse" in caplog.text
        assert "inference ready" in caplog.text and "base_url" not in caplog.text
        reset_gateway(InferenceGateway({}))

    async def test_lifespan_survives_an_unreachable_database(self, monkeypatch, caplog):
        import app.api as api_module

        def broken():
            raise ConnectionError("no mongo")

        monkeypatch.setattr(api_module, "get_db", broken)
        with caplog.at_level(logging.WARNING):
            async with api_module.lifespan(api_module.app):
                pass
        assert "could not prepare database indexes or the audit sink: ConnectionError" in caplog.text
        assert "no mongo" not in caplog.text

    def test_configure_gateway_audit_sets_a_mongo_sink(self, mock_db):
        import app.api as api_module
        from app.inference import get_gateway

        api_module.configure_gateway_audit(mock_db)
        assert isinstance(get_gateway().audit_sink, MongoAuditSink)
        assert get_gateway().audit_sink.db is mock_db

    async def test_configure_db_creates_audit_indexes(self, client, doctor_headers, monkeypatch):
        import app.api as api_module

        indexed = []
        monkeypatch.setattr(api_module, "ensure_audit_indexes", lambda db: indexed.append(db) or True)
        resp = await client.get("/configure_db", headers=doctor_headers)
        assert resp.status_code == 200 and len(indexed) == 1
