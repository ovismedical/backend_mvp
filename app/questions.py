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

class NextQuestionRequest(BaseModel):
    current_answers: dict  # Dictionary mapping question_id to answer

@questionsrouter.get("/getquestions")
async def get_questions():
    """Load questions from api_questions.json file"""
    try:
        with open("/Users/evanning/ovis/api_questions.json", "r") as f:
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
        with open("/Users/evanning/ovis/api_questions.json", "r") as f:
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

@questionsrouter.get("/getstreak")
def get_streak(username, db=Depends(get_db)):
    users = db["users"]
    user = users.find_one({"username": username}, {"_id": 0, "password": 0})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    today = datetime.now(timezone.utc).date()
    last_completion = user.get("last_completion")
    
    if not last_completion:
        # If no last_completion date, streak is 0
        return 0
    
    try:
        last_check_in = datetime.strptime(last_completion, "%m/%d/%Y").date()
    except (ValueError, TypeError):
        # If invalid date format, reset streak
        users.update_one({"username": username}, {"$set": {"streak": 0}})
        return 0

    if last_check_in == today:
        return user.get("streak", 0)
    elif last_check_in == today - timedelta(days=1):
        return user.get("streak", 0)
    else:
        users.update_one({"username": username}, {"$set": {"streak": 0}})
        return 0