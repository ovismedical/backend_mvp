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
def get_streak(user = Depends(get_user), db=Depends(get_db)):
    return ({"streak" : user["streak"]})