"""What Florence remembers about a patient between check-ins.

Capture. After a check-in finishes, a background extraction reads the de-identified transcript and
the patient's existing notes and proposes add / update / forget operations. Every proposed note is
screened with the PHI scrubber before it is stored: the model's output is re-identified from the
session TokenMap, scrubbed again, and dropped if the scrubber finds a blocked class in it (an address,
a phone number, a place, a hospital ...). What may be remembered is decided by the scrubber's
classes, not by the model following a prompt.

Recall. At the start of the next check-in the active notes are snapshotted onto the session, the
people they name are seeded into the session TokenMap (so a name with no cue word is still
tokenised, and joins the leak check), and they reach the model as one scrubbed `developer` message
ahead of the transcript. Assessment and triage never see them.

Storage is one document per patient in `patient_memories`, always written whole behind an
optimistic `version` check. No clinician or admin endpoint reads it.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from .inference import InferenceRequest, get_gateway
from .inference.refs import patient_ref, session_ref
from .inference.scrub import ScrubContext, TokenMap, reidentify_text
from .inference.scrub.reidentify import TOKEN_RE
from .inference.scrub.tokens import (
    ADDRESS, DOB, EMAIL, FACILITY, HANDLE, ID, OCCUPATION, ORG, PERSON, PHONE, PLACE, URL,
)
from .florence_utils import (
    MEMORY_CATEGORIES, MemoryExtractionOutput, build_scrub_context, load_prompt_template, model_messages,
    scrub_summary_from_messages, task_metadata,
)

logger = logging.getLogger("ovis.florence")

MEMORIES = "patient_memories"

# FLORENCE_MEMORY: off (default) | capture (extract and store, never inject) | on.
MEMORY_MODES = ("off", "capture", "on")

MAX_MEMORIES = 40       # kept per patient; the least recently updated go first
RECALL_LIMIT = 20       # brought into a check-in
MAX_TEXT = 200
MAX_OPS = 20            # read from one extraction
DURABILITY_TTL = {"short": timedelta(days=7), "long": timedelta(days=365)}

# A note that names any of these is not stored. People, dates, ages and occupations are allowed:
# a pet's name, a daughter who visits, "retired teacher" are what the feature is for.
MEMORY_BLOCKED_CLASSES = frozenset({ADDRESS, PHONE, EMAIL, HANDLE, URL, ID, DOB, PLACE, ORG, FACILITY})
# The classes the scrubber's session layer re-matches from a TokenMap. Dates and ages are
# generalised afresh on every scrub, so seeding them would add nothing.
SEEDED_CLASSES = frozenset({PERSON, OCCUPATION})

MEMORY_INSTRUCTIONS = (
    "You keep short notes about a patient for a friendly check-in nurse, so she can pick up next time "
    "where the last conversation left off. The transcript is de-identified: the patient appears as "
    "[PERSON_1] and other people, places, dates and numbers as bracketed placeholders. Copy placeholders "
    "exactly as written, never invent one, and never guess who or where they refer to."
)

EXISTING_HEADER = {
    "en": "Notes already kept about this patient:",
    "zh-HK": "已經記低關於呢位病人嘅筆記：",
}
NO_EXISTING = {"en": "(none)", "zh-HK": "（冇）"}

RECALL_HEADER = {
    "en": "Notes from this patient's earlier check-ins - things they chose to tell you, and when. "
          "They may be out of date:",
    "zh-HK": "呢位病人喺之前報到時同你講過嘅嘢，同埋幾時講。情況可能已經唔同咗：",
}


def memory_mode() -> str:
    value = (os.getenv("FLORENCE_MEMORY") or "off").strip().lower()
    return value if value in MEMORY_MODES else "off"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return None


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScreenedMemory:
    text: str                                   # clear text, as stored
    entities: tuple[tuple[str, str], ...] = ()  # (class, original) to seed into a later session's map


def _without_patient(text: str, ctx: ScrubContext) -> str:
    """The patient's own placeholder becomes "the patient": a note is about them and never needs their name."""
    def swap(match):
        index = match.group(2)
        if match.group(1).upper() == PERSON and index and f"[PERSON_{int(index)}]" == ctx.patient_token:
            return "the patient" + (match.group(4) or "")
        return match.group(0)
    return TOKEN_RE.sub(swap, text)


