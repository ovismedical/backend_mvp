# Plan: PHI-safe inference for Florence (scrub → gate → Azure → audit)

**Revision 3 — 2026-09-09** (amended same day: scrubber scope now explicitly covers PII the patient volunteers in chat and indirect identifiers that combine to identify). Supersedes rev 2. Changes: Hong Kong's PDPO is the
governing law, not HIPAA; the self-hosted model is **parked** with explicit unpark triggers; the
scrubber stays on the critical path; clinician agree/override capture is added because it is the one
debt that compounds while the local route is parked; residency work targets storage and the API,
which we control, rather than inference, which we cannot place in Hong Kong on Azure OpenAI.

## Headline claim this plan makes true

> Patient data is stored in Hong Kong and de-identified before any AI call. Inference runs on Azure
> OpenAI under Microsoft's data protection terms, never on OpenAI directly. Every call is audited
> without content. A self-hosted model route exists for sites that require on-premises processing.

For Hong Kong clinicians the binding law is the Personal Data (Privacy) Ordinance (PDPO, Cap. 486).
"HIPAA" stays in the plan only as the technical-safeguards checklist; the controls are the same.

## 0. Where we are

Legend: ✅ done · 🟡 partly · ⬜ not started · 🅿 parked

| Area | Now | Remaining |
|---|---|---|
| LLM calls | ✅ All three tasks go through `InferenceGateway.complete()` (`app/inference/gateway.py`); per-task provider + model from env | Policy engine + fail-closed (Phase 2) |
| Vendor | ✅ Azure OpenAI via Foundry, deployment `gpt-5.6-sol` for all tasks, `store=False`, effort negotiation | Confirm resource region + deployment type; Modified Abuse Monitoring (Phase 4) |
| PHI in prompts | 🟡 DOB/email no longer sent. **Full name still goes to the model** in the opening turn (`florence_ai.py:40`) and therefore in every transcript | Scrubber + stop seeding the name (Phase 1) |
| Logging | ✅ No content prints in Florence modules; gateway emits a content-free audit line | Persist to `audit_events` (Phase 2) |
| Sessions | ✅ `florence_sessions` in Mongo with TTL; background triage | Field encryption (Phase 5) |
| Storage | 🟡 Plaintext transcripts; `user_info` copied into every assessment (`florence_utils.py:277`); cluster region unknown | `patient_ref`; CSFLE; Hong Kong region (Phases 1, 4, 5) |
| Auth / CORS | 🟡 CORS allowlist ✅; JWT in `localStorage`, 60-min expiry | httpOnly cookie (Phase 5) |
| Clinician feedback on triage | ⬜ No agree/override capture anywhere | Phase 3 |
| Self-hosted route | 🅿 `local` provider slot built from `LOCAL_INFERENCE_URL/MODEL`; unit-tested with a fake; nothing running | Parked, see §5 |
| Scrubber / policy / evals | ⬜ | Phases 1, 2, 3 |

## 1. Regulatory position (checked 2026-09-09)

**What binds us**
- PDPO Data Protection Principles 1–6: tell patients at collection that an AI processor is used and
  that processing happens outside Hong Kong (DPP1); keep no longer than needed (DPP2); use only for
  the stated purpose (DPP3); take all practicable steps to secure data **and bind processors by
  contract** (DPP4); publish a privacy policy (DPP5); honour access/correction (DPP6).
- PCPD *Artificial Intelligence: Model Personal Data Protection Framework* (June 2024): governance,
  risk assessment, human oversight, processor agreements, transparency to data subjects.
- PCPD breach-handling guidance (notification is still voluntary, but expected).
- Medical Council confidentiality duties; Code of Practice for Private Hospitals (2024) if we deploy
  inside a licensed facility: records secure, "additional care" with cloud providers.

**What does not bind us**
- **No data-localisation law.** PDPO s.33 (cross-border restriction) was enacted in 1995 and has
  never been brought into force; the PCPD's stated position is that the Ordinance does not require
  data to stay in Hong Kong. Transfers are lawful with notice and contractual safeguards (PCPD model
  clauses, 2022).
- The PDPO reform (mandatory breach notification, direct regulation of processors, fines up to
  HK$10m or 10% of turnover) was paused Nov 2024 and revived Feb 2026. No localisation proposal.
  Design for it anyway: breach runbook, processor register.
