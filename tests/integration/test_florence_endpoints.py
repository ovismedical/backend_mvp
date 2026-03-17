"""
Integration tests for Florence AI endpoints (/florence/*).
Uses mocked OpenAI client to avoid real API calls.
"""

import pytest
from unittest.mock import patch, MagicMock

from tests.mock_openai import (
    mock_chat_completion,
    mock_assessment_function_call,
    mock_triage_function_call,
    make_mock_openai_client,
)


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Clear active sessions before each test."""
    from app.florence import active_sessions
    active_sessions.clear()
    yield
    active_sessions.clear()


@pytest.fixture
def mock_florence_ai():
    """Patch FlorenceAI and assessment/triage modules with mock OpenAI clients."""
    chat_client = make_mock_openai_client(chat_response=mock_chat_completion("Hello! How are you feeling?"))
    assessment_client = make_mock_openai_client(function_call_response=mock_assessment_function_call())
    triage_client = make_mock_openai_client(function_call_response=mock_triage_function_call())

    with (
        patch("app.florence_ai.florence_ai") as mock_ai,
        patch("app.florence_assessment.florence_assessment") as mock_assess,
        patch("app.florence_triage.florence_triage") as mock_triage_inst,
    ):
        # Configure FlorenceAI mock
        mock_ai.client = chat_client
        mock_ai.model = "gpt-4"
        mock_ai.language = "en"
        mock_ai.system_prompt = "You are Florence."
        mock_ai.set_language = MagicMock()

        # Make start_conversation and process_message return proper dicts
        async def fake_start(name="there"):
            return {"response": "Hello! How are you feeling today?", "conversation_state": "starting"}

        async def fake_process(msg, history):
            return {"response": "I understand. Tell me more.", "conversation_state": "assessing"}

        mock_ai.start_conversation = fake_start
        mock_ai.process_message = fake_process
        mock_ai.initialize = MagicMock(return_value=True)

        # Configure assessment mock
        mock_assess.client = assessment_client
        mock_assess.initialize = MagicMock(return_value=True)

        async def fake_assessment(history, patient_id, treatment_status, language):
            return {
                "structured_assessment": {
                    "timestamp": "2026-03-17T00:00:00+00:00",
                    "patient_id": patient_id,
                    "symptoms": {
                        "cough": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                        "nausea": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                        "lack_of_appetite": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                        "fatigue": {"frequency_rating": 2, "severity_rating": 2, "key_indicators": ["tired"]},
                        "pain": {"frequency_rating": 1, "severity_rating": 1, "key_indicators": []},
                    },
                    "flag_for_oncologist": False,
                    "oncologist_notification_level": "none",
                    "treatment_status": treatment_status,
                },
                "conversation_length": len(history),
            }

        mock_assess.generate_structured_assessment = fake_assessment

        # Configure triage mock
        mock_triage_inst.client = triage_client
        mock_triage_inst.initialize = MagicMock(return_value=True)

        async def fake_triage(history, patient_id, treatment_status, language):
            return {
                "triage_assessment": {
                    "timestamp": "2026-03-17T00:00:00+00:00",
                    "patient_id": patient_id,
                    "clinical_reasoning": "Mild symptoms.",
                    "diagnosis_predictions": [{"suspected_diagnosis": "Side effects", "probability": "medium", "urgency": 2, "reasoning": "Common."}],
                    "alert_level": "GREEN",
                    "alert_rationale": "Normal.",
                    "key_symptoms": ["fatigue"],
                    "recommended_timeline": "Routine",
                    "confidence_level": "medium",
                    "clinical_notes": "",
                    "treatment_status": treatment_status,
                },
                "alert_level": "GREEN",
            }

        mock_triage_inst.generate_triage_assessment = fake_triage

        yield {
            "ai": mock_ai,
            "assessment": mock_assess,
            "triage": mock_triage_inst,
        }


class TestFlorenceTest:

    async def test_florence_test_endpoint(self, client):
        response = await client.get("/florence/test")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "active_sessions" in body


class TestStartSession:

    async def test_start_session(self, client, patient_headers, mock_florence_ai):
        response = await client.post(
            "/florence/start_session",
            json={"language": "en", "input_mode": "keyboard", "treatment_status": "undergoing_treatment"},
            headers=patient_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert "session_id" in body
        assert body["status"] == "active"
        assert len(body["message"]) > 0

    async def test_start_session_no_auth(self, client):
        response = await client.post(
            "/florence/start_session",
            json={"language": "en"},
        )
        assert response.status_code == 401


class TestSendMessage:

    async def _start_session(self, client, headers, mock_florence_ai):
        resp = await client.post(
            "/florence/start_session",
            json={"language": "en", "input_mode": "keyboard", "treatment_status": "undergoing_treatment"},
            headers=headers,
        )
        return resp.json()["session_id"]

    async def test_send_message(self, client, patient_headers, mock_florence_ai):
        session_id = await self._start_session(client, patient_headers, mock_florence_ai)
        response = await client.post(
            "/florence/send_message",
            json={"session_id": session_id, "message": "I feel tired today"},
            headers=patient_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert len(body["response"]) > 0

    async def test_send_message_invalid_session(self, client, patient_headers):
        response = await client.post(
            "/florence/send_message",
            json={"session_id": "nonexistent_123", "message": "Hello"},
            headers=patient_headers,
        )
        assert response.status_code == 404

    async def test_send_message_wrong_user(self, client, patient_headers, doctor_headers, mock_florence_ai):
        session_id = await self._start_session(client, patient_headers, mock_florence_ai)
        # Doctor tries to send message to patient's session
        response = await client.post(
            "/florence/send_message",
            json={"session_id": session_id, "message": "Hello"},
            headers=doctor_headers,
        )
        assert response.status_code == 403


class TestGetSession:

    async def test_get_session(self, client, patient_headers, mock_florence_ai):
        # Start a session first
        resp = await client.post(
            "/florence/start_session",
            json={"language": "en"},
            headers=patient_headers,
        )
        session_id = resp.json()["session_id"]

        # Get session
        response = await client.get(
            f"/florence/session/{session_id}",
            headers=patient_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == session_id
        assert "conversation_history" in body

    async def test_get_nonexistent_session(self, client, patient_headers):
        response = await client.get(
            "/florence/session/fake_session_123",
            headers=patient_headers,
        )
        assert response.status_code == 404
