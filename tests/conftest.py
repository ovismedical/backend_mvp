"""
Root conftest for OVIS backend tests.
Provides fixtures for test client, mock database, authentication, and OpenAI mocking.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from jose import jwt
from datetime import datetime, timezone, timedelta

from .factories import make_user, make_doctor


# ---------------------------------------------------------------------------
# Environment — set before any app imports
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    """Set required environment variables and patch module-level constants."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-for-testing-only")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("MONGODB_DB", "ovis-test")
    # login.py reads SECRET_KEY at import time — patch the module-level variable
    import app.login as login_mod
    monkeypatch.setattr(login_mod, "SECRET_KEY", "test-secret-key-for-testing-only")


# ---------------------------------------------------------------------------
# Mock MongoDB
# ---------------------------------------------------------------------------
class MockCollection:
    """Simple in-memory MongoDB collection mock."""

    def __init__(self):
        self._docs = []
        self._id_counter = 0

    def insert_one(self, doc):
        self._id_counter += 1
        doc = doc.copy()
        doc["_id"] = self._id_counter
        self._docs.append(doc)
        result = MagicMock()
        result.inserted_id = self._id_counter
        result.upserted_id = None
        return result

    def find_one(self, filter_dict=None, projection=None):
        for doc in self._docs:
            if self._matches(doc, filter_dict or {}):
                result = doc.copy()
                if projection:
                    # Handle exclusion projection
                    for key, val in projection.items():
                        if val == 0 and key in result:
                            del result[key]
                return result
        return None

    def find(self, filter_dict=None, projection=None):
        results = []
        for doc in self._docs:
            if self._matches(doc, filter_dict or {}):
                result = doc.copy()
                if projection:
                    for key, val in projection.items():
                        if val == 0 and key in result:
                            del result[key]
                results.append(result)
        return results

    def update_one(self, filter_dict, update, upsert=False):
        for doc in self._docs:
            if self._matches(doc, filter_dict):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$setOnInsert" in update:
                    pass  # Only applies on insert
                result = MagicMock()
                result.modified_count = 1
                result.upserted_id = None
                return result

        if upsert:
            new_doc = {**filter_dict}
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            self._id_counter += 1
            new_doc["_id"] = self._id_counter
            self._docs.append(new_doc)
            result = MagicMock()
            result.modified_count = 0
            result.upserted_id = self._id_counter
            return result

        result = MagicMock()
        result.modified_count = 0
        result.upserted_id = None
        return result

    def delete_many(self, filter_dict=None):
        before = len(self._docs)
        self._docs = [d for d in self._docs if not self._matches(d, filter_dict or {})]
        result = MagicMock()
        result.deleted_count = before - len(self._docs)
        return result

    def count_documents(self, filter_dict=None):
        return sum(1 for d in self._docs if self._matches(d, filter_dict or {}))

    def create_index(self, *args, **kwargs):
        pass  # no-op for tests

    @staticmethod
    def _matches(doc, filter_dict):
        for key, val in filter_dict.items():
            if doc.get(key) != val:
                return False
        return True


class MockDatabase:
    """Simple in-memory MongoDB database mock."""

    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MockCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self[name]

    def list_collection_names(self):
        return list(self._collections.keys())


@pytest.fixture
def mock_db():
    """Provide a fresh in-memory mock database."""
    return MockDatabase()


@pytest.fixture
def seeded_db(mock_db):
    """Provide a mock database pre-seeded with a patient and a doctor."""
    mock_db["users"].insert_one(make_user())
    mock_db["doctors"].insert_one(make_doctor())
    mock_db["hospitals"].insert_one({"name": "Test Hospital", "code": "HOSP"})
    return mock_db


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _create_test_token(username, is_doctor=False, expired=False):
    """Create a JWT token for testing."""
    secret = "test-secret-key-for-testing-only"
    if expired:
        exp = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    else:
        exp = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    payload = {"sub": username, "admin": is_doctor, "exp": exp}
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.fixture
def patient_token():
    """Valid JWT token for a patient user."""
    return _create_test_token("testpatient", is_doctor=False)


@pytest.fixture
def doctor_token():
    """Valid JWT token for a doctor user."""
    return _create_test_token("testdoctor", is_doctor=True)


@pytest.fixture
def expired_token():
    """Expired JWT token."""
    return _create_test_token("testpatient", expired=True)


@pytest.fixture
def patient_headers(patient_token):
    """Auth headers for a patient."""
    return {"Authorization": f"Bearer {patient_token}"}


@pytest.fixture
def doctor_headers(doctor_token):
    """Auth headers for a doctor."""
    return {"Authorization": f"Bearer {doctor_token}"}


# ---------------------------------------------------------------------------
# FastAPI test client with mocked DB
# ---------------------------------------------------------------------------
@pytest.fixture
def app_with_db(seeded_db):
    """FastAPI app with database dependency overridden to use mock DB."""
    from app.api import app
    from app.login import get_db

    app.dependency_overrides[get_db] = lambda: seeded_db
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(app_with_db):
    """Async HTTP test client."""
    from httpx import AsyncClient, ASGITransport

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
