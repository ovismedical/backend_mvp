"""
Florence Triage Module - Clinical triage and diagnosis assessment
Handles clinical triage assessment and alert level determination independently from conversation and summary
"""

import os
import json
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from openai import OpenAI

from .florence_utils import (
    TRIAGE_FUNCTION_SCHEMA,
    TRIAGE_FUNCTION_SCHEMA_ZH,
    create_timestamp,
    format_conversation_history_for_ai,
    handle_ai_response_error
)


class FlorenceTriage:
    """Handles clinical triage assessment from conversation history"""
    
    def __init__(self):
        self.client = None
        self.model = "gpt-4"
        self.temperature = 0.3  # Lower temperature for more consistent clinical decisions
        
    def initialize(self, api_key: str = None):
        """Initialize OpenAI client for triage"""
        try:
            if api_key:
                print(f"🔑 Initializing Triage module with provided API key: {api_key[:10]}...")
                self.client = OpenAI(
                    api_key=api_key,
                    timeout=60.0,  # Increase timeout for VPN
                    max_retries=3
                )
            else:
                # Try to get from environment
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    print("❌ No OpenAI API key found for triage")
                    raise ValueError("OpenAI API key not provided")
                print(f"🔑 Triage module using environment API key: {api_key[:10]}...")
                self.client = OpenAI(
                    api_key=api_key,
                    timeout=60.0,  # Increase timeout for VPN
                    max_retries=3
                )
            print("✅ Triage client initialized successfully")
            return True
        except Exception as e:
            print(f"❌ Failed to initialize Triage client: {e}")
            return False
    
    def _load_triage_prompt(self, language: str = "en") -> str:
        """Load triage prompt from external file"""
        try:
            # Get the directory where this module is located
            current_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Select prompt file based on language
            if language == "zh-HK":
                prompt_file_path = os.path.join(current_dir, "triage_prompt_canto.txt")
                print(f"🔤 Loading Cantonese triage prompt from {prompt_file_path}")
            else:
                prompt_file_path = os.path.join(current_dir, "triage_prompt_eng.txt")
                print(f"🔤 Loading English triage prompt from {prompt_file_path}")
            
            with open(prompt_file_path, 'r', encoding='utf-8') as file:
                prompt_template = file.read().strip()
                
            if not prompt_template:
                raise ValueError("Triage prompt file is empty")
                
            print(f"✅ Successfully loaded triage prompt from {prompt_file_path}")
            return prompt_template
            
        except FileNotFoundError:
            print(f"❌ Triage prompt file not found at {prompt_file_path}")
            # Fallback prompt
            return "Based on the conversation above with patient {patient_id}, please perform a clinical triage assessment to determine potential diagnoses and urgency level."
        except Exception as e:
            print(f"❌ Error loading triage prompt file: {e}")
            # Fallback prompt
            return "Based on the conversation above with patient {patient_id}, please perform a clinical triage assessment to determine potential diagnoses and urgency level."
    
    async def generate_triage_assessment(
        self, 
        conversation_history: List[Dict], 
        patient_id: str, 
        treatment_status: str = "undergoing_treatment", 
        session_language: str = "en"
    ) -> Dict[str, Any]:
        """Generate a clinical triage assessment using OpenAI function calling"""
        if not self.client:
            return {"error": "Triage system not initialized"}
        
        try:
            # Use the session language setting to determine report language
            is_cantonese_report = session_language == "zh-HK"
            
            print(f"🚨 Session language: {session_language}, Using Cantonese triage: {is_cantonese_report}")
            
            # Load triage prompt template from file
            prompt_template = self._load_triage_prompt(session_language)
            
            # Format the prompt with dynamic values
            if is_cantonese_report:
                # Translate treatment status to Cantonese
                treatment_status_zh = "正在接受治療" if treatment_status == "undergoing_treatment" else "康復期"
                triage_prompt = prompt_template.format(
                    patient_id=patient_id,
                    treatment_status=treatment_status_zh
                )
            else:
                triage_prompt = prompt_template.format(
                    patient_id=patient_id,
                    treatment_status=treatment_status
                )
            
            # Format history for AI and add triage request
            ai_history = format_conversation_history_for_ai(conversation_history, include_system_prompt=False)
            ai_history.append({"role": "user", "content": triage_prompt})
            
            print(f"🚨 Making clinical triage API call with function calling...")
            print(f"📝 Conversation length: {len(conversation_history)} messages")
            print(f"👤 Patient ID: {patient_id}")
            print(f"🏥 Treatment status: {treatment_status}")
            print(f"🗣️ Triage language: {'Cantonese' if is_cantonese_report else 'English'}")
            
            # Choose the appropriate function schema based on session language
            function_schema = TRIAGE_FUNCTION_SCHEMA_ZH if is_cantonese_report else TRIAGE_FUNCTION_SCHEMA
            
            # Make API call with function calling
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=ai_history,
                temperature=self.temperature,
                functions=[function_schema],
                function_call={"name": "record_triage_assessment"},
                stream=False
            )
            
            # Parse the function call response
            if completion.choices[0].message.function_call:
                print("✅ Got triage function call response from OpenAI")
                function_args = json.loads(completion.choices[0].message.function_call.arguments)
                print(f"🚨 Triage function args received: {json.dumps(function_args, indent=2)}")
                
                # Add timestamp and patient_id if not provided
                function_args["timestamp"] = create_timestamp()
                function_args["patient_id"] = patient_id
                
                # Log triage results
                alert_level = function_args.get("alert_level", "UNKNOWN")
                diagnoses_count = len(function_args.get("potential_diagnoses", []))
                print(f"🚨 Triage completed: Alert Level = {alert_level}, {diagnoses_count} potential diagnoses")
                
                return {
                    "triage_assessment": function_args,
                    "conversation_length": len(conversation_history),
                    "alert_level": alert_level
                }
            else:
                print("❌ No function call in OpenAI triage response, using fallback")
                # Fallback if function calling fails
                return await self._generate_fallback_triage(conversation_history, patient_id, treatment_status)
                
        except Exception as e:
            print(f"❌ Error generating triage assessment: {e}")
            return await self._generate_fallback_triage(conversation_history, patient_id, treatment_status)
    
    async def _generate_fallback_triage(self, conversation_history: List[Dict], patient_id: str, treatment_status: str) -> Dict[str, Any]:
        """Generate a fallback triage assessment when structured function calling fails"""
        try:
            # Create a conservative fallback triage assessment
            fallback_triage = {
                "timestamp": create_timestamp(),
                "patient_id": patient_id,
                "potential_diagnoses": [
                    {
                        "condition": "Unable to assess - system error",
                        "likelihood": "low",
                        "rationale": "Automated triage failed, manual clinical review required"
                    }
                ],
                "alert_level": "YELLOW",  # Conservative fallback
                "alert_rationale": "Unable to complete automated triage assessment - recommend clinical review as precaution",
                "key_symptoms": ["System unable to analyze conversation"],
                "recommended_timeline": "within 24 hours",
                "clinical_notes": f"Automated triage failed for conversation with {len(conversation_history)} messages. Manual clinical assessment recommended.",
                "treatment_status": treatment_status
            }
            
            return {
                "triage_assessment": fallback_triage,
                "conversation_length": len(conversation_history),
                "alert_level": "YELLOW"
            }
            
        except Exception as e:
            print(f"Error in fallback triage: {e}")
            return {
                "error": str(e),
                "triage_assessment": None,
                "alert_level": "RED"  # Error state = urgent review
            }
    
    def get_alert_level_description(self, alert_level: str, language: str = "en") -> str:
        """Get human-readable description of alert level"""
        descriptions = {
            "en": {
                "GREEN": "Routine symptoms, stable condition - normal follow-up appropriate",
                "YELLOW": "Moderate symptoms requiring monitoring - consider consultation", 
                "ORANGE": "Concerning symptoms - same-day medical review recommended",
                "RED": "Severe symptoms - urgent medical attention required"
            },
            "zh-HK": {
                "GREEN": "常規症狀，病情穩定 - 適合正常隨訪",
                "YELLOW": "中度症狀需要監察 - 考慮諮詢", 
                "ORANGE": "令人擔憂的症狀 - 建議當日醫療檢查",
                "RED": "嚴重症狀 - 需要緊急醫療關注"
            }
        }
        
        lang_key = "zh-HK" if language == "zh-HK" else "en"
        return descriptions[lang_key].get(alert_level, f"Unknown alert level: {alert_level}")


# Global Triage instance
florence_triage = FlorenceTriage()

# Convenience functions for the API
async def initialize_florence_triage(api_key: str = None) -> bool:
    """Initialize Florence Triage system"""
    return florence_triage.initialize(api_key)

async def get_florence_triage_assessment(
    conversation_history: List[Dict], 
    patient_id: str, 
    treatment_status: str = "undergoing_treatment",
    session_language: str = "en"
) -> Dict[str, Any]:
    """Generate triage assessment using the separated triage system"""
    return await florence_triage.generate_triage_assessment(
        conversation_history, patient_id, treatment_status, session_language
    )

def get_alert_level_description(alert_level: str, language: str = "en") -> str:
    """Get human-readable description of alert level"""
    return florence_triage.get_alert_level_description(alert_level, language) 