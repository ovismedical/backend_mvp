"""
Tests for app.login — password hashing, JWT creation, code verification.
"""

import pytest
from datetime import datetime, timezone
from jose import jwt

from app.login import (
    verify_password,
    hash_password,
    create_access_token,
    verify_code,
)


class TestPasswordHashing:

    def test_hash_and_verify(self):
        hashed = hash_password("secret123")
        assert verify_password("secret123", hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("secret123")
        assert verify_password("wrongpass", hashed) is False

    def test_hash_is_not_plaintext(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"
        assert hashed.startswith("$2b$")


class TestCreateAccessToken:

    def test_creates_valid_jwt(self):
        token = create_access_token({"sub": "alice"})
        payload = jwt.decode(token, "test-secret-key-for-testing-only", algorithms=["HS256"])
        assert payload["sub"] == "alice"

    def test_contains_expiry(self):
        token = create_access_token({"sub": "alice"})
        payload = jwt.decode(token, "test-secret-key-for-testing-only", algorithms=["HS256"])
        assert "exp" in payload

    def test_admin_flag_false_by_default(self):
        token = create_access_token({"sub": "alice"})
        payload = jwt.decode(token, "test-secret-key-for-testing-only", algorithms=["HS256"])
        assert payload["admin"] is False

    def test_admin_flag_true_when_set(self):
        token = create_access_token({"sub": "dr_smith"}, admin=True)
        payload = jwt.decode(token, "test-secret-key-for-testing-only", algorithms=["HS256"])
        assert payload["admin"] is True


class TestVerifyCode:

    def test_valid_doctor_code(self, seeded_db):
        """When a valid doctor code is provided, user gets linked to that doctor."""
        from unittest.mock import patch
        with patch("app.login.get_db", return_value=seeded_db):
            user_dict = {
                "username": "newpatient",
                "access_code": "ABCD",
                "password": "hashed",
                "email": "new@test.com",
            }
            result = verify_code("ABCD", user_dict)
            assert result["isDoctor"] is False
            assert result["doctor"] == "testdoctor"
            assert "access_code" not in result

    def test_valid_hospital_code(self, seeded_db):
        """When a valid hospital code is provided, user becomes a doctor."""
        from unittest.mock import patch
        with patch("app.login.get_db", return_value=seeded_db):
            user_dict = {
                "username": "newdoctor",
                "access_code": "HOSP",
                "password": "hashed",
                "email": "dr@test.com",
            }
            result = verify_code("HOSP", user_dict)
            assert result["isDoctor"] is True
            assert result["hospital"] == "Test Hospital"

    def test_invalid_code_raises(self, seeded_db):
        """Invalid access code raises HTTPException."""
        from unittest.mock import patch
        from fastapi import HTTPException
        with patch("app.login.get_db", return_value=seeded_db):
            user_dict = {
                "username": "newuser",
                "access_code": "ZZZZ",
                "password": "hashed",
                "email": "x@test.com",
            }
            with pytest.raises(HTTPException) as exc_info:
                verify_code("ZZZZ", user_dict)
            assert exc_info.value.status_code == 400
