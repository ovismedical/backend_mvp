"""Doctor-facing endpoints and auth capability flags."""

from datetime import datetime, timedelta, timezone

import pytest

from tests.factories import make_pending_assessment_record, make_review
from tests.integration.test_analytics_endpoints import _florence_doc, _questionnaire_doc


def _pending_doc(days_ago=0, user_id="testpatient"):
    """A chat whose AI assessment was refused: no triage, waiting for a clinician."""
    created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return make_pending_assessment_record({
        "user_id": user_id,
        "session_id": f"{user_id}_pending_{days_ago}",
        "created_at": created,
        "completed_at": created,
    })


def _untriaged_doc(status, days_ago=0):
    """A finished chat still generating, skipped (no AI) or failed - never a clinician's concern."""
    doc = _florence_doc(days_ago=days_ago)
    doc.update({
        "session_id": f"testpatient_{status}_{days_ago}",
        "triage_status": status,
        "triage_assessment": None,
        "structured_assessment": None,
        "alert_level": "UNKNOWN" if status == "skipped" else "PENDING",
        "oncologist_notification_level": "none",
        "flag_for_oncologist": False,
    })
    return doc


def _review_url(session_id):
    return f"/doctor/assessments/{session_id}/review"


class TestDoctorPatients:

    async def test_patient_details_include_latest_alert(self, client, doctor_headers, seeded_db):
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=3, alert="GREEN"))
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=0, alert="RED"))
        body = (await client.get("/doctor/patients/details", headers=doctor_headers)).json()
        assert [p["username"] for p in body["patients"]] == ["testpatient"]
        patient = body["patients"][0]
        assert patient["latest_alert_level"] == "RED"
        assert "password" not in patient

    async def test_patient_endpoints_require_doctor(self, client, patient_headers):
        assert (await client.get("/doctor/patients/details", headers=patient_headers)).status_code == 401
        assert (await client.get("/doctor/alerts", headers=patient_headers)).status_code == 401

    async def test_doctor_cannot_read_unassigned_patient(self, client, doctor_headers):
        assert (await client.get("/doctor/patient/stranger/assessments", headers=doctor_headers)).status_code == 403
        assert (await client.get("/doctor/answers?user_id=stranger", headers=doctor_headers)).status_code == 403

    async def test_alerts_and_patient_records(self, client, doctor_headers, seeded_db):
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=0, alert="ORANGE"))
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=1, alert="GREEN"))
        seeded_db["symptom_questionnaires"].insert_one(_questionnaire_doc(days_ago=0))

        alerts = (await client.get("/doctor/alerts", headers=doctor_headers)).json()
        assert alerts["count"] == 1 and alerts["alerts"][0]["alert_level"] == "ORANGE"

        assessments = (await client.get("/doctor/patient/testpatient/assessments", headers=doctor_headers)).json()
        assert assessments["count"] == 2
        assert all("conversation_history" not in a for a in assessments["assessments"])

        questionnaires = (await client.get("/doctor/patient/testpatient/questionnaires", headers=doctor_headers)).json()
        assert questionnaires["count"] == 1
        assert questionnaires["questionnaires"][0]["clinical_summary"]["max_severity_area"] == "Appetite Loss"

    async def test_patient_details_newest_pending_record_needs_review(self, client, doctor_headers, seeded_db):
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=3, alert="GREEN"))
        seeded_db["florence_assessments"].insert_one(_pending_doc(days_ago=0))
        patient = (await client.get("/doctor/patients/details", headers=doctor_headers)).json()["patients"][0]
        assert patient["latest_alert_level"] == "PENDING_REVIEW"
        assert patient["latest_review"] is None

    async def test_patient_details_ignores_generating_skipped_and_failed_records(self, client, doctor_headers, seeded_db):
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=3, alert="GREEN"))
        for status in ("generating", "skipped", "failed"):
            seeded_db["florence_assessments"].insert_one(_untriaged_doc(status, days_ago=0))
        patient = (await client.get("/doctor/patients/details", headers=doctor_headers)).json()["patients"][0]
        assert patient["latest_alert_level"] == "GREEN"

    async def test_patient_details_uses_the_clinician_override(self, client, doctor_headers, seeded_db):
        doc = _florence_doc(days_ago=0, alert="RED")
        seeded_db["florence_assessments"].insert_one(doc)
        seeded_db["assessment_reviews"].insert_one(make_review({
            "session_id": doc["session_id"], "agrees": False, "alert_level_override": "YELLOW", "florence_alert_level": "RED",
        }))
        patient = (await client.get("/doctor/patients/details", headers=doctor_headers)).json()["patients"][0]
        assert patient["latest_alert_level"] == "YELLOW"
        assert patient["latest_review"] == {"agrees": False, "alert_level_override": "YELLOW"}

    async def test_patient_details_prefers_the_requesting_doctors_review(self, client, doctor_headers, seeded_db):
        doc = _florence_doc(days_ago=0, alert="RED")
        seeded_db["florence_assessments"].insert_one(doc)
        seeded_db["assessment_reviews"].insert_one(make_review({
            "session_id": doc["session_id"], "doctor": "otherdoc", "agrees": False, "alert_level_override": "GREEN",
            "florence_alert_level": "RED", "reviewed_at": "2999-01-01T00:00:00+00:00",
        }))
        seeded_db["assessment_reviews"].insert_one(make_review({
            "session_id": doc["session_id"], "agrees": True, "florence_alert_level": "RED",
        }))
        patient = (await client.get("/doctor/patients/details", headers=doctor_headers)).json()["patients"][0]
        assert patient["latest_alert_level"] == "RED"
        assert patient["latest_review"] == {"agrees": True, "alert_level_override": None}

    async def test_alerts_include_pending_records(self, client, doctor_headers, seeded_db):
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=1, alert="GREEN"))
        seeded_db["florence_assessments"].insert_one(_pending_doc(days_ago=0))
        alerts = (await client.get("/doctor/alerts", headers=doctor_headers)).json()
        assert alerts["count"] == 1
        alert = alerts["alerts"][0]
        assert alert["alert_level"] == "PENDING_REVIEW"
        assert alert["effective_alert_level"] == "PENDING_REVIEW"
        assert alert["triage_status"] == "pending_clinician_review"
        assert alert["review"] is None
        assert alert["key_symptoms"] == []

    async def test_assessments_list_includes_pending_records_and_reviews(self, client, doctor_headers, seeded_db):
        triaged = _florence_doc(days_ago=1, alert="ORANGE")
        seeded_db["florence_assessments"].insert_one(triaged)
        seeded_db["florence_assessments"].insert_one(_pending_doc(days_ago=0))
        seeded_db["florence_assessments"].insert_one(_untriaged_doc("generating", days_ago=0))
        seeded_db["assessment_reviews"].insert_one(make_review({
            "session_id": triaged["session_id"], "agrees": False, "alert_level_override": "YELLOW",
            "florence_alert_level": "ORANGE", "note": "Looks like a routine flare",
        }))

        body = (await client.get("/doctor/patient/testpatient/assessments", headers=doctor_headers)).json()
        assert body["count"] == 2
        pending, reviewed = body["assessments"]
        assert pending["triage_status"] == "pending_clinician_review"
        assert pending["effective_alert_level"] == "PENDING_REVIEW" and pending["review"] is None
        assert reviewed["alert_level"] == "ORANGE"
        assert reviewed["effective_alert_level"] == "YELLOW"
        assert reviewed["review"]["alert_level_override"] == "YELLOW"
        assert reviewed["review"]["note"] == "Looks like a routine flare"
        assert "_id" not in reviewed["review"]

        detail = (await client.get(f"/doctor/patient/testpatient/assessment/{triaged['session_id']}", headers=doctor_headers)).json()
        assert detail["effective_alert_level"] == "YELLOW" and detail["review"]["agrees"] is False
        assert "conversation_history" in detail


