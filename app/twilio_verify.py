"""
Twilio Verify Service Integration
Provides secure OTP delivery via email with built-in rate limiting and fraud protection
"""

import os
import logging
from typing import Optional, Dict, Any
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
from fastapi import HTTPException

logger = logging.getLogger(__name__)

class TwilioVerifyService:
    """
    Twilio Verify service wrapper for secure OTP delivery
    Provides enterprise-grade OTP with automatic rate limiting and fraud protection
    """
    
    def __init__(self):
        self.account_sid = os.getenv('TWILIO_ACCOUNT_SID')
        self.auth_token = os.getenv('TWILIO_AUTH_TOKEN')
        self.verify_service_sid = os.getenv('TWILIO_VERIFY_SERVICE_SID')
        
        if not all([self.account_sid, self.auth_token, self.verify_service_sid]):
            logger.warning("Twilio credentials not fully configured. OTP service may not work.")
            self.client = None
        else:
            self.client = Client(self.account_sid, self.auth_token)
            logger.info("Twilio Verify service initialized successfully")
    
    def is_configured(self) -> bool:
        """Check if Twilio is properly configured"""
        return self.client is not None
    
    def send_verification_email(self, email: str, purpose: str = "registration") -> Dict[str, Any]:
        """
        Send OTP verification email using Twilio Verify
        
        Args:
            email: Email address to send OTP to
            purpose: Purpose of verification (registration, password_reset, etc.)
            
        Returns:
            Dict with verification status and details
        """
        if not self.is_configured():
            raise HTTPException(
                status_code=500,
                detail="Twilio Verify service not configured. Please check environment variables."
            )
        
        try:
            # Create verification
            verification = self.client.verify \
                .v2 \
                .services(self.verify_service_sid) \
                .verifications \
                .create(
                    to=email,
                    channel='email',
                    channel_configuration={
                        "template_id": "d-4147d5fb8a7f4e3682f69aeb3bd72f73",
                        "from": "no-reply@ovismedical.com",
                    }
                )
            
            logger.info(f"Verification sent to {email} with SID: {verification.sid}")
            
            return {
                "success": True,
                "verification_sid": verification.sid,
                "status": verification.status,
                "to": verification.to,
                "channel": verification.channel,
                "valid": verification.valid,
                "lookup": verification.lookup
            }
            
        except TwilioRestException as e:
            logger.error(f"Twilio error sending verification to {email}: {e}")
            
            # Handle specific Twilio errors
            if e.code == 60200:  # Invalid parameter
                raise HTTPException(
                    status_code=400,
                    detail="Invalid email address format"
                )
            elif e.code == 60202:  # Max check attempts reached
                raise HTTPException(
                    status_code=429,
                    detail="Too many verification attempts. Please wait before requesting again."
                )
            elif e.code == 60203:  # Max send attempts reached
                raise HTTPException(
                    status_code=429,
                    detail="Too many verification requests. Please wait before requesting again."
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to send verification: {e.msg}"
                )
        
        except Exception as e:
            logger.error(f"Unexpected error sending verification to {email}: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to send verification email"
            )
    
    def verify_code(self, email: str, code: str) -> Dict[str, Any]:
        """
        Verify OTP code using Twilio Verify
        
        Args:
            email: Email address that received the OTP
            code: OTP code to verify
            
        Returns:
            Dict with verification result
        """
        if not self.is_configured():
            raise HTTPException(
                status_code=500,
                detail="Twilio Verify service not configured"
            )
        
        try:
            # Check verification
            verification_check = self.client.verify \
                .v2 \
                .services(self.verify_service_sid) \
                .verification_checks \
                .create(
                    to=email,
                    code=code
                )
            
            logger.info(f"Verification check for {email}: {verification_check.status}")
            
            return {
                "success": verification_check.status == "approved",
                "status": verification_check.status,
                "verification_sid": verification_check.sid,
                "to": verification_check.to,
                "channel": verification_check.channel,
                "valid": verification_check.valid
            }
            
        except TwilioRestException as e:
            logger.error(f"Twilio error verifying code for {email}: {e}")
            
            # Handle specific verification errors
            if e.code == 60200:  # Invalid parameter
                raise HTTPException(
                    status_code=400,
                    detail="Invalid verification code format"
                )
            elif e.code == 60202:  # Max check attempts reached
                raise HTTPException(
                    status_code=429,
                    detail="Too many verification attempts. Please request a new code."
                )
            elif e.code == 60023:  # No pending verifications
                raise HTTPException(
                    status_code=400,
                    detail="No pending verification found. Please request a new code."
                )
            else:
                return {
                    "success": False,
                    "status": "failed",
                    "error": e.msg
                }
        
        except Exception as e:
            logger.error(f"Unexpected error verifying code for {email}: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to verify code"
            )
    
    def cancel_verification(self, email: str) -> bool:
        """
        Cancel any pending verification for an email
        
        Args:
            email: Email address to cancel verification for
            
        Returns:
            bool: Success status
        """
        if not self.is_configured():
            return False
        
        try:
            # Note: Twilio doesn't have a direct cancel API
            # Verifications automatically expire after 10 minutes
            # This is mainly for logging purposes
            logger.info(f"Verification cancellation requested for {email}")
            return True
            
        except Exception as e:
            logger.error(f"Error cancelling verification for {email}: {e}")
            return False
    
    def get_verification_status(self, verification_sid: str) -> Optional[Dict[str, Any]]:
        """
        Get the status of a verification by SID
        
        Args:
            verification_sid: Twilio verification SID
            
        Returns:
            Dict with verification details or None if not found
        """
        if not self.is_configured():
            return None
        
        try:
            verification = self.client.verify \
                .v2 \
                .services(self.verify_service_sid) \
                .verifications(verification_sid) \
                .fetch()
            
            return {
                "sid": verification.sid,
                "status": verification.status,
                "to": verification.to,
                "channel": verification.channel,
                "valid": verification.valid,
                "date_created": verification.date_created,
                "date_updated": verification.date_updated
            }
            
        except TwilioRestException as e:
            logger.error(f"Error fetching verification {verification_sid}: {e}")
            return None
        
        except Exception as e:
            logger.error(f"Unexpected error fetching verification {verification_sid}: {e}")
            return None
    
    def _get_custom_message(self, purpose: str) -> str:
        """Get custom message template based on purpose"""
        templates = {
            "registration": "Welcome to OVIS Medical! Your verification code is: {{CODE}}",
            "password_reset": "OVIS Medical password reset code: {{CODE}}",
            "login_verification": "OVIS Medical login verification code: {{CODE}}",
            "account_verification": "OVIS Medical account verification code: {{CODE}}"
        }
        
        return templates.get(purpose, "Your OVIS Medical verification code is: {{CODE}}")


# Global service instance
_twilio_verify_service = None

def get_twilio_verify_service() -> TwilioVerifyService:
    """
    Get the global Twilio Verify service instance
    Creates singleton instance on first call
    """
    global _twilio_verify_service
    
    if _twilio_verify_service is None:
        _twilio_verify_service = TwilioVerifyService()
    
    return _twilio_verify_service


# FastAPI dependency for injection
def get_verify_service() -> TwilioVerifyService:
    """FastAPI dependency to inject Twilio Verify service"""
    return get_twilio_verify_service()