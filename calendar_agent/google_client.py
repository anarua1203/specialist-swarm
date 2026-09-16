"""Google Calendar client using the Google Calendar API.

Required environment variables:
  GOOGLE_CREDENTIALS_JSON  – Path to a service-account or OAuth client JSON file.
                             For service accounts, also set GOOGLE_DELEGATED_EMAIL.
  GOOGLE_DELEGATED_EMAIL   – (service account only) The user to impersonate.
  GOOGLE_CALENDAR_ID       – Calendar ID to read. Defaults to "primary".

Service account needs domain-wide delegation with scope:
  https://www.googleapis.com/auth/calendar.readonly
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

import google.auth.transport.requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

from .schemas import Attachment, Attendee, CalendarEvent

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
ATTACHMENT_TEXT_LIMIT = 1000


def _build_service():
    creds_path = os.environ["GOOGLE_CREDENTIALS_JSON"]
    with open(creds_path) as f:
        info = json.load(f)

    creds_type = info.get("type")
    if creds_type == "service_account":
        delegated = os.environ.get("GOOGLE_DELEGATED_EMAIL")
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        if delegated:
            creds = creds.with_subject(delegated)
    else:
        raise NotImplementedError(
            "GOOGLE_CREDENTIALS_JSON must be a service_account key file. "
            "OAuth user credentials are not yet supported."
        )

    request = google.auth.transport.requests.Request()
    creds.refresh(request)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_dt(value: str | dict | None) -> datetime:
    if not value:
        return datetime.now(tz=timezone.utc)
    if isinstance(value, dict):
        # dateTime has timezone; date is all-day
        if "dateTime" in value:
            raw = value["dateTime"]
        else:
            raw = f"{value['date']}T00:00:00+00:00"
    else:
        raw = value
    return datetime.fromisoformat(raw)


def _parse_attendees(raw_list: list[dict]) -> tuple[Attendee, ...]:
    return tuple(
        Attendee(
            name=a.get("displayName", ""),
            email=a.get("email", "").lower(),
            is_organiser=a.get("organizer", False),
        )
        for a in raw_list
    )


def _parse_attachments(raw_list: list[dict]) -> tuple[Attachment, ...]:
    # Google Calendar attachments are Drive links — we capture the title only.
    return tuple(
        Attachment(
            name=a.get("title", "unknown"),
            content_type=a.get("mimeType", "application/octet-stream"),
            text_preview="",  # Drive content requires separate auth scope
        )
        for a in raw_list
    )


def fetch_today_events(target_date: date | None = None) -> list[CalendarEvent]:
    service = _build_service()
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    day = target_date or date.today()

    time_min = f"{day}T00:00:00Z"
    time_max = f"{day}T23:59:59Z"

    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )

    events: list[CalendarEvent] = []
    for item in result.get("items", []):
        if item.get("status") == "cancelled":
            continue

        events.append(CalendarEvent(
            event_id=item["id"],
            title=item.get("summary", "(no title)"),
            start=_parse_dt(item.get("start")),
            end=_parse_dt(item.get("end")),
            attendees=_parse_attendees(item.get("attendees", [])),
            description=item.get("description", "")[:2000],
            location=item.get("location", ""),
            source="google",
            attachments=_parse_attachments(item.get("attachments", [])),
        ))
    return events
