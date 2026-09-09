"""PHI recall bars for the deterministic scrubber, run as part of the default suite.

Asserts (overall and per language): direct recall >= 0.99, indirect recall >= 0.95,
over-redaction <= 0.02, with the deterministic backend only and in well under 10 s.
If a bar fails, DO NOT lower it — fix the scrubber (with a unit test) or report the
per-class shortfall from ``docs/eval-phi-recall.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.inference.scrub import KnownIdentifiers, NullBackend, ScrubContext, leak_check
from app.inference.scrub.tokens import DATE, DOB, PERSON

from tests.eval.phi_recall import generate as gen
from tests.eval.phi_recall.generate import (
    DIRECT_CLASSES, INDIRECT_CLASSES, NOW, GoldSpan, Persona, Transcript, TrapSpan, Turn, corpus_stats, generate,
)
from tests.eval.phi_recall.run_phi_eval import (
    EvalResult, Metrics, Tally, covered, overlaps, render_report, run_eval, score_transcript, write_report,
)

pytestmark = pytest.mark.eval

DIRECT_BAR = 0.99
INDIRECT_BAR = 0.95
OVER_REDACTION_BAR = 0.02
TIME_BUDGET_S = 10.0

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def corpus() -> list[Transcript]:
    return generate()


@pytest.fixture(scope="module")
def timed_result(corpus) -> tuple[EvalResult, float]:
    started = time.perf_counter()
    result = run_eval(corpus, NullBackend())
    return result, time.perf_counter() - started


@pytest.fixture(scope="module")
def result(timed_result) -> EvalResult:
    return timed_result[0]


def _digest(transcripts: list[Transcript]) -> str:
    h = hashlib.sha256()
    for t in transcripts:
        h.update(t.id.encode())
        for turn in t.turns:
            h.update(turn.role.encode())
            h.update(turn.text.encode("utf-8"))
            for g in turn.gold:
                h.update(f"{g.start}:{g.end}:{g.cls}:{g.kind}:{g.future}".encode())
            for x in turn.traps:
                h.update(f"{x.start}:{x.end}:{x.kind}".encode())
    return h.hexdigest()


# --- the corpus --------------------------------------------------------------------------------------------
class TestCorpus:

    def test_size_and_languages(self, corpus):
        assert len(corpus) == 200
        assert sum(t.language == "en" for t in corpus) == 100
        assert sum(t.language == "zh-HK" for t in corpus) == 100
        assert len({t.id for t in corpus}) == 200

    def test_deterministic_within_process(self, corpus):
        assert _digest(generate()) == _digest(corpus)

    def test_deterministic_across_hash_seeds(self, corpus):
        """No set-iteration order or clock leaks into the corpus: two interpreters
        with different PYTHONHASHSEED values produce the same bytes."""
        code = ("from tests.eval.phi_recall.generate import generate; from tests.eval.test_phi_recall import _digest; "
                "import sys; sys.stdout.write(_digest(generate()))")
        digests = []
        for seed in ("1", "4242"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True,
                                 timeout=120, check=True)
            digests.append(out.stdout.strip())
        assert digests[0] == digests[1] == _digest(corpus)

    def test_turn_counts(self, corpus):
        for t in corpus:
            assert 6 <= t.n_turns <= 12, t.id
            assert {turn.role for turn in t.turns} <= {"assistant", "user"}

    def test_offsets_are_exact_and_disjoint(self, corpus):
        for t in corpus:
            for turn in t.turns:
                spans = []
                for g in turn.gold:
                    assert turn.text[g.start:g.end] == g.surface, (t.id, g)
                    assert g.cls in DIRECT_CLASSES | INDIRECT_CLASSES
                    spans.append((g.start, g.end))
                for x in turn.traps:
                    assert turn.text[x.start:x.end] == x.surface, (t.id, x)
                    spans.append((x.start, x.end))
                spans.sort()
                for a, b in zip(spans, spans[1:]):
                    assert a[1] <= b[0], (t.id, turn.text, a, b)

    def test_indirect_class_density(self, corpus):
        for lang in ("en", "zh-HK"):
            ts = [t for t in corpus if t.language == lang]
            share = sum(len(t.indirect_classes()) >= 3 for t in ts) / len(ts)
            assert share >= 0.30, (lang, share)
        stats = corpus_stats(corpus)
        for lang in stats["by_language"].values():
            assert set(lang["classes"]) == DIRECT_CLASSES | INDIRECT_CLASSES

    def test_common_word_patient_names(self, corpus):
        common = [t for t in corpus if t.persona.common]
        assert len(common) / len(corpus) >= 0.10
        names = {t.persona.full_name for t in common}
        assert {"Ho Long", "Ka Man", "May Chan", "Wing Yan", "Sum Yee", "Test Patient"} <= names
        # the F.5 traps: the common word used as an ordinary word, in user and assistant turns
        assert sum(t.common_word_trap for t in common) >= len(common) * 0.8
        test_patient = [t for t in common if t.persona.full_name == "Test Patient"]
        assert test_patient and all(t.persona.doctor_name == "Dr. Test" for t in test_patient)

    def test_cjk_colloquial_forms(self, corpus):
        zh = [t for t in corpus if t.language == "zh-HK"]
        assert sum(t.colloquial for t in zh) / len(zh) >= 0.20
        kinds = {g.kind for t in zh for turn in t.turns for g in turn.gold if g.cls == PERSON}
        assert {"patient_surname_short", "patient_given", "patient_ah", "doctor_colloq", "patient_title"} <= kinds

    def test_dob_formats(self, corpus):
        kinds = {g.kind for t in corpus for turn in t.turns for g in turn.gold if g.cls == DOB}
        assert len(kinds) >= 10, kinds
        assert {"dob:dd/mm/yyyy", "dob:mm/dd/yyyy", "dob:iso", "dob:yyyy年m月d日", "dob:m月d日"} <= kinds

    def test_traps_and_future_appointments(self, corpus):
        assert sum(t.has_traps() for t in corpus) / len(corpus) >= 0.25
        traps = {x.surface for t in corpus for turn in t.turns for x in turn.traps}
        for must in ("BP 120/80", "pressure was 98/60", "Sugar was 6.5", "血壓 98/60", "血糖 7.2", "Tamoxifen", "pain 7/10",
                     "陳皮", "李子", "王子", "花生", "何時發生", "掛咗8號風球", "3號線", "long", "may", "grace"):
            assert any(must in s for s in traps), must
        future = [g for t in corpus for turn in t.turns for g in turn.gold if g.cls == DATE and g.future]
        assert len(future) >= 20
        facility_traps = [g for t in corpus for turn in t.turns for g in turn.gold if g.kind == "facility:trap"]
        assert any("Queen Mary Hospital" == g.surface for g in facility_traps)
        assert any("Sha Tin Hospital" == g.surface for g in facility_traps)

    def test_questionnaire_transcripts(self, corpus):
        qs = [t for t in corpus if t.kind == "questionnaire"]
        assert len(qs) >= 20 and {t.language for t in qs} == {"en", "zh-HK"}
        text = "\n".join(turn.text for t in qs for turn in t.turns)
        for title in ("Appetite Loss", "Constipation/Diarrhoea", "Dyspnea", "Dysuria", "Insomnia", "Hot Flashes"):
            assert f"Regarding {title}:" in text
        assert "- Please specify: " in text

    def test_persona_name_never_appears_outside_gold(self, corpus):
        """Template literals must not leak the patient's name: every occurrence is a gold span."""
        for t in corpus:
            name = t.persona.full_name
            for turn in t.turns:
                gold = [(g.start, g.end) for g in turn.gold]
                idx = turn.text.find(name)
                while idx >= 0:
                    assert any(s <= idx and idx + len(name) <= e for s, e in gold), (t.id, turn.text)
                    idx = turn.text.find(name, idx + 1)


