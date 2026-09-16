"""Phase 1: deterministic priority scoring for today's calendar events.

Score = 0.5 × attendee_score + 0.5 × keyword_score

attendee_score:
  +2 per external-domain attendee
  +3 if any attendee title matches a seniority keyword
  +1 per attendee beyond the first (capped at 5)

keyword_score (title + description, case-insensitive):
  +3: board, deadline, escalation, urgent
  +2: client, demo, presentation, proposal
  +1: review, sync, check-in, planning
"""

from __future__ import annotations

from .schemas import Attendee, CalendarEvent, PriorityScore, ScoredEvent

INTERNAL_DOMAINS: frozenset[str] = frozenset({"capgemini.com"})

SENIORITY_KEYWORDS: frozenset[str] = frozenset({
    "director", "vp", "vice president", "c-suite",
    "ceo", "cto", "cfo", "coo", "ciso",
    "president", "partner", "managing director", "md",
    "svp", "evp", "principal",
})

KEYWORD_WEIGHTS: dict[str, int] = {
    "board": 3, "deadline": 3, "escalation": 3, "urgent": 3,
    "client": 2, "demo": 2, "presentation": 2, "proposal": 2,
    "review": 1, "sync": 1, "check-in": 1, "planning": 1,
}


def _is_external(email: str) -> bool:
    domain = email.split("@")[-1].lower() if "@" in email else ""
    return domain not in INTERNAL_DOMAINS and bool(domain)


def _score_attendees(attendees: tuple[Attendee, ...]) -> tuple[float, list[str]]:
    score = 0.0
    signals: list[str] = []

    external = [a for a in attendees if _is_external(a.email)]
    score += len(external) * 2
    for a in external:
        signals.append(f"external attendee: {a.name or a.email}")

    extra = min(len(attendees) - 1, 5)
    if extra > 0:
        score += extra
        signals.append(f"{len(attendees)} total attendees")

    for a in attendees:
        name_lower = a.name.lower()
        if any(t in name_lower for t in SENIORITY_KEYWORDS):
            score += 3
            signals.append(f"senior attendee: {a.name}")
            break  # one bonus per event regardless of how many senior attendees

    return score, signals


def _score_keywords(event: CalendarEvent) -> tuple[float, list[str]]:
    text = f"{event.title} {event.description}".lower()
    score = 0.0
    signals: list[str] = []
    for keyword, weight in KEYWORD_WEIGHTS.items():
        if keyword in text:
            score += weight
            signals.append(f"keyword '{keyword}'")
    return score, signals


def score_event(event: CalendarEvent) -> PriorityScore:
    attendee_score, attendee_signals = _score_attendees(event.attendees)
    keyword_score, keyword_signals = _score_keywords(event)
    total = round(0.5 * attendee_score + 0.5 * keyword_score, 2)
    return PriorityScore(
        total=total,
        attendee_score=round(attendee_score, 2),
        keyword_score=round(keyword_score, 2),
        signals=tuple(attendee_signals + keyword_signals),
    )


def _dedup(events: list[CalendarEvent]) -> list[CalendarEvent]:
    """Remove cross-source duplicates by matching on (normalised title, start time).

    When the same meeting appears in both M365 and Google (common when a Google
    account has M365 calendar sync enabled), prefer the M365 copy because it
    carries richer attachment data.
    """
    seen: dict[tuple[str, str], CalendarEvent] = {}
    for event in events:
        key = (event.title.strip().lower(), event.start.strftime("%Y-%m-%dT%H:%M"))
        existing = seen.get(key)
        if existing is None:
            seen[key] = event
        elif existing.source != "m365" and event.source == "m365":
            seen[key] = event  # prefer M365 copy
    return list(seen.values())


def rank_events(events: list[CalendarEvent], top_n: int = 5) -> list[ScoredEvent]:
    """Deduplicate, score, and return top_n events sorted by score desc then start asc."""
    unique = _dedup(events)
    scored = [ScoredEvent(event=e, score=score_event(e), rank=0) for e in unique]
    scored.sort(key=lambda x: (-x.score.total, x.event.start))
    return [
        ScoredEvent(event=s.event, score=s.score, rank=i + 1)
        for i, s in enumerate(scored[:top_n])
    ]
