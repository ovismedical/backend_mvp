#!/usr/bin/env python3
"""Create (or rebuild) the hands-on demo patient: a named persona under drlee with three weeks of history.

The account is written straight into the database with a hashed password, so no OTP is involved.
Re-running wipes the persona's records and rebuilds them; the user document keeps its _id.

Usage (from backend_mvp/):
    python scripts/seed_demo_account.py                    # local MONGODB_URI
    python scripts/seed_demo_account.py --prod             # MONGODB_URI_PROD from .env
    python scripts/seed_demo_account.py --tz Asia/Hong_Kong  # streak dates in the demo browser's zone

The streak is stamped "completed yesterday" in the demo browser's timezone (default: this machine's),
so the first check-in on demo day extends it.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))
if "--prod" in sys.argv:
    prod_uri = os.getenv("MONGODB_URI_PROD")
    if not prod_uri:
        sys.exit("MONGODB_URI_PROD is not set in .env")
    os.environ["MONGODB_URI"] = prod_uri

from app.login import get_db, hash_password  # noqa: E402
from app.achievements import check_and_unlock_achievements  # noqa: E402
from scripts.seed_demo import DOCTOR, florence_record, questionnaire_record, utc  # noqa: E402



def demo_timezone():
    """IANA zone from --tz, else this machine's local zone (the browser the demo runs from)."""
    if "--tz" in sys.argv:
        return ZoneInfo(sys.argv[sys.argv.index("--tz") + 1])
    return datetime.now().astimezone().tzinfo


PATIENT_TZ = demo_timezone()

DEMO = {
    "username": "demo",
    "password": "demo1234",
    "email": "grace.tam@example.com",
    "full_name": "Grace Tam",
    "phone": "+852 9123 4567",
    "dob": "08/22/1966",
    "sex": "female",
    "isDoctor": False,
    "doctor": DOCTOR["username"],
    "treatment_status": "undergoing_treatment",
}
STREAK, LONGEST_STREAK = 5, 12
ACCOUNT_AGE_DAYS = 45

# days_ago -> severities (1..5) for fatigue / nausea / lack_of_appetite / cough / pain.
# Story: settled, a flare nine days ago (ORANGE, same-day review), easing since. Nothing today.
FLORENCE_DAYS = {
    20: dict(fatigue=2, nausea=1, lack_of_appetite=2, cough=1, pain=1),   # GREEN
    18: dict(fatigue=2, nausea=2, lack_of_appetite=2, cough=1, pain=1),   # GREEN
    15: dict(fatigue=3, nausea=2, lack_of_appetite=2, cough=1, pain=2),   # YELLOW
    13: dict(fatigue=2, nausea=2, lack_of_appetite=2, cough=1, pain=1),   # GREEN
    11: dict(fatigue=3, nausea=3, lack_of_appetite=3, cough=1, pain=2),   # YELLOW
    9:  dict(fatigue=4, nausea=4, lack_of_appetite=3, cough=1, pain=2),   # ORANGE
    6:  dict(fatigue=3, nausea=3, lack_of_appetite=3, cough=1, pain=2),   # YELLOW
    4:  dict(fatigue=3, nausea=2, lack_of_appetite=2, cough=1, pain=2),   # YELLOW
    2:  dict(fatigue=2, nausea=2, lack_of_appetite=2, cough=1, pain=1),   # GREEN
    1:  dict(fatigue=2, nausea=1, lack_of_appetite=2, cough=1, pain=1),   # GREEN
}
QUESTIONNAIRE_DAYS = {1: False, 2: False, 3: True, 4: False, 5: False}  # days_ago -> bad_day
RECORD_COLLECTIONS = ("answers", "florence_assessments", "symptom_questionnaires",
                      "questionnaire_drafts", "user_achievements", "assessment_reviews")


def seed_demo_account():
    db = get_db()
    username = DEMO["username"]

    for coll in RECORD_COLLECTIONS:
        db[coll].delete_many({"user_id": username})
    db["user_achievements"].delete_many({"username": username})

    yesterday = (datetime.now(PATIENT_TZ) - timedelta(days=1)).strftime("%m/%d/%Y")
    profile = dict(DEMO)
    profile["password"] = hash_password(profile.pop("password"))
    profile.update({
        "streak": STREAK,
        "longest_streak": LONGEST_STREAK,
        "last_completion": yesterday,
        "created_at": utc(ACCOUNT_AGE_DAYS).isoformat(),
    })
    db["users"].update_one({"username": username}, {"$set": profile}, upsert=True)
    check_and_unlock_achievements(db, username, LONGEST_STREAK)

    doctor = db["doctors"].find_one({"username": DOCTOR["username"]}, {"_id": 1})
    if not doctor:
        sys.exit(f"Doctor {DOCTOR['username']} not found; run seed_demo.py first")
    db["doctors"].update_one({"username": DOCTOR["username"]}, {"$addToSet": {"patients": username}})

    florence_docs = [
        florence_record(username, utc(days_ago, hour=(19 if days_ago % 2 else 8), minute=15),
                        baseline=2.0, in_remission=False, severities=sev)
        for days_ago, sev in sorted(FLORENCE_DAYS.items(), reverse=True)
    ]
    db["florence_assessments"].insert_many(florence_docs)

    questionnaire_docs = [
        questionnaire_record(username, utc(days_ago, hour=8, minute=40), bad_day=bad)
        for days_ago, bad in sorted(QUESTIONNAIRE_DAYS.items(), reverse=True)
    ]
    db["symptom_questionnaires"].insert_many(questionnaire_docs)

    levels = [d["alert_level"] for d in florence_docs]
    print(f"Demo account rebuilt in {db.name}")
    print(f"  Login   : {username} / {DEMO['password']}  ({DEMO['full_name']}, under {DOCTOR['username']})")
    print(f"  Florence: {len(florence_docs)} chats over 21 days -> {', '.join(levels)}")
    print(f"  Check-ins: {len(questionnaire_docs)} (bad day {[d for d, b in QUESTIONNAIRE_DAYS.items() if b]} days ago)")
    print(f"  Streak {STREAK} (longest {LONGEST_STREAK}), last completion {yesterday} {PATIENT_TZ}; nothing logged today")


if __name__ == "__main__":
    seed_demo_account()