# --- the bars -----------------------------------------------------------------------------------------------
def _views(result: EvalResult) -> dict[str, Metrics]:
    return {"all": result.overall, **result.by_language}


class TestBars:

    def test_direct_recall(self, result):
        for name, m in _views(result).items():
            assert m.direct.gold > 200, name
            assert m.direct.recall >= DIRECT_BAR, (name, m.direct.to_dict(), m.misses_by_kind)

    def test_indirect_recall(self, result):
        for name, m in _views(result).items():
            assert m.indirect.gold > 200, name
            assert m.indirect.recall >= INDIRECT_BAR, (name, m.indirect.to_dict(), m.misses_by_kind)

    def test_over_redaction(self, result):
        for name, m in _views(result).items():
            assert m.traps > 300, name
            assert m.over_redaction_rate <= OVER_REDACTION_BAR, (name, m.traps_altered, m.traps, [x.surface for x in m.trap_hits[:10]])

    def test_no_stray_redactions_of_template_text(self, result):
        assert result.overall.stray == 0, [(x.surface, x.scrubbed) for x in result.overall.strays[:10]]

    def test_facility_names_are_never_split_into_persons(self, result):
        assert result.overall.facility_person_confusions == 0

    def test_future_appointments_keep_their_direction(self, result):
        m = result.overall
        assert m.future_dates >= 20 and m.future_as_past == 0, [x.scrubbed for x in m.direction_errors]
        assert m.past_as_future == 0, [x.scrubbed for x in m.direction_errors]

    def test_known_dob_expressions_become_the_dob_token(self, result):
        m = result.overall
        assert m.dob_gold >= 20 and m.dob_as_dob == m.dob_gold

    def test_runtime_budget(self, timed_result):
        result, elapsed = timed_result
        assert result.backend == "none"
        assert elapsed < TIME_BUDGET_S, elapsed
        assert result.overall.latency(95) < 5.0   # ms per turn, spec: deterministic layers <= 5 ms

    def test_leak_check_passes_on_scrubbed_turns(self, corpus):
        """Belt and braces: the gateway's leak check finds no known identifier in the scrubbed text."""
        for t in corpus[::7]:
            known = t.known()
            ctx = ScrubContext(known, ner=NullBackend())
            scrubbed = [{"role": turn.role, "content": ctx.scrub(turn.text, now=NOW, language=t.language).text} for turn in t.turns]
            assert leak_check(scrubbed, ctx.leak_forms()) == [], t.id


