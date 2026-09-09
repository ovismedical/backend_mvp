"""
Florence AI Shared Utilities
Shared functionality for Florence conversation system using structured assessment format
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Literal, Optional

from pydantic import BaseModel, Field

from .inference.refs import patient_ref as _patient_ref
from .inference.scrub import KnownIdentifiers, ScrubContext, ScrubError, TokenMap, ner_backend_from_env
from .inference.scrub.tokens import ALL_CLASSES, LINKAGE_CLASSES

logger = logging.getLogger("ovis.florence")


# ---------------------------------------------------------------------------
# Structured-output schemas (OpenAI Responses API `text_format`).
# Every field is required (Optional = nullable) as strict JSON schema demands.
# ---------------------------------------------------------------------------
class SymptomRating(BaseModel):
    frequency_rating: int = Field(ge=1, le=5, description="How often the symptom occurs, 1 (rare) to 5 (constant)")
    severity_rating: int = Field(ge=1, le=5, description="How severe the symptom is, 1 (minimal) to 5 (severe)")
    key_indicators: List[str] = Field(description="Direct patient quotes or observations supporting the ratings")
    additional_notes: Optional[str] = Field(description="Relevant context, or null")


class PainRating(SymptomRating):
    location: Optional[str] = Field(description="Where the pain is located, or null if not mentioned")


class SymptomSet(BaseModel):
    cough: SymptomRating
    nausea: SymptomRating
    lack_of_appetite: SymptomRating
    fatigue: SymptomRating
    pain: PainRating


class SymptomAssessmentOutput(BaseModel):
    symptoms: SymptomSet
    flag_for_oncologist: bool
    flag_reason: Optional[str]
    mood_assessment: Optional[str] = Field(description="Brief assessment of the patient's mood and outlook")
    conversation_notes: Optional[str] = Field(description="Clinically relevant notes from the conversation")
    oncologist_notification_level: Literal["none", "amber", "red"]
    treatment_status: Literal["undergoing_treatment", "in_remission"]


class DiagnosisPrediction(BaseModel):
    suspected_diagnosis: str
    probability: Literal["low", "medium", "high"]
    urgency: int = Field(ge=1, le=5, description="1=routine monitoring, 2=scheduled follow-up, 3=same-week review, 4=same-day attention, 5=immediate emergency care")
    reasoning: str


class TriageAssessmentOutput(BaseModel):
    clinical_reasoning: str = Field(description="Step-by-step reasoning: key symptoms, pattern recognition, differentials, risk stratification, treatment context")
    diagnosis_predictions: List[DiagnosisPrediction]
    alert_level: Literal["GREEN", "YELLOW", "ORANGE", "RED"] = Field(description="Overall urgency based on the highest-urgency diagnosis")
    alert_rationale: str
    key_symptoms: List[str]
    recommended_timeline: str = Field(description="Specific recommended timeline for medical review")
    confidence_level: Literal["low", "medium", "high"]
    clinical_notes: Optional[str]
    treatment_status: Literal["undergoing_treatment", "in_remission"]


# Constants
TARGET_SYMPTOMS = {"fatigue", "lack_of_appetite", "nausea", "cough", "pain"}
PAIN_KEYWORDS = ["pain", "hurt", "ache", "sore", "discomfort"]

FALLBACK_SYSTEM_PROMPT = (
    "You are Florence, a friendly AI nurse. Have a warm conversation to assess how the patient is feeling today. "
    "The patient appears as [PERSON_1]; write placeholders exactly as given and never ask for real names, "
    "addresses, ID numbers, phone numbers or exact dates."
)


def load_florence_system_prompt(language: str = "en") -> str:
    """Load the Florence system prompt for `language` from the prompt files next to this module."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    filename = "prompt_canto.txt" if language == "zh-HK" else "prompt_eng.txt"
    prompt_file_path = os.path.join(current_dir, filename)
    try:
        with open(prompt_file_path, "r", encoding="utf-8") as file:
            prompt = file.read().strip()
        if not prompt:
            raise ValueError("Prompt file is empty")
        logger.debug("loaded Florence system prompt %s", filename)
        return prompt
    except FileNotFoundError:
        logger.warning("Florence prompt file %s not found; using the built-in fallback prompt", filename)
        return FALLBACK_SYSTEM_PROMPT
    except Exception as e:
        logger.warning("Florence prompt file %s unusable (%s); using the built-in fallback prompt", filename, type(e).__name__)
        return FALLBACK_SYSTEM_PROMPT

def create_timestamp() -> str:
    """Create a standardized timestamp string"""
    return datetime.now(timezone.utc).isoformat()

def create_conversation_message(role: str, content: str, include_timestamp: bool = True) -> Dict[str, str]:
    """Create a standardized conversation message"""
    message = {
        "role": role,
        "content": content
    }
    if include_timestamp:
        message["timestamp"] = create_timestamp()
    return message

