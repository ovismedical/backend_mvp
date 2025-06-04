from .login import get_db, get_user
from fastapi import APIRouter
from fastapi import Depends, FastAPI, HTTPException, status
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
import json

questionsrouter = APIRouter(tags = ["questions"])

class SubmissionRequest(BaseModel):
    user_id: str
    answers: list

@questionsrouter.get("/getquestions")
async def get_questions(db = Depends(get_db)):
    questions = db["questions"]
    doc = questions.find_one({}, {"_id": 0})
    if doc and "questions" in doc:
        return doc
    raise HTTPException(status_code=404, detail="Database empty")

@questionsrouter.post("/submit")
async def submit_answers(submission: SubmissionRequest, db = Depends(get_db)):
    try:
        # Store the submission in the answers collection
        answers_collection = db["answers"]
        submission_data = {
            "user_id": submission.user_id,
            "answers": submission.answers,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "date": datetime.now(timezone.utc).strftime("%m/%d/%Y")
        }
        answers_collection.insert_one(submission_data)
        
        # Update user's streak and last completion date
        users = db["users"]
        today = datetime.now(timezone.utc).strftime("%m/%d/%Y")
        
        user = users.find_one({"username": submission.user_id})
        if user:
            current_streak = user.get("streak", 0)
            last_completion = user.get("last_completion")
            
            # If completed today already, don't update streak
            if last_completion == today:
                return {"message": "Answers submitted successfully", "streak": current_streak}
            
            # If completed yesterday, increment streak
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%m/%d/%Y")
            if last_completion == yesterday:
                new_streak = current_streak + 1
            else:
                new_streak = 1  # Reset streak if more than a day gap
            
            users.update_one(
                {"username": submission.user_id},
                {"$set": {"streak": new_streak, "last_completion": today}}
            )
            return {"message": "Answers submitted successfully", "streak": new_streak}
        
        return {"message": "Answers submitted successfully"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit answers: {str(e)}")

@questionsrouter.get("/submitquestions")
async def submit(answers, user = Depends(get_user),):
    filename = "results"+(datetime.now().strftime("%m%d%Y_%H%M%S"))
    with open ("answers/"+filename, 'w') as f:
        json.dump(answers, f)
    return ("Answers saved at " + filename)

@questionsrouter.get("/getstreak")
def get_streak(user = Depends(get_user), db=Depends(get_db)):
    return {"streak": user["streak"]}