from fastapi import Depends, FastAPI, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
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
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail


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
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        doctor = payload.get("admin")
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
    if doctor:
        users = db["doctors"]
        user = users.find_one({"username": username}, {"_id": 0, "password": 0})
    else:
        users = db["users"]
        user = users.find_one({"username": username}, {"_id": 0, "password": 0})
    if not user:
        raise credentials_exception
    return jsonable_encoder(user)

def gen_otp():
    otp = ""
    for i in range(4):
        otp+=str(math.floor(random.random()*10))
    return otp

def verify_code(code, user_dict):
    db = get_db()
    doctors = db["doctors"]
    doctor = doctors.find_one({"code":code})
    hospitals = db["hospitals"]
    hospital = hospitals.find_one({"code":code})
    new_user = user_dict.copy()
    new_user.pop("access_code")
    if doctor:
        new_user.update({"isDoctor": False, "doctor": doctor["username"]})
        return new_user
    if hospital:
        new_user.update({"isDoctor": True, "hospital": hospital["name"]})
        return new_user
    raise HTTPException(status_code=400, detail="Invalid access code")

@loginrouter.post("/configure_db")
def startup(db = Depends(get_db)):
    temp_users = db["temp_users"]
    temp_users.drop()
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

    user_dict = verify_code(user.access_code, user_dict)

    message = Mail(
    from_email='no-reply@ovismedical.com',
    to_emails=user_dict["email"],
    subject='One Time Password For Registration',
    html_content='Here is the OTP for registration: ' + otp)
    try:
        sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
        sg.send(message)
    except Exception as e:
        raise(e.message)

    users.insert_one({"user_id" : user.username, "user_dict": user_dict, "otp":hash_password(otp), "createdAt": creation})
    token = create_access_token({"sub": user.username})
    return {"temp_token":token}

@loginrouter.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db = Depends(get_db)):
    user = db["users"].find_one({"username": form_data.username})
    if not user:
        user = db["doctors"].find_one({"username": form_data.username})
        if not user:
            return ({"details":"Invalid credentials"}) 
        if verify_password(form_data.password, user["password"]):
            token = create_access_token({"sub": user["username"]}, admin = True)
            return {"access_token": token, "token_type": "Bearer"}
    if verify_password(form_data.password, user["password"]):
        token = create_access_token({"sub": user["username"]})
        return {"access_token": token, "token_type": "Bearer"}
    return ({"details":"Invalid credentials"}) 

@loginrouter.get("/userinfo")
async def get_info(user = Depends(get_user)):
    return user #aaa

@loginrouter.put("/verify")
async def verify(otp: str, token :str = Depends(oauth2_scheme), db = Depends(get_db)):
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
    if verify_password(otp, user["otp"]):
        if user["user_dict"]["isDoctor"]:
            db["doctors"].insert_one(user["user_dict"])
            users.delete_one({"user_id": username})
            return ({"msg": "Doctor succesfully created. Please login to continue."})
        else:
            db["users"].insert_one(user["user_dict"])
            users.delete_one({"user_id": username})
            return ({"msg": "User succesfully created. Please login to continue."})
    else:
        raise credentials_exception