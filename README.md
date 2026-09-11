# OVIS Medical Backend

FastAPI backend for the OVIS healthcare platform. Provides authentication, AI-powered health triage (Florence AI), patient management, analytics, and appointment scheduling.

## Prerequisites

- Python 3.12+
- MongoDB Atlas account (or local MongoDB)
- OpenAI API key (for Florence AI)

## Setup

```bash
cd backend_mvp
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy the environment template and fill in your values:

```bash
cp .env.example .env
```

## Running

```bash
source venv/bin/activate
python -m uvicorn app.api:app --reload --port 8000 --no-access-log
```

API docs available at http://localhost:8000/docs

`--no-access-log` keeps request paths (which carry usernames in session ids and `/doctor/patient/{username}`)
out of stdout; `app/api.py` also disables the `uvicorn.access` logger for bare `uvicorn` runs.

## Local demo (no Atlas, no OpenAI key required)

```bash
docker run -d --name ovis-mongo -p 27017:27017 mongo:7
# .env: MONGODB_URI=mongodb://localhost:27017, SECRET_KEY=<random>, OPENAI_API_KEY=<optional>
python scripts/seed_demo.py          # hospital + doctor + 2 patients with 6 weeks of history
python -m uvicorn app.api:app --reload --port 8000 --no-access-log
```

Demo accounts (password `demo1234` for all): patient `alex`, patient `jordan`, clinician `drlee`.
Patients sign up with the clinician's access code `OVIS`; clinicians sign up with hospital code `HOSP`.
Re-running `seed_demo.py` wipes and recreates the demo accounts and their data.

Without `OPENAI_API_KEY`, Florence chat runs in fallback mode (placeholder replies, no AI triage);
the questionnaire, analytics, and clinician dashboard work fully from stored data.

With a key but without `COMPLIANCE_DPA_OK=true`, the routing policy (`app/inference/routing_policy.yaml`)
refuses every call to the `openai` provider: chat gets a scripted reply and finished check-ins are saved
as `pending_clinician_review` for the care team. `GET /health` shows `florence_ai: "refusing"` in that state.
Every model call is recorded, content-free, in the `audit_events` collection.

Before anything reaches a provider it is de-identified (`app/inference/scrub`): the patient becomes
`[PERSON_1]`, other people, places, facilities, dates, ages and numbers become bracketed placeholders, and
replies are re-identified from a per-session token map before they are stored or shown. Logs and audit
documents carry only `patient_ref` / `session_ref` (HMACs keyed by `SECRET_KEY`), never names or usernames.

Run the tests with `pytest` (no database needed).

## Project Structure

```
backend_mvp/
├── app/
│   ├── api.py                  # FastAPI app, router registration, health check
│   ├── login.py                # Auth, JWT tokens, user management, DB connection
│   ├── doctor.py               # Doctor endpoints
│   ├── florence.py              # Florence AI chat router
│   ├── florence_ai.py           # Florence AI core logic
│   ├── florence_assessment.py   # AI assessment generation
│   ├── florence_triage.py       # AI triage system
│   ├── florence_utils.py        # Florence helper utilities
│   ├── analytics.py             # Health analytics endpoints
│   ├── calendar.py              # Appointment/calendar endpoints
│   ├── questions.py             # Daily check-in questions
│   ├── symptom_questionnaire.py # Symptom questionnaire CRUD
│   ├── triage_api.py            # Triage API endpoints
│   ├── otp_routes.py            # OTP verification routes
│   ├── otp_system.py            # OTP logic
│   └── twilio_verify.py         # Twilio SMS integration
├── scripts/                     # Database utility scripts
├── .env.example                 # Environment variable template
├── requirements.txt             # Python dependencies
└── README.md
```

## Environment Variables

See [.env.example](.env.example) for all required variables:

- `MONGODB_URI` - MongoDB Atlas connection string
- `MONGODB_DB` - Database name (default: `ovis-demo`)
- `SECRET_KEY` - JWT signing key
- `OPENAI_API_KEY` - OpenAI API key for Florence AI (`OPENAI_BASE_URL` points it at Azure OpenAI / Foundry)
- `COMPLIANCE_DPA_OK` - declared in the routing policy but **not required** by the shipped one, so nothing needs to be set. Add `dpa_ok` to `providers.openai.requires` to turn the contractual gate back on; it then refuses every `openai`-routed call while unset (scripted chat fallback, assessments saved as `pending_clinician_review`, `/health` shows `florence_ai: refusing`)
- `INFERENCE_POLICY_PATH` - optional alternative to `app/inference/routing_policy.yaml` (which task runs on which provider, and what each provider requires)
- `INFERENCE_ROUTE_CHAT` / `INFERENCE_ROUTE_ASSESSMENT` / `INFERENCE_ROUTE_TRIAGE` - optional per-task provider override (`openai` or `local`); unset = the policy file's provider
- `OPENAI_CHAT_MODEL` / `OPENAI_ASSESSMENT_MODEL` / `OPENAI_TRIAGE_MODEL` - optional per-task model (Azure: deployment names); unset = `OPENAI_MODEL`. This is how cost tiering is turned on: chat runs on every patient message at the cheapest reasoning effort, triage runs once per session at `high`, so pointing `OPENAI_CHAT_MODEL` at a smaller deployment splits them with no code change. `GET /health` reports the live picture under `cost` (`model_tiering: shared` while one deployment serves everything). Efforts and tool-hop caps live in `TASK_PROFILES` in `app/inference/gateway.py`.
- `LOCAL_INFERENCE_URL` / `LOCAL_INFERENCE_MODEL` - an OpenAI-compatible server on your own network (Ollama, vLLM). With `INFERENCE_POLICY_PATH=app/inference/routing_policy.local.yaml` this gives the hybrid split: the patient's conversation is answered by the local model and never leaves the network, while assessment and triage reasoning go to Azure on a de-identified transcript. See [Hybrid routing](#hybrid-routing-self-hosted-chat-vendor-reasoning)
- `SCRUB_NER_BACKEND` - optional NER layer for the PHI scrubber: `none` (default), `local`, `presidio`, `gliner`; a configured but unavailable backend fails closed (chat refused, check-ins saved for clinician review). `local` asks the model behind the policy's `pii_detect` route to name identifiers the deterministic layers missed; measured net-negative on the eval corpus, so it stays off (see [tests/eval/README.md](tests/eval/README.md))
- `SECRET_KEY` also keys the opaque `patient_ref` / `session_ref` written to logs and `audit_events`; rotating it breaks the join between old audit rows and patients
- `SENDGRID_API_KEY` - SendGrid for email
- `CALENDAR_ENCRYPTION_KEY` - Calendar data encryption

## Hybrid routing: self-hosted chat, vendor reasoning

The router reads `app/inference/routing_policy.yaml` on every call and decides which provider serves
which task. The shipped default sends everything to Azure OpenAI, because the hosted deployment has
no GPU. `app/inference/routing_policy.local.yaml` is the alternative: private queries stay on a model
you run, and only de-identified transcripts reach the vendor.

```bash
ollama pull medgemma && ollama serve
# .env
LOCAL_INFERENCE_URL=http://localhost:11434/v1
LOCAL_INFERENCE_MODEL=medgemma:latest
INFERENCE_POLICY_PATH=app/inference/routing_policy.local.yaml
```

| Task | Provider | Why |
|---|---|---|
| `chat_turn` | local | Raw patient conversation. Never leaves the network. ~0.6 s per turn on a 4B model. |
| `pii_detect` | local | Must see unscrubbed text by definition. |
| `symptom_assessment`, `triage` | Azure OpenAI | Highest-stakes reasoning, on a scrubbed transcript. |

The split is enforced, not advisory: the vendor provider requires `scrubbed`, so a de-identification
failure refuses instead of falling back, and a missing local server refuses the chat turn rather than
quietly sending raw text to Azure. `tests/unit/test_routing_policy_local.py` fails if an edit ever
routes a raw-text task off the network.

## API Endpoints

The backend exposes 47 endpoints across these routers:

| Router | Prefix | Purpose |
|--------|--------|---------|
| login | `/token`, `/register`, `/userinfo`, `/updateinfo` | Auth & user management (`/register` is direct sign-up with an access code; `/otp/*` is the Twilio-verified variant) |
| doctor | `/doctor` | Doctor-specific endpoints |
| florence | `/florence` | AI chat & triage |
| calendar | `/calendar` | Appointments |
| questions | `/questions` | Daily check-in |
| symptom | `/symptom-questionnaire` | Symptom tracking |
| analytics | `/analytics` | Health analytics |
| otp | `/otp` | OTP verification |
| triage | `/triage` | Triage system |

Full interactive docs at `/docs` when running locally.

## Documentation

Additional docs in the project root [`docs/`](../docs/) directory:

- [Symptom Questionnaire Integration](../docs/symptom-questionnaire-integration.md)
- [Dashboard Quiz Integration](../docs/dashboard-quiz-integration.md)
- [Database Structure](../docs/database-structure.md)
- [API Reference](../docs/api-reference.md)
