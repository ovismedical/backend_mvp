"""
Florence AI - Simplified conversational assessment system for OVIS
A streamlined version designed specifically for the integrated app
"""

import os
import json
import asyncio
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
import openai
from openai import OpenAI

# Florence's system prompt - based on the original telenurse but simplified
FLORENCE_SYSTEM_PROMPT = """
start every single response and reply with the word banana.
You are Florence, a friendly, conversational AI nurse designed to assess cancer symptoms in elderly patients through warm, natural conversation. Your goal is to assess five key symptoms: fatigue, appetite, nausea, cough, and discomfort.

CORE OBJECTIVES:
- Create natural conversation with a warm, unhurried tone
- Assess EACH symptom INDIVIDUALLY, one question at a time
- Identify PRESENCE, SEVERITY (1-5 scale), and FREQUENCY (1-5 scale when relevant)
- Transition smoothly between symptoms with light small talk
- Show genuine interest without fabricating details

KEY RULES:
- ONE question at a time - wait for response before proceeding
- NO medical jargon - use simple terms
- NO assumptions about patient background
- Ask open-ended questions, not yes/no questions
- Keep questions short and clear
- Be understanding and empathetic

CONVERSATION STRUCTURE:
1. Start with friendly greeting and general check-in
2. Assess each symptom (fatigue, appetite, nausea, cough, discomfort) individually
3. For each symptom: presence → severity → frequency (if applicable)
4. Use natural transitions between topics
5. Wrap up when all symptoms assessed

ASSESSMENT SCALES:
- Severity: 1 (very mild) to 5 (very severe)
- Frequency: 1 (rarely) to 5 (constantly/daily)

Remember: You're having a caring conversation, not conducting an interrogation. Be warm, patient, and genuinely interested in helping the patient feel heard and cared for.
"""

