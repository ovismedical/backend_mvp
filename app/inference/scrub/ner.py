"""Layer 4: optional NER backends behind a Protocol.

The deterministic layers carry the recall bar today; NER is an add-on selected by
``SCRUB_NER_BACKEND=none|presidio|gliner``. Backends import their libraries
lazily and raise ``ScrubError("ner", ...)`` when configured but unavailable, so
a misconfigured deployment refuses instead of silently running without NER.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .tokens import (
    ADDRESS, CLASSES, EMAIL, ID, ORG, PERSON, PHONE, PLACE, PRIORITY_NER, URL, ScrubError, Span,
)

logger = logging.getLogger("ovis.scrub.ner")
LAYER = "ner"

ENV_VAR = "SCRUB_NER_BACKEND"
GLINER_MODEL_ENV = "SCRUB_GLINER_MODEL"
DEFAULT_GLINER_MODEL = "urchade/gliner_multi_pii-v1"


@dataclass(frozen=True)
class NERSpan:
    start: int
    end: int
    cls: str            # one of tokens.CLASSES
    score: float = 1.0


@runtime_checkable
class NERBackend(Protocol):
    name: str

    def detect(self, text: str, language: str | None = None) -> list[NERSpan]: ...


class NullBackend:
    """The default: detects nothing."""

    name = "none"

    def detect(self, text: str, language: str | None = None) -> list[NERSpan]:
        return []


# Presidio entity -> token class. DATE_TIME is deliberately dropped: the
# generaliser owns dates and an unparsed NER date would become a bare [DATE_n].
PRESIDIO_MAP = {
    "PERSON": PERSON,
    "LOCATION": PLACE,
    "ORGANIZATION": ORG,
    "PHONE_NUMBER": PHONE,
    "EMAIL_ADDRESS": EMAIL,
    "URL": URL,
    "CREDIT_CARD": ID,
    "IBAN_CODE": ID,
    "MEDICAL_LICENSE": ID,
    "US_SSN": ID,
    "US_PASSPORT": ID,
    "US_DRIVER_LICENSE": ID,
    "UK_NHS": ID,
    "IP_ADDRESS": ID,
}


class PresidioBackend:
    name = "presidio"

    def __init__(self, languages: tuple[str, ...] = ("en",), score_threshold: float = 0.4):
        try:
            from presidio_analyzer import AnalyzerEngine  # type: ignore
        except Exception as e:  # ImportError or a broken install
            raise ScrubError(LAYER, type(e).__name__) from None
        try:
            self._engine = AnalyzerEngine()
        except Exception as e:
            raise ScrubError(LAYER, type(e).__name__) from None
        self.languages = languages
        self.score_threshold = score_threshold

    def detect(self, text: str, language: str | None = None) -> list[NERSpan]:
        lang = (language or "en").split("-")[0].lower()
        if lang not in self.languages:
            lang = self.languages[0]
        results = self._engine.analyze(text=text, language=lang)
        out = []
        for r in results:
            cls = PRESIDIO_MAP.get(r.entity_type)
            if cls and r.score >= self.score_threshold:
                out.append(NERSpan(r.start, r.end, cls, float(r.score)))
        return out


GLINER_LABELS = {
    "person": PERSON,
    "name": PERSON,
    "organization": ORG,
    "company": ORG,
    "location": PLACE,
    "address": ADDRESS,
    "phone number": PHONE,
    "email": EMAIL,
    "url": URL,
    "id number": ID,
    "passport number": ID,
    "credit card number": ID,
    "medical record number": ID,
}


class GlinerBackend:
    name = "gliner"

    def __init__(self, model_name: str | None = None, threshold: float = 0.5):
        try:
            from gliner import GLiNER  # type: ignore
        except Exception as e:
            raise ScrubError(LAYER, type(e).__name__) from None
        try:
            self._model = GLiNER.from_pretrained(model_name or os.getenv(GLINER_MODEL_ENV) or DEFAULT_GLINER_MODEL)
        except Exception as e:
            raise ScrubError(LAYER, type(e).__name__) from None
        self.threshold = threshold

    def detect(self, text: str, language: str | None = None) -> list[NERSpan]:
        entities = self._model.predict_entities(text, list(GLINER_LABELS), threshold=self.threshold)
        out = []
        for ent in entities:
            cls = GLINER_LABELS.get(str(ent.get("label", "")).lower())
            if cls:
                out.append(NERSpan(int(ent["start"]), int(ent["end"]), cls, float(ent.get("score", 1.0))))
        return out


def ner_backend_from_env() -> NERBackend:
    """``SCRUB_NER_BACKEND=none|presidio|gliner`` (default none)."""
    choice = (os.getenv(ENV_VAR) or "none").strip().lower()
    if choice in ("", "none", "null", "off", "0", "false"):
        return NullBackend()
    if choice == "presidio":
        return PresidioBackend()
    if choice == "gliner":
        return GlinerBackend()
    raise ScrubError(LAYER, "UnknownBackend")


def ner_spans(text: str, backend: NERBackend, language: str | None = None) -> list[Span]:
    """Run the backend and convert its hits to candidate spans (validated)."""
    out: list[Span] = []
    for hit in backend.detect(text, language):
        if hit.cls not in CLASSES:
            continue
        start, end = int(hit.start), int(hit.end)
        if not (0 <= start < end <= len(text)):
            continue
        out.append(Span(start, end, hit.cls, PRIORITY_NER, LAYER))
    return out


__all__ = [
    "DEFAULT_GLINER_MODEL", "ENV_VAR", "GLINER_LABELS", "GlinerBackend", "LAYER", "NERBackend", "NERSpan",
    "NullBackend", "PRESIDIO_MAP", "PresidioBackend", "ner_backend_from_env", "ner_spans",
]
