"""What Florence remembers, for the patient it is about: see it, delete it, switch it off.

Patient-only. Everything is keyed on the authenticated username, so another patient's memory id is
simply not found. The entities stored with a note (the names seeded into the scrubber) never leave
the server.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .login import get_db, get_user
from .florence_memory import PatientMemories, mutate_memories

memoriesrouter = APIRouter(prefix="/memories", tags=["memories"])


class MemorySettings(BaseModel):
    enabled: bool


def require_patient(user=Depends(get_user)):
    if user.get("isDoctor"):
        raise HTTPException(status_code=403, detail="Patient access only")
    return user


def _public(memories: PatientMemories) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "enabled": memories.enabled,
        "memories": [
            {"memory_id": m.get("memory_id"), "text": m.get("text"), "category": m.get("category"),
             "captured_at": m.get("captured_at")}
            for m in memories.recent(now, limit=None)
        ],
    }


def _write(db, username: str, change) -> PatientMemories:
    saved = mutate_memories(db, username, change)
    if saved is None:
        raise HTTPException(status_code=409, detail="Please try again")
    return saved


@memoriesrouter.get("/me")
def list_my_memories(user=Depends(require_patient), db=Depends(get_db)):
    return _public(PatientMemories.load(db, user["username"]))


@memoriesrouter.delete("/me")
def clear_my_memories(user=Depends(require_patient), db=Depends(get_db)):
    def clear(memories):
        memories.memories = []
    return _public(_write(db, user["username"], clear))


@memoriesrouter.delete("/{memory_id}")
def delete_my_memory(memory_id: str, user=Depends(require_patient), db=Depends(get_db)):
    if PatientMemories.load(db, user["username"]).find(memory_id) is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _public(_write(db, user["username"], lambda memories: memories.remove(memory_id)))


@memoriesrouter.put("/settings")
def update_memory_settings(settings: MemorySettings, user=Depends(require_patient), db=Depends(get_db)):
    def apply(memories):
        memories.enabled = settings.enabled
    return _public(_write(db, user["username"], apply))
