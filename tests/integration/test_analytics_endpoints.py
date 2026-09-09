"""Analytics endpoints: Florence assessments and symptom questionnaires feed the same views."""

from datetime import datetime, timedelta, timezone

import pytest


def _florence_doc(user_id="testpatient", days_ago=0, fatigue=4, alert="YELLOW", assessment_type="florence_conversation_with_triage"):
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "session_id": f"{user_id}_{int(created.timestamp())}",
        "user_id": user_id,
        "created_at": created.isoformat(),
        "assessment_type": assessment_type,
        "structured_assessment": {
            "symptoms": {
                "fatigue": {"frequency_rating": fatigue, "severity_rating": fatigue, "key_indicators": []},
                "nausea": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
            }
        },
        "triage_assessment": {"alert_level": alert, "recommended_timeline": "Review this week"},
        "alert_level": alert,
        "oncologist_notification_level": "amber" if alert != "GREEN" else "none",
        "flag_for_oncologist": alert != "GREEN",
        "conversation_history": [{"role": "user", "content": "tired"}],
    }


def _questionnaire_doc(user_id="testpatient", days_ago=0, appetite_score=3, flags=None):
    submitted = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "user_id": user_id,
        "submitted_at": submitted,
        "timestamp": submitted.isoformat(),
        "date": submitted.strftime("%Y-%m-%d"),
        "completion": {"questions_answered": 20, "sections_completed": 13, "total_sections": 13},
        "sections": [
            {"section_id": "SN001", "title": "Appetite Loss", "clinical_area": "nutrition",
             "severity_score": appetite_score, "severity_label": "Poor",
             "responses": [{"question_id": "appetite_rating", "question_text": "Appetite?", "type": "rating",
                            "was_shown": True, "raw_value": 4, "display_value": "Poor"}]},
            {"section_id": "SN003", "title": "Cough", "clinical_area": "respiratory", "severity_score": 0,
             "severity_label": "None", "responses": []},
        ],
        "clinical_summary": {
            "symptom_count": 1, "max_severity": appetite_score, "max_severity_area": "Appetite Loss",
            "areas_of_concern": [{"section_id": "SN001", "area": "Appetite Loss", "clinical_area": "nutrition",
                                  "severity_score": appetite_score, "severity_label": "Poor", "details": []}],
            "alert_flags": flags or [],
        },
        "triage_status": "skipped",
        "answers": {"appetite_rating": 4},
    }


class TestUnifiedAssessments:

    async def test_lists_both_sources_newest_first(self, client, patient_headers, seeded_db):
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=2))
        seeded_db["symptom_questionnaires"].insert_one(_questionnaire_doc(days_ago=0, flags=["Appetite Loss: rated Poor (4/5)"]))
        # a questionnaire-derived triage record must not show up twice
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=0, assessment_type="questionnaire_triage"))

        body = (await client.get("/analytics/unified_assessments", headers=patient_headers)).json()
        assert body["total_assessments"] == 2
        assert [a["type"] for a in body["assessments"]] == ["daily_checkin", "florence_conversation"]
        checkin = body["assessments"][0]
        assert checkin["title"] == "Daily Symptom Check-in"
        assert "Appetite Loss" in checkin["summary"]
        assert checkin["oncologist_notification_level"] == "amber"  # flagged answers count as amber
        # flagged chats lead with the triage outcome rather than the raw symptom list
        assert body["assessments"][1]["summary"].startswith("Triage YELLOW")
        assert "Review this week" in body["assessments"][1]["summary"]

    async def test_other_users_data_is_invisible(self, client, patient_headers, seeded_db):
        seeded_db["florence_assessments"].insert_one(_florence_doc(user_id="someone_else"))
        body = (await client.get("/analytics/unified_assessments", headers=patient_headers)).json()
        assert body["total_assessments"] == 0


class TestAssessmentDetail:

    async def test_questionnaire_detail(self, client, patient_headers, seeded_db):
        inserted = seeded_db["symptom_questionnaires"].insert_one(_questionnaire_doc())
        # mock ids are ints; the endpoint needs a real ObjectId, so patch the stored id
        from bson import ObjectId
        oid = ObjectId()
        seeded_db["symptom_questionnaires"]._docs[0]["_id"] = oid

        body = (await client.get(f"/analytics/assessment/{oid}", headers=patient_headers)).json()
        assert body["success"] is True
        detail = body["assessment"]
        assert detail["type"] == "daily_checkin"
        assert detail["clinical_summary"]["max_severity_area"] == "Appetite Loss"
        # only sections with shown responses come back
        assert [s["title"] for s in detail["sections"]] == ["Appetite Loss"]

    async def test_bad_id_is_400_and_missing_is_404(self, client, patient_headers):
        assert (await client.get("/analytics/assessment/not-an-id", headers=patient_headers)).status_code == 400
        from bson import ObjectId
        assert (await client.get(f"/analytics/assessment/{ObjectId()}", headers=patient_headers)).status_code == 404


class TestWeeklyAndMonthly:

    async def test_weekly_merges_questionnaires_and_florence(self, client, patient_headers, seeded_db):
        # both today, so both land inside the current week regardless of weekday
        seeded_db["florence_assessments"].insert_one(_florence_doc(days_ago=0, fatigue=4))
        seeded_db["symptom_questionnaires"].insert_one(_questionnaire_doc(days_ago=0, appetite_score=3))

        body = (await client.get("/analytics/weekly?week_offset=0", headers=patient_headers)).json()
        data = body["data"]
        assert data["totalAssessments"] == 2
        assert data["sources"] == {"florence": 1, "questionnaire": 1}
        assert data["avgSeverityBySymptom"]["fatigue"] == 4
        assert data["avgSeverityBySymptom"]["appetite_loss"] == 3
        today = [d for d in data["dailyData"] if d["hasData"]]
        assert len(today) == 1 and today[0]["assessmentCount"] == 2
        assert data["totalAlerts"] == 1  # the YELLOW florence one

    async def test_weekly_with_no_data(self, client, patient_headers):
        body = (await client.get("/analytics/weekly?week_offset=3", headers=patient_headers)).json()
        assert body["data"]["totalAssessments"] == 0
        assert body["data"]["insights"][0]["id"] == "no_data"

    async def test_weekly_tolerates_records_without_structured_data(self, client, patient_headers, seeded_db):
        doc = _florence_doc(days_ago=0)
        doc["structured_assessment"] = None  # offline Florence session
        seeded_db["florence_assessments"].insert_one(doc)
        legacy = _florence_doc(days_ago=0)
        legacy["structured_assessment"] = {"symptoms": ["fatigue", "nausea"]}  # legacy list format
        seeded_db["florence_assessments"].insert_one(legacy)
        response = await client.get("/analytics/weekly", headers=patient_headers)
        assert response.status_code == 200
        assert response.json()["data"]["totalAssessments"] == 2

    async def test_monthly_includes_questionnaire_days(self, client, patient_headers, seeded_db):
        seeded_db["symptom_questionnaires"].insert_one(_questionnaire_doc(days_ago=0, appetite_score=2))
        body = (await client.get("/analytics/monthly?month_offset=0", headers=patient_headers)).json()
        data = body["data"]
        assert data["totalAssessments"] == 1
        assert data["sources"]["questionnaire"] == 1
        assert "appetite_loss" in data["availableSymptoms"]
        day = datetime.now(timezone.utc).day
        assert data["symptomsByDay"]["appetite_loss"][str(day)] == 2 or data["symptomsByDay"]["appetite_loss"][day] == 2
