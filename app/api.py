from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import json 
import os
from datetime import datetime
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Load .env from current directory
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5713", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .login import loginrouter, get_user, get_db
from .admin import adminrouter
from .questions import questionsrouter
from .florence import florencerouter

app.include_router(loginrouter)
app.include_router(adminrouter)
app.include_router(questionsrouter)
app.include_router(florencerouter)

@app.get("/unified_assessments")
async def get_unified_assessments(user = Depends(get_user), db = Depends(get_db)):
    """
    Get all assessments (both daily check-ins and Florence conversations) for the user
    Returns unified format with type, date, and summary
    """
    try:
        user_id = user['username']
        unified_assessments = []
        
        # Get daily check-in responses (from 'answers' collection)
        answers_collection = db["answers"]
        daily_responses = answers_collection.find({"user_id": user_id}).sort("timestamp", -1)
        
        for response in daily_responses:
            # Convert to unified format
            assessment = {
                "id": str(response.get("_id")),
                "type": "daily_checkin",
                "date": response.get("timestamp", "Unknown"),
                "title": "Daily Check-in",
                "summary": f"Completed questionnaire with {len(response.get('answers', []))} responses",
                "data": {
                    "answers": response.get("answers", []),
                    "questions_answered": len(response.get("answers", [])),
                    "completion_time": response.get("timestamp")
                },
                "icon": "fa-clipboard-check",
                "color": "#8b5cf6"
            }
            unified_assessments.append(assessment)
        
        # Get Florence conversations (from 'florence_assessments' collection)
        florence_collection = db["florence_assessments"]
        florence_responses = florence_collection.find({"user_id": user_id}).sort("created_at", -1)
        
        for conversation in florence_responses:
            # Extract summary from assessment result
            assessment_result = conversation.get("assessment_result", {})
            summary_text = "AI conversation completed"
            
            if assessment_result and isinstance(assessment_result, dict):
                summary_text = assessment_result.get("assessment_summary", "AI conversation completed")
                if len(summary_text) > 100:
                    summary_text = summary_text[:100] + "..."
            
            # Count conversation messages
            conv_history = conversation.get("conversation_history", [])
            message_count = len(conv_history)
            user_messages = len([msg for msg in conv_history if msg.get("role") == "user"])
            
            assessment = {
                "id": str(conversation.get("_id")),
                "type": "florence_conversation",
                "date": conversation.get("created_at", "Unknown"),
                "title": "Florence AI Chat",
                "summary": summary_text,
                "data": {
                    "total_messages": message_count,
                    "user_messages": user_messages,
                    "symptoms_assessed": conversation.get("symptoms_assessed", []),
                    "ai_powered": conversation.get("ai_powered", False),
                    "conversation_length": message_count,
                    "session_id": conversation.get("session_id"),
                    "assessment_summary": assessment_result
                },
                "icon": "fa-robot",
                "color": "#3b82f6"
            }
            unified_assessments.append(assessment)
        
        # Sort all assessments by date (newest first)
        unified_assessments.sort(key=lambda x: x["date"] if x["date"] != "Unknown" else "", reverse=True)
        
        return {
            "success": True,
            "total_assessments": len(unified_assessments),
            "daily_checkins": len([a for a in unified_assessments if a["type"] == "daily_checkin"]),
            "florence_conversations": len([a for a in unified_assessments if a["type"] == "florence_conversation"]),
            "assessments": unified_assessments
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch unified assessments: {str(e)}"
        )

@app.get("/assessment/{assessment_id}")
async def get_assessment_by_id(assessment_id: str, user = Depends(get_user), db = Depends(get_db)):
    """
    Get a specific assessment (Florence conversation or daily check-in) by ID
    """
    try:
        from bson import ObjectId
        
        user_id = user['username']
        
        # Try to find in Florence assessments first
        florence_collection = db["florence_assessments"]
        florence_assessment = florence_collection.find_one({
            "_id": ObjectId(assessment_id),
            "user_id": user_id
        })
        
        if florence_assessment:
            # Return detailed Florence assessment data
            assessment_result = florence_assessment.get("assessment_result", {})
            conversation_history = florence_assessment.get("conversation_history", [])
            
            # Extract symptom data from conversation and assessment
            symptoms_data = {}
            symptoms_assessed = florence_assessment.get("symptoms_assessed", [])
            
            # Mock symptom severity/frequency data (in real implementation, this would be parsed from conversation)
            symptom_mappings = {
                "fatigue": {"name": "Energy Level", "icon": "⚡"},
                "appetite": {"name": "Appetite", "icon": "🍽️"},
                "nausea": {"name": "Nausea", "icon": "🤢"},
                "cough": {"name": "Cough", "icon": "💨"},
                "discomfort": {"name": "Pain", "icon": "💊"}
            }
            
            for symptom in symptoms_assessed:
                if symptom in symptom_mappings:
                    # Mock data - in real implementation, extract from conversation
                    symptoms_data[symptom] = {
                        "name": symptom_mappings[symptom]["name"],
                        "icon": symptom_mappings[symptom]["icon"],
                        "frequency": 3,  # Mock value 1-5
                        "intensity": 3,  # Mock value 1-5
                        "discussed": True
                    }
            
            return {
                "success": True,
                "assessment": {
                    "id": str(florence_assessment.get("_id")),
                    "type": "florence_conversation",
                    "title": "Florence AI Assessment",
                    "date": florence_assessment.get("created_at", "Unknown"),
                    "completed_date": florence_assessment.get("completed_at"),
                    "user_id": user_id,
                    "session_id": florence_assessment.get("session_id"),
                    "conversation_length": len(conversation_history),
                    "ai_powered": florence_assessment.get("ai_powered", False),
                    "symptoms_tracked": len(symptoms_assessed),
                    "symptoms_assessed": symptoms_assessed,
                    "symptoms_data": symptoms_data,
                    "conversation_history": conversation_history,
                    "assessment_result": assessment_result,
                    "avg_severity": sum([s.get("intensity", 0) for s in symptoms_data.values()]) / max(len(symptoms_data), 1),
                    "alerts_today": 0,  # Mock data
                    "treatment_status": "Currently in Treatment"  # Mock data
                }
            }
        
        # Try to find in daily check-ins (answers collection)
        answers_collection = db["answers"]
        daily_assessment = answers_collection.find_one({
            "_id": ObjectId(assessment_id),
            "user_id": user_id
        })
        
        if daily_assessment:
            return {
                "success": True,
                "assessment": {
                    "id": str(daily_assessment.get("_id")),
                    "type": "daily_checkin",
                    "title": "Daily Check-in",
                    "date": daily_assessment.get("timestamp", "Unknown"),
                    "user_id": user_id,
                    "answers": daily_assessment.get("answers", []),
                    "questions_answered": len(daily_assessment.get("answers", [])),
                    "completion_time": daily_assessment.get("timestamp")
                }
            }
        
        # Assessment not found
        raise HTTPException(status_code=404, detail="Assessment not found")
        
    except Exception as e:
        if "invalid ObjectId" in str(e).lower():
            raise HTTPException(status_code=400, detail="Invalid assessment ID format")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch assessment: {str(e)}"
        )


