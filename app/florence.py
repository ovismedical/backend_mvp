"""Florence chat endpoints. Sessions live in Mongo (`florence_sessions`, TTL-expired)."""

import asyncio
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .login import get_user, get_db
from .inference import get_gateway
from .florence_ai import start_florence_conversation, send_message_to_florence
from .florence_assessment import get_florence_structured_assessment
from .florence_triage import get_florence_triage_assessment, get_alert_level_description
from .florence_utils import (
    create_timestamp,
    create_conversation_message,
    generate_fallback_response,
    validate_session_access,
    create_assessment_record,
    create_session_response_data,
    get_localized_message,
)

logger = logging.getLogger("ovis.florence")
florencerouter = APIRouter(prefix="/florence", tags=["florence"])

SESSION_TTL = timedelta(minutes=30)
SESSIONS = "florence_sessions"


class StartSessionRequest(BaseModel):
    language: str = "en"
    input_mode: str = "keyboard"
    treatment_status: str = "undergoing_treatment"


class SendMessageRequest(BaseModel):
    session_id: str
    message: str


class SessionResponse(BaseModel):
    session_id: str
    status: str
    message: str
    ai_available: bool = True


# ---------------------------------------------------------------------------
# Session storage helpers
# ---------------------------------------------------------------------------
def ensure_session_index(db) -> None:
    try:
        db[SESSIONS].create_index("expires_at", expireAfterSeconds=0)
        db[SESSIONS].create_index("session_id", unique=True)
    except Exception as e:  # index creation is best-effort (mock DBs, read-only users)
        logger.warning("could not create florence_sessions indexes: %s", type(e).__name__)


def _load_session(db, session_id: str, user: dict) -> dict:
    session = db[SESSIONS].find_one({"session_id": session_id})
    language = (session or {}).get("language", "en")
    if not session:
        raise HTTPException(status_code=404, detail=get_localized_message("session_not_found"))
    if not validate_session_access(session, user["username"]):
        raise HTTPException(status_code=403, detail=get_localized_message("access_denied", language))
    expires_at = session.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail=get_localized_message("session_expired", language))
    return session


def _save_session(db, session: dict, **changes) -> None:
    session.update(changes)
    session["expires_at"] = datetime.now(timezone.utc) + SESSION_TTL
    payload = {k: v for k, v in session.items() if k != "_id"}
    db[SESSIONS].update_one({"session_id": session["session_id"]}, {"$set": payload})


def active_session_count(db) -> int:
    try:
        return db[SESSIONS].count_documents({"status": "active"})
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@florencerouter.post("/start_session", response_model=SessionResponse)
async def start_florence_session(request: StartSessionRequest, user=Depends(get_user), db=Depends(get_db)):
    # millisecond timestamp + random suffix: two starts in the same second must not collide
    session_id = f"{user['username']}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    patient_name = user.get("full_name", user["username"])
    ai_available = get_gateway().available()

    if ai_available:
        florence_response = await start_florence_conversation(patient_name, language=request.language)
        ai_available = "error" not in florence_response
    if not ai_available:
        logger.info("florence session %s starting in fallback mode (no provider)", session_id)
        opening = generate_fallback_response(patient_name, "welcome")
        state = "starting"
    else:
        opening = florence_response["response"]
        state = florence_response.get("conversation_state", "starting")

    session = {
        "session_id": session_id,
        "user_id": user["username"],
        # Minimal identity only: the assessment record needs a display name, nothing else.
        "user_info": {"username": user["username"], "full_name": user.get("full_name")},
        "language": request.language,
        "input_mode": request.input_mode,
        "treatment_status": request.treatment_status,
        "status": "active",
        "conversation_history": [create_conversation_message("assistant", opening)],
        "created_at": create_timestamp(),
        "structured_assessment": None,
        "florence_state": state,
        "ai_available": ai_available,
        "oncologist_notification_level": "none",
        "flag_for_oncologist": False,
        "expires_at": datetime.now(timezone.utc) + SESSION_TTL,
    }
    db[SESSIONS].insert_one(session)
    return SessionResponse(session_id=session_id, status="active", message=opening, ai_available=ai_available)


@florencerouter.get("/session/{session_id}")
async def get_session_status(session_id: str, user=Depends(get_user), db=Depends(get_db)):
    return create_session_response_data(_load_session(db, session_id, user))


@florencerouter.post("/send_message")
async def send_message_to_florence_endpoint(request: SendMessageRequest, user=Depends(get_user), db=Depends(get_db)):
    session = _load_session(db, request.session_id, user)
    language = session.get("language", "en")
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail=get_localized_message("session_not_active", language))

    history = list(session.get("conversation_history", []))
    history.append(create_conversation_message("user", request.message))
    patient_name = (session.get("user_info") or {}).get("full_name") or user["username"]

    state = session.get("florence_state", "assessing")
    if session.get("ai_available"):
        florence_response = await send_message_to_florence(request.message, history[:-1], language=language)
        if "error" in florence_response:
            reply = generate_fallback_response(patient_name, "processing_error")
        else:
            reply = florence_response["response"]
            state = florence_response.get("conversation_state", "assessing")
    else:
        reply = generate_fallback_response(patient_name, "general_followup")

    history.append(create_conversation_message("assistant", reply))
    _save_session(db, session, conversation_history=history, florence_state=state)

    return {"success": True, "message": "Message sent to Florence", "response": reply, "florence_state": state}


