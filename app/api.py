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
from collections import defaultdict
import statistics

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
            
            # Get oncologist notification level for alert coloring
            oncologist_level = conversation.get("oncologist_notification_level", "none")
            flag_for_oncologist = conversation.get("flag_for_oncologist", False)
            
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
                    "assessment_summary": assessment_result,
                    "oncologist_notification_level": oncologist_level,
                    "flag_for_oncologist": flag_for_oncologist
                },
                "icon": "fa-robot",
                "color": "#3b82f6",
                "oncologist_notification_level": oncologist_level,
                "flag_for_oncologist": flag_for_oncologist
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
            structured_assessment = florence_assessment.get("structured_assessment", {})
            
            # Extract symptom data from structured assessment if available
            symptoms_data = {}
            
            # Check if we have structured assessment data (new format)
            if structured_assessment and "symptoms" in structured_assessment:
                # Use real structured assessment data
                structured_symptoms = structured_assessment["symptoms"]
                symptoms_assessed = list(structured_symptoms.keys())  # Get all symptoms from structured data
                symptom_mappings = {
                    "fatigue": {"name": "Energy Level", "icon": "⚡"},
                    "lack_of_appetite": {"name": "Appetite", "icon": "🍽️"},
                    "nausea": {"name": "Nausea", "icon": "🤢"},
                    "cough": {"name": "Cough", "icon": "💨"},
                    "pain": {"name": "Pain", "icon": "💊"}
                }
                
                for symptom_key, symptom_data in structured_symptoms.items():
                    if symptom_key in symptom_mappings:
                        symptoms_data[symptom_key] = {
                            "name": symptom_mappings[symptom_key]["name"],
                            "icon": symptom_mappings[symptom_key]["icon"],
                            "frequency": symptom_data.get("frequency_rating", 1),
                            "intensity": symptom_data.get("severity_rating", 1),
                            "key_indicators": symptom_data.get("key_indicators", []),
                            "additional_notes": symptom_data.get("additional_notes", ""),
                            "discussed": True
                        }
            else:
                # Fallback for assessments without structured data
                symptoms_assessed = florence_assessment.get("symptoms_assessed", [])
                for symptom in symptoms_assessed:
                    symptoms_data[symptom] = {
                        "name": symptom.replace('_', ' ').title(),
                        "icon": "📊",
                        "frequency": 1,
                        "intensity": 1,
                        "key_indicators": [],
                        "additional_notes": "No detailed data available",
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
                    "ai_powered": florence_assessment.get("ai_powered", False),
                    "symptoms_tracked": len(symptoms_assessed),
                    "symptoms_assessed": symptoms_assessed,
                    "symptoms_data": symptoms_data,
                    "avg_severity": sum([s.get("intensity", 0) for s in symptoms_data.values()]) / max(len(symptoms_data), 1),
                    "alerts_today": 1 if florence_assessment.get("flag_for_oncologist", False) else 0,
                    "treatment_status": structured_assessment.get("treatment_status", "Currently in Treatment"),
                    "oncologist_notification_level": florence_assessment.get("oncologist_notification_level", "none"),
                    "flag_for_oncologist": florence_assessment.get("flag_for_oncologist", False)
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

@app.get("/weekly_analytics")
async def get_weekly_analytics(week_offset: int = 0, user = Depends(get_user), db = Depends(get_db)):
    """
    Get comprehensive weekly Florence assessment data with trends, statistics, and insights
    
    Args:
        week_offset: Number of weeks back from current week (0 = current week, 1 = last week, etc.)
    """
    try:
        user_id = user['username']
        florence_collection = db["florence_assessments"]
        
        # Get target week date range based on offset
        today = datetime.now()
        current_week_start = today - timedelta(days=today.weekday())
        target_week_start = current_week_start - timedelta(weeks=week_offset)
        target_week_end = target_week_start + timedelta(days=6)
        
        # Query assessments from the target week
        florence_responses = list(florence_collection.find({
            "user_id": user_id,
            "created_at": {
                "$gte": target_week_start.isoformat(),
                "$lte": target_week_end.isoformat()
            }
        }).sort("created_at", 1))
        
        # Calculate week label for display
        if week_offset == 0:
            week_label = "This Week"
        elif week_offset == 1:
            week_label = "Last Week"
        else:
            week_label = f"{week_offset} Weeks Ago"
        
        if not florence_responses:
            return {
                "success": True,
                "data": {
                    "totalAssessments": 0,
                    "totalAlerts": 0,
                    "mostConcerningSymptom": None,
                    "overallTrend": "No Data",
                    "dailyData": [],
                    "symptomTrends": {},
                    "avgSeverityBySymptom": {},
                    "alertDistribution": {"none": 0, "amber": 0, "red": 0},
                    "insights": [{
                        "id": "no_data",
                        "type": "info",
                        "icon": "fa-info-circle",
                        "title": "No Data Available",
                        "description": f"No Florence assessments found for {week_label.lower()}. Complete some assessments to see analytics."
                    }],
                    "weekRange": {
                        "start": target_week_start.strftime("%Y-%m-%d"),
                        "end": target_week_end.strftime("%Y-%m-%d")
                    },
                    "weekLabel": week_label,
                    "weekOffset": week_offset
                }
            }
        
        # Initialize tracking variables
        daily_data = []
        symptom_trends = defaultdict(dict)
        avg_severity_by_symptom = defaultdict(list)
        alert_distribution = {"none": 0, "amber": 0, "red": 0}
        total_alerts = 0
        insights = []
        
        # Generate 7 days of data (even if no assessments on some days)
        for i in range(7):
            current_day = target_week_start + timedelta(days=i)
            day_str = current_day.strftime("%Y-%m-%d")
            
            # Find assessments for this day
            day_assessments = [
                a for a in florence_responses 
                if a.get("created_at", "").startswith(day_str)
            ]
            
            if day_assessments:
                # Calculate average severity for the day
                daily_severities = []
                day_symptoms = defaultdict(list)
                
                for assessment in day_assessments:
                    structured = assessment.get("structured_assessment", {})
                    symptoms = structured.get("symptoms", {})
                    
                    if symptoms:  # Only process if symptoms exist
                        for symptom_name, symptom_data in symptoms.items():
                            severity = symptom_data.get("severity_rating", 0)
                            frequency = symptom_data.get("frequency_rating", 0)
                            if severity > 0:  # Only count non-zero severities
                                daily_severities.append(severity)
                                day_symptoms[symptom_name].append(severity)
                                avg_severity_by_symptom[symptom_name].append(severity)
                                
                                # Store for trends
                                symptom_trends[symptom_name][day_str] = severity
                    
                    # Count alerts
                    alert_level = assessment.get("oncologist_notification_level", "none")
                    if alert_level in alert_distribution:
                        alert_distribution[alert_level] += 1
                    if assessment.get("flag_for_oncologist", False):
                        total_alerts += 1
                
                avg_severity = statistics.mean(daily_severities) if daily_severities else 0
                
                daily_data.append({
                    "date": day_str,
                    "avgSeverity": round(avg_severity, 1),
                    "assessmentCount": len(day_assessments),
                    "hasData": True
                })
            else:
                daily_data.append({
                    "date": day_str,
                    "avgSeverity": 0,
                    "assessmentCount": 0,
                    "hasData": False
                })
        
        # Calculate overall statistics
        total_assessments = len(florence_responses)
        
        # Find most concerning symptom
        most_concerning_symptom = None
        highest_avg_severity = 0
        
        for symptom, severities in avg_severity_by_symptom.items():
            if severities:  # Only process if we have data
                avg_sev = statistics.mean(severities)
                if avg_sev > highest_avg_severity:
                    highest_avg_severity = avg_sev
                    most_concerning_symptom = {
                        "name": symptom.replace('_', ' ').title(),
                        "avgSeverity": round(avg_sev, 1)
                    }
        
        # Calculate average severity by symptom for chart
        final_avg_severity = {}
        for symptom, severities in avg_severity_by_symptom.items():
            if severities:  # Only process if we have data
                final_avg_severity[symptom] = round(statistics.mean(severities), 1)
        
        # Determine overall trend
        if len(daily_data) >= 2:
            recent_data = [d["avgSeverity"] for d in daily_data[-3:] if d["hasData"] and d["avgSeverity"] > 0]
            earlier_data = [d["avgSeverity"] for d in daily_data[:3] if d["hasData"] and d["avgSeverity"] > 0]
            
            if recent_data and earlier_data:
                recent_avg = statistics.mean(recent_data)
                earlier_avg = statistics.mean(earlier_data)
                
                if recent_avg < earlier_avg - 0.5:
                    overall_trend = "Improving"
                elif recent_avg > earlier_avg + 0.5:
                    overall_trend = "Concerning"
                else:
                    overall_trend = "Stable"
            else:
                overall_trend = "Insufficient Data"
        else:
            overall_trend = "Insufficient Data"
        
        # Generate insights (context-aware for different weeks)
        week_context = "this week" if week_offset == 0 else week_label.lower()
        
        if total_alerts > 3:
            insights.append({
                "id": "high_alerts",
                "type": "critical",
                "icon": "fa-exclamation-triangle",
                "title": "High Alert Activity",
                "description": f"You had {total_alerts} oncologist alerts {week_context}." + (" Consider reaching out to your care team." if week_offset == 0 else "")
            })
        
        if most_concerning_symptom and most_concerning_symptom["avgSeverity"] > 3.5:
            insights.append({
                "id": "concerning_symptom",
                "type": "warning",
                "icon": "fa-heartbeat",
                "title": f"Monitor {most_concerning_symptom['name']}",
                "description": f"Your {most_concerning_symptom['name'].lower()} averaged {most_concerning_symptom['avgSeverity']}/5 {week_context}."
            })
        
        if overall_trend == "Improving":
            insights.append({
                "id": "improving_trend",
                "type": "positive",
                "icon": "fa-chart-line",
                "title": "Positive Trend",
                "description": f"Your symptoms showed an improving trend {week_context}." + (" Keep up the good work!" if week_offset == 0 else "")
            })
        
        if total_assessments >= 5:
            insights.append({
                "id": "consistent_tracking",
                "type": "positive",
                "icon": "fa-check-circle",
                "title": "Consistent Tracking",
                "description": f"You completed {total_assessments} assessments {week_context}." + (" Great job!" if week_offset == 0 else "")
            })
        elif total_assessments < 3 and week_offset == 0:
            insights.append({
                "id": "low_engagement",
                "type": "info",
                "icon": "fa-calendar-check",
                "title": "More Check-ins Recommended",
                "description": "Try to complete daily check-ins for better health insights."
            })
        
        return {
            "success": True,
            "data": {
                "totalAssessments": total_assessments,
                "totalAlerts": total_alerts,
                "mostConcerningSymptom": most_concerning_symptom,
                "overallTrend": overall_trend,
                "dailyData": daily_data,
                "symptomTrends": dict(symptom_trends),
                "avgSeverityBySymptom": final_avg_severity,
                "alertDistribution": alert_distribution,
                "insights": insights,
                "weekRange": {
                    "start": target_week_start.strftime("%Y-%m-%d"),
                    "end": target_week_end.strftime("%Y-%m-%d")
                },
                "weekLabel": week_label,
                "weekOffset": week_offset
            }
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch weekly analytics: {str(e)}"
        )

@app.get("/monthly_analytics")
async def get_monthly_analytics(month_offset: int = 0, user = Depends(get_user), db = Depends(get_db)):
    """
    Get monthly Florence assessment data with alert heatmaps and symptom-specific heatmaps
    
    Args:
        month_offset: Number of months back from current month (0 = current month, 1 = last month, etc.)
    """
    try:
        user_id = user['username']
        florence_collection = db["florence_assessments"]
        
        # Get target month date range based on offset
        today = datetime.now()
        
        # Calculate target month
        target_year = today.year
        target_month = today.month - month_offset
        
        # Handle year rollover
        while target_month <= 0:
            target_month += 12
            target_year -= 1
        while target_month > 12:
            target_month -= 12
            target_year += 1
            
        # Get first and last day of target month
        from calendar import monthrange
        first_day = datetime(target_year, target_month, 1)
        last_day_num = monthrange(target_year, target_month)[1]
        last_day = datetime(target_year, target_month, last_day_num, 23, 59, 59)
        
        # Query assessments from the target month
        florence_responses = list(florence_collection.find({
            "user_id": user_id,
            "created_at": {
                "$gte": first_day.isoformat(),
                "$lte": last_day.isoformat()
            }
        }).sort("created_at", 1))
        
        # Calculate month label for display
        month_names = ["", "January", "February", "March", "April", "May", "June",
                      "July", "August", "September", "October", "November", "December"]
        if month_offset == 0:
            month_label = f"{month_names[target_month]} {target_year}"
        else:
            month_label = f"{month_names[target_month]} {target_year}"
        
        if not florence_responses:
            return {
                "success": True,
                "data": {
                    "totalAssessments": 0,
                    "totalAlerts": 0,
                    "alertsByDay": {},
                    "severityByDay": {},
                    "symptomsByDay": {},
                    "availableSymptoms": [],
                    "monthRange": {
                        "start": first_day.strftime("%Y-%m-%d"),
                        "end": last_day.strftime("%Y-%m-%d")
                    },
                    "monthLabel": month_label,
                    "monthOffset": month_offset,
                    "year": target_year,
                    "month": target_month,
                    "daysInMonth": last_day_num
                }
            }
        
        # Initialize tracking variables
        alerts_by_day = {}  # day -> alert_count
        symptoms_by_day = {}  # symptom -> day -> severity
        severity_by_day = {}  # day -> average_severity
        available_symptoms = set()
        total_alerts = 0
        
        # Process each assessment
        for assessment in florence_responses:
            # Extract date (day of month)
            created_at = assessment.get("created_at", "")
            if created_at:
                try:
                    assessment_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    day_of_month = assessment_date.day
                    
                    # Count alerts by day
                    if assessment.get("flag_for_oncologist", False):
                        alerts_by_day[day_of_month] = alerts_by_day.get(day_of_month, 0) + 1
                        total_alerts += 1
                    
                    # Process symptoms by day
                    structured = assessment.get("structured_assessment", {})
                    symptoms = structured.get("symptoms", {})
                    
                    daily_severities = []
                    for symptom_name, symptom_data in symptoms.items():
                        available_symptoms.add(symptom_name)
                        severity = symptom_data.get("severity_rating", 0)
                        daily_severities.append(severity)
                        
                        if symptom_name not in symptoms_by_day:
                            symptoms_by_day[symptom_name] = {}
                        
                        # Store max severity for the day (if multiple assessments)
                        current_severity = symptoms_by_day[symptom_name].get(day_of_month, 0)
                        symptoms_by_day[symptom_name][day_of_month] = max(current_severity, severity)
                    
                    # Calculate average severity for this assessment
                    if daily_severities:
                        avg_severity = sum(daily_severities) / len(daily_severities)
                        # Store max average severity for the day (if multiple assessments)
                        current_avg = severity_by_day.get(day_of_month, 0)
                        severity_by_day[day_of_month] = max(current_avg, avg_severity)
                        
                except Exception as e:
                    print(f"Error parsing date {created_at}: {e}")
                    continue
        
        return {
            "success": True,
            "data": {
                "totalAssessments": len(florence_responses),
                "totalAlerts": total_alerts,
                "alertsByDay": alerts_by_day,
                "severityByDay": severity_by_day,
                "symptomsByDay": symptoms_by_day,
                "availableSymptoms": sorted(list(available_symptoms)),
                "monthRange": {
                    "start": first_day.strftime("%Y-%m-%d"),
                    "end": last_day.strftime("%Y-%m-%d")
                },
                "monthLabel": month_label,
                "monthOffset": month_offset,
                "year": target_year,
                "month": target_month,
                "daysInMonth": last_day_num
            }
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch monthly analytics: {str(e)}"
        )


