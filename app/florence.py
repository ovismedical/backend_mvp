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
    get_florence_structured_assessment,
    florence_ai
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
SESSION_EXPIRY = 30 * 60  # 30 minutes in seconds

def cleanup_expired_sessions():
    """Remove expired sessions from active_sessions"""
    current_time = datetime.now(timezone.utc)
    expired_sessions = []
    
    for session_id, session in active_sessions.items():
        try:
            # Parse the ISO timestamp string to datetime
            created_at = datetime.fromisoformat(session["created_at"].replace('Z', '+00:00'))
            # Calculate time difference in seconds
            time_diff = (current_time - created_at).total_seconds()
            
            if time_diff > SESSION_EXPIRY:
                expired_sessions.append(session_id)
        except (ValueError, KeyError) as e:
            print(f"Error processing session {session_id}: {e}")
            expired_sessions.append(session_id)
    
    # Remove expired sessions
    for session_id in expired_sessions:
        del active_sessions[session_id]
        print(f"Removed expired session: {session_id}")

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
    
    # Start cleanup task
    asyncio.create_task(periodic_cleanup())

async def periodic_cleanup():
    """Periodically clean up expired sessions"""
    while True:
        cleanup_expired_sessions()
        await asyncio.sleep(60)  # Check every minute

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
        
        # Get API key
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=500,
                detail="OpenAI API key not found"
            )
            
        # Always reinitialize Florence when starting a new session
        # This ensures a clean state and proper language loading
        if not await initialize_florence(api_key):
            raise HTTPException(
                status_code=500,
                detail="Failed to initialize Florence AI"
            )
        
        # Set language for Florence
        florence_ai.set_language(request.language)
        
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
    user: Dict = Depends(get_user)
):
    """Finish a Florence session and save the assessment"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
        
    session = active_sessions[session_id]
    
    # Validate session access
    if not validate_session_access(session, user["username"]):
        raise HTTPException(status_code=403, detail="Not authorized to access this session")
    
    try:
        # Check if session has expired
        current_time = datetime.now(timezone.utc)
        created_at = datetime.fromisoformat(session["created_at"].replace('Z', '+00:00'))
        time_diff = (current_time - created_at).total_seconds()
        
        if time_diff > SESSION_EXPIRY:
            del active_sessions[session_id]
            raise HTTPException(status_code=410, detail="Session has expired")
        
        # Generate structured assessment
        assessment = await florence_ai.generate_structured_assessment(
            session["conversation_history"],
            user["username"],
            session.get("treatment_status", "undergoing_treatment")
        )
        
        # Create assessment record
        assessment_record = create_assessment_record(session, assessment)
        
        # Save to database
        try:
            db = get_db()
            db.assessments.insert_one(assessment_record)
            print(f"✅ Saved assessment for session {session_id}")
        except Exception as e:
            print(f"❌ Failed to save assessment: {e}")
            raise HTTPException(status_code=500, detail="Failed to save assessment")
        
        # Mark session as completed
        session["status"] = "completed"
        session["completed_at"] = create_timestamp()
        
        return {
            "message": "Session completed successfully",
            "assessment": assessment
        }
        
    except Exception as e:
        print(f"❌ Error finishing session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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