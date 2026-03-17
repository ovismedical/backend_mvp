"""
Test data factories for OVIS backend tests.
Each factory returns a dict with sensible defaults that can be overridden.
"""

from datetime import datetime, timezone, timedelta
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def make_user(overrides=None):
    """Create a test patient user dict (as stored in MongoDB 'users' collection)."""
    user = {
        "username": "testpatient",
        "password": pwd_context.hash("testpass123"),
        "email": "patient@test.com",
        "isDoctor": False,
        "full_name": "Test Patient",
        "birthdate": "01/01/1990",
        "gender": "male",
        "height": 170,
        "weight": 70,
        "bloodtype": "O+",
        "fitness_level": 3,
        "exercises": ["walking"],
        "checkups": "monthly",
        "streak": 0,
        "longest_streak": 0,
        "last_completion": None,
        "doctor": "testdoctor",
    }
    if overrides:
        user.update(overrides)
    return user


def make_doctor(overrides=None):
    """Create a test doctor user dict (as stored in MongoDB 'doctors' collection)."""
    doctor = {
        "username": "testdoctor",
        "password": pwd_context.hash("doctorpass123"),
        "email": "doctor@test.com",
        "isDoctor": True,
        "code": "ABCD",
        "patients": ["testpatient"],
        "full_name": "Dr. Test",
        "hospital": "Test Hospital",
    }
    if overrides:
        doctor.update(overrides)
    return doctor


def make_florence_session(overrides=None):
    """Create an in-memory Florence session dict."""
    session = {
        "session_id": "testpatient_1710000000",
        "user_id": "testpatient",
        "user_info": {"username": "testpatient", "full_name": "Test Patient"},
        "language": "en",
        "input_mode": "keyboard",
        "treatment_status": "undergoing_treatment",
        "status": "active",
        "conversation_history": [
            {
                "role": "assistant",
                "content": "Hello! I'm Florence. How are you feeling today?",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "structured_assessment": None,
        "florence_state": "starting",
        "ai_available": True,
        "oncologist_notification_level": "none",
        "flag_for_oncologist": False,
    }
    if overrides:
        session.update(overrides)
    return session


def make_symptom_data(freq=1, sev=1, indicators=None):
    """Create a single symptom assessment dict."""
    return {
        "frequency_rating": freq,
        "severity_rating": sev,
        "key_indicators": indicators or [],
    }


def make_symptoms(overrides=None):
    """Create a full symptoms dict with all 5 symptom categories."""
    symptoms = {
        "cough": make_symptom_data(),
        "nausea": make_symptom_data(),
        "lack_of_appetite": make_symptom_data(),
        "fatigue": make_symptom_data(),
        "pain": make_symptom_data(),
    }
    if overrides:
        for key, val in overrides.items():
            if key in symptoms:
                symptoms[key].update(val)
            else:
                symptoms[key] = val
    return symptoms


def make_assessment_record(overrides=None):
    """Create a florence_assessments document."""
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "session_id": "testpatient_1710000000",
        "user_id": "testpatient",
        "user_info": {"username": "testpatient", "full_name": "Test Patient"},
        "language": "en",
        "input_mode": "keyboard",
        "conversation_history": [
            {"role": "assistant", "content": "Hello!", "timestamp": now},
            {"role": "user", "content": "I feel tired.", "timestamp": now},
        ],
        "structured_assessment": {
            "timestamp": now,
            "patient_id": "testpatient",
            "symptoms": make_symptoms(),
            "flag_for_oncologist": False,
            "oncologist_notification_level": "none",
            "treatment_status": "undergoing_treatment",
        },
        "triage_assessment": make_triage_result(),
        "alert_level": "GREEN",
        "created_at": now,
        "completed_at": now,
        "assessment_type": "florence_conversation_with_triage",
        "florence_state": "completed",
        "ai_powered": True,
        "oncologist_notification_level": "none",
        "flag_for_oncologist": False,
    }
    if overrides:
        record.update(overrides)
    return record


def make_triage_result(alert_level="GREEN", overrides=None):
    """Create a triage assessment dict."""
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "patient_id": "testpatient",
        "clinical_reasoning": "Patient reports mild symptoms.",
        "diagnosis_predictions": [
            {
                "suspected_diagnosis": "Treatment side effects",
                "probability": "medium",
                "urgency": 2,
                "reasoning": "Common side effects of treatment.",
            }
        ],
        "alert_level": alert_level,
        "alert_rationale": "Symptoms are within expected range.",
        "key_symptoms": ["fatigue"],
        "recommended_timeline": "Routine follow-up",
        "confidence_level": "medium",
        "clinical_notes": "",
        "treatment_status": "undergoing_treatment",
    }
    if overrides:
        result.update(overrides)
    return result


def make_conversation_history(turns=3):
    """Create a realistic conversation history list."""
    messages = [
        {
            "role": "assistant",
            "content": "Hello! I'm Florence. How are you feeling today?",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    ]
    user_msgs = [
        "I've been feeling quite tired lately.",
        "Maybe 3 out of 5. It's been affecting my daily activities.",
        "I also have a slight cough but nothing too bad.",
    ]
    assistant_msgs = [
        "I'm sorry to hear that. Can you tell me more about your fatigue? On a scale of 1-5, how would you rate it?",
        "Thank you for sharing. Have you noticed any other symptoms?",
        "I understand. Let me note that down. Is there anything else you'd like to discuss?",
    ]
    for i in range(min(turns, len(user_msgs))):
        messages.append(
            {
                "role": "user",
                "content": user_msgs[i],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": assistant_msgs[i],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    return messages


def make_questionnaire_answers(overrides=None):
    """Create raw questionnaire answers dict (matching questionnaire_definitions SECTIONS)."""
    answers = {
        "appetite_rating": 3,
        "cough_frequency": "1-2",
        "cough_severity": 2,
        "fatigue_rating": 3,
        "nausea_frequency": "none",
        "pain_locations": [],
        "sleep_hours": 7,
        "mood_rating": 3,
    }
    if overrides:
        answers.update(overrides)
    return answers
