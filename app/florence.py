from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Dict, Optional
import json
import threading
import time
import asyncio
import os
from datetime import datetime, timezone
from .login import get_user, get_db
from .florence_ai import (
    initialize_florence,
    start_florence_conversation,
    send_message_to_florence,
    get_florence_assessment
)

florencerouter = APIRouter(prefix="/florence", tags=["florence"])

# Pydantic models
class StartSessionRequest(BaseModel):
    language: str = "en"
    input_mode: str = "keyboard"

class SendMessageRequest(BaseModel):
    session_id: str
    message: str

class SessionResponse(BaseModel):
    session_id: str
    status: str
    message: str

# Global session storage (in production, this should be Redis/database)
active_sessions: Dict[str, Dict] = {}

# Initialize Florence AI on startup
@florencerouter.on_event("startup")
async def startup_florence():
    """Initialize Florence AI system on startup"""
    api_key = "nuh uh"
    if api_key:
        success = await initialize_florence(api_key)
        if success:
            print("✅ Florence AI initialized successfully")
        else:
            print("❌ Florence AI initialization failed")
    else:
        print("⚠️ No OpenAI API key found - Florence will use fallback responses")

@florencerouter.post("/start_session", response_model=SessionResponse)
async def start_florence_session(
    request: StartSessionRequest,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Start a new Florence conversation session"""
    try:
        # Create unique session ID
        session_id = f"{user['username']}_{int(time.time())}"
        
        # Start conversation with Florence
        patient_name = user.get('full_name', user['username'])
        florence_response = await start_florence_conversation(patient_name)
        
        if "error" in florence_response:
            # Fallback response if AI fails
            welcome_message = f"Hello {patient_name}! I'm Florence, your AI nurse. I'm here to chat with you about how you're feeling today. How are you doing?"
            conversation_history = [
                {
                    "role": "assistant",
                    "content": welcome_message,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            ]
        else:
            # Use AI response
            conversation_history = [
                {
                    "role": "assistant",
                    "content": florence_response["response"],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            ]
        
        # Initialize session state
        session_data = {
            "session_id": session_id,
            "user_id": user['username'],
            "user_info": user,
            "language": request.language,
            "input_mode": request.input_mode,
            "status": "active",
            "conversation_history": conversation_history,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "assessment_result": None,
            "florence_state": florence_response.get("conversation_state", "starting"),
            "symptoms_assessed": florence_response.get("symptoms_assessed", []),
            "ai_available": "error" not in florence_response
        }
        
        # Store session
        active_sessions[session_id] = session_data
        
        return SessionResponse(
            session_id=session_id,
            status="active",
            message=conversation_history[0]["content"]
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start Florence session: {str(e)}"
        )

@florencerouter.get("/session/{session_id}")
async def get_session_status(
    session_id: str,
    user = Depends(get_user)
):
    """Get current session status and conversation history"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = active_sessions[session_id]
    
    # Verify user owns this session
    if session["user_id"] != user["username"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    return {
        "session_id": session_id,
        "status": session["status"],
        "conversation_history": session["conversation_history"],
        "assessment_result": session["assessment_result"],
        "created_at": session["created_at"],
        "florence_state": session.get("florence_state", "starting"),
        "symptoms_assessed": session.get("symptoms_assessed", []),
        "ai_available": session.get("ai_available", False)
    }

@florencerouter.post("/send_message")
async def send_message_to_florence_endpoint(
    request: SendMessageRequest,
    user = Depends(get_user)
):
    """Send a message to Florence in an active session"""
    if request.session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = active_sessions[request.session_id]
    
    # Verify user owns this session
    if session["user_id"] != user["username"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail="Session is not active")
    
    try:
        # Add user message to conversation history
        user_message = {
            "role": "user",
            "content": request.message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        session["conversation_history"].append(user_message)
        
        # Get Florence's response
        if session.get("ai_available", False):
            # Use AI response
            florence_response = await send_message_to_florence(
                request.message, 
                session["conversation_history"]
            )
            
            if "error" in florence_response:
                # Fallback response
                ai_response = "I'm sorry, I had trouble processing that. Could you tell me more about how you're feeling today?"
            else:
                ai_response = florence_response["response"]
                # Update session state
                session["florence_state"] = florence_response.get("conversation_state", "assessing")
                session["symptoms_assessed"] = florence_response.get("symptoms_assessed", [])
        else:
            # Fallback response when AI is not available
            ai_response = f"Thank you for sharing that, {user['full_name']}. I understand you mentioned: '{request.message}'. Can you tell me more about how you've been feeling today?"
        
        # Add Florence's response to conversation history
        assistant_message = {
            "role": "assistant", 
            "content": ai_response,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        session["conversation_history"].append(assistant_message)
        
        return {
            "success": True,
            "message": "Message sent to Florence",
            "response": ai_response,
            "florence_state": session.get("florence_state", "assessing"),
            "symptoms_assessed": session.get("symptoms_assessed", [])
        }
        
    except Exception as e:
        print(f"Error in send_message: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send message: {str(e)}"
        )

@florencerouter.post("/finish_session/{session_id}")
async def finish_florence_session(
    session_id: str,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Finish Florence session and save assessment to database"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = active_sessions[session_id]
    
    # Verify user owns this session
    if session["user_id"] != user["username"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        # Generate assessment summary if AI is available
        assessment_summary = None
        if session.get("ai_available", False):
            try:
                summary_result = await get_florence_assessment(session["conversation_history"])
                assessment_summary = summary_result.get("summary", {})
            except Exception as e:
                print(f"Error generating assessment summary: {e}")
                assessment_summary = {
                    "assessment_summary": "Assessment completed through conversation with Florence",
                    "symptoms_discussed": session.get("symptoms_assessed", [])
                }
        
        # Mark session as completed
        session["status"] = "completed"
        session["completed_at"] = datetime.now(timezone.utc).isoformat()
        session["assessment_result"] = assessment_summary
        
        # Save to MongoDB
        florence_collection = db["florence_assessments"]
        assessment_record = {
            "session_id": session_id,
            "user_id": session["user_id"],
            "user_info": session["user_info"],
            "language": session["language"],
            "input_mode": session["input_mode"],
            "conversation_history": session["conversation_history"],
            "assessment_result": assessment_summary,
            "created_at": session["created_at"],
            "completed_at": session["completed_at"],
            "assessment_type": "florence_conversation",
            "florence_state": session.get("florence_state", "completed"),
            "symptoms_assessed": session.get("symptoms_assessed", []),
            "ai_powered": session.get("ai_available", False)
        }
        
        result = florence_collection.insert_one(assessment_record)
        
        # Clean up session from memory
        del active_sessions[session_id]
        
        return {
            "success": True,
            "message": "Florence session completed and saved",
            "assessment_id": str(result.inserted_id),
            "conversation_length": len(session["conversation_history"]),
            "symptoms_assessed": session.get("symptoms_assessed", []),
            "assessment_summary": assessment_summary
        }
        
    except Exception as e:
        print(f"Error finishing session: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to finish session: {str(e)}"
        )

@florencerouter.get("/test")
async def test_florence_endpoint():
    """Simple test endpoint to verify Florence module is working"""
    # Test if OpenAI is available
    openai_available = os.getenv("OPENAI_API_KEY") is not None
    
    return {
        "status": "ok",
        "message": "Florence module is working!",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_sessions": len(active_sessions),
        "openai_available": openai_available,
        "ai_enabled": openai_available
    } 