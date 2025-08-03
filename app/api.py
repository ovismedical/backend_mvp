"""
FastAPI Application Setup
Clean, focused main application file with only app configuration and router registration
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
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

# Health check endpoint
@app.get("/health")
async def health_check():
    """Simple health check endpoint"""
    return {
        "status": "healthy",
        "service": "OVIS Medical Backend",
        "version": "1.0.0"
    }

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

@app.get("/configure_db")
async def configuredb(db = Depends(get_db)):
    auth_states = db["auth_states"]
    auth_states.create_index("expires_at", expireAfterSeconds = 1)
    temp_users = db["temp_users"]
    auth_states.create_index("created_at", expireAfterSeconds = 600)
