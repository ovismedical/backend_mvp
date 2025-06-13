from .login import get_db, get_user
from fastapi import APIRouter
from fastapi import Depends, FastAPI, HTTPException, status

adminrouter = APIRouter(prefix = "/admin", tags = ["admin"])

@adminrouter.get("/patients")
def get_patients_by_doctor(user = Depends(get_user), db=Depends(get_db)):
    users = db["users"]
    patients = list(users.find({"doctor" : user["username"]}, {"_id": 0, "password": 0}))
    return {"patients": patients}

@adminrouter.get("/answers")
def get_patient_answers(user_id: str, db = Depends(get_db)):
    answers_collection = db["answers"]
    submissions = list(answers_collection.find(
        {"user_id": user_id}, {"_id": 0}
    ))
    return {"answers": submissions}