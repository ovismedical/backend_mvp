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
from typing import List, Protocol, runtime_checkable

from pydantic import BaseModel

from .tokens import (
    ADDRESS, CLASSES, EMAIL, FACILITY, HANDLE, ID, ORG, PERSON, PHONE, PLACE, PRIORITY_NER, URL,
    ScrubError, Span,
)

logger = logging.getLogger("ovis.scrub.ner")
LAYER = "ner"

ENV_VAR = "SCRUB_NER_BACKEND"
GLINER_MODEL_ENV = "SCRUB_GLINER_MODEL"
DEFAULT_GLINER_MODEL = "urchade/gliner_multi_pii-v1"
# Local-model backend: which model, which languages it is worth paying for, how long to wait.
LOCAL_MODEL_ENV = "SCRUB_LOCAL_MODEL"
LOCAL_LANGUAGES_ENV = "SCRUB_NER_LANGUAGES"
LOCAL_TIMEOUT_ENV = "SCRUB_LOCAL_TIMEOUT"


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


LOCAL_LABELS = {
    "person": PERSON,
    "place": PLACE,
    "facility": FACILITY,
    "organisation": ORG,
    "organization": ORG,
    "address": ADDRESS,
    "phone": PHONE,
    "email": EMAIL,
    "url": URL,
    "id": ID,
    "handle": HANDLE,
}

LOCAL_INSTRUCTIONS = (
    "You find personal identifiers in a patient's message so they can be removed before the text "
    "is sent anywhere else. List every span that identifies a person, place, facility, organisation, "
    "address, phone number, email, URL, ID number or online handle. Copy each span exactly as it "
    "appears in the message, character for character. Never list a symptom, a medication, a "
    "measurement, a number with a unit, or a relationship word such as \"my daughter\". If there are "
    "none, return an empty list."
)


class _LocalSpan(BaseModel):
    text: str
    kind: str


class _LocalSpans(BaseModel):
    spans: List[_LocalSpan]


class LocalModelBackend:
    """A model running inside our own network, asked to name the identifiers it can see.

    This is the one task that must see raw text by definition, so it only ever runs against the
    provider the routing policy assigns to ``pii_detect``. If that provider is not the local one,
    or is not configured, the backend refuses rather than sending a patient's unredacted words to
    a remote vendor. It is additive: the deterministic layers still run, and span resolution keeps
    the longest match, so the model adds recall without being trusted to carry it.
    """

    name = "local"
    task = "pii_detect"

    def __init__(self, model: str | None = None, base_url: str | None = None,
                 languages: tuple[str, ...] | None = None, timeout: float | None = None):
        from ..router import Policy   # local import: the scrubber must not import the gateway

        try:
            provider = Policy.load().provider_for(self.task)
        except Exception as e:
            raise ScrubError(LAYER, type(e).__name__) from None
        if provider != "local":
            # The policy routes pii_detect somewhere remote. Raw text must not go there.
            raise ScrubError(LAYER, "PiiDetectNotLocal")

        self.base_url = base_url or os.getenv("LOCAL_INFERENCE_URL")
        if not self.base_url:
            raise ScrubError(LAYER, "LocalInferenceUrlUnset")
        self.model = model or os.getenv(LOCAL_MODEL_ENV) or os.getenv("LOCAL_INFERENCE_MODEL") or "medgemma"
        self.languages = languages if languages is not None else _languages_from_env()
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=os.getenv("LOCAL_INFERENCE_API_KEY") or "local",
                                  base_url=self.base_url, max_retries=0,
                                  timeout=timeout if timeout is not None else float(os.getenv(LOCAL_TIMEOUT_ENV, "60")))
        except Exception as e:
            raise ScrubError(LAYER, type(e).__name__) from None

    def _applies_to(self, language: str | None) -> bool:
        """`SCRUB_NER_LANGUAGES` limits the pass to the languages that need it (default: all)."""
        if not self.languages:
            return True
        return (language or "en").lower() in self.languages

    def detect(self, text: str, language: str | None = None) -> list[NERSpan]:
        if not text.strip() or not self._applies_to(language):
            return []
        try:
            parsed = self._client.responses.parse(
                model=self.model, store=False, instructions=LOCAL_INSTRUCTIONS,
                input=[{"role": "user", "content": text}], text_format=_LocalSpans,
            ).output_parsed
        except Exception as e:
            # Fail closed: an unreachable or misbehaving local model must not quietly downgrade
            # the scrubber to its deterministic layers alone.
            raise ScrubError(LAYER, type(e).__name__) from None
        if parsed is None:
            raise ScrubError(LAYER, "NoParsedOutput")
        return _locate(text, parsed.spans)


def _locate(text: str, spans) -> list[NERSpan]:
    """Turn the model's copied strings into character offsets, every occurrence of each.

    A model returns text, not positions, and may hallucinate a span that is not in the message;
    anything we cannot find verbatim is dropped rather than guessed at.
    """
    out: list[NERSpan] = []
    for span in spans:
        surface = (getattr(span, "text", "") or "").strip()
        cls = LOCAL_LABELS.get(str(getattr(span, "kind", "")).strip().lower())
        if not cls or len(surface) < 2:
            continue
        start = text.find(surface)
        while start != -1:
            out.append(NERSpan(start, start + len(surface), cls))
            start = text.find(surface, start + len(surface))
    return out


def _languages_from_env() -> tuple[str, ...]:
    raw = (os.getenv(LOCAL_LANGUAGES_ENV) or "").strip()
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip()) if raw else ()


def ner_backend_from_env() -> NERBackend:
    """``SCRUB_NER_BACKEND=none|local|presidio|gliner`` (default none)."""
    choice = (os.getenv(ENV_VAR) or "none").strip().lower()
    if choice in ("", "none", "null", "off", "0", "false"):
        return NullBackend()
    if choice == "local":
        return LocalModelBackend()
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
    "DEFAULT_GLINER_MODEL", "ENV_VAR", "GLINER_LABELS", "GlinerBackend", "LAYER", "LOCAL_LABELS",
    "LOCAL_LANGUAGES_ENV", "LOCAL_MODEL_ENV", "LOCAL_TIMEOUT_ENV", "LocalModelBackend", "NERBackend",
    "NERSpan", "NullBackend", "PRESIDIO_MAP", "PresidioBackend", "ner_backend_from_env", "ner_spans",
]
