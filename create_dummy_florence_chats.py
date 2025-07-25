#!/usr/bin/env python3
"""
Script to create comprehensive Florence structured assessment records for user ID 101 (Isaac)
showing varied health patterns throughout May 2024 and current week for robust weekly analytics.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
import json
import random

def get_db_connection():
    """Get MongoDB connection"""
    try:
        # Use the Atlas MongoDB URI
        connection_string = "mongodb+srv://ening:evanning1234@cluster0.mltpuqa.mongodb.net/ovis-demo?retryWrites=true&w=majority&appName=Cluster0"
        
        client = MongoClient(connection_string)
        db = client["ovis-demo"]  # Use ovis-demo database
        return db
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None

def create_assessment(date, session_id, symptoms_data, user_info, oncologist_level="none", flag_oncologist=False):
    """Create a single assessment with given parameters"""
    
    return {
        "session_id": session_id,
        "user_id": "101",
        "user_info": user_info,
        "language": "en",
        "input_mode": "keyboard",
        "structured_assessment": {
            "timestamp": date.isoformat(),
            "patient_id": "101",
            "symptoms": symptoms_data,
            "flag_for_oncologist": flag_oncologist,
            "flag_reason": "High severity symptoms" if flag_oncologist else "",
            "mood_assessment": f"Patient condition assessment for {date.strftime('%Y-%m-%d')}",
            "conversation_notes": f"Assessment completed on {date.strftime('%Y-%m-%d')}",
            "oncologist_notification_level": oncologist_level,
            "treatment_status": "undergoing_treatment"
        },
        "created_at": date.isoformat(),
        "completed_at": (date + timedelta(minutes=random.randint(10, 25))).isoformat(),
        "assessment_type": "florence_conversation",
        "florence_state": "completed",

        "ai_powered": True,
        "oncologist_notification_level": oncologist_level,
        "flag_for_oncologist": flag_oncologist
    }

def generate_symptoms(severity_pattern="moderate", day_of_week=0):
    """Generate symptom data based on pattern and day"""
    
    # Base severity levels by pattern
    base_severities = {
        "severe": {"min": 4, "max": 5},
        "moderate": {"min": 2, "max": 4},  
        "mild": {"min": 1, "max": 3},
        "improving": {"min": 1, "max": 2},
        "worsening": {"min": 3, "max": 5}
    }
    
    # Add some weekly variation (worse on weekends due to treatment schedules)
    weekend_modifier = 1 if day_of_week in [5, 6] else 0
    
    base = base_severities.get(severity_pattern, base_severities["moderate"])
    
    symptoms = {}
    symptom_configs = [
        ("fatigue", "extreme tiredness", "sleep difficulties"),
        ("lack_of_appetite", "poor appetite", "weight loss"),
        ("nausea", "feeling sick", "stomach upset"),
        ("cough", "persistent coughing", "breathing issues"),
        ("pain", "body aches", "discomfort")
    ]
    
    for symptom, desc1, desc2 in symptom_configs:
        severity = min(5, max(1, random.randint(base["min"], base["max"]) + weekend_modifier))
        frequency = min(5, max(1, severity + random.randint(-1, 1)))
        
        symptoms[symptom] = {
            "frequency_rating": frequency,
            "severity_rating": severity,
            "key_indicators": [desc1, desc2] if severity >= 3 else [desc1],
            "additional_notes": f"Assessed severity {severity}/5 for {symptom.replace('_', ' ')}"
        }
    
    return symptoms

def create_comprehensive_assessments():
    """Create comprehensive assessment data for testing analytics"""
    
    # Base user info for Isaac
    user_info = {
        "username": "101",
        "full_name": "Isaac",
        "email": "101@example.com",
        "sex": "male",
        "dob": "11/17/2008",
        "streak": 0,
        "last_answer": -1
    }
    
    assessments = []
    
    # Get current week dates for testing current week analytics
    current_time = datetime.now(timezone.utc)
    current_week_start = current_time - timedelta(days=current_time.weekday())
    
    # Create current week data (Monday to today)
    print("Creating current week data...")
    for i in range(min(current_time.weekday() + 1, 7)):  # Up to today
        assessment_date = current_week_start + timedelta(days=i, hours=random.randint(9, 18))
        if assessment_date <= current_time:  # Only past/present dates
            pattern = random.choice(["mild", "moderate", "mild", "improving"])  # Mostly good
            symptoms = generate_symptoms(pattern, assessment_date.weekday())
            
            max_severity = max(s["severity_rating"] for s in symptoms.values())
            if max_severity >= 5:
                oncologist_level = "red"
            elif max_severity >= 4:
                oncologist_level = "amber" 
            else:
                oncologist_level = "none"
            
            flag_oncologist = oncologist_level in ["amber", "red"]
            
            assessment = create_assessment(
                assessment_date,
                f"101_{assessment_date.strftime('%Y%m%d')}_001",
                symptoms,
                user_info,
                oncologist_level,
                flag_oncologist
            )
            assessments.append(assessment)
    
    # Create previous weeks data for comparison
    print("Creating previous weeks data...")
    for week_offset in [1, 2, 3, 4]:  # Last 4 weeks
        week_start = current_week_start - timedelta(weeks=week_offset)
        
        # Vary the pattern by week to show trends
        if week_offset == 4:  # 4 weeks ago - worse condition
            week_pattern = ["severe", "moderate", "severe", "moderate", "severe"]
        elif week_offset == 3:  # 3 weeks ago - improving
            week_pattern = ["moderate", "moderate", "mild", "moderate", "mild"]
        elif week_offset == 2:  # 2 weeks ago - stable
            week_pattern = ["mild", "moderate", "mild", "mild", "moderate"]
        else:  # 1 week ago - good week
            week_pattern = ["mild", "mild", "improving", "mild", "improving"]
        
        for i in range(7):  # Full week
            if random.random() > 0.15:  # 85% chance of assessment each day
                assessment_date = week_start + timedelta(days=i, hours=random.randint(8, 20))
                pattern = week_pattern[min(i, len(week_pattern) - 1)]
                symptoms = generate_symptoms(pattern, assessment_date.weekday())
                
                max_severity = max(s["severity_rating"] for s in symptoms.values())
                if max_severity >= 5:
                    oncologist_level = "red"
                elif max_severity >= 4:
                    oncologist_level = "amber"
                else:
                    oncologist_level = "none"
                
                flag_oncologist = oncologist_level in ["amber", "red"]
                
                assessment = create_assessment(
                    assessment_date,
                    f"101_{assessment_date.strftime('%Y%m%d')}_001",
                    symptoms,
                    user_info,
                    oncologist_level,
                    flag_oncologist
                )
                assessments.append(assessment)
    
    return assessments

def main():
    """Main function to insert the comprehensive assessments into the database"""
    print("Creating comprehensive Florence assessment records for weekly analytics...")
    print("This includes current week and previous weeks data")
    
    # Get database connection
    db = get_db_connection()
    if db is None:
        print("Failed to connect to database")
        return
    
    # Clear existing data for user 101
    florence_collection = db["florence_assessments"]
    result = florence_collection.delete_many({"user_id": "101"})
    print(f"Cleared {result.deleted_count} existing assessments")
    
    # Create comprehensive assessments
    assessments = create_comprehensive_assessments()
    
    # Insert into database
    try:
        result = florence_collection.insert_many(assessments)
        print(f"Successfully inserted {len(result.inserted_ids)} assessment records!")
        
        # Show summary by week
        print("\nSummary by week:")
        weeks = {}
        current_time = datetime.now(timezone.utc)
        
        for assessment in assessments:
            date_str = assessment["created_at"][:19]  # Remove timezone info for parsing
            date_obj = datetime.fromisoformat(date_str)
            
            # Calculate week offset from current week
            current_week_start = current_time - timedelta(days=current_time.weekday())
            assessment_week_start = date_obj - timedelta(days=date_obj.weekday())
            week_diff = (current_week_start.replace(tzinfo=None) - assessment_week_start).days // 7
            
            if week_diff == 0:
                week_key = "Current Week"
            elif week_diff == 1:
                week_key = "Last Week"
            else:
                week_key = f"{week_diff} Weeks Ago"
            
            if week_key not in weeks:
                weeks[week_key] = {"count": 0, "alerts": 0, "avg_severity": []}
            
            weeks[week_key]["count"] += 1
            if assessment["flag_for_oncologist"]:
                weeks[week_key]["alerts"] += 1
                
            # Calculate average severity for this assessment
            symptoms = assessment["structured_assessment"]["symptoms"]
            avg_sev = sum(s["severity_rating"] for s in symptoms.values()) / len(symptoms)
            weeks[week_key]["avg_severity"].append(avg_sev)
        
        # Sort weeks by recency
        week_order = ["Current Week", "Last Week"] + [f"{i} Weeks Ago" for i in range(2, 10)]
        sorted_weeks = [(k, weeks[k]) for k in week_order if k in weeks]
        
        for week, data in sorted_weeks:
            avg_sev = sum(data["avg_severity"]) / len(data["avg_severity"]) if data["avg_severity"] else 0
            print(f"{week}: {data['count']} assessments, {data['alerts']} alerts, avg severity: {avg_sev:.1f}/5")
            
        print(f"\n🎉 Created comprehensive data for weekly analytics!")
        print(f"📊 Total assessments: {len(assessments)}")
        print(f"🚨 Total alerts: {sum(1 for a in assessments if a['flag_for_oncologist'])}")
        print(f"📅 Current week assessments: {weeks.get('Current Week', {}).get('count', 0)}")
        
    except Exception as e:
        print(f"Error inserting assessments: {e}")

if __name__ == "__main__":
    main() 