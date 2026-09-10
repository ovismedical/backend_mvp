"""
Bridge between symptom questionnaire submissions and AI triage generation.
Converts enriched questionnaire data into pseudo-conversation format,
then feeds it to the existing Florence triage/assessment pipeline.
Runs as a fire-and-forget background task after questionnaire submission.

The synthesised conversation goes through a fresh ScrubContext (the patient's own
identifiers plus their doctor's) before it reaches the gateway; model output is
re-identified before it is stored. A policy refusal or scrubber failure saves the
questionnaire for clinician review instead of fabricating a triage.
"""

import asyncio
import logging
from typing import Any, Dict, List

from .login import get_db
from .inference import InferenceRefused, get_gateway
from .inference.refs import patient_ref, session_ref
from .inference.scrub import ScrubError
from .florence import analyse_transcript, refusal_reason_for
from .florence_utils import build_scrub_context, create_assessment_record, create_timestamp, pending_review_fields

logger = logging.getLogger("ovis.questionnaire")

TASK_SOURCE = "questionnaire"
QUESTIONNAIRES = "symptom_questionnaires"
ASSESSMENTS = "florence_assessments"


def enriched_to_conversation_history(enriched: dict) -> List[Dict[str, str]]:
    """Convert an enriched questionnaire document into a pseudo-conversation.

    The existing triage/assessment functions expect a list of
    ``{role, content}`` dicts representing a nurse–patient chat.
    We synthesise one by walking the enriched sections and turning
    each answered question into natural-language dialogue.
    """
    messages: List[Dict[str, str]] = []

    # Opening assistant greeting
    messages.append({
        "role": "assistant",
        "content": (
            "Hello! I'm going to review your symptom questionnaire responses "
            "to assess how you're feeling today."
        ),
    })

    for section in enriched.get("sections", []):
        # Collect answered responses in this section
        answered = []
        for resp in section.get("responses", []):
            if not resp.get("was_shown"):
                continue
            if resp.get("display_value") is None:
                continue
            # Format the display value
            dv = resp["display_value"]
            if isinstance(dv, list):
                dv = ", ".join(str(v) for v in dv)
            answered.append(f"- {resp['question_text']}: {dv}")

        if not answered:
            continue

        # Assistant asks about this symptom area
        messages.append({
            "role": "assistant",
            "content": f"Let me ask about {section['title']}.",
        })

        # Patient responds with all answers for this section
        severity_note = ""
        if section.get("severity_label") and section.get("severity_score") is not None:
            severity_note = f"\nOverall severity: {section['severity_label']} ({section['severity_score']})"

        messages.append({
            "role": "user",
            "content": (
                f"Regarding {section['title']}:\n"
                + "\n".join(answered)
                + severity_note
            ),
        })

    # Append alert flags as a final user message if any
    alert_flags = enriched.get("clinical_summary", {}).get("alert_flags", [])
    if alert_flags:
        messages.append({
            "role": "user",
            "content": (
                "Additional concerns I want to flag:\n"
                + "\n".join(f"- {flag}" for flag in alert_flags)
            ),
        })

    return messages


def _set_questionnaire_status(db, questionnaire_id: Any, fields: Dict[str, Any]) -> None:
    """Best-effort status update on the questionnaire document (raw `_id`: ObjectId in prod, int in tests)."""
    try:
        db[QUESTIONNAIRES].update_one({"_id": questionnaire_id}, {"$set": fields})
    except Exception as e:  # noqa: BLE001 - a status update must not mask the outcome
        logger.warning("questionnaire status update failed: %s", type(e).__name__)


def _save_pending(db, session_data: Dict[str, Any], questionnaire_id: Any, qid: str, reason: str) -> None:
    """Record a questionnaire whose triage did not happen as awaiting clinician review."""
    try:
        pending = create_assessment_record(session_data, None, None)
        pending.update(pending_review_fields(reason))
        pending["assessment_type"] = "questionnaire_triage"
        pending["source_questionnaire_id"] = qid
        db[ASSESSMENTS].insert_one(pending)
    except Exception as e:  # noqa: BLE001 - never mask the outcome that got us here
        logger.error("could not save pending questionnaire triage: %s", type(e).__name__)
    _set_questionnaire_status(db, questionnaire_id, {
        "triage_status": "pending_clinician_review", "alert_level": "PENDING_REVIEW",
    })


