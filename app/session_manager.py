"""
Database-backed session management to replace global dictionary
Simple, reliable, and compatible with existing MongoDB setup
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
import secrets
import logging
from pymongo.database import Database
from .login import get_db
from fastapi import Depends

logger = logging.getLogger(__name__)

class SessionManager:
    """MongoDB-backed session management for Florence AI sessions"""
    
    def __init__(self, db: Database, collection_name: str = "florence_sessions"):
        self.db = db
        self.collection = db[collection_name]
        self.default_ttl_seconds = 30 * 60  # 30 minutes
        self._ensure_indexes()
    
    def _ensure_indexes(self):
        """Create necessary indexes for performance and TTL"""
        try:
            # Index for fast session lookup
            self.collection.create_index("session_id", unique=True)
            
            # Index for user session queries
            self.collection.create_index("user_id")
            
            # TTL index for automatic expiration
            self.collection.create_index("expires_at", expireAfterSeconds=0)
            
            logger.info("Session manager indexes created successfully")
        except Exception as e:
            logger.error(f"Failed to create session indexes: {e}")
    
    def generate_session_id(self) -> str:
        """Generate a cryptographically secure session ID"""
        return secrets.token_urlsafe(32)
    
    def create_session(self, user_id: str, initial_data: Dict[str, Any] = None) -> str:
        """
        Create a new session and return session ID
        
        Args:
            user_id: User identifier
            initial_data: Optional initial session data
            
        Returns:
            str: Generated session ID
        """
        try:
            session_id = self.generate_session_id()
            now = datetime.utcnow()
            expires_at = now + timedelta(seconds=self.default_ttl_seconds)
            
            session_doc = {
                "session_id": session_id,
                "user_id": user_id,
                "created_at": now.isoformat() + 'Z',
                "updated_at": now.isoformat() + 'Z',
                "expires_at": expires_at,
                "conversation_history": [],
                "ai_powered": True,
                "status": "active"
            }
            
            # Add any initial data
            if initial_data:
                session_doc.update(initial_data)
            
            result = self.collection.insert_one(session_doc)
            
            if result.inserted_id:
                logger.info(f"Created session {session_id} for user {user_id}")
                return session_id
            else:
                raise RuntimeError("Failed to insert session document")
                
        except Exception as e:
            logger.error(f"Failed to create session for user {user_id}: {e}")
            raise RuntimeError(f"Session creation failed: {str(e)}")
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session data by session ID
        Automatically removes expired sessions
        """
        try:
            session_doc = self.collection.find_one({"session_id": session_id})
            
            if not session_doc:
                return None
            
            # Check if session has expired (manual check as backup to TTL)
            if session_doc.get("expires_at"):
                if datetime.utcnow() > session_doc["expires_at"]:
                    # Clean up expired session
                    self.collection.delete_one({"session_id": session_id})
                    logger.info(f"Removed expired session: {session_id}")
                    return None
            
            return session_doc
            
        except Exception as e:
            logger.error(f"Failed to get session {session_id}: {e}")
            return None
    
    def update_session(self, session_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update session data and refresh expiration
        
        Args:
            session_id: Session to update
            updates: Data to update
            
        Returns:
            bool: Success status
        """
        try:
            # Refresh expiration time
            new_expires_at = datetime.utcnow() + timedelta(seconds=self.default_ttl_seconds)
            
            update_doc = {
                "$set": {
                    **updates,
                    "updated_at": datetime.utcnow().isoformat() + 'Z',
                    "expires_at": new_expires_at
                }
            }
            
            result = self.collection.update_one(
                {"session_id": session_id},
                update_doc
            )
            
            success = result.matched_count > 0
            if success:
                logger.debug(f"Updated session {session_id}")
            else:
                logger.warning(f"Session {session_id} not found for update")
                
            return success
            
        except Exception as e:
            logger.error(f"Failed to update session {session_id}: {e}")
            return False
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session"""
        try:
            result = self.collection.delete_one({"session_id": session_id})
            success = result.deleted_count > 0
            
            if success:
                logger.info(f"Deleted session {session_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to delete session {session_id}: {e}")
            return False
    
    def add_message(self, session_id: str, message: Dict[str, Any]) -> bool:
        """Add a message to session conversation history"""
        try:
            # Add timestamp to message
            message_with_timestamp = {
                **message,
                "timestamp": datetime.utcnow().isoformat() + 'Z'
            }
            
            # Refresh expiration time
            new_expires_at = datetime.utcnow() + timedelta(seconds=self.default_ttl_seconds)
            
            result = self.collection.update_one(
                {"session_id": session_id},
                {
                    "$push": {"conversation_history": message_with_timestamp},
                    "$set": {
                        "updated_at": datetime.utcnow().isoformat() + 'Z',
                        "expires_at": new_expires_at
                    }
                }
            )
            
            success = result.matched_count > 0
            if not success:
                logger.warning(f"Failed to add message to session {session_id} - session not found")
            
            return success
            
        except Exception as e:
            logger.error(f"Failed to add message to session {session_id}: {e}")
            return False
    
    def get_user_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all active sessions for a user"""
        try:
            sessions = list(self.collection.find(
                {"user_id": user_id},
                {"_id": 0}  # Exclude MongoDB ObjectId
            ).sort("created_at", -1))
            
            return sessions
            
        except Exception as e:
            logger.error(f"Failed to get sessions for user {user_id}: {e}")
            return []
    
    def validate_session_access(self, session_id: str, user_id: str) -> bool:
        """Validate that a user has access to a session"""
        try:
            session = self.collection.find_one({
                "session_id": session_id,
                "user_id": user_id
            })
            
            return session is not None
            
        except Exception as e:
            logger.error(f"Failed to validate session access {session_id} for user {user_id}: {e}")
            return False
    
    def extend_session(self, session_id: str, additional_seconds: int = None) -> bool:
        """Extend session expiration time"""
        try:
            if additional_seconds is None:
                additional_seconds = self.default_ttl_seconds
            
            new_expires_at = datetime.utcnow() + timedelta(seconds=additional_seconds)
            
            result = self.collection.update_one(
                {"session_id": session_id},
                {
                    "$set": {
                        "expires_at": new_expires_at,
                        "updated_at": datetime.utcnow().isoformat() + 'Z'
                    }
                }
            )
            
            return result.matched_count > 0
            
        except Exception as e:
            logger.error(f"Failed to extend session {session_id}: {e}")
            return False
    
    def cleanup_expired_sessions(self) -> int:
        """
        Manual cleanup of expired sessions (backup to TTL index)
        Returns count of cleaned up sessions
        """
        try:
            result = self.collection.delete_many({
                "expires_at": {"$lt": datetime.utcnow()}
            })
            
            count = result.deleted_count
            if count > 0:
                logger.info(f"Manually cleaned up {count} expired sessions")
            
            return count
            
        except Exception as e:
            logger.error(f"Failed to cleanup expired sessions: {e}")
            return 0
    
    def get_session_stats(self) -> Dict[str, Any]:
        """Get statistics about active sessions"""
        try:
            pipeline = [
                {
                    "$group": {
                        "_id": None,
                        "total_sessions": {"$sum": 1},
                        "unique_users": {"$addToSet": "$user_id"}
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "total_sessions": 1,
                        "unique_user_count": {"$size": "$unique_users"}
                    }
                }
            ]
            
            result = list(self.collection.aggregate(pipeline))
            
            if result:
                return result[0]
            else:
                return {"total_sessions": 0, "unique_user_count": 0}
                
        except Exception as e:
            logger.error(f"Failed to get session stats: {e}")
            return {"total_sessions": 0, "unique_user_count": 0, "error": str(e)}


# Dependency injection for FastAPI
def get_session_manager(db = Depends(get_db)) -> SessionManager:
    """
    Dependency to get session manager instance
    Use this in your FastAPI dependencies
    """
    return SessionManager(db)