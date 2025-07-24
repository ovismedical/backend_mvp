from .login import get_db, get_user
from fastapi import Depends, FastAPI, HTTPException, status, APIRouter

doctorrouter = APIRouter(prefix = "/doctor", tags = ["doctor"])

@doctorrouter.post("/create")
def create_doctor(email : str, password : str, db = Depends(get_db)):
    users = db["doctors"]
    user_dict = {"email" : email, "password" : password}

    users.insert_one(user_dict)
    return {"msg": "User created"}

@doctorrouter.put("/create_code")
def create_doctor(code:str,email : str, db = Depends(get_db)):
    doctors = db["doctors"]
    doctors.update_one(
        {"email": email},      # Filter
        {"$set": {"code": code}}  # Update
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