# Plan: Guided replies + in-conversation symptom capture

**Revision 1 — 2026-09-10.** Replaces the client-side keyword heuristic that picks Florence's
"Quick responses" with a model-emitted field, and uses the same per-turn structured output to
capture symptom ratings while the conversation is still running. Written against the post-scrubber
codebase (`34f63098`), so every model-bound string here goes through `ScrubContext` and every
model-returned string comes back through `reidentify_obj`.

## Headline claim this plan makes true

> The answers Florence offers a patient are written by Florence, in the patient's language, for the
> question she just asked — and the severity she records is traceable to what the patient said,
> not to the button she offered them.

## 0. Where we are

**Quick responses are wrong most of the time.** `getQuickResponseOptions()`
(`frontend_ui_revamp/src/pages/patient/florence_chat.jsx:34`) lowercases the last bot message and
walks a five-branch cascade of hardcoded English substring tests. Verified failures:

| Florence says | Matches | Buttons shown |
|---|---|---|
| "What have you been spending your time doing lately?" | `"have you"` | Yes / No / Not sure / Sometimes |
| "How much rice do you normally eat?" | `"do you"`, `"no"` | Yes / No / Not sure / Sometimes |
| "I know that can be frustrating." | `"no"` (inside *know*) | Yes / No / Not sure / Sometimes |
| Any Cantonese message | nothing | the 6 default symptom buttons, always |

Five root causes:

1. `includes("have you")` / `includes("do you")` fire on open-ended questions — the opposite of
   what the branch assumes. `app/prompt_eng.txt` explicitly forbids Florence from asking yes/no
   questions, so this branch is wrong essentially every time it fires.
2. `includes("yes") || includes("no")` — "no" is a substring of *not, now, nothing, know, notice,
   normally, annoying, diagnosis*.
3. The yes/no branch has the broadest triggers and sits third, ahead of duration and symptom.
4. The keyword tables are English-only. zh-HK never matches any branch: the locale files translate
   the *labels*, not the matching.
5. The option sets don't match the product. Florence assesses fatigue, appetite, nausea, cough,
   discomfort; the buttons offer headache, fever, and "Medication questions" (a coming-soon stub
   with no backend). The 1–5 severity buttons — the useful ones — almost never fire, because the
   prompt tells Florence not to make patients self-rate, and they send a contextless `"1"`.

**This is a data-quality bug, not a cosmetic one.** Offering Yes/No under an open question teaches
an elderly patient that yes/no is the expected answer, which starves `florence_assessment.py` of
the detail it needs and feeds the known over-escalation problem.

**What already exists and makes this cheap.**

- The gateway dispatches `provider.parse()` whenever `InferenceRequest.schema` is set
  (`app/inference/gateway.py:187`); the provider passes it as `text_format` to the Responses API
  (`app/inference/providers.py:110-117`). Structured output on `chat_turn` is a **call-site change
  only** — no gateway or provider work.
- `reidentify_obj` (`app/inference/scrub/reidentify.py:65`) already walks dicts, lists and pydantic
  models recursively and returns an unresolved-placeholder count. One call re-identifies a whole
  `TurnOutput`, suggestion strings included, and feeds `_note_unresolved` exactly as today.
- The turn already scrubs outbound and re-identifies inbound (`app/florence.py:279-288`).

**What's missing.** `SendMessageRequest` (`app/florence.py:41-43`) carries only `session_id` and
`message`, and `handleQuickResponse` (`florence_chat.jsx:262`) stuffs the button label into the
input box and calls `sendMessage()` — so a *tapped* answer is indistinguishable from a *typed* one
in the transcript, in the assessment, and in triage.

## 1. Design: one call, two fields, two governance regimes

```python
class TurnOutput(BaseModel):
    reply: str                        # what the patient sees
    suggested_replies: list[str] = [] # UI affordance      — tune freely
    update: SymptomUpdate | None = None  # clinical write  — eval-gated, audited
```

