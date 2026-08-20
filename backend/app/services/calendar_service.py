import datetime
import json
import logging
from typing import Optional
from google.oauth2 import service_account
from googleapiclient.discovery import build

from ..config import settings

logger = logging.getLogger(__name__)

def _get_calendar_service():
    if not settings.GOOGLE_SERVICE_ACCOUNT_JSON:
        logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON not set. Running Google Calendar API in mock mode.")
        return None
        
    try:
        import os
        if os.path.exists(settings.GOOGLE_SERVICE_ACCOUNT_JSON):
            with open(settings.GOOGLE_SERVICE_ACCOUNT_JSON, "r") as f:
                info = json.load(f)
        else:
            info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
            
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/calendar"]
        )
        return build("calendar", "v3", credentials=credentials)
    except Exception as e:
        logger.error(f"Failed to initialize Google Calendar client: {e}")
        return None

def create_calendar_event(
    summary: str,
    start_time: datetime.datetime,
    end_time: datetime.datetime,
    description: str = "",
    timezone: str = "Asia/Kolkata"
) -> Optional[str]:
    service = _get_calendar_service()
    if not service:
        # Mock mode fallback
        logger.info(f"[MOCK CALENDAR] Creating event: '{summary}' from {start_time} to {end_time}")
        return f"mock-event-{int(datetime.datetime.utcnow().timestamp())}"

    event = {
        "summary": summary,
        "description": description,
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": timezone,
        },
    }

    try:
        created_event = service.events().insert(
            calendarId=settings.GOOGLE_CALENDAR_ID,
            body=event
        ).execute()
        return created_event.get("id")
    except Exception as e:
        logger.error(f"Error creating Google Calendar event: {e}")
        raise e

def cancel_calendar_event(event_id: str) -> None:
    service = _get_calendar_service()
    if not service:
        logger.info(f"[MOCK CALENDAR] Cancelling event: {event_id}")
        return

    try:
        service.events().delete(
            calendarId=settings.GOOGLE_CALENDAR_ID,
            eventId=event_id
        ).execute()
    except Exception as e:
        logger.error(f"Error deleting Google Calendar event: {e}")
        raise e

def update_calendar_event(
    event_id: str,
    start_time: datetime.datetime,
    end_time: datetime.datetime,
    timezone: str = "Asia/Kolkata"
) -> None:
    service = _get_calendar_service()
    if not service:
        logger.info(f"[MOCK CALENDAR] Updating event {event_id}: new times {start_time} to {end_time}")
        return

    try:
        # Retrieve the event first
        event = service.events().get(
            calendarId=settings.GOOGLE_CALENDAR_ID,
            eventId=event_id
        ).execute()

        event["start"] = {
            "dateTime": start_time.isoformat(),
            "timeZone": timezone,
        }
        event["end"] = {
            "dateTime": end_time.isoformat(),
            "timeZone": timezone,
        }

        service.events().update(
            calendarId=settings.GOOGLE_CALENDAR_ID,
            eventId=event_id,
            body=event
        ).execute()
    except Exception as e:
        logger.error(f"Error updating Google Calendar event: {e}")
        raise e

def check_calendar_availability(start_time: datetime.datetime, end_time: datetime.datetime) -> bool:
    service = _get_calendar_service()
    if not service:
        logger.info(f"[MOCK CALENDAR] Checking availability from {start_time} to {end_time}")
        return True
    try:
        # Check standard FreeBusy queries
        body = {
            "timeMin": start_time.isoformat() + "Z",
            "timeMax": end_time.isoformat() + "Z",
            "items": [{"id": settings.GOOGLE_CALENDAR_ID}]
        }
        freebusy = service.freebusy().query(body=body).execute()
        calendars = freebusy.get("calendars", {})
        cal = calendars.get(settings.GOOGLE_CALENDAR_ID, {})
        busy = cal.get("busy", [])
        return len(busy) == 0
    except Exception as e:
        logger.error(f"Error checking calendar availability: {e}")
        return True

