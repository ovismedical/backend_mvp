from .login import get_db
from fastapi import APIRouter
from fastapi import Depends, FastAPI, HTTPException, status
from datetime import datetime
import json

questionsrouter = APIRouter()

@questionsrouter.get("/questions")
async def get_questions(test = True, db = Depends(get_db)):
    collection_name = "testquestions" if test else "questions"
    questions = db[collection_name]
    
    doc = questions.find_one({}, {"_id": 0})  # get the first document, omit _id
    if doc and "questions" in doc:
        return {"questions": doc["questions"]}
    
    return {"questions": []}

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