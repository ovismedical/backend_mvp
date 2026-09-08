"""
Florence AI Shared Utilities
Shared functionality for Florence conversation system using structured assessment format
"""

from typing import List, Dict, Optional, Any, Literal
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import os


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

def load_florence_system_prompt(language: str = "en") -> str:
    """Load Florence system prompt from prompt file based on language"""
    try:
        # Get the directory where this module is located
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Select prompt file based on language
        if language == "zh-HK":
            prompt_file_path = os.path.join(current_dir, "prompt_canto.txt")
            print(f"🔤 Loading Cantonese prompt from {prompt_file_path}")
        else:
            prompt_file_path = os.path.join(current_dir, "prompt_eng.txt")
            print(f"🔤 Loading English prompt from {prompt_file_path}")
        
        with open(prompt_file_path, 'r', encoding='utf-8') as file:
            prompt = file.read().strip()
            
        if not prompt:
            raise ValueError("Prompt file is empty")
            
        print(f"✅ Successfully loaded Florence system prompt from {prompt_file_path}")
        return prompt
        
    except FileNotFoundError:
        print(f"❌ Prompt file not found at {prompt_file_path}")
        # Fallback prompt
        return "You are Florence, a friendly AI nurse. Have a warm conversation to assess how the patient is feeling today."
    except Exception as e:
        print(f"❌ Error loading prompt file: {e}")
        # Fallback prompt
        return "You are Florence, a friendly AI nurse. Have a warm conversation to assess how the patient is feeling today."

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

def generate_fallback_response(patient_name: str, context: str = "general") -> str:
    """Generate fallback responses when AI is unavailable"""
    error_message = "AI connection difficulty. Please contact the developers. 我們無法連接 AI。請聯繫開發人員。"
    fallback_responses = {
        "welcome": error_message,
        "processing_error": error_message,
        "general_followup": error_message,
        "system_error": error_message
    }
    return fallback_responses.get(context, error_message)

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
    """Standardized error handling for AI responses"""
    print(f"❌ AI Error in {context}: {error}")
    
    return {
        "error": str(error),
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
        "user_info": session_data["user_info"],
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