from .login import get_db, get_user
from fastapi import Depends, FastAPI, HTTPException, status, APIRouter

doctorrouter = APIRouter(prefix = "/doctor", tags = ["doctor"])

@doctorrouter.put("/create_code")
def create_doctor(code:str, doctor = Depends(get_user), db = Depends(get_db)):
    doctors = db["doctors"]
    if not doctor["isDoctor"]:
        raise HTTPException(status_code=401, detail="Unauthorized")
    doctors.update_one(
        {"username": doctor["username"]},
        {"$set": {"code": code}}
    )
    return {"msg": "code updated"}
    
@doctorrouter.get("/patients")
def get_patients_by_doctor(doctor = Depends(get_user), db=Depends(get_db)):
    if not doctor["isDoctor"]:
        raise HTTPException(status_code=401, detail="Unauthorized")
    patients = doctor["patients"]
    return {"patients": patients}

@doctorrouter.get("/answers")
def get_patient_answers(user_id: str, doctor = Depends(get_user), db=Depends(get_db)):
    if not doctor["isDoctor"]:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    answers_collection = db["answers"]
    answers = list(answers_collection.find({"user_id": user_id}).sort("timestamp", -1))
    
    # Convert ObjectId to string for JSON serialization
    for answer in answers:
        answer["_id"] = str(answer["_id"])
    
    return {"answers": answers}