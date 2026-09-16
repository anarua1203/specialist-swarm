"""Microsoft 365 calendar client using the Microsoft Graph API.

Required environment variables:
  AZURE_TENANT_ID       – Azure AD tenant ID
  AZURE_CLIENT_ID       – App registration client ID
  AZURE_CLIENT_SECRET   – App registration client secret
  M365_USER_EMAIL       – UPN of the user whose calendar to read

The app registration needs the following application (not delegated) permissions:
  Calendars.Read
  User.Read.All
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

import msal
import requests

from .schemas import Attendee, Attachment, CalendarEvent

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPE = ["https://graph.microsoft.com/.default"]
ATTACHMENT_TEXT_LIMIT = 1000


def _get_token() -> str:
    tenant = os.environ["AZURE_TENANT_ID"]
    client_id = os.environ["AZURE_CLIENT_ID"]
    client_secret = os.environ["AZURE_CLIENT_SECRET"]

    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"MSAL error: {result.get('error_description', result)}")
    return result["access_token"]


def _parse_attendee(raw: dict[str, Any]) -> Attendee:
    ep = raw.get("emailAddress", {})
    return Attendee(
        name=ep.get("name", ""),
        email=ep.get("address", "").lower(),
        is_organiser=(raw.get("type") == "organizer"),
    )


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(tz=timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _fetch_attachments(token: str, user: str, event_id: str) -> tuple[Attachment, ...]:
    url = f"{GRAPH_BASE}/users/{user}/events/{event_id}/attachments"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=10)
    if not resp.ok:
        return ()

    attachments: list[Attachment] = []
    for item in resp.json().get("value", []):
        content_type = item.get("contentType", "")
        text_preview = ""
        if content_type.startswith("text/") and item.get("contentBytes"):
            import base64
            raw_bytes = base64.b64decode(item["contentBytes"])
            text_preview = raw_bytes.decode("utf-8", errors="replace")[:ATTACHMENT_TEXT_LIMIT]
        attachments.append(Attachment(
            name=item.get("name", "unknown"),
            content_type=content_type,
            text_preview=text_preview,
        ))
    return tuple(attachments)


def fetch_today_events(target_date: date | None = None) -> list[CalendarEvent]:
    token = _get_token()
    user = os.environ["M365_USER_EMAIL"]
    day = target_date or date.today()

    start = f"{day}T00:00:00Z"
    end = f"{day}T23:59:59Z"
    url = (
        f"{GRAPH_BASE}/users/{user}/calendarView"
        f"?startDateTime={start}&endDateTime={end}"
        f"&$select=id,subject,start,end,attendees,body,location"
        f"&$top=50"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Prefer": 'outlook.timezone="UTC"',
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    events: list[CalendarEvent] = []
    for item in resp.json().get("value", []):
        event_id = item["id"]
        attendees = tuple(_parse_attendee(a) for a in item.get("attendees", []))
        attachments = _fetch_attachments(token, user, event_id)
        description = item.get("body", {}).get("content", "")
        # Strip basic HTML tags for cleaner text
        import re
        description = re.sub(r"<[^>]+>", " ", description).strip()

        events.append(CalendarEvent(
            event_id=event_id,
            title=item.get("subject", "(no title)"),
            start=_parse_dt(item.get("start", {}).get("dateTime")),
            end=_parse_dt(item.get("end", {}).get("dateTime")),
            attendees=attendees,
            description=description[:2000],
            location=item.get("location", {}).get("displayName", ""),
            source="m365",
            attachments=attachments,
        ))
    return events
