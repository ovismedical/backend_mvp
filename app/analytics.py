from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from bson.errors import InvalidId
from .login import get_user, get_db
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re
import statistics

analyticsrouter = APIRouter(prefix="/analytics", tags=["analytics"])


def _structured_symptoms(doc):
    structured = doc.get("structured_assessment") or {}
    symptoms = structured.get("symptoms") if isinstance(structured, dict) else None
    return symptoms if isinstance(symptoms, dict) else {}


def _florence_summary(doc):
    if doc.get("triage_status") == "generating":
        return "Florence is preparing the assessment…"
    if doc.get("triage_status") == "failed":
        return "Chat saved — automated assessment unavailable"
    if doc.get("triage_status") == "pending_clinician_review":
        return "Chat saved — awaiting clinician review"
    triage = doc.get("triage_assessment") or {}
    alert = doc.get("alert_level") or triage.get("alert_level")
    if alert in ("YELLOW", "ORANGE", "RED"):
        key = [k for k in (triage.get("key_symptoms") or []) if isinstance(k, str)][:3]
        timeline = (triage.get("recommended_timeline") or "").split(".")[0].strip()
        head = f"Triage {alert}" + (f" — {', '.join(key)}" if key else "")
        return head + (f". {timeline}" if timeline and len(timeline) < 90 else "")
    symptoms = _structured_symptoms(doc)
    rated = []
    for name, data in symptoms.items():
        if isinstance(data, dict) and data.get("severity_rating", 0) >= 3:
            rated.append((data["severity_rating"], name.replace("_", " ").title()))
    if not symptoms:
        return "AI conversation completed"
    if not rated:
        return "Chat completed — no significant symptoms reported"
    rated.sort(reverse=True)
    parts = [f"{name} {sev}/5" for sev, name in rated[:3]]
    return "Notable symptoms: " + ", ".join(parts)


def _questionnaire_summary(doc):
    summary = doc.get("clinical_summary") or {}
    concerns = summary.get("areas_of_concern") or []
    if not concerns:
        return "No symptoms of concern reported"
    parts = []
    for concern in concerns[:3]:
        label = concern.get("severity_label") or concern.get("severity_score")
        parts.append(f"{concern['area']} ({label})" if label is not None else concern["area"])
    text = "Concerns: " + ", ".join(parts)
    if len(concerns) > 3:
        text += f" +{len(concerns) - 3} more"
    return text


def _questionnaire_date(doc):
    if doc.get("timestamp"):
        return doc["timestamp"]
    submitted = doc.get("submitted_at")
    return submitted.isoformat() if isinstance(submitted, datetime) else "Unknown"


def _questionnaire_as_assessment(doc):
    """Present a questionnaire submission in the same shape as a Florence assessment for analytics."""
    symptoms = {}
    for section in doc.get("sections", []) or []:
        score = section.get("severity_score")
        if isinstance(score, (int, float)) and score > 0:
            key = re.sub(r"[^a-z0-9]+", "_", (section.get("title") or section.get("clinical_area") or "symptom").lower()).strip("_")
            symptoms[key] = {"severity_rating": int(min(5, max(1, round(score)))), "frequency_rating": int(min(5, max(1, round(score))))}
    summary = doc.get("clinical_summary") or {}
    alert_level = doc.get("alert_level")
    if alert_level in ("RED", "ORANGE"):
        level = "red" if alert_level == "RED" else "amber"
    elif alert_level == "YELLOW" or summary.get("alert_flags"):
        level = "amber"
    else:
        level = "none"
    return {
        "source": "questionnaire",
        "created_at": _questionnaire_date(doc),
        "structured_assessment": {"symptoms": symptoms},
        "oncologist_notification_level": level,
        "flag_for_oncologist": level != "none",
        "alert_level": alert_level,
    }


