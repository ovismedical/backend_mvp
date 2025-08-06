from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from .triage_simple import TriageService
from .login import get_user, get_db
import os
from datetime import datetime
from pymongo.errors import PyMongoError

triage_router = APIRouter(prefix="/triage", tags=["triage"])

# Database models for storing triage history
class TriageHistory(BaseModel):
    interview_id: str
    username: str
    age: int
    sex: str
    chief_complaint: Optional[str]
    questions_answered: int
    triage_result: Optional[Dict] = None
    completed: bool = False
    created_at: datetime
    completed_at: Optional[datetime] = None

# Request models
class StartTriageRequest(BaseModel):
    age: int = Field(..., ge=0, le=130, description="Patient age")
    sex: str = Field(..., regex="^(male|female)$", description="Patient sex")
    chief_complaint: Optional[str] = Field(None, description="Main symptom or complaint")

class AnswerRequest(BaseModel):
    interview_id: str = Field(..., description="Interview session ID")
    answers: List[Dict[str, str]] = Field(..., description="Question answers")

class UpdateTriageRequest(BaseModel):
    notes: Optional[str] = Field(None, description="Additional notes")
    urgency_override: Optional[str] = Field(None, description="Manual urgency override")

def get_triage_service():
    """Get Infermedica triage service instance"""
    app_id = os.getenv("INFERMEDICA_APP_ID")
    app_key = os.getenv("INFERMEDICA_APP_KEY")
    
    if not app_id or not app_key:
        raise HTTPException(
            status_code=500,
            detail="Infermedica API credentials not configured. Set INFERMEDICA_APP_ID and INFERMEDICA_APP_KEY environment variables."
        )
    
    return TriageService(app_id, app_key)

