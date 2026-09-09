"""Orchestration: span resolution, report, fail-closed ScrubError, caching,
scrub_messages shape, conveniences, performance."""

import time
import traceback
from datetime import datetime

import pytest

from app.inference.scrub import (
    KnownIdentifiers, ScrubContext, ScrubError, ScrubResult, Scrubber, TokenMap, scrub_messages, scrub_text,
)
from app.inference.scrub import scrubber as scrubber_mod
from app.inference.scrub.scrubber import resolve_spans
from app.inference.scrub.tokens import PRIORITY_GAZETTEER, PRIORITY_KNOWN, PRIORITY_NAMES, Span

NOW = datetime(2026, 9, 9, 10, 0)
KNOWN = KnownIdentifiers(full_name="Grace Tam", username="demo", email="grace.tam@example.com", phone="+852 9123 4567",
                         dob="08/22/1966", doctor_name="Dr. Amanda Lee", hospital="Ovis Demo Hospital")


class TestResolveSpans:

    def test_longest_wins_then_priority_then_leftmost(self):
        spans = [
            Span(6, 10, "PERSON", PRIORITY_NAMES, "names"),          # "Mary"
            Span(0, 19, "FACILITY", PRIORITY_GAZETTEER, "gazetteer"),  # "Queen Mary Hospital"
            Span(0, 19, "ORG", PRIORITY_NAMES, "ner"),
            Span(25, 29, "PERSON", PRIORITY_KNOWN, "known"),
            Span(25, 29, "PERSON", PRIORITY_NAMES, "names"),
            Span(30, 30, "PERSON", PRIORITY_KNOWN, "known"),            # empty: dropped
        ]
        chosen = resolve_spans(spans)
        assert [(s.start, s.end, s.cls, s.layer) for s in chosen] == [(0, 19, "FACILITY", "gazetteer"), (25, 29, "PERSON", "known")]

    def test_partial_overlap_keeps_longer(self):
        spans = [Span(0, 5, "PLACE", 60, "g"), Span(3, 12, "ADDRESS", 80, "p")]
        assert [(s.start, s.end) for s in resolve_spans(spans)] == [(3, 12)]


class TestScrubResult:

    def test_result_shape_and_report(self):
        ctx = ScrubContext(KNOWN)
        res = ctx.scrub("Grace Tam lives in Tai Koo Shing, works at HSBC Bank, I'm 59 and saw Dr Chan on 5 March", now=NOW)
        assert isinstance(res, ScrubResult)
        assert res.text == "[PERSON_1] lives in [PLACE_1], works at [ORG_1], I'm [AGE · 50s] and saw [PERSON_2] on [DATE_1 · 188 days ago]"
        assert res.report.counts == {"PERSON": 2, "PLACE": 1, "ORG": 1, "AGE": 1, "DATE": 1}
        assert res.report.layers["known"] == 1 and res.report.layers["generalise"] == 2
        assert res.report.ner_backend == "none"
        assert res.report.linkage_score == 4          # PLACE, ORG, AGE, DATE
        assert res.report.ms >= 0
        d = res.report.to_dict()
        assert set(d) == {"counts", "layers", "ner_backend", "linkage_score", "ms"}

    def test_spans_reference_original_offsets(self):
        ctx = ScrubContext(KNOWN)
        text = "call 9123 4567 now"
        res = ctx.scrub(text, now=NOW)
        assert [(text[s.start:s.end], s.cls, s.token, s.layer) for s in res.spans] == [("9123 4567", "PHONE", "[PHONE_1]", "known")]

    def test_empty_and_whitespace_text(self):
        ctx = ScrubContext(KNOWN)
        assert ctx.scrub("").text == "" and ctx.scrub("   ").text == "   "
        assert ctx.scrub(None).text == ""

    def test_linkage_score_counts_distinct_indirect_classes_only(self):
        ctx = ScrubContext(KNOWN)
        assert ctx.scrub("Grace Tam and grace.tam@example.com", now=NOW).report.linkage_score == 0
        assert ctx.scrub("Sha Tin and Mong Kok", now=NOW).report.linkage_score == 1

    def test_tokens_stable_across_calls_in_one_context(self):
        ctx = ScrubContext(KNOWN)
        assert ctx.scrub("my friend Mary", now=NOW).text == "my friend [PERSON_2]"
        assert ctx.scrub("my neighbour Peter", now=NOW).text == "my neighbour [PERSON_3]"
        assert ctx.scrub("my friend Mary", now=NOW).text == "my friend [PERSON_2]"
        assert ctx.token_map.get("[PERSON_3]") == "Peter"


