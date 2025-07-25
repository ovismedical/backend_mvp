"""
Florence AI Shared Utilities
Shared functionality for Florence conversation system using structured assessment format
"""

from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from pydantic import BaseModel
import os

# Shared data models based on telenurse/gpt_json.py format
class SymptomAssessment(BaseModel):
    frequency_rating: int  # 1-5 scale
    severity_rating: int   # 1-5 scale
    key_indicators: List[str]  # Patient quotes or observations
    additional_notes: Optional[str] = None
    location: Optional[str] = None  # For pain symptoms

class StructuredAssessment(BaseModel):
    timestamp: str
    patient_id: str
    symptoms: Dict[str, SymptomAssessment]  # cough, nausea, lack_of_appetite, fatigue, pain
    flag_for_oncologist: bool
    flag_reason: Optional[str] = None
    mood_assessment: Optional[str] = None
    conversation_notes: Optional[str] = None
    oncologist_notification_level: str  # "none", "amber", "red"
    treatment_status: str  # "undergoing_treatment", "in_remission"

class ConversationMessage(BaseModel):
    role: str  # "user", "assistant", "system"
    content: str
    timestamp: Optional[str] = None

class FlorenceResponse(BaseModel):
    response: str
    conversation_state: str = "starting"
    progress: float = 0.0
    is_complete: bool = False
    error: Optional[str] = None

class SessionState(BaseModel):
    session_id: str
    user_id: str
    status: str = "active"
    conversation_state: str = "starting"
    ai_available: bool = False
    created_at: str
    completed_at: Optional[str] = None

# Constants
TARGET_SYMPTOMS = {"fatigue", "lack_of_appetite", "nausea", "cough", "pain"}
PAIN_KEYWORDS = ["pain", "hurt", "ache", "sore", "discomfort"]

