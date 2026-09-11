"""
FastAPI Application Setup
App configuration, middleware, and router registration.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

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
from .inference.audit import MongoAuditSink, ensure_audit_indexes  # noqa: E402
from .florence import ensure_session_index, wait_for_background  # noqa: E402
from .florence_memory import ensure_memory_indexes  # noqa: E402
from .florence_utils import pending_review_fields  # noqa: E402
from .doctor import ensure_review_indexes  # noqa: E402


def cors_origins() -> list[str]:
    """Comma-separated CORS_ORIGINS; '*' when unset so existing deployments keep working."""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw:
        logger.warning("CORS_ORIGINS not set - allowing all origins")
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# A background analysis cannot outlive its process, so anything still "generating" when we start
# belongs to a process that was killed (SIGKILL, OOM, redeploy). Left alone the record is invisible
# to the clinician and "still processing" to the patient for ever.
ORPHAN_ANALYSIS_AGE = timedelta(minutes=10)
SHUTDOWN_GRACE_S = 60


def sweep_interrupted_analyses(db, *, now=None) -> int:
    """Move analyses orphaned by a previous process to pending_clinician_review. Best effort."""
    cutoff = ((now or datetime.now(timezone.utc)) - ORPHAN_ANALYSIS_AGE).isoformat()
    try:
        result = db["florence_assessments"].update_many(
            {"triage_status": "generating", "created_at": {"$lt": cutoff}},
            {"$set": pending_review_fields("interrupted")},
        )
        swept = int(getattr(result, "modified_count", 0) or 0)
    except Exception as e:  # noqa: BLE001 - a startup sweep must never stop the app booting
        logger.warning("could not sweep interrupted analyses: %s", type(e).__name__)
        return 0
    if swept:
        logger.warning("%d interrupted analyses moved to clinician review", swept)
    return swept


def configure_gateway_audit(db) -> None:
    """Point the (lazy) gateway's audit trail at Mongo. The sink swallows its own errors, so an
    unreachable database degrades to the content-free log line rather than failing a call."""
    get_gateway().audit_sink = MongoAuditSink(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    gateway = get_gateway()
    if gateway.available():
        logger.info("inference ready: %s", gateway.describe())
    else:
        logger.warning("no inference provider configured - Florence runs in fallback mode")
    gateway.log_policy_state()  # e.g. "inference policy dpa_ok=false: openai-routed tasks will refuse"
    try:
        db = get_db()
        ensure_session_index(db)
        ensure_memory_indexes(db)
        ensure_review_indexes(db)
        ensure_audit_indexes(db)
        configure_gateway_audit(db)
        sweep_interrupted_analyses(db)
    except Exception as e:
        logger.warning("could not prepare database indexes or the audit sink: %s", type(e).__name__)
    yield
    try:
        # Let in-flight analyses finish on a graceful SIGTERM rather than being cancelled mid-call.
        await asyncio.wait_for(wait_for_background(), timeout=SHUTDOWN_GRACE_S)
    except Exception as e:  # noqa: BLE001 - includes TimeoutError; shutdown must not raise
        logger.warning("background analyses did not finish before shutdown: %s", type(e).__name__)


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
from .memories import memoriesrouter  # noqa: E402

for router in (loginrouter, doctorrouter, questionsrouter, florencerouter, calendarrouter, otprouter,
               analyticsrouter, trierouter, symptom_router, adminrouter, achievementsrouter, memoriesrouter):
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
    ensure_memory_indexes(db)
    ensure_review_indexes(db)
    ensure_audit_indexes(db)
    return {"message": "Database indexes configured successfully"}
