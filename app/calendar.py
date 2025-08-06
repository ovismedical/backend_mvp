from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import os
import json
import base64
import secrets
from urllib.parse import urlencode
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .login import get_user, get_db

# Load environment variables
load_dotenv()

calendarrouter = APIRouter(prefix="/calendar", tags=["calendar"])

# Google Calendar API scopes
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Pydantic models for calendar operations
class CalendarEvent(BaseModel):
    summary: str = Field(..., description="Event title")
    description: Optional[str] = Field(None, description="Event description")
    start_datetime: datetime = Field(..., description="Event start time")
    end_datetime: datetime = Field(..., description="Event end time")
    attendees: Optional[List[str]] = Field(default=[], description="List of attendee emails")
    location: Optional[str] = Field(None, description="Event location")
    calendar_id: Optional[str] = Field(default='primary', description="Calendar ID")

class EventUpdate(BaseModel):
    summary: Optional[str] = None
    description: Optional[str] = None
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    attendees: Optional[List[str]] = None
    location: Optional[str] = None

class CalendarEventResponse(BaseModel):
    id: str
    summary: str
    description: Optional[str]
    start_datetime: datetime
    end_datetime: datetime
    attendees: List[str]
    location: Optional[str]
    html_link: str
    status: str

class FreeBlock(BaseModel):
    start_datetime: datetime
    end_datetime: datetime
    duration_minutes: int

class TimeRange(BaseModel):
    start_time: str  # Military time format HHMM (e.g., "0900" for 9:00 AM, "1730" for 5:30 PM)
    end_time: str    # Military time format HHMM

def get_encryption_key():
    """Get or create encryption key for credential storage"""
    key = os.getenv('CALENDAR_ENCRYPTION_KEY')    
    if not key:
        # Generate a new key if none exists
        key = Fernet.generate_key().decode()
    return key.encode() if isinstance(key, str) else key

def encrypt_credentials(creds_json: str) -> str:
    """Encrypt credentials JSON for secure storage"""
    key = get_encryption_key()
    f = Fernet(key)
    encrypted_data = f.encrypt(creds_json.encode())
    return base64.b64encode(encrypted_data).decode()

def decrypt_credentials(encrypted_data: str) -> str:
    """Decrypt credentials from storage"""
    key = get_encryption_key()
    f = Fernet(key)
    decoded_data = base64.b64decode(encrypted_data.encode())
    decrypted_data = f.decrypt(decoded_data)
    return decrypted_data.decode()

def save_credentials_to_db(user_id: str, creds: Credentials, db):
    """Save encrypted credentials to MongoDB"""
    try:
        creds_json = creds.to_json()
        encrypted_creds = encrypt_credentials(creds_json)
        
        # Store in MongoDB
        calendar_creds_collection = db["calendar_credentials"]
        calendar_creds_collection.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "user_id": user_id,
                    "encrypted_credentials": encrypted_creds,
                    "updated_at": datetime.utcnow().isoformat()
                }
            },
            upsert=True
        )
    except Exception as e:
        print(f"Error saving credentials to DB: {e}")
        raise

def load_credentials_from_db(user_id: str, db) -> Optional[Credentials]:
    """Load and decrypt credentials from MongoDB"""
    try:
        calendar_creds_collection = db["calendar_credentials"]
        creds_doc = calendar_creds_collection.find_one({"user_id": user_id})
        if not creds_doc:
            return None
            
        encrypted_creds = creds_doc.get("encrypted_credentials")
        if not encrypted_creds:
            return None
            
        decrypted_json = decrypt_credentials(encrypted_creds)
        creds_data = json.loads(decrypted_json)
        
        # Debug: Check what fields we have
        print(f"DEBUG: Credential fields: {list(creds_data.keys())}")
        
        # Validate required fields
        required_fields = ['token', 'client_id', 'client_secret']
        missing_fields = [field for field in required_fields if field not in creds_data]
        
        if missing_fields:
            print(f"WARNING: Missing credential fields: {missing_fields}")
            return None
            
        creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
        return creds
        
    except Exception as e:
        print(f"Error loading credentials from DB: {e}")
        return None

