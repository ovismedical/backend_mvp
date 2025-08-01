from fastapi import APIRouter, Depends, HTTPException
from .login import get_db, UserCreate, hash_password, verify_code
from .twilio_verify import get_verify_service, TwilioVerifyService
from datetime import datetime, timezone
from pydantic import BaseModel, EmailStr

otprouter = APIRouter(prefix="/otp", tags=["otp"])

# Pydantic models for Twilio Verify OTP
class OTPRequest(BaseModel):
    user_id: str
    email: EmailStr
    purpose: str = "registration"

class OTPVerification(BaseModel):
    user_id: str
    email: EmailStr
    otp_code: str
    purpose: str = "registration"

@otprouter.post("/register")
async def register_with_otp(
    user: UserCreate, 
    db = Depends(get_db),
    verify_service: TwilioVerifyService = Depends(get_verify_service)
):
    """
    Register user and send OTP via Twilio Verify
    Uses Twilio's enterprise-grade OTP service with built-in rate limiting
    """
    try:
        # Check if user already exists
        users_collection = db["users"]
        doctors_collection = db["doctors"]
        temp_users_collection = db["temp_users"]
        
        if (users_collection.find_one({"username": user.username}) or 
            doctors_collection.find_one({"username": user.username}) or
            temp_users_collection.find_one({"user_id": user.username})):
            raise HTTPException(status_code=400, detail="User already exists")
        
        # Verify access code and prepare user data
        hashed_password = hash_password(user.password)
        user_dict = user.dict()
        user_dict["password"] = hashed_password
        user_dict = verify_code(user.access_code, user_dict)
        
        # Store user data temporarily
        temp_user_doc = {
            "user_id": user.username,
            "user_dict": user_dict,
            "created_at": datetime.utcnow(),
            "email": user.email
        }
        
        temp_users_collection.insert_one(temp_user_doc)
        
        # Send OTP via Twilio Verify
        verification_result = verify_service.send_verification_email(
            email=user.email,
            purpose="registration"
        )
        
        return {
            "message": "Registration initiated. Please check your email for the OTP code.",
            "user_id": user.username,
            "email": user.email,
            "verification_sid": verification_result["verification_sid"],
            "status": verification_result["status"],
            "expires_in_minutes": 10
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Registration failed: {str(e)}"
        )

@otprouter.post("/verify")
async def verify_otp(
    verification: OTPVerification,
    db = Depends(get_db),
    verify_service: TwilioVerifyService = Depends(get_verify_service)
):
    """
    Verify OTP using Twilio Verify and complete user registration
    """
    try:
        # Verify OTP with Twilio
        verification_result = verify_service.verify_code(
            email=verification.email,
            code=verification.otp_code
        )
        
        if not verification_result["success"]:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid OTP code: {verification_result.get('status', 'verification failed')}"
            )
        
        # Get temporary user data
        temp_users_collection = db["temp_users"]
        temp_user = temp_users_collection.find_one({"user_id": verification.user_id})
        
        if not temp_user:
            raise HTTPException(
                status_code=400,
                detail="Registration session expired. Please register again."
            )
        
        # Verify email matches
        if temp_user["email"] != verification.email:
            raise HTTPException(
                status_code=400,
                detail="Email mismatch. Please use the same email address used for registration."
            )
        
        user_dict = temp_user["user_dict"]
        
        # Move user to appropriate collection
        if user_dict.get("isDoctor", False):
            db["doctors"].insert_one(user_dict)
            user_type = "Doctor"
        else:
            db["users"].insert_one(user_dict)
            user_type = "User"
        
        # Clean up temporary data
        temp_users_collection.delete_one({"user_id": verification.user_id})
        
        return {
            "message": f"{user_type} successfully created. Please login to continue.",
            "user_type": user_type.lower(),
            "username": verification.user_id,
            "email": verification.email,
            "verification_status": verification_result["status"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Verification failed: {str(e)}"
        )

@otprouter.post("/resend")
async def resend_otp(
    request: OTPRequest,
    db = Depends(get_db),
    verify_service: TwilioVerifyService = Depends(get_verify_service)
):
    """
    Resend OTP code using Twilio Verify
    """
    try:
        # Check if user has pending registration
        temp_users_collection = db["temp_users"]
        temp_user = temp_users_collection.find_one({"user_id": request.user_id})
        
        if not temp_user:
            raise HTTPException(
                status_code=400,
                detail="No pending registration found for this user."
            )
        
        # Verify email matches
        if temp_user["email"] != request.email:
            raise HTTPException(
                status_code=400,
                detail="Email mismatch. Please use the same email address used for registration."
            )
        
        # Send new OTP via Twilio Verify
        verification_result = verify_service.send_verification_email(
            email=request.email,
            purpose=request.purpose
        )
        
        return {
            "message": "New OTP sent to your email address.",
            "user_id": request.user_id,
            "email": request.email,
            "verification_sid": verification_result["verification_sid"],
            "status": verification_result["status"],
            "expires_in_minutes": 10
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resend OTP: {str(e)}"
        )