- Protection of Critical Infrastructures (Computer Systems) Ordinance (in force 2026-01-01) covers
  healthcare but only *designated* operators. Applies to us only as a supplier clause if a
  designated hospital buys Ovis.
- Mainland PIPL localisation: only if we collect mainland residents' data from the mainland. Not today.

**The geoblock**
- OpenAI has blocked Hong Kong since July 2024. A direct OpenAI key does not work from a clinic.
  This is why Azure Foundry is the vendor.
- Azure OpenAI is available to Hong Kong customers but **cannot be deployed in the East Asia (Hong
  Kong) region**. Nearest regions with current models: Japan East, Korea Central, Australia East.
  Therefore "inference inside Hong Kong" is only possible self-hosted — the single hard reason the
  parked route exists.

## 2. Target architecture

```
Florence chat ─┐
Questionnaire ─┼─► InferenceGateway.complete(request)                         (exists)
Future surfaces┘        │
                        ├─ 1. Scrubber  known ids → patterns → NER → token map   (Phase 1)
                        ├─ 2. Gate      dpa_ok ∧ scrubber_ok ∧ provider healthy  (Phase 2, fail-closed)
                        ├─ 3. Provider  ─► AzureOpenAIProvider gpt-5.6-sol       (exists)
                        │               ╌► PrivateProvider (Gemma 4)             (parked slot, exists)
                        ├─ 4. Re-identify outputs (token map, server-side only)  (Phase 1)
                        └─ 5. Audit event, no content → `audit_events`           (Phase 2)
```

### 2.1 Gateway — exists
Add `pii_detect` as a task and `patient_ref` (opaque id) in `metadata`; the username must stop
appearing in metadata, logs and records.

### 2.2 Scrubber (`app/inference/scrub.py`)
Runs on **every message in both directions**, including anything the patient volunteers in free
text. The identifiers we hold in the profile are the easy part. The hard part is what people say:
"my daughter Mei Ling took me to Queen Mary on Tuesday", "I live in Tai Koo Shing", "Dr Chan at the
Sanatorium said…", "I'm 59 and I teach at a school in Sha Tin". Each piece is harmless alone and
identifying together (the mosaic effect). So the scrubber targets direct identifiers **and** the
enumerable indirect ones, and it **generalises rather than deletes** so the clinical meaning survives.

Layered; if any layer errors the request is **refused**, not forwarded.
1. **Known-identifier pass** — exact/fuzzy replace of values we hold for the session: `full_name`,
   `username`, `email`, `dob`, doctor name, hospital. Also **stop seeding the name**: the opening
   turn becomes "I'm here for my health check-in" and the greeting is personalised after the reply
   via the `[PERSON_1]` token.
2. **Deterministic patterns** — HKID `A123456(7)`, passport / Home Return Permit numbers, phones
   (+852 / 8-digit), email, addresses (Flat/Floor/Block/Estate and 室/樓/座/邨/苑), URLs and handles
   (WhatsApp, Instagram, WeChat), MRN-like ids, insurance policy numbers, card numbers, plates.
3. **Hong Kong gazetteers** — Hospital Authority and private hospitals and clinics, the 18
   districts, major housing estates, MTR stations, in English and Chinese. Deterministic and
   high-recall for the places people actually mention.
4. **NER** — Presidio with spaCy `en_core_web_lg` + `zh_core_web_lg`, **plus** a small multilingual
   PII token classifier (GLiNER-class, runs on CPU in tens of milliseconds) evaluated against the
   same set; whichever wins on Traditional Chinese recall ships. Kinship rule: a name that follows
   我個女 / 我老公 / my daughter / my husband is a person.
5. **Model pass — conditional.** Only if recall still misses the bar. If needed, scope it to the
   background triage payload first, which is latency-insensitive; a 4B model on CPU covers it.
