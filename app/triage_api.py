"""
Triage API endpoints for fetching triage assessment data
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Optional
from datetime import datetime, timezone
from .login import get_user, get_db

trierouter = APIRouter(prefix="/triage", tags=["triage"])

@trierouter.get("/history/{patient_id}")
async def get_triage_history(
    patient_id: str,
    limit: int = 10,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """
    Get triage history for a patient
    """
    try:
        # Verify user has access to this patient's data
        if user['username'] != patient_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get triage assessments from florence_assessments collection
        collection = db["florence_assessments"]
        
        # Find assessments with triage data for this patient
        assessments = collection.find(
            {
                "user_id": patient_id,
                "triage_assessment": {"$exists": True, "$ne": None}
            }
        ).sort("created_at", -1).limit(limit)
        
        triage_history = []
        for assessment in assessments:
            triage_data = assessment.get("triage_assessment", {})
            if triage_data:
                triage_history.append({
                    "session_id": assessment.get("session_id"),
                    "timestamp": triage_data.get("timestamp"),
                    "alert_level": triage_data.get("alert_level"),
                    "alert_rationale": triage_data.get("alert_rationale"),
                    "recommended_timeline": triage_data.get("recommended_timeline"),
                    "confidence_level": triage_data.get("confidence_level"),
                    "key_symptoms": triage_data.get("key_symptoms", []),
                    "diagnosis_predictions": triage_data.get("diagnosis_predictions", []),
                    "clinical_reasoning": triage_data.get("clinical_reasoning"),
                    "treatment_status": triage_data.get("treatment_status"),
                    "created_at": assessment.get("created_at")
                })
        
        return {
            "success": True,
            "patient_id": patient_id,
            "triage_history": triage_history,
            "count": len(triage_history)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching triage history: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch triage history: {str(e)}")

@trierouter.get("/latest/{patient_id}")
async def get_latest_triage(
    patient_id: str,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """
    Get the latest triage assessment for a patient
    """
    try:
        # Allow demo_patient access without authentication for testing
        if patient_id == "demo_patient":
            pass  # Skip authentication check for demo
        else:
            # Verify user has access to this patient's data
            if user['username'] != patient_id:
                raise HTTPException(status_code=403, detail="Access denied")
        
        # Get latest triage assessment
        collection = db["florence_assessments"]
        
        # Find the most recent assessment with triage data
        assessment = collection.find_one(
            {
                "user_id": patient_id,
                "triage_assessment": {"$exists": True, "$ne": None}
            },
            sort=[("created_at", -1)]
        )
        
        if not assessment:
            raise HTTPException(status_code=404, detail="No triage assessment found")
        
        triage_data = assessment.get("triage_assessment", {})
        structured_data = assessment.get("structured_assessment", {})
        
        return {
            "success": True,
            "patient_id": patient_id,
            "triage_assessment": triage_data,
            "structured_assessment": structured_data,
            "session_id": assessment.get("session_id"),
            "created_at": assessment.get("created_at")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching latest triage: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch latest triage: {str(e)}")

@trierouter.get("/session/{session_id}")
async def get_triage_by_session(
    session_id: str,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """
    Get triage assessment for a specific session
    """
    try:
        # Get the assessment
        collection = db["florence_assessments"]
        assessment = collection.find_one({"session_id": session_id})
        
        if not assessment:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Verify user has access to this session
        if user['username'] != assessment.get("user_id"):
            raise HTTPException(status_code=403, detail="Access denied")
        
        triage_data = assessment.get("triage_assessment")
        if not triage_data:
            raise HTTPException(status_code=404, detail="No triage data found for this session")
        
        return {
            "success": True,
            "session_id": session_id,
            "triage_assessment": triage_data,
            "created_at": assessment.get("created_at")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching session triage: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch session triage: {str(e)}")

@trierouter.get("/stats/{patient_id}")
async def get_triage_stats(
    patient_id: str,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """
    Get triage statistics for a patient
    """
    try:
        # Verify user has access to this patient's data
        if user['username'] != patient_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        collection = db["florence_assessments"]
        
        # Get all assessments with triage data
        assessments = list(collection.find(
            {
                "user_id": patient_id,
                "triage_assessment": {"$exists": True, "$ne": None}
            }
        ))
        
        if not assessments:
            return {
                "success": True,
                "patient_id": patient_id,
                "stats": {
                    "total_assessments": 0,
                    "alert_levels": {},
                    "most_common_symptoms": [],
                    "average_confidence": 0
                }
            }
        
        # Calculate statistics
        alert_levels = {}
        all_symptoms = []
        confidence_scores = []
        
        for assessment in assessments:
            triage_data = assessment.get("triage_assessment", {})
            
            # Count alert levels
            alert_level = triage_data.get("alert_level", "UNKNOWN")
            alert_levels[alert_level] = alert_levels.get(alert_level, 0) + 1
            
            # Collect symptoms
            symptoms = triage_data.get("key_symptoms", [])
            all_symptoms.extend(symptoms)
            
            # Collect confidence levels
            confidence = triage_data.get("confidence_level", "medium")
            confidence_map = {"low": 1, "medium": 2, "high": 3}
            confidence_scores.append(confidence_map.get(confidence, 2))
        
        # Count most common symptoms
        symptom_counts = {}
        for symptom in all_symptoms:
            symptom_counts[symptom] = symptom_counts.get(symptom, 0) + 1
        
        most_common_symptoms = sorted(
            symptom_counts.items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:5]
        
        # Calculate average confidence
        avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0
        
        return {
            "success": True,
            "patient_id": patient_id,
            "stats": {
                "total_assessments": len(assessments),
                "alert_levels": alert_levels,
                "most_common_symptoms": [{"symptom": s, "count": c} for s, c in most_common_symptoms],
                "average_confidence": round(avg_confidence, 2)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching triage stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch triage stats: {str(e)}")

@trierouter.get("/insights/{patient_id}")
async def get_smart_insights(
    patient_id: str,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """
    Get smart insights based on triage and structured assessment data
    """
    try:
        # Verify user has access to this patient's data
        if user['username'] != patient_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get latest assessment data
        collection = db["florence_assessments"]
        assessment = collection.find_one(
            {
                "user_id": patient_id,
                "triage_assessment": {"$exists": True, "$ne": None}
            },
            sort=[("created_at", -1)]
        )
        
        if not assessment:
            return {
                "success": True,
                "patient_id": patient_id,
                "insights": []
            }
        
        triage_data = assessment.get("triage_assessment", {})
        structured_data = assessment.get("structured_assessment", {})
        
        # Generate insights based on data
        insights = generate_smart_insights(triage_data, structured_data)
        
        return {
            "success": True,
            "patient_id": patient_id,
            "insights": insights
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error generating smart insights: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate smart insights: {str(e)}")

def generate_smart_insights(triage_data, structured_data):
    """Generate smart insights based on triage and structured assessment data"""
    insights = []
    
    # Debug logging
    print(f"🔍 Debug - triage_data type: {type(triage_data)}")
    print(f"🔍 Debug - structured_data type: {type(structured_data)}")
    print(f"🔍 Debug - structured_data keys: {structured_data.keys() if isinstance(structured_data, dict) else 'Not a dict'}")
    
    # Analyze alert level
    alert_level = triage_data.get("alert_level") if isinstance(triage_data, dict) else None
    if alert_level == "GREEN":
        insights.append({
            "icon": "check_circle",
            "title": "all_good",
            "description": "all_good_description",
            "insightType": "success"
        })
    elif alert_level == "YELLOW":
        insights.append({
            "icon": "warning",
            "title": "monitor_closely",
            "description": "monitor_closely_description",
            "insightType": "warning"
        })
    elif alert_level in ["ORANGE", "RED"]:
        insights.append({
            "icon": "error",
            "title": "attention_needed",
            "description": "attention_needed_description",
            "insightType": "error"
        })
    
    # Analyze symptoms - handle both dict and list formats
    symptoms = []
    if isinstance(structured_data, dict):
        symptoms_raw = structured_data.get("symptoms", [])
        print(f"🔍 Debug - symptoms_raw type: {type(symptoms_raw)}")
        print(f"🔍 Debug - symptoms_raw content: {symptoms_raw}")
        
        if isinstance(symptoms_raw, list):
            symptoms = symptoms_raw
        elif isinstance(symptoms_raw, dict):
            # Convert dict format to list format
            symptoms = []
            for symptom_name, symptom_data in symptoms_raw.items():
                if isinstance(symptom_data, dict):
                    symptoms.append({
                        "symptom": symptom_name,
                        "severity": symptom_data.get("severity", "unknown"),
                        "frequency": symptom_data.get("frequency", "unknown"),
                        "duration": symptom_data.get("duration", "unknown"),
                        "impact": symptom_data.get("impact", "unknown")
                    })
                else:
                    # If symptom_data is not a dict, treat it as a simple symptom
                    symptoms.append({
                        "symptom": symptom_name,
                        "severity": "unknown"
                    })
    
    print(f"🔍 Debug - processed symptoms: {symptoms}")
    
    if symptoms:
        # Check for mood-sleep correlation
        mood_symptoms = []
        sleep_symptoms = []
        
        for s in symptoms:
            if isinstance(s, dict):
                symptom_name = s.get("symptom", "").lower()
                if any(keyword in symptom_name for keyword in ["mood", "depression", "anxiety"]):
                    mood_symptoms.append(s)
                if any(keyword in symptom_name for keyword in ["sleep", "insomnia", "restless"]):
                    sleep_symptoms.append(s)
        
        if mood_symptoms and sleep_symptoms:
            insights.append({
                "icon": "psychology",
                "title": "mood_sleep_correlation",
                "description": "mood_sleep_correlation_description",
                "insightType": "info"
            })
        
        # Check for high severity symptoms
        high_severity = []
        for s in symptoms:
            if isinstance(s, dict):
                severity = s.get("severity", "").lower()
                if severity in ["severe", "high"]:
                    high_severity.append(s)
        
        if high_severity:
            insights.append({
                "icon": "warning",
                "title": "high_severity_symptoms",
                "description": "high_severity_symptoms_description",
                "insightType": "warning"
            })
        
        # Check for improvement trends
        mild_symptoms = []
        for s in symptoms:
            if isinstance(s, dict):
                severity = s.get("severity", "").lower()
                if severity in ["mild", "low"]:
                    mild_symptoms.append(s)
        
        if len(mild_symptoms) >= len(symptoms) * 0.7:
            insights.append({
                "icon": "trending_up",
                "title": "symptoms_improving",
                "description": "symptoms_improving_description",
                "insightType": "success"
            })
    
    # Analyze diagnoses
    diagnoses = []
    if isinstance(triage_data, dict):
        diagnoses = triage_data.get("diagnosis_predictions", [])
    
    if diagnoses:
        high_urgency = []
        for d in diagnoses:
            if isinstance(d, dict):
                urgency = d.get("urgency", 0)
                if urgency >= 4:
                    high_urgency.append(d)
        
        if high_urgency:
            insights.append({
                "icon": "medical_services",
                "title": "high_priority_conditions",
                "description": "high_priority_conditions_description",
                "insightType": "error"
            })
    
    # Ensure we have at least 2 insights
    if len(insights) < 2:
        insights.extend([
            {
                "icon": "info",
                "title": "general_health_tip",
                "description": "general_health_tip_description",
                "insightType": "info"
            },
            {
                "icon": "favorite",
                "title": "wellness_reminder",
                "description": "wellness_reminder_description",
                "insightType": "success"
            }
        ])
    
    return insights[:4]  # Limit to 4 insights

@trierouter.get("/demo/latest")
async def get_demo_triage_latest(db = Depends(get_db)):
    """
    Get the latest triage assessment for demo_patient (no authentication required)
    """
    try:
        # Get latest triage assessment for demo_patient
        collection = db["florence_assessments"]
        
        # Find the most recent assessment with triage data
        assessment = collection.find_one(
            {
                "user_id": "demo_patient",
                "triage_assessment": {"$exists": True, "$ne": None}
            },
            sort=[("created_at", -1)]
        )
        
        if not assessment:
            raise HTTPException(status_code=404, detail="No demo triage assessment found")
        
        triage_data = assessment.get("triage_assessment", {})
        
        return {
            "success": True,
            "patient_id": "demo_patient",
            "triage_assessment": triage_data,
            "session_id": assessment.get("session_id"),
            "created_at": assessment.get("created_at")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching demo triage: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch demo triage: {str(e)}")