class TestFailClosed:

    def test_layer_exception_becomes_scrub_error_without_input_text(self, monkeypatch):
        hkid, dob = "A1234" + "56(7)", "08/22" + "/1966"            # built so no source line carries the literal
        secret = f"my id is {hkid} born {dob}"

        def boom(text, *a, **kw):
            raise ValueError(f"{hkid} {dob}")

        monkeypatch.setattr(scrubber_mod, "pattern_spans", boom)
        ctx = ScrubContext(KNOWN)
        with pytest.raises(ScrubError) as exc:
            ctx.scrub(secret, now=NOW)
        err = exc.value
        assert str(err) == "patterns: ValueError"
        assert err.layer == "patterns" and err.cause == "ValueError"
        assert err.__cause__ is None and err.__suppress_context__ is True
        formatted = "".join(traceback.format_exception(err))
        assert hkid not in formatted and dob not in formatted and "ValueError" in formatted

    def test_generalise_failure(self, monkeypatch):
        monkeypatch.setattr(scrubber_mod, "generalise_spans", lambda *a, **kw: (_ for _ in ()).throw(KeyError("x")))
        with pytest.raises(ScrubError) as exc:
            ScrubContext(KNOWN).scrub("hello", now=NOW)
        assert str(exc.value) == "generalise: KeyError"

    def test_non_string_input(self):
        with pytest.raises(ScrubError) as exc:
            ScrubContext(KNOWN).scrub(123)
        assert str(exc.value) == "input: TypeError"

    def test_scrub_error_is_runtime_error(self):
        assert issubclass(ScrubError, RuntimeError)
        with pytest.raises(ScrubError):
            ScrubContext("not known identifiers")  # type: ignore[arg-type]

    def test_scrub_messages_propagates_scrub_error(self, monkeypatch):
        monkeypatch.setattr(scrubber_mod, "gazetteer_spans", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(ScrubError) as exc:
            ScrubContext(KNOWN).scrub_messages([{"role": "user", "content": "hi"}])
        assert str(exc.value) == "gazetteer: RuntimeError"


class TestScrubMessages:

    def test_roles_preserved_timestamps_dropped_assistant_scrubbed(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([
            {"role": "assistant", "content": "Hello Grace Tam, how are you?", "timestamp": "2026-09-09T10:00:00"},
            {"role": "user", "content": "I was at Queen Mary Hospital on Tuesday", "timestamp": "x", "extra": 1},
            {"role": "system", "content": "Be kind"},
        ], now=NOW)
        assert out == [
            {"role": "assistant", "content": "Hello [PERSON_1], how are you?"},
            {"role": "user", "content": "I was at [FACILITY_1] on [DATE_1 · 1 day ago]"},
            {"role": "system", "content": "Be kind"},
        ]

    def test_cache_hits_and_invalidates_on_token_map_version(self):
        ctx = ScrubContext(KNOWN)
        calls = []
        real = ctx.scrubber.scrub

        def counting(text, token_map, **kw):
            calls.append(text)
            return real(text, token_map, **kw)

        ctx.scrubber.scrub = counting  # type: ignore[method-assign]
        msgs = [{"role": "user", "content": "I feel tired"}]
        ctx.scrub_messages(msgs, now=NOW)
        ctx.scrub_messages(msgs, now=NOW)
        assert calls.count("I feel tired") == 1
        ctx.token_map.token_for("PERSON", "Mary")      # version bump -> re-scrub
        ctx.scrub_messages(msgs, now=NOW)
        assert calls.count("I feel tired") == 2

    def test_earlier_turn_rescrubbed_when_later_turn_learns_a_name(self):
        ctx = ScrubContext(KNOWN)
        assert ctx.scrub("Mei Ling is coming", now=NOW).text == "Mei Ling is coming"
        assert ctx.scrub("my daughter Mei Ling", now=NOW).text == "my daughter [PERSON_2]"
        assert ctx.scrub("Mei Ling is coming", now=NOW).text == "[PERSON_2] is coming"

    def test_single_pass_learns_across_messages(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([
            {"role": "user", "content": "Mei Ling is coming"},
            {"role": "user", "content": "my daughter Mei Ling brought soup"},
        ], now=NOW)
        assert [m["content"] for m in out] == ["[PERSON_2] is coming", "my daughter [PERSON_2] brought soup"]

    def test_non_string_content_tolerated(self):
        ctx = ScrubContext(KNOWN)
        out = ctx.scrub_messages([{"role": "user", "content": None}, {"role": "user"}], now=NOW)
        assert out == [{"role": "user", "content": ""}, {"role": "user", "content": ""}]


class TestConveniences:

    def test_scrub_text_and_scrub_messages_accept_known_or_context(self):
        res = scrub_text("Grace Tam here", KNOWN, now=NOW)
        assert res.text == "[PERSON_1] here"
        tm = TokenMap()
        out = scrub_messages([{"role": "user", "content": "my friend Mary"}], KNOWN, now=NOW, token_map=tm)
        assert out[0]["content"] == "my friend [PERSON_2]" and tm.get("[PERSON_2]") == "Mary"
        ctx = ScrubContext(KNOWN)
        assert scrub_text("Grace Tam", ctx).text == "[PERSON_1]"

    def test_scrubber_engine_is_reusable_with_any_token_map(self):
        eng = Scrubber(KNOWN)
        tm1, tm2 = TokenMap(), TokenMap()
        assert eng.scrub("my friend Mary", tm1, now=NOW).text == "my friend [PERSON_1]"
        assert eng.scrub("my friend Peter", tm2, now=NOW).text == "my friend [PERSON_1]"

    def test_context_leak_forms_include_session_originals(self):
        ctx = ScrubContext(KNOWN)
        ctx.scrub("my friend Mary is 59 on 5 March", now=NOW)
        forms = ctx.leak_forms()
        assert "Mary" in forms and "Grace Tam" in forms
        assert "59" not in forms and "5 March" not in forms


class TestPerformance:

    def test_200_short_messages_under_two_seconds(self):
        ctx = ScrubContext(KNOWN)
        en = "I feel tired today and my appetite is low, took 2 tablets of Panadol at 8pm. Pain 4/10, temperature 37.8."
        zh = "我今日好攰，食唔落，食咗兩粒必理痛，痛楚4/10，體溫37.8度，去沙田覆診。"
        msgs = [f"{en} #{i}" if i % 2 else f"{zh}{i}" for i in range(200)]
        started = time.perf_counter()
        for m in msgs:
            ctx.scrub(m, now=NOW)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, elapsed

    def test_typical_message_under_five_ms_deterministic_layers(self):
        ctx = ScrubContext(KNOWN)
        text = "my daughter Mei Ling took me to Queen Mary Hospital on Tuesday, I'm 59 and live in Tai Koo Shing"
        ctx.scrub(text, now=NOW)       # warm caches
        timings = []
        for _ in range(20):
            started = time.perf_counter()
            ctx.scrub(text, now=NOW)
            timings.append((time.perf_counter() - started) * 1000)
        timings.sort()
        assert timings[len(timings) // 2] < 5.0, timings