# Assessment function schema for OpenAI function calling
ASSESSMENT_FUNCTION_SCHEMA = {
    "name": "record_symptom_assessment",
    "description": "Record a comprehensive symptom assessment for a cancer patient based on conversation",
    "parameters": {
        "type": "object",
        "properties": {
            "timestamp": {
                "type": "string",
                "description": "Current date and time of the assessment"
            },
            "patient_id": {
                "type": "string",
                "description": "Unique identifier for the patient"
            },
            "symptoms": {
                "type": "object",
                "properties": {
                    "cough": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "key_indicators": {"type": "array", "items": {"type": "string"}},
                            "additional_notes": {"type": "string"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "nausea": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "key_indicators": {"type": "array", "items": {"type": "string"}},
                            "additional_notes": {"type": "string"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "lack_of_appetite": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "key_indicators": {"type": "array", "items": {"type": "string"}},
                            "additional_notes": {"type": "string"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "fatigue": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "key_indicators": {"type": "array", "items": {"type": "string"}},
                            "additional_notes": {"type": "string"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "pain": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5},
                            "location": {"type": "string"},
                            "key_indicators": {"type": "array", "items": {"type": "string"}},
                            "additional_notes": {"type": "string"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    }
                },
                "required": ["cough", "nausea", "lack_of_appetite", "fatigue", "pain"]
            },
            "flag_for_oncologist": {"type": "boolean"},
            "flag_reason": {"type": "string"},
            "mood_assessment": {"type": "string"},
            "conversation_notes": {"type": "string"},
            "oncologist_notification_level": {
                "type": "string",
                "enum": ["none", "amber", "red"]
            },
            "treatment_status": {
                "type": "string",
                "enum": ["undergoing_treatment", "in_remission"]
            }
        },
        "required": ["timestamp", "patient_id", "symptoms", "flag_for_oncologist", "oncologist_notification_level", "treatment_status"]
    }
}

# Cantonese version of the assessment function schema
ASSESSMENT_FUNCTION_SCHEMA_ZH = {
    "name": "record_symptom_assessment",
    "description": "根據對話為癌症患者記錄全面的症狀評估",
    "parameters": {
        "type": "object",
        "properties": {
            "timestamp": {
                "type": "string",
                "description": "評估的當前日期和時間"
            },
            "patient_id": {
                "type": "string",
                "description": "病人的唯一標識符"
            },
            "symptoms": {
                "type": "object",
                "properties": {
                    "cough": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "咳嗽頻率評級（1-5）"},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "咳嗽嚴重程度評級（1-5）"},
                            "key_indicators": {"type": "array", "items": {"type": "string"}, "description": "病人的關鍵指標和引述"},
                            "additional_notes": {"type": "string", "description": "額外註記"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "nausea": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "噁心頻率評級（1-5）"},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "噁心嚴重程度評級（1-5）"},
                            "key_indicators": {"type": "array", "items": {"type": "string"}, "description": "病人的關鍵指標和引述"},
                            "additional_notes": {"type": "string", "description": "額外註記"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "lack_of_appetite": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "食慾不振頻率評級（1-5）"},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "食慾不振嚴重程度評級（1-5）"},
                            "key_indicators": {"type": "array", "items": {"type": "string"}, "description": "病人的關鍵指標和引述"},
                            "additional_notes": {"type": "string", "description": "額外註記"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "fatigue": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "疲勞頻率評級（1-5）"},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "疲勞嚴重程度評級（1-5）"},
                            "key_indicators": {"type": "array", "items": {"type": "string"}, "description": "病人的關鍵指標和引述"},
                            "additional_notes": {"type": "string", "description": "額外註記"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "pain": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "疼痛頻率評級（1-5）"},
                            "severity_rating": {"type": "integer", "minimum": 1, "maximum": 5, "description": "疼痛嚴重程度評級（1-5）"},
                            "location": {"type": "string", "description": "疼痛位置"},
                            "key_indicators": {"type": "array", "items": {"type": "string"}, "description": "病人的關鍵指標和引述"},
                            "additional_notes": {"type": "string", "description": "額外註記"}
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    }
                },
                "required": ["cough", "nausea", "lack_of_appetite", "fatigue", "pain"]
            },
            "flag_for_oncologist": {"type": "boolean", "description": "是否需要通知腫瘤科醫生"},
            "flag_reason": {"type": "string", "description": "通知原因"},
            "mood_assessment": {"type": "string", "description": "情緒評估"},
            "conversation_notes": {"type": "string", "description": "對話記錄"},
            "oncologist_notification_level": {
                "type": "string",
                "enum": ["none", "amber", "red"],
                "description": "腫瘤科醫生通知級別"
            },
            "treatment_status": {
                "type": "string",
                "enum": ["undergoing_treatment", "in_remission"],
                "description": "治療狀態"
            }
        },
        "required": ["timestamp", "patient_id", "symptoms", "flag_for_oncologist", "oncologist_notification_level", "treatment_status"]
    }
}

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
    fallback_responses = {
        "welcome": f"Hello {patient_name}! I'm Florence, your AI nurse. I'm here to chat with you about how you're feeling today. How are you doing?",
        "processing_error": "I'm sorry, I had trouble processing that. Could you tell me more about how you're feeling today?",
        "general_followup": f"Thank you for sharing that, {patient_name}. Can you tell me more about how you've been feeling today?",
        "system_error": "I'm sorry, but I'm having trouble right now. Please try again later."
    }
    return fallback_responses.get(context, fallback_responses["general_followup"])

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

def create_assessment_record(session_data: Dict, structured_assessment: Optional[Dict] = None) -> Dict[str, Any]:
    """Create standardized assessment record for database storage using the structured format"""
    return {
        "session_id": session_data["session_id"],
        "user_id": session_data["user_id"],
        "user_info": session_data["user_info"],
        "language": session_data.get("language", "en"),
        "input_mode": session_data.get("input_mode", "keyboard"),
        "conversation_history": session_data["conversation_history"],
        "structured_assessment": structured_assessment,  # New structured format
        "created_at": session_data["created_at"],
        "completed_at": session_data.get("completed_at", create_timestamp()),
        "assessment_type": "florence_conversation",
        "florence_state": session_data.get("florence_state", "completed"),
        "ai_powered": session_data.get("ai_available", False),
        "oncologist_notification_level": structured_assessment.get("oncologist_notification_level", "none") if structured_assessment else "none",
        "flag_for_oncologist": structured_assessment.get("flag_for_oncologist", False) if structured_assessment else False
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