def get_calendar_service(user_id: str, db):
    """Initialize and return Google Calendar service for a user"""
    creds = None
    
    # Load existing credentials from MongoDB
    creds = load_credentials_from_db(user_id, db)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Save refreshed credentials back to DB
                save_credentials_to_db(user_id, creds, db)
            except Exception as e:
                print(f"Failed to refresh credentials: {e}")
                # If refresh fails, we need to re-authenticate
                creds = None
        
        if not creds:
            # No valid credentials - user needs to authenticate
            raise HTTPException(
                status_code=401,
                detail="Google Calendar authentication required. Please call /calendar/auth/start to begin authentication."
            )

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize Google Calendar service: {str(e)}"
        )

@calendarrouter.post("/createevent", response_model=CalendarEventResponse)
async def create_calendar_event(
    event: CalendarEvent,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Create a new calendar event"""
    try:
        user_id = user['username']
        service = get_calendar_service(user_id, db)
        
        # Get doctor's email if user has an assigned doctor
        attendee_emails = list(event.attendees) if event.attendees else []
        if user.get("doctor"):
            doctor_username = user["doctor"]
            doctors_collection = db["doctors"]
            doctor = doctors_collection.find_one({"username": doctor_username})
            if doctor and doctor.get("email"):
                # Add doctor's email to attendees if not already present
                if doctor["email"] not in attendee_emails:
                    attendee_emails.append(doctor["email"])
        
        # Prepare event data for Google Calendar API
        event_data = {
            'summary': event.summary,
            'location': event.location,
            'description': event.description,
            'start': {
                'dateTime': event.start_datetime.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': event.end_datetime.isoformat(),
                'timeZone': 'UTC',
            },
            'attendees': [{'email': email} for email in attendee_emails],
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                    {'method': 'popup', 'minutes': 10},       # 10 minutes before
                ],
            },
        }
        
        # Create the event
        created_event = service.events().insert(
            calendarId=event.calendar_id,
            body=event_data
        ).execute()
        
        # Parse attendees from response
        attendees = []
        if 'attendees' in created_event:
            attendees = [attendee.get('email', '') for attendee in created_event['attendees']]
        
        # Return standardized response
        return CalendarEventResponse(
            id=created_event['id'],
            summary=created_event.get('summary', ''),
            description=created_event.get('description'),
            start_datetime=datetime.fromisoformat(created_event['start']['dateTime'].replace('Z', '+00:00')),
            end_datetime=datetime.fromisoformat(created_event['end']['dateTime'].replace('Z', '+00:00')),
            attendees=attendees,
            location=created_event.get('location'),
            html_link=created_event.get('htmlLink', ''),
            status=created_event.get('status', 'confirmed')
        )
        
    except HttpError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Google Calendar API error: {error}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create calendar event: {str(e)}"
        )

@calendarrouter.get("/events", response_model=List[CalendarEventResponse])
async def list_calendar_events(
    calendar_id: str = 'primary',
    max_results: int = 10,
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
    user = Depends(get_user),
    db = Depends(get_db)
):
    """List calendar events with optional filters"""
    try:
        user_id = user['username']
        service = get_calendar_service(user_id, db)
        
        # Set default time range if not provided
        if not time_min:
            time_min = datetime.utcnow()
        if not time_max:
            time_max = time_min + timedelta(days=30)  # Next 30 days
            
        # Call the Calendar API
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat() + 'Z',
            timeMax=time_max.isoformat() + 'Z',
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        # Convert to standardized format
        formatted_events = []
        for event in events:
            # Skip events without start time
            if 'start' not in event:
                continue
                
            # Handle all-day events
            start_time = event['start'].get('dateTime')
            end_time = event['end'].get('dateTime')
            
            if not start_time:
                # All-day event
                start_time = event['start'].get('date') + 'T00:00:00Z'
                end_time = event['end'].get('date') + 'T23:59:59Z'
            
            # Parse attendees
            attendees = []
            if 'attendees' in event:
                attendees = [attendee.get('email', '') for attendee in event['attendees']]
            
            formatted_events.append(CalendarEventResponse(
                id=event['id'],
                summary=event.get('summary', 'No Title'),
                description=event.get('description'),
                start_datetime=datetime.fromisoformat(start_time.replace('Z', '+00:00')),
                end_datetime=datetime.fromisoformat(end_time.replace('Z', '+00:00')),
                attendees=attendees,
                location=event.get('location'),
                html_link=event.get('htmlLink', ''),
                status=event.get('status', 'confirmed')
            ))
        
        return formatted_events
        
    except HttpError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Google Calendar API error: {error}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch calendar events: {str(e)}"
        )

@calendarrouter.put("/events/{event_id}", response_model=CalendarEventResponse)
async def update_calendar_event(
    event_id: str,
    event_update: EventUpdate,
    calendar_id: str = 'primary',
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Update an existing calendar event"""
    try:
        user_id = user['username']
        service = get_calendar_service(user_id, db)
        
        # Get the existing event first
        existing_event = service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()
        
        # Update only the provided fields
        if event_update.summary is not None:
            existing_event['summary'] = event_update.summary
        if event_update.description is not None:
            existing_event['description'] = event_update.description
        if event_update.location is not None:
            existing_event['location'] = event_update.location
        if event_update.start_datetime is not None:
            existing_event['start'] = {
                'dateTime': event_update.start_datetime.isoformat(),
                'timeZone': 'UTC',
            }
        if event_update.end_datetime is not None:
            existing_event['end'] = {
                'dateTime': event_update.end_datetime.isoformat(),
                'timeZone': 'UTC',
            }
        if event_update.attendees is not None:
            existing_event['attendees'] = [{'email': email} for email in event_update.attendees]
        
        # Update the event
        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=existing_event
        ).execute()
        
        # Parse attendees from response
        attendees = []
        if 'attendees' in updated_event:
            attendees = [attendee.get('email', '') for attendee in updated_event['attendees']]
        
        return CalendarEventResponse(
            id=updated_event['id'],
            summary=updated_event.get('summary', ''),
            description=updated_event.get('description'),
            start_datetime=datetime.fromisoformat(updated_event['start']['dateTime'].replace('Z', '+00:00')),
            end_datetime=datetime.fromisoformat(updated_event['end']['dateTime'].replace('Z', '+00:00')),
            attendees=attendees,
            location=updated_event.get('location'),
            html_link=updated_event.get('htmlLink', ''),
            status=updated_event.get('status', 'confirmed')
        )
        
    except HttpError as error:
        if error.resp.status == 404:
            raise HTTPException(
                status_code=404,
                detail="Calendar event not found"
            )
        raise HTTPException(
            status_code=400,
            detail=f"Google Calendar API error: {error}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update calendar event: {str(e)}"
        )

