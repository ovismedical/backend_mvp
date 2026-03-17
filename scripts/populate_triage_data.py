#!/usr/bin/env python3
"""
Script to populate the database with fake triage data for testing the frontend integration.
This creates realistic Florence assessment records with triage data.
"""

import os
import json
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_database_connection():
    """Get MongoDB database connection"""
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        print("❌ MONGODB_URI environment variable not set")
        print("   Make sure you have a .env file with your Atlas connection string")
        return None
    
    try:
        client = MongoClient(mongodb_uri)
        db = client["ovis-demo"]  # Same database name as backend
        print("✅ Connected to MongoDB")
        return db
    except Exception as e:
        print(f"❌ Failed to connect to MongoDB: {e}")
        return None

def create_mock_conversation_history():
    """Create a realistic conversation history"""
    return [
        {
            "role": "user",
            "content": "Hi Florence, I've been feeling a bit tired lately and having some headaches. Is this normal?",
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        },
        {
            "role": "assistant", 
            "content": "Hello! I'm here to help you understand your symptoms. Fatigue and headaches can be common during treatment. Can you tell me more about when these symptoms started and how severe they feel?",
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=14)).isoformat()
        },
        {
            "role": "user",
            "content": "The fatigue started about 3 days ago. I feel tired even after sleeping 8 hours. The headaches come and go, maybe 2-3 times a day. They're not too bad, maybe a 4 out of 10.",
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=13)).isoformat()
        },
        {
            "role": "assistant",
            "content": "Thank you for sharing that. It sounds like you're experiencing moderate fatigue and mild to moderate headaches. Are you drinking enough water? Sometimes dehydration can contribute to these symptoms. Also, have you noticed any changes in your appetite?",
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=12)).isoformat()
        },
        {
            "role": "user",
            "content": "I think I could drink more water. My appetite has been a bit lower than usual, but I'm still eating. I'm just worried these symptoms might mean something serious.",
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
        },
        {
            "role": "assistant",
            "content": "I understand your concern, and it's good that you're paying attention to your symptoms. Based on what you've described, these symptoms are consistent with common treatment-related side effects. However, it's important to monitor them. I'll make sure your healthcare team is aware of these symptoms so they can provide appropriate guidance.",
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        }
    ]

def create_mock_triage_assessment():
    """Create a realistic triage assessment"""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "patient_id": "demo_patient",
        "clinical_reasoning": "Patient reports fatigue starting 3 days ago with 8+ hours of sleep, and intermittent headaches (2-3 times daily, severity 4/10). Appetite slightly decreased. No red flag symptoms identified. Symptoms are consistent with common treatment-related side effects. Patient is concerned but symptoms are manageable. Recommend monitoring and hydration.",
        "diagnosis_predictions": [
            {
                "suspected_diagnosis": "Treatment-related fatigue",
                "probability": "medium",
                "urgency": 2,
                "reasoning": "Fatigue is a common side effect of current treatment protocol. Patient reports it's manageable but affecting daily activities. Duration of 3 days with adequate sleep suggests treatment-related cause."
            },
            {
                "suspected_diagnosis": "Dehydration-related headaches",
                "probability": "low",
                "urgency": 2,
                "reasoning": "Mild headaches could be related to insufficient fluid intake. Patient acknowledges need to increase water consumption. No severe headache characteristics noted."
            }
        ],
        "alert_level": "YELLOW",
        "alert_rationale": "Patient experiencing manageable treatment-related symptoms that should be monitored. No urgent concerns identified, but symptoms warrant follow-up to ensure they don't progress.",
        "key_symptoms": ["fatigue", "headaches", "decreased appetite"],
        "recommended_timeline": "Schedule follow-up within 1-2 weeks",
        "confidence_level": "medium",
        "clinical_notes": "Patient is appropriately concerned about symptoms. Good self-awareness and communication. Symptoms are within expected range for current treatment phase.",
        "treatment_status": "undergoing_treatment"
    }

def create_mock_structured_assessment():
    """Create a realistic structured assessment"""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "patient_id": "demo_patient",
        "symptoms": [
            {
                "symptom": "fatigue",
                "severity": "moderate",
                "frequency": "daily",
                "duration": "3 days",
                "impact": "affecting daily activities"
            },
            {
                "symptom": "headaches",
                "severity": "mild",
                "frequency": "intermittent",
                "duration": "3 days",
                "impact": "manageable with rest"
            },
            {
                "symptom": "decreased appetite",
                "severity": "mild",
                "frequency": "daily",
                "duration": "3 days",
                "impact": "still eating but less than usual"
            }
        ],
        "flag_for_oncologist": False,
        "flag_reason": None,
        "mood_assessment": "Patient is concerned but coping well. Good communication and self-awareness.",
        "conversation_notes": "Patient expressed appropriate concern about symptoms. Good engagement with Florence. Symptoms are manageable but warrant monitoring.",
        "oncologist_notification_level": "none",
        "treatment_status": "undergoing_treatment"
    }

