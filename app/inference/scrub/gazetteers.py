"""Layer 3a: Hong Kong gazetteers (hospitals, districts, MTR, estates).

Data lives in ``data/*.txt`` — one entry per line, ``#`` comments, English and
Traditional Chinese in the same file. Loaded lazily and cached per process.

Matching: Latin entries are case-insensitive whole-word matches (longest entry
first); CJK entries are exact substrings. Two guards against over-redaction:
- a single-word Latin entry that is also an ordinary English word (Central,
  Stanley, Jordan — see common_words.txt) only matches Capitalised and not at
  the start of a sentence;
- a two-character CJK entry (沙田, 上水, 太子, 彩虹) only matches next to a
  location cue (住/去/到/返/喺/在/區/站/附近…), so 早上水腫 and 彩虹 the
  colour are untouched.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .tokens import (
    FACILITY, LB, PLACE, PRIORITY_GAZETTEER, RB, Span, has_cjk, sentence_initial,
)

DATA_DIR = Path(__file__).parent / "data"

GAZETTEER_FILES = {
    "hospitals": FACILITY,
    "districts": PLACE,
    "mtr": PLACE,
    "estates": PLACE,
}


@lru_cache(maxsize=None)
def load_list(name: str) -> tuple[str, ...]:
    """Entries of ``data/<name>.txt`` (deduplicated, order preserved)."""
    path = DATA_DIR / f"{name}.txt"
    seen: dict[str, None] = {}
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            seen.setdefault(line, None)
    return tuple(seen)


@lru_cache(maxsize=None)
def common_words() -> frozenset[str]:
    return frozenset(w.lower() for w in load_list("common_words"))


@lru_cache(maxsize=None)
def surnames_zh() -> tuple[str, ...]:
    return load_list("surnames_zh")


@lru_cache(maxsize=None)
def compound_surnames_zh() -> tuple[str, ...]:
    return tuple(s for s in surnames_zh() if len(s) > 1)


@lru_cache(maxsize=None)
def single_surnames_zh() -> frozenset[str]:
    return frozenset(s for s in surnames_zh() if len(s) == 1)


@lru_cache(maxsize=None)
def kinship_words() -> tuple[str, ...]:
    return load_list("kinship")


@lru_cache(maxsize=None)
def kinship_zh_with_ah() -> frozenset[str]:
    """CJK kinship words beginning with 阿 (阿媽, 阿姨…) — never a person variant."""
    return frozenset(w for w in kinship_words() if has_cjk(w) and w.startswith("阿"))


@lru_cache(maxsize=None)
def titles() -> tuple[str, ...]:
    return load_list("titles")


@lru_cache(maxsize=None)
def occupations() -> tuple[str, ...]:
    return load_list("occupations")


@lru_cache(maxsize=None)
def org_suffixes() -> tuple[str, ...]:
    return load_list("orgs_suffixes")


def split_scripts(entries) -> tuple[list[str], list[str]]:
    latin, cjk = [], []
    for e in entries:
        (cjk if has_cjk(e) else latin).append(e)
    return latin, cjk


def _alternation(entries: list[str], *, escape=re.escape) -> str:
    # Longest first so the alternation prefers the longest entry at a position.
    return "|".join(escape(e) for e in sorted(set(entries), key=lambda s: (-len(s), s)))


# Location cues that unlock two-character CJK place names.
_ZH_CUE_BEFORE = set("住去到返喺在來嚟從由搬過經落出入往至於自近係是同和及或見回")
_ZH_CUE_AFTER = set("區站邊帶近嘅的見")
_ZH_CUE_AFTER_PHRASES = ("附近", "嗰邊", "那邊", "一帶", "嘅", "的")


class Gazetteer:
    """Compiled matcher for one class (FACILITY or PLACE) over several files."""

    def __init__(self, cls: str, entries, layer: str):
        self.cls = cls
        self.layer = layer
        latin, cjk = split_scripts(entries)
        stop = common_words()
        plain, capital_only = [], []
        for e in latin:
            if " " not in e and "-" not in e and e.lower() in stop:
                capital_only.append(e)
            else:
                plain.append(e)
        self._latin = re.compile(LB + "(?:" + _alternation(plain) + ")" + RB, re.IGNORECASE) if plain else None
        self._capital = (
            re.compile(LB + "(?:" + _alternation([e[0].upper() + e[1:] for e in capital_only]) + ")" + RB)
            if capital_only else None
        )
        long_cjk = [e for e in cjk if len(e) >= 3]
        short_cjk = [e for e in cjk if len(e) <= 2]
        self._cjk = re.compile("(?:" + _alternation(long_cjk) + ")") if long_cjk else None
        self._cjk_short = re.compile("(?:" + _alternation(short_cjk) + ")") if short_cjk else None

    def spans(self, text: str) -> list[Span]:
        out: list[Span] = []
        if self._latin:
            for m in self._latin.finditer(text):
                out.append(Span(m.start(), m.end(), self.cls, PRIORITY_GAZETTEER, self.layer))
        if self._capital:
            for m in self._capital.finditer(text):
                if not sentence_initial(text, m.start()):
                    out.append(Span(m.start(), m.end(), self.cls, PRIORITY_GAZETTEER, self.layer))
        if has_cjk(text):
            if self._cjk:
                for m in self._cjk.finditer(text):
                    out.append(Span(m.start(), m.end(), self.cls, PRIORITY_GAZETTEER, self.layer))
            if self._cjk_short:
                for m in self._cjk_short.finditer(text):
                    if _short_cjk_cued(text, m.start(), m.end()):
                        out.append(Span(m.start(), m.end(), self.cls, PRIORITY_GAZETTEER, self.layer))
        return out


def _short_cjk_cued(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 3):start]
    after = text[end:end + 2]
    if any(ch in _ZH_CUE_BEFORE for ch in before):
        return True
    if after and after[0] in _ZH_CUE_AFTER:
        return True
    return any(after.startswith(p) for p in _ZH_CUE_AFTER_PHRASES)


@lru_cache(maxsize=None)
def gazetteers() -> tuple[Gazetteer, ...]:
    by_cls: dict[str, list[str]] = {}
    for name, cls in GAZETTEER_FILES.items():
        by_cls.setdefault(cls, []).extend(load_list(name))
    return tuple(Gazetteer(cls, entries, layer="gazetteer") for cls, entries in by_cls.items())


def gazetteer_spans(text: str) -> list[Span]:
    out: list[Span] = []
    for g in gazetteers():
        out.extend(g.spans(text))
    return out


__all__ = [
    "DATA_DIR", "GAZETTEER_FILES", "Gazetteer", "common_words", "compound_surnames_zh", "gazetteer_spans",
    "gazetteers", "kinship_words", "kinship_zh_with_ah", "load_list", "occupations", "org_suffixes",
    "single_surnames_zh", "split_scripts", "surnames_zh", "titles",
]
