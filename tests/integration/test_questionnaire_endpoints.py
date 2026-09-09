"""
Integration tests for the symptom questionnaire -> AI triage bridge (/symptom-questionnaire/submit).

The bridge runs as a tracked background task against the request's (mock) database and the
FakeProvider gateway under the real routing policy, so these tests prove that free-text answers
are de-identified before they leave and that a refusal is saved for a clinician, never fabricated.
"""

import logging

import pytest

from app.inference import InferenceGateway, reset_gateway
from app.inference.audit import MongoAuditSink
from app.inference.refs import patient_ref, session_ref
from app.inference.scrub import ScrubContext, ScrubError
from app.florence import wait_for_background
from app.questionnaire_triage_bridge import generate_questionnaire_triage
from tests.factories import make_questionnaire_answers
from tests.mock_openai import FakeProvider, all_message_text, fake_gateway

FREE_TEXT = "My daughter Mei Ling cooks for me; I'm Test Patient (testpatient), born 01/01/1990, seen at Queen Mary Hospital."


@pytest.fixture
def provider():
    fake = FakeProvider(name="openai")
    reset_gateway(fake_gateway(openai=fake))
    yield fake
    reset_gateway(InferenceGateway({}))


def payload(**answer_overrides):
    answers = make_questionnaire_answers({
        "appetite_rating": 4,
        "appetite_causes": ["others_specify"],
        "appetite_causes_other_text": FREE_TEXT,
        **answer_overrides,
    })
    return {"answers": answers, "sections_completed": 13, "total_sections": 13, "completion_percentage": 100,
            "submission_mode": "full"}


async def submit(client, headers, body=None):
    resp = await client.post("/symptom-questionnaire/submit", json=body or payload(), headers=headers)
    assert resp.status_code == 200, resp.text
    await wait_for_background()
    return resp.json()


