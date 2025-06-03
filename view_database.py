#!/usr/bin/env python3
"""
Simple script to view OVIS MongoDB database contents
"""
import os
from pymongo import MongoClient
from datetime import datetime
import json
from dotenv import load_dotenv

# Load .env from app directory where it's located
load_dotenv('app/.env')

def view_database():
    # Connect to MongoDB Atlas
    MONGODB_URI = os.getenv("MONGODB_URI")
    print(f"🔗 Connecting to: {MONGODB_URI[:30]}...")
    
    client = MongoClient(MONGODB_URI)
    db = client["ovis-demo"]
    
    print("🗄️  OVIS Database Overview")
    print("=" * 50)
    
    # List all collections
    collections = db.list_collection_names()
    print(f"📁 Collections found: {collections}")
    print()
    
    # View each collection
    for collection_name in collections:
        collection = db[collection_name]
        count = collection.count_documents({})
        print(f"📊 Collection: {collection_name}")
        print(f"   Documents: {count}")
        
        if count > 0:
            # Show a sample document
            sample = collection.find_one()
            if sample and '_id' in sample:
                sample.pop('_id', None)  # Remove ObjectId for cleaner display
            if sample and 'password' in sample:
                sample.pop('password', None)  # Don't show passwords
            
            print(f"   Sample document:")
            print(f"   {json.dumps(sample, indent=2, default=str)[:500]}...")
        print()
    
    # Specific queries for Florence assessments
    if "florence_assessments" in collections:
        florence_collection = db["florence_assessments"]
        florence_count = florence_collection.count_documents({})
        print(f"🤖 Florence Assessments: {florence_count}")
        
        if florence_count > 0:
            # Get latest Florence assessment
            latest = florence_collection.find().sort("created_at", -1).limit(1)
            for assessment in latest:
                assessment.pop('_id', None)
                print("   Latest Florence Assessment:")
                print(f"   User: {assessment.get('user_id', 'Unknown')}")
                print(f"   Created: {assessment.get('created_at', 'Unknown')}")
                print(f"   Messages: {len(assessment.get('conversation_history', []))}")
                print(f"   AI Powered: {assessment.get('ai_powered', False)}")
                print(f"   Symptoms: {assessment.get('symptoms_assessed', [])}")
        print()
    
    # Specific queries for daily check-ins
    if "responses" in collections:
        responses_collection = db["responses"]
        responses_count = responses_collection.count_documents({})
        print(f"📝 Daily Check-in Responses: {responses_count}")
        
        if responses_count > 0:
            # Get latest response
            latest = responses_collection.find().sort("timestamp", -1).limit(1)
            for response in latest:
                response.pop('_id', None)
                print("   Latest Daily Check-in:")
                print(f"   User: {response.get('user_id', 'Unknown')}")
                print(f"   Time: {response.get('timestamp', 'Unknown')}")
                print(f"   Answers: {len(response.get('answers', []))}")
        print()
    
    # User stats
    if "users" in collections:
        users_collection = db["users"]
        users_count = users_collection.count_documents({})
        print(f"👥 Users: {users_count}")
        
        if users_count > 0:
            # Show user list (without passwords)
            users = users_collection.find({}, {"username": 1, "full_name": 1, "streak": 1, "_id": 0})
            print("   Registered users:")
            for user in users:
                print(f"   - {user.get('username')} ({user.get('full_name', 'No name')}) - Streak: {user.get('streak', 0)}")
        print()
    
    client.close()
    print("✅ Database overview complete!")

if __name__ == "__main__":
    view_database() 