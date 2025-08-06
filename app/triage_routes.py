from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from .infermedica_api import InfermedicaService, PatientInfo, Evidence, Sex, EvidenceChoiceId
import os
from datetime import datetime

triage_router = APIRouter(prefix="/triage", tags=["triage"])

# Pydantic models for triage requests
class SymptomEvidence(BaseModel):
    symptom_id: str = Field(..., description="Infermedica symptom ID")
    choice: str = Field(..., description="present, absent, or unknown")
    source: str = Field(default="initial", description="Source of evidence")

class TriageRequest(BaseModel):
    age: int = Field(..., ge=0, le=130, description="Patient age")
    sex: str = Field(..., regex="^(male|female)$", description="Patient sex")
    symptoms: List[SymptomEvidence] = Field(..., description="List of symptoms with evidence")
    
class TriageTextRequest(BaseModel):
    age: int = Field(..., ge=0, le=130, description="Patient age")
    sex: str = Field(..., regex="^(male|female)$", description="Patient sex")
    symptom_text: str = Field(..., description="Natural language description of symptoms")

# Initialize Infermedica service
def get_infermedica_service():
    app_id = os.getenv("INFERMEDICA_APP_ID")
    app_key = os.getenv("INFERMEDICA_APP_KEY")
    
    if not app_id or not app_key:
        raise HTTPException(
            status_code=500,
            detail="Infermedica API credentials not configured"
        )
    
    return InfermedicaService(app_id, app_key)

@triage_router.post("/assess")
async def assess_triage(
    request: TriageRequest,
    infermedica: InfermedicaService = Depends(get_infermedica_service)
):
    """
    Assess triage level based on patient symptoms
    Returns urgency level and recommendations
    """
    try:
        # Convert symptoms to evidence format
        evidence = []
        for symptom in request.symptoms:
            evidence.append(Evidence(
                id=symptom.symptom_id,
                choice_id=EvidenceChoiceId(symptom.choice),
                source=symptom.source
            ))
        
        # Create patient info
        patient_info = PatientInfo(
            sex=Sex(request.sex.lower()),
            age={"value": request.age, "unit": "year"},
            evidence=evidence
        )
        
        # Get triage assessment
        triage_result = infermedica.api.get_triage(patient_info)
        
        # Parse and format the response
        response = {
            "triage_level": triage_result.get("triage_level"),
            "description": triage_result.get("description"),
            "recommendations": triage_result.get("recommendations", []),
            "urgency_score": triage_result.get("urgency_score"),
            "patient_info": {
                "age": request.age,
                "sex": request.sex,
                "symptoms_count": len(request.symptoms)
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
        return response
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Triage assessment failed: {str(e)}"
        )

@triage_router.post("/assess-text")
async def assess_triage_from_text(
    request: TriageTextRequest,
    infermedica: InfermedicaService = Depends(get_infermedica_service)
):
    """
    Assess triage level from natural language symptom description
    """
    try:
        # Parse symptoms from text
        parsed = infermedica.api.parse_text(request.symptom_text)
        
        # Convert parsed mentions to evidence
        evidence = []
        for mention in parsed.get("mentions", []):
            evidence.append(Evidence(
                id=mention["id"],
                choice_id=EvidenceChoiceId.PRESENT,
                source="initial"
            ))
        
        if not evidence:
            raise HTTPException(
                status_code=400,
                detail="No medical symptoms detected in the provided text"
            )
        
        # Create patient info
        patient_info = PatientInfo(
            sex=Sex(request.sex.lower()),
            age={"value": request.age, "unit": "year"},
            evidence=evidence
        )
        
        # Get triage assessment
        triage_result = infermedica.api.get_triage(patient_info)
        
        response = {
            "triage_level": triage_result.get("triage_level"),
            "description": triage_result.get("description"),
            "recommendations": triage_result.get("recommendations", []),
            "urgency_score": triage_result.get("urgency_score"),
            "parsed_symptoms": [
                {
                    "name": mention["name"],
                    "id": mention["id"],
                    "common_name": mention.get("common_name")
                }
                for mention in parsed.get("mentions", [])
            ],
            "original_text": request.symptom_text,
            "patient_info": {
                "age": request.age,
                "sex": request.sex
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Text-based triage assessment failed: {str(e)}"
        )

@triage_router.post("/urgent-check")
async def check_urgent_symptoms(
    request: TriageTextRequest,
    infermedica: InfermedicaService = Depends(get_infermedica_service)
):
    """
    Quick check for urgent symptoms that require immediate medical attention
    Returns boolean indicating if immediate care is needed
    """
    try:
        # Use the existing text-based triage
        result = await assess_triage_from_text(request, infermedica)
        
        triage_level = result.get("triage_level")
        
        # Define urgent levels (adjust based on Infermedica's triage levels)
        urgent_levels = ["emergency", "consultation_24", "urgent"]
        
        is_urgent = triage_level in urgent_levels
        
        return {
            "is_urgent": is_urgent,
            "triage_level": triage_level,
            "description": result.get("description"),
            "immediate_recommendations": result.get("recommendations", [])[:3],  # Top 3 recommendations
            "detected_symptoms": [s["name"] for s in result.get("parsed_symptoms", [])],
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Urgent symptom check failed: {str(e)}"
        )

@triage_router.get("/levels")
async def get_triage_levels():
    """
    Get available triage levels and their descriptions
    """
    return {
        "triage_levels": [
            {
                "level": "emergency",
                "description": "Requires immediate emergency care",
                "action": "Call emergency services or go to ER immediately"
            },
            {
                "level": "consultation_24",
                "description": "Should see a doctor within 24 hours",
                "action": "Schedule urgent medical consultation"
            },
            {
                "level": "consultation",
                "description": "Should see a doctor soon",
                "action": "Schedule medical consultation within a few days"
            },
            {
                "level": "self_care",
                "description": "Can likely be managed with self-care",
                "action": "Monitor symptoms and use appropriate self-care measures"
            }
        ]
    }

# Health check endpoint
@triage_router.get("/health")
async def triage_health_check():
    """Check if triage service is operational"""
    try:
        infermedica = get_infermedica_service()
        return {
            "status": "healthy",
            "service": "infermedica_triage",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }