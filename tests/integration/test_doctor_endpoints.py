"""Doctor-facing endpoints and auth capability flags."""

from datetime import datetime, timezone

import pytest

from tests.integration.test_analytics_endpoints import _florence_doc, _questionnaire_doc


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
