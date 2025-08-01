from fastapi import HTTPException
from datetime import datetime, timedelta, timezone
import secrets
import string
from typing import Optional
import hashlib
from collections import defaultdict
import time

class OTPManager:
    """Improved OTP management system with security features"""
    
    def __init__(self, db):
        self.db = db
        self.otp_collection = db["otp_codes"]
        self.attempt_tracker = defaultdict(list)  # Track failed attempts per user
        
    def generate_otp(self, length: int = 6) -> str:
        """Generate cryptographically secure numeric OTP"""
        digits = string.digits
        return ''.join(secrets.choice(digits) for _ in range(length))
    
    def create_otp(self, user_id: str, email: str, purpose: str = "registration", 
                   expiry_minutes: int = 10) -> str:
        """
        Create and store OTP for user
        
        Args:
            user_id: Unique identifier for the user
            email: User's email address
            purpose: Purpose of OTP (registration, password_reset, etc.)
            expiry_minutes: OTP validity in minutes
        """
        # Check rate limiting - max 3 OTPs per user per hour
        recent_otps = self.otp_collection.count_documents({
            "user_id": user_id,
            "created_at": {"$gte": datetime.utcnow() - timedelta(hours=1)}
        })
        
        if recent_otps >= 3:
            raise HTTPException(
                status_code=429,
                detail="Too many OTP requests. Please wait before requesting again."
            )
        
        # Invalidate any existing OTPs for this user and purpose
        self.otp_collection.delete_many({
            "user_id": user_id,
            "purpose": purpose
        })
        
        # Generate new OTP
        otp_code = self.generate_otp()
        expiry_time = datetime.utcnow() + timedelta(minutes=expiry_minutes)
        
        # Store OTP (plaintext for verification, hashed for logs)
        otp_doc = {
            "user_id": user_id,
            "email": email,
            "otp_code": otp_code,  # Store plaintext for easy verification
            "otp_hash": hashlib.sha256(otp_code.encode()).hexdigest(),  # For audit logs
            "purpose": purpose,
            "created_at": datetime.utcnow(),
            "expires_at": expiry_time,
            "verified": False,
            "attempts": 0,
            "max_attempts": 5
        }
        
        self.otp_collection.insert_one(otp_doc)
        return otp_code
    
    def verify_otp(self, user_id: str, otp_code: str, purpose: str = "registration") -> bool:
        """
        Verify OTP with rate limiting and security checks
        
        Args:
            user_id: User identifier
            otp_code: OTP code to verify
            purpose: Purpose of verification
            
        Returns:
            bool: True if OTP is valid, False otherwise
        """
        # Check attempt rate limiting (max 5 attempts per minute per user)
        current_time = time.time()
        user_attempts = self.attempt_tracker[user_id]
        
        # Remove attempts older than 1 minute
        user_attempts[:] = [attempt_time for attempt_time in user_attempts 
                          if current_time - attempt_time < 60]
        
        if len(user_attempts) >= 5:
            raise HTTPException(
                status_code=429,
                detail="Too many verification attempts. Please wait 1 minute."
            )
        
        # Find the OTP
        otp_doc = self.otp_collection.find_one({
            "user_id": user_id,
            "purpose": purpose,
            "verified": False
        })
        
        if not otp_doc:
            user_attempts.append(current_time)
            raise HTTPException(
                status_code=400,
                detail="No valid OTP found. Please request a new one."
            )
        
        # Check if OTP has expired
        if datetime.utcnow() > otp_doc["expires_at"]:
            self.otp_collection.delete_one({"_id": otp_doc["_id"]})
            raise HTTPException(
                status_code=400,
                detail="OTP has expired. Please request a new one."
            )
        
        # Check attempt limit for this OTP
        if otp_doc["attempts"] >= otp_doc["max_attempts"]:
            self.otp_collection.delete_one({"_id": otp_doc["_id"]})
            raise HTTPException(
                status_code=400,
                detail="Maximum verification attempts exceeded. Please request a new OTP."
            )
        
        # Increment attempt counter
        self.otp_collection.update_one(
            {"_id": otp_doc["_id"]},
            {"$inc": {"attempts": 1}}
        )
        
        # Verify OTP
        if otp_doc["otp_code"] == otp_code:
            # Mark as verified and clean up
            self.otp_collection.update_one(
                {"_id": otp_doc["_id"]},
                {"$set": {"verified": True, "verified_at": datetime.utcnow()}}
            )
            return True
        else:
            user_attempts.append(current_time)
            return False
    
    def resend_otp(self, user_id: str, email: str, purpose: str = "registration") -> str:
        """
        Resend OTP for user (creates new OTP, invalidates old)
        """
        # Check if user can request new OTP (minimum 30 seconds between requests)
        recent_otp = self.otp_collection.find_one({
            "user_id": user_id,
            "purpose": purpose,
            "created_at": {"$gte": datetime.utcnow() - timedelta(seconds=30)}
        })
        
        if recent_otp:
            raise HTTPException(
                status_code=429,
                detail="Please wait 30 seconds before requesting a new OTP."
            )
        
        return self.create_otp(user_id, email, purpose)
    
    def cleanup_expired_otps(self):
        """Clean up expired OTPs (call this periodically)"""
        result = self.otp_collection.delete_many({
            "expires_at": {"$lt": datetime.utcnow()}
        })
        return result.deleted_count
    
    def get_otp_status(self, user_id: str, purpose: str = "registration") -> Optional[dict]:
        """Get current OTP status for user"""
        otp_doc = self.otp_collection.find_one({
            "user_id": user_id,
            "purpose": purpose,
            "verified": False
        })
        
        if not otp_doc:
            return None
            
        return {
            "exists": True,
            "expires_at": otp_doc["expires_at"],
            "attempts_remaining": otp_doc["max_attempts"] - otp_doc["attempts"],
            "created_at": otp_doc["created_at"]
        }

# Pydantic models for API
from pydantic import BaseModel, EmailStr

class OTPRequest(BaseModel):
    user_id: str
    email: EmailStr
    purpose: str = "registration"

class OTPVerification(BaseModel):
    user_id: str
    otp_code: str
    purpose: str = "registration"

class OTPStatus(BaseModel):
    exists: bool
    expires_at: Optional[datetime] = None
    attempts_remaining: Optional[int] = None
    created_at: Optional[datetime] = None