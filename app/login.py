from fastapi import Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
import os
from datetime import datetime, timezone, timedelta
from pymongo import MongoClient
from dotenv import load_dotenv
from typing import Annotated, Optional, List


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

class UserInfo(BaseModel):
    full_name:str
    birthdate:str
    gender: Optional[str]
    height: int
    weight: int
    bloodtype: Optional[str]
    fitness_level: int
    exercises : List[str]
    checkups : Optional[str]



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



@loginrouter.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db = Depends(get_db)):
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
    
@loginrouter.post("/updateinfo")
async def updateinfo(info: UserInfo, user = Depends(get_user), db = Depends(get_db)):
    db["users"].update_one({"username":user["username"]}, {"$set":info.dict()})
    return ({"details": "Succesfully updated user info"})

@loginrouter.get("/userinfo")
async def get_info(user = Depends(get_user)):
    return user

