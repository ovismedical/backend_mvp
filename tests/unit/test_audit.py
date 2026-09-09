"""Unit tests for the inference audit trail: event shape, Mongo sink, null sink, indexes, error tolerance."""

import logging
from datetime import datetime, timezone

import pytest

from app.inference.audit import (
    AUDIT_COLLECTION, AuditEvent, MongoAuditSink, NullAuditSink, ensure_audit_indexes, scrub_summary,
)
from tests.conftest import MockDatabase

# The only keys an audit document may ever have. Adding one is a deliberate schema change.
ALLOWED_KEYS = {
    "ts", "kind", "patient_ref", "session_ref", "task_source", "task", "provider", "model", "decision", "reason",
    "scrub", "latency_ms", "outcome", "lang", "msgs",
}


def event(**overrides) -> AuditEvent:
    base = dict(task="chat_turn", decision="allow", reason="ok", outcome="ok", provider="openai", model="gpt-5-mini",
                latency_ms=12, lang="en", msgs=3, patient_ref="a" * 16, session_ref="b" * 12, task_source="florence")
    base.update(overrides)
    return AuditEvent(**base)


class TestAuditEvent:

    def test_to_dict_has_exactly_the_allowed_keys(self):
        doc = event().to_dict()
        assert set(doc) == ALLOWED_KEYS
        assert doc["kind"] == "inference" and doc["task"] == "chat_turn" and doc["decision"] == "allow"
        assert doc["patient_ref"] == "a" * 16 and doc["session_ref"] == "b" * 12 and doc["scrub"] is None

    def test_has_no_field_that_could_hold_content(self):
        names = {f for f in AuditEvent.__dataclass_fields__}
        for forbidden in ("messages", "content", "text", "instructions", "known_identifiers", "username", "name", "email"):
            assert forbidden not in names

    def test_timestamp_is_utc_now_by_default(self):
        before = datetime.now(timezone.utc)
        ts = event().ts
        assert ts.tzinfo is not None and before <= ts <= datetime.now(timezone.utc)

    def test_refusal_event(self):
        doc = event(decision="refuse", reason="dpa_not_confirmed", outcome="refused", model=None).to_dict()
        assert doc["decision"] == "refuse" and doc["reason"] == "dpa_not_confirmed" and doc["model"] is None


class TestScrubSummary:

    def test_none(self):
        assert scrub_summary(None) is None

    def test_dict_keeps_only_the_three_fields(self):
        report = {"counts": {"PERSON": 2}, "linkage_score": 1, "ner_backend": "none", "layers": {"known": 2}, "ms": 4,
                  "text": "should never be here"}
        assert scrub_summary(report) == {"counts": {"PERSON": 2}, "linkage_score": 1, "ner_backend": "none"}

    def test_object_with_attributes(self):
        class Report:
            counts = {"DATE": 1}
            linkage_score = 0
            ner_backend = "presidio"
            text = "original message"
        assert scrub_summary(Report()) == {"counts": {"DATE": 1}, "linkage_score": 0, "ner_backend": "presidio"}

    def test_missing_fields_become_none(self):
        assert scrub_summary({}) == {"counts": None, "linkage_score": None, "ner_backend": None}


class TestMongoAuditSink:

    def test_record_inserts_into_audit_events(self):
        db = MockDatabase()
        sink = MongoAuditSink(db)
        event_id = sink.record(event())
        docs = db[AUDIT_COLLECTION]._docs
        assert len(docs) == 1 and event_id == docs[0]["_id"]
        assert set(docs[0]) == ALLOWED_KEYS | {"_id"}
        assert docs[0]["patient_ref"] == "a" * 16 and docs[0]["kind"] == "inference"

    def test_annotate_merges_fields(self):
        db = MockDatabase()
        sink = MongoAuditSink(db)
        event_id = sink.record(event())
        sink.annotate(event_id, unresolved_tokens=2)
        assert db[AUDIT_COLLECTION].find_one({"_id": event_id})["unresolved_tokens"] == 2
        sink.annotate(None, unresolved_tokens=5)  # no id: no-op
        sink.annotate(event_id)                    # no fields: no-op
        assert db[AUDIT_COLLECTION].count_documents({}) == 1

    def test_write_errors_are_logged_by_type_and_swallowed(self, caplog):
        class BrokenDB:
            def __getitem__(self, name):
                raise ConnectionError("mongo is away: patient Grace Tam")

        sink = MongoAuditSink(BrokenDB())
        with caplog.at_level(logging.WARNING, logger="ovis.inference.audit"):
            assert sink.record(event()) is None
            sink.annotate(1, unresolved_tokens=1)
        assert "audit sink write failed: ConnectionError" in caplog.text
        assert "audit sink annotate failed: ConnectionError" in caplog.text
        assert "Grace" not in caplog.text  # exception text is never logged, only its type

    def test_custom_collection_name(self):
        db = MockDatabase()
        MongoAuditSink(db, collection="audit_test").record(event())
        assert db["audit_test"].count_documents({}) == 1 and db[AUDIT_COLLECTION].count_documents({}) == 0


class TestNullAuditSink:

    def test_keeps_nothing_and_returns_none(self):
        sink = NullAuditSink()
        assert sink.record(event()) is None
        assert sink.annotate(None, unresolved_tokens=1) is None
        assert sink.annotate("x", unresolved_tokens=1) is None


class TestEnsureAuditIndexes:

    def test_creates_ts_and_patient_ref_indexes(self):
        created = []

        class Coll:
            def create_index(self, key, **kw):
                created.append(key)

        class DB:
            def __getitem__(self, name):
                assert name == AUDIT_COLLECTION
                return Coll()

        assert ensure_audit_indexes(DB()) is True
        assert created == ["ts", "patient_ref"]

    def test_best_effort_on_mock_db(self):
        assert ensure_audit_indexes(MockDatabase()) is True

    def test_failure_is_logged_not_raised(self, caplog):
        class DB:
            def __getitem__(self, name):
                raise TimeoutError("no server")

        with caplog.at_level(logging.WARNING, logger="ovis.inference.audit"):
            assert ensure_audit_indexes(DB()) is False
        assert "could not prepare audit indexes: TimeoutError" in caplog.text
