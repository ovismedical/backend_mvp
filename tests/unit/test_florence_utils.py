"""
Tests for app.florence_utils — clinical safety logic, message formatting, and utilities.
"""

import os
import pytest
from unittest.mock import patch

from app.florence_utils import (
    should_flag_symptoms,
    format_conversation_history_for_ai,
    create_conversation_message,
    create_timestamp,
    generate_fallback_response,
    get_localized_message,
    validate_session_access,
    is_ai_available,
    create_assessment_record,
    load_florence_system_prompt,
)
from tests.factories import make_symptoms, make_florence_session, make_triage_result


# ===================================================================
# should_flag_symptoms — CRITICAL clinical safety logic
# ===================================================================

class TestShouldFlagSymptoms:
    """Tests for oncologist flagging thresholds."""

    # --- Undergoing treatment ---

    def test_treatment_no_flag_below_thresholds(self):
        symptoms = make_symptoms()  # all freq=1, sev=1
        flag, level, reason = should_flag_symptoms(symptoms, "undergoing_treatment")
        assert flag is False
        assert level == "none"

    def test_treatment_flags_on_high_frequency(self):
        symptoms = make_symptoms({"fatigue": {"frequency_rating": 5}})
        flag, level, reason = should_flag_symptoms(symptoms, "undergoing_treatment")
        assert flag is True
        assert level == "amber"
        assert "fatigue" in reason

    def test_treatment_flags_on_severity_3(self):
        symptoms = make_symptoms({"cough": {"severity_rating": 3}})
        flag, level, reason = should_flag_symptoms(symptoms, "undergoing_treatment")
        assert flag is True
        assert level == "amber"
        assert "cough" in reason

    def test_treatment_no_flag_at_severity_2(self):
        symptoms = make_symptoms({"cough": {"severity_rating": 2, "frequency_rating": 2}})
        flag, level, reason = should_flag_symptoms(symptoms, "undergoing_treatment")
        assert flag is False

    def test_treatment_flags_on_freq3_sev3_combo(self):
        symptoms = make_symptoms({"nausea": {"frequency_rating": 3, "severity_rating": 3}})
        flag, level, reason = should_flag_symptoms(symptoms, "undergoing_treatment")
        assert flag is True
        assert level == "amber"

    def test_treatment_boundary_freq4_sev2(self):
        symptoms = make_symptoms({"fatigue": {"frequency_rating": 4, "severity_rating": 2}})
        flag, level, reason = should_flag_symptoms(symptoms, "undergoing_treatment")
        # freq < 5 and sev < 3 and not (freq >= 3 and sev >= 3)
        assert flag is False

    # --- In remission ---

    def test_remission_no_flag_below_thresholds(self):
        symptoms = make_symptoms({"fatigue": {"frequency_rating": 3, "severity_rating": 3}})
        flag, level, reason = should_flag_symptoms(symptoms, "in_remission")
        # In remission: freq >= 4 or sev >= 4 triggers
        assert flag is False

    def test_remission_flags_on_frequency_4(self):
        symptoms = make_symptoms({"cough": {"frequency_rating": 4}})
        flag, level, reason = should_flag_symptoms(symptoms, "in_remission")
        assert flag is True
        assert level == "amber"
        assert "remission" in reason.lower()

    def test_remission_flags_on_severity_4(self):
        symptoms = make_symptoms({"pain": {"severity_rating": 4}})
        flag, level, reason = should_flag_symptoms(symptoms, "in_remission")
        assert flag is True
        assert level == "amber"

    def test_remission_no_flag_at_severity_3(self):
        symptoms = make_symptoms({"pain": {"severity_rating": 3, "frequency_rating": 2}})
        flag, level, reason = should_flag_symptoms(symptoms, "in_remission")
        assert flag is False

    # --- Edge cases ---

    def test_empty_symptoms(self):
        flag, level, reason = should_flag_symptoms({}, "undergoing_treatment")
        assert flag is False
        assert level == "none"

    def test_unknown_treatment_status(self):
        symptoms = make_symptoms({"fatigue": {"frequency_rating": 5, "severity_rating": 5}})
        flag, level, reason = should_flag_symptoms(symptoms, "unknown_status")
        # Neither branch matches, falls through to default
        assert flag is False

    def test_missing_rating_keys_default_to_1(self):
        symptoms = {"fatigue": {}}  # no frequency_rating or severity_rating
        flag, level, reason = should_flag_symptoms(symptoms, "undergoing_treatment")
        assert flag is False


# ===================================================================
# format_conversation_history_for_ai
# ===================================================================