6. **Generalisation table** — the part that defends against piecing together:

   | Found | Becomes | Why |
   |---|---|---|
   | person (patient, relative, clinician) | `[PERSON_1]`, `[PERSON_2]` … consistent per session | the model keeps the thread |
   | hospital / clinic | `[FACILITY_1]` | |
   | district / estate / street / station | `[PLACE_1]` | |
   | employer / school / insurer | `[ORG_1]` | |
   | occupation | `[OCCUPATION]` | rarely clinically relevant here |
   | absolute date | `[DATE_1 · 3 days ago]` | the model needs timing, not the date |
   | stated age | decade band; 90+ collapsed | Safe Harbor rule |
   | id numbers, phones, emails, handles | `[ID]`, `[PHONE]`, `[EMAIL]`, `[HANDLE]` | |

   Symptom words, medication names, severities and durations are never touched. The eval measures
   **over-scrubbing** as well as recall ("Queen Mary" in a hospital name vs "Mary" the patient's
   friend; 陳皮 and 李子 are not surnames).
7. **Reversible token map** — stored in the session record, never sent to a provider. Outputs are
   re-identified before reaching the patient; clinician views are re-identified too, since the care
   team is entitled to the real text.
8. **Scrub report** — counts per category, plus a *linkage score*: how many distinct indirect
   categories appeared in one payload. An audit metric for now, not a gate.

**Prompt and UI side, cheap and effective**
- Florence's system prompt: never ask for names, addresses, ID numbers or exact dates; if the
  patient offers them, do not repeat them; refer to people by relationship.
- Chat surface hint: "You don't need to share names, addresses or ID numbers with Florence."

**Honest limit.** Narrative can still identify someone: a rare diagnosis plus a life event plus a
treatment timeline. Scrubbing is data minimisation, not anonymisation. The processor contract in §4
is the backstop, which is why the gate refuses when `dpa_ok` is false even with a perfect scrub.

### 2.3 Gate + policy (`app/inference/router.py` + `routing_policy.yaml`)
Inputs: task, `scrubber_ok`, `compliance.dpa_ok` (Microsoft DPA accepted for the subscription and
abuse-monitoring status recorded), provider health.

| Condition | Behaviour |
|---|---|
| all ok | Azure `gpt-5.6-sol`: chat effort low, assessment low, triage high in background (today) |
| `scrubber_ok=false` or `dpa_ok=false` | **Refuse the AI call.** Chat returns the existing scripted fallback (`generate_fallback_response`); assessment/triage records `status=pending_clinician_review` and the clinician sees it flagged |
| provider unhealthy | same refusal path; alert |
| private route configured and healthy (when unparked) | policy may route `chat_turn`/`pii_detect` there; Azure remains default for triage |

Delete today's "configured route unavailable → fall back to whatever provider exists" branch in
`provider_for()`. Policy is YAML so a reviewer reads a diff. The chosen provider and reason go into
the audit event.

### 2.4 Providers — exists, one class
`OpenAICompatibleProvider` already serves Azure and any OpenAI-compatible server. Ollama and vLLM
both expose `/v1/responses` with JSON-schema output now, so the parked route needs no new code.

## 3. Clinician feedback capture (new; the compounding debt)
Nothing records whether the clinician agreed with Florence's alert level. Every unlabelled triage
is training and calibration signal lost for good, and it is also the evidence base for the known
over-escalation of mild symptoms.
- `POST /doctor/assessments/{id}/review` with `{agrees: bool, alert_level_override?, note?}` →
  `assessment_reviews` (doctor ref, assessment id, timestamp). One tap on the assessment view in
  `/patient_details/:id`; override shows as the reviewed level on the patient list.
- Feeds the triage-agreement eval (§6) and a future fine-tune if the private route is unparked.

## 4. Compliance controls mapped to the PDPO
- **DPP1 notice** — update the sign-up notice / PICS and the privacy policy: AI-assisted symptom
  monitoring, processor (Microsoft Azure), processing outside Hong Kong (Japan/Korea/Australia
  region as configured), retention periods. Florence must be labelled as AI in the chat surface.
- **DPP2 retention** — TTL on sessions ✅ and drafts; define retention for assessments and
  transcripts; `DELETE /admin/users/{u}` cascade ✅ plus crypto-shred of token maps.
- **DPP3 use** — provider receives scrubbed text + treatment status only; no training (`store=False`).
- **DPP4 security + processors** — Microsoft DPA (includes HIPAA BAA terms) for the `ovis-oai`
  subscription; apply for **Modified Abuse Monitoring** (default keeps prompts up to 30 days; the
  opt-out is limited to managed EA/MCA-E customers — find out in Phase 4 week 1); MongoDB Atlas DPA;
  Twilio/SendGrid DPA; remove the unused Google Calendar module. Move the JWT to an `httpOnly;
  Secure; SameSite=strict` cookie; CSFLE for `conversation_history`, token maps and verbatim
  `key_indicators`; TLS ✅; PHI-redacting log filter.
