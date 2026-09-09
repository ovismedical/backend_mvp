#!/usr/bin/env python3
"""Seed a self-contained demo dataset: one hospital, one doctor, one patient with weeks of history.

Usage (from backend_mvp/):  python scripts/seed_demo.py
Re-running wipes and recreates the demo accounts and their data.
"""

import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.login import get_db, hash_password  # noqa: E402
from app.achievements import check_and_unlock_achievements  # noqa: E402
from app.questionnaire_enrichment import enrich_submission  # noqa: E402
from app.questionnaire_definitions import SECTIONS  # noqa: E402

HOSPITAL = {"name": "Ovis Demo Hospital", "code": "HOSP"}
DOCTOR = {
    "username": "drlee",
    "password": "demo1234",
    "email": "amanda.lee@ovis-demo.health",
    "full_name": "Dr. Amanda Lee",
    "specialty": "Medical Oncology",
    "isDoctor": True,
    "hospital": HOSPITAL["name"],
    "code": "OVIS",
}
PATIENT = {
    "username": "alex",
    "password": "demo1234",
    "email": "alex.chan@example.com",
    "full_name": "Alex Chan",
    "dob": "03/14/1968",
    "sex": "female",
    "isDoctor": False,
    "doctor": DOCTOR["username"],
    "treatment_status": "undergoing_treatment",
}
SECOND_PATIENT = {
    "username": "jordan",
    "password": "demo1234",
    "email": "jordan.wu@example.com",
    "full_name": "Jordan Wu",
    "dob": "11/02/1975",
    "sex": "male",
    "isDoctor": False,
    "doctor": DOCTOR["username"],
    "treatment_status": "in_remission",
}

SYMPTOMS = ["fatigue", "nausea", "lack_of_appetite", "cough", "pain"]
ALERT_BY_MAX_SEVERITY = {1: "GREEN", 2: "GREEN", 3: "YELLOW", 4: "ORANGE", 5: "RED"}
NOTIFICATION_BY_ALERT = {"GREEN": "none", "YELLOW": "amber", "ORANGE": "amber", "RED": "red"}

rng = random.Random(42)


