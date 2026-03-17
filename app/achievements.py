from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone, timedelta
from .login import get_user, get_db

achievementsrouter = APIRouter(prefix="/achievements", tags=["achievements"])

# Achievement definitions — single source of truth for all unlock conditions
ACHIEVEMENT_DEFINITIONS = [
    {
        "id": "7daystreak",
        "type": "streak",
        "title": {"en": "7-Day Streak", "zh": "7天連續"},
        "description": {"en": "Logged symptoms daily for 7 days", "zh": "連續7天每日記錄症狀"},
        "threshold": 7,
        "badge_image": "7daystreak",
    },
    {
        "id": "day10",
        "type": "streak",
        "title": {"en": "10-Day Streak", "zh": "10天連續"},
        "description": {"en": "Logged symptoms daily for 10 days", "zh": "連續10天每日記錄症狀"},
        "threshold": 10,
        "badge_image": "day10",
    },
    {
        "id": "14daystreak",
        "type": "streak",
        "title": {"en": "14-Day Streak", "zh": "14天連續"},
        "description": {"en": "Logged symptoms daily for 14 days", "zh": "連續14天每日記錄症狀"},
        "threshold": 14,
        "badge_image": "14daystreak",
    },
    {
        "id": "day20",
        "type": "streak",
        "title": {"en": "20-Day Streak", "zh": "20天連續"},
        "description": {"en": "Logged symptoms daily for 20 days", "zh": "連續20天每日記錄症狀"},
        "threshold": 20,
        "badge_image": "day20",
    },
    {
        "id": "21daystreak",
        "type": "streak",
        "title": {"en": "21-Day Streak", "zh": "21天連續"},
        "description": {"en": "Logged symptoms daily for 21 days", "zh": "連續21天每日記錄症狀"},
        "threshold": 21,
        "badge_image": "21daystreak",
    },
    {
        "id": "25logs",
        "type": "milestone",
        "title": {"en": "25 Day Club", "zh": "25天俱樂部"},
        "description": {"en": "Logged symptoms for 25 days", "zh": "記錄症狀25天"},
        "threshold": 25,
        "badge_image": "25logs",
    },
    {
        "id": "30daystreak",
        "type": "streak",
        "title": {"en": "30-Day Streak", "zh": "30天連續"},
        "description": {"en": "A full month of daily logging", "zh": "整整一個月的每日記錄"},
        "threshold": 30,
        "badge_image": "30daystreak",
    },
    {
        "id": "50logs",
        "type": "milestone",
        "title": {"en": "50 Day Legend", "zh": "50天傳奇"},
        "description": {"en": "Logged symptoms for 50 days", "zh": "記錄症狀50天"},
        "threshold": 50,
        "badge_image": "50logs",
    },
]

# Milestone thresholds shown on the achievements page (14, 21, 25)
MILESTONE_IDS = {"14daystreak", "21daystreak", "25logs"}


def check_and_unlock_achievements(db, username: str, longest_streak: int):
    """Check streak-based achievements and unlock any newly earned ones.

    Called after streak updates in submit_answers and submit_symptom_questionnaire.
    Uses upsert with $setOnInsert so existing unlocks are never overwritten.

    Returns list of newly unlocked achievement IDs.
    """
    user_achievements = db["user_achievements"]
    newly_unlocked = []

    for defn in ACHIEVEMENT_DEFINITIONS:
        if longest_streak >= defn["threshold"]:
            result = user_achievements.update_one(
                {"user_id": username, "achievement_id": defn["id"]},
                {"$setOnInsert": {
                    "user_id": username,
                    "achievement_id": defn["id"],
                    "unlocked_at": datetime.now(timezone.utc).isoformat(),
                }},
                upsert=True,
            )
            if result.upserted_id:
                newly_unlocked.append(defn["id"])

    return newly_unlocked


@achievementsrouter.get("/me")
async def get_my_achievements(user=Depends(get_user), db=Depends(get_db)):
    """Get all achievements for the authenticated user.

    Returns achievement definitions merged with user's unlock state,
    plus current/longest streak for progress display.
    """
    try:
        username = user["username"]

        # Get streak data from user document
        users = db["users"]
        user_doc = users.find_one({"username": username}, {"_id": 0, "password": 0})
        current_streak = user_doc.get("streak", 0) if user_doc else 0
        longest_streak = user_doc.get("longest_streak", 0) if user_doc else 0

        # Validate current streak is still active (not broken)
        if user_doc:
            last_completion = user_doc.get("last_completion")
            if last_completion:
                try:
                    last_date = datetime.strptime(last_completion, "%m/%d/%Y").date()
                    today = datetime.now(timezone.utc).date()
                    if last_date != today and last_date != today - timedelta(days=1):
                        current_streak = 0
                except (ValueError, TypeError):
                    current_streak = 0

        # Get user's unlocked achievements
        user_achievements = db["user_achievements"]
        unlocked_docs = list(user_achievements.find(
            {"user_id": username},
            {"_id": 0}
        ))
        unlocked_map = {doc["achievement_id"]: doc for doc in unlocked_docs}

        # Merge definitions with unlock state
        achievements = []
        for defn in ACHIEVEMENT_DEFINITIONS:
            unlock = unlocked_map.get(defn["id"])
            achievements.append({
                **defn,
                "unlocked": unlock is not None,
                "unlocked_at": unlock["unlocked_at"] if unlock else None,
                "progress": min(current_streak, defn["threshold"]),
                "total": defn["threshold"],
            })

        return {
            "success": True,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "achievements": achievements,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch achievements: {str(e)}")
