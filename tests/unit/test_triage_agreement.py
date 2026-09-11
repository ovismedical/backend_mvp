"""scripts/triage_agreement.py: clinician agreement with Florence, computed from reviews joined to assessments."""

import json

import pytest

from scripts.triage_agreement import ALERT_LEVELS, agreement_report, format_text, load_from_db
from tests.factories import make_assessment_record, make_pending_assessment_record, make_review


def _triaged(session_id, level):
    return make_assessment_record({"session_id": session_id, "alert_level": level,
                                   "triage_assessment": {"alert_level": level}})


def _review(session_id, florence, clinician=None):
    """A clinician review: agrees when no clinician level is given, otherwise overrides to it."""
    return make_review({
        "session_id": session_id,
        "florence_alert_level": florence,
        "agrees": clinician is None,
        "alert_level_override": clinician,
    })


class TestAgreementReport:

    def test_empty_inputs_give_an_empty_but_complete_report(self):
        report = agreement_report([], [])
        assert report["n"] == 0 and report["agreement_rate"] is None
        assert report["over_escalation_rate"] is None and report["under_escalation_rate"] is None
        assert set(report["confusion_matrix"]) == set(ALERT_LEVELS)
        assert all(set(row) == set(ALERT_LEVELS) for row in report["confusion_matrix"].values())
        assert report["clinician_assigned_n"] == 0 and report["orphan_review_n"] == 0

    def test_agreement_rate_overall_and_per_florence_level(self):
        assessments = [_triaged("s1", "GREEN"), _triaged("s2", "GREEN"), _triaged("s3", "RED"), _triaged("s4", "YELLOW")]
        reviews = [
            _review("s1", "GREEN"),             # agrees
            _review("s2", "GREEN", "YELLOW"),   # clinician went higher: Florence under-escalated
            _review("s3", "RED", "ORANGE"),     # clinician went lower: Florence over-escalated
            _review("s4", "YELLOW"),            # agrees
        ]
        report = agreement_report(assessments, reviews)
        assert report["n"] == 4 and report["agreed_n"] == 2 and report["agreement_rate"] == 0.5
        by_level = report["agreement_by_florence_level"]
        assert by_level["GREEN"] == {"n": 2, "agreed_n": 1, "rate": 0.5}
        assert by_level["YELLOW"] == {"n": 1, "agreed_n": 1, "rate": 1.0}
        assert by_level["ORANGE"] == {"n": 0, "agreed_n": 0, "rate": None}
        assert by_level["RED"] == {"n": 1, "agreed_n": 0, "rate": 0.0}

    def test_confusion_matrix_and_escalation_directions(self):
        assessments = [_triaged(f"s{i}", lvl) for i, lvl in enumerate(["GREEN", "RED", "RED", "ORANGE", "YELLOW"])]
        reviews = [
            _review("s0", "GREEN", "RED"),      # under-escalation (Florence GREEN < clinician RED)
            _review("s1", "RED", "GREEN"),      # over-escalation
            _review("s2", "RED", "YELLOW"),     # over-escalation
            _review("s3", "ORANGE"),            # agree
            _review("s4", "YELLOW"),            # agree
        ]
        report = agreement_report(assessments, reviews)
        matrix = report["confusion_matrix"]
        assert matrix["GREEN"]["RED"] == 1
        assert matrix["RED"]["GREEN"] == 1 and matrix["RED"]["YELLOW"] == 1
        assert matrix["ORANGE"]["ORANGE"] == 1 and matrix["YELLOW"]["YELLOW"] == 1
        assert sum(sum(row.values()) for row in matrix.values()) == report["n"] == 5
        assert report["over_escalation_n"] == 2 and report["over_escalation_rate"] == 0.4
        assert report["under_escalation_n"] == 1 and report["under_escalation_rate"] == 0.2
        assert report["agreement_rate"] == 0.4

    def test_pending_record_reviews_are_reported_separately(self):
        assessments = [_triaged("s1", "GREEN"), make_pending_assessment_record({"session_id": "p1"}),
                       make_pending_assessment_record({"session_id": "p2"})]
        reviews = [
            _review("s1", "GREEN"),
            make_review({"session_id": "p1", "agrees": None, "florence_alert_level": None, "alert_level_override": "ORANGE"}),
            make_review({"session_id": "p2", "agrees": None, "florence_alert_level": None, "alert_level_override": "GREEN"}),
        ]
        report = agreement_report(assessments, reviews)
        assert report["n"] == 1 and report["agreement_rate"] == 1.0
        assert report["clinician_assigned_n"] == 2
        assert report["clinician_assigned_by_level"] == {"GREEN": 1, "YELLOW": 0, "ORANGE": 1, "RED": 0}
        assert sum(sum(row.values()) for row in report["confusion_matrix"].values()) == 1
        assert "PENDING_REVIEW" not in report["confusion_matrix"]

    def test_legacy_pending_snapshot_value_is_excluded_from_the_matrix(self):
        # a review that snapshotted the placeholder level instead of None must not become a matrix row
        assessments = [make_pending_assessment_record({"session_id": "p1"})]
        reviews = [make_review({"session_id": "p1", "florence_alert_level": "PENDING_REVIEW", "alert_level_override": "RED"})]
        report = agreement_report(assessments, reviews)
        assert report["n"] == 0 and report["clinician_assigned_n"] == 1

    def test_reviews_without_an_assessment_are_counted_as_orphans(self):
        report = agreement_report([_triaged("s1", "GREEN")], [_review("s1", "GREEN"), _review("gone", "GREEN")])
        assert report["n"] == 1 and report["orphan_review_n"] == 1

    def test_florence_level_falls_back_to_the_assessment_when_the_snapshot_is_missing(self):
        assessments = [_triaged("s1", "ORANGE"), make_pending_assessment_record({"session_id": "p1"})]
        old_review = make_review({"session_id": "s1", "agrees": False, "alert_level_override": "YELLOW"})
        del old_review["florence_alert_level"]
        pending_review = make_review({"session_id": "p1", "agrees": None, "alert_level_override": "GREEN"})
        del pending_review["florence_alert_level"]
        report = agreement_report(assessments, [old_review, pending_review])
        assert report["n"] == 1 and report["confusion_matrix"]["ORANGE"]["YELLOW"] == 1
        assert report["over_escalation_n"] == 1
        assert report["clinician_assigned_n"] == 1

    def test_disagreement_with_an_equal_level_counts_as_agreement_by_level(self):
        report = agreement_report([_triaged("s1", "GREEN")], [_review("s1", "GREEN", "GREEN")])
        assert report["agreed_n"] == 1 and report["agreement_rate"] == 1.0

    def test_report_is_json_serialisable_and_carries_no_identifiers(self):
        assessments = [_triaged("testpatient_1710000000", "GREEN")]
        reviews = [_review("testpatient_1710000000", "GREEN", "YELLOW")]
        report = agreement_report(assessments, reviews)
        text = json.dumps(report) + format_text(report)
        for needle in ("testpatient", "testdoctor", "1710000000", "Test Patient"):
            assert needle not in text


