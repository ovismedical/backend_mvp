"""Pydantic models for structured questionnaire storage (schema v2)."""

from pydantic import BaseModel, Field, field_validator
from typing import Dict, Any, Optional, List, Union
from datetime import datetime

# Free-text answers are synthesised into the triage transcript and run through the scrubber, so
# they are bounded here: an unbounded body would otherwise burn CPU in the event loop.
MAX_ANSWER_CHARS = 2000


def cap_answer_text(answers: Dict[str, Any]) -> Dict[str, Any]:
    """Reject any answer (or any string inside a list answer) longer than MAX_ANSWER_CHARS."""
    for value in (answers or {}).values():
        for item in (value if isinstance(value, list) else [value]):
            if isinstance(item, str) and len(item) > MAX_ANSWER_CHARS:
                raise ValueError(f"answer text must be at most {MAX_ANSWER_CHARS} characters")
    return answers


class SubmissionInput(BaseModel):
    """What the frontend sends — unchanged from current API contract."""
    answers: Dict[str, Any]
    sections_completed: int = Field(ge=0, le=13)
    total_sections: int = Field(ge=1, le=13)
    completion_percentage: int = Field(ge=0, le=100)
    submission_mode: Optional[str] = None

    _cap_answers = field_validator("answers")(cap_answer_text)


class DraftInput(BaseModel):
    """Draft save payload — unchanged."""
    answers: Dict[str, Any]
    current_section: int = Field(ge=0)

    _cap_answers = field_validator("answers")(cap_answer_text)


class QuestionResponse(BaseModel):
    """A single enriched question response."""
    question_id: str
    question_text: str
    type: str
    raw_value: Optional[Union[int, float, str, List[str]]] = None
    display_value: Optional[Union[str, List[str]]] = None
    severity_normalized: Optional[float] = Field(None, ge=0.0, le=1.0)
    was_shown: bool
    was_required: bool
    skip_reason: Optional[str] = None
    region_count: Optional[int] = None


class SectionResult(BaseModel):
    """An enriched symptom section."""
    section_id: str
    title: str
    clinical_area: str
    severity_score: Optional[int] = None
    severity_label: Optional[str] = None
    responses: List[QuestionResponse]


class ConcernArea(BaseModel):
    """A single area of clinical concern."""
    section_id: str
    area: str
    clinical_area: str
    severity_score: Optional[int] = None
    severity_label: Optional[str] = None
    details: Optional[List[str]] = None


class ClinicalSummary(BaseModel):
    """Pre-computed clinical roll-up for triage integration."""
    areas_of_concern: List[ConcernArea]
    symptom_count: int
    max_severity: Optional[int] = None
    max_severity_area: Optional[str] = None
    clinical_areas_affected: List[str]
    alert_flags: List[str]


class CompletionInfo(BaseModel):
    sections_completed: int
    total_sections: int
    questions_answered: int
    questions_shown: int
    total_questions: int
    completion_percentage: int