CONNECTION_ERROR_MESSAGE = "AI connection difficulty. Please contact the developers. 我們無法連接 AI。請聯繫開發人員。"

# Shown when the routing policy refuses the model call (or the scrubber fails closed):
# the chat stays open, nothing is lost, and the care team will read the transcript.
REFUSED_MESSAGES = {
    "en": (
        "Thank you for checking in. Florence can't run the AI conversation right now, but everything you "
        "share here is saved for your care team to read. Please carry on telling me how you've been feeling, "
        "or come back later."
    ),
    "zh-HK": (
        "多謝你今日嘅分享。Florence 暫時未能進行 AI 對話，不過你喺呢度講嘅一切都會儲存俾你嘅醫療團隊查閱。"
        "你可以繼續講講最近身體點樣，或者遲啲再返嚟。"
    ),
}


def generate_fallback_response(patient_name: str, context: str = "general", language: Optional[str] = None) -> str:
    """Scripted replies when the model cannot answer.

    `context="refused"` is the friendly text for a policy refusal / scrubber failure, in the session
    language (`en`, `zh-HK`; anything else or None -> bilingual). Every other context is the
    provider-connectivity message.
    """
    if context == "refused":
        if language in REFUSED_MESSAGES:
            return REFUSED_MESSAGES[language]
        return f"{REFUSED_MESSAGES['en']}\n\n{REFUSED_MESSAGES['zh-HK']}"
    return CONNECTION_ERROR_MESSAGE

def get_localized_message(message_key: str, language: str = "en") -> str:
    """Get localized message based on language setting"""
    messages = {
        "session_not_found": {
            "en": "Session not found",
            "zh-HK": "找不到會話"
        },
        "access_denied": {
            "en": "Access denied",
            "zh-HK": "拒絕訪問"
        },
        "session_expired": {
            "en": "Session has expired",
            "zh-HK": "會話已過期"
        },
        "failed_to_save_assessment": {
            "en": "Failed to save assessment",
            "zh-HK": "保存評估失敗"
        },
        "session_completed": {
            "en": "Session completed successfully",
            "zh-HK": "會話已成功完成"
        },
        "session_not_active": {
            "en": "Session is not active",
            "zh-HK": "會話未處於活動狀態"
        }
    }
    
    lang_key = "zh-HK" if language == "zh-HK" else "en"
    if message_key in messages:
        return messages[message_key].get(lang_key, messages[message_key]["en"])
    return message_key  # Return the key if not found

# Note: Text-based symptom detection removed as it was unreliable.
# The AI now uses structured assessment for accurate symptom tracking.

# Note: Functions for conversation state tracking removed as they were based on
# unreliable keyword matching. The AI now handles conversation flow naturally.

def should_flag_symptoms(symptoms: Dict[str, Dict], treatment_status: str) -> tuple:
    """
    Determine if symptoms should be flagged based on the OnCallLogist criteria
    
    Args:
        symptoms: Dict containing symptom assessments
        treatment_status: String "undergoing_treatment" or "in_remission"
        
    Returns:
        tuple: (flag_boolean, notification_level, reason)
    """
    # Logic for patients undergoing treatment
    if treatment_status == "undergoing_treatment":
        # Check for severe symptoms
        for symptom_name, symptom_data in symptoms.items():
            freq = symptom_data.get("frequency_rating", 1)
            sev = symptom_data.get("severity_rating", 1)
            
            # Severe symptoms criteria: occur at least five times per day or are rated three or above
            if freq >= 5 or sev >= 3:
                return (True, "amber", f"Severe {symptom_name} - high frequency ({freq}) or severity ({sev})")
            
            # OR occur at least three times per day and an increase in severity
            if freq >= 3 and sev >= 3:
                return (True, "amber", f"Significant {symptom_name} - frequent and severe")
    
    # Logic for patients in remission
    elif treatment_status == "in_remission":
        # Check for severe symptoms
        for symptom_name, symptom_data in symptoms.items():
            freq = symptom_data.get("frequency_rating", 1)
            sev = symptom_data.get("severity_rating", 1)
            
            # Severe symptoms: occur at least seven times per day or are rated four or above
            if freq >= 4 or sev >= 4:
                return (True, "amber", f"Severe {symptom_name} in remission patient - high frequency ({freq}) or severity ({sev})")
            
            # OR occur at least three times per day and high severity
            if freq >= 3 and sev >= 4:
                return (True, "amber", f"Significant {symptom_name} in remission patient")
    
    # Default - no flagging needed
    return (False, "none", "")

