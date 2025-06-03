from .login import get_db, get_user
from fastapi import APIRouter
from fastapi import Depends, FastAPI, HTTPException, status
from datetime import datetime, timezone, timedelta
import json

questionsrouter = APIRouter(tags = ["questions"])

@questionsrouter.get("/getquestions")
async def get_questions(db = Depends(get_db)):
    questions = db["questions"]
    doc = questions.find_one({}, {"_id": 0})
    if doc and "questions" in doc:
        return doc
    raise HTTPException(status_code=404, detail="Database empty")

@questionsrouter.get("/submitquestions")
async def submit(answers):
    filename = "results"+(datetime.now().strftime("%m%d%Y_%H%M%S"))
    with open ("answers/"+filename, 'w') as f:
        json.dump(answers, f)
    return ("Answers saved at " + filename)

@questionsrouter.get("/getstreak")
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