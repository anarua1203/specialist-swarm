"""Immutable dataclasses for the calendar agent pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Attendee:
    name: str
    email: str
    is_organiser: bool = False


@dataclass(frozen=True)
class Attachment:
    name: str
    content_type: str
    text_preview: str = ""  # first 1000 chars for text/* types; empty for binary


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    title: str
    start: datetime
    end: datetime
    attendees: tuple[Attendee, ...]
    description: str
    location: str
    source: str  # "m365" | "google"
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class PriorityScore:
    total: float
    attendee_score: float
    keyword_score: float
    signals: tuple[str, ...]


@dataclass(frozen=True)
class ScoredEvent:
    event: CalendarEvent
    score: PriorityScore
    rank: int


@dataclass(frozen=True)
class PrepAction:
    action: str
    grounded_in: str  # "description" | "attachment:<name>" | "attendees" | "title"


@dataclass(frozen=True)
class EventBriefing:
    rank: int
    event: CalendarEvent
    priority: PriorityScore
    prep_actions: tuple[PrepAction, ...]


@dataclass(frozen=True)
class EvalResult:
    prep_relevance: float       # 0–10; primary metric
    hallucination_score: float  # 0–10; higher = fewer hallucinations
    schema_consistency: float   # 0 or 10
    priority_correctness: float # 0–10
    issues: tuple[str, ...]
    overall: float


@dataclass(frozen=True)
class DailyBriefing:
    generated_at: datetime
    date: str
    events: tuple[EventBriefing, ...]
    eval_result: EvalResult | None = None
