"""
Integration tests for authentication endpoints (/token, /userinfo, /updateinfo).
"""

import pytest


class TestLoginEndpoint:

    async def test_login_valid_patient(self, client, seeded_db):
        response = await client.post(
            "/token",
            data={"username": "testpatient", "password": "testpass123"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "Bearer"

    async def test_login_valid_doctor(self, client, seeded_db):
        response = await client.post(
            "/token",
            data={"username": "testdoctor", "password": "doctorpass123"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body

    async def test_login_invalid_password(self, client, seeded_db):
        response = await client.post(
            "/token",
            data={"username": "testpatient", "password": "wrongpassword"},
        )
        assert response.status_code == 200  # App returns 200 with details
        body = response.json()
        assert "details" in body
        assert "Invalid" in body["details"]

    async def test_login_nonexistent_user(self, client, seeded_db):
        response = await client.post(
            "/token",
            data={"username": "nobody", "password": "whatever"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "details" in body


class TestUserInfoEndpoint:

    async def test_get_userinfo_authenticated(self, client, patient_headers):
        response = await client.get("/userinfo", headers=patient_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "testpatient"
        assert "password" not in body  # Excluded by projection

    async def test_get_userinfo_no_token(self, client):
        response = await client.get("/userinfo")
        assert response.status_code == 401

    async def test_get_userinfo_expired_token(self, client, expired_token):
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = await client.get("/userinfo", headers=headers)
        assert response.status_code == 401

    async def test_get_userinfo_invalid_token(self, client):
        headers = {"Authorization": "Bearer totally.invalid.token"}
        response = await client.get("/userinfo", headers=headers)
        assert response.status_code == 401

    async def test_get_doctor_info(self, client, doctor_headers):
        response = await client.get("/userinfo", headers=doctor_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "testdoctor"


class TestUpdateInfoEndpoint:

    async def test_update_user_info(self, client, patient_headers):
        payload = {
            "full_name": "Updated Name",
            "birthdate": "01/01/1990",
            "gender": "male",
            "height": 175,
            "weight": 72,
            "bloodtype": "A+",
            "fitness_level": 4,
            "exercises": ["running", "swimming"],
            "checkups": "quarterly",
        }
        response = await client.post(
            "/updateinfo", json=payload, headers=patient_headers
        )
        assert response.status_code == 200

    async def test_update_info_no_auth(self, client):
        payload = {
            "full_name": "Hacker",
            "birthdate": "01/01/2000",
            "height": 180,
            "weight": 80,
            "fitness_level": 1,
            "exercises": [],
        }
        response = await client.post("/updateinfo", json=payload)
        assert response.status_code == 401


class TestHealthEndpoints:

    async def test_root(self, client):
        response = await client.get("/")
        assert response.status_code == 200
        body = response.json()
        assert "OVIS" in body["message"]

    async def test_render_health(self, client):
        response = await client.get("/render-health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
