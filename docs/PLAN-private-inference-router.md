# Plan: PHI-safe inference gateway (scrub → route → compliant inference)

Goal: make this statement true and defensible:

> A router directs private queries to local MedGemma and reasoning tasks to OpenAI; every chat
> surface strips PII/PHI before routing, and all inference paths are HIPAA-compliant.

## 0. Where we are (gaps this plan closes)

| Area | Today | Problem |
|---|---|---|
| LLM calls | 3 direct `AsyncOpenAI` calls (`florence_ai`, `florence_assessment`, `florence_triage`) | No single choke point; no routing |
| PHI in prompts | Full transcript + `user_info` (name, DOB, email) go to OpenAI | PHI leaves the VPC with no BAA, no minimisation |
| Logging | `print()` of full transcripts, function args, `api_key[:10]` | PHI + secrets in stdout/Render logs |
| Sessions | `active_sessions` dict in-process | Lost on restart; no expiry audit; not encrypted |
| Storage | Mongo Atlas, plaintext `conversation_history`, `user_info` copied into every assessment | No field-level encryption, over-retention |
| Vendors | OpenAI, Atlas, Render, Twilio/SendGrid, Google Calendar | No BAAs on file |
| Auth | JWT in `localStorage`, 60-min expiry, `allow_origins=["*"]` | XSS-exfiltrable token, permissive CORS |

## 1. Target architecture

```
Florence chat ─┐
Questionnaire ─┼─► InferenceGateway.complete(request)
Future surfaces┘        │
                        ├─ 1. Scrubber  (deterministic + NER + reversible token map)
                        ├─ 2. Router    (policy: task × sensitivity × availability)
                        ├─ 3. Provider  ─► LocalMedGemmaProvider  (vLLM/Ollama, in-VPC)
                        │               └► OpenAIProvider          (BAA + zero-data-retention)
                        ├─ 4. Re-identify outputs (token map, server-side only)
                        └─ 5. Audit event (no content): who, task, provider, scrub stats, tokens, latency
```

### 1.1 `app/inference/gateway.py`
One entry point: `await gateway.complete(InferenceRequest)` where the request carries
`task` (`chat_turn` | `symptom_assessment` | `triage` | `pii_detect`), `messages`, optional
`schema` (Pydantic), `language`, `patient_ref` (opaque id, never the username), `session_id`.
`florence_ai`, `florence_assessment`, `florence_triage`, and `questionnaire_triage_bridge` are
rewritten to call only this.

### 1.2 Scrubber (`app/inference/scrub.py`)
Layered, fail-closed (if any layer errors, the request is routed local-only):
1. **Known-identifier pass** — exact/fuzzy replace of values we already hold for the session:
   `full_name`, `username`, `email`, `dob`, doctor name, hospital. Cheap and high-recall.
2. **Deterministic patterns** — HKID (`A123456(7)`), phone (+852 / 8-digit), email, dates,
   addresses, URLs, MRN-like ids, credit cards. Safe Harbor 18-identifier list as the checklist.
3. **NER** — Microsoft Presidio with spaCy `en_core_web_lg` + `zh_core_web_lg` (Cantonese text is
   Traditional Chinese; add a custom recogniser for Chinese surnames + honorifics). Recall on Chinese
   free text is the weak spot, so:
4. **Model pass (optional, local only)** — MedGemma 4B prompted as a PII detector with a strict JSON
   schema, run *locally* so it can see raw text. Used for the `openai`-bound path only.
5. **Reversible token map** — `[PATIENT]`, `[DATE_1]`, `[PHONE_1]` … stored in the session record
   (encrypted), never sent to a provider. Responses containing tokens are re-identified before
   returning to the patient (e.g. Florence says "Hello [PATIENT]").
6. **Scrub report** — counts per category → audit log + test assertions.

Chat turns keep a *scrubbed* transcript for OpenAI-bound tasks but MedGemma (local) may see raw
text; the router decides which copy to send.

### 1.3 Router (`app/inference/router.py` + `routing_policy.yaml`)
Inputs: task, `phi_detected`, language, `provider_health`, `compliance.openai_baa_signed` flag.

| Task | Default | Rationale |
|---|---|---|
| `chat_turn` (patient-facing) | **local MedGemma** | Raw PHI, latency-sensitive, medical tone. Never leaves VPC. |
| `pii_detect` | **local MedGemma 4B** | Must see raw text by definition. |
| `symptom_assessment` | OpenAI `gpt-5-mini` on *scrubbed* transcript | Structured extraction; cheap. Falls back to MedGemma 27B. |
| `triage` (reasoning) | OpenAI `gpt-5` (reasoning effort medium) on *scrubbed* transcript | Highest-stakes reasoning; MedGemma 27B fallback. |
| any, if `openai_baa_signed=false` | **local only** | Hard gate — no PHI-adjacent data to OpenAI without a BAA. |
| any, if scrubber failed | **local only** | Fail closed. |

Policy is data, not code, so compliance can review a YAML diff. Router emits the chosen provider
and reason into the audit event.

### 1.4 Providers (`app/inference/providers/`)
Both implement `chat(messages, params)` and `parse(messages, schema, params)`.
- `OpenAIProvider` — today's Responses API code, moved. Requires `OPENAI_BAA_SIGNED=true` and uses
  the org's zero-data-retention setting; sends `store=false`.
