"""Florence chat endpoints. Sessions live in Mongo (`florence_sessions`, TTL-expired).

De-identification happens here, at the call site: every outbound message goes through
the session's `ScrubContext` (patient + doctor identifiers, Hong Kong gazetteers, date
and age generalisation), model output is re-identified from the session `TokenMap`
before it is stored or shown, and the gateway gets only opaque refs plus the list of
identifiers it must not see. Records stay in clear text at rest (encryption is a later
phase); the token map lives on the session document for the session's life only.
"""

import asyncio
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .login import get_user, get_db
from .inference import InferenceRefused, get_gateway
from .inference.refs import patient_ref, session_ref
from .inference.scrub import ScrubContext, ScrubError, reidentify_obj, reidentify_text
from .florence_ai import start_florence_conversation, send_message_to_florence
from .florence_memory import extract_session_memories, memory_messages, memory_mode, recall_snapshot, seed_token_map
from .florence_tools import CoverageState, build_registry
from .florence_assessment import get_florence_structured_assessment
from .florence_triage import get_florence_triage_assessment, get_alert_level_description
from .florence_utils import (
    OPENING_TURN,
    SCRUB_FAILED_REASON,
    build_scrub_context,
    create_timestamp,
    create_conversation_message,
    generate_fallback_response,
    validate_session_access,
    create_assessment_record,
    create_session_response_data,
    get_localized_message,
    model_messages,
    pending_review_fields,
    scrub_summary_from_messages,
)

logger = logging.getLogger("ovis.florence")
florencerouter = APIRouter(prefix="/florence", tags=["florence"])

SESSION_TTL = timedelta(minutes=30)
SESSIONS = "florence_sessions"
PENDING_REVIEW_STATUS = "pending_clinician_review"


# `language` is written into the audit trail and `treatment_status` is interpolated into the
# prompt templates, so both are closed sets at the API boundary rather than free text.
class StartSessionRequest(BaseModel):
    language: Literal["en", "zh-HK"] = "en"
    input_mode: Literal["keyboard", "voice"] = "keyboard"
    treatment_status: Literal["undergoing_treatment", "in_remission"] = "undergoing_treatment"


# The scrubber is linear in the message length but unbounded input is still a way to burn
# CPU in the event loop; a check-in turn is never this long.
MAX_MESSAGE_CHARS = 4000


class SendMessageRequest(BaseModel):
    session_id: str
    message: str = Field(max_length=MAX_MESSAGE_CHARS)


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
# De-identification helpers
# ---------------------------------------------------------------------------
def refusal_reason_for(error: Exception) -> str:
    """Why a model call did not happen: the gate's decision reason, or `scrub_failed` for a scrubber error."""
    if isinstance(error, InferenceRefused):
        return error.reason
    return SCRUB_FAILED_REASON


def _note_unresolved(count: int, sref: str, audit_id: Any) -> None:
    """A placeholder the model emitted that the token map cannot resolve: count only, never content."""
    if not count:
        return
    logger.warning("unresolved placeholders in model output session_ref=%s count=%d", sref, count)
    try:
        get_gateway().audit_sink.annotate(audit_id, unresolved_tokens=count)
    except Exception as e:  # noqa: BLE001 - the audit trail must never break the chat
        logger.warning("could not annotate audit event: %s", type(e).__name__)


def reidentify_reply(text: Optional[str], ctx: ScrubContext, sref: str, audit_id: Any = None) -> str:
    out, unresolved = reidentify_text(text or "", ctx.token_map)
    _note_unresolved(unresolved, sref, audit_id)
    return out


def model_history(history: list, tool_history: list | None) -> list:
    """The model-bound sequence: role/content turns with each turn's tool items spliced back in.

    Tool items are kept off `conversation_history` -- that list is rendered in the clinician view and
    counted in analytics, and its consumers assume every entry has a role. They live in their own
    session field and are put back in position here, so the model still sees what it recorded.
    """
    msgs = model_messages(history)
    if not tool_history:
        return msgs
    by_position: dict[int, list] = {}
    for entry in tool_history:
        if isinstance(entry, dict) and entry.get("items"):
            by_position.setdefault(int(entry.get("after", 0)), []).extend(entry["items"])
    out: list = []
    for position, message in enumerate(msgs, start=1):
        out.append(message)
        out.extend(by_position.get(position, []))
    return out