class TestAssessmentReviews:

    @pytest.fixture
    def triaged(self, seeded_db):
        doc = _florence_doc(days_ago=0, alert="ORANGE")
        seeded_db["florence_assessments"].insert_one(doc)
        return doc

    @pytest.fixture
    def pending(self, seeded_db):
        doc = _pending_doc(days_ago=0)
        seeded_db["florence_assessments"].insert_one(doc)
        return doc

    async def test_patients_cannot_review(self, client, patient_headers, triaged):
        assert (await client.post(_review_url(triaged["session_id"]), json={"agrees": True}, headers=patient_headers)).status_code == 401
        assert (await client.get(_review_url(triaged["session_id"]), headers=patient_headers)).status_code == 401

    async def test_unknown_session_is_404(self, client, doctor_headers):
        assert (await client.post(_review_url("nope_1"), json={"agrees": True}, headers=doctor_headers)).status_code == 404
        assert (await client.get(_review_url("nope_1"), headers=doctor_headers)).status_code == 404

    async def test_other_doctors_patient_is_403(self, client, doctor_headers, seeded_db):
        doc = _florence_doc(user_id="stranger", days_ago=0)
        seeded_db["florence_assessments"].insert_one(doc)
        assert (await client.post(_review_url(doc["session_id"]), json={"agrees": True}, headers=doctor_headers)).status_code == 403
        assert seeded_db["assessment_reviews"].count_documents({}) == 0

    async def test_triaged_record_validation(self, client, doctor_headers, triaged):
        url = _review_url(triaged["session_id"])
        assert (await client.post(url, json={}, headers=doctor_headers)).status_code == 422          # agrees required
        assert (await client.post(url, json={"agrees": False}, headers=doctor_headers)).status_code == 422  # override required
        assert (await client.post(url, json={"agrees": True, "alert_level_override": "GREEN"}, headers=doctor_headers)).status_code == 422
        assert (await client.post(url, json={"agrees": False, "alert_level_override": "PURPLE"}, headers=doctor_headers)).status_code == 422
        assert (await client.post(url, json={"agrees": True, "note": "x" * 501}, headers=doctor_headers)).status_code == 422

    async def test_pending_record_requires_an_assigned_level(self, client, doctor_headers, pending):
        url = _review_url(pending["session_id"])
        assert (await client.post(url, json={"agrees": True}, headers=doctor_headers)).status_code == 422
        assert (await client.post(url, json={}, headers=doctor_headers)).status_code == 422

    async def test_agree_records_the_florence_level(self, client, doctor_headers, seeded_db, triaged):
        resp = await client.post(_review_url(triaged["session_id"]), json={"agrees": True, "note": "Concur"}, headers=doctor_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["effective_alert_level"] == "ORANGE"
        review = body["review"]
        assert review["agrees"] is True and review["alert_level_override"] is None
        assert review["florence_alert_level"] == "ORANGE" and review["note"] == "Concur"
        assert review["doctor"] == "testdoctor" and review["user_id"] == "testpatient"
        assert review["session_id"] == triaged["session_id"] and review["reviewed_at"]
        stored = seeded_db["assessment_reviews"].find_one({"session_id": triaged["session_id"]})
        assert stored["doctor"] == "testdoctor" and stored["agrees"] is True

    async def test_agreeing_with_a_matching_override_is_normalised(self, client, doctor_headers, triaged):
        resp = await client.post(_review_url(triaged["session_id"]), json={"agrees": True, "alert_level_override": "ORANGE"}, headers=doctor_headers)
        assert resp.status_code == 200
        assert resp.json()["review"]["alert_level_override"] is None

    async def test_override_changes_the_effective_level_everywhere(self, client, doctor_headers, triaged):
        resp = await client.post(_review_url(triaged["session_id"]), json={"agrees": False, "alert_level_override": "YELLOW"}, headers=doctor_headers)
        assert resp.status_code == 200
        assert resp.json()["effective_alert_level"] == "YELLOW"

        listed = (await client.get("/doctor/patient/testpatient/assessments", headers=doctor_headers)).json()["assessments"][0]
        assert listed["effective_alert_level"] == "YELLOW" and listed["alert_level"] == "ORANGE"
        patient = (await client.get("/doctor/patients/details", headers=doctor_headers)).json()["patients"][0]
        assert patient["latest_alert_level"] == "YELLOW"
        assert patient["latest_review"] == {"agrees": False, "alert_level_override": "YELLOW"}
        alert = (await client.get("/doctor/alerts", headers=doctor_headers)).json()["alerts"][0]
        assert alert["alert_level"] == "ORANGE" and alert["effective_alert_level"] == "YELLOW"
        assert alert["review"]["alert_level_override"] == "YELLOW"

    async def test_review_is_upserted_per_doctor_and_session(self, client, doctor_headers, seeded_db, triaged):
        url = _review_url(triaged["session_id"])
        assert (await client.post(url, json={"agrees": True}, headers=doctor_headers)).status_code == 200
        assert (await client.post(url, json={"agrees": True}, headers=doctor_headers)).status_code == 200
        assert seeded_db["assessment_reviews"].count_documents({}) == 1
        resp = await client.post(url, json={"agrees": False, "alert_level_override": "RED", "note": "Worse than it reads"}, headers=doctor_headers)
        assert resp.status_code == 200
        assert seeded_db["assessment_reviews"].count_documents({}) == 1
        stored = seeded_db["assessment_reviews"].find_one({"session_id": triaged["session_id"], "doctor": "testdoctor"})
        assert stored["agrees"] is False and stored["alert_level_override"] == "RED" and stored["note"] == "Worse than it reads"

    async def test_assigning_a_level_to_a_pending_record(self, client, doctor_headers, seeded_db, pending):
        resp = await client.post(_review_url(pending["session_id"]), json={"alert_level_override": "GREEN"}, headers=doctor_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["effective_alert_level"] == "GREEN"
        assert body["review"]["agrees"] is None and body["review"]["florence_alert_level"] is None
        assert body["review"]["alert_level_override"] == "GREEN"

        patient = (await client.get("/doctor/patients/details", headers=doctor_headers)).json()["patients"][0]
        assert patient["latest_alert_level"] == "GREEN"
        assert patient["latest_review"] == {"agrees": None, "alert_level_override": "GREEN"}
        listed = (await client.get("/doctor/patient/testpatient/assessments", headers=doctor_headers)).json()["assessments"][0]
        assert listed["effective_alert_level"] == "GREEN" and listed["triage_status"] == "pending_clinician_review"
        # an explicit agrees flag is tolerated but stored as null: there was no Florence level to agree with
        resp = await client.post(_review_url(pending["session_id"]), json={"agrees": True, "alert_level_override": "YELLOW"}, headers=doctor_headers)
        assert resp.status_code == 200 and resp.json()["review"]["agrees"] is None
        assert seeded_db["assessment_reviews"].count_documents({}) == 1

    async def test_skipped_record_also_needs_an_assigned_level(self, client, doctor_headers, seeded_db):
        skipped = _untriaged_doc("skipped", days_ago=0)  # AI unavailable: alert_level UNKNOWN, no triage
        seeded_db["florence_assessments"].insert_one(skipped)
        url = _review_url(skipped["session_id"])
        assert (await client.post(url, json={"agrees": True}, headers=doctor_headers)).status_code == 422
        resp = await client.post(url, json={"alert_level_override": "YELLOW"}, headers=doctor_headers)
        assert resp.status_code == 200
        assert resp.json()["review"]["agrees"] is None and resp.json()["review"]["florence_alert_level"] is None
        assert resp.json()["effective_alert_level"] == "YELLOW"

    async def test_get_review(self, client, doctor_headers, triaged):
        url = _review_url(triaged["session_id"])
        body = (await client.get(url, headers=doctor_headers)).json()
        assert body == {"review": None, "effective_alert_level": "ORANGE"}
        await client.post(url, json={"agrees": False, "alert_level_override": "RED"}, headers=doctor_headers)
        body = (await client.get(url, headers=doctor_headers)).json()
        assert body["effective_alert_level"] == "RED" and body["review"]["doctor"] == "testdoctor"

    async def test_review_does_not_leak_identifiers_into_logs(self, client, doctor_headers, triaged, caplog):
        import logging
        with caplog.at_level(logging.DEBUG, logger="ovis"):
            resp = await client.post(_review_url(triaged["session_id"]), json={"agrees": True}, headers=doctor_headers)
        assert resp.status_code == 200
        for needle in ("testpatient", "testdoctor", "Test Patient", triaged["session_id"]):
            assert needle not in caplog.text


class TestReviewHousekeeping:

    def test_ensure_review_indexes_is_best_effort(self, caplog):
        import logging
        from app.doctor import ensure_review_indexes

        class Broken:
            def __getitem__(self, name):
                return self

            def create_index(self, *args, **kwargs):
                raise RuntimeError("read-only user secret-db-name")

        with caplog.at_level(logging.WARNING, logger="ovis.doctor"):
            ensure_review_indexes(Broken())  # must not raise
        assert "RuntimeError" in caplog.text and "secret-db-name" not in caplog.text

    def test_ensure_review_indexes_creates_a_unique_compound_index(self):
        from app.doctor import ensure_review_indexes

        calls = []

        class Recorder:
            def __getitem__(self, name):
                calls.append(("collection", name))
                return self

            def create_index(self, keys, **kwargs):
                calls.append(("index", keys, kwargs))

        ensure_review_indexes(Recorder())
        assert ("collection", "assessment_reviews") in calls
        assert ("index", [("session_id", 1), ("doctor", 1)], {"unique": True}) in calls

    async def test_configure_db_creates_review_indexes(self, client, doctor_headers, seeded_db, monkeypatch):
        import app.api as api_mod
        seen = []
        monkeypatch.setattr(api_mod, "ensure_review_indexes", lambda db: seen.append(db))
        assert (await client.get("/configure_db", headers=doctor_headers)).status_code == 200
        assert seen == [seeded_db]

    async def test_deleting_a_user_removes_reviews_and_sessions(self, client, doctor_headers, seeded_db):
        seeded_db["assessment_reviews"].insert_one(make_review())
        seeded_db["florence_sessions"].insert_one({"session_id": "testpatient_1", "user_id": "testpatient", "status": "active"})
        body = (await client.delete("/admin/users/testpatient", headers=doctor_headers)).json()
        assert body["cleaned"]["assessment_reviews"] == 1
        assert body["cleaned"]["florence_sessions"] == 1
        assert seeded_db["assessment_reviews"].count_documents({}) == 0


class TestAuthConfig:

    async def test_reports_otp_off_without_twilio(self, client, monkeypatch):
        for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SERVICE_SID"):
            monkeypatch.delenv(var, raising=False)
        body = (await client.get("/auth/config")).json()
        assert body["registration"]["otp_required"] is False
        assert body["registration"]["direct_endpoint"] == "/register"
        assert body["florence_available"] is False  # no provider in tests

    async def test_reports_otp_on_with_twilio(self, client, monkeypatch):
        for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SERVICE_SID"):
            monkeypatch.setenv(var, "x")
        body = (await client.get("/auth/config")).json()
        assert body["registration"]["otp_required"] is True


class TestRegister:

    async def test_register_links_patient_to_doctor(self, client, seeded_db):
        resp = await client.post("/register", json={
            "username": "newbie", "password": "pass1234", "email": "n@example.com",
            "access_code": "ABCD", "full_name": "New Bee", "dob": "01/02/1990", "sex": "female",
        })
        assert resp.status_code == 201, resp.text
        doctor = seeded_db["doctors"].find_one({"username": "testdoctor"})
        assert "newbie" in doctor["patients"]
        user = seeded_db["users"].find_one({"username": "newbie"})
        assert user["doctor"] == "testdoctor" and user["isDoctor"] is False
        assert user["password"] != "pass1234"

    async def test_register_rejects_bad_code_and_duplicates(self, client):
        bad = await client.post("/register", json={"username": "someone", "password": "pass1234", "email": "s@example.com",
                                                   "access_code": "ZZZZ", "full_name": "S"})
        assert bad.status_code == 400
        dup = await client.post("/register", json={"username": "testpatient", "password": "pass1234", "email": "s@example.com",
                                                   "access_code": "ABCD", "full_name": "S"})
        assert dup.status_code == 400
