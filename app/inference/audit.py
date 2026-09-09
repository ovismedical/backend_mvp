"""Inference audit trail: what ran where and why, never what was said.

An `AuditEvent` is written for every gateway decision (allowed or refused). It
carries opaque references (`patient_ref`, `session_ref` are HMACs computed by the
call site), routing facts, the scrub report summary and timings. It has no field
that could hold message content, names or identifiers, and the gateway never
passes it any. Sinks swallow their own errors: an audit failure must never fail
or delay a clinical call.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

logger = logging.getLogger("ovis.inference.audit")

AUDIT_COLLECTION = "audit_events"
AUDIT_KIND = "inference"
SCRUB_SUMMARY_KEYS = ("counts", "linkage_score", "ner_backend")


@dataclass
class AuditEvent:
    task: str
    decision: str                      # "allow" | "refuse"
    reason: str                        # see app.inference.router.REASONS
    outcome: str                       # "ok" | "refused" | "error:<ExceptionType>"
    provider: str | None = None
    model: str | None = None
    latency_ms: int = 0
    lang: str = "en"
    msgs: int = 0
    patient_ref: str | None = None     # opaque HMAC, never a username
    session_ref: str | None = None     # opaque HMAC, never a session id
    task_source: str | None = None     # e.g. "florence" | "questionnaire"
    scrub: dict | None = None          # {counts, linkage_score, ner_backend} or None
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    kind: str = AUDIT_KIND

    def to_dict(self) -> dict:
        return asdict(self)


def scrub_summary(report: Any) -> dict | None:
    """Reduce a ScrubReport (dataclass or dict) to the three content-free fields the audit keeps."""
    if report is None:
        return None
    if isinstance(report, dict):
        return {key: report.get(key) for key in SCRUB_SUMMARY_KEYS}
    return {key: getattr(report, key, None) for key in SCRUB_SUMMARY_KEYS}


class AuditSink(Protocol):
    def record(self, event: AuditEvent) -> Any:
        """Persist one event; returns an opaque id (or None). Must not raise."""
        ...

    def annotate(self, event_id: Any, **fields: Any) -> None:
        """Attach post-hoc facts (e.g. unresolved_tokens=2) to an already recorded event. Must not raise."""
        ...


class NullAuditSink:
    """Default sink: keeps nothing (the gateway's content-free log line is still emitted)."""

    def record(self, event: AuditEvent) -> None:
        return None

    def annotate(self, event_id: Any, **fields: Any) -> None:
        return None


class MongoAuditSink:
    """Writes audit events to the `audit_events` collection. Errors are logged by type and swallowed."""

    def __init__(self, db, collection: str = AUDIT_COLLECTION):
        self.db = db
        self.collection = collection

    def record(self, event: AuditEvent) -> Any:
        try:
            return self.db[self.collection].insert_one(event.to_dict()).inserted_id
        except Exception as e:  # noqa: BLE001 - the audit trail must never break a call
            logger.warning("audit sink write failed: %s", type(e).__name__)
            return None

    def annotate(self, event_id: Any, **fields: Any) -> None:
        if event_id is None or not fields:
            return
        try:
            self.db[self.collection].update_one({"_id": event_id}, {"$set": dict(fields)})
        except Exception as e:  # noqa: BLE001
            logger.warning("audit sink annotate failed: %s", type(e).__name__)


def ensure_audit_indexes(db, collection: str = AUDIT_COLLECTION) -> bool:
    """Best-effort indexes on `ts` and `patient_ref`. Returns False (and logs) instead of raising."""
    try:
        db[collection].create_index("ts")
        db[collection].create_index("patient_ref")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("could not prepare audit indexes: %s", type(e).__name__)
        return False
