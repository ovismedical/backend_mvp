import logging
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .login import get_db, get_user

logger = logging.getLogger("ovis.doctor")

doctorrouter = APIRouter(prefix="/doctor", tags=["doctor"])

ALERT_LEVELS = ("GREEN", "YELLOW", "ORANGE", "RED")
PENDING_REVIEW_STATUS = "pending_clinician_review"
PENDING_REVIEW_LEVEL = "PENDING_REVIEW"
REVIEWS = "assessment_reviews"

# A clinician sees records that have a completed triage OR are waiting for a clinician because the
# AI assessment was refused. Records still generating / skipped / failed stay hidden, as before.
# (Kept as a list of clauses so callers can splice it into an existing "$or"; MockCollection has no "$and".)
VISIBLE_TRIAGE_CLAUSES = [
    {"triage_assessment": {"$exists": True, "$ne": None}},
    {"triage_status": PENDING_REVIEW_STATUS},
]


def _visible_filter(**extra) -> dict:
    return {**extra, "$or": list(VISIBLE_TRIAGE_CLAUSES)}


def _require_doctor(user):
    """Raise 401 if the authenticated user is not a doctor."""
    if not user.get("isDoctor"):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _require_doctor_owns_patient(user, patient_id):
    """Raise 403 if the doctor doesn't have this patient."""
    _require_doctor(user)
    if patient_id not in user.get("patients", []):
        raise HTTPException(status_code=403, detail="Patient not assigned to you")


# ---------------------------------------------------------------------------
# Clinician reviews of Florence assessments
# ---------------------------------------------------------------------------
def ensure_review_indexes(db) -> None:
    """Best-effort unique index on (session_id, doctor) so a clinician has one review per assessment."""
    try:
        db[REVIEWS].create_index([("session_id", 1), ("doctor", 1)], unique=True)
    except Exception as e:  # mock DBs, read-only users
        logger.warning("could not create assessment_reviews indexes: %s", type(e).__name__)


def _public_review(review: dict) -> dict:
    return {
        "session_id": review.get("session_id"),
        "user_id": review.get("user_id"),
        "doctor": review.get("doctor"),
        "agrees": review.get("agrees"),
        "alert_level_override": review.get("alert_level_override"),
        "note": review.get("note"),
        "florence_alert_level": review.get("florence_alert_level"),
        "reviewed_at": review.get("reviewed_at"),
    }


def _florence_alert_level(doc: dict) -> Optional[str]:
    """The level Florence assigned, or PENDING_REVIEW when the AI assessment was refused."""
    if doc.get("triage_status") == PENDING_REVIEW_STATUS:
        return PENDING_REVIEW_LEVEL
    return (doc.get("triage_assessment") or {}).get("alert_level") or doc.get("alert_level")


def _effective_alert_level(doc: dict, review: Optional[dict]) -> Optional[str]:
    """Clinician override when present, else Florence's level (PENDING_REVIEW for a refused record)."""
    if review and review.get("alert_level_override"):
        return review["alert_level_override"]
    return _florence_alert_level(doc)


def attach_reviews(db, docs, doctor: Optional[str] = None):
    """Add `review` (or None) and `effective_alert_level` to each assessment doc, in place, with one query.

    When `doctor` is given their own review wins; otherwise the most recent review of the session is used.
    """
    session_ids = [d.get("session_id") for d in docs if d.get("session_id")]
    by_session: dict = {}
    if session_ids:
        for review in db[REVIEWS].find({"session_id": {"$in": session_ids}}):
            current = by_session.get(review["session_id"])
            if current is None:
                by_session[review["session_id"]] = review
                continue
            if current.get("doctor") == doctor:
                continue
            if review.get("doctor") == doctor or (review.get("reviewed_at") or "") > (current.get("reviewed_at") or ""):
                by_session[review["session_id"]] = review
    for doc in docs:
        raw = by_session.get(doc.get("session_id"))
        review = _public_review(raw) if raw else None
        doc["review"] = review
        doc["effective_alert_level"] = _effective_alert_level(doc, review)
    return docs


class ReviewIn(BaseModel):
    agrees: Optional[bool] = None
    alert_level_override: Optional[Literal["GREEN", "YELLOW", "ORANGE", "RED"]] = None
    note: Optional[str] = Field(default=None, max_length=500)