- `LocalMedGemmaProvider` — MedGemma served over an OpenAI-compatible endpoint, so it reuses
  `AsyncOpenAI(base_url=...)`:
  - **Dev (Mac):** Ollama, `medgemma-4b-it` (or 27B Q4 on a 32 GB+ machine). `format` = JSON schema
    for structured outputs.
  - **Prod:** vLLM on a GPU node inside the VPC (`google/medgemma-27b-text-it`, or 4B on an L4),
    `--guided-decoding-backend` for schema-constrained outputs. Weights require accepting Google's
    Health AI Developer Foundations terms; model is *not* a medical device — prompts and UI must
    keep "decision support, clinician reviews" language.
  - Health check + circuit breaker so the router can fail over.

### 1.5 Compliance controls (the "HIPAA-compliant inference" half)
Technical safeguards (45 CFR §164.312), mapped to concrete changes:
- **Access control** — keep JWT but move it to an `httpOnly; Secure; SameSite=strict` cookie;
  restrict CORS to the app origin; add role checks already started in `doctor.py`; idle timeout.
- **Audit controls** — new `audit_events` collection: actor, action, resource, provider, scrub
  stats, timestamps. No message content. 6-year retention (HIPAA documentation rule).
- **Integrity / transmission security** — TLS everywhere (Render does this), mTLS or VPC-only for
  the MedGemma endpoint.
- **Encryption at rest** — Atlas encryption + **Client-Side Field Level Encryption** for
  `conversation_history`, `user_info`, token maps, `structured_assessment.key_indicators`
  (verbatim quotes). Stop copying `user_info` into every assessment record; store `patient_ref`.
- **Minimum necessary** — providers get scrubbed text + treatment status only. Delete
  `print()`s of transcripts/args; structured logging with a PHI-redacting filter; never log keys.
- **Retention & deletion** — TTL on drafts/sessions; `DELETE /admin/users/{u}` already cascades,
  add audit + crypto-shred of token maps. Move `active_sessions` to Mongo with TTL index.
- **Vendors / BAAs** — required before PHI or de-identified-but-sensitive data flows:
  OpenAI (BAA + ZDR for API), MongoDB Atlas (BAA available), hosting (Render: confirm BAA
  availability for the plan tier; otherwise move the API to AWS/GCP with a BAA), Twilio/SendGrid
  (BAA available), Google Calendar (Workspace BAA or remove the module — it is unused by the UI).
- **De-identification stance** — Safe Harbor scrubbing reduces risk but free-text clinical
  narrative can still be identifying; treat scrubbed transcripts as PHI for vendor purposes
  (hence the BAA gate), and document the method (§164.514(b)).
- **Administrative** — risk analysis doc, incident response runbook, workforce access list,
  BAA register. Lives in `docs/compliance/`.

### 1.6 Evaluation (makes "confidence-scored" honest too)
- `tests/eval/phi_recall/`: 200 synthetic EN + ZH transcripts with seeded identifiers; assert
  ≥99% recall per category before OpenAI routing is enabled in CI.
- Routing table tests: every (task, flags) combination → expected provider.
- Triage agreement set: 50 transcripts, compare MedGemma-27B vs gpt-5 alert levels; log
  disagreement rate; low `confidence_level` + RED/ORANGE → force "clinician review" state.
- Red-team prompts: patient tries to make Florence reveal other patients / system prompt.

## 2. Delivery phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **0. Hygiene (½ day)** | Remove PHI/secret `print`s; structured logger with redaction; CORS allowlist; `store=false` on OpenAI; stop storing `user_info` in assessments | No PHI in logs; tests green |
| **1. Gateway + scrubber (2 days)** | `app/inference/` package; rewrite the 3 Florence modules + bridge to use it; Presidio + patterns + known-identifier pass; token map; PHI recall eval | 99% recall on eval set; all existing tests pass |
| **2. Local MedGemma provider (1–2 days)** | Ollama locally, vLLM manifest for prod; OpenAI-compatible provider; schema-constrained outputs; health check | Florence chat works end-to-end with OpenAI key removed |
| **3. Router + policy (1 day)** | `routing_policy.yaml`, router, BAA gate, fail-closed, audit events | Routing tests green; audit shows provider per call |
| **4. Compliance controls (3–4 days + vendor lead time)** | httpOnly cookie auth, CSFLE, sessions in Mongo w/ TTL, audit retention, BAA register, compliance docs | `docs/compliance/` complete; BAAs signed or OpenAI path disabled |
| **5. Evals & red team (1 day)** | Triage agreement set, confidence gating, prompt-injection suite | Dashboards show provider + confidence; low-confidence escalations routed to clinician |

Phases 1–3 alone make the resume line literally true. Phase 4 is what makes "HIPAA-compliant"
true; without signed BAAs the honest wording is "HIPAA-aligned safeguards, OpenAI path gated
behind BAA".

## 3. Open decisions (need your call)
1. Hosting for MedGemma prod: GPU node on GCP (Vertex AI serves MedGemma natively, BAA via GCP) vs
   self-hosted vLLM. GCP Vertex is the shortest path to a compliant managed endpoint.
2. Keep OpenAI at all? MedGemma 27B can do triage; OpenAI buys reasoning quality at the cost of a
   BAA and de-identification burden. Suggest: keep, but gated.
3. Render vs. a HIPAA-eligible host for the API (blocking for Phase 4).
4. Cantonese PHI recall target — Presidio's zh support is thin; may need the local-model pass
   always-on for zh sessions.

## 4. Skills to run against this plan
- `/cso` — security-officer review of the plan and current code (PHI logging, CORS, token storage, vendor list). Run first.
- `/plan-eng-review` — architecture review of the gateway/router/provider design and phase order.
- `/plan-ceo-review` — scope check: is Phase 4 (BAAs, hosting move) in or out for the demo timeline?
- `/spec` — turn Phase 1 (gateway + scrubber) into an executable spec before coding.
- `/review` then `/ship` per phase; `/qa` on the Florence flow after Phase 2 (local-only run).
