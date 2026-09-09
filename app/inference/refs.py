"""Opaque references for logs, audit events and gateway metadata.

`patient_ref` / `session_ref` are keyed HMACs of the username / session id, so a
log line or audit document can be joined back to a patient only by someone who
holds `SECRET_KEY`. They are transport and audit identifiers only: never
interpolate them into prompts or model-bound messages.

`app.login.SECRET_KEY` is read at call time (tests patch it on the module).
"""

from __future__ import annotations

import hashlib
import hmac

PATIENT_REF_LEN = 16
SESSION_REF_LEN = 12


def _key() -> bytes:
    import app.login as login_module  # late import: read the patched/module value at call time

    secret = getattr(login_module, "SECRET_KEY", None)
    if not secret:
        raise RuntimeError("SECRET_KEY is not configured; cannot derive opaque references")
    return str(secret).encode("utf-8")


def _digest(value: str) -> str:
    if value is None:
        raise ValueError("cannot derive a reference from None")
    return hmac.new(_key(), str(value).encode("utf-8"), hashlib.sha256).hexdigest()


def patient_ref(username: str) -> str:
    """Stable per-patient reference: HMAC-SHA256(SECRET_KEY, username)[:16]."""
    return _digest(username)[:PATIENT_REF_LEN]


def session_ref(session_id: str) -> str:
    """Per-session reference: HMAC-SHA256(SECRET_KEY, session_id)[:12]."""
    return _digest(session_id)[:SESSION_REF_LEN]


__all__ = ["PATIENT_REF_LEN", "SESSION_REF_LEN", "patient_ref", "session_ref"]
