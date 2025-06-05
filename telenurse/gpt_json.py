# Define the structured output format using OpenAI's function calling
assessment_function = {
    "name": "record_symptom_assessment",
    "description": "Record a comprehensive symptom assessment for a cancer patient based on conversation",
    "parameters": {
        "type": "object",
        "properties": {
            "timestamp": {
                "type": "string",
                "description": "Current date and time of the assessment"
            },
            "patient_id": {
                "type": "string",
                "description": "Unique identifier for the patient"
            },
            "symptoms": {
                "type": "object",
                "properties": {
                    "cough": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of cough frequency on 1-5 scale"
                            },
                            "severity_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of cough severity on 1-5 scale"
                            },
                            "key_indicators": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                                "description": "Patient quotes or observations indicating cough severity"
                            },
                            "additional_notes": {
                                "type": "string",
                                "description": "Contextual information about the cough"
                            }
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "nausea": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of nausea frequency on 1-5 scale"
                            },
                            "severity_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of nausea severity on 1-5 scale"
                            },
                            "key_indicators": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                                "description": "Patient quotes or observations indicating nausea severity"
                            },
                            "additional_notes": {
                                "type": "string",
                                "description": "Contextual information about the nausea"
                            }
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "lack_of_appetite": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of appetite issues frequency on 1-5 scale"
                            },
                            "severity_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of appetite issues severity on 1-5 scale"
                            },
                            "key_indicators": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                                "description": "Patient quotes or observations indicating appetite issues"
                            },
                            "additional_notes": {
                                "type": "string",
                                "description": "Contextual information about appetite issues"
                            }
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "fatigue": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of fatigue frequency on 1-5 scale"
                            },
                            "severity_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of fatigue severity on 1-5 scale"
                            },
                            "key_indicators": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                                "description": "Patient quotes or observations indicating fatigue severity"
                            },
                            "additional_notes": {
                                "type": "string",
                                "description": "Contextual information about the fatigue"
                            }
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    },
                    "pain": {
                        "type": "object",
                        "properties": {
                            "frequency_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of pain frequency on 1-5 scale"
                            },
                            "severity_rating": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 5,
                                "description": "Rating of pain severity on 1-5 scale"
                            },
                            "location": {
                                "type": "string",
                                "description": "Body location of pain if specified"
                            },
                            "key_indicators": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                                "description": "Patient quotes or observations indicating pain severity"
                            },
                            "additional_notes": {
                                "type": "string",
                                "description": "Contextual information about the pain"
                            }
                        },
                        "required": ["frequency_rating", "severity_rating", "key_indicators"]
                    }
                },
                "required": ["cough", "nausea", "lack_of_appetite", "fatigue", "pain"]
            },
            "flag_for_oncologist": {
                "type": "boolean",
                "description": "Whether symptoms meet criteria for oncologist notification"
            },
            "flag_reason": {
                "type": "string",
                "description": "Reason for flagging for oncologist attention if applicable"
            },
            "mood_assessment": {
                "type": "string",
                "description": "Brief assessment of patient's mood and mental state"
            },
            "conversation_notes": {
                "type": "string",
                "description": "Any additional relevant information from the conversation"
            },
            "oncologist_notification_level": {
                "type": "string",
                "enum": ["none", "amber", "red"],
                "description": "The urgency level for oncologist notification: none, amber (4 hours), or red (45 minutes)"
            },
            "treatment_status": {
                "type": "string",
                "enum": ["undergoing_treatment", "in_remission"],
                "description": "Whether the patient is currently undergoing treatment or in remission"
            }
        },
        "required": ["timestamp", "patient_id", "symptoms", "flag_for_oncologist", "oncologist_notification_level", "treatment_status"]
    }
}

# Function to determine if symptoms should be flagged for oncologist notification
# This follows the rules on pages 11-12 of the OnCallLogist functional plan
def should_flag_symptoms(symptoms, treatment_status):
    """
    Determine if symptoms should be flagged based on the OnCallLogist criteria
    
    Args:
        symptoms: Dict containing symptom assessments
        treatment_status: String "undergoing_treatment" or "in_remission"
        
    Returns:
        tuple: (flag_boolean, notification_level, reason)
    """
    # Logic for patients undergoing treatment
    if treatment_status == "undergoing_treatment":
        # Check for life-threatening symptoms (would return immediately)
        # This would be implemented based on specific symptoms defined in the application
        
        # Check for severe symptoms
        for symptom_name, symptom_data in symptoms.items():
            # Severe symptoms criteria: Symptoms persist for two consecutive days
            # They occur at least five times per day or are rated three or above
            freq = symptom_data["frequency_rating"]
            sev = symptom_data["severity_rating"]
            
            if (freq >= 5 or sev >= 3) and "persists for two consecutive days" in str(symptom_data).lower():
                return (True, "amber", f"Severe {symptom_name} - occurs frequently or at significant severity for two consecutive days")
            
            # OR occur at least three times per day and an increase in severity by at least one grade
            if freq >= 3 and "increase in severity" in str(symptom_data).lower():
                return (True, "amber", f"Significant increase in {symptom_name} severity")
        
        # Check for persistent symptoms
        for symptom_name, symptom_data in symptoms.items():
            freq = symptom_data["frequency_rating"]
            sev = symptom_data["severity_rating"]
            
            # Persistent symptoms: persist for six consecutive days, occur at least three times per day and are rated two or above
            if freq >= 3 and sev >= 2 and "six consecutive days" in str(symptom_data).lower():
                return (True, "amber", f"Persistent {symptom_name} - continued for six days at moderate levels")
    
    # Logic for patients in remission
    elif treatment_status == "in_remission":
        # Check for severe symptoms
        for symptom_name, symptom_data in symptoms.items():
            freq = symptom_data["frequency_rating"]
            sev = symptom_data["severity_rating"]
            
            # Severe symptoms: persist for three consecutive days
            # Occur at least seven times per day or are rated seven or above
            if (freq >= 4 or sev >= 4) and "three consecutive days" in str(symptom_data).lower():
                return (True, "amber", f"Severe {symptom_name} in remission patient - high frequency or severity for three consecutive days")
            
            # OR occur at least three times per day and an increase in severity by at least two grades
            if freq >= 3 and "increase in severity by at least two" in str(symptom_data).lower():
                return (True, "amber", f"Significant increase in {symptom_name} severity in remission patient")
        
        # Check for persistent symptoms
        for symptom_name, symptom_data in symptoms.items():
            freq = symptom_data["frequency_rating"]
            sev = symptom_data["severity_rating"]
            
            # Persistent symptoms: persist for five days, occur at least three times per day and rated four or above
            if freq >= 3 and sev >= 4 and "five days" in str(symptom_data).lower():
                return (True, "amber", f"Persistent {symptom_name} in remission patient - continued for five days at moderate-high levels")
    
    # Default - no flagging needed
    return (False, "none", "")

# Example of how the oncologist flag determination could be used
def process_assessment(assessment_data):
    """Process the assessment and determine if oncologist should be notified"""
    
    # Extract data
    symptoms = assessment_data["symptoms"]
    treatment_status = assessment_data["treatment_status"]
    
    # Check if symptoms should be flagged
    should_flag, level, reason = should_flag_symptoms(symptoms, treatment_status)
    
    # Update the assessment data
    assessment_data["flag_for_oncologist"] = should_flag
    assessment_data["oncologist_notification_level"] = level
    
    if should_flag:
        assessment_data["flag_reason"] = reason
    
    return assessment_data