- **DPP5 openness** — privacy policy page in the app (verify one exists on `ui-revamp`; add if not).
- **DPP6 access/correction** — export endpoint for a patient's own records; correction via clinician.
- **Residency of what we control** — Atlas cluster in Azure East Asia (Hong Kong) or AWS
  `ap-east-1`; API on Azure Container Apps East Asia if Render (no Hong Kong region) is a problem
  for the customer; Azure OpenAI deployment as **regional Standard in Japan East** rather than
  Global Standard if the customer cares where processing happens. Check in the portal: resource
  Location and deployment SKU.
- **PCPD AI framework** — risk assessment document, human-in-the-loop (clinician review of every
  ORANGE/RED), transparency, `docs/compliance/` with processor register + breach runbook.
- **Audit** — `audit_events`: actor ref, action, resource, provider, scrub stats, gate decision,
  timestamps. No message content. Keep 7 years (matches HK medical-record norms).

## 5. Parked: self-hosted route
Built: provider slot, env wiring, unit tests with a fake. Not built: anything running.

**Unpark when any of these is true**
1. A customer puts "processing in Hong Kong" or "on-premises" in writing.
2. Volume approaches ~1,000 daily check-ins with background triage (GPU break-even).
3. A vendor cut-off or export-control change threatens the Azure route.
4. `assessment_reviews` holds a few thousand clinician-confirmed labels worth fine-tuning on.

**Keep it alive while parked**
- Keep the gateway unit tests green.
- `docs/runbooks/local-inference.md`: the three commands (`ollama pull gemma4:e4b`, env vars,
  `uvicorn`) to run Florence against Ollama. Run before any pitch that says "hybrid" or
  "self-hostable".
- Optional one-day proof of fallback: run the triage-agreement set against Gemma 4 31B and record
  the number. Do it only if a pitch needs it.

Model choice when unparked: **Gemma 4** (Apache 2.0, 140+ languages, on Ollama/vLLM/Foundry), not
MedGemma (Gemma 3 fine-tune; latest 1.5 release is 4B and imaging-focused; independent July 2026
evaluation had Gemma 4 31B well ahead on triage and reasoning). Revisit if a Gemma-4-based medical
model ships.

## 6. Evaluation
- `tests/eval/phi_recall/`: 200 synthetic EN + ZH-HK transcripts. Identifiers are seeded in
  **patient turns as volunteered free text**, not just in the profile: relatives' names after
  kinship terms, hospitals, districts and estates, employers, exact dates, stated ages, HKIDs,
  phones, handles, and combinations of three or more indirect identifiers in one session. Report
  recall per category, zh separately, **and over-redaction** (clinical terms wrongly removed).
  ≥99% recall on direct identifiers and ≥95% on indirect ones before the Azure route is enabled in CI.
- Gate tests: every (scrubber_ok, dpa_ok, health) combination → expected behaviour, including the
  refusal UX.
- Triage agreement: clinician labels from `assessment_reviews` vs Florence; disagreement rate by
  alert level; this is the tool for the over-escalation fix. Low `confidence_level` + RED/ORANGE →
  "clinician review" state.
- Red team: patient tries to make Florence reveal other patients / the system prompt / give dosing.

## 7. Phases

| Phase | Scope | Effort | Exit criteria |
|---|---|---|---|
| **1. Scrubber + minimum necessary** | `scrub.py` with all eight layers incl. HK gazetteers and the generalisation table; stop seeding the name; token map + re-identify (patient and clinician views); `patient_ref` replaces username in metadata and records; stop copying `user_info` into assessments; Florence prompt + chat hint; PHI recall + over-redaction eval EN + ZH | 3 days | ≥99% direct / ≥95% indirect recall; Azure sees no name, place, date or facility in any transcript; tests green |
| **2. Gate + audit** | `routing_policy.yaml`; `dpa_ok`/`scrubber_ok` gate; delete fall-back-to-anything; refusal UX; `audit_events` | 1 day | Gate tests green; every call has an audit doc with decision + reason |
| **3. Clinician feedback + evals** | Agree/override endpoint + UI; triage-agreement report; confidence gating | 1.5 days | Every ORANGE/RED can be reviewed in one tap; disagreement report runs |
| **4. Contracts + residency** | Portal check of region/SKU; Modified Abuse Monitoring application; Atlas to Hong Kong region; Render-vs-Container-Apps decision; DPP1 notice + privacy policy; processor register; breach runbook | 2–3 days + lead time | Notice live; processors documented; storage in Hong Kong |
| **5. Hardening** | httpOnly cookie auth; CSFLE; retention policy; export endpoint; red-team suite | 2–3 days | `docs/compliance/` complete |
| **🅿 Self-hosted route** | Ollama dev recipe now; vLLM/Foundry prod only on an unpark trigger | ½ day now; 1–2 days when triggered | Runbook works; (later) Florence runs with `OPENAI_API_KEY` unset |

