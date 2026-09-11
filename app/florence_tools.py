"""The two tools Florence may call during a check-in, and the coverage state they maintain.

The point of these is that coverage stops being a wish expressed in the prompt and becomes state.
Florence records a symptom the moment the patient pins it down, and the tool result hands back what
is still outstanding -- so she is told what remains instead of having to remember it, and the server
knows mid-conversation what was skipped while the patient is still there to ask.

Neither tool reads the database. Their arguments come from the model and their results are authored
here from a fixed vocabulary of five symptom keys, so nothing patient-identifying travels in either
direction. That is what lets them sit at `discloses: none` in the routing policy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .inference import ToolArgumentError, ToolRegistry, ToolSpec

# The five Florence assesses, in the order the prompt introduces them.
SYMPTOMS: tuple[str, ...] = ("fatigue", "appetite", "nausea", "cough", "discomfort")
RATING_MIN, RATING_MAX = 1, 5
MAX_EVIDENCE = 300


def _symptom(args: dict) -> str:
    value = args.get("symptom")
    if not isinstance(value, str) or value.strip().lower() not in SYMPTOMS:
        raise ToolArgumentError(f"symptom must be one of: {', '.join(SYMPTOMS)}")
    return value.strip().lower()


def _rating(args: dict, key: str) -> int | None:
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolArgumentError(f"{key} must be a whole number from {RATING_MIN} to {RATING_MAX}, or null")
    if not RATING_MIN <= value <= RATING_MAX:
        raise ToolArgumentError(f"{key} must be from {RATING_MIN} to {RATING_MAX}, or null")
    return value


def _text(args: dict, key: str, *, required: bool) -> str:
    value = args.get(key)
    text = value.strip() if isinstance(value, str) else ""
    if required and not text:
        raise ToolArgumentError(f"{key} is required: quote what the patient said")
    return text[:MAX_EVIDENCE]


class CoverageState:
    """What has been covered so far in one session. Serialises onto the session document.

    Held in memory for the length of a turn and saved with the ordinary end-of-turn write, so a
    tool hop never costs a database round trip.
    """

    def __init__(self, records: dict | None = None, unassessable: dict | None = None):
        self.records: dict[str, dict] = dict(records or {})
        self.unassessable: dict[str, dict] = dict(unassessable or {})

    # -- queries ----------------------------------------------------------
    def remaining(self) -> list[str]:
        settled = set(self.records) | set(self.unassessable)
        return [s for s in SYMPTOMS if s not in settled]

    def complete(self) -> bool:
        return not self.remaining()

    def summary(self) -> dict:
        """The honest account that goes on the assessment record for the clinician."""
        return {
            "recorded": [s for s in SYMPTOMS if s in self.records],
            "unassessable": [s for s in SYMPTOMS if s in self.unassessable],
            "missing": self.remaining(),
            "complete": self.complete(),
        }

    def to_dict(self) -> dict:
        return {"records": self.records, "unassessable": self.unassessable}

    @classmethod
    def from_dict(cls, doc: Any) -> "CoverageState":
        if not isinstance(doc, dict):
            return cls()
        return cls(records=doc.get("records") or {}, unassessable=doc.get("unassessable") or {})

    # -- executors --------------------------------------------------------
    def record_symptom(self, args: dict) -> dict:
        symptom = _symptom(args)
        present = args.get("present")
        if not isinstance(present, bool):
            raise ToolArgumentError("present must be true or false")
        severity = _rating(args, "severity")
        frequency = _rating(args, "frequency")
        # A rating with nothing behind it is exactly what the after-the-fact extraction produces,
        # and exactly what this is meant to replace.
        evidence = _text(args, "evidence", required=present)
        if present and severity is None:
            raise ToolArgumentError("severity is required when the symptom is present")
        self.records[symptom] = {
            "symptom": symptom,
            "present": present,
            "severity": severity if present else None,
            "frequency": frequency if present else None,
            "evidence": evidence,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        self.unassessable.pop(symptom, None)
        return {"recorded": symptom, "remaining": self.remaining()}

    def note_unassessable(self, args: dict) -> dict:
        symptom = _symptom(args)
        reason = _text(args, "reason", required=True)
        self.unassessable[symptom] = {
            "symptom": symptom,
            "reason": reason,
            "noted_at": datetime.now(timezone.utc).isoformat(),
        }
        self.records.pop(symptom, None)
        return {"unassessable": symptom, "remaining": self.remaining()}


RECORD_SYMPTOM_PARAMETERS = {
    "type": "object",
    "properties": {
        "symptom": {"type": "string", "enum": list(SYMPTOMS)},
        "present": {"type": "boolean", "description": "Is the patient experiencing this symptom at all?"},
        "severity": {"type": ["integer", "null"], "minimum": RATING_MIN, "maximum": RATING_MAX,
                     "description": "1 = very mild, 5 = very severe. Required when present is true."},
        "frequency": {"type": ["integer", "null"], "minimum": RATING_MIN, "maximum": RATING_MAX,
                      "description": "1 = rarely, 5 = very often. Use for nausea, cough and discomfort."},
        "evidence": {"type": "string",
                     "description": "A short quote of what the patient said that supports this rating."},
    },
    "required": ["symptom", "present"],
    "additionalProperties": False,
}

NOTE_UNASSESSABLE_PARAMETERS = {
    "type": "object",
    "properties": {
        "symptom": {"type": "string", "enum": list(SYMPTOMS)},
        "reason": {"type": "string", "description": "Why this could not be assessed, in a few words."},
    },
    "required": ["symptom", "reason"],
    "additionalProperties": False,
}


def build_registry(state: CoverageState) -> ToolRegistry:
    """The tools for one session, with their executors bound to that session's coverage state."""
    return ToolRegistry([
        ToolSpec(
            name="record_symptom",
            description=(
                "Record what the patient has told you about one of the five symptoms, as soon as "
                "they have said enough to judge it. Returns the symptoms still to cover."
            ),
            parameters=RECORD_SYMPTOM_PARAMETERS,
            executor=state.record_symptom,
        ),
        ToolSpec(
            name="note_unassessable",
            description=(
                "Note that a symptom cannot be assessed this time - the patient declined, could not "
                "answer, or the conversation had to end. Returns the symptoms still to cover."
            ),
            parameters=NOTE_UNASSESSABLE_PARAMETERS,
            executor=state.note_unassessable,
        ),
    ])


def coverage_of(records: Iterable[dict]) -> dict:
    """Coverage summary for an already-stored record set (used when reading a saved session)."""
    state = CoverageState(records={r["symptom"]: r for r in records if isinstance(r, dict) and r.get("symptom")})
    return state.summary()