@calendarrouter.delete("/events/{event_id}")
async def delete_calendar_event(
    event_id: str,
    calendar_id: str = 'primary',
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Delete a calendar event"""
    try:
        user_id = user['username']
        service = get_calendar_service(user_id, db)
        
        # Delete the event
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()
        
        return {"message": "Event deleted successfully", "event_id": event_id}
        
    except HttpError as error:
        if error.resp.status == 404:
            raise HTTPException(
                status_code=404,
                detail="Calendar event not found"
            )
        raise HTTPException(
            status_code=400,
            detail=f"Google Calendar API error: {error}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete calendar event: {str(e)}"
        )

@calendarrouter.get("/calendars")
async def list_calendars(
    user = Depends(get_user),
    db = Depends(get_db)
):
    """List available calendars for the authenticated user"""
    try:
        user_id = user['username']
        service = get_calendar_service(user_id, db)
        
        calendars_result = service.calendarList().list().execute()
        calendars = calendars_result.get('items', [])
        
        formatted_calendars = []
        for calendar in calendars:
            formatted_calendars.append({
                'id': calendar['id'],
                'summary': calendar.get('summary', ''),
                'description': calendar.get('description'),
                'primary': calendar.get('primary', False),
                'access_role': calendar.get('accessRole', ''),
                'background_color': calendar.get('backgroundColor', ''),
                'foreground_color': calendar.get('foregroundColor', '')
            })
        
        return {
            "calendars": formatted_calendars,
            "total": len(formatted_calendars)
        }
        
    except HttpError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Google Calendar API error: {error}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch calendars: {str(e)}"
        )

@calendarrouter.get("/auth/start")
async def start_google_auth(
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Start Google Calendar OAuth flow - returns authorization URL"""
    try:
        user_id = user['username']
        
        # Check if user already has valid credentials
        existing_creds = load_credentials_from_db(user_id, db)
        if existing_creds and existing_creds.valid:
            return {
                "message": "User already authenticated with Google Calendar",
                "authenticated": True,
                "auth_url": None
            }
        
        # Check if credentials file exists
        credentials_file = os.getenv("CALENDAR_SECRET_FILE")
        if not os.path.exists(credentials_file):
            raise HTTPException(
                status_code=500,
                detail="Google Calendar credentials file not found. Please ensure the OAuth2 credentials file is available."
            )
        
        # Generate state parameter for CSRF protection
        state = secrets.token_urlsafe(32)
        
        # Store state in database temporarily
        auth_states_collection = db["auth_states"]
        auth_states_collection.insert_one({
            "user_id": user_id,
            "state": state,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=10)
        })
        
        # Create OAuth flow with web-based redirect
        flow = InstalledAppFlow.from_client_secrets_file(
            credentials_file, SCOPES)
        
        # Set redirect URI to our callback endpoint
        flow.redirect_uri = f"{os.getenv('BASE_URL', 'http://localhost:8000')}/calendar/auth/callback"
        
        # Generate authorization URL with proper offline access
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',  # Force consent screen to ensure refresh token
            state=state
        )
        
        return {
            "message": "Please visit the authorization URL to grant calendar access",
            "authenticated": False,
            "auth_url": auth_url,
            "state": state
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start Google Calendar authentication: {str(e)}"
        )