async def analyse_transcript(
    ctx: ScrubContext,
    history: list,
    *,
    pref: str,
    sref: str,
    language: str,
    treatment_status: str,
    task_source: str = "florence",
) -> Tuple[Optional[dict], Optional[dict]]:
    """Scrub the transcript once, run assessment + triage on it, re-identify both outputs.

    Raises `InferenceRefused` / `ScrubError` when the call must not happen (the caller writes a
    pending_clinician_review record); any other provider failure is already turned into the
    generators' conservative fallbacks.
    """
    now = datetime.now(timezone.utc)
    outbound = ctx.scrub_messages(model_messages(history), now=now, language=language)
    report = scrub_summary_from_messages(outbound, ctx.ner.name)
    known = ctx.leak_forms()
    common = dict(session_ref=sref, known_identifiers=known, scrub_report=report, task_source=task_source)
    results = await asyncio.gather(
        get_florence_structured_assessment(outbound, pref, treatment_status, language, **common),
        get_florence_triage_assessment(outbound, pref, treatment_status, language, **common),
        return_exceptions=True,
    )
    for outcome in results:
        if isinstance(outcome, (InferenceRefused, ScrubError)):
            raise outcome
    for outcome in results:
        if isinstance(outcome, BaseException):
            raise outcome
    assessment, triage = results
    structured = assessment.get("structured_assessment") if assessment else None
    triage_assessment = triage.get("triage_assessment") if triage else None
    structured, n1 = reidentify_obj(structured, ctx.token_map)
    triage_assessment, n2 = reidentify_obj(triage_assessment, ctx.token_map)
    _note_unresolved(n1, sref, (assessment or {}).get("audit_id"))
    _note_unresolved(n2, sref, (triage or {}).get("audit_id"))
    return structured, triage_assessment


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@florencerouter.post("/start_session", response_model=SessionResponse)
async def start_florence_session(request: StartSessionRequest, user=Depends(get_user), db=Depends(get_db)):
    # millisecond timestamp + random suffix: two starts in the same second must not collide
    session_id = f"{user['username']}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
    pref, sref = patient_ref(user["username"]), session_ref(session_id)
    ai_available = get_gateway().available()

    opening: Optional[str] = None
    state = "starting"
    token_map_doc = None
    refusal_reason = None
    memory_context: list = []
    if ai_available:
        ctx = None
        try:
            ctx = build_scrub_context(db, user)
            now = datetime.now(timezone.utc)
            if memory_mode() == "on":
                # Seed before anything is scrubbed, so a person the notes name is tokenised (and
                # leak-checked) in the notes and in everything the patient says this session.
                memory_context = recall_snapshot(db, user["username"], now=now)
                seed_token_map(ctx.token_map, memory_context)
            preamble = ctx.scrub_messages(memory_messages(memory_context, now, request.language),
                                          now=now, language=request.language)
            florence_response = await start_florence_conversation(
                language=request.language, patient_ref=pref, session_ref=sref, known_identifiers=ctx.leak_forms(),
                scrub_report=scrub_summary_from_messages(preamble + [{"role": "user", "content": OPENING_TURN}],
                                                         ctx.ner.name),
                preamble=preamble,
            )
            if florence_response.get("unavailable"):
                ai_available = False  # nothing configured: today's skipped/UNKNOWN fallback mode
            elif "error" in florence_response:
                # Transient provider error (timeout, 5xx): keep the session AI-enabled so the next
                # turn and the finishing analysis retry instead of recording the chat as "skipped".
                opening = generate_fallback_response("processing_error")
                state = "starting"
            else:
                opening = reidentify_reply(florence_response["response"], ctx, sref, florence_response.get("audit_id"))
                state = florence_response.get("conversation_state", "starting")
        except (InferenceRefused, ScrubError) as e:
            # Policy refusal or scrubber failure: friendly scripted opening, chat stays open, and the
            # session remains "AI available" so finishing records pending_clinician_review, not skipped.
            refusal_reason = refusal_reason_for(e)
            logger.warning("florence session_ref=%s opening refused: %s reason=%s", sref, type(e).__name__, refusal_reason)
            opening = generate_fallback_response("refused", request.language)
            state = "starting"
        if ctx is not None:
            token_map_doc = ctx.token_map.to_dict()
    if not ai_available:
        logger.info("florence session_ref=%s starting in fallback mode (no provider)", sref)
        opening = generate_fallback_response("welcome")
        state = "starting"

    session = {
        "session_id": session_id,
        "user_id": user["username"],
        "patient_ref": pref,
        "language": request.language,
        "input_mode": request.input_mode,
        "treatment_status": request.treatment_status,
        "status": "active",
        "conversation_history": [create_conversation_message("assistant", opening)],
        "created_at": create_timestamp(),
        "structured_assessment": None,
        "florence_state": state,
        "ai_available": ai_available,
        "refusal_reason": refusal_reason,
        "token_map": token_map_doc,   # session-scoped re-identification map; never returned by any endpoint
        "memory_context": memory_context,  # notes from earlier check-ins, for the model only; never returned
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
    pref, sref = patient_ref(user["username"]), session_ref(request.session_id)

    state = session.get("florence_state", "assessing")
    token_map_doc = session.get("token_map")
    tool_history = list(session.get("tool_history") or [])
    coverage = CoverageState.from_dict(session.get("symptom_state"))
    if session.get("ai_available"):
        ctx = None
        try:
            ctx = build_scrub_context(db, user, token_map_doc)
            now = datetime.now(timezone.utc)
            # Position for any tool items this turn produces: an index into the plain transcript,
            # which is what model_history() splices against. Using the spliced length instead would
            # drift once a second turn records something.
            position = len(model_messages(history))
            # Notes from earlier check-ins go first; `position` indexes the plain transcript, so tool
            # items still splice back where they belong.
            memory = memory_messages(session.get("memory_context") or [], now, language)
            outbound = ctx.scrub_messages(memory + model_history(history, tool_history), now=now, language=language)
            florence_response = await send_message_to_florence(
                outbound, language=language, patient_ref=pref, session_ref=sref,
                known_identifiers=ctx.leak_forms(),
                scrub_report=scrub_summary_from_messages(outbound, ctx.ner.name),
                tools=build_registry(coverage),
                scrub_tool_output=lambda text: ctx.scrub(text, now=now, language=language).text,
            )
            if "error" in florence_response:
                reply = generate_fallback_response("processing_error")
            else:
                reply = reidentify_reply(florence_response["response"], ctx, sref, florence_response.get("audit_id"))
                state = florence_response.get("conversation_state", "assessing")
                # The items are already de-identified (they were built from scrubbed input and
                # scrubbed again on the way back), so they are stored as the model will replay them.
                items = florence_response.get("tool_items") or []
                if items:
                    tool_history.append({"after": position, "items": items})
        except (InferenceRefused, ScrubError) as e:
            logger.warning("florence session_ref=%s turn refused: %s reason=%s", sref, type(e).__name__, refusal_reason_for(e))
            reply = generate_fallback_response("refused", language)
        if ctx is not None:
            token_map_doc = ctx.token_map.to_dict()
    else:
        reply = generate_fallback_response("general_followup")

    history.append(create_conversation_message("assistant", reply))
    _save_session(db, session, conversation_history=history, florence_state=state, token_map=token_map_doc,
                  symptom_state=coverage.to_dict(), tool_history=tool_history)

    return {"success": True, "message": "Message sent to Florence", "response": reply, "florence_state": state,
            "coverage": coverage.summary()}


# Background analysis tasks (assessment + triage) keyed by session; awaited in tests via wait_for_background()
_background_tasks: set = set()


def track_background_task(task: "asyncio.Task") -> "asyncio.Task":
    """Register a fire-and-forget task so tests (and shutdown) can await it; the bridge uses this too."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def wait_for_background() -> None:
    if _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)


async def _analyse_session(db, record_id, session: dict, user: dict) -> None:
    """Run structured assessment + triage after the patient has left, then fill in the saved record."""
    session_id = session["session_id"]
    pref, sref = patient_ref(user["username"]), session_ref(session_id)
    language = session.get("language", "en")
    history = session.get("conversation_history", [])
    treatment_status = session.get("treatment_status", "undergoing_treatment")
    try:
        ctx = build_scrub_context(db, user, session.get("token_map"))
        structured_assessment, triage_assessment = await analyse_transcript(
            ctx, history, pref=pref, sref=sref, language=language, treatment_status=treatment_status,
        )
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
        logger.info("analysis complete for session_ref=%s alert=%s", sref, completed["alert_level"])
    except asyncio.CancelledError:
        # Shutdown or deploy cancelled us mid-call: never leave the record stuck at "generating",
        # where it is invisible to the clinician and "still processing" to the patient.
        logger.warning("analysis interrupted for session_ref=%s; saved for clinician review", sref)
        try:
            db.florence_assessments.update_one({"_id": record_id}, {"$set": pending_review_fields("interrupted")})
        except Exception:  # noqa: BLE001 - we are being cancelled; nothing else can be done here
            pass
        raise
    except (InferenceRefused, ScrubError) as e:
        reason = refusal_reason_for(e)
        logger.warning("analysis refused for session_ref=%s: %s reason=%s; saved for clinician review", sref, type(e).__name__, reason)
        try:
            db.florence_assessments.update_one({"_id": record_id}, {"$set": pending_review_fields(reason)})
        except Exception as inner:  # noqa: BLE001
            logger.error("could not mark session_ref=%s pending: %s", sref, type(inner).__name__)
    except Exception as e:
        logger.error("analysis failed for session_ref=%s: %s", sref, type(e).__name__)
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
    sref = session_ref(session_id)
    if session["status"] != "active":
        existing = db.florence_assessments.find_one({"session_id": session_id})
        if existing:
            return {"message": get_localized_message("session_completed", language), **_result_payload(existing, language)}
        raise HTTPException(status_code=400, detail=get_localized_message("session_not_active", language))

    completed_at = create_timestamp()
    session["completed_at"] = completed_at
    history = session.get("conversation_history", [])
    patient_turns = sum(1 for m in history if m.get("role") == "user")
    if patient_turns == 0:
        # Nothing was said: don't manufacture an assessment or a triage alert out of an empty chat.
        _save_session(db, session, status="abandoned", completed_at=completed_at)
        logger.info("session_ref=%s abandoned with no patient messages; nothing saved", sref)
        return {
            "message": get_localized_message("session_completed", language),
            "assessment_id": None,
            "session_id": session_id,
            "triage_status": "skipped",
            "alert_level": None,
            "alert_description": None,
            "oncologist_notification_level": "none",
            "structured_assessment": None,
            "triage_assessment": None,
            "ai_available": bool(session.get("ai_available")),
            "abandoned": True,
        }
    ai_available = bool(session.get("ai_available")) and get_gateway().available()

    record = create_assessment_record(session, None, None)
    record["triage_status"] = "generating" if ai_available else "skipped"
    record["alert_level"] = "PENDING" if ai_available else "UNKNOWN"
    try:
        inserted = db.florence_assessments.insert_one(record)
    except Exception as e:
        logger.error("failed to save assessment for session_ref=%s: %s", sref, type(e).__name__)
        raise HTTPException(status_code=500, detail=get_localized_message("failed_to_save_assessment", language))

    _save_session(db, session, status="completed", completed_at=completed_at)
    logger.info("saved conversation for session_ref=%s (%d messages); analysis=%s",
                sref, len(session.get("conversation_history", [])), record["triage_status"])

    if ai_available:
        track_background_task(asyncio.create_task(_analyse_session(db, inserted.inserted_id, session, user)))
        if memory_mode() != "off":
            # Its own task, not part of the analysis: a refused or failed extraction must never
            # turn the clinical record into pending_clinician_review.
            track_background_task(asyncio.create_task(extract_session_memories(db, session, user)))

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
        session = db[SESSIONS].find_one({"session_id": session_id})
        if session and session.get("user_id") == user["username"] and session.get("status") == "abandoned":
            return {"session_id": session_id, "triage_status": "skipped", "alert_level": None, "alert_description": None,
                    "oncologist_notification_level": "none", "structured_assessment": None, "triage_assessment": None,
                    "ai_available": bool(session.get("ai_available")), "abandoned": True}
        raise HTTPException(status_code=404, detail=get_localized_message("session_not_found"))
    if record.get("user_id") != user["username"]:
        raise HTTPException(status_code=403, detail=get_localized_message("access_denied"))
    language = (record.get("language") or "en")
    return _result_payload(record, language)


@florencerouter.get("/test")
async def test_florence_endpoint(user: Dict = Depends(get_user), db=Depends(get_db)):
    """Operator smoke check. Authenticated: the body carries deployment/model names, per-provider
    health and the compliance flags. The tokenless signal is GET /health -> florence_ai."""
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
