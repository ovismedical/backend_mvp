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
    get_florence_structured_assessment
)
from .florence_utils import (
    create_timestamp,
    create_conversation_message,
    generate_fallback_response,
    validate_session_access,
    is_ai_available,
    create_assessment_record,
    create_session_response_data
)

florencerouter = APIRouter(prefix="/florence", tags=["florence"])

# Pydantic models
class StartSessionRequest(BaseModel):
    language: str = "en"
    input_mode: str = "keyboard"
    treatment_status: str = "undergoing_treatment"  # New field for structured assessment

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
    api_key = os.getenv("OPENAI_API_KEY")
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
        
        # Create conversation history with standardized format
        if "error" in florence_response:
            # Fallback response if AI fails
            welcome_message = generate_fallback_response(patient_name, "welcome")
            conversation_history = [
                create_conversation_message("assistant", welcome_message)
            ]
            ai_available = False
        else:
            # Use AI response
            conversation_history = [
                create_conversation_message("assistant", florence_response["response"])
            ]
            ai_available = True
        
        # Initialize session state using standardized structure
        session_data = {
            "session_id": session_id,
            "user_id": user['username'],
            "user_info": user,
            "language": request.language,
            "input_mode": request.input_mode,
            "treatment_status": request.treatment_status,  # Store treatment status
            "status": "active",
            "conversation_history": conversation_history,
            "created_at": create_timestamp(),
            "structured_assessment": None,  # Will be populated when session completes
            "florence_state": florence_response.get("conversation_state", "starting"),
            "symptoms_assessed": florence_response.get("symptoms_assessed", []),
            "ai_available": ai_available,
            "oncologist_notification_level": "none",
            "flag_for_oncologist": False
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
    
    # Verify user owns this session using shared utility
    if not validate_session_access(session, user["username"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Return standardized session response
    return create_session_response_data(session)

@florencerouter.post("/send_message")
async def send_message_to_florence_endpoint(
    request: SendMessageRequest,
    user = Depends(get_user)
):
    """Send a message to Florence in an active session"""
    if request.session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = active_sessions[request.session_id]
    
    # Verify user owns this session using shared utility
    if not validate_session_access(session, user["username"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    if session["status"] != "active":
        raise HTTPException(status_code=400, detail="Session is not active")
    
    try:
        # Add user message to conversation history using standardized format
        user_message = create_conversation_message("user", request.message)
        session["conversation_history"].append(user_message)
        
        # Get Florence's response
        if session.get("ai_available", False):
            # Use AI response
            florence_response = await send_message_to_florence(
                request.message, 
                session["conversation_history"]
            )
            
            if "error" in florence_response:
                # Fallback response using shared utility
                ai_response = generate_fallback_response(user.get('full_name', user['username']), "processing_error")
            else:
                ai_response = florence_response["response"]
                # Update session state
                session["florence_state"] = florence_response.get("conversation_state", "assessing")
                session["symptoms_assessed"] = florence_response.get("symptoms_assessed", [])
        else:
            # Fallback response when AI is not available using shared utility
            ai_response = generate_fallback_response(user.get('full_name', user['username']), "general_followup")
        
        # Add Florence's response to conversation history using standardized format
        assistant_message = create_conversation_message("assistant", ai_response)
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
    """Finish Florence session and save structured assessment to database"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session = active_sessions[session_id]
    
    # Verify user owns this session using shared utility
    if not validate_session_access(session, user["username"]):
        raise HTTPException(status_code=403, detail="Access denied")
    
    try:
        # Generate structured assessment if AI is available
        structured_assessment = None
        if session.get("ai_available", False):
            try:
                # Get patient ID and treatment status from session
                patient_id = session["user_id"]
                treatment_status = session.get("treatment_status", "undergoing_treatment")
                
                assessment_result = await get_florence_structured_assessment(
                    session["conversation_history"], 
                    patient_id, 
                    treatment_status
                )
                structured_assessment = assessment_result.get("structured_assessment")
                
                # Update session with oncologist notification info
                if structured_assessment:
                    session["oncologist_notification_level"] = structured_assessment.get("oncologist_notification_level", "none")
                    session["flag_for_oncologist"] = structured_assessment.get("flag_for_oncologist", False)
                
            except Exception as e:
                print(f"Error generating structured assessment: {e}")
                # Create a minimal fallback assessment
                structured_assessment = {
                    "timestamp": create_timestamp(),
                    "patient_id": session["user_id"],
                    "symptoms": {
                        "cough": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                        "nausea": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                        "lack_of_appetite": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                        "fatigue": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                        "pain": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []}
                    },
                    "flag_for_oncologist": False,
                    "oncologist_notification_level": "none",
                    "treatment_status": session.get("treatment_status", "undergoing_treatment"),
                    "mood_assessment": "Assessment completed through conversation with Florence",
                    "conversation_notes": f"Symptoms discussed: {session.get('symptoms_assessed', [])}"
                }
        
        # Mark session as completed
        session["status"] = "completed"
        session["completed_at"] = create_timestamp()
        session["structured_assessment"] = structured_assessment
        
        # Save to MongoDB using standardized record format
        florence_collection = db["florence_assessments"]
        assessment_record = create_assessment_record(session, structured_assessment)
        
        result = florence_collection.insert_one(assessment_record)
        
        # Clean up session from memory
        del active_sessions[session_id]
        
        return {
            "success": True,
            "message": "Florence session completed and saved",
            "assessment_id": str(result.inserted_id),
            "conversation_length": len(session["conversation_history"]),
            "symptoms_assessed": session.get("symptoms_assessed", []),
            "structured_assessment": structured_assessment,
            "oncologist_notification_level": session.get("oncologist_notification_level", "none"),
            "flag_for_oncologist": session.get("flag_for_oncologist", False)
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
    # Test if OpenAI is available using shared utility
    openai_available = is_ai_available()
    
    return {
        "status": "ok",
        "message": "Florence module is working!",
        "timestamp": create_timestamp(),
        "active_sessions": len(active_sessions),
        "openai_available": openai_available,
        "ai_enabled": openai_available
    } 