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


# ===================================================================
# De-identification helpers (Package E)
# ===================================================================

import logging
from datetime import date

from app.florence_utils import (
    CONNECTION_ERROR_MESSAGE,
    OPENING_TURN,
    REFUSED_MESSAGES,
    build_known_identifiers,
    build_scrub_context,
    dob_day_month_forms,
    handle_ai_response_error,
    model_messages,
    pending_review_fields,
    scrub_summary_from_messages,
)
from app.inference.scrub import ScrubError
from tests.conftest import MockDatabase
from tests.factories import make_doctor, make_user, patient_ref_for


class TestRefusedFallback:

    def test_refused_in_english(self):
        text = generate_fallback_response("Test Patient", "refused", "en")
        assert text == REFUSED_MESSAGES["en"]
        assert "care team" in text and "Test Patient" not in text
        assert "AI connection difficulty" not in text

    def test_refused_in_cantonese(self):
        text = generate_fallback_response("Test Patient", "refused", "zh-HK")
        assert text == REFUSED_MESSAGES["zh-HK"]
        assert "醫療團隊" in text

    def test_refused_default_is_bilingual(self):
        text = generate_fallback_response("Test Patient", "refused")
        assert REFUSED_MESSAGES["en"] in text and REFUSED_MESSAGES["zh-HK"] in text
        assert generate_fallback_response("x", "refused", "fr") == text

    def test_other_contexts_keep_the_connection_message(self):
        for context in ("welcome", "processing_error", "general_followup", "system_error", "anything"):
            assert generate_fallback_response("x", context, "zh-HK") == CONNECTION_ERROR_MESSAGE


class TestErrorHandlingLogsTypeOnly:

    def test_handle_ai_response_error_never_logs_or_returns_the_message(self, caplog):
        secret = "patient Test Patient HKID A123456(7)"
        with caplog.at_level(logging.DEBUG, logger="ovis.florence"):
            out = handle_ai_response_error(ValueError(secret), "process_message")
        assert out["error"] == "ValueError"
        assert out["response"] == CONNECTION_ERROR_MESSAGE
        assert "ValueError" in caplog.text and secret not in caplog.text and "A123456" not in caplog.text

    def test_system_prompt_loads_without_printing(self, capsys):
        prompt = load_florence_system_prompt("en")
        assert "[PERSON_1]" in prompt
        assert capsys.readouterr().out == ""

    def test_missing_prompt_file_falls_back_with_a_log_line(self, monkeypatch, caplog):
        monkeypatch.setattr("app.florence_utils.os.path.join", lambda *a: "/nonexistent/prompt.txt")
        with caplog.at_level(logging.WARNING, logger="ovis.florence"):
            prompt = load_florence_system_prompt("en")
        assert "Florence" in prompt and "[PERSON_1]" in prompt
        assert "not found" in caplog.text


class TestAssessmentRecordIdentity:

    def test_record_has_patient_ref_and_no_display_name(self):
        session = make_florence_session()
        record = create_assessment_record(session, None, None)
        assert "user_info" not in record
        assert record["patient_ref"] == patient_ref_for("testpatient")
        assert record["user_id"] == "testpatient"

    def test_patient_ref_is_derived_when_the_session_lacks_it(self):
        session = make_florence_session()
        del session["patient_ref"]
        record = create_assessment_record(session, None, None)
        assert record["patient_ref"] == patient_ref_for("testpatient")

    def test_pending_review_fields(self):
        fields = pending_review_fields("dpa_not_confirmed")
        assert fields["triage_status"] == "pending_clinician_review"
        assert fields["alert_level"] == "PENDING_REVIEW"
        assert fields["refusal_reason"] == "dpa_not_confirmed"
        assert fields["structured_assessment"] is None and fields["triage_assessment"] is None
        assert fields["flag_for_oncologist"] is False and fields["oncologist_notification_level"] == "none"
        assert "analysed_at" in fields


