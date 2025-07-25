#!/usr/bin/env python3
"""
Script to create comprehensive Florence assessment records spanning multiple months
for testing monthly analytics with alert and symptom heatmaps.
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
            "mood_assessment": f"Assessment for {date.strftime('%Y-%m-%d')}",
            "conversation_notes": f"Assessment completed on {date.strftime('%Y-%m-%d')}",
            "oncologist_notification_level": oncologist_level,
            "treatment_status": "undergoing_treatment"
        },
        "created_at": date.isoformat(),
        "completed_at": (date + timedelta(minutes=random.randint(5, 20))).isoformat(),
        "assessment_type": "florence_conversation",
        "florence_state": "completed",

        "ai_powered": True,
        "oncologist_notification_level": oncologist_level,
        "flag_for_oncologist": flag_oncologist
    }

def generate_symptoms(base_pattern="moderate", day_variation=0):
    """Generate symptom data with daily variation"""
    
    # Base severity levels by pattern
    patterns = {
        "mild": {"min": 1, "max": 2},
        "moderate": {"min": 2, "max": 3},  
        "severe": {"min": 3, "max": 5},
        "critical": {"min": 4, "max": 5}
    }
    
    base = patterns.get(base_pattern, patterns["moderate"])
    
    symptoms = {}
    symptom_configs = [
        ("fatigue", "tiredness", "low energy"),
        ("lack_of_appetite", "poor appetite", "food aversion"),
        ("nausea", "feeling sick", "stomach upset"),
        ("cough", "persistent cough", "breathing issues"),
        ("pain", "body aches", "discomfort")
    ]
    
    for symptom, desc1, desc2 in symptom_configs:
        # Add some daily variation
        severity = min(5, max(1, random.randint(base["min"], base["max"]) + day_variation))
        frequency = min(5, max(1, severity + random.randint(-1, 1)))
        
        symptoms[symptom] = {
            "frequency_rating": frequency,
            "severity_rating": severity,
            "key_indicators": [desc1, desc2] if severity >= 3 else [desc1],
            "additional_notes": f"Severity {severity}/5 for {symptom.replace('_', ' ')}"
        }
    
    return symptoms

def create_monthly_data():
    """Create comprehensive monthly assessment data"""
    
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
    current_time = datetime.now(timezone.utc)
    
    # Create data for last 6 months to ensure we have good historical data
    for month_offset in range(6):
        target_month = current_time.month - month_offset
        target_year = current_time.year
        
        # Handle year rollover
        while target_month <= 0:
            target_month += 12
            target_year -= 1
            
        # Get number of days in this month
        from calendar import monthrange
        days_in_month = monthrange(target_year, target_month)[1]
        
        # Define health patterns by month (recent months better)
        if month_offset == 0:  # Current month
            month_pattern = "mild"
            assessment_frequency = 0.6  # 60% of days
        elif month_offset == 1:  # Last month
            month_pattern = "moderate"
            assessment_frequency = 0.7
        elif month_offset == 2:  # 2 months ago
            month_pattern = "moderate"
            assessment_frequency = 0.8
        elif month_offset == 3:  # 3 months ago
            month_pattern = "severe"
            assessment_frequency = 0.9
        else:  # Older months
            month_pattern = "critical"
            assessment_frequency = 0.5
        
        print(f"Creating data for {target_year}-{target_month} ({month_pattern} pattern)")
        
        # Create assessments for this month
        for day in range(1, days_in_month + 1):
            # Skip some days randomly based on assessment frequency
            if random.random() > assessment_frequency:
                continue
                
            # Create 1-3 assessments per active day
            assessments_today = random.randint(1, 3) if random.random() > 0.7 else 1
            
            for assessment_num in range(assessments_today):
                assessment_time = datetime(
                    target_year, target_month, day,
                    random.randint(8, 20), random.randint(0, 59)
                )
                
                # Add some daily variation based on day patterns
                day_variation = 0
                if day % 7 in [0, 6]:  # Weekends might be worse
                    day_variation = random.randint(0, 1)
                if day <= 7:  # Start of month (treatment cycles)
                    day_variation = random.randint(-1, 2)
                    
                symptoms = generate_symptoms(month_pattern, day_variation)
                
                # Determine alert level based on max severity
                max_severity = max(s["severity_rating"] for s in symptoms.values())
                if max_severity >= 5:
                    oncologist_level = "red"
                elif max_severity >= 4:
                    oncologist_level = "amber" 
                else:
                    oncologist_level = "none"
                
                flag_oncologist = oncologist_level in ["amber", "red"]
                
                assessment = create_assessment(
                    assessment_time,
                    f"101_{assessment_time.strftime('%Y%m%d')}_{assessment_num:03d}",
                    symptoms,
                    user_info,
                    oncologist_level,
                    flag_oncologist
                )
                assessments.append(assessment)
    
    return assessments

def main():
    """Main function to insert the monthly assessments into the database"""
    print("Creating comprehensive monthly Florence assessment records...")
    print("This will create 6 months of data for monthly analytics testing")
    
    # Get database connection
    db = get_db_connection()
    if db is None:
        print("Failed to connect to database")
        return
    
    # Clear existing data for user 101
    florence_collection = db["florence_assessments"]
    result = florence_collection.delete_many({"user_id": "101"})
    print(f"Cleared {result.deleted_count} existing assessments")
    
    # Create monthly assessments
    assessments = create_monthly_data()
    
    # Insert into database
    try:
        result = florence_collection.insert_many(assessments)
        print(f"Successfully inserted {len(result.inserted_ids)} assessment records!")
        
        # Show summary by month
        print("\nSummary by month:")
        months = {}
        
        for assessment in assessments:
            date_str = assessment["created_at"][:7]  # YYYY-MM
            
            if date_str not in months:
                months[date_str] = {"count": 0, "alerts": 0, "alert_days": set()}
            
            months[date_str]["count"] += 1
            if assessment["flag_for_oncologist"]:
                months[date_str]["alerts"] += 1
                # Track unique alert days
                day = int(assessment["created_at"][8:10])
                months[date_str]["alert_days"].add(day)
        
        # Sort months chronologically
        sorted_months = sorted(months.items(), reverse=True)
        
        for month_str, data in sorted_months:
            alert_days_count = len(data["alert_days"])
            print(f"{month_str}: {data['count']} assessments, {data['alerts']} alerts on {alert_days_count} days")
            
        print(f"\n🎉 Created comprehensive monthly data!")
        print(f"📊 Total assessments: {len(assessments)}")
        print(f"🚨 Total alerts: {sum(1 for a in assessments if a['flag_for_oncologist'])}")
        print(f"📅 Months with data: {len(months)}")
            
    except Exception as e:
        print(f"Error inserting assessments: {e}")

if __name__ == "__main__":
    main() 