class TestQuestionnaireTriage:

    async def test_submit_generates_a_scrubbed_triage(self, client, patient_headers, seeded_db, provider):
        body = await submit(client, patient_headers)
        assert body["triage_status"] == "generating"
        qid = body["questionnaire_id"]

        # the bridge ran on this request's db, not app.login.get_client()
        record = seeded_db["florence_assessments"].find_one({"session_id": f"questionnaire_{qid}"})
        assert record is not None
        assert record["assessment_type"] == "questionnaire_triage"
        assert record["source_questionnaire_id"] == qid
        assert record["triage_status"] == "completed" and record["alert_level"] == "GREEN"
        assert record["patient_ref"] == patient_ref("testpatient") and "user_info" not in record
        assert record["structured_assessment"]["symptoms"]["fatigue"]["severity_rating"] == 3
        # the stored transcript is the clear-text synthesised conversation
        assert FREE_TEXT in "\n".join(m["content"] for m in record["conversation_history"])

        questionnaire = seeded_db["symptom_questionnaires"].find_one({"_id": 1})
        assert questionnaire["triage_status"] == "completed" and questionnaire["alert_level"] == "GREEN"

        assert sorted(r.task for r in provider.requests) == ["symptom_assessment", "triage"]
        outbound = all_message_text(provider.requests)
        for identifier in ("Test Patient", "testpatient", "Mei Ling", "01/01/1990", "1990", "Queen Mary Hospital", patient_ref("testpatient")):
            assert identifier not in outbound
        sent = "\n".join(m["content"] for m in provider.requests[0].messages)
        assert "[PERSON_1]" in sent and "[PERSON_2]" in sent and "[DOB]" in sent and "[FACILITY_1]" in sent
        for request in provider.requests:
            assert request.scrubbed is True
            assert request.metadata == {"task_source": "questionnaire", "patient_ref": patient_ref("testpatient"),
                                        "session_ref": session_ref(f"questionnaire_{qid}")}
            assert "Test Patient" in request.known_identifiers and "Mei Ling" in request.known_identifiers
            assert request.scrub_report["counts"]["PERSON"] >= 2

    async def test_refusal_saves_the_questionnaire_for_clinician_review(self, client, patient_headers, seeded_db, provider, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        body = await submit(client, patient_headers)
        qid = body["questionnaire_id"]

        record = seeded_db["florence_assessments"].find_one({"session_id": f"questionnaire_{qid}"})
        assert record["triage_status"] == "pending_clinician_review"
        assert record["alert_level"] == "PENDING_REVIEW"
        assert record["refusal_reason"] == "dpa_not_confirmed"
        assert record["structured_assessment"] is None and record["triage_assessment"] is None
        assert record["assessment_type"] == "questionnaire_triage" and record["source_questionnaire_id"] == qid
        questionnaire = seeded_db["symptom_questionnaires"].find_one({"_id": 1})
        assert questionnaire["triage_status"] == "pending_clinician_review"
        assert questionnaire["alert_level"] == "PENDING_REVIEW"
        assert provider.requests == []

    async def test_pending_questionnaire_triage_is_visible_to_the_doctor(self, client, patient_headers, doctor_headers, provider, monkeypatch):
        monkeypatch.delenv("COMPLIANCE_DPA_OK", raising=False)
        qid = (await submit(client, patient_headers))["questionnaire_id"]
        listing = (await client.get("/doctor/patient/testpatient/assessments", headers=doctor_headers)).json()
        [item] = [a for a in listing["assessments"] if a["session_id"] == f"questionnaire_{qid}"]
        assert item["effective_alert_level"] == "PENDING_REVIEW"

    async def test_scrub_failure_is_saved_for_review_without_leaking(self, client, patient_headers, seeded_db, provider, monkeypatch, caplog):
        def boom(self, *args, **kwargs):
            raise ScrubError("gazetteer", "KeyError")

        monkeypatch.setattr(ScrubContext, "scrub_messages", boom)
        with caplog.at_level(logging.DEBUG):
            qid = (await submit(client, patient_headers))["questionnaire_id"]
        record = seeded_db["florence_assessments"].find_one({"session_id": f"questionnaire_{qid}"})
        assert record["triage_status"] == "pending_clinician_review" and record["refusal_reason"] == "scrub_failed"
        assert provider.requests == []
        for fragment in ("Test Patient", "testpatient", "Mei Ling", "01/01/1990", f"questionnaire_{qid}"):
            assert fragment not in caplog.text
        assert session_ref(f"questionnaire_{qid}") in caplog.text

    async def test_no_provider_means_skipped(self, client, patient_headers, seeded_db):
        reset_gateway(InferenceGateway({}))
        qid = (await submit(client, patient_headers))["questionnaire_id"]
        assert seeded_db["symptom_questionnaires"].find_one({"_id": 1})["triage_status"] == "skipped"
        assert seeded_db["florence_assessments"].find_one({"session_id": f"questionnaire_{qid}"}) is None

    async def test_too_few_answers_means_skipped(self, client, patient_headers, seeded_db, provider):
        body = {"answers": {}, "sections_completed": 0, "total_sections": 13, "completion_percentage": 0, "submission_mode": "full"}
        await submit(client, patient_headers, body)
        assert seeded_db["symptom_questionnaires"].find_one({"_id": 1})["triage_status"] == "skipped"
        assert provider.requests == []

    async def test_duplicate_triage_is_not_generated(self, client, patient_headers, seeded_db, provider):
        qid = (await submit(client, patient_headers))["questionnaire_id"]
        questionnaire = seeded_db["symptom_questionnaires"].find_one({"_id": 1})
        before = len(provider.requests)
        user = seeded_db["users"].find_one({"username": "testpatient"})
        await generate_questionnaire_triage(questionnaire, 1, user, db=seeded_db)
        assert len(provider.requests) == before
        assert seeded_db["florence_assessments"].count_documents({"session_id": f"questionnaire_{qid}"}) == 1

    async def test_provider_error_falls_back_conservatively(self, client, patient_headers, seeded_db, provider):
        provider.fail = True
        qid = (await submit(client, patient_headers))["questionnaire_id"]
        record = seeded_db["florence_assessments"].find_one({"session_id": f"questionnaire_{qid}"})
        assert record["alert_level"] == "YELLOW"  # the generators' conservative fallback: an error is not a refusal
        assert record["triage_status"] == "completed" and record["triage_assessment"]["confidence_level"] == "low"
        assert seeded_db["symptom_questionnaires"].find_one({"_id": 1})["triage_status"] == "completed"

    async def test_audit_events_carry_the_questionnaire_source(self, client, patient_headers, seeded_db):
        fake = FakeProvider(name="openai")
        reset_gateway(fake_gateway(openai=fake, audit_sink=MongoAuditSink(seeded_db)))
        qid = (await submit(client, patient_headers))["questionnaire_id"]
        events = list(seeded_db["audit_events"].find({}))
        assert sorted(e["task"] for e in events) == ["symptom_assessment", "triage"]
        for event in events:
            assert event["task_source"] == "questionnaire" and event["decision"] == "allow"
            assert event["session_ref"] == session_ref(f"questionnaire_{qid}")
            assert "Mei Ling" not in repr(event) and "Test Patient" not in repr(event)
        reset_gateway(InferenceGateway({}))

    async def test_bridge_logs_carry_refs_only(self, client, patient_headers, provider, caplog):
        with caplog.at_level(logging.DEBUG):
            qid = (await submit(client, patient_headers))["questionnaire_id"]
        text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("ovis."))
        assert text
        for fragment in ("testpatient", "Test Patient", "Mei Ling", f"questionnaire_{qid}"):
            assert fragment not in text
        assert session_ref(f"questionnaire_{qid}") in text
