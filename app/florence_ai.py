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
    generate_fallback_response,
    format_conversation_history_for_ai,
    handle_ai_response_error,
    load_florence_system_prompt
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
    
# Assessment logic moved to florence_assessment.py

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

# Assessment functions moved to florence_assessment.py 