Phases 1–3 (about 5.5 days) make the headline claim true except "stored in Hong Kong", which is Phase 4.

## 8. Decisions

Taken in this revision
1. Vendor = Azure OpenAI (Foundry), `gpt-5.6-sol`. OpenAI direct is unusable from Hong Kong.
2. Governing framework = PDPO; HIPAA kept as the technical checklist only.
3. Self-hosted route parked with the triggers in §5; Gemma 4 when unparked.
4. Scrubber stays on the critical path and covers **volunteered and indirect identifiers**, not just profile fields; model pass only if recall demands it.
5. Clinician agree/override capture added now.

Need your call
6. **Ask the doctor which they meant**: (a) "not ChatGPT" — done; (b) "data stays in Hong Kong" —
   storage yes, inference no without unparking; (c) "on-premises/offline" — unpark.
7. Render stays, or API moves to Azure Container Apps East Asia in Phase 4?
8. Traditional Chinese PHI recall bar for v1: same 99% as English, or lower with the model pass
   deferred?
9. Drop the word "HIPAA" from Hong Kong-facing materials?

## 9. Skills to run against this plan
- `/cso` — security review of the plan and current code (name in prompts, token storage, processors).
- `/plan-eng-review` — scrubber/gate design and phase order.
- `/plan-ceo-review` — is Phase 4 in the next demo cycle or after the first signed pilot?
- `/spec` — Phase 1 as an executable spec before coding.
- `/review` then `/ship` per phase; `/qa` on Florence after Phases 1–2.

## 10. Sources (checked 2026-09-09)
- PCPD on data localisation: https://www.pcpd.org.hk/english/news_events/media_enquiry/enquiry_20200415.html
- PDPO s.33 status: https://www.tannerdewitt.com/cross-border-transfers-section-33/
- PDPO reform status (paused 2024, revived Feb 2026): https://www.hfw.com/insights/a-new-era-for-data-protection-in-hong-kong-legislative-updates-for-a-digital-age/
- PCPD AI framework (2024): https://www.pcpd.org.hk/english/news_events/media_statements/press_20240611.html
- Code of Practice for Private Hospitals (2024): https://www.orphf.gov.hk/files/forms/PHF(E)_11A_CoP_PH_Eng.pdf
- Critical Infrastructures ordinance in force 2026-01-01: https://www.info.gov.hk/gia/general/202506/27/P2025062700238.htm
- OpenAI supported countries: https://help.openai.com/en/articles/5347006-openai-api-supported-countries-and-territories
- Azure OpenAI not deployable in Hong Kong (Microsoft Q&A, Mar 2026): https://learn.microsoft.com/en-gb/answers/questions/5813396/providing-solution-in-hong-kong-using-openai-found
- Azure OpenAI region list for gpt-5.x: https://foundrymap.fyi/models/gpt-5-4
- Modified Abuse Monitoring eligibility: https://learn.microsoft.com/en-au/answers/questions/5954113/modified-abuse-monitoring-on-azure-foundry
- Atlas regions (Azure East Asia, AWS ap-east-1): https://www.mongodb.com/docs/atlas/cloud-providers-regions/
- Gemma releases / MedGemma status: https://ai.google.dev/gemma/docs/releases
- MedGemma vs Gemma 4 evaluation (Jul 2026): https://groundtruth.health/medgemma-medical-badge/
- Ollama OpenAI compatibility incl. `/v1/responses`: https://docs.ollama.com/capabilities/structured-outputs