def _assessments_in_range(db, user_id, start_iso, end_iso):
    """Florence assessments + questionnaires for the user between two ISO timestamps, oldest first."""
    florence = list(db["florence_assessments"].find({
        "user_id": user_id,
        "assessment_type": {"$ne": "questionnaire_triage"},
        "created_at": {"$gte": start_iso, "$lte": end_iso},
    }))
    for doc in florence:
        doc["source"] = "florence"
    questionnaires = [
        _questionnaire_as_assessment(q)
        for q in db["symptom_questionnaires"].find({"user_id": user_id, "timestamp": {"$gte": start_iso, "$lte": end_iso}})
    ]
    items = florence + questionnaires
    items.sort(key=lambda d: d.get("created_at") or "")
    return items

@analyticsrouter.get("/unified_assessments")
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
            # Triage generated from a questionnaire is surfaced on the questionnaire entry instead
            if conversation.get("assessment_type") == "questionnaire_triage":
                continue

            conv_history = conversation.get("conversation_history", [])
            message_count = len(conv_history)
            user_messages = len([msg for msg in conv_history if msg.get("role") == "user"])

            oncologist_level = conversation.get("oncologist_notification_level", "none")
            flag_for_oncologist = conversation.get("flag_for_oncologist", False)

            assessment = {
                "id": str(conversation.get("_id")),
                "type": "florence_conversation",
                "date": conversation.get("created_at", "Unknown"),
                "title": "Florence AI Chat",
                "summary": _florence_summary(conversation),
                "data": {
                    "total_messages": message_count,
                    "user_messages": user_messages,
                    "symptoms_assessed": list(_structured_symptoms(conversation).keys()),
                    "ai_powered": conversation.get("ai_powered", False),
                    "conversation_length": message_count,
                    "session_id": conversation.get("session_id"),
                    "alert_level": conversation.get("alert_level") if conversation.get("alert_level") not in ("PENDING", "PENDING_REVIEW") else None,
                    "triage_status": conversation.get("triage_status"),
                    "oncologist_notification_level": oncologist_level,
                    "flag_for_oncologist": flag_for_oncologist
                },
                "icon": "fa-robot",
                "color": "#3b82f6",
                "oncologist_notification_level": oncologist_level,
                "flag_for_oncologist": flag_for_oncologist
            }
            unified_assessments.append(assessment)

        # Structured symptom questionnaires (the current daily check-in)
        questionnaires = db["symptom_questionnaires"].find({"user_id": user_id}).sort("submitted_at", -1)
        for questionnaire in questionnaires:
            summary = questionnaire.get("clinical_summary") or {}
            completion = questionnaire.get("completion") or {}
            alert_level = questionnaire.get("alert_level")
            if alert_level in ("PENDING", "PENDING_REVIEW"):
                alert_level = None   # awaiting clinician review; `triage_status` carries that state
            oncologist_level = "none"
            if alert_level in ("RED", "ORANGE"):
                oncologist_level = "red" if alert_level == "RED" else "amber"
            elif alert_level == "YELLOW" or summary.get("alert_flags"):
                oncologist_level = "amber"

            unified_assessments.append({
                "id": str(questionnaire.get("_id")),
                "type": "daily_checkin",
                "date": _questionnaire_date(questionnaire),
                "title": "Daily Symptom Check-in",
                "summary": _questionnaire_summary(questionnaire),
                "data": {
                    "questions_answered": completion.get("questions_answered", len(questionnaire.get("answers", {}))),
                    "symptom_count": summary.get("symptom_count", 0),
                    "max_severity": summary.get("max_severity"),
                    "max_severity_area": summary.get("max_severity_area"),
                    "alert_flags": summary.get("alert_flags", []),
                    "triage_status": questionnaire.get("triage_status"),
                    "alert_level": alert_level,
                },
                "icon": "fa-clipboard-check",
                "color": "#8b5cf6",
                "oncologist_notification_level": oncologist_level,
                "flag_for_oncologist": oncologist_level != "none",
            })

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

