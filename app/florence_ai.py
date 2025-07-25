"""
Florence AI - Simplified conversational assessment system for OVIS
A streamlined version using structured assessment format from telenurse/gpt_json.py
"""

import os
import json
import asyncio
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
import openai
from openai import OpenAI

from .florence_utils import (
    TARGET_SYMPTOMS,
    PAIN_KEYWORDS,
    ASSESSMENT_FUNCTION_SCHEMA,
    ASSESSMENT_FUNCTION_SCHEMA_ZH,
    create_timestamp,
    generate_fallback_response,
    format_conversation_history_for_ai,
    handle_ai_response_error,
    load_florence_system_prompt,
    should_flag_symptoms
)

class FlorenceAI:
    def __init__(self):
        self.client = None
        self.model = "gpt-4"
        self.temperature = 0.8
        self.conversation_state = "starting"  # starting, assessing, completing
        self.system_prompt = None  # Will be loaded when needed
        self.language = "en"  # Default language
        
    def _get_system_prompt(self) -> str:
        """Get the system prompt, loading it if necessary"""
        if self.system_prompt is None:
            self.system_prompt = load_florence_system_prompt(self.language)
        return self.system_prompt
        
    def set_language(self, language: str):
        """Set the language for the conversation"""
        self.language = language
        self.system_prompt = None  # Reset system prompt to force reload with new language
        
    def initialize(self, api_key: str = None):
        """Initialize OpenAI client"""
        try:
            if api_key:
                print(f"🔑 Initializing with provided API key: {api_key[:10]}...")
                self.client = OpenAI(api_key=api_key)
            else:
                # Try to get from environment
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    print("❌ No OpenAI API key found")
                    raise ValueError("OpenAI API key not provided")
                print(f"🔑 Using hardcoded API key: {api_key[:10]}...")
                self.client = OpenAI(api_key=api_key)
            print("✅ OpenAI client initialized successfully")
            return True
        except Exception as e:
            print(f"❌ Failed to initialize OpenAI client: {e}")
            return False
    
    async def start_conversation(self, patient_name: str = "there") -> Dict[str, Any]:
        """Start a new conversation with Florence"""
        print(f"🚀 Starting conversation for {patient_name}")
        if not self.client:
            print("🔄 Client not initialized, attempting to initialize...")
            if not self.initialize():
                print("❌ Failed to initialize AI system")
                return {
                    "error": "Failed to initialize AI system",
                    "response": generate_fallback_response(patient_name, "system_error")
                }
        
        # Reset conversation state
        self.conversation_state = "starting"
        
        # Generate initial greeting
        try:
            print("📡 Making OpenAI API call...")
            response = await self._get_ai_response([
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": f"Hello, I'm {patient_name}. I'm here for my health check-in."}
            ])
            print(f"✅ Got AI response: {response[:50]}...")
            
            return {
                "response": response,
                "conversation_state": self.conversation_state
            }
            
        except Exception as e:
            return handle_ai_response_error(e, "start_conversation", patient_name)
    
    async def process_message(self, message: str, conversation_history: List[Dict]) -> Dict[str, Any]:
        """Process a user message and generate Florence's response"""
        if not self.client:
            return handle_ai_response_error(
                Exception("AI system not initialized"), 
                "process_message"
            )
        
        try:
            # Format conversation history for AI
            ai_history = format_conversation_history_for_ai(
                conversation_history, 
                include_system_prompt=True,
                system_prompt=self._get_system_prompt()
            )
            
            # Add the new user message
            ai_history.append({"role": "user", "content": message})
            
            # Get AI response
            response = await self._get_ai_response(ai_history)
            
            return {
                "response": response,
                "conversation_state": self.conversation_state
            }
            
        except Exception as e:
            return handle_ai_response_error(e, "process_message")
    
    async def _get_ai_response(self, conversation_history: List[Dict]) -> str:
        """Get response from OpenAI"""
        try:
            # Make API call
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=conversation_history,
                temperature=self.temperature,
                stream=False
            )
            
            response = completion.choices[0].message.content.strip()
            return response
            
        except Exception as e:
            print(f"OpenAI API error: {e}")
            raise e
    