@triage_router.post("/start")
async def start_triage(
    request: StartTriageRequest,
    user = Depends(get_user),
    db = Depends(get_db),
    service: TriageService = Depends(get_triage_service)
):
    """Start a new triage interview for authenticated user"""
    try:
        result = service.start_interview(
            age=request.age,
            sex=request.sex,
            chief_complaint=request.chief_complaint
        )
        
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        
        # Store triage session in database
        triage_history = {
            "interview_id": result["interview_id"],
            "username": user["username"],
            "age": request.age,
            "sex": request.sex,
            "chief_complaint": request.chief_complaint,
            "questions_answered": 0,
            "triage_result": None,
            "completed": False,
            "created_at": datetime.utcnow(),
            "completed_at": None
        }
        
        db["triage_history"].insert_one(triage_history)
        
        return {
            "success": True,
            "interview_id": result["interview_id"],
            "question": result.get("question"),
            "should_stop": result.get("should_stop", False),
            "status": result["status"],
            "user": user["username"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start triage: {str(e)}")

@triage_router.post("/answer")
async def answer_triage_question(
    request: AnswerRequest,
    user = Depends(get_user),
    db = Depends(get_db),
    service: TriageService = Depends(get_triage_service)
):
    """Answer a triage question for authenticated user"""
    try:
        # Validate user owns this interview
        interview_record = db["triage_history"].find_one({
            "interview_id": request.interview_id,
            "username": user["username"]
        })
        
        if not interview_record:
            raise HTTPException(
                status_code=404, 
                detail="Interview not found or access denied"
            )
        
        # Validate answers
        for answer in request.answers:
            if "id" not in answer or "choice_id" not in answer:
                raise HTTPException(
                    status_code=400,
                    detail="Each answer must have 'id' and 'choice_id'"
                )
            
            if answer["choice_id"] not in ["present", "absent", "unknown"]:
                raise HTTPException(
                    status_code=400,
                    detail="choice_id must be: present, absent, or unknown"
                )
        
        result = service.answer_question(request.interview_id, request.answers)
        
        if "error" in result:
            if "not found" in result["error"].lower():
                raise HTTPException(status_code=404, detail=result["error"])
            else:
                raise HTTPException(status_code=400, detail=result["error"])
        
        # Update database record
        update_data = {
            "questions_answered": interview_record["questions_answered"] + len(request.answers)
        }
        
        if result.get("completed"):
            update_data.update({
                "completed": True,
                "triage_result": result.get("triage_result"),
                "completed_at": datetime.utcnow()
            })
        
        db["triage_history"].update_one(
            {"interview_id": request.interview_id},
            {"$set": update_data}
        )
        
        response = {
            "success": True,
            "interview_id": request.interview_id,
            "completed": result.get("completed", False),
            "status": result["status"],
            "user": user["username"]
        }
        
        if result.get("completed"):
            response.update({
                "triage_result": result.get("triage_result")
            })
        else:
            response.update({
                "question": result.get("question")
            })
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to answer question: {str(e)}")

@triage_router.get("/question/{interview_id}")
async def get_next_question(
    interview_id: str,
    user = Depends(get_user),
    db = Depends(get_db),
    service: TriageService = Depends(get_triage_service)
):
    """Get next question or final triage result for authenticated user"""
    try:
        # Validate user owns this interview
        interview_record = db["triage_history"].find_one({
            "interview_id": interview_id,
            "username": user["username"]
        })
        
        if not interview_record:
            raise HTTPException(
                status_code=404, 
                detail="Interview not found or access denied"
            )
        
        result = service.get_next_question(interview_id)
        
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        
        return {
            "success": True,
            "interview_id": interview_id,
            "completed": result.get("completed", False),
            "question": result.get("question"),
            "triage_result": result.get("triage_result") if result.get("completed") else None,
            "status": result["status"],
            "user": user["username"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get question: {str(e)}")

@triage_router.get("/status/{interview_id}")
async def get_triage_status(
    interview_id: str,
    user = Depends(get_user),
    db = Depends(get_db),
    service: TriageService = Depends(get_triage_service)
):
    """Get interview status for authenticated user"""
    try:
        # Get from database first
        interview_record = db["triage_history"].find_one({
            "interview_id": interview_id,
            "username": user["username"]
        }, {"_id": 0})
        
        if not interview_record:
            raise HTTPException(
                status_code=404, 
                detail="Interview not found or access denied"
            )
        
        # Get live status from service if still active
        if not interview_record["completed"]:
            live_status = service.get_status(interview_id)
            if "error" not in live_status:
                interview_record.update(live_status)
        
        return {"success": True, **interview_record}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")

@triage_router.post("/complete/{interview_id}")
async def complete_triage(
    interview_id: str,
    user = Depends(get_user),
    db = Depends(get_db),
    service: TriageService = Depends(get_triage_service)
):
    """Complete triage interview early for authenticated user"""
    try:
        # Validate user owns this interview
        interview_record = db["triage_history"].find_one({
            "interview_id": interview_id,
            "username": user["username"]
        })
        
        if not interview_record:
            raise HTTPException(
                status_code=404, 
                detail="Interview not found or access denied"
            )
        
        result = service.complete_interview(interview_id)
        
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        
        # Update database record
        db["triage_history"].update_one(
            {"interview_id": interview_id},
            {"$set": {
                "completed": True,
                "triage_result": result.get("triage_result"),
                "completed_at": datetime.utcnow()
            }}
        )
        
        return {"success": True, "user": user["username"], **result}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to complete triage: {str(e)}")

@triage_router.get("/history")
async def get_triage_history(
    user = Depends(get_user),
    db = Depends(get_db),
    limit: int = 10,
    skip: int = 0
):
    """Get triage history for authenticated user"""
    try:
        history = list(db["triage_history"].find(
            {"username": user["username"]},
            {"_id": 0}
        ).sort("created_at", -1).skip(skip).limit(limit))
        
        total_count = db["triage_history"].count_documents(
            {"username": user["username"]}
        )
        
        return {
            "success": True,
            "history": history,
            "total_count": total_count,
            "returned_count": len(history),
            "user": user["username"]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get triage history: {str(e)}")

@triage_router.get("/active")
async def get_active_interviews(
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Get active interview sessions for authenticated user"""
    try:
        active_interviews = list(db["triage_history"].find(
            {"username": user["username"], "completed": False},
            {"_id": 0}
        ).sort("created_at", -1))
        
        return {
            "success": True,
            "active_interviews": active_interviews,
            "count": len(active_interviews),
            "user": user["username"]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get active interviews: {str(e)}")

@triage_router.delete("/history/{interview_id}")
async def delete_triage_record(
    interview_id: str,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Delete a triage record for authenticated user"""
    try:
        result = db["triage_history"].delete_one({
            "interview_id": interview_id,
            "username": user["username"]
        })
        
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=404,
                detail="Triage record not found or access denied"
            )
        
        return {
            "success": True,
            "message": "Triage record deleted successfully",
            "interview_id": interview_id,
            "user": user["username"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete triage record: {str(e)}")

@triage_router.get("/health")
async def health_check():
    """Health check for triage service"""
    try:
        get_triage_service()
        return {
            "status": "healthy",
            "service": "triage",
            "authenticated": True,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }