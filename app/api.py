"""
FastAPI Application Setup
Clean, focused main application file with only app configuration and router registration
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from datetime import datetime, timezone
import os
import asyncio
import time
from pymongo import MongoClient
from .florence import florence_ai, initialize_florence, periodic_cleanup
from .login import get_db

# Load environment variables
load_dotenv()

# Create FastAPI application
app = FastAPI(
    title="OVIS Medical Backend",
    description="Medical application backend with Florence AI, analytics, and patient management",
    version="1.0.0"
)

# Configure CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and register routers
from .login import loginrouter
from .doctor import doctorrouter
from .questions import questionsrouter
from .florence import florencerouter
from .calendar import calendarrouter
from .otp_routes import otprouter
from .analytics import analyticsrouter

# Register all routers
app.include_router(loginrouter)
app.include_router(doctorrouter)
app.include_router(questionsrouter)
app.include_router(florencerouter)
app.include_router(calendarrouter)
app.include_router(otprouter)
app.include_router(analyticsrouter)

# In app/florence.py - Replace the startup event
@florencerouter.on_event("startup")
async def startup_florence():
    """Initialize background tasks without blocking startup"""
    # Start cleanup task immediately
    asyncio.create_task(periodic_cleanup())
    
    # Initialize Florence AI in background
    asyncio.create_task(background_florence_init())

async def background_florence_init():
    """Initialize Florence AI in background"""
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        success = await initialize_florence(api_key)
        if success:
            print("✅ Florence AI initialized successfully")
        else:
            print("❌ Florence AI initialization failed")
    else:
        print("⚠️ No OpenAI API key found - Florence will use fallback responses")

# Health check endpoint
@app.get("/health")
async def health_check():
    """Enhanced health check with dependency status"""
    # Basic health - always return quickly
    health_status = {
        "status": "healthy",
        "service": "OVIS Medical Backend",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Add optional dependency checks (non-blocking)
    try:
        # Quick MongoDB ping (with timeout)
        db = get_db()
        db.admin.command('ping')
        health_status["database"] = "connected"
    except:
        health_status["database"] = "disconnected"
    
    # Florence AI status (check if initialized, don't initialize)
    health_status["florence_ai"] = "ready" if florence_ai.client else "initializing"
    
    return health_status

# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "OVIS Medical Backend API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc"
    }

@app.get("/render-health")
async def render_health_check():
    """Ultra-lightweight health check for Render monitoring"""
    return {"status": "ok"}

@app.get("/configure_db")
async def configure_db(db = Depends(get_db)):
    """Configure database indexes for TTL collections"""
    auth_states = db["auth_states"]
    auth_states.create_index("expires_at", expireAfterSeconds=1)
    temp_users = db["temp_users"]
    temp_users.create_index("created_at", expireAfterSeconds=600)
    return {"message": "Database indexes configured successfully"}