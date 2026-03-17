"""
Tests for app.questionnaire_enrichment — severity scoring, alert flags, and enrichment pipeline.
"""

import pytest

from app.questionnaire_enrichment import (
    evaluate_conditional,
    _resolve_display_value,
    _compute_severity_normalized,
    _check_alert_flags,
    enrich_submission,
)
from app.questionnaire_definitions import (
    FREQUENCY_SEVERITY_MAP,
    region_count_to_severity,
    SECTIONS,
)


# ===================================================================
# evaluate_conditional
# ===================================================================

class TestEvaluateConditional:

    def test_no_conditional_always_shown(self):
        q = {"id": "q1", "type": "rating", "text": "Rate it"}
        assert evaluate_conditional(q, {}) is True

    def test_gte_met(self):
        q = {
            "id": "q2",
            "conditional": {"depends_on": "appetite_rating", "operator": "gte", "value": 4},
        }
        assert evaluate_conditional(q, {"appetite_rating": 4}) is True
        assert evaluate_conditional(q, {"appetite_rating": 5}) is True

    def test_gte_not_met(self):
        q = {
            "id": "q2",
            "conditional": {"depends_on": "appetite_rating", "operator": "gte", "value": 4},
        }
        assert evaluate_conditional(q, {"appetite_rating": 3}) is False

    def test_not_eq(self):
        q = {
            "id": "q3",
            "conditional": {"depends_on": "has_cough", "operator": "not_eq", "value": "none"},
        }
        assert evaluate_conditional(q, {"has_cough": "yes"}) is True
        assert evaluate_conditional(q, {"has_cough": "none"}) is False

    def test_eq(self):
        q = {
            "id": "q4",
            "conditional": {"depends_on": "status", "operator": "eq", "value": "active"},
        }
        assert evaluate_conditional(q, {"status": "active"}) is True
        assert evaluate_conditional(q, {"status": "inactive"}) is False

    def test_in_list(self):
        q = {
            "id": "q5",
            "conditional": {"depends_on": "color", "operator": "in_list", "value": ["red", "blue"]},
        }
        assert evaluate_conditional(q, {"color": "red"}) is True
        assert evaluate_conditional(q, {"color": "green"}) is False

    def test_missing_dependency_returns_false(self):
        q = {
            "id": "q6",
            "conditional": {"depends_on": "missing_field", "operator": "gte", "value": 1},
        }
        assert evaluate_conditional(q, {}) is False


# ===================================================================
# _compute_severity_normalized
# ===================================================================

class TestComputeSeverityNormalized:

    def test_rating_ascending_min(self):
        q = {"type": "rating", "min": 1, "max": 5, "severity_direction": "ascending"}
        assert _compute_severity_normalized(q, 1) == 0.0

    def test_rating_ascending_max(self):
        q = {"type": "rating", "min": 1, "max": 5, "severity_direction": "ascending"}
        assert _compute_severity_normalized(q, 5) == 1.0

    def test_rating_ascending_mid(self):
        q = {"type": "rating", "min": 1, "max": 5, "severity_direction": "ascending"}
        assert _compute_severity_normalized(q, 3) == 0.5

    def test_rating_descending(self):
        q = {"type": "rating", "min": 1, "max": 5, "severity_direction": "descending"}
        # Descending: higher raw → lower severity
        assert _compute_severity_normalized(q, 5) == 0.0
        assert _compute_severity_normalized(q, 1) == 1.0

    def test_slider(self):
        q = {"type": "slider", "slider_config": {"min": 0, "max": 10}}
        assert _compute_severity_normalized(q, 0) == 0.0
        assert _compute_severity_normalized(q, 10) == 1.0
        assert _compute_severity_normalized(q, 5) == 0.5

    def test_none_value(self):
        q = {"type": "rating", "min": 1, "max": 5}
        assert _compute_severity_normalized(q, None) is None

    def test_non_rating_type(self):
        q = {"type": "single-select"}
        assert _compute_severity_normalized(q, "some_value") is None

    def test_zero_range(self):
        q = {"type": "rating", "min": 3, "max": 3}
        assert _compute_severity_normalized(q, 3) == 0.0


# ===================================================================
# FREQUENCY_SEVERITY_MAP and region_count_to_severity
# ===================================================================

