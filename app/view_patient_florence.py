#!/usr/bin/env python3
"""
Simple script to view all Florence assessment conversations and structured assessments for a patient.

Usage from backend_mvp directory:
    python app/view_patient_florence.py <patient_id>
    python app/view_patient_florence.py  # Interactive mode
"""

import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv
from pymongo import MongoClient

# Load environment variables
load_dotenv()

def format_timestamp(timestamp_str):
    """Format timestamp for display"""
    try:
        if 'T' in timestamp_str:
            dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(timestamp_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return timestamp_str

def print_conversation(conversation_history):
    """Print conversation in a readable format"""
    print("📝 Conversation Log:")
    print("-" * 50)
    
    for i, message in enumerate(conversation_history, 1):
        role = message.get('role', 'unknown')
        content = message.get('content', '')
        
        if role == 'user':
            print(f"👤 Patient: {content}")
        elif role == 'assistant':
            print(f"🤖 Florence: {content}")
        elif role == 'system':
            print(f"⚙️  System: {content}")
        
        print()  # Empty line between messages

def print_structured_assessment(structured_assessment):
    """Print structured assessment in a readable format"""
    if not structured_assessment:
        print("❌ No structured assessment available")
        return
    
    print("🔬 Structured Assessment:")
    print("-" * 50)
    
    # Basic info
    print(f"📅 Timestamp: {format_timestamp(structured_assessment.get('timestamp', 'N/A'))}")
    print(f"👤 Patient ID: {structured_assessment.get('patient_id', 'N/A')}")
    print(f"🏥 Treatment Status: {structured_assessment.get('treatment_status', 'N/A')}")
    print(f"🚨 Oncologist Flag: {'Yes' if structured_assessment.get('flag_for_oncologist', False) else 'No'}")
    print(f"⚠️  Notification Level: {structured_assessment.get('oncologist_notification_level', 'none').upper()}")
    
    if structured_assessment.get('flag_reason'):
        print(f"🔍 Flag Reason: {structured_assessment['flag_reason']}")
    
    print()
    
    # Symptoms
    symptoms = structured_assessment.get('symptoms', {})
    if symptoms:
        print("💊 Symptoms Assessment:")
        for symptom_name, symptom_data in symptoms.items():
            print(f"\n   🔸 {symptom_name.replace('_', ' ').title()}:")
            print(f"      Frequency: {symptom_data.get('frequency_rating', 'N/A')}/5")
            print(f"      Severity:  {symptom_data.get('severity_rating', 'N/A')}/5")
            
            if symptom_data.get('location'):
                print(f"      Location:  {symptom_data['location']}")
            
            key_indicators = symptom_data.get('key_indicators', [])
            if key_indicators:
                print("      Key Indicators:")
                for indicator in key_indicators:
                    print(f"        • {indicator}")
            
            if symptom_data.get('additional_notes'):
                print(f"      Notes: {symptom_data['additional_notes']}")
    
    print()
    
    # Additional assessments
    if structured_assessment.get('mood_assessment'):
        print(f"😊 Mood Assessment: {structured_assessment['mood_assessment']}")
    
    if structured_assessment.get('conversation_notes'):
        print(f"📋 Notes: {structured_assessment['conversation_notes']}")

def view_patient_florence(patient_id):
    """View all Florence assessments for a patient"""
    
    # Get MongoDB connection (same as backend configuration)
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        print("❌ MONGODB_URI environment variable not set")
        print("   Make sure you have a .env file with your Atlas connection string")
        return
    
    try:
        client = MongoClient(mongodb_uri)
        db = client["ovis-demo"]  # Same database name as backend
        collection = db.florence_assessments
        
        print(f"🔗 Connected to MongoDB")
        print(f"👤 Looking up patient: {patient_id}")
        print("=" * 70)
        
        # Find all assessments for this patient
        assessments = collection.find({"user_id": patient_id}).sort("created_at", -1)
        assessments_list = list(assessments)
        
        if not assessments_list:
            print(f"❌ No Florence assessments found for patient: {patient_id}")
            return
        
        print(f"✅ Found {len(assessments_list)} Florence assessment(s)")
        print()
        
        # Display each assessment
        for i, assessment in enumerate(assessments_list, 1):
            print(f"🗂️  Assessment #{i}")
            print(f"📅 Created: {format_timestamp(assessment.get('created_at', 'N/A'))}")
            print(f"📅 Completed: {format_timestamp(assessment.get('completed_at', 'N/A'))}")
            print(f"🆔 Session ID: {assessment.get('session_id', 'N/A')}")
            print(f"🌐 Language: {assessment.get('language', 'N/A')}")
            print(f"🤖 AI Powered: {'Yes' if assessment.get('ai_powered', False) else 'No'}")
            print()
            
            # Print conversation
            conversation_history = assessment.get('conversation_history', [])
            if conversation_history:
                print_conversation(conversation_history)
            else:
                print("❌ No conversation history available")
            
            print()
            
            # Print structured assessment
            structured_assessment = assessment.get('structured_assessment')
            print_structured_assessment(structured_assessment)
            
            print("\n" + "=" * 70 + "\n")
    
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
    
    finally:
        if 'client' in locals():
            client.close()

def main():
    """Main function"""
    print("🔍 Florence Patient Assessment Viewer")
    print("=" * 40)
    
    # Get patient ID from command line or user input
    if len(sys.argv) > 1:
        patient_id = sys.argv[1]
    else:
        patient_id = input("Enter patient ID: ").strip()
    
    if not patient_id:
        print("❌ Patient ID is required")
        sys.exit(1)
    
    view_patient_florence(patient_id)

if __name__ == "__main__":
    main() 