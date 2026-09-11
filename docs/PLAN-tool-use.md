# Plan: Symptom coverage tools in Florence chat

**Revision 3 — 2026-09-10.** Phases 1-4 are implemented (see §10). Scoped down from rev 1. Tool use is limited to **completing and
covering the five symptoms inside the chat**. No history reads, no trend answers, nothing that
takes data out of Mongo and puts it in the model's context. Written against `34f63098`.

## Scope

> Two tools, registered on `chat_turn` only. Arguments flow model → us. The return value is a
> coverage ack we author. Nothing leaves the building that wasn't already in the prompt.

Explicitly **out**: `get_recent_checkins`, `get_symptom_trend`, patient-facing trend Q&A, and every
other read. They were families B and C in rev 1; they are deferred whole, not phased.

## 0. What this replaces

Florence must assess fatigue, appetite, nausea, cough and discomfort. Today that requirement lives
as prose in `prompt_eng.txt` — "Assess EACH symptom INDIVIDUALLY" — and structure is recovered
*after* the conversation by a second model pass (`florence_assessment.py`) reading the whole
transcript. Two consequences:

- **Coverage is unenforceable.** Nothing knows mid-conversation that nausea frequency was skipped,
  so nothing can ask for it while the patient is still there.
- **Severity is reconstructed, not captured.** The extraction infers a 1–5 rating from a transcript
  in which no rating was ever stated. That is a large part of the over-escalation problem.

## 1. Why tools rather than a per-turn schema

Worth stating, because a `TurnOutput.update` field would also capture structure: **with a tool,
silence is the default.** Most turns are small talk and transitions, and forcing an `update` field
on every one of them invites the model to fill it with noise or nulls. A tool is called when there
is something to record and not otherwise — and a patient who volunteers two symptoms in one breath
produces two calls, which a single-value field cannot express.

## 2. The two tools

```
record_symptom(symptom, present, severity, frequency, evidence)
note_unassessable(symptom, reason)
```

`symptom` is a closed enum of the five. `severity` and `frequency` are 1–5 or null. `evidence` is a
short quote from the patient turn the rating came from — the field that makes a rating auditable
instead of asserted.

**The return value is the coverage state:**

```json
{"recorded": "fatigue", "remaining": ["appetite", "nausea", "cough", "discomfort"]}
```

This is the whole design in one line. The ack tells Florence what is still outstanding, so coverage
needs no injected system notes and no prompt bookkeeping — it is a fact she is handed every time
she records something. It is also a fixed vocabulary of five English symptom keys: **no patient
content, in either direction, in the tool result.**

## 3. What must change in the scrub path

Rev 1 framed this as a fail-open PHI hole. With reads out of scope that framing is overstated, and
the honest version is narrower:

- **Correctness, and it is blocking.** `ScrubContext.scrub_messages` keeps only `role` and `content`
  <span>(`scrubber.py:225`)</span> and coerces anything else to `""`. Responses API tool items carry
  neither key, so on the next turn's replay **the model loses its own call history** — it cannot see
  what it already recorded, and will re-ask or double-record. Tool items must survive the replay.
- **Defence in depth, and it is cheap.** `leak_check` reads only `msg["content"]`
  <span>(`scrubber.py:274`)</span>, so a tool item contributes nothing to the check. For these two
  tools the exposure is small — `evidence` is model-generated text derived from already-scrubbed
  input, so it should already carry `[PERSON_n]` tokens rather than names — but "should" is not what
  a fail-closed system runs on. Extend `_content_strings` to read `arguments` and `output` while we
  are in here.

The real fail-open risk lives with read tools, which are now out of scope. This work still closes
the door before anyone opens it.

## 4. Policy and audit

```yaml
tools:
  record_symptom:    { requires: [], discloses: none }
  note_unassessable: { requires: [], discloses: none }
```

`Router.decide_tool(name)` returns the existing `Decision`, so a refused tool takes the
`InferenceRefused` path already wired through every call site. `AuditEvent` gains `tool` (the
declared name, never the arguments) and `hop` (loop index), so a refused call is visible in the
trail rather than invisible inside a turn that audits as allowed. The registry is the allowlist: an
unregistered name is refused and audited, never executed.

## 5. Coverage does not block the patient

Rev 1 said `finish_session` should refuse until all five are recorded. **That was wrong.**
`finish_session` saves the conversation immediately and runs assessment and triage in the background
<span>(`florence.py:388`)</span>; it is the patient ending their check-in. Refusing it would trap
someone in a chat because the model didn't ask about cough.

What coverage actually buys:

- **During the chat**, the ack's `remaining` list lets Florence close her own gaps naturally, and
  gives the wrap-up a condition: don't sign off while something is outstanding.
- **At the end**, the record carries `coverage: {recorded: [...], unassessable: [...], missing:
  [...]}` so the clinician sees an honest account of what was and wasn't covered, rather than an
  assessment that silently guessed.
