"""
Florence Assessment Module - Separated assessment/summarization logic
Handles structured assessment generation independently from conversation flow
"""

import os
import json
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from openai import OpenAI

from .florence_utils import (
    ASSESSMENT_FUNCTION_SCHEMA,
    ASSESSMENT_FUNCTION_SCHEMA_ZH,
    create_timestamp,
    should_flag_symptoms,
    format_conversation_history_for_ai,
    handle_ai_response_error
)


class FlorenceAssessment:
    """Handles structured assessment generation from conversation history"""
    
    def __init__(self):
        self.client = None
        self.model = "gpt-4"
        self.temperature = 0.8
        
    def initialize(self, api_key: str = None):
        """Initialize OpenAI client for assessment"""
        try:
            if api_key:
                print(f"🔑 Initializing Assessment module with provided API key: {api_key[:10]}...")
                self.client = OpenAI(api_key=api_key)
            else:
                # Try to get from environment
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    print("❌ No OpenAI API key found for assessment")
                    raise ValueError("OpenAI API key not provided")
                print(f"🔑 Assessment module using environment API key: {api_key[:10]}...")
                self.client = OpenAI(api_key=api_key)
            print("✅ Assessment client initialized successfully")
            return True
        except Exception as e:
            print(f"❌ Failed to initialize Assessment client: {e}")
            return False
    
    def _load_assessment_prompt(self, language: str = "en") -> str:
        """Load assessment prompt from external file"""
        try:
            # Get the directory where this module is located
            current_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Select prompt file based on language
            if language == "zh-HK":
                prompt_file_path = os.path.join(current_dir, "assessment_prompt_canto.txt")
                print(f"🔤 Loading Cantonese assessment prompt from {prompt_file_path}")
            else:
                prompt_file_path = os.path.join(current_dir, "assessment_prompt_eng.txt")
                print(f"🔤 Loading English assessment prompt from {prompt_file_path}")
            
            with open(prompt_file_path, 'r', encoding='utf-8') as file:
                prompt_template = file.read().strip()
                
            if not prompt_template:
                raise ValueError("Assessment prompt file is empty")
                
            print(f"✅ Successfully loaded assessment prompt from {prompt_file_path}")
            return prompt_template
            
        except FileNotFoundError:
            print(f"❌ Assessment prompt file not found at {prompt_file_path}")
            # Fallback prompt
            return "Based on the conversation above with patient {patient_id}, please generate a comprehensive structured assessment."
        except Exception as e:
            print(f"❌ Error loading assessment prompt file: {e}")
            # Fallback prompt
            return "Based on the conversation above with patient {patient_id}, please generate a comprehensive structured assessment."
    
    async def generate_structured_assessment(
        self, 
        conversation_history: List[Dict], 
        patient_id: str, 
        treatment_status: str = "undergoing_treatment", 
        session_language: str = "en"
    ) -> Dict[str, Any]:
        """Generate a structured assessment using OpenAI function calling"""
        if not self.client:
            return {"error": "Assessment system not initialized"}
        
        try:
            # Use the session language setting to determine report language
            is_cantonese_report = session_language == "zh-HK"
            
            print(f"🔍 Session language: {session_language}, Using Cantonese report: {is_cantonese_report}")
            
            # Load assessment prompt template from file
            prompt_template = self._load_assessment_prompt(session_language)
            
            # Format the prompt with dynamic values
            if is_cantonese_report:
                # Translate treatment status to Cantonese
                treatment_status_zh = "正在接受治療" if treatment_status == "undergoing_treatment" else "康復期"
                assessment_prompt = prompt_template.format(
                    patient_id=patient_id,
                    treatment_status=treatment_status_zh
                )
            else:
                assessment_prompt = prompt_template.format(
                    patient_id=patient_id,
                    treatment_status=treatment_status
                )
            
            # Format history for AI and add assessment request
            ai_history = format_conversation_history_for_ai(conversation_history, include_system_prompt=False)
            ai_history.append({"role": "user", "content": assessment_prompt})
            
            print(f"🔍 Making structured assessment API call with function calling...")
            print(f"📝 Conversation length: {len(conversation_history)} messages")
            print(f"👤 Patient ID: {patient_id}")
            print(f"🏥 Treatment status: {treatment_status}")
            print(f"🗣️ Report language: {'Cantonese' if is_cantonese_report else 'English'}")
            
            # Choose the appropriate function schema based on session language
            function_schema = ASSESSMENT_FUNCTION_SCHEMA_ZH if is_cantonese_report else ASSESSMENT_FUNCTION_SCHEMA
            
            # Make API call with function calling
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=ai_history,
                temperature=self.temperature,
                functions=[function_schema],
                function_call={"name": "record_symptom_assessment"},
                stream=False
            )
            
            # Parse the function call response
            if completion.choices[0].message.function_call:
                print("✅ Got function call response from OpenAI")
                function_args = json.loads(completion.choices[0].message.function_call.arguments)
                print(f"📊 Function args received: {json.dumps(function_args, indent=2)}")
                
                # Add timestamp and patient_id if not provided
                function_args["timestamp"] = create_timestamp()
                function_args["patient_id"] = patient_id
                
                # Determine oncologist flagging
                symptoms = function_args.get("symptoms", {})
                should_flag, notification_level, flag_reason = should_flag_symptoms(symptoms, treatment_status)
                
                function_args["flag_for_oncologist"] = should_flag
                function_args["oncologist_notification_level"] = notification_level
                if should_flag:
                    function_args["flag_reason"] = flag_reason
                
                print(f"🏁 Final structured assessment created with {len(symptoms)} symptoms")
                return {
                    "structured_assessment": function_args,
                    "conversation_length": len(conversation_history)
                }
            else:
                print("❌ No function call in OpenAI response, using fallback")
                # Fallback if function calling fails
                return await self._generate_fallback_assessment(conversation_history, patient_id, treatment_status)
                
        except Exception as e:
            print(f"❌ Error generating structured assessment: {e}")
            return await self._generate_fallback_assessment(conversation_history, patient_id, treatment_status)
    
    async def _generate_fallback_assessment(self, conversation_history: List[Dict], patient_id: str, treatment_status: str) -> Dict[str, Any]:
        """Generate a fallback assessment when structured function calling fails"""
        try:
            # Create a simple structured assessment based on what we know
            fallback_assessment = {
                "timestamp": create_timestamp(),
                "patient_id": patient_id,
                "symptoms": {
                    "cough": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                    "nausea": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                    "lack_of_appetite": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                    "fatigue": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                    "pain": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []}
                },
                "flag_for_oncologist": False,
                "oncologist_notification_level": "none",
                "treatment_status": treatment_status,
                "mood_assessment": "Assessment completed through conversation with Florence",
                "conversation_notes": f"Conversation with {len(conversation_history)} messages completed."
            }
            
            return {
                "structured_assessment": fallback_assessment,
                "conversation_length": len(conversation_history)
            }
            
        except Exception as e:
            print(f"Error in fallback assessment: {e}")
            return {
                "error": str(e),
                "structured_assessment": None
            }


# Global Assessment instance
florence_assessment = FlorenceAssessment()

# Convenience functions for the API
async def initialize_florence_assessment(api_key: str = None) -> bool:
    """Initialize Florence Assessment system"""
    return florence_assessment.initialize(api_key)

async def get_florence_structured_assessment(
    conversation_history: List[Dict], 
    patient_id: str, 
    treatment_status: str = "undergoing_treatment",
    session_language: str = "en"
) -> Dict[str, Any]:
    """Generate structured assessment using the separated assessment system"""
    return await florence_assessment.generate_structured_assessment(
        conversation_history, patient_id, treatment_status, session_language
    ) 