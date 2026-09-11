# Plan: Florence agent memory

**Revision 1 — 2026-09-10.** Built behind `FLORENCE_MEMORY` (default `off`).

## Scope

> Florence remembers what a patient volunteers across check-ins - a pet's name, a grandchild who
> visits, what they had for dinner - and never stores an address, a place, a hospital, a contact
> detail, an ID number or a birthday. The PHI scrubber, not the model, decides which is which.

## 0. What this replaces

Every check-in started from nothing. Sessions TTL out after 30 minutes and the session `TokenMap`
goes with them. `prompt_eng.txt` told Florence to "never collect personal details (names, where they
live, who exactly visits, dates)". That rule is narrowed to what actually identifies someone: Florence
still never *asks* for names, addresses, IDs, contact details or exact dates, but may talk about the
everyday things a patient offers.

## 1. Storage

One document per patient in `patient_memories`, written whole behind an optimistic `version`:

```json
{"user_id": "demo", "enabled": true, "version": 7, "updated_at": "…",
 "memories": [{"memory_id": "mem_8f3a1c", "text": "Has a dog called Biscuit", "category": "pet",
               "durability": "long", "entities": [], "source_session_id": "…",
               "captured_at": "…", "updated_at": "…", "expires_at": "…"}]}
```

- Clear text at rest, like assessment records. `entities` holds the PERSON / OCCUPATION originals a
  note names, for seeding the next session's map (§4).
- `durability`: `short` = 7 days (a meal, this week's plans), `long` = 365 days. A TTL index cannot
  expire array items, so expiry is applied on read and pruned on every write. Capped at 40 notes.
- A separate collection because no clinician or admin path reads it. On `users`, the session or the
  assessment record it would leak through `/admin/users` or `/doctor/patient/{id}/assessment/{sid}`.
- `DELETE /admin/users/{u}` cascades to it. Index: unique `user_id`.

## 2. Capture

`extract_session_memories` runs from `finish_session` as **its own background task**, not inside
`analyse_transcript`'s gather, so a refused or failed extraction can never turn the clinical record
into `pending_clinician_review`.

- Input: the scrubbed transcript, the existing notes (as `m1…mN`, entities seeded first, scrubbed),
  then the static template (`memory_prompt_eng.txt` / `_canto`) as the one trusted tail message.
- Output: `MemoryExtractionOutput` - `add` / `update` / `forget` ops. Refs the model was not given are
  ignored; an update that fails screening leaves the old note untouched.

| Task | Effort | Tool hops | Model env var | on_refuse |
|---|---|---|---|---|
| `memory_extraction` | low | 0 | `OPENAI_MEMORY_MODEL` (falls back to `OPENAI_MODEL`) | skip |

## 3. Screening: the scrubber as the filter

`MEMORY_BLOCKED_CLASSES = {ADDRESS, PHONE, EMAIL, HANDLE, URL, ID, DOB, PLACE, ORG, FACILITY}`.
Allowed: PERSON, DATE, AGE, OCCUPATION.

1. A blocked placeholder in the model's output rejects the note.
2. `[PERSON_1]` becomes "the patient"; everything else is re-identified from the session map. An
   unresolvable placeholder (the model invented one) rejects the note.
3. The clear text is scrubbed again on a *copy* of the session map. Any blocked class rejects it -
   this is what catches "Lives at 12 Nathan Road" written out with no placeholder at all.

Rejections are logged as a count, never content.

## 4. Recall

- `start_session` (flag `on`) snapshots the 20 most recent active notes onto `session.memory_context`
  and seeds their entities into the session `TokenMap` before anything is scrubbed. A name with no cue
  word ("Mei Ling came over") is otherwise only caught once the map already knows it; seeded, it is
  tokenised in the notes and in everything the patient says, and joins `leak_forms()`.
- The notes reach the model as one `developer` message ahead of the transcript on every `chat_turn`,
  scrubbed with it and never trusted, so the leak check covers them. Ages are relative ("8 days ago").
- **Not** in `symptom_assessment` or `triage`: old notes must never move an alert level.
- `memory_context` is never returned: `create_session_response_data` and `create_assessment_record`
  copy explicit field lists. Pet names are not scrubber-detectable and go out in clear, by decision.

## 5. Patient controls

`app/memories.py` (patient-only; doctors get 403): `GET /memories/me`, `DELETE /memories/{id}`,
`DELETE /memories/me`, `PUT /memories/settings {enabled}`. Entities are never returned. Frontend:
Settings → Privacy & Data → "What Florence remembers" (`pages/patient/settings/florence_memory.jsx`).

## 6. Failure modes

| Event | Behaviour |
|---|---|
| Extraction refused / provider error / bad output | Logged by type; nothing stored; clinical record unaffected |
| Two check-ins finish together | Version conflict → reload and re-apply once, then drop with a log line |
| Memory store unreadable at session start | Chat starts without notes |
| Patient switches memory off mid-extraction | Ops are not applied |
| Note names a blocked class | Dropped at screening |

## 7. Evaluation

- `tests/unit/test_memory_screen.py` - kept vs blocked per class, invented placeholders, seeding,
  storage (versioning, races, expiry, cap).
- `tests/integration/test_florence_memory.py` - end to end: capture in clear, extraction sees only
  placeholders, address never stored, refusal and crash leave triage `completed`, recall with names
  tokenised, assessment/triage never see notes, `capture` / `off` / patient opt-out, endpoints, cascade.
- Not measured yet: extraction precision on real transcripts. Run a week in `capture` and read the
  stored notes before switching to `on`.

## 8. Phases

| Phase | Status | Scope |
|---|---|---|
| **1. Capture** | **built** | storage, extraction, screening, flag, cascade |
| **2. Recall** | **built** | seeding, developer message, prompt changes |
| **3. Patient controls** | **built** | `/memories/*`, settings page |
| **4. Rollout** | not started | `capture` in prod, review notes, then `on` |

## 9. Decisions

- **The scrubber is the filter.** A prompt saying "don't remember addresses" is a wish; a blocked
  class is a rule with tests.
- **One document per patient.** Notes are always read and written as a set.
- **Extraction after the check-in, not a mid-chat tool.** No added latency on the turn the patient is
  waiting for, and Florence's attention stays on the five symptoms.
- **Its own task.** Memory must never be able to degrade the clinical record.
- **Recall never reaches assessment or triage.**
- **Default off.** Render auto-deploys `main`.
