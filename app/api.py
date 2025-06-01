from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import json 
import os
from datetime import datetime
from pymongo import MongoClient
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .login import loginrouter
from .admin import adminrouter
from .questions import questionsrouter

app.include_router(loginrouter)
app.include_router(adminrouter)
app.include_router(questionsrouter)


