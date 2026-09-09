# Plan: PHI-safe inference gateway (scrub → route → compliant inference)

**Revision 2 — 2026-09-09.** Revision 1 (same week) was written before the demo-readiness push. This
revision marks what that push already landed, switches the reasoning vendor to Azure OpenAI
(Foundry), and replaces MedGemma with Gemma 4 as the default private model (see §1.5 for why).

Goal: make this statement true and defensible:

> A router directs private queries to a self-hosted open-weight medical model and reasoning tasks
> to Azure OpenAI; every chat surface strips PII/PHI before routing, and every inference path sits
> under a signed BAA.

(Revision 1 said "local MedGemma" and "OpenAI". Both words changed; §1.5 and §3 explain.)

## 0. Where we are

Legend: ✅ done in the demo-readiness push · 🟡 partly · ⬜ not started

| Area | Rev 1 (start of week) | Now | Remaining |
|---|---|---|---|
| LLM calls | 3 direct `AsyncOpenAI` calls | ✅ All three tasks go through `InferenceGateway.complete()` (`app/inference/gateway.py`); per-task provider + model from env | Policy engine replaces env routing (Phase 3) |
| Vendor | OpenAI direct, no BAA | ✅ Azure OpenAI via Foundry, deployment `gpt-5.6-sol` for all three tasks, `store=False`, reasoning-effort negotiation | Confirm BAA coverage + Modified Abuse Monitoring (Phase 4) |
| PHI in prompts | Full transcript + `user_info` (name, DOB, email) | 🟡 `user_info` narrowed to `{username, full_name}`; DOB/email no longer sent. **Full name still goes to the model** in the opening turn (`florence_ai.py:40`) and therefore in every later transcript | Scrubber + stop seeding the name (Phase 1) |
| Logging | `print()` of transcripts, args, key prefix | ✅ Florence modules have no content prints; gateway writes a content-free audit line (task, provider, model, lang, msg count, outcome, latency) | Persist audit events to Mongo (Phase 3) |
| Sessions | In-process dict | ✅ `florence_sessions` in Mongo with TTL index; background triage | Encrypt transcript fields (Phase 4) |
| Storage | Plaintext transcripts; `user_info` copied into every assessment | 🟡 Still plaintext; `user_info` still copied (`florence_utils.py:277`) | Store `patient_ref` only; CSFLE (Phase 4) |
| Auth / CORS | JWT in `localStorage`, `allow_origins=["*"]` | 🟡 CORS allowlist via `CORS_ORIGINS`; JWT still in `localStorage`, 60-min expiry | httpOnly cookie (Phase 4) |
| Local provider | none | 🟡 `providers_from_env()` builds a `local` provider from `LOCAL_INFERENCE_URL/MODEL` — nothing is running behind it | Stand up the model (Phase 2) |
| Scrubber / router policy / evals | none | ⬜ | Phases 1, 3, 5 |

## 1. Target architecture

```
Florence chat ─┐
Questionnaire ─┼─► InferenceGateway.complete(request)          (exists)
Future surfaces┘        │
                        ├─ 1. Scrubber  (known ids + patterns + NER + reversible token map)   (new)
                        ├─ 2. Router    (policy YAML: task × phi × language × health × BAA)  (new; env routing today)
                        ├─ 3. Provider  ─► PrivateProvider   (Gemma 4, Ollama dev / vLLM or Foundry managed compute prod)
                        │               └► AzureOpenAIProvider (gpt-5.6-sol, BAA, store=False)                (exists)
                        ├─ 4. Re-identify outputs (token map, server-side only)              (new)
                        └─ 5. Audit event (no content) → log today, `audit_events` collection next
```

### 1.1 Gateway (`app/inference/gateway.py`) — exists
`InferenceRequest(task, messages, instructions, schema, language, effort, temperature, metadata)`,
tasks `chat_turn | symptom_assessment | triage`. Add `pii_detect` as a fourth task and a
`patient_ref` (opaque id) in `metadata`; the username must stop appearing there.

### 1.2 Scrubber (`app/inference/scrub.py`) — new
Layered, fail-closed (any layer errors → request is routed private-only):
1. **Known-identifier pass** — exact/fuzzy replace of values we already hold for the session:
   `full_name`, `username`, `email`, `dob`, doctor name, hospital. Cheap, highest recall. Also
   **stop seeding the name**: the opening turn becomes "Hello, I'm here for my health check-in" and
   the greeting is personalised after the model replies (token `[PATIENT]` → name).
