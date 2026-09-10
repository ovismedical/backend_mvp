"""Token classes, candidate spans, the reversible TokenMap and ScrubError.

Every layer of the scrubber emits `Span`s; the orchestrator resolves overlaps
and asks the `TokenMap` for a token per original. The map is the only thing
that can turn `[PERSON_2]` back into a name, so it lives on the session and is
never sent to a provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

# --- token classes ---------------------------------------------------------
PERSON = "PERSON"
FACILITY = "FACILITY"
PLACE = "PLACE"
ADDRESS = "ADDRESS"
ORG = "ORG"
OCCUPATION = "OCCUPATION"
DATE = "DATE"
AGE = "AGE"
ID = "ID"
PHONE = "PHONE"
EMAIL = "EMAIL"
HANDLE = "HANDLE"
URL = "URL"
DOB = "DOB"

CLASSES = (PERSON, FACILITY, PLACE, ADDRESS, ORG, OCCUPATION, DATE, AGE, ID, PHONE, EMAIL, HANDLE, URL)
ALL_CLASSES = CLASSES + (DOB,)
# Classes whose token surface carries no index (a generalisation, not an identity).
UNINDEXED = frozenset({AGE, DOB})
# Classes that contribute to the mosaic-effect linkage score.
LINKAGE_CLASSES = frozenset({FACILITY, PLACE, ADDRESS, ORG, OCCUPATION, DATE, AGE})
# Classes never re-matched from the session TokenMap (their originals are not identities).
SESSION_EXCLUDED = frozenset({AGE, DATE, DOB})
# Classes kept out of the gateway leak check. An OCCUPATION original is a generalisation, not an
# identity, and collides with words in the static prompt templates (醫生, 翻譯), which would refuse
# every assessment of a session where the patient mentioned their job.
LEAK_EXCLUDED = SESSION_EXCLUDED | {OCCUPATION}

# --- layer priorities (higher wins on an equal-length overlap) ----------------
PRIORITY_KNOWN = 100
PRIORITY_PATTERNS = 80
PRIORITY_GAZETTEER = 60
PRIORITY_NAMES = 50
PRIORITY_NER = 40
PRIORITY_GENERALISE = 20


class ScrubError(RuntimeError):
    """A scrub layer failed. Callers treat this as a refusal (fail closed).

    The message is exactly ``f"{layer}: {cause}"`` where ``cause`` is an exception
    class name — never input text, match spans or identifier values.
    """

    def __init__(self, layer: str, cause: str):
        self.layer = str(layer)
        self.cause = str(cause)
        super().__init__(f"{self.layer}: {self.cause}")


@dataclass(frozen=True)
class Span:
    """A candidate replacement in the ORIGINAL text (``text[start:end]``)."""

    start: int
    end: int
    cls: str
    priority: int
    layer: str
    # Canonical original used for token lookup (so "Mei-Ling" and "Mei Ling" share a
    # token). Defaults to the matched text.
    key: str | None = None
    # A fixed token to reuse (session-known layer) or a fixed surface (DOB/AGE).
    token: str | None = None
    # Extra annotation after the index, e.g. "3 days ago" -> "[DATE_2 · 3 days ago]".
    note: str | None = None
    # Optional dedup key distinct from the stored original (a resolved date in ISO
    # form, so "5 March", "March 5" and "5/3" share one DATE token).
    dedup: str | None = None

    @property
    def length(self) -> int:
        return self.end - self.start


# --- text helpers shared by the layers -----------------------------------------
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_WS_RE = re.compile(r"[\s\-‐-―]+")
# Latin "word" characters for our own boundaries: ASCII letters/digits plus the
# Latin-1 / Latin Extended letter blocks. CJK is deliberately NOT a word char here,
# so "1966年" still has a boundary after the digits.
LATIN_WORD = "A-Za-z0-9À-ɏ"
LB = f"(?<![{LATIN_WORD}])"   # left boundary
RB = f"(?![{LATIN_WORD}])"    # right boundary


def has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s))


def is_cjk(s: str) -> bool:
    """True when every non-space character is CJK."""
    stripped = s.replace(" ", "")
    return bool(stripped) and all(_CJK_RE.match(ch) for ch in stripped)


def normalise_key(original: str) -> str:
    """Dedup key: CJK exact; Latin case-insensitive, whitespace/hyphen-normalised."""
    s = original.strip()
    if has_cjk(s):
        return s
    return _WS_RE.sub(" ", s).lower()


def digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def normalise_phone(s: str) -> str:
    """Strip +852/852 prefix, spaces, hyphens, brackets and dots."""
    d = digits_only(s)
    if len(d) > 8 and d.startswith("852"):
        d = d[3:]
    return d


def flexible_literal(original: str) -> str:
    """Regex source matching ``original`` with any whitespace/hyphen run between
    its Latin tokens (so "Mei Ling" also matches "Mei-Ling" and "Meiling"),
    bounded on both sides. CJK originals are exact substrings."""
    s = original.strip()
    if has_cjk(s):
        return re.escape(s)
    parts = [re.escape(p) for p in _WS_RE.split(s) if p]
    if not parts:
        return re.escape(s)
    return LB + r"[\s\-]*".join(parts) + RB


def sentence_initial(text: str, start: int) -> bool:
    """True when ``text[start]`` begins a sentence: preceded only by whitespace
    since start-of-text, '.', '!', '?', a newline or a CJK full stop."""
    i = start - 1
    while i >= 0 and text[i] in " \t\r":
        i -= 1
    if i < 0:
        return True
    return text[i] in ".!?\n。！？"


# --- the reversible map ----------------------------------------------------------
class TokenMap:
    """token -> original, with a reverse index and a per-class counter.

    Tokens are canonical ``[CLASS_n]`` strings (``[DOB]`` and ``[AGE · 50s]`` for
    the unindexed classes). ``version`` increments whenever a token is added so
    callers can invalidate caches.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}
        self._classes: dict[str, str] = {}
        self._reverse: dict[tuple[str, str], str] = {}
        self._dedup: dict[str, str] = {}          # token -> explicit dedup key (dates)
        self._next: dict[str, int] = {}
        self.version: int = 0

    # -- construction ------------------------------------------------------------
    def token_for(self, cls: str, original: str, *, fixed: str | None = None, dedup: str | None = None) -> str:
        """Return the token for ``original`` in ``cls``, creating one if needed.

        ``fixed`` names the token surface for unindexed classes (e.g. ``[DOB]``,
        ``[AGE · 50s]``); indexed classes get ``[CLASS_n]``. ``dedup`` overrides
        the dedup key (the stored original stays ``original``)."""
        if cls not in ALL_CLASSES:
            raise ValueError(f"unknown token class {cls!r}")
        key = (cls, normalise_key(dedup if dedup is not None else original))
        existing = self._reverse.get(key)
        if existing is not None:
            return existing
        if fixed is not None:
            token = fixed
            if token in self._tokens:
                # Same band / DOB already mapped to another original: keep the first,
                # still remember this original so leak/session checks can see it.
                self._reverse[key] = token
                return token
        else:
            n = self._next.get(cls, 0) + 1
            self._next[cls] = n
            token = f"[{cls}_{n}]"
        self._tokens[token] = original.strip()
        self._classes[token] = cls
        self._reverse[key] = token
        if dedup is not None:
            self._dedup[token] = dedup
        self.version += 1
        return token

    def reserve(self, cls: str, original: str) -> str:
        """Alias of token_for; used to pin [PERSON_1] at context creation."""
        return self.token_for(cls, original)

    # -- lookup ------------------------------------------------------------------
    def get(self, token: str) -> str | None:
        return self._tokens.get(token)

    def lookup(self, cls: str, index: int | None, note: str | None = None) -> str | None:
        """Resolve (class, index[, note]) to an original. For AGE the note is the
        band; with no usable note and exactly one AGE entry, that entry is used."""
        cls = cls.upper()
        if cls == DOB:
            return self._tokens.get("[DOB]")
        if cls == AGE:
            if note:
                return self._tokens.get(f"[AGE · {note.strip()}]")   # an unknown band stays unresolved
            ages = [t for t, c in self._classes.items() if c == AGE]
            return self._tokens[ages[0]] if len(ages) == 1 else None
        if index is None:
            return None
        return self._tokens.get(f"[{cls}_{index}]")

    def has(self, cls: str, index: int | None, note: str | None = None) -> bool:
        return self.lookup(cls, index, note) is not None

    def class_of(self, token: str) -> str | None:
        return self._classes.get(token)

    def dedup_for(self, token: str) -> str | None:
        """The explicit dedup key stored with ``token`` (dates/DOB), if any."""
        return self._dedup.get(token)

    def items(self) -> Iterable[tuple[str, str]]:
        return list(self._tokens.items())

    def entries(self) -> list[tuple[str, str, str]]:
        """(token, class, original) triples in insertion order."""
        return [(t, self._classes[t], o) for t, o in self._tokens.items()]

    def originals(self, exclude: Iterable[str] | None = None) -> list[str]:
        """Every mapped original, optionally excluding some classes (E4 uses
        ``exclude={AGE, DATE, DOB}``)."""
        ex = set(exclude or ())
        return [o for t, o in self._tokens.items() if self._classes[t] not in ex]

    def __len__(self) -> int:
        return len(self._tokens)

    def __contains__(self, token: object) -> bool:
        return token in self._tokens

    # -- persistence -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Mongo-safe: entries as a list (tokens contain '[' which is fine, but a
        list avoids any key-shape surprises)."""
        return {
            "version": self.version,
            "next_index": dict(self._next),
            "entries": [[t, c, o] + ([self._dedup[t]] if t in self._dedup else []) for t, c, o in self.entries()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TokenMap":
        tm = cls()
        if not data:
            return tm
        for entry in data.get("entries") or []:
            token, kls, original = entry[0], entry[1], entry[2]
            if kls not in ALL_CLASSES:
                continue
            tm._tokens[token] = original
            tm._classes[token] = kls
            dedup = entry[3] if len(entry) > 3 and entry[3] else None
            if dedup is not None:
                tm._dedup[token] = dedup
            tm._reverse[(kls, normalise_key(dedup if dedup is not None else original))] = token
        tm._next = {k: int(v) for k, v in (data.get("next_index") or {}).items()}
        # Never let a restored counter lag behind the restored tokens.
        for token, kls in tm._classes.items():
            m = re.fullmatch(r"\[[A-Z]+_(\d+)\]", token)
            if m:
                tm._next[kls] = max(tm._next.get(kls, 0), int(m.group(1)))
        tm.version = int(data.get("version") or len(tm._tokens))
        return tm


def token_surface(token: str, note: str | None) -> str:
    """``[DATE_2]`` + "3 days ago" -> ``[DATE_2 · 3 days ago]``."""
    if not note:
        return token
    return f"{token[:-1]} · {note}]"


__all__ = [
    "ADDRESS", "AGE", "ALL_CLASSES", "CLASSES", "DATE", "DOB", "EMAIL", "FACILITY", "HANDLE", "ID",
    "LEAK_EXCLUDED", "LINKAGE_CLASSES", "OCCUPATION", "ORG", "PERSON", "PHONE", "PLACE", "URL", "UNINDEXED",
    "SESSION_EXCLUDED", "PRIORITY_GAZETTEER", "PRIORITY_GENERALISE", "PRIORITY_KNOWN", "PRIORITY_NAMES",
    "PRIORITY_NER", "PRIORITY_PATTERNS", "ScrubError", "Span", "TokenMap", "LB", "RB", "LATIN_WORD",
    "has_cjk", "is_cjk", "normalise_key", "normalise_phone", "digits_only", "flexible_literal",
    "sentence_initial", "token_surface",
]