@calendarrouter.get("/auth/callback")
async def google_auth_callback(
    code: str,
    state: str,
    db = Depends(get_db)
):
    """Handle Google OAuth callback"""
    try:
        # Verify state parameter to prevent CSRF
        auth_states_collection = db["auth_states"]
        state_doc = auth_states_collection.find_one({
            "state": state,
            "expires_at": {"$gt": datetime.utcnow()}
        })
        
        if not state_doc:
            raise HTTPException(
                status_code=400,
                detail="Invalid or expired state parameter"
            )
        
        user_id = state_doc["user_id"]
        
        # Clean up state
        auth_states_collection.delete_one({"_id": state_doc["_id"]})
        
        # Check if credentials file exists
        credentials_file = os.getenv("CALENDAR_SECRET_FILE")
        if not os.path.exists(credentials_file):
            raise HTTPException(
                status_code=500,
                detail="Google Calendar credentials file not found"
            )
        
        # Create OAuth flow
        flow = InstalledAppFlow.from_client_secrets_file(
            credentials_file, SCOPES)
        flow.redirect_uri = f"{os.getenv('BASE_URL', 'http://localhost:8000')}/calendar/auth/callback"
        
        # Exchange authorization code for credentials
        flow.fetch_token(code=code)
        creds = flow.credentials
        
        # Save credentials to MongoDB
        save_credentials_to_db(user_id, creds, db)
        
        # Return success page or redirect
        return {
            "message": "Successfully authenticated with Google Calendar",
            "authenticated": True,
            "user_id": user_id
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to complete Google Calendar authentication: {str(e)}"
        )