def _load_reviewable_assessment(db, session_id: str, doctor: dict) -> dict:
    doc = db["florence_assessments"].find_one({"session_id": session_id}, {"conversation_history": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Assessment not found")
    _require_doctor_owns_patient(doctor, doc.get("user_id"))
    return doc


@doctorrouter.post("/assessments/{session_id}/review")
def review_assessment(session_id: str, body: ReviewIn, doctor=Depends(get_user), db=Depends(get_db)):
    """Record whether the clinician agrees with Florence's alert level, or assign their own.

    Triaged record: `agrees` is required; disagreeing requires `alert_level_override`.
    Record without a Florence level (awaiting clinician review because the AI was refused, or skipped/failed):
    `alert_level_override` is required and `agrees` is stored as null.
    One review per (session, doctor) - posting again replaces the earlier one.
    """
    _require_doctor(doctor)
    doc = _load_reviewable_assessment(db, session_id, doctor)

    florence_level = _florence_alert_level(doc)
    if florence_level not in ALERT_LEVELS:
        florence_level = None
    override = body.alert_level_override
    if florence_level is None:
        if not override:
            raise HTTPException(status_code=422, detail="alert_level_override is required for a record without a Florence alert level")
        agrees = None
    else:
        if body.agrees is None:
            raise HTTPException(status_code=422, detail="agrees is required")
        if body.agrees is False and not override:
            raise HTTPException(status_code=422, detail="alert_level_override is required when disagreeing")
        if body.agrees is True and override and override != florence_level:
            raise HTTPException(status_code=422, detail="alert_level_override must match Florence's level when agreeing")
        if body.agrees is True:
            override = None
        agrees = body.agrees

    review = {
        "session_id": session_id,
        "user_id": doc.get("user_id"),
        "doctor": doctor["username"],
        "agrees": agrees,
        "alert_level_override": override,
        "note": body.note,
        "florence_alert_level": florence_level,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    db[REVIEWS].update_one(
        {"session_id": session_id, "doctor": doctor["username"]},
        {"$set": review},
        upsert=True,
    )
    public = _public_review(review)
    return {"review": public, "effective_alert_level": _effective_alert_level(doc, public)}


@doctorrouter.get("/assessments/{session_id}/review")
def get_assessment_review(session_id: str, doctor=Depends(get_user), db=Depends(get_db)):
    """The requesting clinician's review of an assessment (null when none), plus the effective alert level."""
    _require_doctor(doctor)
    doc = _load_reviewable_assessment(db, session_id, doctor)
    attach_reviews(db, [doc], doctor=doctor["username"])
    return {"review": doc["review"], "effective_alert_level": doc["effective_alert_level"]}


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
    _require_doctor_owns_patient(doctor, user_id)
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

        # Latest triaged-or-pending assessment drives the alert level (clinician override wins);
        # any assessment or questionnaire drives "last activity".
        latest = assessments_coll.find_one(
            _visible_filter(user_id=uname),
            {"conversation_history": 0},
            sort=[("created_at", -1)],
        )
        any_assessment = assessments_coll.find_one({"user_id": uname}, {"created_at": 1, "oncologist_notification_level": 1},
                                                   sort=[("created_at", -1)])
        latest_q = db["symptom_questionnaires"].find_one({"user_id": uname}, {"timestamp": 1, "alert_level": 1},
                                                          sort=[("submitted_at", -1)])
        alert = None
        latest_review = None
        if latest:
            attach_reviews(db, [latest], doctor=doctor["username"])
            alert = latest["effective_alert_level"]
            if latest["review"]:
                latest_review = {
                    "agrees": latest["review"]["agrees"],
                    "alert_level_override": latest["review"]["alert_level_override"],
                }
        if not alert and latest_q and latest_q.get("alert_level") not in (None, "UNKNOWN", "PENDING"):
            alert = latest_q.get("alert_level")
        if not alert and any_assessment:
            alert = {"red": "RED", "amber": "YELLOW", "none": "GREEN"}.get(any_assessment.get("oncologist_notification_level"))
        dates = [d for d in (
            latest.get("created_at") if latest else None,
            any_assessment.get("created_at") if any_assessment else None,
            latest_q.get("timestamp") if latest_q else None,
        ) if isinstance(d, str)]
        profile["latest_alert_level"] = alert
        profile["latest_review"] = latest_review
        profile["last_assessment_date"] = max(dates) if dates else None
        patients.append(profile)

    return {"patients": patients}


# ---------------------------------------------------------------------------
# B1: Doctor alerts — flagged assessments across all assigned patients
# ---------------------------------------------------------------------------
@doctorrouter.get("/alerts")
def get_doctor_alerts(limit: int = 50, doctor=Depends(get_user), db=Depends(get_db)):
    """Return florence_assessments flagged for oncologist, or awaiting clinician review, across the doctor's patients."""
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
                {"triage_status": PENDING_REVIEW_STATUS},
            ],
        },
        {"conversation_history": 0},
    ).sort("created_at", -1).limit(limit)

    docs = attach_reviews(db, list(cursor), doctor=doctor["username"])
    alerts = []
    for doc in docs:
        triage = doc.get("triage_assessment", {}) or {}
        alerts.append({
            "session_id": doc.get("session_id"),
            "patient_id": doc.get("user_id"),
            "alert_level": _florence_alert_level(doc),
            "effective_alert_level": doc["effective_alert_level"],
            "review": doc["review"],
            "triage_status": doc.get("triage_status"),
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
    """List a patient's florence_assessments (summary, no conversation_history), including records awaiting review."""
    _require_doctor_owns_patient(doctor, patient_id)

    cursor = db["florence_assessments"].find(
        _visible_filter(user_id=patient_id),
        {"conversation_history": 0},  # exclude for performance
    ).sort("created_at", -1).limit(limit)

    results = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        results.append(doc)
    attach_reviews(db, results, doctor=doctor["username"])

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
    attach_reviews(db, [doc], doctor=doctor["username"])
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
