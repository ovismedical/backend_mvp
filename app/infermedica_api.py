import requests
import json
from typing import Dict, List, Optional, Any
from pydantic import BaseModel
from enum import Enum

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

class PatientInfo(BaseModel):
    sex: Sex
    age: Dict[str, int]
    evidence: List[Evidence] = []

class InfermedicaAPI:
    """
    Python wrapper for the Infermedica API
    Provides medical diagnosis suggestions based on symptoms
    """
    
    def __init__(self, app_id: str, app_key: str, model: str = "infermedica-en"):
        self.app_id = app_id
        self.app_key = app_key
        self.model = model
        self.base_url = "https://api.infermedica.com/v3"
        
        self.headers = {
            "App-Id": self.app_id,
            "App-Key": self.app_key,
            "Content-Type": "application/json",
            "Model": self.model
        }
    
    def _make_request(self, endpoint: str, method: str = "GET", data: dict = None) -> dict:
        """Make HTTP request to Infermedica API"""
        url = f"{self.base_url}/{endpoint}"
        
        try:
            if method == "GET":
                response = requests.get(url, headers=self.headers)
            elif method == "POST":
                response = requests.post(url, headers=self.headers, json=data)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"API request failed: {str(e)}")
    
    def get_symptoms(self) -> List[dict]:
        """Get list of all available symptoms"""
        return self._make_request("symptoms")
    
    def get_conditions(self) -> List[dict]:
        """Get list of all available conditions/diseases"""
        return self._make_request("conditions")
    
    def get_risk_factors(self) -> List[dict]:
        """Get list of all available risk factors"""
        return self._make_request("risk_factors")
    
    def parse_text(self, text: str, context: List[str] = None) -> dict:
        """
        Parse natural language text to extract medical concepts
        
        Args:
            text: Natural language description of symptoms
            context: List of concept IDs for context
        """
        data = {"text": text}
        if context:
            data["context"] = context
            
        return self._make_request("parse", "POST", data)
    
    def get_diagnosis(self, patient_info: PatientInfo) -> dict:
        """
        Get diagnosis suggestions based on patient information
        
        Args:
            patient_info: PatientInfo object containing age, sex, and evidence
        """
        data = patient_info.dict()
        return self._make_request("diagnosis", "POST", data)
    
    def get_triage(self, patient_info: PatientInfo) -> dict:
        """
        Get triage recommendations (emergency level)
        
        Args:
            patient_info: PatientInfo object containing age, sex, and evidence
        """
        data = patient_info.dict()
        return self._make_request("triage", "POST", data)
    
    def get_explanation(self, patient_info: PatientInfo, target_id: str) -> dict:
        """
        Get explanation for a specific condition
        
        Args:
            patient_info: PatientInfo object
            target_id: ID of the condition to explain
        """
        data = patient_info.dict()
        data["target"] = target_id
        return self._make_request("explain", "POST", data)
    
    def suggest_questions(self, patient_info: PatientInfo, max_results: int = 5) -> dict:
        """
        Get suggested questions to ask the patient
        
        Args:
            patient_info: PatientInfo object
            max_results: Maximum number of questions to return
        """
        data = patient_info.dict()
        data["max_results"] = max_results
        return self._make_request("suggest", "POST", data)
    
    def get_concept_details(self, concept_id: str, concept_type: str = "symptom") -> dict:
        """
        Get detailed information about a specific concept (symptom, condition, etc.)
        
        Args:
            concept_id: ID of the concept
            concept_type: Type of concept (symptom, condition, risk_factor)
        """
        endpoint_map = {
            "symptom": "symptoms",
            "condition": "conditions", 
            "risk_factor": "risk_factors"
        }
        
        endpoint = endpoint_map.get(concept_type, "symptoms")
        return self._make_request(f"{endpoint}/{concept_id}")

class InfermedicaService:
    """
    High-level service for medical diagnosis using Infermedica API
    """
    
    def __init__(self, app_id: str, app_key: str):
        self.api = InfermedicaAPI(app_id, app_key)
    
    def analyze_symptoms(self, text: str, age: int, sex: str) -> dict:
        """
        Analyze symptoms from natural language text
        
        Args:
            text: Patient's description of symptoms
            age: Patient's age
            sex: Patient's sex ('male' or 'female')
        """
        try:
            # Parse the text to extract medical concepts
            parsed = self.api.parse_text(text)
            
            # Convert parsed mentions to evidence
            evidence = []
            for mention in parsed.get("mentions", []):
                evidence.append(Evidence(
                    id=mention["id"],
                    choice_id=EvidenceChoiceId.PRESENT,
                    source="initial"
                ))
            
            # Create patient info
            patient_info = PatientInfo(
                sex=Sex(sex.lower()),
                age={"value": age, "unit": "year"},
                evidence=evidence
            )
            
            # Get diagnosis and triage
            diagnosis = self.api.get_diagnosis(patient_info)
            triage = self.api.get_triage(patient_info)
            
            return {
                "parsed_symptoms": parsed,
                "diagnosis": diagnosis,
                "triage": triage,
                "patient_info": patient_info.dict()
            }
            
        except Exception as e:
            return {"error": str(e)}
    
    def get_follow_up_questions(self, patient_info: PatientInfo) -> List[dict]:
        """Get follow-up questions to refine diagnosis"""
        try:
            suggestions = self.api.suggest_questions(patient_info)
            return suggestions.get("questions", [])
        except Exception as e:
            return [{"error": str(e)}]
    
    def evaluate_urgency(self, symptoms: List[str], age: int, sex: str) -> dict:
        """
        Evaluate the urgency of symptoms
        
        Args:
            symptoms: List of symptom IDs
            age: Patient's age
            sex: Patient's sex
        """
        try:
            evidence = []
            for symptom_id in symptoms:
                evidence.append(Evidence(
                    id=symptom_id,
                    choice_id=EvidenceChoiceId.PRESENT
                ))
            
            patient_info = PatientInfo(
                sex=Sex(sex.lower()),
                age={"value": age, "unit": "year"},
                evidence=evidence
            )
            
            triage = self.api.get_triage(patient_info)
            
            return {
                "urgency_level": triage.get("triage_level"),
                "description": triage.get("description"),
                "recommendations": triage.get("recommendations", [])
            }
            
        except Exception as e:
            return {"error": str(e)}

# Example usage
if __name__ == "__main__":
    # Initialize the service (you need to provide your API credentials)
    # app_id = "your_app_id"
    # app_key = "your_app_key"
    
    # service = InfermedicaService(app_id, app_key)
    
    # Example: Analyze symptoms
    # result = service.analyze_symptoms(
    #     text="I have a headache and fever for 2 days",
    #     age=30,
    #     sex="male"
    # )
    # print(json.dumps(result, indent=2))
    
    print("Infermedica API service initialized. Add your credentials to use.")