class TestLoadFromDb:

    def test_joins_reviews_to_their_assessments(self, mock_db):
        mock_db["florence_assessments"].insert_one(_triaged("s1", "RED"))
        mock_db["florence_assessments"].insert_one(_triaged("s2", "GREEN"))  # never reviewed
        mock_db["assessment_reviews"].insert_one(_review("s1", "RED", "ORANGE"))
        mock_db["assessment_reviews"].insert_one(_review("missing", "GREEN"))
        report = load_from_db(mock_db)
        assert report["n"] == 1 and report["over_escalation_n"] == 1 and report["orphan_review_n"] == 1

    def test_no_reviews_yet(self, mock_db):
        report = load_from_db(mock_db)
        assert report["n"] == 0 and report["agreement_rate"] is None


class TestFormatText:

    def test_renders_rates_and_matrix(self):
        report = agreement_report([_triaged("s1", "RED"), _triaged("s2", "GREEN")],
                                  [_review("s1", "RED", "YELLOW"), _review("s2", "GREEN")])
        text = format_text(report)
        assert "Reviews of triaged records: 2" in text
        assert "Agreement: 50.0% (1/2)" in text
        assert "Florence over-escalated: 50.0% (1)" in text
        assert "Florence under-escalated: 0.0% (0)" in text
        assert "n/a" in text  # levels with no reviews
        assert "Confusion matrix" in text
