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
python -m uvicorn app.api:app --reload --port 8000
```

API docs available at http://localhost:8000/docs

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
- `OPENAI_API_KEY` - OpenAI API key for Florence AI
- `SENDGRID_API_KEY` - SendGrid for email
- `CALENDAR_ENCRYPTION_KEY` - Calendar data encryption

## API Endpoints

The backend exposes 47 endpoints across these routers:

| Router | Prefix | Purpose |
|--------|--------|---------|
| login | `/token`, `/userinfo`, `/updateinfo` | Auth & user management |
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
