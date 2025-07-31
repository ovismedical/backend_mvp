from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import os
from .login import get_db, UserCreate, hash_password, verify_code
from .otp_system import OTPManager, OTPRequest, OTPVerification, OTPStatus
from datetime import datetime, timezone

otprouter = APIRouter(prefix="/otp", tags=["otp"])

def get_otp_manager(db = Depends(get_db)) -> OTPManager:
    """Dependency to get OTP manager instance"""
    return OTPManager(db)

def send_otp_email(email: str, otp_code: str, purpose: str = "registration"):
    """Send OTP via email using SendGrid"""
    try:
        sg = SendGridAPIClient(api_key=os.getenv('SENDGRID_API_KEY'))
        
        subject_map = {
            "registration": "Complete Your Registration - OTP Code",
            "password_reset": "Password Reset - OTP Code",
            "login_verification": "Login Verification - OTP Code"
        }
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #333;">Your OTP Code</h2>
            <p>Your one-time password (OTP) code is:</p>
            <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; margin: 20px 0;">
                <h1 style="color: #007bff; font-size: 32px; margin: 0; letter-spacing: 5px;">{otp_code}</h1>
            </div>
            <p><strong>Important:</strong></p>
            <ul>
                <li>This code expires in 10 minutes</li>
                <li>Do not share this code with anyone</li>
                <li>If you didn't request this code, please ignore this email</li>
            </ul>
            <p style="color: #666; font-size: 14px;">
                This is an automated message from OVIS Medical. Please do not reply to this email.
            </p>
        </div>
        """
        
        message = Mail(
            from_email='no-reply@ovismedical.com',
            to_emails=email,
            subject=subject_map.get(purpose, "OTP Verification Code"),
            html_content=html_content
        )
        
        response = sg.send(message)
        return response.status_code == 202
        
    except Exception as e:
        print(f"Failed to send OTP email: {e}")
        return False

@otprouter.post("/register")
async def register_with_otp(
    user: UserCreate, 
    background_tasks: BackgroundTasks,
    db = Depends(get_db),
    otp_manager: OTPManager = Depends(get_otp_manager)
):
    """
    Register user and send OTP for verification
    Replaces the original register endpoint with improved OTP flow
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
        
        # Generate and send OTP
        otp_code = otp_manager.create_otp(
            user_id=user.username,
            email=user.email,
            purpose="registration"
        )
        
        # Send email in background
        background_tasks.add_task(send_otp_email, user.email, otp_code, "registration")
        
        return {
            "message": "Registration initiated. Please check your email for the OTP code.",
            "user_id": user.username,
            "email": user.email,
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
    otp_manager: OTPManager = Depends(get_otp_manager)
):
    """
    Verify OTP and complete user registration
    """
    try:
        # Verify OTP
        is_valid = otp_manager.verify_otp(
            user_id=verification.user_id,
            otp_code=verification.otp_code,
            purpose=verification.purpose
        )
        
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail="Invalid OTP code. Please check and try again."
            )
        
        # Get temporary user data
        temp_users_collection = db["temp_users"]
        temp_user = temp_users_collection.find_one({"user_id": verification.user_id})
        
        if not temp_user:
            raise HTTPException(
                status_code=400,
                detail="Registration session expired. Please register again."
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
            "username": verification.user_id
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
    background_tasks: BackgroundTasks,
    db = Depends(get_db),
    otp_manager: OTPManager = Depends(get_otp_manager)
):
    """
    Resend OTP code to user
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
        
        # Generate new OTP
        otp_code = otp_manager.resend_otp(
            user_id=request.user_id,
            email=request.email,
            purpose=request.purpose
        )
        
        # Send email in background
        background_tasks.add_task(send_otp_email, request.email, otp_code, request.purpose)
        
        return {
            "message": "New OTP sent to your email address.",
            "user_id": request.user_id,
            "expires_in_minutes": 10
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resend OTP: {str(e)}"
        )

@otprouter.get("/status/{user_id}")
async def get_otp_status(
    user_id: str,
    purpose: str = "registration",
    otp_manager: OTPManager = Depends(get_otp_manager)
):
    """
    Get OTP status for user
    """
    try:
        status = otp_manager.get_otp_status(user_id, purpose)
        
        if not status:
            return {
                "exists": False,
                "message": "No active OTP found for this user."
            }
        
        return {
            "exists": True,
            "expires_at": status["expires_at"],
            "attempts_remaining": status["attempts_remaining"],
            "created_at": status["created_at"],
            "time_remaining_minutes": max(0, int((status["expires_at"] - datetime.utcnow()).total_seconds() / 60))
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get OTP status: {str(e)}"
        )

@otprouter.delete("/cleanup")
async def cleanup_expired_otps(
    db = Depends(get_db),
    otp_manager: OTPManager = Depends(get_otp_manager)
):
    """
    Admin endpoint to clean up expired OTPs
    """
    try:
        deleted_count = otp_manager.cleanup_expired_otps()
        return {
            "message": f"Cleaned up {deleted_count} expired OTP codes.",
            "deleted_count": deleted_count
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cleanup failed: {str(e)}"
        )