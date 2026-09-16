"""Regina's roster. Add a new sub-agent by subclassing Subagent and listing it here."""

from __future__ import annotations

from typing import Any

from .base import Subagent, SubagentReply
from .calendar_agent import CalendarAgent
from .email_agent import EmailAgent
from .news_agent import NewsAgent

ROSTER: list[type[Subagent]] = [EmailAgent, CalendarAgent, NewsAgent]


def build_roster(backend: str | None = None, client: Any = None) -> dict[str, Subagent]:
    """Instantiate every sub-agent, keyed by its short key (email/calendar/news)."""
    return {cls.key: cls(backend=backend, client=client) for cls in ROSTER}


__all__ = ["Subagent", "SubagentReply", "EmailAgent", "CalendarAgent", "NewsAgent", "ROSTER", "build_roster"]