# --- the scoring code --------------------------------------------------------------------------------------
def _persona(**over) -> Persona:
    base = dict(language="en", full_name="Grace Tam", given="Grace", surname="Tam", sex="f", order="west", common=True,
                username="gracet42", email="grace.tam@gmail.com", phone="9123 4567", doctor_name="Dr Kenneth Chow",
                doctor_stripped="Kenneth Chow", doctor_surname="Chow", hospital="Queen Mary Hospital", known_phone=True,
                known_email=True, handle="grace.tam7")
    base.update(over)
    from datetime import date
    return Persona(dob=date(1966, 8, 22), **base)


def _transcript(turns: list[Turn], **over) -> Transcript:
    return Transcript(id="t-1", language="en", kind="chat", symptom="pain", persona=_persona(**over), turns=turns)


class TestScoring:

    def test_covered_and_overlaps(self):
        assert covered(0, 10, [(0, 4), (4, 10)]) == 10
        assert covered(0, 10, [(2, 5), (3, 7)]) == 5
        assert covered(5, 10, [(0, 5)]) == 0
        assert overlaps((0, 5), (4, 9)) and not overlaps((0, 5), (5, 9))

    def test_tally_and_metrics_absorb(self):
        a, b = Tally(gold=2, recalled=1, pred=3, correct=3), Tally(gold=2, recalled=2)
        a.add(b)
        assert (a.gold, a.recalled, a.recall, a.precision) == (4, 3, 0.75, 1.0)
        assert Tally().recall is None and Tally().precision is None
        m1, m2 = Metrics("en"), Metrics("zh-HK")
        m1.latencies_ms, m2.latencies_ms = [1.0, 2.0], [3.0, 100.0]
        m1.linkage_turn_hist = {0: 3}
        m2.linkage_turn_hist = {0: 1, 2: 1}
        m = Metrics("all")
        m.absorb(m1)
        m.absorb(m2)
        assert m.linkage_turn_hist == {0: 4, 2: 1}
        assert m.latency(50) == 3.0 and m.latency(95) == 100.0   # nearest rank over [1, 2, 3, 100]

    def test_score_transcript_recall_precision_and_traps(self):
        text = "Hello Grace Tam, my friend Mary said pain 7/10 and Queen Mary Hospital was busy."
        turn = Turn("user", text, gold=[
            GoldSpan(6, 15, PERSON, "Grace Tam", "patient_full"),
            GoldSpan(27, 31, PERSON, "Mary", "rel_intro"),
            GoldSpan(51, 70, "FACILITY", "Queen Mary Hospital", "facility:trap"),
        ], traps=[TrapSpan(37, 46, "pain 7/10", "pain 7/10")])
        assert text[51:70] == "Queen Mary Hospital" and text[37:46] == "pain 7/10"
        m = Metrics("en")
        score_transcript(_transcript([turn]), NullBackend(), m)
        assert m.turns == 1 and m.direct.gold == 2 and m.direct.recalled == 2
        assert m.indirect.gold == 1 and m.indirect.recalled == 1
        assert m.traps == 1 and m.traps_altered == 0 and m.stray == 0 and m.misses == []
        assert m.per_class[PERSON].pred == 2 and m.per_class[PERSON].precision == 1.0
        assert m.facility_person_confusions == 0
        assert m.linkage_turn_hist == {1: 1} and m.linkage_transcript_hist == {1: 1}

    def test_score_transcript_records_misses_strays_and_trap_hits(self):
        # gold claims "pain" is a PERSON (it is not scrubbed -> miss); the trap is the patient's
        # own name (it IS scrubbed -> trap altered); "Queen Mary Hospital" is unlabelled -> stray.
        text = "pain is bad, Grace Tam went to Queen Mary Hospital"
        turn = Turn("user", text, gold=[GoldSpan(0, 4, PERSON, "pain", "bogus")],
                    traps=[TrapSpan(13, 22, "Grace Tam", "name-as-trap")])
        m = Metrics("en")
        score_transcript(_transcript([turn]), NullBackend(), m)
        assert m.direct.gold == 1 and m.direct.recalled == 0
        assert m.misses_by_kind == {"PERSON:bogus": 1} and m.misses[0].surface == "pain"
        assert m.traps_altered == 1 and m.trap_hits[0].surface == "Grace Tam"
        assert m.stray == 1 and m.strays[0].surface == "Queen Mary Hospital"
        assert "[PERSON_1]" in m.misses[0].scrubbed and "Grace" not in m.misses[0].scrubbed

    def test_partial_overlap_needs_80_percent(self):
        # gold = the whole phrase "59 years old"; the scrubber replaces exactly that -> hit.
        # gold = "59 years old and more" (too wide) -> < 80% covered -> miss.
        ok = Turn("user", "I'm 59 years old", gold=[GoldSpan(4, 16, "AGE", "59 years old", "age_phrase")])
        wide = Turn("user", "I'm 59 years old and more", gold=[GoldSpan(4, 25, "AGE", "59 years old and more", "too_wide")])
        m = Metrics("en")
        score_transcript(_transcript([ok, wide]), NullBackend(), m)
        assert m.indirect.gold == 2 and m.indirect.recalled == 1
        assert m.misses_by_kind == {"AGE:too_wide": 1}

    def test_direction_and_dob_bookkeeping(self):
        text = "I'm seeing the oncologist this Friday. I was born on 22/08/1966. Last Monday I rested."
        turn = Turn("user", text, gold=[
            GoldSpan(26, 37, DATE, "this Friday", "weekday_future:prefixed", future=True),
            GoldSpan(53, 63, DOB, "22/08/1966", "dob:dd/mm/yyyy"),
            GoldSpan(65, 76, DATE, "Last Monday", "weekday_past:prefixed", future=False),
        ])
        assert text[26:37] == "this Friday" and text[53:63] == "22/08/1966" and text[65:76] == "Last Monday"
        m = Metrics("en")
        score_transcript(_transcript([turn]), NullBackend(), m)
        assert (m.future_dates, m.future_as_past, m.past_dates, m.past_as_future) == (1, 0, 1, 0)
        assert (m.dob_gold, m.dob_as_dob) == (1, 1)

    def test_report_rendering_and_files(self, result, corpus, tmp_path):
        md = tmp_path / "r.md"
        js = tmp_path / "r.json"
        write_report([result], md, js, corpus)
        text = md.read_text(encoding="utf-8")
        assert "# PHI recall eval" in text and "## Backend: `none`" in text
        assert "direct recall (bar >= 99%)" in text and "| PERSON | direct |" in text and "### Linkage-score histogram" in text
        payload = json.loads(js.read_text(encoding="utf-8"))
        assert payload["seed"] == gen.SEED and payload["runs"][0]["backend"] == "none"
        run = payload["runs"][0]
        assert set(run["by_language"]) == {"en", "zh-HK"}
        assert run["overall"]["direct"]["recall"] >= DIRECT_BAR
        assert "PERSON" in run["overall"]["per_class"] and "latency_ms" in run["overall"]
        # rendering two runs produces two backend sections
        two = render_report([result, result], corpus_stats(corpus), __import__("datetime").datetime(2026, 9, 9), "abc123")
        assert two.count("## Backend:") == 2 and "abc123" in two

    def test_known_identifiers_from_transcript(self, corpus):
        t = corpus[0]
        k = t.known()
        assert isinstance(k, KnownIdentifiers) and k.full_name == t.persona.full_name and k.dob == t.persona.dob
        assert k.doctor_name == t.persona.doctor_name and k.hospital == t.persona.hospital