- **In the eval**, coverage becomes a number to move.

## 6. Reconciling with the existing assessment pass

Do not delete `florence_assessment.py`, and do not switch the clinical record over on day one.
**Run both and compare first.** For a defined period the tool captures ratings while the existing
extraction runs unchanged; the record keeps the extraction's output, and both are stored so they can
be diffed offline.

Two things come out of that shadow period: a measured disagreement rate, and the answer to the
question this whole plan is really about — *does capturing severity at the moment the patient says
it produce lower ratings than reconstructing it afterwards?* If it does, that is the over-escalation
fix, with evidence. Only then does the recorded structure become the source of truth, with the
extraction demoted to filling gaps the tools left.

## 6a. Cost tiering: cheap conversation, expensive triage

Chat runs on every patient message with someone watching a typing indicator. Triage runs once per
session in the background where nobody is waiting. Those want different models *and* different
effort, and before this the split was three magic strings in three call sites with nothing holding
it in place.

**One table, in `app/inference/gateway.py`:**

| Task | Effort | Tool hops | Model env var |
|---|---|---|---|
| `chat_turn` | `minimal` | 1 | `OPENAI_CHAT_MODEL` |
| `symptom_assessment` | `low` | 0 | `OPENAI_ASSESSMENT_MODEL` |
| `triage` | `high` | 0 | `OPENAI_TRIAGE_MODEL` |
| `pii_detect` | `minimal` | 0 | — |

`InferenceRequest` defaults `effort`, `max_tool_hops` and `text_ends_turn` from the task's profile,
so a call site now has to override *visibly* rather than by copying a neighbour. `tests/unit/
test_task_cost_profiles.py` fails if chat drifts off a cheap effort, if a background task is handed
tools, or if the chat hop cap rises.

**The optimisation that pays for tool use.** A tool hop is normally a second round trip. But the
coverage tools are writes: their result is an ack the model has no reason to read. So when a hop
comes back with *both* an answer and tool calls, the gateway runs the tools and keeps that answer
instead of calling again (`text_ends_turn`). The results are still appended and replayed next turn,
so `remaining` reaches Florence — just not mid-sentence. Both prompts now tell her to record and
reply in the same response, which makes that the common path.

Net effect: **a recording turn costs the same single model call as any other turn.** The second call
only happens when the model records without answering, and the cap means a chat turn can never
exceed two.

### Measured against the live deployment, 2026-09-10

Probed directly rather than assumed. Three findings, two of which changed the code:

**1. `text_ends_turn` does not fire on `gpt-5.6-sol`.** Over a four-turn conversation the model
emitted the tool call *alone* and answered on the following hop, every time — `together=False` on
both recording turns, including after the prompt was changed to ask for both in one response. So a
recording turn costs two model calls and the measured rate is **1.50 calls per turn** (2 on a
recording turn, 1 otherwise). The optimisation stays, because it is correct and costs nothing when
it does not fire, but it buys nothing on this model. **Do not quote it as a saving.**

**2. `gpt-5.6-sol` is the only deployment on the resource.** `models.list()` returns 423 entries, but
that is the Azure *catalogue*, not what is deployed: `gpt-5.6-luna`, `gpt-5-mini`, `gpt-5.4-mini`,
`gpt-4o-mini` and every other candidate return `DeploymentNotFound`.

**Running everything on one deployment is an accepted configuration, not a gap to fix.** What has to
hold is that a second deployment is *config, never code*, so the split can be turned on the day one
exists:

- `OPENAI_CHAT_MODEL` / `OPENAI_ASSESSMENT_MODEL` / `OPENAI_TRIAGE_MODEL` set the model per task;
  unset falls back to `OPENAI_MODEL`. Documented in `.env.example` and the README.
- `GET /health` reports the live picture under `cost`: each task's model, effort, hop cap and tier,
  plus `model_tiering: shared | split`.
- `test_the_override_actually_reaches_the_provider` asserts the override lands on the provider call
  rather than merely sitting in a dict, so the seam cannot rot while it is unused.
- Deliberately **not** a startup warning. A warning about a configuration we have accepted just
  trains people to ignore warnings; it is a fact on /health instead.

**3. Chat runs at `low`.** The deployment rejects `minimal` and reports
`none / low / medium / high / max / xhigh`, so asking for `minimal` costs a failed round trip per
process to learn that. `low` is asked for directly: still the cheap tier, well below triage's
`high`, and unlike `none` it leaves the turn a reasoning budget — the same call has to judge
severity, keep to one question at a time and decide whether to record. Probed at both: identical
cost (1.50 calls/turn) and identical coverage after four turns; at `low` the first symptom is
recorded a turn earlier. `choose_effort` still negotiates for a future model that lacks `low`.

## 7. Latency

