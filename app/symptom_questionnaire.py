import asyncio

from .login import get_db, get_user
from .achievements import check_and_unlock_achievements
from .questionnaire_models import SubmissionInput, DraftInput
from .questionnaire_enrichment import enrich_submission
from .questionnaire_triage_bridge import generate_questionnaire_triage
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone, timedelta

symptom_router = APIRouter(tags=["symptom_questionnaire"])


@symptom_router.post("/symptom-questionnaire/submit")
async def submit_symptom_questionnaire(
    submission: SubmissionInput,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Submit completed symptom questionnaire with structured enrichment."""
    try:
        questionnaires_collection = db["symptom_questionnaires"]

        # Enrich raw answers into structured clinical document
        enriched = enrich_submission(
            raw_answers=submission.answers,
            sections_completed=submission.sections_completed,
            total_sections=submission.total_sections,
            completion_percentage=submission.completion_percentage,
            submission_mode=submission.submission_mode,
        )

        # Add user + timestamp fields
        now = datetime.now(timezone.utc)
        enriched["user_id"] = user["username"]
        enriched["submitted_at"] = now
        enriched["date"] = now.strftime("%Y-%m-%d")
        enriched["timestamp"] = now.isoformat()

        # Backward compat: keep top-level fields the dashboard reads
        enriched["sections_completed"] = submission.sections_completed
        enriched["total_sections"] = submission.total_sections
        enriched["completion_percentage"] = submission.completion_percentage
        enriched["answers"] = submission.answers

        result = questionnaires_collection.insert_one(enriched)

        # Spawn background triage generation (fire-and-forget)
        asyncio.create_task(
            generate_questionnaire_triage(
                enriched=enriched,
                questionnaire_id=str(result.inserted_id),
                user=user,
                language="en",
                treatment_status=user.get("treatment_status", "undergoing_treatment"),
            )
        )

        # Update user's streak and last completion date
        users = db["users"]
        today = now.strftime("%Y-%m-%d")

        user_doc = users.find_one({"username": user["username"]})
        if user_doc:
            current_streak = user_doc.get("questionnaire_streak", 0)
            last_completion = user_doc.get("last_questionnaire_completion")

            if last_completion == today:
                return {
                    "message": "Symptom questionnaire submitted successfully",
                    "questionnaire_id": str(result.inserted_id),
                    "streak": current_streak,
                    "triage_status": "generating"
                }

            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            if last_completion == yesterday:
                new_streak = current_streak + 1
            else:
                new_streak = 1

            users.update_one(
                {"username": user["username"]},
                {"$set": {
                    "questionnaire_streak": new_streak,
                    "last_questionnaire_completion": today
                }}
            )

            overall_longest = max(user_doc.get("longest_streak", 0), new_streak)
            newly_unlocked = check_and_unlock_achievements(db, user["username"], overall_longest)

            return {
                "message": "Symptom questionnaire submitted successfully",
                "questionnaire_id": str(result.inserted_id),
                "streak": new_streak,
                "newly_unlocked": newly_unlocked,
                "triage_status": "generating"
            }

        return {
            "message": "Symptom questionnaire submitted successfully",
            "questionnaire_id": str(result.inserted_id),
            "triage_status": "generating"
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to submit symptom questionnaire: {str(e)}"
        )


@symptom_router.post("/symptom-questionnaire/save-draft")
async def save_questionnaire_draft(
    draft: DraftInput,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Save questionnaire draft for later completion"""
    try:
        drafts_collection = db["questionnaire_drafts"]

        existing_draft = drafts_collection.find_one({"user_id": user["username"]})

        draft_data = {
            "user_id": user["username"],
            "answers": draft.answers,
            "current_section": draft.current_section,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }

        if existing_draft:
            drafts_collection.update_one(
                {"user_id": user["username"]},
                {"$set": draft_data}
            )
            message = "Draft updated successfully"
        else:
            drafts_collection.insert_one(draft_data)
            message = "Draft saved successfully"

        return {"message": message}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save draft: {str(e)}"
        )


@symptom_router.get("/symptom-questionnaire/draft")
async def get_questionnaire_draft(
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Retrieve saved draft for current user"""
    try:
        drafts_collection = db["questionnaire_drafts"]

        draft = drafts_collection.find_one(
            {"user_id": user["username"]},
            {"_id": 0}
        )

        if draft:
            return draft
        else:
            return None

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve draft: {str(e)}"
        )


@symptom_router.delete("/symptom-questionnaire/draft")
async def delete_questionnaire_draft(
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Delete saved draft after submission or user request"""
    try:
        drafts_collection = db["questionnaire_drafts"]

        result = drafts_collection.delete_one({"user_id": user["username"]})

        if result.deleted_count > 0:
            return {"message": "Draft deleted successfully"}
        else:
            return {"message": "No draft found"}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete draft: {str(e)}"
        )


@symptom_router.get("/symptom-questionnaire/history")
async def get_questionnaire_history(
    user = Depends(get_user),
    db = Depends(get_db),
    limit: int = 10
):
    """Get user's questionnaire submission history"""
    try:
        questionnaires_collection = db["symptom_questionnaires"]

        history = list(questionnaires_collection.find(
            {"user_id": user["username"]},
            {"_id": 0}
        ).sort("submitted_at", -1).limit(limit))

        return {"history": history}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve history: {str(e)}"
        )


@symptom_router.get("/symptom-questionnaire/latest")
async def get_latest_questionnaire(
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Get user's most recent questionnaire submission"""
    try:
        questionnaires_collection = db["symptom_questionnaires"]

        latest = questionnaires_collection.find_one(
            {"user_id": user["username"]},
            {"_id": 0},
            sort=[("submitted_at", -1)]
        )

        if latest:
            return latest
        else:
            return None

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve latest questionnaire: {str(e)}"
        )
