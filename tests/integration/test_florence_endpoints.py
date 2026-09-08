"""
Integration tests for Florence AI endpoints (/florence/*).
Runs the real code paths against a FakeProvider installed in the inference gateway.
"""

import pytest

from app.inference import InferenceGateway, reset_gateway
from app.florence import wait_for_background
from tests.mock_openai import FakeProvider, fake_gateway


@pytest.fixture
def mock_florence_ai():
    """Install a gateway backed by a FakeProvider so Florence runs its real code paths."""
    provider = FakeProvider(name="openai")
    reset_gateway(fake_gateway(openai=provider))
    yield provider
    reset_gateway(InferenceGateway({}))


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


class TestFinishSession:

    async def _start(self, client, headers, language="en"):
        resp = await client.post(
            "/florence/start_session",
            json={"language": language, "input_mode": "keyboard", "treatment_status": "undergoing_treatment"},
            headers=headers,
        )
        return resp.json()["session_id"]

    async def test_finish_returns_immediately_then_analysis_lands_in_background(self, client, patient_headers, seeded_db, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        await client.post("/florence/send_message", json={"session_id": session_id, "message": "I feel very tired"},
                          headers=patient_headers)

        response = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["triage_status"] == "generating"
        assert body["alert_level"] is None          # not known yet
        assert body["ai_available"] is True

        # the conversation is already persisted, marked pending
        saved = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert saved["triage_status"] == "generating" and saved["alert_level"] == "PENDING"
        assert saved["structured_assessment"] is None
        assert set(saved["user_info"]) == {"username", "full_name"}
        assert seeded_db["florence_sessions"].find_one({"session_id": session_id})["status"] == "completed"

        # polling: with the instant FakeProvider the background task may already have landed
        pending = (await client.get(f"/florence/result/{session_id}", headers=patient_headers)).json()
        assert pending["triage_status"] in ("generating", "completed")
        if pending["triage_status"] == "generating":
            assert pending["alert_level"] is None

        await wait_for_background()

        done = (await client.get(f"/florence/result/{session_id}", headers=patient_headers)).json()
        assert done["triage_status"] == "completed"
        assert done["alert_level"] == "GREEN"
        assert done["structured_assessment"]["symptoms"]["fatigue"]["severity_rating"] == 3
        assert done["triage_assessment"]["alert_level"] == "GREEN"

        saved = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert saved["alert_level"] == "GREEN" and saved["triage_status"] == "completed"
        tasks = [r.task for r in mock_florence_ai.requests]
        assert tasks.count("symptom_assessment") == 1 and tasks.count("triage") == 1

    async def test_finishing_twice_returns_the_saved_result(self, client, patient_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        await wait_for_background()
        again = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert again.status_code == 200 and again.json()["alert_level"] == "GREEN"

    async def test_result_is_owner_only(self, client, patient_headers, doctor_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert (await client.get(f"/florence/result/{session_id}", headers=doctor_headers)).status_code == 403
        assert (await client.get("/florence/result/nope", headers=patient_headers)).status_code == 404

    async def test_finish_session_is_tracked_per_session_language(self, client, patient_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers, language="zh-HK")
        await client.post("/florence/send_message", json={"session_id": session_id, "message": "我好攰"}, headers=patient_headers)
        await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        await wait_for_background()
        languages = {r.task: r.language for r in mock_florence_ai.requests}
        assert languages["chat_turn"] == "zh-HK"
        assert languages["triage"] == "zh-HK"

    async def test_finish_session_wrong_user(self, client, patient_headers, doctor_headers, mock_florence_ai):
        session_id = await self._start(client, patient_headers)
        response = await client.post(f"/florence/finish_session/{session_id}", headers=doctor_headers)
        assert response.status_code == 403


class TestFallbackWithoutProvider:
    """No inference provider configured: chat still works with placeholder text and nothing 500s."""

    async def test_start_and_finish_in_fallback_mode(self, client, patient_headers, seeded_db):
        reset_gateway(InferenceGateway({}))
        resp = await client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ai_available"] is False
        session_id = body["session_id"]

        resp = await client.post("/florence/send_message", json={"session_id": session_id, "message": "hi"}, headers=patient_headers)
        assert resp.status_code == 200

        resp = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert resp.status_code == 200
        assert resp.json()["triage_status"] == "skipped" and resp.json()["alert_level"] is None
        saved = seeded_db["florence_assessments"].find_one({"session_id": session_id})
        assert saved["structured_assessment"] is None
        assert saved["ai_powered"] is False and saved["alert_level"] == "UNKNOWN"

    async def test_provider_failure_falls_back_conservatively(self, client, patient_headers, seeded_db):
        provider = FakeProvider(name="openai")
        reset_gateway(fake_gateway(openai=provider))
        session_id = (await client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers)).json()["session_id"]
        provider.fail = True  # provider dies mid-session
        resp = await client.post("/florence/send_message", json={"session_id": session_id, "message": "hi"}, headers=patient_headers)
        assert resp.status_code == 200  # placeholder reply, no crash
        resp = await client.post(f"/florence/finish_session/{session_id}", headers=patient_headers)
        assert resp.status_code == 200
        await wait_for_background()
        result = (await client.get(f"/florence/result/{session_id}", headers=patient_headers)).json()
        assert result["alert_level"] == "YELLOW"  # conservative triage fallback
        reset_gateway(InferenceGateway({}))


class TestSessionIds:

    async def test_rapid_double_start_yields_distinct_sessions(self, client, patient_headers, mock_florence_ai):
        import asyncio
        a, b = await asyncio.gather(
            client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers),
            client.post("/florence/start_session", json={"language": "en"}, headers=patient_headers),
        )
        assert a.status_code == 200 and b.status_code == 200
        assert a.json()["session_id"] != b.json()["session_id"]
        assert a.json()["session_id"].startswith("testpatient_")
