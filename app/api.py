"""
FastAPI Application Setup
App configuration, middleware, and router registration.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ovis")
# Request paths carry usernames (session ids, /doctor/patient/{username}); keep them out of stdout.
logging.getLogger("uvicorn.access").disabled = True

from .login import get_db, get_client, get_user  # noqa: E402
from .inference import get_gateway  # noqa: E402
from .florence import ensure_session_index  # noqa: E402


def cors_origins() -> list[str]:
    """Comma-separated CORS_ORIGINS; '*' when unset so existing deployments keep working."""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw:
        logger.warning("CORS_ORIGINS not set - allowing all origins")
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    gateway = get_gateway()
    if gateway.available():
        logger.info("inference ready: %s", gateway.describe())
    else:
        logger.warning("no inference provider configured - Florence runs in fallback mode")
    try:
        ensure_session_index(get_db())
    except Exception as e:
        logger.warning("could not prepare session indexes: %s", type(e).__name__)
    yield


app = FastAPI(
    title="OVIS Medical Backend",
    description="Medical application backend with Florence AI, analytics, and patient management",
    version="1.1.0",
    lifespan=lifespan,
)

origins = cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from .login import loginrouter  # noqa: E402
from .doctor import doctorrouter  # noqa: E402
from .questions import questionsrouter  # noqa: E402
from .florence import florencerouter  # noqa: E402
from .calendar import calendarrouter  # noqa: E402
from .otp_routes import otprouter  # noqa: E402
from .analytics import analyticsrouter  # noqa: E402
from .triage_api import trierouter  # noqa: E402
from .symptom_questionnaire import symptom_router  # noqa: E402
from .admin import adminrouter  # noqa: E402
from .achievements import achievementsrouter  # noqa: E402

for router in (loginrouter, doctorrouter, questionsrouter, florencerouter, calendarrouter, otprouter,
               analyticsrouter, trierouter, symptom_router, adminrouter, achievementsrouter):
    app.include_router(router)


@app.get("/health")
async def health_check():
    health_status = {
        "status": "healthy",
        "service": "OVIS Medical Backend",
        "version": app.version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        get_client().admin.command("ping")
        health_status["database"] = "connected"
    except Exception:
        health_status["database"] = "disconnected"
    gateway = get_gateway()
    if not gateway.available():
        health_status["florence_ai"] = "fallback"
    elif gateway.decide("chat_turn", scrubbed=True).allow:
        health_status["florence_ai"] = "ready"
    else:
        health_status["florence_ai"] = "refusing"  # providers exist but the routing policy refuses (e.g. dpa_ok=false)
    return health_status


@app.get("/")
async def root():
    return {"message": "OVIS Medical Backend API", "version": app.version, "docs": "/docs", "redoc": "/redoc"}


@app.get("/render-health")
async def render_health_check():
    return {"status": "ok"}


@app.get("/configure_db")
async def configure_db(user=Depends(get_user), db=Depends(get_db)):
    """Create TTL indexes. Clinician-only; safe to re-run."""
    if not user.get("isDoctor"):
        raise HTTPException(status_code=403, detail="Clinician access required")
    db["auth_states"].create_index("expires_at", expireAfterSeconds=1)
    db["temp_users"].create_index("created_at", expireAfterSeconds=600)
    ensure_session_index(db)
    return {"message": "Database indexes configured successfully"}