@calendarrouter.delete("/auth")
async def revoke_google_auth(
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Revoke Google Calendar authentication and delete stored credentials"""
    try:
        user_id = user['username']
        
        # Delete credentials from database
        calendar_creds_collection = db["calendar_credentials"]
        result = calendar_creds_collection.delete_one({"user_id": user_id})
        
        return {
            "message": "Google Calendar authentication revoked successfully",
            "deleted": result.deleted_count > 0
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to revoke Google Calendar authentication: {str(e)}"
        )

@calendarrouter.get("/auth/status")
async def get_auth_status(
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Check if user is authenticated with Google Calendar"""
    try:
        user_id = user['username']
        creds = load_credentials_from_db(user_id, db)
        if not creds:
            return {
                "authenticated": False,
                "message": "No Google Calendar credentials found"
            }
        
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    save_credentials_to_db(user_id, creds, db)
                    return {
                        "authenticated": True,
                        "message": "Credentials refreshed successfully"
                    }
                except Exception:
                    return {
                        "authenticated": False,
                        "message": "Credentials expired and refresh failed"
                    }
            else:
                return {
                    "authenticated": False,
                    "message": "Invalid credentials - re-authentication required"
                }
        
        return {
            "authenticated": True,
            "message": "Successfully authenticated with Google Calendar"
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to check authentication status: {str(e)}"
        )

@calendarrouter.post("/quick-add")
async def quick_add_event(
    text: str,
    calendar_id: str = 'primary',
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Create an event using Google's Quick Add feature with natural language"""
    try:
        user_id = user['username']
        service = get_calendar_service(user_id, db)
        
        # Use Google's Quick Add feature
        event = service.events().quickAdd(
            calendarId=calendar_id,
            text=text
        ).execute()
        
        # Parse attendees from response
        attendees = []
        if 'attendees' in event:
            attendees = [attendee.get('email', '') for attendee in event['attendees']]
        
        return CalendarEventResponse(
            id=event['id'],
            summary=event.get('summary', ''),
            description=event.get('description'),
            start_datetime=datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00')),
            end_datetime=datetime.fromisoformat(event['end']['dateTime'].replace('Z', '+00:00')),
            attendees=attendees,
            location=event.get('location'),
            html_link=event.get('htmlLink', ''),
            status=event.get('status', 'confirmed')
        )
        
    except HttpError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Google Calendar API error: {error}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create quick event: {str(e)}"
        )

@calendarrouter.post("/doctor/free-blocks", response_model=List[FreeBlock])
async def get_doctor_free_blocks(
    date: datetime,
    time_ranges: List[TimeRange],
    user = Depends(get_user),
    db = Depends(get_db)
):
    """Get free time blocks from the user's assigned doctor's calendar"""
    try:
        # Check if user has an assigned doctor
        if not user.get("doctor"):
            raise HTTPException(
                status_code=400,
                detail="User does not have an assigned doctor"
            )
        
        doctor_username = user["doctor"]
        
        # Verify doctor exists and get their calendar service
        doctors_collection = db["doctors"]
        doctor = doctors_collection.find_one({"username": doctor_username})
        if not doctor:
            raise HTTPException(
                status_code=404,
                detail="Assigned doctor not found"
            )
        
        # Get doctor's calendar service
        try:
            service = get_calendar_service(doctor_username, db)
        except HTTPException as e:
            if e.status_code == 401:
                raise HTTPException(
                    status_code=400,
                    detail="Doctor has not authenticated their Google Calendar"
                )
            raise
        
        # Get doctor's events in the specified date range
        events_result = service.events().list(
            calendarId='primary',
            timeMin=date.isoformat() + 'Z',
            timeMax=(date+timedelta(days = 1)).isoformat() + 'Z',
            maxResults=1000,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        # Convert events to busy periods
        busy_periods = []
        for event in events:
            if 'start' not in event or event.get('status') == 'cancelled':
                continue
                
            start_time = event['start'].get('dateTime')
            end_time = event['end'].get('dateTime')
            
            if not start_time or not end_time:
                # Skip all-day events
                continue
            
            busy_periods.append({
                'start': datetime.fromisoformat(start_time.replace('Z', '+00:00')),
                'end': datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            })
        
        # Sort busy periods by start time
        busy_periods.sort(key=lambda x: x['start'])
        
        # Generate free blocks for each provided time range
        free_blocks = []
        target_date = date.date()
        
        for time_range in time_ranges:
            # Convert military time to datetime objects
            try:
                start_hour = int(time_range.start_time[:2])
                start_minute = int(time_range.start_time[2:])
                end_hour = int(time_range.end_time[:2])
                end_minute = int(time_range.end_time[2:])
                
                range_start = datetime.combine(target_date, datetime.min.time().replace(hour=start_hour, minute=start_minute))
                range_end = datetime.combine(target_date, datetime.min.time().replace(hour=end_hour, minute=end_minute))
                
            except (ValueError, IndexError) as e:
                # Skip invalid time format
                continue
            
            # Get busy periods that overlap with this time range
            overlapping_busy_periods = []
            for period in busy_periods:
                # Check if busy period overlaps with our time range
                if (period['start'] < range_end and period['end'] > range_start):
                    overlapping_busy_periods.append(period)
            
            # Check if this time range is free
            if not overlapping_busy_periods:
                # Entire time range is free
                range_duration = int((range_end - range_start).total_seconds() / 60)
                free_blocks.append(FreeBlock(
                    start_datetime=range_start,
                    end_datetime=range_end,
                    duration_minutes=range_duration
                ))
        
        return free_blocks
        
    except HttpError as error:
        raise HTTPException(
            status_code=400,
            detail=f"Google Calendar API error: {error}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get doctor's free blocks: {str(e)}"
        )