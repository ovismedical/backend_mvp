"""Tolerant token replacement in strings and nested dict/list/pydantic outputs.

Models mangle placeholders: ``[ PERSON_1 ]``, ``[person 1]``, ``【PERSON_1】``,
``PERSON_1``, ``[PERSON_1's]``. One case-insensitive regex accepts ASCII and
fullwidth/CJK brackets around a known class, an optional index and an optional
``· note``; a bracketless form is accepted only when that (class, index) exists.
Unknown tokens are left unchanged and counted so the caller can log the count.
"""

from __future__ import annotations

import re
from typing import Any

from .tokens import ALL_CLASSES, TokenMap, UNINDEXED

_OPEN = "\\[［【「〔"
_CLOSE = "\\]］】」〕"
_CLASS_ALT = "|".join(ALL_CLASSES)

TOKEN_RE = re.compile(
    rf"[{_OPEN}]\s*({_CLASS_ALT})[\s_\-]*(\d+)?\s*(?:·\s*([^{_CLOSE}]*?))?\s*('s)?\s*[{_CLOSE}]",
    re.IGNORECASE,
)
BARE_RE = re.compile(rf"(?<![A-Za-z0-9_{_OPEN}])({_CLASS_ALT})[_\s]?(\d+)(?![A-Za-z0-9_])", re.IGNORECASE)
# Anything that still looks like a placeholder after substitution.
UNRESOLVED_RE = re.compile(rf"[{_OPEN}]\s*[A-Z][A-Z_]{{2,}}[\s_\-]*\d*\s*(?:·[^{_CLOSE}]*)?\s*[{_CLOSE}]")


def _lookup(token_map: TokenMap, cls: str, index: str | None, note: str | None) -> str | None:
    cls = cls.upper()
    if cls in UNINDEXED:
        return token_map.lookup(cls, None, note)
    if index is None:
        return None
    return token_map.lookup(cls, int(index))


def reidentify_text(text: str, token_map: TokenMap) -> tuple[str, int]:
    """Replace known tokens; return (text, number of unresolved placeholders)."""
    if not text or token_map is None or len(token_map) == 0:
        unresolved = len(UNRESOLVED_RE.findall(text)) if text else 0
        return text, unresolved

    def bracketed(m: re.Match) -> str:
        original = _lookup(token_map, m.group(1), m.group(2), m.group(3))
        if original is None:
            return m.group(0)
        return original + (m.group(4) or "")

    out = TOKEN_RE.sub(bracketed, text)

    def bare(m: re.Match) -> str:
        original = _lookup(token_map, m.group(1), m.group(2), None)
        return original if original is not None else m.group(0)

    out = BARE_RE.sub(bare, out)
    return out, len(UNRESOLVED_RE.findall(out))


def reidentify(text: str, token_map: TokenMap) -> str:
    return reidentify_text(text, token_map)[0]


def reidentify_obj(obj: Any, token_map: TokenMap) -> tuple[Any, int]:
    """Recursively re-identify every string in dict/list/tuple/str/pydantic
    BaseModel values. Returns (new object, unresolved placeholder count)."""
    if isinstance(obj, str):
        return reidentify_text(obj, token_map)
    if isinstance(obj, dict):
        total = 0
        out = {}
        for k, v in obj.items():
            nv, n = reidentify_obj(v, token_map)
            out[k] = nv
            total += n
        return out, total
    if isinstance(obj, (list, tuple)):
        total = 0
        items = []
        for v in obj:
            nv, n = reidentify_obj(v, token_map)
            items.append(nv)
            total += n
        return (items if isinstance(obj, list) else tuple(items)), total
    model_fields = getattr(type(obj), "model_fields", None)
    if model_fields and hasattr(obj, "model_copy"):
        total = 0
        update = {}
        for name in model_fields:
            current = getattr(obj, name)
            nv, n = reidentify_obj(current, token_map)
            if nv is not current and nv != current:
                update[name] = nv
            total += n
        return (obj.model_copy(update=update) if update else obj), total
    return obj, 0


__all__ = ["BARE_RE", "TOKEN_RE", "UNRESOLVED_RE", "reidentify", "reidentify_obj", "reidentify_text"]