# Background analysis tasks (assessment + triage) keyed by session; awaited in tests via wait_for_background()
_background_tasks: set = set()


async def wait_for_background() -> None:
    if _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)


async def _analyse_session(db, record_id, session: dict, username: str) -> None:
    """Run structured assessment + triage after the patient has left, then fill in the saved record."""
    session_id = session["session_id"]
    language = session.get("language", "en")
    history = session.get("conversation_history", [])
    treatment_status = session.get("treatment_status", "undergoing_treatment")
    try:
        assessment, triage = await asyncio.gather(
            get_florence_structured_assessment(history, username, treatment_status, language),
            get_florence_triage_assessment(history, username, treatment_status, language),
        )
        structured_assessment = assessment.get("structured_assessment") if assessment else None
        triage_assessment = triage.get("triage_assessment") if triage else None
        completed = create_assessment_record(session, structured_assessment, triage_assessment)
        update = {
            "structured_assessment": structured_assessment,
            "triage_assessment": triage_assessment,
            "alert_level": completed["alert_level"],
            "oncologist_notification_level": completed["oncologist_notification_level"],
            "flag_for_oncologist": completed["flag_for_oncologist"],
            "triage_status": "completed",
            "analysed_at": create_timestamp(),
        }
        db.florence_assessments.update_one({"_id": record_id}, {"$set": update})
        db[SESSIONS].update_one({"session_id": session_id}, {"$set": {
            "structured_assessment": structured_assessment,
            "oncologist_notification_level": completed["oncologist_notification_level"],
            "flag_for_oncologist": completed["flag_for_oncologist"],
        }})
        logger.info("analysis complete for session %s alert=%s", session_id, completed["alert_level"])
    except Exception as e:
        logger.error("analysis failed for session %s: %s", session_id, type(e).__name__)
        try:
            db.florence_assessments.update_one({"_id": record_id}, {"$set": {"triage_status": "failed", "triage_error": type(e).__name__}})
        except Exception:
            pass


def _result_payload(record: dict, language: str) -> dict:
    status = record.get("triage_status") or ("completed" if record.get("triage_assessment") else "skipped")
    alert_level = record.get("alert_level") if status == "completed" else None
    return {
        "session_id": record.get("session_id"),
        "triage_status": status,
        "alert_level": alert_level,
        "alert_description": get_alert_level_description(alert_level, language) if alert_level else None,
        "oncologist_notification_level": record.get("oncologist_notification_level", "none"),
        "structured_assessment": record.get("structured_assessment") if status == "completed" else None,
        "triage_assessment": record.get("triage_assessment") if status == "completed" else None,
        "ai_available": bool(record.get("ai_powered")),
    }


@florencerouter.post("/finish_session/{session_id}")
async def finish_florence_session(session_id: str, user: Dict = Depends(get_user), db=Depends(get_db)):
    """Save the conversation immediately; assessment + triage run in the background (poll /florence/result)."""
    session = _load_session(db, session_id, user)
    language = session.get("language", "en")
    if session["status"] != "active":
        existing = db.florence_assessments.find_one({"session_id": session_id})
        if existing:
            return {"message": get_localized_message("session_completed", language), **_result_payload(existing, language)}
        raise HTTPException(status_code=400, detail=get_localized_message("session_not_active", language))

    completed_at = create_timestamp()
    session["completed_at"] = completed_at
    ai_available = bool(session.get("ai_available")) and get_gateway().available()

    record = create_assessment_record(session, None, None)
    record["triage_status"] = "generating" if ai_available else "skipped"
    record["alert_level"] = "PENDING" if ai_available else "UNKNOWN"
    try:
        inserted = db.florence_assessments.insert_one(record)
    except Exception as e:
        logger.error("failed to save assessment for session %s: %s", session_id, type(e).__name__)
        raise HTTPException(status_code=500, detail=get_localized_message("failed_to_save_assessment", language))

    _save_session(db, session, status="completed", completed_at=completed_at)
    logger.info("saved conversation for session %s (%d messages); analysis=%s",
                session_id, len(session.get("conversation_history", [])), record["triage_status"])

    if ai_available:
        task = asyncio.create_task(_analyse_session(db, inserted.inserted_id, session, user["username"]))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return {
        "message": get_localized_message("session_completed", language),
        "assessment_id": str(inserted.inserted_id),
        **_result_payload(record, language),
    }


@florencerouter.get("/result/{session_id}")
async def get_session_result(session_id: str, user: Dict = Depends(get_user), db=Depends(get_db)):
    """Poll for the background assessment/triage of a finished session."""
    record = db.florence_assessments.find_one({"session_id": session_id})
    if not record:
        raise HTTPException(status_code=404, detail=get_localized_message("session_not_found"))
    if record.get("user_id") != user["username"]:
        raise HTTPException(status_code=403, detail=get_localized_message("access_denied"))
    language = (record.get("language") or "en")
    return _result_payload(record, language)


@florencerouter.get("/test")
async def test_florence_endpoint(db=Depends(get_db)):
    gateway = get_gateway()
    return {
        "status": "ok",
        "message": "Florence module is working!",
        "timestamp": create_timestamp(),
        "active_sessions": active_session_count(db),
        "openai_available": gateway.available(),
        "ai_enabled": gateway.available(),
        "inference": gateway.describe(),
    }
