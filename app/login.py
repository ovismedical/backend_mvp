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
from pymongo import MongoClient
from dotenv import load_dotenv
from typing import Annotated


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

class UserCreate(BaseModel):
    username: Annotated[str, Field(min_length = 4)]
    password: Annotated[str, Field(min_length = 4)]
    email: EmailStr
    full_name: str
    sex: str
    dob: str

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

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
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
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

@loginrouter.post("/register")
def register(user: UserCreate, db = Depends(get_db)):
    users = db["users"]
    if users.find_one({"username": user.username}):
        raise HTTPException(status_code=400, detail="User already exists")
    hashed = hash_password(user.password)
    
    # Convert to dict and add additional fields
    user_dict = user.dict()
    user_dict["password"] = hashed
    user_dict["streak"] = 0
    user_dict["last_answer"] = -1
    
    users.insert_one(user_dict)
    return {"msg": "User created"}

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