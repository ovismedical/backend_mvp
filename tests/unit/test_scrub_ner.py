"""Layer 4: NER backends behind the Protocol; optional imports fail closed."""

import sys

import pytest

from app.inference.scrub import (
    GlinerBackend, KnownIdentifiers, NERBackend, NERSpan, NullBackend, PresidioBackend, ScrubContext, ScrubError,
    ner_backend_from_env,
)
from app.inference.scrub.ner import ner_spans


class FakeBackend:
    """A tiny backend that flags a fixed word so we can test integration."""

    name = "fake"

    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def detect(self, text, language=None):
        self.calls.append((text, language))
        out = []
        for word, cls in self.hits:
            i = text.find(word)
            while i >= 0:
                out.append(NERSpan(i, i + len(word), cls))
                i = text.find(word, i + 1)
        return out


class TestProtocol:

    def test_null_backend_is_default_and_detects_nothing(self):
        assert isinstance(NullBackend(), NERBackend)
        assert NullBackend().detect("Mary went to Sha Tin") == []
        assert ScrubContext(KnownIdentifiers(full_name="A B")).ner.name == "none"

    def test_fake_backend_satisfies_protocol(self):
        assert isinstance(FakeBackend([]), NERBackend)

    def test_spans_are_validated(self):
        class Bad:
            name = "bad"

            def detect(self, text, language=None):
                return [NERSpan(-1, 3, "PERSON"), NERSpan(0, 99, "PERSON"), NERSpan(0, 4, "PATIENT"), NERSpan(0, 4, "PERSON")]

        spans = ner_spans("Mary is here", Bad())
        assert [(s.start, s.end, s.cls, s.layer) for s in spans] == [(0, 4, "PERSON", "ner")]


class TestEnv:

    def test_default_none(self, monkeypatch):
        monkeypatch.delenv("SCRUB_NER_BACKEND", raising=False)
        assert ner_backend_from_env().name == "none"
        monkeypatch.setenv("SCRUB_NER_BACKEND", "none")
        assert ner_backend_from_env().name == "none"
        monkeypatch.setenv("SCRUB_NER_BACKEND", "OFF")
        assert ner_backend_from_env().name == "none"

    def test_unknown_backend_is_scrub_error(self, monkeypatch):
        monkeypatch.setenv("SCRUB_NER_BACKEND", "spacy")
        with pytest.raises(ScrubError) as exc:
            ner_backend_from_env()
        assert str(exc.value) == "ner: UnknownBackend"

    def test_presidio_configured_but_unavailable(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "presidio_analyzer", None)   # import raises ImportError
        monkeypatch.setenv("SCRUB_NER_BACKEND", "presidio")
        with pytest.raises(ScrubError) as exc:
            ner_backend_from_env()
        assert exc.value.layer == "ner" and exc.value.cause in ("ImportError", "ModuleNotFoundError")
        with pytest.raises(ScrubError):
            PresidioBackend()

    def test_gliner_configured_but_unavailable(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "gliner", None)
        monkeypatch.setenv("SCRUB_NER_BACKEND", "gliner")
        with pytest.raises(ScrubError) as exc:
            ner_backend_from_env()
        assert exc.value.layer == "ner" and exc.value.cause in ("ImportError", "ModuleNotFoundError")
        with pytest.raises(ScrubError):
            GlinerBackend()

    def test_presidio_entity_mapping(self, monkeypatch):
        """A stub presidio module exercises the adapter without the real package."""
        import types

        class Result:
            def __init__(self, entity_type, start, end, score):
                self.entity_type, self.start, self.end, self.score = entity_type, start, end, score

        class AnalyzerEngine:
            def analyze(self, text, language):
                assert language == "en"
                return [Result("PERSON", 0, 4, 0.9), Result("DATE_TIME", 5, 9, 0.9), Result("LOCATION", 10, 17, 0.3)]

        monkeypatch.setitem(sys.modules, "presidio_analyzer", types.SimpleNamespace(AnalyzerEngine=AnalyzerEngine))
        backend = PresidioBackend()
        hits = backend.detect("Mary went to Sha Tin", language="zh-HK")
        assert [(h.start, h.end, h.cls) for h in hits] == [(0, 4, "PERSON")]   # DATE_TIME dropped, low score dropped


class TestIntegration:

    def test_ner_spans_are_used_at_ner_priority(self):
        backend = FakeBackend([("Mary", "PERSON"), ("Sha Tin", "PLACE")])
        ctx = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="u1"), ner=backend)
        res = ctx.scrub("Mary went to Sha Tin", language="en")
        assert res.text == "[PERSON_2] went to [PLACE_1]"
        assert res.report.ner_backend == "fake"
        assert res.report.layers.get("ner") == 1          # Mary; Sha Tin came from the gazetteer (higher priority)
        assert backend.calls[0][1] == "en"

    def test_known_beats_ner_on_equal_span(self):
        backend = FakeBackend([("Grace Tam", "ORG")])
        ctx = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="u1"), ner=backend)
        assert ctx.scrub("Grace Tam is here").text == "[PERSON_1] is here"

    def test_ner_inside_a_facility_name_loses_to_the_longer_span(self):
        backend = FakeBackend([("Mary", "PERSON")])
        ctx = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="u1"), ner=backend)
        assert ctx.scrub("at Queen Mary Hospital").text == "at [FACILITY_1]"

    def test_backend_exception_is_scrub_error(self):
        class Boom:
            name = "boom"

            def detect(self, text, language=None):
                raise RuntimeError("A123456(7)")

        ctx = ScrubContext(KnownIdentifiers(full_name="Grace Tam", username="u1"), ner=Boom())
        with pytest.raises(ScrubError) as exc:
            ctx.scrub("hello A123456(7)")
        assert str(exc.value) == "ner: RuntimeError"
