from .login import get_db, get_user
from .achievements import update_daily_streak, streak_is_current
from fastapi import APIRouter
from fastapi import Depends, HTTPException, Header, status
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from typing import Optional
import json
import os

questionsrouter = APIRouter(tags = ["questions"])

QUESTIONS_FILE = os.getenv(
    "QUESTIONS_FILE",
    os.path.join(os.path.dirname(__file__), "..", "api_questions.json")
)

class SubmissionRequest(BaseModel):
    answers: list

class NextQuestionRequest(BaseModel):
    current_answers: dict  # Dictionary mapping question_id to answer

@questionsrouter.get("/getquestions")
async def get_questions():
    """Load questions from api_questions.json file"""
    try:
        with open(QUESTIONS_FILE, "r") as f:
            questions_data = json.load(f)
        return questions_data
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Questions file not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid JSON in questions file")

@questionsrouter.post("/getnext")
async def get_next_question(request: NextQuestionRequest):
    """Get the next question based on current answers and prerequisites"""
    try:
        # Load questions from api_questions.json file
        with open(QUESTIONS_FILE, "r") as f:
            questions_data = json.load(f)
        
        all_questions = questions_data["questions"]
        current_answers = request.current_answers
        
        def check_prerequisites(question):
            """Check if a question's prerequisites are met"""
            if "prerequisites" not in question:
                return True
            
            for prereq in question["prerequisites"]:
                prereq_question_number = prereq["question_number"]
                allowed_answers = prereq["allowed_answers"]
                
                # Check if the prerequisite question has been answered
                user_answer = current_answers.get(str(prereq_question_number))
                if user_answer is None:
                    return False
                
                # Handle multiple answers (list)
                if isinstance(user_answer, list):
                    # Check if any of the user's answers match allowed answers
                    if not any(answer in allowed_answers for answer in user_answer):
                        return False
                else:
                    # Single answer
                    if user_answer not in allowed_answers:
                        return False
            
            return True
        
        # Find the first unanswered question that meets prerequisites
        for question in all_questions:
            question_number = question["question_number"]
            
            # Skip if already answered
            if str(question_number) in current_answers:
                continue
            
            # Check if prerequisites are met
            if check_prerequisites(question):
                return {
                    "next_question": question,
                    "question_number": question_number,
                    "total_questions": len(all_questions),
                    "completed": False
                }
        
        # No more eligible questions
        return {
            "next_question": None,
            "question_number": len(current_answers),
            "total_questions": len(all_questions),
            "completed": True,
            "message": "All eligible questions completed"
        }
        
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Questions file not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid JSON in questions file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get next question: {str(e)}")

@questionsrouter.post("/submit")
async def submit_answers(
    submission: SubmissionRequest,
    current_user=Depends(get_user),
    db = Depends(get_db),
    x_timezone: Optional[str] = Header(default=None, alias="X-Timezone"),
):
    try:
        username = current_user["username"]
        answers_collection = db["answers"]
        submission_data = {
            "user_id": username,
            "answers": submission.answers,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "date": datetime.now(timezone.utc).strftime("%m/%d/%Y")
        }
        answers_collection.insert_one(submission_data)

        streak, newly_unlocked = update_daily_streak(db, username, x_timezone)
        if streak is None:
            return {"message": "Answers submitted successfully"}
        return {"message": "Answers submitted successfully", "streak": streak, "newly_unlocked": newly_unlocked}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit answers: {str(e)}")

@questionsrouter.get("/getstreak")
def get_streak(
    current_user=Depends(get_user),
    db=Depends(get_db),
    x_timezone: Optional[str] = Header(default=None, alias="X-Timezone"),
):
    username = current_user["username"]
    users = db["users"]
    user = users.find_one({"username": username}, {"_id": 0, "password": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    longest_streak = user.get("longest_streak", 0)
    if streak_is_current(user.get("last_completion"), x_timezone):
        return {"streak": user.get("streak", 0), "longest_streak": longest_streak,
                "last_completion": user.get("last_completion")}

    if user.get("streak", 0):
        users.update_one({"username": username}, {"$set": {"streak": 0}})
    return {"streak": 0, "longest_streak": longest_streak, "last_completion": user.get("last_completion")}