def utc(days_ago: int, hour: int = 9, minute: int = 0) -> datetime:
    base = datetime.now(timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return base - timedelta(days=days_ago)


def florence_record(username: str, created: datetime, baseline: float, in_remission: bool,
                    severities: dict | None = None) -> dict:
    """One completed Florence session. Severities are drawn around `baseline` unless given explicitly
    (a {symptom: 1..5} mapping), which lets a seed script script a specific day."""
    symptoms = {}
    for name in SYMPTOMS:
        if severities and name in severities:
            sev = max(1, min(5, int(severities[name])))
        else:
            sev = max(1, min(5, round(rng.gauss(baseline, 0.9))))
        freq = max(1, min(5, sev + rng.choice([-1, 0, 0, 1])))
        symptoms[name] = {
            "frequency_rating": freq,
            "severity_rating": sev,
            "key_indicators": [f"Patient described {name.replace('_', ' ')} as {sev}/5 today"],
            "additional_notes": "",
        }
    worst = max(symptoms.items(), key=lambda kv: kv[1]["severity_rating"])
    max_sev = worst[1]["severity_rating"]
    alert = ALERT_BY_MAX_SEVERITY[max_sev]
    notification = NOTIFICATION_BY_ALERT[alert]
    flag = alert in ("ORANGE", "RED")
    worst_name = worst[0].replace("_", " ")

    conversation = [
        {"role": "assistant", "content": f"Hello {username.title()}, it's Florence. How are you feeling today?",
         "timestamp": created.isoformat()},
        {"role": "user", "content": f"Mostly okay, but my {worst_name} has been around {max_sev} out of 5.",
         "timestamp": (created + timedelta(minutes=1)).isoformat()},
        {"role": "assistant", "content": "Thank you for telling me. Has it changed compared to yesterday?",
         "timestamp": (created + timedelta(minutes=2)).isoformat()},
        {"role": "user", "content": "About the same, maybe slightly better in the afternoon.",
         "timestamp": (created + timedelta(minutes=3)).isoformat()},
    ]

    triage = {
        "timestamp": created.isoformat(),
        "patient_id": username,
        "clinical_reasoning": f"Predominant complaint is {worst_name} rated {max_sev}/5 with otherwise stable pattern.",
        "diagnosis_predictions": [
            {
                "suspected_diagnosis": f"Treatment-related {worst_name}",
                "probability": "high" if max_sev >= 4 else "medium",
                "urgency": min(5, max(1, max_sev)),
                "reasoning": "Consistent with expected therapy side-effects; monitor trend.",
            }
        ],
        "alert_level": alert,
        "alert_rationale": f"Highest urgency driven by {worst_name} severity {max_sev}/5.",
        "key_symptoms": [worst_name],
        "recommended_timeline": {"GREEN": "Routine follow-up", "YELLOW": "Review within the week",
                                 "ORANGE": "Same-day clinical review", "RED": "Immediate attention"}[alert],
        "confidence_level": "medium",
        "clinical_notes": "",
        "treatment_status": "in_remission" if in_remission else "undergoing_treatment",
    }

    return {
        "session_id": f"{username}_{int(created.timestamp())}",
        "user_id": username,
        "user_info": {"username": username},
        "language": "en",
        "input_mode": "keyboard",
        "conversation_history": conversation,
        "structured_assessment": {
            "timestamp": created.isoformat(),
            "patient_id": username,
            "symptoms": symptoms,
            "flag_for_oncologist": flag,
            "flag_reason": f"{worst_name} severity {max_sev}/5" if flag else None,
            "mood_assessment": rng.choice(["Calm and cooperative", "Tired but positive", "A little anxious"]),
            "conversation_notes": f"Main concern today: {worst_name}.",
            "oncologist_notification_level": notification,
            "treatment_status": triage["treatment_status"],
        },
        "triage_assessment": triage,
        "alert_level": alert,
        "created_at": created.isoformat(),
        "completed_at": (created + timedelta(minutes=6)).isoformat(),
        "assessment_type": "florence_conversation_with_triage",
        "florence_state": "completed",
        "ai_powered": True,
        "oncologist_notification_level": notification,
        "flag_for_oncologist": flag,
    }


def questionnaire_record(username: str, submitted: datetime, bad_day: bool) -> dict:
    appetite = rng.choice([4, 5]) if bad_day else rng.choice([1, 2, 3])
    fatigue = rng.choice([3, 4]) if bad_day else rng.choice([0, 1, 2])
    nausea = rng.choice([2, 3]) if bad_day else rng.choice([0, 1])
    answers = {
        "appetite_rating": appetite,
        "bowel_frequency": rng.choice(["1-2", "3-4"]),
        "bowel_description": ["normal"],
        "cough_frequency": rng.choice(["none", "1-2"]),
        "dyspnea_frequency": "none",
        "dyspnea_pain": 0,
        "dysuria_frequency": "none",
        "dysuria_severity": 1,
        "sleep_quality": rng.choice([3, 4]) if bad_day else rng.choice([1, 2, 3]),
        "fatigue_level": fatigue,
        "nausea_level": nausea,
        "vomiting_episodes": "none",
        "hot_flash_frequency": "none",
        "hot_flash_intensity": 1,
        "vaginal_discharge": "no",
        "vaginal_discomfort": 0,
        "headache_frequency": rng.choice(["none", "1-2"]),
        "headache_severity": 1,
        "joint_pain_areas": ["left_knee", "right_knee"] if bad_day else [],
        "muscle_pain_areas": ["left_lower_back"] if bad_day else [],
    }
    if appetite >= 4:
        answers["appetite_causes"] = ["taste", "fullness"]
    if fatigue >= 2:
        answers["fatigue_interference"] = max(0, fatigue - 1)
    if answers["sleep_quality"] >= 4:
        answers["sleep_hours"] = 4.5
        answers["sleep_symptoms"] = ["exhausted"]

    enriched = enrich_submission(
        raw_answers=answers,
        sections_completed=len(SECTIONS),
        total_sections=len(SECTIONS),
        completion_percentage=100,
        submission_mode="full",
    )
    enriched.update({
        "user_id": username,
        "submitted_at": submitted,
        "date": submitted.strftime("%Y-%m-%d"),
        "timestamp": submitted.isoformat(),
        "sections_completed": len(SECTIONS),
        "total_sections": len(SECTIONS),
        "completion_percentage": 100,
        "answers": answers,
        "triage_status": "completed",
        "alert_level": "YELLOW" if bad_day else "GREEN",
    })
    return enriched


def seed():
    db = get_db()
    usernames = [PATIENT["username"], SECOND_PATIENT["username"]]

    db["hospitals"].delete_many({"code": HOSPITAL["code"]})
    db["doctors"].delete_many({"username": DOCTOR["username"]})
    db["users"].delete_many({"username": {"$in": usernames}})
    for coll in ("answers", "florence_assessments", "symptom_questionnaires", "questionnaire_drafts", "user_achievements"):
        db[coll].delete_many({"user_id": {"$in": usernames}})

    db["hospitals"].insert_one(dict(HOSPITAL))

    doctor = dict(DOCTOR)
    doctor["password"] = hash_password(doctor["password"])
    doctor["patients"] = usernames
    db["doctors"].insert_one(doctor)

    today = datetime.now(timezone.utc).strftime("%m/%d/%Y")
    for patient_def, streak, longest in ((PATIENT, 6, 9), (SECOND_PATIENT, 2, 4)):
        patient = dict(patient_def)
        patient["password"] = hash_password(patient["password"])
        patient.update({"streak": streak, "longest_streak": longest, "last_completion": today,
                        "created_at": utc(60).isoformat()})
        db["users"].insert_one(patient)
        check_and_unlock_achievements(db, patient["username"], longest)

    florence_docs = []
    for days_ago in range(0, 42):
        if days_ago % 7 in (1, 3, 5):
            continue
        baseline = 3.4 if days_ago < 7 else 2.6 if days_ago < 21 else 2.2
        florence_docs.append(florence_record(PATIENT["username"], utc(days_ago, hour=rng.choice([8, 12, 19])),
                                             baseline, in_remission=False))
    for days_ago in (0, 2, 5, 9, 14, 20, 27):
        florence_docs.append(florence_record(SECOND_PATIENT["username"], utc(days_ago, hour=10), 1.8, in_remission=True))
    db["florence_assessments"].insert_many(florence_docs)

    questionnaire_docs = [
        questionnaire_record(PATIENT["username"], utc(days_ago, hour=8, minute=30), bad_day=days_ago in (0, 4))
        for days_ago in range(0, 7)
    ]
    questionnaire_docs.append(questionnaire_record(SECOND_PATIENT["username"], utc(0, hour=9), bad_day=False))
    questionnaire_docs.append(questionnaire_record(SECOND_PATIENT["username"], utc(1, hour=9), bad_day=False))
    db["symptom_questionnaires"].insert_many(questionnaire_docs)

    print("Demo data seeded into", db.name)
    print(f"  Hospital access code (doctor sign-up): {HOSPITAL['code']}")
    print(f"  Doctor  : {DOCTOR['username']} / {DOCTOR['password']}   (patient sign-up code: {DOCTOR['code']})")
    print(f"  Patient : {PATIENT['username']} / {PATIENT['password']}")
    print(f"  Patient : {SECOND_PATIENT['username']} / {SECOND_PATIENT['password']}")
    print(f"  {len(florence_docs)} Florence assessments, {len(questionnaire_docs)} questionnaires")


if __name__ == "__main__":
    seed()