def screen_memory(text_scrubbed: str, ctx: ScrubContext, *, now=None, language: Optional[str] = None) -> Optional[ScreenedMemory]:
    """A proposed note in placeholder form -> the note as it may be stored, or None if it must not be.

    1. a placeholder of a blocked class in the model's own output rejects it outright;
    2. it is re-identified from the session map, and a placeholder the map cannot resolve rejects it
       (the model invented one);
    3. the clear text is scrubbed again on a copy of the session map, so an address the model wrote
       out in full is caught even though no placeholder marked it. The live map is never touched.
    """
    text = (text_scrubbed or "").strip()[:MAX_TEXT].strip()
    if not text:
        return None
    if any(match.group(1).upper() in MEMORY_BLOCKED_CLASSES for match in TOKEN_RE.finditer(text)):
        return None
    clear, unresolved = reidentify_text(_without_patient(text, ctx), ctx.token_map)
    if unresolved:
        return None
    probe = TokenMap.from_dict(ctx.token_map.to_dict())
    result = ctx.scrubber.scrub(clear, probe, now=now, language=language)
    if {span.cls for span in result.spans} & MEMORY_BLOCKED_CLASSES:
        return None
    patient_name = ctx.token_map.get(ctx.patient_token)
    entities: list[tuple[str, str]] = []
    for span in result.spans:
        if span.cls not in SEEDED_CLASSES:
            continue
        original = (probe.get(span.token) or clear[span.start:span.end]).strip()
        if original and original != patient_name and (span.cls, original) not in entities:
            entities.append((span.cls, original))
    return ScreenedMemory(text=clear, entities=tuple(entities))


# ---------------------------------------------------------------------------
# Storage: one document per patient
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PlannedOp:
    op: str                                  # add | update | forget
    memory_id: Optional[str] = None          # update / forget
    memory: Optional[ScreenedMemory] = None  # add / update
    category: str = "other"
    durability: str = "long"


class PatientMemories:
    """A patient's notes, loaded whole, changed in memory and saved whole."""

    def __init__(self, user_id: str, memories: Optional[list] = None, enabled: bool = True, version: int = 0):
        self.user_id = user_id
        self.memories: list[dict] = list(memories or [])
        self.enabled = enabled
        self.version = version

    @classmethod
    def load(cls, db, user_id: str) -> "PatientMemories":
        doc = db[MEMORIES].find_one({"user_id": user_id})
        if not doc:
            return cls(user_id)
        # A deep copy: the notes are edited in place and must not alias whatever the driver returned.
        return cls(user_id, memories=copy.deepcopy(doc.get("memories") or []),
                   enabled=doc.get("enabled", True) is not False, version=int(doc.get("version") or 0))

    # -- queries ----------------------------------------------------------
    def active(self, now: datetime) -> list[dict]:
        out = []
        for memory in self.memories:
            expires = _parse(memory.get("expires_at"))
            if expires is None or expires > now:
                out.append(memory)
        return out

    def recent(self, now: datetime, limit: Optional[int] = RECALL_LIMIT) -> list[dict]:
        ordered = sorted(self.active(now), key=lambda m: m.get("updated_at") or "", reverse=True)
        return ordered[:limit] if limit else ordered

    def find(self, memory_id: str) -> Optional[dict]:
        return next((m for m in self.memories if m.get("memory_id") == memory_id), None)

    # -- changes ----------------------------------------------------------
    @staticmethod
    def _item(planned: PlannedOp, *, source_session_id: Optional[str], now: datetime, memory_id: Optional[str] = None) -> dict:
        durability = planned.durability if planned.durability in DURABILITY_TTL else "long"
        return {
            "memory_id": memory_id or f"mem_{secrets.token_hex(6)}",
            "text": planned.memory.text,
            "category": planned.category if planned.category in MEMORY_CATEGORIES else "other",
            "durability": durability,
            "entities": [{"cls": cls, "original": original} for cls, original in planned.memory.entities],
            "source_session_id": source_session_id,
            "captured_at": _iso(now),
            "updated_at": _iso(now),
            "expires_at": _iso(now + DURABILITY_TTL[durability]),
        }

    def add(self, planned: PlannedOp, *, source_session_id: Optional[str], now: datetime) -> None:
        key = planned.memory.text.casefold()
        for i, memory in enumerate(self.memories):
            if (memory.get("text") or "").casefold() == key:   # said again: refresh, don't duplicate
                self.memories[i] = self._item(planned, source_session_id=source_session_id, now=now,
                                              memory_id=memory.get("memory_id"))
                return
        self.memories.append(self._item(planned, source_session_id=source_session_id, now=now))

    def update(self, planned: PlannedOp, *, source_session_id: Optional[str], now: datetime) -> bool:
        for i, memory in enumerate(self.memories):
            if memory.get("memory_id") == planned.memory_id:
                self.memories[i] = self._item(planned, source_session_id=source_session_id, now=now,
                                              memory_id=planned.memory_id)
                return True
        return False

    def remove(self, memory_id: str) -> bool:
        before = len(self.memories)
        self.memories = [m for m in self.memories if m.get("memory_id") != memory_id]
        return len(self.memories) != before

    def prune(self, now: datetime) -> None:
        self.memories = self.recent(now, limit=MAX_MEMORIES)

    def save(self, db, *, now: datetime) -> bool:
        """False when another writer got there first (the caller reloads and re-applies)."""
        payload = {"user_id": self.user_id, "enabled": self.enabled, "memories": self.memories,
                   "version": self.version + 1, "updated_at": _iso(now)}
        collection = db[MEMORIES]
        if self.version == 0:
            try:
                collection.insert_one(payload)
            except DuplicateKeyError:
                return False
        else:
            result = collection.update_one({"user_id": self.user_id, "version": self.version}, {"$set": payload})
            if getattr(result, "modified_count", 0) != 1:
                return False
        self.version += 1
        return True


