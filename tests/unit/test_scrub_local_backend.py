"""The local-model NER backend: routed by policy, fails closed, additive to the deterministic layers.

No model runs here. The backend's client is replaced by a stub, so these tests pin the contract
(where it is allowed to send text, what it does with what comes back) rather than a model's output.
"""

import pytest

from app.inference.scrub import KnownIdentifiers, ScrubContext
from app.inference.scrub.ner import (
    LOCAL_LANGUAGES_ENV, LOCAL_MODEL_ENV, LocalModelBackend, NullBackend, ner_backend_from_env,
)
from app.inference.scrub.tokens import FACILITY, PERSON, PLACE, ScrubError


class StubSpan:
    def __init__(self, text, kind):
        self.text, self.kind = text, kind


class StubClient:
    """Stands in for the OpenAI client pointed at the local server."""

    def __init__(self, spans=(), raises=None, parsed_none=False):
        self.spans, self.raises, self.parsed_none = list(spans), raises, parsed_none
        self.calls = []
        self.responses = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        payload = type("P", (), {"spans": [] if self.parsed_none else self.spans})()
        return type("R", (), {"output_parsed": None if self.parsed_none else payload})()


@pytest.fixture
def local_env(monkeypatch):
    monkeypatch.setenv("LOCAL_INFERENCE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv(LOCAL_MODEL_ENV, "test-model")
    monkeypatch.delenv(LOCAL_LANGUAGES_ENV, raising=False)


def backend_with(monkeypatch, local_env_applied=None, **stub_kwargs):
    b = LocalModelBackend()
    b._client = StubClient(**stub_kwargs)
    return b


class TestRouting:
    """The backend sends raw, unscrubbed text, so where it may send it is a policy decision."""

    def test_refuses_when_the_policy_does_not_route_pii_detect_locally(self, local_env, tmp_path, monkeypatch):
        policy = tmp_path / "remote.yaml"
        policy.write_text(
            "version: 1\nflags: {}\n"
            "providers: {openai: {requires: []}}\n"
            "tasks: {pii_detect: {provider: openai, on_refuse: skip}}\n"
        )
        monkeypatch.setenv("INFERENCE_POLICY_PATH", str(policy))
        with pytest.raises(ScrubError) as exc:
            LocalModelBackend()
        assert "PiiDetectNotLocal" in str(exc.value)

    def test_refuses_when_no_local_server_is_configured(self, monkeypatch):
        monkeypatch.delenv("LOCAL_INFERENCE_URL", raising=False)
        with pytest.raises(ScrubError) as exc:
            LocalModelBackend()
        assert "LocalInferenceUrlUnset" in str(exc.value)

    def test_shipped_policy_routes_pii_detect_locally(self, local_env):
        """The claim that private queries go to the local model is the policy file's to make."""
        assert LocalModelBackend().name == "local"

    def test_selected_by_env(self, local_env, monkeypatch):
        monkeypatch.setenv("SCRUB_NER_BACKEND", "local")
        assert isinstance(ner_backend_from_env(), LocalModelBackend)
        monkeypatch.setenv("SCRUB_NER_BACKEND", "none")
        assert isinstance(ner_backend_from_env(), NullBackend)


class TestDetect:

    def test_locates_every_occurrence_of_a_returned_span(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, spans=[StubSpan("Mei Ling", "person")])
        text = "Mei Ling came, then Mei Ling left"
        hits = b.detect(text, "en")
        assert [(h.start, h.end, h.cls) for h in hits] == [(0, 8, PERSON), (20, 28, PERSON)]

    def test_drops_spans_that_are_not_in_the_text(self, local_env, monkeypatch):
        """A model returns strings, not offsets; anything it invents is dropped, never guessed at."""
        b = backend_with(monkeypatch, spans=[StubSpan("Someone Else", "person"), StubSpan("Mei Ling", "person")])
        assert [h.cls for h in b.detect("Mei Ling came", "en")] == [PERSON]

    def test_drops_unknown_kinds_and_single_characters(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, spans=[StubSpan("Mei Ling", "mood"), StubSpan("X", "person")])
        assert b.detect("Mei Ling and X", "en") == []

    def test_maps_kinds_to_token_classes(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, spans=[StubSpan("Queen Mary", "facility"), StubSpan("Sha Tin", "place")])
        assert sorted(h.cls for h in b.detect("Queen Mary in Sha Tin", "en")) == sorted([FACILITY, PLACE])

    def test_empty_text_never_reaches_the_model(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, spans=[StubSpan("x", "person")])
        assert b.detect("   ", "en") == [] and b._client.calls == []


class TestLanguageGate:
    """The pass costs a model call per turn, so it can be limited to the languages that need it."""

    def test_runs_only_for_the_configured_languages(self, local_env, monkeypatch):
        monkeypatch.setenv(LOCAL_LANGUAGES_ENV, "zh-HK")
        b = backend_with(monkeypatch, spans=[StubSpan("美玲", "person")])
        assert b.detect("我個女美玲", "en") == [] and b._client.calls == []
        assert len(b.detect("我個女美玲", "zh-HK")) == 1

    def test_unset_means_every_language(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, spans=[StubSpan("Mei Ling", "person")])
        assert len(b.detect("Mei Ling", "en")) == 1


class TestFailsClosed:
    """An unreachable model must not quietly downgrade the scrubber to its deterministic layers."""

    def test_a_model_error_is_a_scrub_error(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, raises=RuntimeError("connection refused"))
        with pytest.raises(ScrubError) as exc:
            b.detect("anything", "en")
        assert "RuntimeError" in str(exc.value)
        assert "connection refused" not in str(exc.value)   # never the message, only its type

    def test_no_parsed_output_is_a_scrub_error(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, parsed_none=True)
        with pytest.raises(ScrubError):
            b.detect("anything", "en")

    def test_the_error_never_carries_the_patient_text(self, local_env, monkeypatch):
        secret = "A123456(7) and 9123 4567"
        b = backend_with(monkeypatch, raises=ValueError(secret))
        with pytest.raises(ScrubError) as exc:
            b.detect(secret, "en")
        assert secret not in str(exc.value) and secret not in repr(exc.value)


class TestAdditive:
    """The model adds recall; it is never trusted to carry it."""

    def test_deterministic_layers_still_win_the_longer_span(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, spans=[StubSpan("Mary", "person")])
        ctx = ScrubContext(known=KnownIdentifiers(full_name="Grace Tam", username="demo"), ner=b)
        out = ctx.scrub("I went to Queen Mary Hospital", language="en").text
        assert "[FACILITY_1]" in out and "PERSON_2" not in out

    def test_model_catches_what_the_gazetteers_do_not(self, local_env, monkeypatch):
        b = backend_with(monkeypatch, spans=[StubSpan("Bettina", "person")])
        ctx = ScrubContext(known=KnownIdentifiers(full_name="Grace Tam", username="demo"), ner=b)
        assert "Bettina" not in ctx.scrub("Bettina drove me", language="en").text