def create_assessment_record():
    """Create a complete assessment record"""
    session_id = f"session_{int(datetime.now().timestamp())}"
    user_id = "demo_patient"
    
    # Create session data
    session_data = {
        "session_id": session_id,
        "user_id": user_id,
        "user_info": {
            "username": user_id,
            "full_name": "Demo Patient",
            "email": "demo@example.com"
        },
        "language": "en",
        "input_mode": "keyboard",
        "conversation_history": create_mock_conversation_history(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "florence_state": "completed",
        "ai_available": True,
        "treatment_status": "undergoing_treatment"
    }
    
    # Create assessments
    structured_assessment = create_mock_structured_assessment()
    triage_assessment = create_mock_triage_assessment()
    
    # Create the complete record
    assessment_record = {
        "session_id": session_id,
        "user_id": user_id,
        "user_info": session_data["user_info"],
        "language": "en",
        "input_mode": "keyboard",
        "conversation_history": session_data["conversation_history"],
        "structured_assessment": structured_assessment,
        "triage_assessment": triage_assessment,
        "alert_level": "YELLOW",
        "created_at": session_data["created_at"],
        "completed_at": session_data["completed_at"],
        "assessment_type": "florence_conversation_with_triage",
        "florence_state": "completed",
        "ai_powered": True,
        "oncologist_notification_level": "none",
        "flag_for_oncologist": False
    }
    
    return assessment_record

def populate_database():
    """Populate the database with mock triage data"""
    db = get_database_connection()
    if db is None:
        return
    
    try:
        # Clear existing demo data
        print("🧹 Clearing existing demo data...")
        db.florence_assessments.delete_many({"user_id": "demo_patient"})
        
        # Clear existing data for user 101
        print("🧹 Clearing existing data for user 101...")
        db.florence_assessments.delete_many({"user_id": "101"})
        
        # Create and insert new assessment records
        print("📝 Creating mock triage assessments...")
        
        # Create multiple assessments for different scenarios
        scenarios = [
            {
                "alert_level": "GREEN",
                "description": "Stable patient with normal treatment effects"
            },
            {
                "alert_level": "YELLOW", 
                "description": "Patient with mild symptoms requiring monitoring"
            },
            {
                "alert_level": "ORANGE",
                "description": "Patient with concerning symptoms needing attention"
            }
        ]
        
        for i, scenario in enumerate(scenarios):
            # Create assessment record
            assessment_record = create_assessment_record()
            
            # Set user_id to 101
            assessment_record["user_id"] = "101"
            assessment_record["user_info"]["username"] = "101"
            assessment_record["triage_assessment"]["patient_id"] = "101"
            
            # Modify for different scenarios
            if scenario["alert_level"] == "GREEN":
                assessment_record["triage_assessment"]["alert_level"] = "GREEN"
                assessment_record["triage_assessment"]["alert_rationale"] = "Patient reports feeling well with only mild, expected treatment side effects. All symptoms are within normal range."
                assessment_record["triage_assessment"]["recommended_timeline"] = "Continue routine monitoring"
                assessment_record["triage_assessment"]["key_symptoms"] = ["mild fatigue"]
                assessment_record["triage_assessment"]["diagnosis_predictions"] = [
                    {
                        "suspected_diagnosis": "Normal treatment effects",
                        "probability": "high",
                        "urgency": 1,
                        "reasoning": "Symptoms are consistent with expected treatment side effects and are well-managed."
                    }
                ]
                assessment_record["alert_level"] = "GREEN"
                
            elif scenario["alert_level"] == "ORANGE":
                assessment_record["triage_assessment"]["alert_level"] = "ORANGE"
                assessment_record["triage_assessment"]["alert_rationale"] = "Patient reports worsening symptoms including persistent fever and severe fatigue. These symptoms require prompt medical evaluation."
                assessment_record["triage_assessment"]["recommended_timeline"] = "Schedule same-day or next-day evaluation"
                assessment_record["triage_assessment"]["key_symptoms"] = ["fever", "severe fatigue", "persistent nausea"]
                assessment_record["triage_assessment"]["diagnosis_predictions"] = [
                    {
                        "suspected_diagnosis": "Possible infection",
                        "probability": "medium",
                        "urgency": 4,
                        "reasoning": "Fever and worsening symptoms could indicate infection, which requires prompt evaluation in immunocompromised patients."
                    }
                ]
                assessment_record["alert_level"] = "ORANGE"
                assessment_record["oncologist_notification_level"] = "amber"
                assessment_record["flag_for_oncologist"] = True
            
            # Set different timestamps
            days_ago = i
            assessment_record["created_at"] = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
            assessment_record["completed_at"] = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
            assessment_record["triage_assessment"]["timestamp"] = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
            
            # Insert into database
            result = db.florence_assessments.insert_one(assessment_record)
            print(f"✅ Inserted {scenario['alert_level']} assessment: {result.inserted_id}")
        
        print(f"\n🎉 Successfully populated database with {len(scenarios)} triage assessments for user 101!")
        print("📊 Assessment summary:")
        for scenario in scenarios:
            print(f"   - {scenario['alert_level']}: {scenario['description']}")
        
        # Verify the data
        count_101 = db.florence_assessments.count_documents({"user_id": "101"})
        count_demo = db.florence_assessments.count_documents({"user_id": "demo_patient"})
        print(f"\n✅ Total assessments for user 101: {count_101}")
        print(f"✅ Total assessments for demo_patient: {count_demo}")
        
    except Exception as e:
        print(f"❌ Error populating database: {e}")

if __name__ == "__main__":
    print("🚀 Starting triage data population...")
    populate_database()
    print("✨ Done!")