**One call**, because all three come from the same latent state ("which symptom am I on, what did
the patient just say, what am I about to ask"). Two calls would double latency on the one path
where the patient is actively waiting, and would let the suggester drift from the question.

**Separate governance**, because the two fields have different blast radii:

| | `suggested_replies` | `update` |
|---|---|---|
| Consumer | the chat UI, this turn only | triage, clinician dashboard, the record |
| Failure mode | render nothing | lose or corrupt a clinical rating |
| Change cadence | weekly copy/tone tweaks | frozen, regression-tested |
| Prompt location | appended block, versioned separately | the clinical prompt body |
| On malformed | default `[]`, turn still succeeds | turn fails loudly |

Concretely: `suggested_replies` is optional with a default so a bad suggestion can never
fail-validate the turn and lose the clinical write; and suggestion copy changes must not require
re-running the extraction eval, which means the suggestion rules live in their own appended prompt
block, not woven into `prompt_eng.txt`'s clinical body.

## 2. The bias loop, and the three controls on it

If Florence suggests "Quite tired, most days", the patient taps it, and Florence then records
severity from that transcript, the model is grading its own multiple-choice question. That is the
existing over-escalation loop, but tighter and invisible. Three controls:

1. **Span the range or emit nothing.** Four options that all imply "yes, symptomatic" is a leading
   question with buttons. The prompt rule is: offer the full spread of plausible answers
   (none → mild → moderate → marked), or return `[]`. Never 4 variants of the same answer.
2. **Record how the answer arrived.** Add `input_source: "tap" | "type" | "voice"` to
   `SendMessageRequest`, persist it on the message record, and pass it through to the assessment
   prompt so a rating derived only from taps can be discounted. The frontend already knows this at
   `florence_chat.jsx:262` and currently throws it away.
3. **Evidence is the patient's words.** When `update` carries a severity, its `evidence` field must
   quote the patient turn it came from. A tapped suggestion is weaker evidence than a volunteered
   sentence, and the eval in §5 measures whether the two diverge.

## 3. Backend changes

**`app/florence_utils.py`** — add the schemas next to `SymptomAssessmentOutput`:
`TurnOutput` as above; `SymptomUpdate{symptom: Literal[fatigue|appetite|nausea|cough|discomfort],
present: bool, severity: int|None, frequency: int|None, evidence: str}`. Keep `suggested_replies`
capped (max 4, max ~30 chars each) — long buttons are unusable on the phone layout in the
screenshot.

**`app/florence_ai.py`** — `_complete()` gains `schema=TurnOutput`; `start_conversation` and
`process_message` return `{"response", "suggested_replies", "update", "conversation_state",
"audit_id"}` built from `result.parsed` instead of `result.text`. Both paths, because **the opening
turn is where the screenshot bug shows**.

*Risk to handle here:* `parse()` raises `RuntimeError("model returned no parsed output")` on refusal
or truncation (`providers.py:120`), where `chat()` previously returned text. That must land in the
existing `handle_ai_response_error` path so the turn degrades to a scripted reply rather than 500ing.

**`app/florence.py`** — in `start_florence_session` and `send_message_to_florence_endpoint`, replace
the `reidentify_reply(...)` call with `reidentify_obj(...)` over the whole payload, keeping the
`_note_unresolved` bookkeeping. Persist `suggested_replies` on the assistant message and `update`
into a new session field `symptom_state`. Return `suggested_replies` in the endpoint response.
Add `input_source` to `SendMessageRequest` (default `"type"`, so old clients keep working).

**Prompt files** — append a delimited `## Reply suggestions` block to `prompt_eng.txt` and
`prompt_canto.txt` carrying: same language as the reply, 0–4 options, short, span the range, never
suggest an answer the patient hasn't been given the chance to form, and emit `[]` for small talk and
for anything open-ended where a button would narrow the answer.

**Policy** — no change. `chat_turn` stays `provider: openai, requires: [dpa_ok, scrubbed]`,
`on_refuse: scripted_fallback`; scripted fallbacks carry no suggestions, which renders as no buttons.

## 4. Frontend changes

- `src/utils/api.js` — pass `input_source` through `sendMessage`.
- `florence_chat.jsx` — **delete `getQuickResponseOptions()` and `getDefaultOptions()` (lines 34–113,
  ~80 lines)**. Store `suggestions` on the bot message built at line 207. Render from the newest bot
  message; render nothing when the array is empty. `handleQuickResponse` sends `input_source: "tap"`.
- `src/locales/florence/{en,zh}.json` — remove the ~25 now-dead `quick_responses_*` option keys, keep
  `quick_responses_title`.
- `src/__tests__/mocks/handlers.js` — the `/florence/send_message` mock returns `suggested_replies`.

## 5. Evaluation

Add to the existing eval harness under `tests/eval`:

- **Suggestion relevance.** A fixture set of ~40 (last Florence turn → expected answer shape) pairs
  across both languages. Score: does the option set answer *that* question, and does it span the
  range? This is the regression gate for suggestion prompt changes.
- **Coverage.** % of sessions where all five symptoms carry an `update` with non-empty evidence.
  Binary per symptom, so it's a real number to move.
- **Bias check — the one that matters.** Compare the severity distribution of tapped answers vs
  typed answers on the same symptom. If tapped skews systematically higher, the suggestions are
  leading and the spread rule isn't working.
- **Latency budget.** Chat is the only interactive model call. Measure p50/p95 of `chat_turn` before
  and after the schema; if `parse()` costs more than ~300 ms over `chat()`, shrink the schema before
  shipping. Note `effort` is already `"minimal"` and the provider negotiates it down
  (`_supported_efforts_from_error`).

## 6. Phases

| Phase | Scope | Risk |
|---|---|---|
| **0. Stop the bleeding** (frontend only) | Delete the yes/no branch; render nothing when no branch confidently matches; default set becomes the five real symptoms. No backend, no deploy coupling. | none — strictly fewer wrong buttons |
| **1. Model-emitted suggestions** | `TurnOutput{reply, suggested_replies}` on both turns, `reidentify_obj`, frontend renders, heuristic deleted. | latency on the interactive path; `parse()` failure mode |
| **2. Input provenance** | `input_source` end to end, persisted on the message. | none, additive |
| **3. Symptom capture** | `update` field, `symptom_state` on the session, `finish_session` gated on coverage (or an explicit unassessable reason). | changes what the record contains — needs the coverage eval green first |
| **4. Tools, if warranted** | If more write kinds appear (flag urgent, finish session), migrate `update` to a real tool call. `suggested_replies` **stays** in the response schema. | deferred; not needed for one write kind |

Phase 0 ships independently and is the right move if a demo is near. Phases 1–3 are one coherent
piece of work; 3 should not ship without its eval.

## 7. Decisions

- **One call, not two.** Latency on the interactive path, and consistency by construction.
- **Structured output now, tools later.** A tool is the right shape for a write the model makes
  zero-or-many times per turn. Suggestions are exactly-one-per-turn — that's what a schema is for.
  Phase 4 splits them along that line; nothing before then needs tool-calling plumbing.
- **Suggestions never required.** Optional field, `[]` default, empty renders nothing. No suggestion
  failure may cost a clinical write or a reply.
- **No new PHI surface.** Everything added here is model *output*, re-identified on the way back
  through the existing `token_map`. The plan adds nothing to what leaves the building.

## 8. Out of scope

Voice input provenance beyond the `input_source` tag; the questionnaire tab (shares no code with
this path); patient-facing history retrieval and trend Q&A (both should wait until `symptom_state`
from Phase 3 is trustworthy — they read what this writes).
