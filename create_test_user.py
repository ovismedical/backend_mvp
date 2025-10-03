#!/usr/bin/env python3

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.login import get_db, hash_password

def create_test_user():
    db = get_db()
    
    # Create test patient user
    patient_user = {
        "username": "patient",
        "password": hash_password("patient123"),
        "email": "patient@test.com",
        "full_name": "Test Patient",
        "birthdate": "1990-01-01",
        "gender": "male",
        "height": 175,
        "weight": 70,
        "bloodtype": "O+",
        "fitness_level": 3,
        "exercises": ["walking"],
        "checkups": "2024-01-01"
    }
    
    # Create test doctor user
    doctor_user = {
        "username": "doctor", 
        "password": hash_password("doctor123"),
        "email": "doctor@test.com",
        "full_name": "Test Doctor",
        "specialty": "General Practice"
    }
    
    # Insert users
    db["users"].insert_one(patient_user)
    db["doctors"].insert_one(doctor_user)
    
    print("✅ Created test users:")
    print("   Patient: username='patient', password='patient123'")
    print("   Doctor: username='doctor', password='doctor123'")

if __name__ == "__main__":
    create_test_user()
