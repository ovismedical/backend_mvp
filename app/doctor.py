from .login import get_db, get_user
from fastapi import Depends, FastAPI, HTTPException, status, APIRouter

doctorrouter = APIRouter(prefix = "/doctor", tags = ["doctor"])

@doctorrouter.put("/create_code")
def create_doctor(code:str,email : str, db = Depends(get_db)):
    doctors = db["doctors"]
    doctors.update_one(
        {"email": email},
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
def get_patient_answers(user_id: str, db = Depends(get_db)):
    answers_collection = db["answers"]
    submissions = list(answers_collection.find(
        {"user_id": user_id}, {"_id": 0}
    ))
    return {"answers": submissions}