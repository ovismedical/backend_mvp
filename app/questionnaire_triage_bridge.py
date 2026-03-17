"""
Bridge between symptom questionnaire submissions and AI triage generation.
Converts enriched questionnaire data into pseudo-conversation format,
then feeds it to the existing Florence triage/assessment pipeline.
Runs as a fire-and-forget background task after questionnaire submission.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from .login import get_db
from .florence_assessment import (
    initialize_florence_assessment,
    get_florence_structured_assessment,
)
from .florence_triage import (
    initialize_florence_triage,
    get_florence_triage_assessment,
)
from .florence_utils import create_assessment_record, create_timestamp


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


async def generate_questionnaire_triage(
    enriched: dict,
    questionnaire_id: str,
    user: dict,
    language: str = "en",
    treatment_status: str = "undergoing_treatment",
) -> None:
    """Generate AI triage from a questionnaire submission (background task).

    This is designed to be called via ``asyncio.create_task()`` so it runs
    in the background without blocking the questionnaire submit response.
    Failures are logged but never propagated — the questionnaire submission
    is always considered successful regardless of triage outcome.
    """
    db = get_db()

    try:
        # Build pseudo-conversation from enriched data
        conversation_history = enriched_to_conversation_history(enriched)

        # Skip if too few messages (no answered sections)
        if len(conversation_history) < 3:
            print(f"⏭️ Skipping triage for questionnaire {questionnaire_id}: too few responses")
            db["symptom_questionnaires"].update_one(
                {"_id": __import__("bson").ObjectId(questionnaire_id)},
                {"$set": {"triage_status": "skipped"}},
            )
            return

        # Dedup: check if triage already exists for this questionnaire
        existing = db["florence_assessments"].find_one(
            {"session_id": f"questionnaire_{questionnaire_id}"}
        )
        if existing:
            print(f"⏭️ Triage already exists for questionnaire {questionnaire_id}")
            return

        # Initialize AI modules
        api_key = os.getenv("OPENAI_API_KEY")
        await initialize_florence_assessment(api_key)
        await initialize_florence_triage(api_key)

        # Run triage + structured assessment in parallel
        print(f"🚀 Generating triage for questionnaire {questionnaire_id}...")
        assessment_result, triage_result = await asyncio.gather(
            get_florence_structured_assessment(
                conversation_history,
                user["username"],
                treatment_status,
                language,
            ),
            get_florence_triage_assessment(
                conversation_history,
                user["username"],
                treatment_status,
                language,
            ),
        )

        structured_assessment = (
            assessment_result.get("structured_assessment") if assessment_result else None
        )
        triage_assessment = (
            triage_result.get("triage_assessment") if triage_result else None
        )

        # Build session_data compatible with create_assessment_record
        session_data = {
            "session_id": f"questionnaire_{questionnaire_id}",
            "user_id": user["username"],
            "user_info": user,
            "language": language,
            "input_mode": "questionnaire",
            "conversation_history": conversation_history,
            "created_at": enriched.get("timestamp", create_timestamp()),
            "completed_at": create_timestamp(),
            "ai_available": True,
        }

        assessment_record = create_assessment_record(
            session_data, structured_assessment, triage_assessment
        )

        # Override assessment_type and add source reference
        assessment_record["assessment_type"] = "questionnaire_triage"
        assessment_record["source_questionnaire_id"] = questionnaire_id

        # Store in florence_assessments
        db["florence_assessments"].insert_one(assessment_record)

        alert_level = assessment_record.get("alert_level", "UNKNOWN")
        print(f"✅ Triage generated for questionnaire {questionnaire_id}: alert_level={alert_level}")

        # Update questionnaire doc status
        db["symptom_questionnaires"].update_one(
            {"_id": __import__("bson").ObjectId(questionnaire_id)},
            {"$set": {"triage_status": "completed", "alert_level": alert_level}},
        )

    except Exception as e:
        print(f"❌ Failed to generate triage for questionnaire {questionnaire_id}: {e}")
        try:
            db["symptom_questionnaires"].update_one(
                {"_id": __import__("bson").ObjectId(questionnaire_id)},
                {"$set": {"triage_status": "failed", "triage_error": str(e)}},
            )
        except Exception:
            pass  # Don't let status update failure mask the original error