A tool call is a second model round trip on the one path where the patient is watching a typing
indicator. Bounded here by scope:

- At most one or two records per turn, and most turns record nothing.
- **The executor is a pure in-memory state update.** No Mongo round trip inside the hop; coverage
  state persists with the ordinary end-of-turn session save.
- The ack is a few dozen bytes with no content to reason about.
- Hop cap of 3, then force a text answer.

If measured p95 on a recording turn is unacceptable, the fallback is the per-turn schema field from
`PLAN-guided-replies.md` — same captured data, no second round trip, at the cost of forcing the
field on every turn.

## 8. Failure modes

| Event | Behaviour |
|---|---|
| Tool refused by policy | `InferenceRefused` → existing `scripted_fallback` |
| Malformed arguments | Typed error back to the model; counts as a hop |
| Unknown tool name | Never executed; refuse and audit |
| Symptom outside the enum | Rejected by the executor, returned as a typed error |
| Same symptom recorded twice | Last write wins; both kept in the audit trail |
| Hop cap reached | Force a text answer; audit `hop=cap` |
| Leak check fails on tool arguments | Refuse the turn, fail closed |
| Scrub raises on a tool item | `ScrubError` → the existing path |

## 9. Evaluation

- **Coverage.** Percentage of sessions where all five symptoms carry a `record_symptom` or a
  `note_unassessable`. Binary per symptom.
- **Evidence quality.** Percentage of recorded severities whose `evidence` quote actually appears in
  the patient's turns. Catches invented ratings directly.
- **Agreement with the extraction** during the shadow period, per symptom, plus the signed
  difference — the over-escalation measurement.
- **Tool-call precision.** Well-formed arguments first time; records fired on small talk.
- **Latency.** p50/p95 of `chat_turn` on turns with a hop vs turns without.
- **Replay integrity.** A session with several records replays with its tool history intact — the
  regression test for §3.

## 10. Phases

| Phase | Status | Scope |
|---|---|---|
| **1. Tool items survive the scrub** | **done** | `scrub_messages` preserves tool items and scrubs their payloads; `_content_strings` reads `arguments` and `output`. `tests/unit/test_scrub_tool_items.py` |
| **2. Loop, policy, audit** | **done** | `tools`/`tool_choice`/`max_tool_hops`/`scrub_tool_output` on `InferenceRequest`, bounded loop in `InferenceGateway`, `tools:` block in the policy, `tool` + `hop` on `AuditEvent`. `tests/unit/test_inference_tool_loop.py` |
| **3. The two tools, shadow mode** | **done** | `app/florence_tools.py`; registered on `chat_turn`; the extraction still owns `structured_assessment`. `tests/integration/test_florence_coverage_tools.py` |
| **4. Coverage surfaces** | **done** | Tool results carry `remaining`; prompts tell Florence not to sign off while symptoms are outstanding; `symptom_coverage` block on the assessment record. |
| **5. Tools own the record** | **not built** | Needs the shadow-period disagreement rate first — that measurement does not exist yet. |

### What phase 5 is waiting on

`symptom_coverage` and `structured_assessment` now sit side by side on every assessment record. Once
enough sessions have accumulated, diff them per symptom and look at the *signed* difference. If
recorded severities run consistently lower than extracted ones, that is the over-escalation fix
measured rather than assumed, and the record can move onto tool data with the extraction demoted to
filling gaps.

### Storage notes for whoever picks this up

- Tool items live on the session as `tool_history` (`[{after, items}]`), **not** in
  `conversation_history` — that list is rendered in the clinician view and counted in analytics, and
  every consumer assumes each entry has a `role`. `model_history()` splices them back in for the
  model. `after` is an index into the plain transcript, not the spliced one.
- Coverage lives on the session as `symptom_state` and is rebuilt each turn, so a tool hop never
  costs a database round trip.
- The assessment and triage passes still see the plain transcript with no tool items, which is what
  keeps shadow mode honest.

The loop lives in `InferenceGateway.complete()`, not in `florence.py` — if the call site runs it,
every future call site re-implements gating, leak-checking and auditing, and one of them will get it
wrong.

## 11. Decisions

- **Two tools, not three.** No `finish_assessment` tool: the patient ends the session, and the ack's
  `remaining` list already tells Florence when she is done.
- **Tools over a per-turn schema field**, because silence is the default and multi-record turns are
  natural. The schema field remains the documented fallback if hop latency proves unacceptable.
- **The tool result is the coverage state.** No injected system notes, no prompt bookkeeping.
- **Coverage never blocks the patient.** It shapes Florence's wrap-up and it is reported honestly on
  the record; it does not gate `finish_session`.
- **Shadow before switchover.** Changing what goes into a clinical record needs a measured
  disagreement rate first.
- **Reads stay out.** Deferred whole. Revisit only once recorded coverage is trustworthy.
