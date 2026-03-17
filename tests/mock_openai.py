"""
OpenAI mock helpers for testing Florence AI without real API calls.
"""

import json
from unittest.mock import MagicMock, AsyncMock


class MockChoice:
    """Mock an OpenAI ChatCompletion choice."""

    def __init__(self, content=None, function_call=None):
        self.message = MagicMock()
        self.message.content = content
        self.message.function_call = function_call


class MockFunctionCall:
    """Mock an OpenAI function_call response."""

    def __init__(self, name, arguments):
        self.name = name
        self.arguments = json.dumps(arguments) if isinstance(arguments, dict) else arguments


class MockCompletion:
    """Mock an OpenAI ChatCompletion response."""

    def __init__(self, content=None, function_call=None):
        self.choices = [MockChoice(content=content, function_call=function_call)]


def mock_chat_completion(content="Hello! How are you feeling today?"):
    """Create a mock ChatCompletion with a text response."""
    return MockCompletion(content=content)


def mock_function_call_completion(name, arguments):
    """Create a mock ChatCompletion with a function_call response."""
    fc = MockFunctionCall(name, arguments)
    return MockCompletion(function_call=fc)


def mock_assessment_function_call(
    patient_id="testpatient",
    treatment_status="undergoing_treatment",
    symptoms=None,
):
    """Create a mock assessment function call response with realistic data."""
    if symptoms is None:
        symptoms = {
            "cough": {"frequency_rating": 2, "severity_rating": 2, "key_indicators": ["occasional dry cough"]},
            "nausea": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
            "lack_of_appetite": {"frequency_rating": 2, "severity_rating": 2, "key_indicators": ["reduced meals"]},
            "fatigue": {"frequency_rating": 3, "severity_rating": 3, "key_indicators": ["affects daily activities"]},
            "pain": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
        }

    args = {
        "timestamp": "2026-03-17T00:00:00+00:00",
        "patient_id": patient_id,
        "symptoms": symptoms,
        "flag_for_oncologist": False,
        "oncologist_notification_level": "none",
        "treatment_status": treatment_status,
        "mood_assessment": "Patient appears in stable mood.",
        "conversation_notes": "Standard check-in conversation.",
    }
    return mock_function_call_completion("record_symptom_assessment", args)


def mock_triage_function_call(
    patient_id="testpatient",
    alert_level="GREEN",
    treatment_status="undergoing_treatment",
):
    """Create a mock triage function call response."""
    args = {
        "timestamp": "2026-03-17T00:00:00+00:00",
        "patient_id": patient_id,
        "clinical_reasoning": "Symptoms are mild and within expected range.",
        "diagnosis_predictions": [
            {
                "suspected_diagnosis": "Treatment side effects",
                "probability": "medium",
                "urgency": 2,
                "reasoning": "Common side effects during treatment.",
            }
        ],
        "alert_level": alert_level,
        "alert_rationale": "Symptoms within normal parameters.",
        "key_symptoms": ["fatigue"],
        "recommended_timeline": "Routine follow-up",
        "confidence_level": "medium",
        "clinical_notes": "",
        "treatment_status": treatment_status,
    }
    return mock_function_call_completion("record_triage_assessment", args)


def make_mock_openai_client(chat_response=None, function_call_response=None):
    """Create a fully mocked OpenAI client.

    Returns a MagicMock that behaves like openai.OpenAI() with
    client.chat.completions.create() returning the specified response.
    """
    client = MagicMock()

    if function_call_response is not None:
        client.chat.completions.create.return_value = function_call_response
    elif chat_response is not None:
        client.chat.completions.create.return_value = chat_response
    else:
        client.chat.completions.create.return_value = mock_chat_completion()

    return client