class TestKnownIdentifiersFromUser:

    def _db(self, user=None, doctor=None):
        db = MockDatabase()
        db["users"].insert_one(user or make_user())
        db["doctors"].insert_one(doctor or make_doctor())
        return db

    def test_patient_profile_and_doctor_lookup(self):
        db = self._db()
        known = build_known_identifiers(db, make_user())
        assert known.full_name == "Test Patient" and known.username == "testpatient"
        assert known.email == "patient@test.com"
        assert known.doctor_name == "Dr. Test" and known.hospital == "Test Hospital"
        assert known.display_name == "Test Patient"

    def test_dob_from_legacy_birthdate_field(self):
        known = build_known_identifiers(self._db(), make_user())  # birthdate "01/01/1990", no dob
        assert known.dob == date(1990, 1, 1)
        assert "01/01" in known.extra and "1/1" in known.extra

    def test_dob_field_wins_and_iso_is_parsed(self):
        user = make_user({"dob": "1966-08-22"})
        known = build_known_identifiers(self._db(user), user)
        assert known.dob == date(1966, 8, 22)
        assert dob_day_month_forms(known.dob) == ["08/22", "22/08", "8/22", "22/8", "22.08", "08.22"]

    def test_unparseable_dob_kept_verbatim_and_no_day_month_forms(self):
        user = make_user({"birthdate": "sometime in 1990"})
        known = build_known_identifiers(self._db(user), user)
        assert known.dob is None and "sometime in 1990" in known.extra
        assert dob_day_month_forms(None) == []

    def test_user_without_doctor_or_full_name(self):
        user = make_user({"doctor": None})
        del user["full_name"]
        known = build_known_identifiers(self._db(user), user)
        assert known.doctor_name is None and known.hospital is None
        assert known.display_name == "testpatient"

    def test_doctor_lookup_failure_is_tolerated(self, caplog):
        class BrokenDB:
            def __getitem__(self, name):
                raise ConnectionError("down")

        with caplog.at_level(logging.WARNING, logger="ovis.florence"):
            known = build_known_identifiers(BrokenDB(), make_user())
        assert known.doctor_name is None and "ConnectionError" in caplog.text and "down" not in caplog.text

    def test_scrub_context_reserves_person_1_and_reloads_the_token_map(self):
        db = self._db()
        ctx = build_scrub_context(db, make_user())
        assert ctx.token_map.get("[PERSON_1]") == "Test Patient"
        ctx.scrub("my friend Mary")
        reloaded = build_scrub_context(db, make_user(), ctx.token_map.to_dict())
        assert reloaded.token_map.get("[PERSON_2]") == "Mary"
        assert reloaded.scrub("Mary again").text == "[PERSON_2] again"

    def test_misconfigured_ner_backend_raises_scrub_error(self, monkeypatch):
        monkeypatch.setenv("SCRUB_NER_BACKEND", "nope")
        with pytest.raises(ScrubError):
            build_scrub_context(self._db(), make_user())

    def test_malformed_token_map_is_a_scrub_error_without_input_text(self):
        with pytest.raises(ScrubError) as info:
            build_scrub_context(self._db(), make_user(), {"entries": [["[PERSON_1]"]], "version": "A123456(7)"})
        assert "A123456" not in str(info.value) and info.value.layer == "context"


class TestScrubSummary:

    def test_counts_tokens_per_class_and_linkage(self):
        msgs = [
            {"role": "user", "content": "[PERSON_1] saw [PERSON_2] at [FACILITY_1] on [DATE_1 · 3 days ago]"},
            {"role": "assistant", "content": "【PERSON_1】 is [AGE · 70s]; id [ID_1]; [DOB]"},
        ]
        summary = scrub_summary_from_messages(msgs, "none")
        assert summary["counts"] == {"PERSON": 3, "FACILITY": 1, "DATE": 1, "AGE": 1, "ID": 1, "DOB": 1}
        assert summary["linkage_score"] == 3  # FACILITY, DATE, AGE
        assert summary["ner_backend"] == "none"

    def test_empty_and_non_string_content(self):
        assert scrub_summary_from_messages([]) == {"counts": {}, "linkage_score": 0, "ner_backend": "none"}
        assert scrub_summary_from_messages([{"role": "user", "content": None}])["counts"] == {}

    def test_model_messages_drops_timestamps_and_system_turns(self):
        history = [
            {"role": "system", "content": "x", "timestamp": "t"},
            {"role": "assistant", "content": "hi", "timestamp": "t"},
            {"role": "user", "content": "hello", "timestamp": "t"},
        ]
        assert model_messages(history) == [{"role": "assistant", "content": "hi"}, {"role": "user", "content": "hello"}]

    def test_opening_turn_is_deidentified(self):
        assert OPENING_TURN == "Hello, I'm [PERSON_1]. I'm here for my health check-in."


class TestPromptFiles:
    """The six prompt files carry the de-identification guidance and no username placeholder."""

    @pytest.mark.parametrize("filename", [
        "prompt_eng.txt", "prompt_canto.txt", "assessment_prompt_eng.txt", "assessment_prompt_canto.txt",
        "triage_prompt_eng.txt", "triage_prompt_canto.txt",
    ])
    def test_placeholders_are_explained_and_patient_id_is_gone(self, filename):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "app", filename)
        text = open(path, encoding="utf-8").read()
        assert "{patient_id}" not in text
        assert "[PERSON_1]" in text
        assert "never translate, reformat or drop the square brackets" in text or "切勿翻譯、改寫格式或刪去方括號" in text
        if not filename.startswith("prompt_"):
            text.format(treatment_status="x")  # the only remaining format field

    def test_florence_prompts_do_not_solicit_identifiers(self):
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "app")
        en = open(os.path.join(base, "prompt_eng.txt"), encoding="utf-8").read()
        zh = open(os.path.join(base, "prompt_canto.txt"), encoding="utf-8").read()
        assert "who visits you the most" not in en and "誰最常來看望你" not in zh
        assert "Never ask for real names" in en and "切勿詢問真實姓名" in zh
