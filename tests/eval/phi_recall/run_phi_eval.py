"""Run the PHI recall eval and write ``docs/eval-phi-recall.md`` (+ ``.json``).

    .venv/bin/python -m tests.eval.phi_recall.run_phi_eval                 # deterministic layers only
    .venv/bin/python -m tests.eval.phi_recall.run_phi_eval --backend none --backend presidio   # two tables

Scoring (span level, offsets in the original text, from ``ScrubResult.spans``):
- a gold span is *recalled* when >= 80% of its characters are covered by the
  union of predicted spans of the same class family (PERSON; ID/PHONE/EMAIL/
  HANDLE/URL; FACILITY/PLACE/ADDRESS; ORG/OCCUPATION; DATE/AGE/DOB);
- a predicted span is *correct* (precision) when >= 80% of its characters lie in
  gold spans of the same family;
- the over-redaction rate is (trap spans touched by any predicted span) / traps;
- a *stray* is a predicted span touching no gold span, trap or neutral span.
Direct = PERSON, ID, PHONE, EMAIL, HANDLE, URL, DOB; indirect = FACILITY, PLACE,
ADDRESS, ORG, OCCUPATION, DATE, AGE (the linkage classes).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.inference.scrub import NERBackend, NullBackend, ScrubContext, ner_backend_from_env
from app.inference.scrub.tokens import DATE, DOB, FACILITY, LINKAGE_CLASSES, PERSON

from .generate import DIRECT_CLASSES, FAMILY, NOW, SEED, Transcript, corpus_stats, generate

logger = logging.getLogger("ovis.eval.phi_recall")

OVERLAP = 0.8
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MD = REPO_ROOT / "docs" / "eval-phi-recall.md"
DEFAULT_JSON = REPO_ROOT / "docs" / "eval-phi-recall.json"
CLASS_ORDER = ("PERSON", "ID", "PHONE", "EMAIL", "HANDLE", "URL", "DOB", "FACILITY", "PLACE", "ADDRESS", "ORG",
               "OCCUPATION", "DATE", "AGE")
_PAST_NOTE = re.compile(r"\d+ days? ago|years ago")
_FUTURE_NOTE = re.compile(r"next \w+|in \d+ days?|in ~\d+ years")


# --- tallies --------------------------------------------------------------------------------------------
@dataclass
class Tally:
    gold: int = 0
    recalled: int = 0
    pred: int = 0
    correct: int = 0

    @property
    def recall(self) -> float | None:
        return self.recalled / self.gold if self.gold else None

    @property
    def precision(self) -> float | None:
        return self.correct / self.pred if self.pred else None

    def add(self, other: "Tally") -> None:
        self.gold += other.gold
        self.recalled += other.recalled
        self.pred += other.pred
        self.correct += other.correct

    def to_dict(self) -> dict:
        return {"gold": self.gold, "recalled": self.recalled, "recall": _r(self.recall), "pred": self.pred,
                "correct": self.correct, "precision": _r(self.precision)}


@dataclass(frozen=True)
class Example:
    transcript: str
    turn: int
    role: str
    cls: str
    kind: str
    surface: str
    scrubbed: str        # the scrubbed turn text (synthetic corpus, safe to show)

    def to_dict(self) -> dict:
        return {"transcript": self.transcript, "turn": self.turn, "role": self.role, "cls": self.cls, "kind": self.kind,
                "surface": self.surface, "scrubbed": self.scrubbed}


@dataclass
class Metrics:
    language: str
    transcripts: int = 0
    turns: int = 0
    per_class: dict[str, Tally] = field(default_factory=dict)
    direct: Tally = field(default_factory=Tally)
    indirect: Tally = field(default_factory=Tally)
    traps: int = 0
    traps_altered: int = 0
    stray: int = 0
    linkage_turn_hist: dict[int, int] = field(default_factory=dict)
    linkage_transcript_hist: dict[int, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    future_dates: int = 0
    future_as_past: int = 0
    past_dates: int = 0
    past_as_future: int = 0
    dob_gold: int = 0
    dob_as_dob: int = 0
    facility_person_confusions: int = 0
    misses_by_kind: dict[str, int] = field(default_factory=dict)
    misses: list[Example] = field(default_factory=list)
    trap_hits: list[Example] = field(default_factory=list)
    strays: list[Example] = field(default_factory=list)
    direction_errors: list[Example] = field(default_factory=list)

    # -- derived --
    @property
    def over_redaction_rate(self) -> float | None:
        return self.traps_altered / self.traps if self.traps else None

    @property
    def overall(self) -> Tally:
        t = Tally()
        t.add(self.direct)
        t.add(self.indirect)
        return t

    def latency(self, pct: float) -> float | None:
        if not self.latencies_ms:
            return None
        xs = sorted(self.latencies_ms)
        idx = min(len(xs) - 1, int(round(pct / 100.0 * (len(xs) - 1))))
        return xs[idx]

    def tally(self, cls: str) -> Tally:
        return self.per_class.setdefault(cls, Tally())

    def absorb(self, other: "Metrics") -> None:
        self.transcripts += other.transcripts
        self.turns += other.turns
        for cls, t in other.per_class.items():
            self.tally(cls).add(t)
        self.direct.add(other.direct)
        self.indirect.add(other.indirect)
        self.traps += other.traps
        self.traps_altered += other.traps_altered
        self.stray += other.stray
        for k, v in other.linkage_turn_hist.items():
            self.linkage_turn_hist[k] = self.linkage_turn_hist.get(k, 0) + v
        for k, v in other.linkage_transcript_hist.items():
            self.linkage_transcript_hist[k] = self.linkage_transcript_hist.get(k, 0) + v
        self.latencies_ms.extend(other.latencies_ms)
        self.future_dates += other.future_dates
        self.future_as_past += other.future_as_past
        self.past_dates += other.past_dates
        self.past_as_future += other.past_as_future
        self.dob_gold += other.dob_gold
        self.dob_as_dob += other.dob_as_dob
        self.facility_person_confusions += other.facility_person_confusions
        for k, v in other.misses_by_kind.items():
            self.misses_by_kind[k] = self.misses_by_kind.get(k, 0) + v
        self.misses.extend(other.misses)
        self.trap_hits.extend(other.trap_hits)
        self.strays.extend(other.strays)
        self.direction_errors.extend(other.direction_errors)

    def to_dict(self, examples: int = 40) -> dict:
        return {
            "language": self.language, "transcripts": self.transcripts, "turns": self.turns,
            "direct": self.direct.to_dict(), "indirect": self.indirect.to_dict(), "overall": self.overall.to_dict(),
            "per_class": {c: self.per_class[c].to_dict() for c in CLASS_ORDER if c in self.per_class},
            "traps": self.traps, "traps_altered": self.traps_altered, "over_redaction_rate": _r(self.over_redaction_rate),
            "stray_redactions": self.stray,
            "linkage_turn_hist": {str(k): v for k, v in sorted(self.linkage_turn_hist.items())},
            "linkage_transcript_hist": {str(k): v for k, v in sorted(self.linkage_transcript_hist.items())},
            "latency_ms": {"p50": _r(self.latency(50), 3), "p95": _r(self.latency(95), 3), "n": len(self.latencies_ms)},
            "future_dates": {"total": self.future_dates, "rendered_as_past": self.future_as_past},
            "past_dates": {"total": self.past_dates, "rendered_as_future": self.past_as_future},
            "dob": {"gold": self.dob_gold, "rendered_as_dob_token": self.dob_as_dob},
            "facility_person_confusions": self.facility_person_confusions,
            "misses_by_kind": dict(sorted(self.misses_by_kind.items(), key=lambda kv: (-kv[1], kv[0]))),
            "example_misses": [m.to_dict() for m in self.misses[:examples]],
            "example_trap_hits": [m.to_dict() for m in self.trap_hits[:examples]],
            "example_strays": [m.to_dict() for m in self.strays[:examples]],
            "example_direction_errors": [m.to_dict() for m in self.direction_errors[:examples]],
        }


@dataclass
class EvalResult:
    backend: str
    by_language: dict[str, Metrics]
    overall: Metrics
    elapsed_s: float

    def to_dict(self) -> dict:
        return {"backend": self.backend, "elapsed_s": _r(self.elapsed_s, 2), "overall": self.overall.to_dict(),
                "by_language": {k: v.to_dict() for k, v in self.by_language.items()}}


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(x, nd)


# --- span matching ------------------------------------------------------------------------------------------
def covered(start: int, end: int, spans: Iterable[tuple[int, int]]) -> int:
    """Characters of [start, end) covered by the union of ``spans``."""
    marks = [False] * max(0, end - start)
    for s, e in spans:
        lo, hi = max(s, start), min(e, end)
        for i in range(lo, hi):
            marks[i - start] = True
    return sum(marks)


def overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def family(cls: str) -> str:
    return FAMILY.get(cls, cls)


def _excerpt(text: str, limit: int = 160) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 1] + "…"


# --- the run ------------------------------------------------------------------------------------------------
def score_transcript(t: Transcript, backend: NERBackend | None, m: Metrics) -> None:
    ctx = ScrubContext(t.known(), ner=backend)
    m.transcripts += 1
    transcript_linkage: set[str] = set()
    for idx, turn in enumerate(t.turns):
        started = time.perf_counter()
        res = ctx.scrub(turn.text, now=NOW, language=t.language)
        m.latencies_ms.append((time.perf_counter() - started) * 1000.0)
        m.turns += 1
        preds = [(s.start, s.end, s.cls, s.token) for s in res.spans]
        transcript_linkage |= {c for _, _, c, _ in preds} & LINKAGE_CLASSES
        m.linkage_turn_hist[res.report.linkage_score] = m.linkage_turn_hist.get(res.report.linkage_score, 0) + 1

        for g in turn.gold:
            fam = family(g.cls)
            same = [(s, e) for s, e, c, _ in preds if family(c) == fam]
            cov = covered(g.start, g.end, same)
            hit = cov >= OVERLAP * (g.end - g.start)
            tally = m.tally(g.cls)
            tally.gold += 1
            agg = m.direct if g.cls in DIRECT_CLASSES else m.indirect
            agg.gold += 1
            if hit:
                tally.recalled += 1
                agg.recalled += 1
            else:
                m.misses_by_kind[f"{g.cls}:{g.kind}"] = m.misses_by_kind.get(f"{g.cls}:{g.kind}", 0) + 1
                m.misses.append(Example(t.id, idx, turn.role, g.cls, g.kind, g.surface, _excerpt(res.text)))
            touching = [(s, e, c, tok) for s, e, c, tok in preds if overlaps((s, e), (g.start, g.end))]
            if g.cls == DATE and g.future is not None and hit:
                notes = " ".join(tok for _, _, _, tok in touching)
                if g.future:
                    m.future_dates += 1
                    if _PAST_NOTE.search(notes):
                        m.future_as_past += 1
                        m.direction_errors.append(Example(t.id, idx, turn.role, g.cls, f"{g.kind}:future_as_past", g.surface, _excerpt(res.text)))
                else:
                    m.past_dates += 1
                    if _FUTURE_NOTE.search(notes):
                        m.past_as_future += 1
                        m.direction_errors.append(Example(t.id, idx, turn.role, g.cls, f"{g.kind}:past_as_future", g.surface, _excerpt(res.text)))
            if g.cls == DOB:
                m.dob_gold += 1
                if any(tok == "[DOB]" for _, _, _, tok in touching):
                    m.dob_as_dob += 1
            if g.cls == FACILITY and any(c == PERSON for _, _, c, _ in touching):
                m.facility_person_confusions += 1

        gold_spans = [(g.start, g.end, g.cls) for g in turn.gold]
        trap_spans = [(x.start, x.end) for x in turn.traps]
        for s, e, c, tok in preds:
            tally = m.tally(c)
            tally.pred += 1
            agg = m.direct if c in DIRECT_CLASSES else m.indirect
            agg.pred += 1
            same = [(gs, ge) for gs, ge, gc in gold_spans if family(gc) == family(c)]
            if covered(s, e, same) >= OVERLAP * (e - s):
                tally.correct += 1
                agg.correct += 1
            touches_gold = any(overlaps((s, e), (gs, ge)) for gs, ge, _ in gold_spans)
            touches_trap = any(overlaps((s, e), tr) for tr in trap_spans)
            touches_neutral = any(overlaps((s, e), n) for n in turn.neutral)
            if not (touches_gold or touches_trap or touches_neutral):
                m.stray += 1
                m.strays.append(Example(t.id, idx, turn.role, c, "stray", turn.text[s:e], _excerpt(res.text)))

        for x in turn.traps:
            m.traps += 1
            if any(overlaps((s, e), (x.start, x.end)) for s, e, _, _ in preds):
                m.traps_altered += 1
                hit_cls = next(c for s, e, c, _ in preds if overlaps((s, e), (x.start, x.end)))
                m.trap_hits.append(Example(t.id, idx, turn.role, hit_cls, x.kind, x.surface, _excerpt(res.text)))
    n = len(transcript_linkage)
    m.linkage_transcript_hist[n] = m.linkage_transcript_hist.get(n, 0) + 1


def run_eval(transcripts: list[Transcript] | None = None, backend: NERBackend | None = None) -> EvalResult:
    transcripts = transcripts if transcripts is not None else generate()
    backend = backend or NullBackend()
    # warm the lazily compiled gazetteers so latency percentiles reflect steady state
    ScrubContext(transcripts[0].known(), ner=backend).scrub("warm up", now=NOW)
    started = time.perf_counter()
    by_lang: dict[str, Metrics] = {}
    for t in transcripts:
        score_transcript(t, backend, by_lang.setdefault(t.language, Metrics(t.language)))
    overall = Metrics("all")
    for lang in sorted(by_lang):
        overall.absorb(by_lang[lang])
    return EvalResult(getattr(backend, "name", "none"), by_lang, overall, time.perf_counter() - started)


# --- report --------------------------------------------------------------------------------------------------
def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _ms(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.2f} ms"


def render_run(result: EvalResult, examples: int = 12) -> str:
    langs = ["all"] + sorted(result.by_language)
    cols = {"all": result.overall, **result.by_language}
    out: list[str] = []
    out.append(f"## Backend: `{result.backend}`\n")
    out.append(f"Scrub time {result.elapsed_s:.2f} s for {result.overall.turns} turns.\n")
    out.append("### Summary\n")
    out.append("| metric | " + " | ".join(langs) + " |")
    out.append("|---|" + "---|" * len(langs))

    def row(label, fn):
        out.append(f"| {label} | " + " | ".join(fn(cols[k]) for k in langs) + " |")

    row("transcripts / turns", lambda m: f"{m.transcripts} / {m.turns}")
    row("direct recall (bar >= 99%)", lambda m: f"{_pct(m.direct.recall)} ({m.direct.recalled}/{m.direct.gold})")
    row("indirect recall (bar >= 95%)", lambda m: f"{_pct(m.indirect.recall)} ({m.indirect.recalled}/{m.indirect.gold})")
    row("over-redaction rate (bar <= 2%)", lambda m: f"{_pct(m.over_redaction_rate)} ({m.traps_altered}/{m.traps})")
    row("precision (all classes)", lambda m: f"{_pct(m.overall.precision)} ({m.overall.correct}/{m.overall.pred})")
    row("stray redactions (spans)", lambda m: str(m.stray))
    row("future dates rendered as past", lambda m: f"{m.future_as_past}/{m.future_dates}")
    row("past dates rendered as future", lambda m: f"{m.past_as_future}/{m.past_dates}")
    row("DOB spans rendered as [DOB]", lambda m: f"{m.dob_as_dob}/{m.dob_gold}")
    row("facility spans touched by a PERSON token", lambda m: str(m.facility_person_confusions))
    row("latency per turn p50 / p95", lambda m: f"{_ms(m.latency(50))} / {_ms(m.latency(95))}")
    out.append("")

    for key in langs:
        m = cols[key]
        out.append(f"### Per-class — {key}\n")
        out.append("| class | group | gold | recalled | recall | predicted | precision |")
        out.append("|---|---|---|---|---|---|---|")
        for cls in CLASS_ORDER:
            t = m.per_class.get(cls)
            if t is None:
                continue
            group = "direct" if cls in DIRECT_CLASSES else "indirect"
            out.append(f"| {cls} | {group} | {t.gold} | {t.recalled} | {_pct(t.recall)} | {t.pred} | {_pct(t.precision)} |")
        out.append("")

    out.append("### Linkage-score histogram\n")
    out.append("Distinct indirect classes (FACILITY, PLACE, ADDRESS, ORG, OCCUPATION, DATE, AGE) the scrubber found — per turn, and as the union over a transcript.\n")
    out.append("| score | " + " | ".join(f"turns ({k})" for k in langs) + " | " + " | ".join(f"transcripts ({k})" for k in langs) + " |")
    out.append("|---|" + "---|" * (2 * len(langs)))
    scores = sorted({k for m in cols.values() for k in list(m.linkage_turn_hist) + list(m.linkage_transcript_hist)})
    for s in scores:
        out.append(f"| {s} | " + " | ".join(str(cols[k].linkage_turn_hist.get(s, 0)) for k in langs) + " | "
                   + " | ".join(str(cols[k].linkage_transcript_hist.get(s, 0)) for k in langs) + " |")
    out.append("")

    m = result.overall
    if m.misses_by_kind:
        out.append("### Misses by class:kind\n")
        out.append("| class:kind | misses |")
        out.append("|---|---|")
        for k, v in sorted(m.misses_by_kind.items(), key=lambda kv: (-kv[1], kv[0])):
            out.append(f"| {k} | {v} |")
        out.append("")
    for title, items in (("Example misses", m.misses), ("Example over-redactions (traps altered)", m.trap_hits),
                         ("Example stray redactions", m.strays), ("Example date-direction errors", m.direction_errors)):
        if not items:
            continue
        out.append(f"### {title} (first {min(examples, len(items))} of {len(items)}; synthetic text)\n")
        for ex in items[:examples]:
            out.append(f"- `{ex.transcript}` turn {ex.turn} ({ex.role}) {ex.cls}:{ex.kind} — `{ex.surface}` → {ex.scrubbed}")
        out.append("")
    return "\n".join(out)


def render_report(results: list[EvalResult], stats: dict, generated_at: datetime, commit: str | None) -> str:
    out = ["# PHI recall eval — de-identification scrubber\n"]
    out.append(f"Generated {generated_at.strftime('%Y-%m-%d %H:%M UTC')}" + (f" at commit `{commit}`" if commit else "")
               + f". Corpus seed `{stats['seed']}`, reference time `{stats['now']}`. "
               "Produced by `tests/eval/phi_recall/run_phi_eval.py`; the deterministic run is asserted by "
               "`tests/eval/test_phi_recall.py` (direct >= 0.99, indirect >= 0.95, over-redaction <= 0.02, overall and per language). "
               "Corpus design, scoring rules, how to run the optional NER pass and how to read its table: `tests/eval/README.md`.\n")
    out.append("## Corpus\n")
    out.append("| | " + " | ".join(stats["by_language"]) + " |")
    out.append("|---|" + "---|" * len(stats["by_language"]))
    keys = ("transcripts", "questionnaires", "turns", "gold_spans", "trap_spans", "with_traps", "with_3plus_indirect",
            "common_word_personas", "colloquial_cjk_forms")
    for k in keys:
        out.append(f"| {k} | " + " | ".join(str(stats["by_language"][lang][k]) for lang in stats["by_language"]) + " |")
    out.append("")
    out.append("Gold spans per class:\n")
    out.append("| class | " + " | ".join(stats["by_language"]) + " |")
    out.append("|---|" + "---|" * len(stats["by_language"]))
    for cls in CLASS_ORDER:
        out.append(f"| {cls} | " + " | ".join(str(stats["by_language"][lang]["gold_per_class"].get(cls, 0)) for lang in stats["by_language"]) + " |")
    out.append("")
    out.append("Scoring: a gold span counts as recalled when >= 80% of its characters are covered by predicted spans of the "
               "same class family (PERSON; ID/PHONE/EMAIL/HANDLE/URL; FACILITY/PLACE/ADDRESS; ORG/OCCUPATION; DATE/AGE/DOB); "
               "a predicted span is precise when >= 80% of it lies inside same-family gold. Over-redaction = traps touched / traps. "
               "Direct = PERSON, ID, PHONE, EMAIL, HANDLE, URL, DOB; indirect = the seven linkage classes.\n")
    for r in results:
        out.append(render_run(r))
    return "\n".join(out) + "\n"


def write_report(results: list[EvalResult], md_path: Path = DEFAULT_MD, json_path: Path = DEFAULT_JSON,
                 transcripts: list[Transcript] | None = None) -> None:
    stats = corpus_stats(transcripts if transcripts is not None else generate())
    generated_at = datetime.now(timezone.utc)
    # No commit stamp: this runs before the report is committed, so it could only ever name the parent
    # commit - a tree where these numbers are not reproducible. `git log docs/eval-phi-recall.json` is
    # the honest provenance.
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_report(results, stats, generated_at, None), encoding="utf-8")
    payload = {"generated_at": generated_at.isoformat(), "seed": SEED, "overlap": OVERLAP,
               "corpus": stats, "runs": [r.to_dict() for r in results]}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _backend(name: str) -> NERBackend:
    if name in ("none", "null", ""):
        return NullBackend()
    if name == "env":
        return ner_backend_from_env()
    os.environ["SCRUB_NER_BACKEND"] = name
    return ner_backend_from_env()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", action="append", default=None,
                    help="none | presidio | gliner | env (SCRUB_NER_BACKEND); repeat for several runs (default: none)")
    ap.add_argument("--out", type=Path, default=DEFAULT_MD)
    ap.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("presidio-analyzer", "presidio_analyzer", "spacy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    transcripts = generate()
    results: list[EvalResult] = []
    for name in args.backend or ["none"]:
        try:
            backend = _backend(name)
        except Exception as e:
            logger.warning("backend %s unavailable: %s", name, type(e).__name__)
            continue
        result = run_eval(transcripts, backend)
        results.append(result)
        m = result.overall
        logger.info("backend=%s direct=%s indirect=%s over_redaction=%s p50=%s p95=%s (%.2f s)", result.backend,
                    _pct(m.direct.recall), _pct(m.indirect.recall), _pct(m.over_redaction_rate), _ms(m.latency(50)),
                    _ms(m.latency(95)), result.elapsed_s)
    if not results:
        return 1
    write_report(results, args.out, args.json, transcripts)
    logger.info("wrote %s and %s", args.out, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