@analyticsrouter.get("/assessment/{assessment_id}")
async def get_assessment_by_id(assessment_id: str, user = Depends(get_user), db = Depends(get_db)):
    """
    Get a specific assessment (Florence conversation or daily check-in) by ID
    """
    try:
        user_id = user['username']
        try:
            object_id = ObjectId(assessment_id)
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid assessment ID format")

        # Try to find in Florence assessments first
        florence_collection = db["florence_assessments"]
        florence_assessment = florence_collection.find_one({
            "_id": object_id,
            "user_id": user_id
        })
        
        if florence_assessment:
            # Return detailed Florence assessment data
            structured_assessment = (florence_assessment.get("structured_assessment") or {})
            
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
                # Fallback for assessments without structured data - no symptom details available
                symptoms_assessed = []  # No reliable symptom data without structured assessment
            
            triage = florence_assessment.get("triage_assessment") or None
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
                    "treatment_status": (structured_assessment.get("treatment_status") or "undergoing_treatment").replace("_", " ").title(),
                    "oncologist_notification_level": florence_assessment.get("oncologist_notification_level", "none"),
                    "flag_for_oncologist": florence_assessment.get("flag_for_oncologist", False),
                    "alert_level": florence_assessment.get("alert_level"),
                    "triage": triage,
                    "mood_assessment": structured_assessment.get("mood_assessment"),
                    "conversation_notes": structured_assessment.get("conversation_notes"),
                    "conversation_history": florence_assessment.get("conversation_history", []),
                }
            }

        # Structured symptom questionnaire
        questionnaire = db["symptom_questionnaires"].find_one({"_id": object_id, "user_id": user_id})
        if questionnaire:
            sections = []
            for section in questionnaire.get("sections", []):
                responses = [
                    {
                        "question_id": r.get("question_id"),
                        "question_text": r.get("question_text"),
                        "type": r.get("type"),
                        "display_value": r.get("display_value"),
                        "raw_value": r.get("raw_value"),
                    }
                    for r in section.get("responses", [])
                    if r.get("was_shown") and r.get("display_value") is not None
                ]
                if responses:
                    sections.append({
                        "section_id": section.get("section_id"),
                        "title": section.get("title"),
                        "clinical_area": section.get("clinical_area"),
                        "severity_score": section.get("severity_score"),
                        "severity_label": section.get("severity_label"),
                        "responses": responses,
                    })

            triage_doc = db["florence_assessments"].find_one(
                {"session_id": f"questionnaire_{assessment_id}"},
                {"_id": 0, "conversation_history": 0, "user_info": 0},
            )
            return {
                "success": True,
                "assessment": {
                    "id": assessment_id,
                    "type": "daily_checkin",
                    "title": "Daily Symptom Check-in",
                    "date": _questionnaire_date(questionnaire),
                    "user_id": user_id,
                    "completion": questionnaire.get("completion"),
                    "clinical_summary": questionnaire.get("clinical_summary"),
                    "sections": sections,
                    "triage_status": questionnaire.get("triage_status"),
                    "alert_level": questionnaire.get("alert_level") or (triage_doc or {}).get("alert_level"),
                    "triage": (triage_doc or {}).get("triage_assessment"),
                    "oncologist_notification_level": (triage_doc or {}).get("oncologist_notification_level", "none"),
                }
            }

        # Legacy daily check-ins (answers collection)
        answers_collection = db["answers"]
        daily_assessment = answers_collection.find_one({
            "_id": object_id,
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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch assessment: {str(e)}"
        )

@analyticsrouter.get("/weekly")
async def get_weekly_analytics(week_offset: int = 0, user = Depends(get_user), db = Depends(get_db)):
    """
    Get comprehensive weekly Florence assessment data with trends, statistics, and insights
    
    Args:
        week_offset: Number of weeks back from current week (0 = current week, 1 = last week, etc.)
    """
    try:
        user_id = user['username']
        florence_collection = db["florence_assessments"]
        
        # Get target week date range based on offset (UTC, whole days)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        current_week_start = today - timedelta(days=today.weekday())
        target_week_start = current_week_start - timedelta(weeks=week_offset)
        target_week_end = target_week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        
        # Florence assessments + symptom questionnaires from the target week
        florence_responses = _assessments_in_range(db, user_id, target_week_start.isoformat(), target_week_end.isoformat())
        
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
                    symptoms = _structured_symptoms(assessment)
                    
                    if symptoms:  # Only process if symptoms exist
                        for symptom_name, symptom_data in symptoms.items():
                            # Handle legacy data where symptom_data might be a string
                            if isinstance(symptom_data, str):
                                # Skip string data - can't extract meaningful severity
                                continue
                            elif isinstance(symptom_data, dict):
                                severity = symptom_data.get("severity_rating", 0)
                                frequency = symptom_data.get("frequency_rating", 0)
                            else:
                                # Unknown data type, skip
                                continue
                                
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
                
                try:
                    avg_severity = statistics.mean(daily_severities) if daily_severities and len(daily_severities) > 0 else 0
                except (statistics.StatisticsError, ValueError):
                    avg_severity = 0
                
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
            if severities and len(severities) > 0:  # Double check we have data
                try:
                    avg_sev = statistics.mean(severities)
                    if avg_sev > highest_avg_severity:
                        highest_avg_severity = avg_sev
                        most_concerning_symptom = {
                            "name": symptom.replace('_', ' ').title(),
                            "avgSeverity": round(avg_sev, 1)
                        }
                except (statistics.StatisticsError, ValueError) as e:
                    print(f"Error calculating mean for symptom {symptom}: {e}")
                    continue
        
        # Calculate average severity by symptom for chart
        final_avg_severity = {}
        for symptom, severities in avg_severity_by_symptom.items():
            if severities and len(severities) > 0:  # Double check we have data
                try:
                    final_avg_severity[symptom] = round(statistics.mean(severities), 1)
                except (statistics.StatisticsError, ValueError) as e:
                    print(f"Error calculating mean for symptom {symptom}: {e}")
                    final_avg_severity[symptom] = 0
        
        # Determine overall trend
        overall_trend = "Insufficient Data"
        if len(daily_data) >= 2:
            recent_data = [d["avgSeverity"] for d in daily_data[-3:] if d["hasData"] and d["avgSeverity"] > 0]
            earlier_data = [d["avgSeverity"] for d in daily_data[:3] if d["hasData"] and d["avgSeverity"] > 0]
            
            if recent_data and earlier_data and len(recent_data) > 0 and len(earlier_data) > 0:
                try:
                    recent_avg = statistics.mean(recent_data)
                    earlier_avg = statistics.mean(earlier_data)
                    
                    if recent_avg < earlier_avg - 0.5:
                        overall_trend = "Improving"
                    elif recent_avg > earlier_avg + 0.5:
                        overall_trend = "Concerning"
                    else:
                        overall_trend = "Stable"
                except (statistics.StatisticsError, ValueError) as e:
                    print(f"Error calculating trend: {e}")
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
                "sources": {
                    "florence": sum(1 for a in florence_responses if a.get("source") == "florence"),
                    "questionnaire": sum(1 for a in florence_responses if a.get("source") == "questionnaire"),
                },
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

@analyticsrouter.get("/monthly")
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
        today = datetime.now(timezone.utc)
        
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
        first_day = datetime(target_year, target_month, 1, tzinfo=timezone.utc)
        last_day_num = monthrange(target_year, target_month)[1]
        last_day = datetime(target_year, target_month, last_day_num, 23, 59, 59, tzinfo=timezone.utc)
        
        # Florence assessments + symptom questionnaires from the target month
        florence_responses = _assessments_in_range(db, user_id, first_day.isoformat(), last_day.isoformat())
        
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
                    symptoms = _structured_symptoms(assessment)
                    
                    daily_severities = []
                    for symptom_name, symptom_data in symptoms.items():
                        available_symptoms.add(symptom_name)
                        
                        # Handle legacy data where symptom_data might be a string
                        if isinstance(symptom_data, str):
                            # Skip string data - can't extract meaningful severity
                            continue
                        elif isinstance(symptom_data, dict):
                            severity = symptom_data.get("severity_rating", 0)
                        else:
                            # Unknown data type, skip
                            continue
                            
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
                "sources": {
                    "florence": sum(1 for a in florence_responses if a.get("source") == "florence"),
                    "questionnaire": sum(1 for a in florence_responses if a.get("source") == "questionnaire"),
                },
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