"""Orchestration: run the layers, resolve overlapping spans, replace, report.

Layers (priority): session_known = known (100) > patterns (80) > gazetteer (60)
> names (50) > ner (40) > generalise (20). Overlaps resolve longest-span-first,
ties to the higher priority; replacement is right-to-left. Any exception inside a
layer is wrapped as ``ScrubError(layer, cause)`` — callers treat it as a refusal.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .gazetteers import gazetteer_spans
from .generalise import DEFAULT_TZ, _today, generalise_spans
from .names import LAYER_NAMES, KnownIdentifiers, KnownMatcher, SessionMatcher, name_rules, phone_regex
from .ner import NERBackend, NullBackend, ner_spans
from .patterns import pattern_spans
from .tokens import (
    LEAK_EXCLUDED, LINKAGE_CLASSES, PERSON, UNINDEXED, ScrubError, Span, TokenMap,
    flexible_literal, has_cjk, normalise_phone, token_surface,
)

logger = logging.getLogger("ovis.scrub")

_CACHE_LIMIT = 512


@dataclass
class ScrubReport:
    counts: dict[str, int] = field(default_factory=dict)      # replaced spans per class
    layers: dict[str, int] = field(default_factory=dict)      # replaced spans per layer
    ner_backend: str = "none"
    linkage_score: int = 0                                    # distinct indirect classes present
    ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts), "layers": dict(self.layers), "ner_backend": self.ner_backend,
            "linkage_score": self.linkage_score, "ms": round(self.ms, 3),
        }


@dataclass(frozen=True)
class ResolvedSpan:
    """A replacement that was applied: offsets in the ORIGINAL text."""

    start: int
    end: int
    cls: str
    token: str
    layer: str


@dataclass
class ScrubResult:
    text: str
    report: ScrubReport
    spans: list[ResolvedSpan] = field(default_factory=list)


def propagate_names(text: str, candidates: Iterable[Span]) -> list[Span]:
    """A person found by a cue rule in this text ("my friend Mary") names every
    other occurrence of the same surface in the same text ("Mary drove"), so a
    repeat mention in the same turn is scrubbed even though its cue is gone —
    the within-turn counterpart of the session-known layer (A0). Latin surfaces
    match exactly as written (Capitalised), CJK exactly; the resolver still lets
    a longer span such as "Queen Mary Hospital" win."""
    seen: set[str] = set()
    out: list[Span] = []
    for c in candidates:
        if c.cls != PERSON or c.layer != LAYER_NAMES:
            continue
        surface = text[c.start:c.end].strip()
        if not surface or surface in seen:
            continue
        seen.add(surface)
        regex = re.compile(re.escape(surface) if has_cjk(surface) else flexible_literal(surface))
        for m in regex.finditer(text):
            if m.start() == c.start:
                continue
            out.append(Span(m.start(), m.end(), PERSON, c.priority - 1, LAYER_NAMES, key=c.key or surface))
    return out


def resolve_spans(spans: Iterable[Span]) -> list[Span]:
    """Longest span wins; tie -> higher priority; then leftmost. No overlaps.

    Occupancy is tracked in a byte map rather than by scanning the accepted spans, so the
    cost is linear in the text length instead of quadratic in the number of candidates
    (an unbounded message used to block the event loop for seconds)."""
    ordered = sorted(spans, key=lambda s: (-s.length, -s.priority, s.start))
    occupied = bytearray(max((s.end for s in ordered), default=0))
    chosen: list[Span] = []
    for s in ordered:
        if s.length <= 0:
            continue
        if occupied.find(b"\x01", s.start, s.end) != -1:
            continue
        occupied[s.start:s.end] = b"\x01" * (s.end - s.start)
        chosen.append(s)
    return sorted(chosen, key=lambda s: s.start)


class Scrubber:
    """The stateless engine: compiled matchers for one patient + one NER backend."""

    def __init__(self, known: KnownIdentifiers, ner: NERBackend | None = None, tz: str = DEFAULT_TZ):
        self.known = known
        self.ner = ner or NullBackend()
        self.tz = tz
        try:
            self._known = KnownMatcher(known)
            self._rules = name_rules()
        except Exception as e:
            raise ScrubError("compile", type(e).__name__) from None
        self._session = SessionMatcher()

    @staticmethod
    def _run(layer: str, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ScrubError:
            raise
        except Exception as e:
            raise ScrubError(layer, type(e).__name__) from None

    def scrub(self, text: str, token_map: TokenMap, *, now=None, language: str | None = None) -> ScrubResult:
        started = time.perf_counter()
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise ScrubError("input", "TypeError")
        if not text.strip():
            return ScrubResult(text, ScrubReport(ner_backend=self.ner.name, ms=_ms(started)))

        today = self._run("generalise", _today, now, self.tz)
        candidates: list[Span] = []
        # known first so an equal-length tie with session_known is attributed to "known"
        candidates += self._run("known", self._known.spans, text, today=today)
        candidates += self._run("session_known", self._session.spans, text, token_map)
        candidates += self._run("patterns", pattern_spans, text)
        candidates += self._run("gazetteer", gazetteer_spans, text)
        candidates += self._run("names", self._rules.spans, text, self._known)
        if not isinstance(self.ner, NullBackend):
            candidates += self._run("ner", ner_spans, text, self.ner, language)
        candidates += self._run("generalise", generalise_spans, text, now=now, tz=self.tz, dob=self.known.dob,
                                dob_md=_dob_md(self.known.dob, token_map))
        candidates += self._run("names", propagate_names, text, candidates)

        return self._run("resolve", self._apply, text, candidates, token_map, started)

    def _apply(self, text: str, candidates: list[Span], token_map: TokenMap, started: float) -> ScrubResult:
        chosen = resolve_spans(candidates)
        report = ScrubReport(ner_backend=self.ner.name)
        resolved: list[ResolvedSpan] = []
        pieces: list[str] = []
        cursor = 0
        for s in chosen:
            original = text[s.start:s.end]
            key = s.key or original
            if s.cls in UNINDEXED:
                surface = token_map.token_for(s.cls, key, fixed=s.token, dedup=s.dedup)
            elif s.token is not None:
                surface = s.token
            else:
                surface = token_surface(token_map.token_for(s.cls, key, dedup=s.dedup), s.note)
            pieces.append(text[cursor:s.start])
            pieces.append(surface)
            cursor = s.end
            resolved.append(ResolvedSpan(s.start, s.end, s.cls, surface, s.layer))
            report.counts[s.cls] = report.counts.get(s.cls, 0) + 1
            report.layers[s.layer] = report.layers.get(s.layer, 0) + 1
        pieces.append(text[cursor:])
        report.linkage_score = len({s.cls for s in chosen} & LINKAGE_CLASSES)
        report.ms = _ms(started)
        return ScrubResult("".join(pieces), report, resolved)


def _dob_md(dob, token_map: TokenMap) -> set[tuple[int, int]]:
    """(month, day) pairs already established as the patient's birthday: the profile DOB plus the
    one learned from a birth-cued date earlier in the session (stored as the [DOB] dedup key)."""
    out: set[tuple[int, int]] = set()
    if dob is not None:
        out.add((dob.month, dob.day))
    learned = token_map.dedup_for("[DOB]")
    if learned:
        try:
            month, day = learned.split("-")
            out.add((int(month), int(day)))
        except ValueError:
            pass
    return out


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


# Responses API tool items are not {role, content} turns: a `function_call` carries its payload
# under "arguments", a `function_call_output` under "output". They have to survive the replay --
# the model needs its own call history to know what it already recorded -- and their payloads are
# outbound content, so they get scrubbed like any other text.
TOOL_ITEM_TEXT_FIELD = {"function_call": "arguments", "function_call_output": "output"}
TOOL_PAYLOAD_KEYS = tuple(dict.fromkeys(TOOL_ITEM_TEXT_FIELD.values()))


def tool_item_field(msg: Any) -> str | None:
    """The payload key for a Responses API tool item, or None for an ordinary {role, content} turn."""
    if not isinstance(msg, dict):
        return None
    return TOOL_ITEM_TEXT_FIELD.get(msg.get("type"))


class ScrubContext:
    """Per-session state: the patient's identifiers, the TokenMap, the NER
    backend and a content cache. Reserves ``[PERSON_1]`` for the patient."""

    def __init__(self, known: KnownIdentifiers, token_map: TokenMap | None = None, tz: str = DEFAULT_TZ,
                 ner: NERBackend | None = None):
        if not isinstance(known, KnownIdentifiers):
            raise ScrubError("context", "TypeError")
        if not known.display_name:
            raise ScrubError("context", "ValueError")
        self.known = known
        self.token_map = token_map if token_map is not None else TokenMap()
        self.tz = tz or DEFAULT_TZ
        self.ner = ner or NullBackend()
        self.patient_token = self.token_map.reserve(PERSON, known.display_name)
        self.scrubber = Scrubber(known, self.ner, self.tz)
        self._cache: dict[tuple, str] = {}

    def scrub(self, text: str, now=None, language: str | None = None) -> ScrubResult:
        return self.scrubber.scrub(text, self.token_map, now=now, language=language)

    def _scrub_cached(self, text: str, today, now, language) -> str:
        """Scrub one string, memoised on (content hash, token_map version, day). The store key
        re-reads the version after scrubbing: a turn that taught the map a token caches under
        the version that token belongs to."""
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        cached = self._cache.get((digest, self.token_map.version, today))
        if cached is not None:
            return cached
        scrubbed = self.scrub(text, now=now, language=language).text
        if len(self._cache) >= _CACHE_LIMIT:
            self._cache.clear()
        self._cache[(digest, self.token_map.version, today)] = scrubbed
        return scrubbed

    def scrub_messages(self, messages: Iterable[dict], now=None, language: str | None = None) -> list[dict]:
        """Scrub every turn (assistant turns too), keeping role + content. Tool items keep all of
        their keys and have their payload scrubbed in place, so the model's own call history
        survives the replay. Cached by (content hash, token_map.version[, day]); a token learned
        from a later turn forces an earlier cached turn to be re-scrubbed."""
        msgs = list(messages or [])
        today = _today(now, self.tz)
        out: list[dict] = []
        for _ in range(3):
            start_version = self.token_map.version
            out = []
            for msg in msgs:
                field = tool_item_field(msg)
                if field is not None:
                    item = dict(msg)
                    payload = item.get(field)
                    if not isinstance(payload, str):
                        payload = "" if payload is None else str(payload)
                    item[field] = self._scrub_cached(payload, today, now, language)
                    out.append(item)
                    continue
                role = msg.get("role", "user") if isinstance(msg, dict) else "user"
                content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
                if not isinstance(content, str):
                    content = "" if content is None else str(content)
                out.append({"role": role, "content": self._scrub_cached(content, today, now, language)})
            if self.token_map.version == start_version:
                break
        return out

    def leak_forms(self) -> list[str]:
        """Known whole-string forms plus every session original (E4, exclude=LEAK_EXCLUDED)."""
        return self.known.leak_forms() + self.token_map.originals(exclude=LEAK_EXCLUDED)


# --- module-level conveniences --------------------------------------------------------------
def scrub_text(text: str, known: KnownIdentifiers | ScrubContext, *, now=None, token_map: TokenMap | None = None,
               tz: str = DEFAULT_TZ, ner: NERBackend | None = None) -> ScrubResult:
    ctx = known if isinstance(known, ScrubContext) else ScrubContext(known, token_map=token_map, tz=tz, ner=ner)
    return ctx.scrub(text, now=now)


def scrub_messages(messages: Iterable[dict], known: KnownIdentifiers | ScrubContext, *, now=None,
                   token_map: TokenMap | None = None, tz: str = DEFAULT_TZ, ner: NERBackend | None = None) -> list[dict]:
    ctx = known if isinstance(known, ScrubContext) else ScrubContext(known, token_map=token_map, tz=tz, ner=ner)
    return ctx.scrub_messages(messages, now=now)


_PHONE_LIKE_RE = re.compile(r"^[\s\d+()\-.]+$")


def _content_strings(messages: Iterable[dict]) -> list[str]:
    out: list[str] = []
    for msg in messages or []:
        if isinstance(msg, dict):
            # Tool payloads carry text outbound without ever appearing under "content"; a leak
            # check that cannot see them passes vacuously. Read them whenever they are present.
            for key in TOOL_PAYLOAD_KEYS:
                value = msg.get(key)
                if isinstance(value, str):
                    out.append(value)
        content = msg.get("content") if isinstance(msg, dict) else msg
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    out.append(part["text"])
                elif isinstance(part, str):
                    out.append(part)
        elif content is not None:
            out.append(str(content))
    return out


def leak_check(messages: Iterable[dict], known: Iterable[str] | None, token_map: TokenMap | None = None) -> list[str]:
    """Identifiers found verbatim in any outbound content, matched with the
    layer-1 matcher: whole strings only, case-insensitive, whitespace/hyphen-
    normalised, Latin word-bounded, CJK exact substring, phones digit-normalised.
    Never plain ``in``. The return value is for truthiness; never log it."""
    idents = [str(k) for k in (known or []) if k is not None]
    if token_map is not None:
        idents += token_map.originals(exclude=LEAK_EXCLUDED)
    contents = _content_strings(messages)
    if not contents:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for ident in idents:
        s = ident.strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        if _PHONE_LIKE_RE.match(s) and len(normalise_phone(s)) >= 7:
            regex = phone_regex(normalise_phone(s))
        else:
            regex = re.compile(flexible_literal(s), 0 if has_cjk(s) else re.IGNORECASE)
        if any(regex.search(c) for c in contents):
            found.append(ident)
    return found


__all__ = [
    "ResolvedSpan", "ScrubContext", "ScrubReport", "ScrubResult", "Scrubber", "leak_check", "propagate_names", "resolve_spans",
    "tool_item_field",
    "scrub_messages", "scrub_text",
]
