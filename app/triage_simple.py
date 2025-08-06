import requests
import uuid
from typing import Dict, List, Optional
from pydantic import BaseModel
from enum import Enum
from datetime import datetime

class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"

class EvidenceChoiceId(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"

class Evidence(BaseModel):
    id: str
    choice_id: EvidenceChoiceId
    source: str = "initial"

class TriageInterview(BaseModel):
    """Model for triage interview session"""
    interview_id: str
    age: Dict[str, int]
    sex: Sex
    evidence: List[Evidence] = []
    chief_complaint: Optional[str] = None
    should_stop: bool = False

class InfermedicaTriageAPI:
    """
    Simple Infermedica Platform API wrapper for triage only
    """
    
    def __init__(self, app_id: str, app_key: str, model: str = "infermedica-en"):
        self.app_id = app_id
        self.app_key = app_key
        self.model = model
        self.base_url = "https://api.infermedica.com/platform"
        
        self.headers = {
            "App-Id": self.app_id,
            "App-Key": self.app_key,
            "Content-Type": "application/json",
            "Model": self.model
        }
    
    def _make_request(self, endpoint: str, method: str = "GET", data: dict = None) -> dict:
        """Make HTTP request to Infermedica Platform API"""
        url = f"{self.base_url}/{endpoint}"
        
        try:
            if method == "GET":
                response = requests.get(url, headers=self.headers)
            elif method == "POST":
                response = requests.post(url, headers=self.headers, json=data)
            elif method == "PUT":
                response = requests.put(url, headers=self.headers, json=data)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Triage API request failed: {str(e)}")
    
    def create_interview(self, age: int, sex: str, chief_complaint: str = None) -> dict:
        """Create a new triage interview session"""
        interview_id = str(uuid.uuid4())
        
        data = {
            "age": {"value": age, "unit": "year"},
            "sex": sex.lower(),
            "evidence": []
        }
        
        if chief_complaint:
            data["chief_complaint"] = chief_complaint
            
        result = self._make_request(f"interviews/{interview_id}", "PUT", data)
        result["interview_id"] = interview_id
        return result
    
    def get_question(self, interview_id: str, evidence: List[Evidence] = None) -> dict:
        """Get next triage question"""
        data = {}
        if evidence:
            data["evidence"] = [e.dict() for e in evidence]
            
        return self._make_request(f"interviews/{interview_id}/next", "POST", data)
    
    def submit_evidence(self, interview_id: str, evidence: List[Evidence]) -> dict:
        """Submit evidence (answers) to the interview"""
        data = {"evidence": [e.dict() for e in evidence]}
        return self._make_request(f"interviews/{interview_id}/evidence", "POST", data)
    
    def get_triage_result(self, interview_id: str) -> dict:
        """Get final triage result"""
        return self._make_request(f"interviews/{interview_id}/triage", "GET")

class TriageService:
    """
    Simple triage service for managing triage interviews
    """
    
    def __init__(self, app_id: str, app_key: str):
        self.api = InfermedicaTriageAPI(app_id, app_key)
        self.active_interviews = {}
    
    def start_interview(self, age: int, sex: str, chief_complaint: str = None) -> dict:
        """Start a new triage interview"""
        try:
            result = self.api.create_interview(age, sex, chief_complaint)
            interview_id = result.get("interview_id")
            
            interview = TriageInterview(
                interview_id=interview_id,
                age={"value": age, "unit": "year"},
                sex=Sex(sex.lower()),
                evidence=[],
                chief_complaint=chief_complaint
            )
            
            self.active_interviews[interview_id] = interview
            
            question_result = self.api.get_question(interview_id)
            
            return {
                "interview_id": interview_id,
                "question": question_result.get("question"),
                "should_stop": question_result.get("should_stop", False),
                "status": "active"
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def get_next_question(self, interview_id: str) -> dict:
        """Get the next question or final result"""
        try:
            if interview_id not in self.active_interviews:
                return {"error": "Interview not found"}
            
            interview = self.active_interviews[interview_id]
            
            if interview.should_stop:
                triage_result = self.api.get_triage_result(interview_id)
                del self.active_interviews[interview_id]
                
                return {
                    "interview_id": interview_id,
                    "completed": True,
                    "triage_result": triage_result,
                    "status": "completed"
                }
            
            question_result = self.api.get_question(interview_id, interview.evidence)
            
            should_stop = question_result.get("should_stop", False)
            if should_stop:
                interview.should_stop = True
                triage_result = self.api.get_triage_result(interview_id)
                del self.active_interviews[interview_id]
                
                return {
                    "interview_id": interview_id,
                    "completed": True,
                    "triage_result": triage_result,
                    "status": "completed"
                }
            
            return {
                "interview_id": interview_id,
                "question": question_result.get("question"),
                "status": "active"
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def answer_question(self, interview_id: str, answers: List[dict]) -> dict:
        """Answer a question and get the next question or final result"""
        try:
            if interview_id not in self.active_interviews:
                return {"error": "Interview not found"}
            
            interview = self.active_interviews[interview_id]
            
            new_evidence = []
            for answer in answers:
                evidence = Evidence(
                    id=answer["id"],
                    choice_id=EvidenceChoiceId(answer["choice_id"]),
                    source="user"
                )
                new_evidence.append(evidence)
                interview.evidence.append(evidence)
            
            self.api.submit_evidence(interview_id, new_evidence)
            
            return self.get_next_question(interview_id)
            
        except Exception as e:
            return {"error": str(e)}
    
    def get_status(self, interview_id: str) -> dict:
        """Get interview status"""
        if interview_id not in self.active_interviews:
            return {"error": "Interview not found"}
        
        interview = self.active_interviews[interview_id]
        
        return {
            "interview_id": interview_id,
            "status": "completed" if interview.should_stop else "active",
            "questions_answered": len(interview.evidence),
            "age": interview.age,
            "sex": interview.sex.value,
            "chief_complaint": interview.chief_complaint
        }
    
    def complete_interview(self, interview_id: str) -> dict:
        """Force complete interview and get triage result"""
        try:
            if interview_id not in self.active_interviews:
                return {"error": "Interview not found"}
            
            triage_result = self.api.get_triage_result(interview_id)
            del self.active_interviews[interview_id]
            
            return {
                "interview_id": interview_id,
                "completed": True,
                "triage_result": triage_result
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def get_active_interviews(self) -> dict:
        """Get list of active interviews"""
        return {
            "active_interviews": [
                {
                    "interview_id": interview_id,
                    "age": interview.age,
                    "sex": interview.sex.value,
                    "questions_answered": len(interview.evidence),
                    "chief_complaint": interview.chief_complaint
                }
                for interview_id, interview in self.active_interviews.items()
            ],
            "count": len(self.active_interviews)
        }