class TestDefinitionHelpers:

    def test_frequency_severity_map(self):
        assert FREQUENCY_SEVERITY_MAP["none"] == 0
        assert FREQUENCY_SEVERITY_MAP["1-2"] == 1
        assert FREQUENCY_SEVERITY_MAP["3-4"] == 2
        assert FREQUENCY_SEVERITY_MAP["5-6"] == 3
        assert FREQUENCY_SEVERITY_MAP["6+"] == 4

    def test_region_count_0(self):
        assert region_count_to_severity(0) == 0

    def test_region_count_1(self):
        assert region_count_to_severity(1) == 1

    def test_region_count_3(self):
        assert region_count_to_severity(3) == 2

    def test_region_count_6(self):
        assert region_count_to_severity(6) == 3

    def test_region_count_10_plus(self):
        assert region_count_to_severity(10) == 4
        assert region_count_to_severity(15) == 4


# ===================================================================
# _check_alert_flags
# ===================================================================

class TestCheckAlertFlags:

    def _section_with_questions(self, questions):
        return {"title": "Test Section", "clinical_area": "test", "questions": questions}

    def test_rating_ge_4_triggers_flag(self):
        section = self._section_with_questions([
            {"id": "q1", "type": "rating", "text": "Rate pain?", "options": {4: "Severe", 5: "Very severe"}},
        ])
        flags = _check_alert_flags(section, {"q1": 4})
        assert len(flags) == 1
        assert "rated" in flags[0].lower()

    def test_rating_below_4_no_flag(self):
        section = self._section_with_questions([
            {"id": "q1", "type": "rating", "text": "Rate pain?", "options": {3: "Moderate"}},
        ])
        flags = _check_alert_flags(section, {"q1": 3})
        assert len(flags) == 0

    def test_frequency_6plus_triggers_flag(self):
        section = self._section_with_questions([
            {"id": "q1", "type": "single-select", "text": "Frequency?"},
        ])
        flags = _check_alert_flags(section, {"q1": "6+"})
        assert len(flags) == 1
        assert "6+" in flags[0]

    def test_body_diagram_3_regions_triggers_flag(self):
        section = self._section_with_questions([
            {"id": "q1", "type": "body-diagram", "text": "Where?", "regions": {}},
        ])
        flags = _check_alert_flags(section, {"q1": ["head", "chest", "back"]})
        assert len(flags) == 1
        assert "3 body regions" in flags[0]

    def test_body_diagram_2_regions_no_flag(self):
        section = self._section_with_questions([
            {"id": "q1", "type": "body-diagram", "text": "Where?", "regions": {}},
        ])
        flags = _check_alert_flags(section, {"q1": ["head", "chest"]})
        assert len(flags) == 0

    def test_slider_alert_rule(self):
        section = self._section_with_questions([
            {
                "id": "sleep",
                "type": "slider",
                "text": "Hours slept?",
                "slider_config": {"unit": "hrs"},
                "alert_rule": {"operator": "lte", "value": 3},
            },
        ])
        flags = _check_alert_flags(section, {"sleep": 2})
        assert len(flags) == 1
        assert "critically low" in flags[0]

    def test_color_chart_alert_values(self):
        section = self._section_with_questions([
            {
                "id": "stool_color",
                "type": "color-chart",
                "text": "Stool color?",
                "options": {"red": "Red", "brown": "Brown"},
                "alert_values": ["red"],
            },
        ])
        flags = _check_alert_flags(section, {"stool_color": ["red"]})
        assert len(flags) == 1
        assert "alarming color" in flags[0]

    def test_no_answer_no_flag(self):
        section = self._section_with_questions([
            {"id": "q1", "type": "rating", "text": "Rate?", "options": {}},
        ])
        flags = _check_alert_flags(section, {})
        assert len(flags) == 0


# ===================================================================
# enrich_submission (integration of all enrichment logic)
# ===================================================================

class TestEnrichSubmission:

    def test_empty_answers(self):
        result = enrich_submission({}, 0, 13, 0)
        assert result["schema_version"] == 2
        assert result["completion"]["questions_answered"] == 0
        assert result["clinical_summary"]["symptom_count"] == 0

    def test_basic_enrichment(self):
        answers = {"appetite_rating": 4}
        result = enrich_submission(answers, 1, 13, 8)
        assert result["completion"]["sections_completed"] == 1
        assert result["completion"]["total_sections"] == 13
        # appetite_rating=4 should trigger an alert flag (rating >= 4)
        assert len(result["clinical_summary"]["alert_flags"]) > 0

    def test_sections_structure(self):
        result = enrich_submission({}, 0, 13, 0)
        assert len(result["sections"]) == len(SECTIONS)
        for section in result["sections"]:
            assert "section_id" in section
            assert "title" in section
            assert "responses" in section

    def test_raw_answers_preserved(self):
        answers = {"appetite_rating": 3, "mood_rating": 2}
        result = enrich_submission(answers, 1, 13, 8)
        assert result["raw_answers"] == answers