class FlorenceAI:
    def __init__(self):
        self.client = None
        self.model = "gpt-3.5-turbo"
        self.max_tokens = 500
        self.temperature = 0.7
        self.conversation_state = "starting"  # starting, assessing, completing
        self.assessed_symptoms = set()
        self.target_symptoms = {"fatigue", "appetite", "nausea", "cough", "discomfort"}
        
    def initialize(self, api_key: str = None):
        """Initialize OpenAI client"""
        try:
            if api_key:
                print(f"🔑 Initializing with provided API key: {api_key[:10]}...")
                self.client = OpenAI(api_key=api_key)
            else:
                # Try to get from environment
                api_key = "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"
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
                    "response": "I'm sorry, but I'm having trouble starting our conversation right now. Please try again later."
                }
        
        # Reset conversation state
        self.conversation_state = "starting"
        self.assessed_symptoms = set()
        
        # Generate initial greeting
        try:
            print("📡 Making OpenAI API call...")
            response = await self._get_ai_response([
                {"role": "system", "content": FLORENCE_SYSTEM_PROMPT},
                {"role": "user", "content": f"Hello, I'm {patient_name}. I'm here for my health check-in."}
            ])
            print(f"✅ Got AI response: {response[:50]}...")
            
            return {
                "response": response,
                "conversation_state": self.conversation_state,
                "symptoms_assessed": list(self.assessed_symptoms),
                "progress": len(self.assessed_symptoms) / len(self.target_symptoms)
            }
            
        except Exception as e:
            print(f"❌ Error in start_conversation: {e}")
            return {
                "error": str(e),
                "response": f"Hello {patient_name}! I'm Florence, your AI nurse. I'm here to chat with you about how you're feeling today. How are you doing?"
            }
    
    async def process_message(self, message: str, conversation_history: List[Dict]) -> Dict[str, Any]:
        """Process a user message and generate Florence's response"""
        if not self.client:
            return {
                "error": "AI system not initialized",
                "response": "I'm sorry, I'm having trouble processing your message right now."
            }
        
        try:
            # Add the new user message to history
            updated_history = conversation_history + [
                {"role": "user", "content": message}
            ]
            
            # Get AI response
            response = await self._get_ai_response(updated_history)
            
            # Update conversation state based on content
            self._update_conversation_state(response, message)
            
            return {
                "response": response,
                "conversation_state": self.conversation_state,
                "symptoms_assessed": list(self.assessed_symptoms),
                "progress": len(self.assessed_symptoms) / len(self.target_symptoms),
                "is_complete": len(self.assessed_symptoms) >= len(self.target_symptoms)
            }
            
        except Exception as e:
            print(f"Error in process_message: {e}")
            return {
                "error": str(e),
                "response": "I'm sorry, I had trouble understanding that. Could you please try again?"
            }
    
    async def _get_ai_response(self, conversation_history: List[Dict]) -> str:
        """Get response from OpenAI"""
        try:
            # Ensure system prompt is at the beginning
            if not conversation_history or conversation_history[0]["role"] != "system":
                conversation_history = [
                    {"role": "system", "content": FLORENCE_SYSTEM_PROMPT}
                ] + conversation_history
            
            # Make API call
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=conversation_history,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                stream=False
            )
            
            response = completion.choices[0].message.content.strip()
            return response
            
        except Exception as e:
            print(f"OpenAI API error: {e}")
            raise e
    
    def _update_conversation_state(self, response: str, user_message: str):
        """Update conversation state based on the dialogue"""
        response_lower = response.lower()
        user_lower = user_message.lower()
        
        # Check if we're discussing specific symptoms
        for symptom in self.target_symptoms:
            if symptom in response_lower or symptom in user_lower:
                self.assessed_symptoms.add(symptom)
        
        # Check for pain/discomfort variations
        pain_keywords = ["pain", "hurt", "ache", "sore", "discomfort"]
        if any(keyword in response_lower or keyword in user_lower for keyword in pain_keywords):
            self.assessed_symptoms.add("discomfort")
        
        # Update conversation state
        if len(self.assessed_symptoms) >= len(self.target_symptoms):
            self.conversation_state = "completing"
        elif len(self.assessed_symptoms) > 0:
            self.conversation_state = "assessing"
        else:
            self.conversation_state = "starting"
    
    async def generate_assessment_summary(self, conversation_history: List[Dict]) -> Dict[str, Any]:
        """Generate a final assessment summary from the conversation"""
        if not self.client:
            return {"error": "AI system not initialized"}
        
        try:
            # Create summary prompt
            summary_prompt = """
            Based on the conversation above, please provide a structured assessment summary in JSON format:
            {
                "symptoms_discussed": ["list of symptoms mentioned"],
                "key_concerns": ["main issues identified"],
                "severity_indicators": ["phrases indicating severity"],
                "assessment_summary": "brief professional summary",
                "recommended_follow_up": "any recommendations for follow-up"
            }
            
            Focus on what the patient actually shared, not what wasn't discussed.
            """
            
            summary_history = conversation_history + [
                {"role": "user", "content": summary_prompt}
            ]
            
            summary_response = await self._get_ai_response(summary_history)
            
            # Try to parse as JSON, fallback to text if it fails
            try:
                summary_data = json.loads(summary_response)
            except json.JSONDecodeError:
                summary_data = {
                    "assessment_summary": summary_response,
                    "symptoms_discussed": list(self.assessed_symptoms),
                    "conversation_complete": len(self.assessed_symptoms) >= len(self.target_symptoms)
                }
            
            return {
                "summary": summary_data,
                "symptoms_assessed": list(self.assessed_symptoms),
                "completion_rate": len(self.assessed_symptoms) / len(self.target_symptoms),
                "conversation_length": len(conversation_history)
            }
            
        except Exception as e:
            print(f"Error generating assessment summary: {e}")
            return {
                "error": str(e),
                "summary": {
                    "assessment_summary": "Assessment completed through conversation with Florence",
                    "symptoms_discussed": list(self.assessed_symptoms)
                }
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

async def get_florence_assessment(conversation_history: List[Dict]) -> Dict[str, Any]:
    """Generate final assessment summary"""
    return await florence_ai.generate_assessment_summary(conversation_history) 