class TestFormatConversationHistory:

    def test_basic_formatting(self):
        history = [
            {"role": "assistant", "content": "Hello!", "timestamp": "2026-01-01T00:00:00"},
            {"role": "user", "content": "Hi", "timestamp": "2026-01-01T00:01:00"},
        ]
        result = format_conversation_history_for_ai(history, include_system_prompt=False)
        assert len(result) == 2
        assert "timestamp" not in result[0]
        assert result[0]["role"] == "assistant"

    def test_includes_system_prompt_when_requested(self):
        history = [{"role": "user", "content": "Hi"}]
        result = format_conversation_history_for_ai(
            history, include_system_prompt=True, system_prompt="You are Florence."
        )
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are Florence."
        assert len(result) == 2

    def test_skips_existing_system_messages(self):
        history = [
            {"role": "system", "content": "Old prompt"},
            {"role": "user", "content": "Hi"},
        ]
        result = format_conversation_history_for_ai(
            history, include_system_prompt=True, system_prompt="New prompt"
        )
        # Should have new system prompt + user message, old system skipped
        assert len(result) == 2
        assert result[0]["content"] == "New prompt"

    def test_no_system_prompt(self):
        history = [{"role": "user", "content": "Hi"}]
        result = format_conversation_history_for_ai(history, include_system_prompt=False)
        assert len(result) == 1


# ===================================================================
# Utility functions
# ===================================================================

class TestUtilities:

    def test_create_timestamp_returns_iso(self):
        ts = create_timestamp()
        assert "T" in ts
        assert "+" in ts or "Z" in ts

    def test_create_conversation_message_with_timestamp(self):
        msg = create_conversation_message("user", "Hello")
        assert msg["role"] == "user"
        assert msg["content"] == "Hello"
        assert "timestamp" in msg

    def test_create_conversation_message_without_timestamp(self):
        msg = create_conversation_message("assistant", "Hi", include_timestamp=False)
        assert "timestamp" not in msg

    def test_generate_fallback_response(self):
        resp = generate_fallback_response("Patient", "welcome")
        assert "AI connection difficulty" in resp

    def test_generate_fallback_unknown_context(self):
        resp = generate_fallback_response("Patient", "nonexistent")
        assert "AI connection difficulty" in resp

    def test_validate_session_access_valid(self):
        session = {"user_id": "alice"}
        assert validate_session_access(session, "alice") is True

    def test_validate_session_access_invalid(self):
        session = {"user_id": "alice"}
        assert validate_session_access(session, "bob") is False

    def test_is_ai_available_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert is_ai_available() is True

    def test_is_ai_available_without_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert is_ai_available() is False


# ===================================================================
# get_localized_message
# ===================================================================

class TestGetLocalizedMessage:

    def test_english_message(self):
        msg = get_localized_message("session_not_found", "en")
        assert msg == "Session not found"

    def test_cantonese_message(self):
        msg = get_localized_message("session_not_found", "zh-HK")
        assert msg == "找不到會話"

    def test_unknown_key_returns_key(self):
        msg = get_localized_message("nonexistent_key", "en")
        assert msg == "nonexistent_key"

    def test_default_language_is_english(self):
        msg = get_localized_message("access_denied")
        assert msg == "Access denied"


# ===================================================================
# create_assessment_record
# ===================================================================

class TestCreateAssessmentRecord:

    def test_basic_record_creation(self):
        session = make_florence_session()
        assessment = {
            "symptoms": make_symptoms(),
            "flag_for_oncologist": False,
            "oncologist_notification_level": "none",
        }
        triage = make_triage_result("GREEN")

        record = create_assessment_record(session, assessment, triage)
        assert record["session_id"] == session["session_id"]
        assert record["user_id"] == session["user_id"]
        assert record["alert_level"] == "GREEN"
        assert record["flag_for_oncologist"] is False

    def test_red_triage_overrides_flag(self):
        session = make_florence_session()
        assessment = {
            "flag_for_oncologist": False,
            "oncologist_notification_level": "none",
        }
        triage = make_triage_result("RED")

        record = create_assessment_record(session, assessment, triage)
        assert record["flag_for_oncologist"] is True
        assert record["oncologist_notification_level"] == "red"

    def test_orange_triage_overrides_flag(self):
        session = make_florence_session()
        assessment = {
            "flag_for_oncologist": False,
            "oncologist_notification_level": "none",
        }
        triage = make_triage_result("ORANGE")

        record = create_assessment_record(session, assessment, triage)
        assert record["flag_for_oncologist"] is True
        assert record["oncologist_notification_level"] == "amber"

    def test_yellow_triage_sets_amber_if_none(self):
        session = make_florence_session()
        assessment = {
            "flag_for_oncologist": False,
            "oncologist_notification_level": "none",
        }
        triage = make_triage_result("YELLOW")

        record = create_assessment_record(session, assessment, triage)
        assert record["oncologist_notification_level"] == "amber"

    def test_no_triage_sets_unknown(self):
        session = make_florence_session()
        record = create_assessment_record(session, None, None)
        assert record["alert_level"] == "UNKNOWN"
