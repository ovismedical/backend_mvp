from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import json 
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

patient_data = {
        "patient1": 
        {
            "password": "password", 
            "name": "patient 1",
            "hospital": "hospital 1",
            "doctor": "doctor 1",
            "dob": "1/5/2000",
            "sex": "male",
            "id" : "qwertypoiuytrewuiop",
            "pfp": "hi",
            "medications": [{"name": "med1", "dosage": "100mg"}]
            },
        "patient2": 
        {
            "password": "qwer", 
            "name": "patient 2",
            "hospital": "hospital 2",
            "doctor": "doctor 2",
            "dob": "2/2/2002",
            "sex": "female",
            "id" : "zxcvbnm"
            }
}

admin_data = {
    "admin" : 
    {
        "password" : "admin",
        "patients" : ["patient1", "patient2"]
    }
}

# Sample data model for login request
class LoginRequest(BaseModel):
    username: str
    password: str

# Sample endpoint for patient login
@app.post("/patient-login")
async def patient_login(data: LoginRequest):
    if data.username == "patient1" and data.password == "password":
        return {"message": "Patient login successful",
                "token": {"name" : "patient1"}}
    if data.username == "patient2" and data.password == "qwer":
        return {"message": "Patient login successful",
                "token": {"name" : "patient2"}}
    return {"message": "Invalid credentials"}

@app.post("/admin-login")
async def admin_login(data: LoginRequest):
    if data.username == "admin" and data.password == "admin":
        return {"message": "Admin login successful",
                "token": {"name" : "admin"}}
    return {"message": "Invalid credentials"}

@app.get("/questions")
async def get_questions():
    with open ("app/questions.json", 'r') as f:
        data = f.read()
    return json.loads(data)

@app.get("/personalinfo")
async def get_info(id):
    return json.loads(json.dumps(patient_data[id]))
