"""Opaque references: deterministic keyed HMACs that never expose the username or session id."""

import hashlib
import hmac
import re

import pytest

import app.login as login_module
from app.inference.refs import PATIENT_REF_LEN, SESSION_REF_LEN, patient_ref, session_ref

TEST_KEY = "test-secret-key-for-testing-only"  # what conftest patches onto app.login.SECRET_KEY


class TestPatientRef:

    def test_is_hmac_sha256_of_the_username_truncated_to_16_hex(self):
        expected = hmac.new(TEST_KEY.encode(), b"testpatient", hashlib.sha256).hexdigest()[:16]
        assert patient_ref("testpatient") == expected
        assert len(patient_ref("testpatient")) == PATIENT_REF_LEN == 16
        assert re.fullmatch(r"[0-9a-f]{16}", patient_ref("testpatient"))

    def test_deterministic_and_distinct_per_user(self):
        assert patient_ref("alex") == patient_ref("alex")
        assert patient_ref("alex") != patient_ref("jordan")

    def test_does_not_contain_the_username(self):
        ref = patient_ref("testpatient")
        assert "testpatient" not in ref and "test" not in ref

    def test_reads_secret_key_at_call_time(self, monkeypatch):
        before = patient_ref("testpatient")
        monkeypatch.setattr(login_module, "SECRET_KEY", "a-rotated-key")
        assert patient_ref("testpatient") != before

    def test_missing_secret_key_fails_closed(self, monkeypatch):
        monkeypatch.setattr(login_module, "SECRET_KEY", None)
        with pytest.raises(RuntimeError):
            patient_ref("testpatient")
        monkeypatch.setattr(login_module, "SECRET_KEY", "")
        with pytest.raises(RuntimeError):
            session_ref("x")

    def test_none_is_rejected(self):
        with pytest.raises(ValueError):
            patient_ref(None)


class TestSessionRef:

    def test_is_hmac_of_the_session_id_truncated_to_12_hex(self):
        sid = "testpatient_1710000000_abc123"
        expected = hmac.new(TEST_KEY.encode(), sid.encode(), hashlib.sha256).hexdigest()[:12]
        assert session_ref(sid) == expected
        assert len(session_ref(sid)) == SESSION_REF_LEN == 12

    def test_session_ref_hides_the_username_embedded_in_the_session_id(self):
        ref = session_ref("testpatient_1710000000_abc123")
        assert "testpatient" not in ref and "1710000000" not in ref

    def test_patient_and_session_refs_differ_for_the_same_input(self):
        # same HMAC, different truncation; a session id is never a username anyway
        assert patient_ref("x")[:12] == session_ref("x")
        assert session_ref("questionnaire_1") != session_ref("questionnaire_2")
