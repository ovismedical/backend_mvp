from fastapi import FastAPI
from fastapi import Depends
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import json 
import os
from datetime import datetime
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta

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

MONGODB_URI = "mongodb+srv://ening:evanning1234@cluster0.mltpuqa.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

def get_db():
    client = MongoClient(MONGODB_URI)
    db = client["ovis-demo"]
    return db

# Sample data model for login request
class LoginRequest(BaseModel):
    username: str
    password: str

class PatientAccount(BaseModel):
    username: str
    password: str
    name: str
    hospital: str
    doctor: str
    dob: str  # Format: MM/DD/YYYY
    sex: str

class AnswerSubmission(BaseModel):
    user_id: str  # or patient_id
    answers: list[list[str]]


# Sample endpoint for patient login
@app.post("/patient-login")
def patient_login(data: LoginRequest, db=Depends(get_db)):
    patients = db["patients"]
    user = patients.find_one({"username": data.username})
    if user and user.get("password") == data.password:
        return {"message": "Patient login successful", "token": {"name": data.username}}
    return {"message": "Invalid credentials"}

@app.post("/admin-login")
def admin_login(data: LoginRequest, db=Depends(get_db)):
    admins = db["admins"]
    user = admins.find_one({"username": data.username})
    if user and user.get("password") == data.password:
        return {
            "message": "Admin login successful",
            "token": {"name": data.username}
        }
    return {"message": "Invalid credentials"}

@app.post("/create-account")
async def create_account(patient: PatientAccount, db = Depends(get_db)):
    patients = db["patients"]
    existing = patients.find_one({"username": patient.username})
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Insert new patient
    patient_data = patient.dict()
    patients.insert_one(patient_data)
    return {"message": "Account created"}

@app.get("/questions")
async def get_questions(test = True, db = Depends(get_db)):
    collection_name = "testquestions" if test else "questions"
    questions = db[collection_name]
    
    doc = questions.find_one({}, {"_id": 0})  # get the first document, omit _id
    if doc and "questions" in doc:
        return {"questions": doc["questions"]}
    
    return {"questions": []}

@app.get("/personalinfo")
async def get_info(username, db = Depends(get_db)):
    patients = db["patients"]
    patient = patients.find_one({"username": username}, {"_id": 0, "password": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient

@app.get("/submitquestions")
async def submit(answers):
    filename = "results"+(datetime.now().strftime("%m%d%Y_%H%M%S"))
    with open ("answers/"+filename, 'w') as f:
        json.dump(answers, f)
    return ("Answers saved at " + filename)

@app.get("/admin/patients")
def get_patients_by_doctor(doctor, db=Depends(get_db)):
    patients_collection = db["patients"]
    
    query = {}
    if doctor:
        query["doctor"] = doctor

    patients = list(patients_collection.find(query, {"_id": 0}))
    return {"patients": patients}

@app.get("/admin/answers/{user_id}")
def get_patient_answers(user_id: str, db = Depends(get_db)):
    answers_collection = db["answers"]
    submissions = list(answers_collection.find(
        {"user_id": user_id}, {"_id": 0}
    ))
    return {"answers": submissions}


@app.post("/submit")
def submit_answers(submission: AnswerSubmission, db=Depends(get_db)):
    answers_collection = db["answers"]

    entry = {
        "user_id": submission.user_id,
        "answers": submission.answers,
        "timestamp": datetime.utcnow()
    }

    answers_collection.insert_one(entry)
    patients = db["patients"]
    date = datetime.now(timezone.utc).date()
    s = date.strftime("%m/%d/%Y")
    patients.update_one({"username": submission.user_id}, {"$set": {"last_completion": s}})

    patient = patients.find_one({"username": submission.user_id}, {"_id": 0, "password": 0})

    patients.update_one({"username": submission.user_id}, {"$set": {"streak": (patient.get("streak")+1)}})
    return {"message": "Answers submitted successfully"}

@app.get("/getstreak")
def get_streak(username, db=Depends(get_db)):
    patients = db["patients"]
    patient = patients.find_one({"username": username}, {"_id": 0, "password": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    today = datetime.now(timezone.utc).date()
    last_check_in = datetime.strptime(patient.get("last_completion"), "%m/%d/%Y").date()

    if last_check_in == today:
        return patient.get("streak")
    elif last_check_in == today - timedelta(days=1):
        return patient.get("streak")
    else:
        patients.update_one({"username": username}, {"$set": {"streak": 0}})
        return 0