# Note: Conversation state tracking simplified - AI now handles flow naturally
    
    async def generate_structured_assessment(self, conversation_history: List[Dict], patient_id: str, treatment_status: str = "undergoing_treatment", session_language: str = "en") -> Dict[str, Any]:
        """Generate a structured assessment using OpenAI function calling"""
        if not self.client:
            return {"error": "AI system not initialized"}
        
        try:
            # Use the session language setting to determine report language
            is_cantonese_report = session_language == "zh-HK"
            
            print(f"🔍 Session language: {session_language}, Using Cantonese report: {is_cantonese_report}")
            
            # Create assessment prompt based on selected session language
            if is_cantonese_report:
                # Translate treatment status to Cantonese
                treatment_status_zh = "正在接受治療" if treatment_status == "undergoing_treatment" else "康復期"
                
                # Cantonese assessment prompt
                assessment_prompt = f"""
根據上面與病人 {patient_id} 的對話，請生成一份全面的結構化評估。

病人目前狀況：{treatment_status_zh}

對於每個症狀（咳嗽、噁心、食慾不振、疲勞、疼痛），請提供：
- 頻率評級（1-5 級）：症狀發生的頻率
- 嚴重程度評級（1-5 級）：症狀的嚴重程度
- 關鍵指標：病人的直接引述或觀察
- 附加註記：任何相關的背景資訊

如果提到疼痛，也請包括位置。

評估病人的情緒和整體狀況，並確定是否需要通知腫瘤科醫生。

請使用提供的結構化格式返回評估結果，並確保所有文字內容都用繁體中文撰寫。
                """
            else:
                # English assessment prompt
                assessment_prompt = f"""
            Based on the conversation above with patient {patient_id}, please generate a comprehensive structured assessment.
            
            The patient is currently: {treatment_status}
            
            For each symptom (cough, nausea, lack_of_appetite, fatigue, pain), provide:
            - Frequency rating (1-5 scale): How often the symptom occurs
            - Severity rating (1-5 scale): How severe the symptom is
            - Key indicators: Direct quotes or observations from the patient
            - Additional notes: Any relevant context
            
            For pain, also include the location if mentioned.
            
            Assess the patient's mood and overall condition, and determine if oncologist notification is needed.
            """
            
            # Format history for AI and add assessment request
            ai_history = format_conversation_history_for_ai(conversation_history, include_system_prompt=False)
            ai_history.append({"role": "user", "content": assessment_prompt})
            
            print(f"🔍 Making structured assessment API call with function calling...")
            print(f"📝 Conversation length: {len(conversation_history)} messages")
            print(f"👤 Patient ID: {patient_id}")
            print(f"🏥 Treatment status: {treatment_status}")
            print(f"🌐 Florence language setting: {self.language}")
            print(f"🗣️ Report language: {'Cantonese' if is_cantonese_report else 'English'}")
            
            # Choose the appropriate function schema based on session language
            function_schema = ASSESSMENT_FUNCTION_SCHEMA_ZH if is_cantonese_report else ASSESSMENT_FUNCTION_SCHEMA
            
            # Make API call with function calling
            completion = self.client.chat.completions.create(
                model="gpt-4",
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

# Global Florence instance
florence_ai = FlorenceAI()

# Convenience functions for the API
async def initialize_florence(api_key: str = None) -> bool:
    """Initialize Florence AI system"""
    return florence_ai.initialize(api_key)

async def start_florence_conversation(patient_name: str = "there") -> Dict[str, Any]:
    """Start a new conversation with Florence"""
    return await florence_ai.start_conversation(patient_name)

async def send_message_to_florence(message: str, conversation_history: List[Dict]) -> Dict[str, Any]:
    """Send a message to Florence and get response"""
    return await florence_ai.process_message(message, conversation_history)

async def get_florence_structured_assessment(conversation_history: List[Dict], patient_id: str, treatment_status: str = "undergoing_treatment") -> Dict[str, Any]:
    """Generate structured assessment using the telenurse format"""
    return await florence_ai.generate_structured_assessment(conversation_history, patient_id, treatment_status) 