def mutate_memories(db, user_id: str, change: Callable[[PatientMemories], None], *, now: Optional[datetime] = None,
                    attempts: int = 2) -> Optional[PatientMemories]:
    """Load, change, prune, save; on a lost race reload and re-apply. None if every attempt lost."""
    now = now or _now()
    for _ in range(attempts):
        memories = PatientMemories.load(db, user_id)
        change(memories)
        memories.prune(now)
        if memories.save(db, now=now):
            return memories
    logger.warning("patient memory write lost %d races; change dropped", attempts)
    return None


def ensure_memory_indexes(db) -> None:
    try:
        db[MEMORIES].create_index("user_id", unique=True)
    except Exception as e:  # best-effort, as for the session indexes
        logger.warning("could not create %s indexes: %s", MEMORIES, type(e).__name__)


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------
def recall_snapshot(db, user_id: str, *, now: Optional[datetime] = None) -> list[dict]:
    """The notes to bring into a new check-in: [] when memory is off for this patient, there are
    none, or the read failed (a memory problem must never stop the chat starting)."""
    now = now or _now()
    try:
        memories = PatientMemories.load(db, user_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("could not load patient memories: %s", type(e).__name__)
        return []
    if not memories.enabled:
        return []
    return [{"text": m.get("text") or "", "entities": list(m.get("entities") or []), "captured_at": m.get("captured_at")}
            for m in memories.recent(now) if m.get("text")]


def seed_token_map(token_map: TokenMap, memories: Iterable[dict]) -> None:
    """Teach a session's map the people the notes name, before anything is scrubbed."""
    for memory in memories or []:
        for entity in memory.get("entities") or []:
            cls, original = entity.get("cls"), entity.get("original")
            if cls in SEEDED_CLASSES and isinstance(original, str) and original.strip():
                token_map.token_for(cls, original)


def _ago(captured: Optional[datetime], now: datetime, language: str) -> str:
    days = max(0, (now - captured).days) if captured else None
    if language == "zh-HK":
        if days is None:
            return "之前"
        if days == 0:
            return "今日"
        if days == 1:
            return "琴日"
        if days < 14:
            return f"{days}日前"
        if days < 60:
            return f"{days // 7}個星期前"
        return f"{days // 30}個月前"
    if days is None:
        return "earlier"
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


def memory_messages(snapshot: Iterable[dict], now: datetime, language: str = "en") -> list[dict]:
    """The notes as one model-bound message in clear text, or [] when there are none. The caller
    scrubs it with the transcript; it is never marked trusted, so the leak check covers it."""
    lines = [f"- {m['text']} ({_ago(_parse(m.get('captured_at')), now, language)})"
             for m in snapshot or [] if m.get("text")]
    if not lines:
        return []
    header = RECALL_HEADER["zh-HK" if language == "zh-HK" else "en"]
    return [{"role": "developer", "content": header + "\n" + "\n".join(lines)}]


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
def plan_memory_ops(ops: Iterable[Any], ctx: ScrubContext, refs: dict[str, str], *, now=None,
                    language: Optional[str] = None) -> list[PlannedOp]:
    """Model operations -> screened operations. Refs the model did not get are ignored; a note that
    fails screening is dropped (an update that fails leaves the old note as it was)."""
    planned: list[PlannedOp] = []
    rejected = 0
    for op in list(ops or [])[:MAX_OPS]:
        kind = getattr(op, "op", None)
        if kind == "add":
            memory_id = None
        elif kind in ("update", "forget"):
            memory_id = refs.get((getattr(op, "ref", None) or "").strip())
            if memory_id is None:
                continue
        else:
            continue
        if kind == "forget":
            planned.append(PlannedOp("forget", memory_id=memory_id))
            continue
        screened = screen_memory(getattr(op, "text", None) or "", ctx, now=now, language=language)
        if screened is None:
            rejected += 1
            continue
        planned.append(PlannedOp(kind, memory_id=memory_id, memory=screened,
                                 category=getattr(op, "category", None) or "other",
                                 durability=getattr(op, "durability", None) or "long"))
    if rejected:
        logger.info("memory screening dropped %d proposed notes", rejected)
    return planned


def apply_memory_ops(memories: PatientMemories, planned: Iterable[PlannedOp], *, source_session_id: Optional[str],
                     now: datetime) -> None:
    if not memories.enabled:   # switched off since the extraction started
        return
    for op in planned:
        if op.op == "forget":
            memories.remove(op.memory_id)
        elif op.op == "update":
            memories.update(op, source_session_id=source_session_id, now=now)
        else:
            memories.add(op, source_session_id=source_session_id, now=now)


def _load_extraction_prompt(language: str) -> str:
    filename = "memory_prompt_canto.txt" if language == "zh-HK" else "memory_prompt_eng.txt"
    return load_prompt_template(
        filename,
        "Above is today's check-in and the notes already kept about this patient. Return add, update or "
        "forget operations for facts the patient volunteered about their life. Never keep addresses, "
        "places, hospitals, workplaces, contact details, ID numbers or birthdays. Return no operations "
        "if nothing new was shared.",
    )


async def extract_session_memories(db, session: dict, user: dict) -> None:
    """Background capture for a finished check-in. Every failure is logged by type and swallowed:
    this runs beside the clinical analysis and must never affect it."""
    session_id = session.get("session_id") or ""
    username = user["username"]
    pref, sref = patient_ref(username), session_ref(session_id)
    language = session.get("language", "en")
    now = _now()
    try:
        existing = PatientMemories.load(db, username)
        if not existing.enabled:
            return
        ctx = build_scrub_context(db, user, session.get("token_map"))
        current = existing.recent(now, limit=MAX_MEMORIES)
        seed_token_map(ctx.token_map, current)
        refs = {f"m{i}": m["memory_id"] for i, m in enumerate(current, start=1) if m.get("memory_id")}
        lang = "zh-HK" if language == "zh-HK" else "en"
        listing = "\n".join(f"{ref}: {m.get('text', '')}" for ref, m in zip(refs, current)) or NO_EXISTING[lang]
        outbound = ctx.scrub_messages(
            model_messages(session.get("conversation_history") or [])
            + [{"role": "user", "content": f"{EXISTING_HEADER[lang]}\n{listing}"}],
            now=now, language=language,
        )
        result = await get_gateway().complete(InferenceRequest(
            task="memory_extraction",
            messages=outbound + [{"role": "user", "content": _load_extraction_prompt(language)}],
            instructions=MEMORY_INSTRUCTIONS,
            schema=MemoryExtractionOutput,
            language=language,
            temperature=0.2,
            metadata=task_metadata(pref, sref, "florence"),
            scrubbed=True,
            known_identifiers=ctx.leak_forms(),
            scrub_report=scrub_summary_from_messages(outbound, ctx.ner.name),
            trusted_tail=1,   # the appended turn is the static prompt template
        ))
        planned = plan_memory_ops(result.parsed.ops, ctx, refs, now=now, language=language)
        if not planned:
            return
        mutate_memories(db, username, lambda m: apply_memory_ops(m, planned, source_session_id=session_id, now=now),
                        now=now)
        logger.info("memory capture session_ref=%s ops=%d", sref, len(planned))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - includes InferenceRefused / ScrubError: nothing is stored
        logger.warning("memory capture skipped for session_ref=%s: %s", sref, type(e).__name__)
