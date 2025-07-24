from fastapi import Depends, FastAPI, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
import re
import json 
import os
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient, ASCENDING
from dotenv import load_dotenv
from typing import Annotated
import random
import math


# Load .env from current directory
load_dotenv()

loginrouter = APIRouter(tags = ["login"])

MONGODB_URI = os.getenv("MONGODB_URI")

def get_db():
    client = MongoClient(MONGODB_URI)
    db = client["ovis-demo"]
    return db

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

class UserCreate(BaseModel):
    username: Annotated[str, Field(min_length = 4)]
    access_code: Annotated[str, Field(min_length = 4, max_length = 4)]
    password: Annotated[str, Field(min_length = 4)]
    email: EmailStr

def verify_password(password, hashed_password):
    return pwd_context.verify(password, hashed_password)

def hash_password(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, admin = False):
    to_encode = data.copy()
    expire = datetime.now(tz = timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    to_encode.update({"admin": admin})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_user (token: str = Depends(oauth2_scheme), db = Depends(get_db)):
    users = db["users"]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        temp = payload.get("exp")
        if username is None:
            raise credentials_exception
        if temp is None:
            raise credentials_exception
        exp = datetime.fromtimestamp(temp, tz=timezone.utc)
        if exp < datetime.now(tz = timezone.utc):
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = users.find_one({"username": username}, {"_id": 0, "password": 0})
    if not user:
        raise credentials_exception
    return user

def gen_otp():
    otp = 0
    for i in range(4):
        otp+=(math.floor(random.random()*10)*(10**i))
    return otp

def verify_code(code):
    db = get_db()
    doctors = db["doctors"]
    doctor = doctors.find_one({"code":code})
    if not doctor:
        return("N/A")
    return doctor["email"]

@loginrouter.post("/configure_db")
def startup(db = Depends(get_db)):
    temp_users = db["temp_users"]
    temp_users.create_index(
        [("createdAt", ASCENDING)],
        expireAfterSeconds=300 
    )
    return "success"

@loginrouter.post("/register")
def register(user: UserCreate, db = Depends(get_db)):
    users = db["temp_users"]
    registered = db["users"]
    if users.find_one({"user_id": user.username}) or registered.find_one({"username":user.username}):
        raise HTTPException(status_code=400, detail="User already exists")
    hashed = hash_password(user.password)
    
    # Convert to dict and add additional fields
    user_dict = user.dict()
    user_dict["password"] = hashed
    
    otp = gen_otp()

    creation = datetime.now(tz = timezone.utc)

    doctor = verify_code(user.access_code)
    if doctor == "N/A":
        raise HTTPException(status_code=400, detail="Invalid access code")
    
    user_dict.update({"doctor": doctor})

    users.insert_one({"user_id" : user.username, "user_dict": user_dict, "otp":otp, "createdAt": creation})
    token = create_access_token({"sub": user.username})
    return {"temp_token":token}

@loginrouter.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db = Depends(get_db)):
    users = db["users"]
    user = users.find_one({"username": form_data.username})
    if not user or not verify_password(form_data.password, user["password"]):
        return ({"details":"Invalid credentials"}) 
    token = create_access_token({"sub": user["username"]})
    return {"access_token": token, "token_type": "Bearer"}

@loginrouter.get("/userinfo")
async def get_info(user = Depends(get_user)):
    return user

@loginrouter.put("/verify")
async def verify(otp: int, token :str = Depends(oauth2_scheme), db = Depends(get_db)):
    users = db["temp_users"]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        temp = payload.get("exp")
        if username is None:
            raise credentials_exception
        if temp is None:
            raise credentials_exception
        exp = datetime.fromtimestamp(temp, tz=timezone.utc)
        if exp < datetime.now(tz = timezone.utc):
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = users.find_one({"user_id": username})
    if not user:
        raise credentials_exception
    
    if otp == user["otp"]:
        db["users"].insert_one(user["user_dict"])
        return ("success")
    else:
        raise credentials_exception