def format_conversation_history_for_ai(history: List[Dict], include_system_prompt: bool = True, system_prompt: str = None) -> List[Dict]:
    """Format conversation history for AI API calls"""
    # Remove timestamps for AI processing
    ai_history = []
    
    if include_system_prompt and system_prompt:
        ai_history.append({"role": "system", "content": system_prompt})
    
    for message in history:
        ai_message = {
            "role": message["role"],
            "content": message["content"]
        }
        # Skip system messages if we're adding our own
        if not (include_system_prompt and message["role"] == "system"):
            ai_history.append(ai_message)
    
    return ai_history

def handle_ai_response_error(error: Exception, context: str = "general", patient_name: str = "there") -> Dict[str, Any]:
    """Standardized error handling for AI responses. Logs the exception type only - never its message."""
    logger.error("AI error in %s: %s", context, type(error).__name__)

    return {
        "error": type(error).__name__,
        "response": generate_fallback_response(patient_name, "processing_error"),
        "conversation_state": "starting",

        "progress": 0.0,
        "is_complete": False
    }

def validate_session_access(session: Dict, user_id: str) -> bool:
    """Validate if user has access to session"""
    return session.get("user_id") == user_id

def is_ai_available() -> bool:
    """Check if AI functionality is available"""
    return os.getenv("OPENAI_API_KEY") is not None

def create_assessment_record(session_data: Dict, structured_assessment: Optional[Dict] = None, triage_assessment: Optional[Dict] = None) -> Dict[str, Any]:
    """Create standardized assessment record for database storage using the structured format with triage data"""
    
    # Extract alert level from triage assessment
    alert_level = "UNKNOWN"
    if triage_assessment:
        alert_level = triage_assessment.get("alert_level", "UNKNOWN")
    
    # Determine overall oncologist notification level (use highest priority from assessment or triage)
    oncologist_notification = "none"
    flag_for_oncologist = False
    
    if structured_assessment:
        oncologist_notification = structured_assessment.get("oncologist_notification_level", "none")
        flag_for_oncologist = structured_assessment.get("flag_for_oncologist", False)
    
    # Triage alert levels can override assessment notification levels
    if triage_assessment:
        triage_alert = triage_assessment.get("alert_level", "UNKNOWN")
        if triage_alert in ["RED", "ORANGE"]:
            flag_for_oncologist = True
            oncologist_notification = "red" if triage_alert == "RED" else "amber"
        elif triage_alert == "YELLOW" and oncologist_notification == "none":
            oncologist_notification = "amber"
    
    return {
        "session_id": session_data["session_id"],
        "user_id": session_data["user_id"],
        # Opaque reference for audit joins; the display name is NOT copied onto the record any more
        # (the doctor UI reads names from /doctor/patients/details).
        "patient_ref": session_data.get("patient_ref") or _patient_ref(session_data["user_id"]),
        "language": session_data.get("language", "en"),
        "input_mode": session_data.get("input_mode", "keyboard"),
        "conversation_history": session_data["conversation_history"],
        "structured_assessment": structured_assessment,  # Symptom assessment
        "triage_assessment": triage_assessment,  # Clinical triage assessment
        "alert_level": alert_level,  # Triage alert level
        "created_at": session_data["created_at"],
        "completed_at": session_data.get("completed_at", create_timestamp()),
        "assessment_type": "florence_conversation_with_triage",  # Updated type
        "florence_state": session_data.get("florence_state", "completed"),
        "ai_powered": session_data.get("ai_available", False),
        "oncologist_notification_level": oncologist_notification,
        "flag_for_oncologist": flag_for_oncologist
    }

def create_session_response_data(session_data: Dict) -> Dict[str, Any]:
    """Create standardized session response data"""
    return {
        "session_id": session_data["session_id"],
        "status": session_data["status"],
        "conversation_history": session_data["conversation_history"],
        "structured_assessment": session_data.get("structured_assessment"),
        "created_at": session_data["created_at"],
        "florence_state": session_data.get("florence_state", "starting"),

        "ai_available": session_data.get("ai_available", False),
        "oncologist_notification_level": session_data.get("oncologist_notification_level", "none"),
        "flag_for_oncologist": session_data.get("flag_for_oncologist", False)
    } 

# ---------------------------------------------------------------------------
# De-identification helpers shared by the Florence chat and the questionnaire bridge.
# The per-session ScrubContext is built from the patient's own record (plus their
# doctor's name/hospital) and its TokenMap is persisted on the session document.
# ---------------------------------------------------------------------------
SCRUB_TZ = "Asia/Hong_Kong"
OPENING_TURN = "Hello, I'm [PERSON_1]. I'm here for my health check-in."
SCRUB_FAILED_REASON = "scrub_failed"

_ner_cache: Dict[str, Any] = {}


def _ner_backend():
    """One NER backend per process and env value (a Presidio engine is expensive to build)."""
    choice = (os.getenv("SCRUB_NER_BACKEND") or "none").strip().lower()
    if choice not in _ner_cache:
        _ner_cache[choice] = ner_backend_from_env()   # may raise ScrubError -> caller refuses
    return _ner_cache[choice]


