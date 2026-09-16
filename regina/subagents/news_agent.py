"""Anthropic News Agent — mock knowledge specialist for the latest announcements."""

from __future__ import annotations

import re
from typing import Any

from .base import Subagent, keywords


class NewsAgent(Subagent):
    key = "news"
    name = "Anthropic News Agent"
    tool_name = "ask_news_agent"
    fixture_file = "anthropic_news.json"
    description = (
        "Knows the latest Anthropic announcements (models, platform, API, "
        "developer tools, research). Can return the newest items in the last N "
        "days, filter by topic, and explain why each item matters to the user."
    )
    persona = (
        "You are the Anthropic News Agent, a specialist sub-agent of Regina "
        "(team Prompt Queens). You know recent Anthropic announcements and "
        "nothing else. Newest first, one line of 'why it matters' per item, "
        "always include the link. Never invent announcements not in your data."
    )

    @property
    def items(self) -> list[dict[str, Any]]:
        return sorted(self.raw["items"], key=lambda i: i["published_days_ago"])

    def answer(self, brief: str) -> tuple[str, dict[str, Any]]:
        low = brief.lower()
        days = 7
        m = re.search(r"(\d+)\s*days?", low)
        if m:
            days = int(m.group(1))
        elif "month" in low:
            days = 30
        elif "week" in low:
            days = 7 if "two" not in low else 14
        limit_match = re.search(r"top (\d+)|(\d+) (?:items|headlines|stories|announcements)", low)
        limit = int(next(g for g in limit_match.groups() if g)) if limit_match else 5

        pool = [i for i in self.items if i["published_days_ago"] <= days]
        terms = self._topic_terms(low)
        topic_hits = [i for i in pool if any(self._matches(t, i) for t in terms)] if terms else []
        filtered = topic_hits or pool
        picked = filtered[:limit]

        scope = f"last {days} days" + (f", topic: {', '.join(sorted(terms))}" if topic_hits else "")
        if not picked:
            return f"**Anthropic News Agent** — nothing published in the {scope}.", {"items": []}
        lines = [f"**Anthropic News Agent** — {len(picked)} item(s), {scope} (newest first):", ""]
        for i in picked:
            age = "today" if i["published_days_ago"] == 0 else f"{i['published_days_ago']}d ago"
            lines.append(f"- **{i['title']}** ({age}, {i['category']})\n  {i['summary']}\n  Why it matters: {i['why_it_matters']}\n  {i['url']}")
        return "\n".join(lines), {"items": picked, "days": days}

    def _topic_terms(self, low: str) -> set[str]:
        """Explicit 'about X' wins; otherwise only short questions are treated as
        topical, and only words from the feed's own tag vocabulary count."""
        explicit = re.search(r"\b(?:about|regarding|related to|on the topic of|concerning) ([^,.?!]+)", low)
        if explicit:
            return {t.rstrip("s") for t in keywords(explicit.group(1))}
        if len(low.split()) > 10:
            return set()
        vocab = {t.rstrip("s") for i in self.raw["items"] for t in i["tags"] + [i["category"]]}
        return {t.rstrip("s") for t in keywords(low) if any(t.rstrip("s") in v or v in t.rstrip("s") for v in vocab)}

    @staticmethod
    def _matches(term: str, item: dict[str, Any]) -> bool:
        """Topic match on tags and category only, so 'agents' finds 'managed-agents'
        and 'multi-agent' but not every summary that mentions an agent."""
        labels = item["tags"] + [item["category"]]
        return any(term in label or label.rstrip("s") in term for label in labels)

    @staticmethod
    def _haystack(i: dict[str, Any]) -> str:
        return " ".join([i["title"], i["summary"], i["category"], " ".join(i["tags"])]).lower()
