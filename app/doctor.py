from .login import get_db, get_user
from fastapi import Depends, HTTPException, APIRouter

doctorrouter = APIRouter(prefix="/doctor", tags=["doctor"])


def _require_doctor(user):
    """Raise 401 if the authenticated user is not a doctor."""
    if not user.get("isDoctor"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _require_doctor_owns_patient(user, patient_id):
    """Raise 403 if the doctor doesn't have this patient."""
    _require_doctor(user)
    if patient_id not in user.get("patients", []):
        raise HTTPException(status_code=403, detail="Patient not assigned to you")

@doctorrouter.put("/create_code")
def create_doctor(code: str, doctor=Depends(get_user), db=Depends(get_db)):
    _require_doctor(doctor)
    db["doctors"].update_one(
        {"username": doctor["username"]},
        {"$set": {"code": code}},
    )
    return {"msg": "code updated"}


@doctorrouter.get("/patients")
def get_patients_by_doctor(doctor=Depends(get_user), db=Depends(get_db)):
    _require_doctor(doctor)
    return {"patients": doctor.get("patients", [])}


@doctorrouter.get("/answers")
def get_patient_answers(user_id: str, doctor=Depends(get_user), db=Depends(get_db)):
    _require_doctor(doctor)
    answers = list(db["answers"].find({"user_id": user_id}).sort("timestamp", -1))
    for a in answers:
        a["_id"] = str(a["_id"])
    return {"answers": answers}


# ---------------------------------------------------------------------------
# B3: Enriched patient list
# ---------------------------------------------------------------------------
@doctorrouter.get("/patients/details")
def get_patients_details(doctor=Depends(get_user), db=Depends(get_db)):
    """Return enriched profile + latest triage info for each of the doctor's patients."""
    _require_doctor(doctor)
    patient_usernames = doctor.get("patients", [])
    if not patient_usernames:
        return {"patients": []}

    users_coll = db["users"]
    assessments_coll = db["florence_assessments"]

    patients = []
    for uname in patient_usernames:
        profile = users_coll.find_one({"username": uname}, {"password": 0})
        if not profile:
            continue
        profile["_id"] = str(profile["_id"])

        # Get latest assessment for alert_level
        latest = assessments_coll.find_one(
            {"user_id": uname, "triage_assessment": {"$exists": True, "$ne": None}},
            sort=[("created_at", -1)],
        )
        profile["latest_alert_level"] = (
            latest.get("triage_assessment", {}).get("alert_level") if latest else None
        )
        profile["last_assessment_date"] = latest.get("created_at") if latest else None
        patients.append(profile)

    return {"patients": patients}


# ---------------------------------------------------------------------------
# B1: Doctor alerts — flagged assessments across all assigned patients
# ---------------------------------------------------------------------------
@doctorrouter.get("/alerts")
def get_doctor_alerts(limit: int = 50, doctor=Depends(get_user), db=Depends(get_db)):
    """Return florence_assessments flagged for oncologist across the doctor's patients."""
    _require_doctor(doctor)
    patient_usernames = doctor.get("patients", [])
    if not patient_usernames:
        return {"alerts": [], "count": 0}

    assessments_coll = db["florence_assessments"]
    cursor = assessments_coll.find(
        {
            "user_id": {"$in": patient_usernames},
            "$or": [
                {"flag_for_oncologist": True},
                {"oncologist_notification_level": {"$in": ["amber", "red"]}},
            ],
        }
    ).sort("created_at", -1).limit(limit)

    alerts = []
    for doc in cursor:
        triage = doc.get("triage_assessment", {}) or {}
        alerts.append({
            "session_id": doc.get("session_id"),
            "patient_id": doc.get("user_id"),
            "alert_level": triage.get("alert_level", doc.get("alert_level")),
            "alert_rationale": triage.get("alert_rationale"),
            "key_symptoms": triage.get("key_symptoms", []),
            "recommended_timeline": triage.get("recommended_timeline"),
            "confidence_level": triage.get("confidence_level"),
            "oncologist_notification_level": doc.get("oncologist_notification_level"),
            "assessment_type": doc.get("assessment_type"),
            "created_at": doc.get("created_at"),
        })

    return {"alerts": alerts, "count": len(alerts)}


# ---------------------------------------------------------------------------
# B2: Doctor access to a patient's Florence assessments
# ---------------------------------------------------------------------------
@doctorrouter.get("/patient/{patient_id}/assessments")
def get_patient_assessments(patient_id: str, limit: int = 20, doctor=Depends(get_user), db=Depends(get_db)):
    """List a patient's florence_assessments (summary, no conversation_history)."""
    _require_doctor_owns_patient(doctor, patient_id)

    cursor = db["florence_assessments"].find(
        {"user_id": patient_id, "triage_assessment": {"$exists": True, "$ne": None}},
        {"conversation_history": 0},  # exclude for performance
    ).sort("created_at", -1).limit(limit)

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)

    return {"assessments": results, "count": len(results)}


@doctorrouter.get("/patient/{patient_id}/assessment/{session_id}")
def get_patient_assessment_detail(patient_id: str, session_id: str, doctor=Depends(get_user), db=Depends(get_db)):
    """Full detail for a single assessment including conversation_history."""
    _require_doctor_owns_patient(doctor, patient_id)

    doc = db["florence_assessments"].find_one(
        {"user_id": patient_id, "session_id": session_id}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Assessment not found")
    doc["_id"] = str(doc["_id"])
    return doc


@doctorrouter.get("/patient/{patient_id}/questionnaires")
def get_patient_questionnaires(patient_id: str, limit: int = 20, doctor=Depends(get_user), db=Depends(get_db)):
    """List a patient's symptom questionnaire submissions."""
    _require_doctor_owns_patient(doctor, patient_id)

    cursor = db["symptom_questionnaires"].find(
        {"user_id": patient_id}, {"_id": 0}
    ).sort("submitted_at", -1).limit(limit)

    results = list(cursor)
    return {"questionnaires": results, "count": len(results)}