async def generate_questionnaire_triage(
    enriched: dict,
    questionnaire_id: Any,
    user: dict,
    language: str = "en",
    treatment_status: str = "undergoing_treatment",
    *,
    db=None,
) -> None:
    """Generate AI triage from a questionnaire submission (background task).

    Designed to be called via ``asyncio.create_task()`` so it runs in the background
    without blocking the questionnaire submit response. Failures are logged but never
    propagated - the questionnaire submission is always considered successful
    regardless of triage outcome. ``questionnaire_id`` is the raw inserted ``_id``;
    ``db`` defaults to the application database (tests pass their own).
    """
    db = db if db is not None else get_db()
    qid = str(questionnaire_id)
    synthetic_session_id = f"questionnaire_{qid}"
    pref, sref = patient_ref(user["username"]), session_ref(synthetic_session_id)

    try:
        # Build pseudo-conversation from enriched data
        conversation_history = enriched_to_conversation_history(enriched)

        # Skip if too few messages (no answered sections)
        if len(conversation_history) < 3:
            logger.info("questionnaire triage skipped session_ref=%s: too few responses", sref)
            _set_questionnaire_status(db, questionnaire_id, {"triage_status": "skipped"})
            return

        # Dedup: check if triage already exists for this questionnaire
        existing = db[ASSESSMENTS].find_one({"session_id": synthetic_session_id})
        if existing:
            logger.info("questionnaire triage already exists session_ref=%s", sref)
            return

        if not get_gateway().available():
            logger.info("questionnaire triage skipped session_ref=%s: no inference provider configured", sref)
            _set_questionnaire_status(db, questionnaire_id, {"triage_status": "skipped"})
            return

        # Build session_data compatible with create_assessment_record (no display name on the record)
        session_data = {
            "session_id": synthetic_session_id,
            "user_id": user["username"],
            "patient_ref": pref,
            "language": language,
            "input_mode": "questionnaire",
            "conversation_history": conversation_history,
            "created_at": enriched.get("timestamp", create_timestamp()),
            "completed_at": create_timestamp(),
            "ai_available": True,
        }

        logger.info("generating questionnaire triage session_ref=%s", sref)
        try:
            ctx = build_scrub_context(db, user)
            structured_assessment, triage_assessment = await analyse_transcript(
                ctx, conversation_history, pref=pref, sref=sref, language=language,
                treatment_status=treatment_status, task_source=TASK_SOURCE,
            )
        except asyncio.CancelledError:
            # Shutdown or deploy cancelled us mid-call: save the questionnaire for the clinician
            # rather than leaving it with no triage at all.
            logger.warning("questionnaire triage interrupted session_ref=%s; saved for clinician review", sref)
            _save_pending(db, session_data, questionnaire_id, qid, "interrupted")
            raise
        except (InferenceRefused, ScrubError) as e:
            reason = refusal_reason_for(e)
            logger.warning("questionnaire triage refused session_ref=%s: %s reason=%s; saved for clinician review",
                           sref, type(e).__name__, reason)
            _save_pending(db, session_data, questionnaire_id, qid, reason)
            return

        assessment_record = create_assessment_record(session_data, structured_assessment, triage_assessment)

        # Override assessment_type and add source reference
        assessment_record["assessment_type"] = "questionnaire_triage"
        assessment_record["source_questionnaire_id"] = qid
        assessment_record["triage_status"] = "completed"
        assessment_record["analysed_at"] = create_timestamp()

        # Store in florence_assessments
        db[ASSESSMENTS].insert_one(assessment_record)

        alert_level = assessment_record.get("alert_level", "UNKNOWN")
        logger.info("questionnaire triage complete session_ref=%s alert=%s", sref, alert_level)

        # Update questionnaire doc status
        _set_questionnaire_status(db, questionnaire_id, {"triage_status": "completed", "alert_level": alert_level})

    except Exception as e:
        logger.error("questionnaire triage failed session_ref=%s: %s", sref, type(e).__name__)
        _set_questionnaire_status(db, questionnaire_id, {"triage_status": "failed", "triage_error": type(e).__name__})