def lookup_doctor_identity(db, user: Dict) -> Dict[str, Any]:
    """The treating clinician's name and hospital (they appear in transcripts as 'Dr X' / 'at Y')."""
    doctor_username = (user or {}).get("doctor")
    if not doctor_username:
        return {}
    try:
        doctor = db["doctors"].find_one({"username": doctor_username}, {"full_name": 1, "hospital": 1})
    except Exception as e:  # noqa: BLE001 - a DB hiccup must not block the chat
        logger.warning("doctor lookup failed for scrub context: %s", type(e).__name__)
        return {}
    return doctor or {}


def dob_day_month_forms(dob) -> List[str]:
    """The DOB's day/month without the year ("01/01", "1/1", "01.01"): a defence-in-depth catch for
    the numeric date forms the generalisation layer misses (e.g. a sentence-final "01/01/1990." -
    app/inference/scrub/generalise.py SLASH_RE/DOT_RE refuse a trailing dot), so a partial DOB never
    goes out in clear. Scrubbed as a whole token; the full date still wins when it is recognised."""
    if dob is None:
        return []
    forms = [
        dob.strftime("%m/%d"), dob.strftime("%d/%m"), f"{dob.month}/{dob.day}", f"{dob.day}/{dob.month}",
        dob.strftime("%d.%m"), dob.strftime("%m.%d"),
    ]
    return list(dict.fromkeys(forms))


def build_known_identifiers(db, user: Dict) -> KnownIdentifiers:
    """Layer-1 identifiers for the scrubber: the patient's profile plus their doctor and hospital.
    `dob` comes from `dob` or the legacy `birthdate` field (parsed by KnownIdentifiers)."""
    doctor = lookup_doctor_identity(db, user)
    known = KnownIdentifiers(
        full_name=user.get("full_name"),
        username=user["username"],
        email=user.get("email"),
        phone=user.get("phone"),
        dob=user.get("dob") or user.get("birthdate"),
        doctor_name=doctor.get("full_name"),
        hospital=doctor.get("hospital"),
    )
    known.extra = list(known.extra) + dob_day_month_forms(known.dob)
    return known


def build_scrub_context(db, user: Dict, token_map_doc: Optional[Dict] = None) -> ScrubContext:
    """A ScrubContext for this patient; `token_map_doc` is the session's persisted `token_map`
    (None starts a fresh map). Raises ScrubError when the scrubber cannot be set up (fail closed):
    the callers turn it into a refusal, never a 500, and the error carries no input text."""
    try:
        return ScrubContext(
            known=build_known_identifiers(db, user),
            token_map=TokenMap.from_dict(token_map_doc),
            tz=SCRUB_TZ,
            ner=_ner_backend(),
        )
    except ScrubError:
        raise
    except Exception as e:  # noqa: BLE001 - e.g. a malformed persisted token map
        raise ScrubError("context", type(e).__name__) from None


def model_messages(history: Iterable[Dict]) -> List[Dict[str, str]]:
    """Conversation history as role/content pairs for the model: timestamps and system turns dropped."""
    return [m for m in format_conversation_history_for_ai(list(history), include_system_prompt=False)
            if m.get("role") != "system"]


_TOKEN_IN_TEXT_RE = re.compile(
    r"[\[［【「〔]\s*(" + "|".join(ALL_CLASSES) + r")(?:[\s_\-]*\d+)?\s*(?:·[^\]］】」〕]*)?\s*[\]］】」〕]"
)


def scrub_summary_from_messages(messages: Iterable[Dict], ner_backend: str = "none") -> Dict[str, Any]:
    """Content-free summary of what is on the wire: placeholder counts per class and the
    linkage score (distinct indirect-identifier classes present). Feeds the audit event."""
    counts: Dict[str, int] = {}
    for m in messages or []:
        content = m.get("content") if isinstance(m, dict) else m
        if not isinstance(content, str):
            continue
        for match in _TOKEN_IN_TEXT_RE.finditer(content):
            cls = match.group(1).upper()
            counts[cls] = counts.get(cls, 0) + 1
    return {
        "counts": counts,
        "linkage_score": len(set(counts) & LINKAGE_CLASSES),
        "ner_backend": ner_backend,
    }


def pending_review_fields(refusal_reason: str) -> Dict[str, Any]:
    """Record fields for an assessment the policy refused: saved for a clinician, no AI output."""
    return {
        "structured_assessment": None,
        "triage_assessment": None,
        "alert_level": "PENDING_REVIEW",
        "oncologist_notification_level": "none",
        "flag_for_oncologist": False,
        "triage_status": "pending_clinician_review",
        "refusal_reason": refusal_reason,
        "analysed_at": create_timestamp(),
    }