2. **Deterministic patterns** — HKID (`A123456(7)`), phones (+852 / 8-digit), email, dates,
   addresses, URLs, MRN-like ids, card numbers. Safe Harbor 18-identifier list is the checklist.
3. **NER** — Microsoft Presidio with spaCy `en_core_web_lg` + `zh_core_web_lg`; custom recogniser
   for Chinese surnames + honorifics. Recall on Traditional Chinese free text is the weak spot, so:
4. **Model pass (private route only)** — Gemma 4 E4B prompted as a PII detector with a strict JSON
   schema. Runs where raw text is allowed; always-on for `zh` sessions.
5. **Reversible token map** — `[PATIENT]`, `[DATE_1]`, `[PHONE_1]` … stored encrypted in the
   session record, never sent to a provider. Outputs are re-identified before reaching the patient.
6. **Scrub report** — counts per category → audit event + test assertions.

The private route may see raw text; the Azure route only ever sees the scrubbed copy.

### 1.3 Router (`app/inference/router.py` + `routing_policy.yaml`) — new
Inputs: task, `phi_detected`, language, provider health, `compliance.azure_baa_signed`,
`compliance.scrubber_ok`.

| Task | Default | Rationale |
|---|---|---|
| `chat_turn` (patient-facing) | **private Gemma 4** | Raw PHI, latency-sensitive, conversational tone. Never leaves our tenant. |
| `pii_detect` | **private Gemma 4 E4B** | Must see raw text by definition. |
| `symptom_assessment` | Azure `gpt-5.6-sol` (effort low) on *scrubbed* transcript | Structured extraction. Fallback: private 31B. |
| `triage` (reasoning) | Azure `gpt-5.6-sol` (effort high, background) on *scrubbed* transcript | Highest-stakes reasoning; today's behaviour. Fallback: private 31B. |
| any, if `azure_baa_signed=false` | **private only** | Hard gate. |
| any, if scrubber failed / private unhealthy and BAA false | **private only / refuse** | Fail closed, never "fall back to whatever exists" (today's `provider_for` does exactly that — remove). |

Policy is data, not code, so a compliance reviewer reads a YAML diff. Router writes provider +
reason into the audit event.

### 1.4 Providers (`app/inference/providers.py`) — exists, one class
`OpenAICompatibleProvider` already serves both routes (only `base_url`/`model` differ) and already
handles Responses API, `store=False`, schema-constrained `parse()`, and effort negotiation. Work left:
- Private route **dev (Mac):** Ollama `gemma4:e4b` for chat/PII, `gemma4:26b` for assessment
  fallback. Ollama speaks the OpenAI API at `/v1`; structured output via JSON schema works.
- Private route **prod:** vLLM serving `google/gemma-4-31b-it` (or 26B A4B for throughput) on a GPU
  node we control, or the same weights on **Azure Foundry managed compute** in our existing tenant
  (Gemma 4 is in the Foundry catalog). Either keeps data under the Microsoft BAA; the Foundry option
  needs no new vendor. Health check + circuit breaker so the router can fail over.
- `is_reasoning_model()` must not treat Gemma as a reasoning model (it keys on `gpt-5`/`o*`, fine).

### 1.5 Model choice: why not MedGemma any more
Checked 2026-09-09:
- **MedGemma is a Gemma 3 fine-tune.** Latest release is MedGemma 1.5 (Jan 2026), 4B only, and the
  1.5 work is imaging (CT/MRI volumes, pathology slides, EHR documents). The 27B text model is the
  May 2025 weights. No Gemma-4-based MedGemma exists as of today's Google releases page.
- **Gemma 4 (Mar 2026, refreshed through Jul 2026)** is one base generation newer, Apache 2.0
  (MedGemma is under HAI-DEF terms with a prohibited-use policy), 140+ languages, 128–256K context,
  and ships on Ollama, vLLM and the Azure Foundry catalog.
- **On the tasks we run, the newer general model wins.** An independent July 2026 evaluation
  (Ground Truth) found Gemma-4-31B ahead of MedGemma-27B on hard medical reasoning (MedXpertQA 48.7
  vs 13.3), emergency triage (flagged 68% of life-threatening cases as urgent vs 8%), differential
  diagnosis (Gemma-4-26B 56.7% vs 40.8%) and note faithfulness (half the unsupported statements),
  with MedQA a wash (69.3 vs 70). MedGemma-1.5-4B *regressed* vs MedGemma-4B on text tasks.
- **Our private tasks are text, conversational, bilingual.** Medical imaging — MedGemma's actual
  edge — is not in scope. Traditional Chinese handling and instruction following matter more than
  exam scores, and both favour the newer base.

Decision: **default the private route to Gemma 4**; keep MedGemma-27B-text in the Phase 5 triage
agreement eval as a comparator, and revisit if Google ships a Gemma-4-based medical model. The
provider class is model-agnostic, so this is an env/manifest change, not code.

Open-weight reasoning alternative for the *Azure* route if we ever want to drop gpt-5.6-sol:
`gpt-oss-120b` (Apache 2.0, HealthBench ≈57.6, in the Foundry catalog). Not needed now.

### 1.6 Compliance controls (the "HIPAA-compliant inference" half)
Technical safeguards (45 CFR §164.312), mapped to concrete changes:
- **Access control** — move the JWT to an `httpOnly; Secure; SameSite=strict` cookie; CORS allowlist
  ✅; role checks on doctor routes ✅; idle timeout.
- **Audit controls** — `audit_events` collection: actor ref, action, resource, provider, scrub stats,
  timestamps. No message content. 6-year retention. (Gateway log line ✅ is the interim.)
- **Transmission security** — TLS everywhere ✅ (Render/Cloudflare/Azure); private endpoint reachable
  only from the API (VNet / private link or allowlisted egress).
- **Encryption at rest** — Atlas encryption ✅ (default) + **Client-Side Field Level Encryption** for
  `conversation_history`, token maps, `structured_assessment.key_indicators` (verbatim quotes).
  Stop copying `user_info` into assessments; store `patient_ref`.
- **Minimum necessary** — providers get scrubbed text + treatment status only. Content prints ✅
  removed; add a PHI-redacting log filter; never log keys.
- **Retention & deletion** — TTL on sessions ✅ and drafts; `DELETE /admin/users/{u}` cascades ✅,
  add audit + crypto-shred of token maps.
- **Vendors / BAAs** — **Azure OpenAI / Foundry**: covered by the Microsoft BAA through the Products
  and Services DPA, no per-model request. Two caveats: (a) Azure OpenAI keeps prompts/completions up
  to 30 days for abuse monitoring by default — apply for **Modified Abuse Monitoring** (Limited
  Access form); eligibility is "managed customers" (EA/MCA-E), which a pay-as-you-go startup may
  not meet — find out in week 1 of Phase 4. (b) Check the DPA applies to the subscription the
  `ovis-oai` resource lives in. **MongoDB Atlas**: BAA available (M10+). **Render**: confirm BAA
  availability for our tier; if none, move the API to Azure Container Apps in the same tenant (keeps
  one vendor, one BAA). **Twilio/SendGrid**: BAA available. **Google Calendar**: unused by the UI —
  remove the module rather than paper it.
- **De-identification stance** — Safe Harbor scrubbing reduces risk but clinical narrative can still
  identify; treat scrubbed transcripts as PHI for vendor purposes (hence the BAA gate) and document
  the method (§164.514(b)).
- **Administrative** — risk analysis, incident-response runbook, workforce access list, BAA register.
  Lives in `docs/compliance/`.

### 1.7 Evaluation
- `tests/eval/phi_recall/`: 200 synthetic EN + ZH transcripts with seeded identifiers; ≥99% recall
  per category before the Azure route is enabled in CI.
- Routing table tests: every (task, flags) combination → expected provider. Fail-closed cases included.
- Triage agreement set: 50 transcripts; compare Gemma-4-31B, MedGemma-27B and gpt-5.6-sol alert
  levels; log disagreement rate. Also the tool for the known **over-escalation** of mild symptoms.
- Red-team prompts: patient tries to make Florence reveal other patients / the system prompt.

## 2. Delivery phases (updated)

| Phase | Scope | Status / exit criteria |
|---|---|---|
| **0. Hygiene** | Content prints; CORS allowlist; `store=False`; sessions in Mongo | ✅ done except: stop copying `user_info` into assessments (moved to Phase 1) |
| **1. Scrubber (2 days)** | `scrub.py`; known-id pass; patterns; Presidio EN/ZH; token map + re-identify; stop seeding the patient's name; `patient_ref` replaces username in metadata and records; PHI recall eval | 99% recall on eval set; Azure sees no name in any transcript; tests green |
| **2. Private provider (1–2 days)** | Ollama Gemma 4 locally; vLLM or Foundry managed-compute manifest for prod; health check; `pii_detect` task | Florence chat works end to end with `OPENAI_API_KEY` unset |
| **3. Router + policy (1 day)** | `routing_policy.yaml`, router, BAA gate, fail-closed (delete the "fall back to whatever exists" branch), `audit_events` | Routing tests green; every call has an audit doc with provider + reason |
| **4. Compliance (3–4 days + vendor lead time)** | httpOnly cookie auth; CSFLE; Modified Abuse Monitoring application; Atlas + Render (or Container Apps) BAAs; BAA register; `docs/compliance/` | BAAs on file or Azure route disabled by policy |
| **5. Evals & red team (1 day)** | Triage agreement set (also fixes over-escalation); confidence gating; prompt-injection suite | Low-confidence RED/ORANGE forced to "clinician review"; dashboards show provider + confidence |

Phases 1–3 make the headline statement literally true. Phase 4 makes "HIPAA-compliant" true;
without signed BAAs the honest wording is "HIPAA-aligned safeguards; cloud route gated behind BAA".

## 3. Decisions

Taken this revision:
1. **Reasoning vendor = Azure OpenAI (Foundry), `gpt-5.6-sol`.** Already live; the BAA path is
   Microsoft's DPA rather than a new OpenAI contract.
2. **Private model = Gemma 4, not MedGemma.** See §1.5.
3. **"Local" means "in our tenant", not "on the Render box".** Dev runs Ollama on the Mac; prod runs
   vLLM on hardware we control or Foundry managed compute. The resume line should read
   "self-hosted open-weight model", not "local MedGemma".

Still open (need your call):
4. Prod home for the private model: GPU node we rent (cheaper per token, more ops) vs Foundry
   managed compute (one vendor, one BAA, simplest). Recommend Foundry until volume justifies a box.
5. Keep Render for the API, or move to Azure Container Apps in the same tenant during Phase 4?
   Blocking for the "every path under a BAA" claim only if Render cannot offer one.
6. Traditional Chinese PHI recall target — Presidio's zh support is thin; plan assumes the model pass
   is always-on for zh sessions. Accept the latency, or set a lower bar for zh in v1?

## 4. Skills to run against this plan
- `/cso` — security-officer review of this plan and current code (name in prompts, token storage,
  vendor list). Run first.
- `/plan-eng-review` — architecture review of scrubber/router/provider and phase order.
- `/plan-ceo-review` — scope check: is Phase 4 in or out for the next demo cycle?
- `/spec` — turn Phase 1 into an executable spec before coding.
- `/review` then `/ship` per phase; `/qa` on the Florence flow after Phase 2 (private-only run).

## 5. Sources for §1.5 (checked 2026-09-09)
- Google Gemma releases page: MedGemma 1.5 4B (2026-01-13); Gemma 4 E2B/E4B/31B/26B A4B
  (2026-03-31), 12B Unified (2026-06-03). https://ai.google.dev/gemma/docs/releases
- MedGemma overview (all variants "built on Gemma 3"):
  https://developers.google.com/health-ai-developer-foundations/medgemma
- MedGemma 1.5 technical report (imaging/EHR focus): https://arxiv.org/abs/2604.05081
- Gemma 4 model card (Apache 2.0, 140+ languages, 128–256K): https://ai.google.dev/gemma/docs/core/model_card_4
- Ground Truth, "MedGemma vs Gemma: does Google's medical fine-tune earn the badge?" (Jul 2026):
  https://groundtruth.health/medgemma-medical-badge/
- Gemma 4 in Microsoft Foundry: https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/gemma-4-now-available-in-microsoft-foundry/4510984
- Azure OpenAI abuse monitoring / Modified Abuse Monitoring eligibility:
  https://learn.microsoft.com/en-au/answers/questions/5954113/modified-abuse-monitoring-on-azure-foundry
- gpt-oss model card (HealthBench): https://arxiv.org/pdf/2508.10925
