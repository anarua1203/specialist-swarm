"""Email Agent — mock inbox specialist."""

from __future__ import annotations

import re
from typing import Any

from .base import Subagent, humanize_minutes, keywords

IMPORTANCE_RANK = {"high": 0, "medium": 1, "low": 2}


class EmailAgent(Subagent):
    key = "email"
    name = "Email Agent"
    tool_name = "ask_email_agent"
    fixture_file = "emails.json"
    description = (
        "Reads the user's inbox. Can produce an inbox digest, list urgent or "
        "unread mail, find mail from a person or about a topic, list what needs "
        "a reply, and propose one-line draft replies."
    )
    persona = (
        "You are the Email Agent, a specialist sub-agent of Regina (team Prompt "
        "Queens). You know the user's inbox and nothing else. Rank by importance "
        "then recency, quote senders and subjects exactly, and offer a one-line "
        "draft reply for anything that needs one."
    )

    # ---- mock backend ---------------------------------------------------------

    @property
    def emails(self) -> list[dict[str, Any]]:
        return self.raw["emails"]

    def answer(self, brief: str) -> tuple[str, dict[str, Any]]:
        low = brief.lower()
        pool = list(self.emails)
        filters: list[str] = []

        sender = re.search(r"from ([A-Z][a-zA-Z'.-]+(?: [A-Z][a-zA-Z'.-]+)*)", brief)
        if sender:
            name = sender.group(1).lower()
            pool = [e for e in pool if name in e["from_name"].lower()]
            filters.append(f"from {sender.group(1)}")

        if re.search(r"\b(urgent|important|priority|high[- ]importance)\b", low):
            pool = [e for e in pool if e["importance"] == "high"]
            filters.append("high importance")
        if "unread" in low:
            pool = [e for e in pool if e["unread"]]
            filters.append("unread")
        if re.search(r"\b(reply|replies|respond|answer)\b", low) and not re.search(
            r"\b(digest|everything|all mail|inbox)\b", low
        ):
            pool = [e for e in pool if e["needs_reply"]]
            filters.append("needs reply")

        topic = re.search(r"\b(?:about|regarding|mentioning|search for|search|on the topic of) ([^,.?]+)", low)
        if topic:
            terms = keywords(topic.group(1))
            if terms:
                pool = [e for e in pool if any(t in self._haystack(e) for t in terms)]
                filters.append("about " + " ".join(sorted(terms)))

        if not filters:
            # Digest: everything that is high importance or needs a reply, then the rest by rank.
            pool = sorted(self.emails, key=self._rank)
            highlighted = [e for e in pool if e["importance"] == "high" or e["needs_reply"]]
            quiet = [e for e in pool if e not in highlighted]
            text = self._render_digest(highlighted, quiet)
            return text, {"matched": highlighted, "quiet": quiet, "counts": self._counts()}

        pool = sorted(pool, key=self._rank)
        header = f"**Email Agent** — {len(pool)} email(s) matching: {', '.join(filters)}"
        if not pool:
            return header + "\n\nNothing in the inbox matches.", {"matched": [], "counts": self._counts()}
        lines = [header, ""] + [self._render_email(e) for e in pool]
        return "\n".join(lines), {"matched": pool, "counts": self._counts()}

    # ---- rendering helpers ----------------------------------------------------

    def _counts(self) -> dict[str, int]:
        return {
            "total": len(self.emails),
            "unread": sum(e["unread"] for e in self.emails),
            "needs_reply": sum(e["needs_reply"] for e in self.emails),
            "high_importance": sum(e["importance"] == "high" for e in self.emails),
        }

    @staticmethod
    def _rank(e: dict[str, Any]) -> tuple[int, int, int]:
        return (IMPORTANCE_RANK[e["importance"]], 0 if e["needs_reply"] else 1, e["received_minutes_ago"])

    @staticmethod
    def _haystack(e: dict[str, Any]) -> str:
        return " ".join([e["subject"], e["body"], e["from_name"], " ".join(e["labels"])]).lower()

    @staticmethod
    def _render_email(e: dict[str, Any]) -> str:
        flags = []
        if e["needs_reply"]:
            flags.append("needs reply")
        if e["unread"]:
            flags.append("unread")
        flag_text = f" ({', '.join(flags)})" if flags else ""
        line = (
            f"- [{e['importance'].upper()}] **{e['from_name']}** — \"{e['subject']}\" "
            f"· {humanize_minutes(e['received_minutes_ago'])}{flag_text}\n"
            f"  {e['body'][:160].rstrip()}{'…' if len(e['body']) > 160 else ''}"
        )
        if e.get("draft_reply"):
            line += f"\n  Draft reply: \"{e['draft_reply']}\""
        return line

    def _render_digest(self, highlighted: list[dict[str, Any]], quiet: list[dict[str, Any]]) -> str:
        c = self._counts()
        lines = [
            f"**Email Agent** — inbox digest: {c['total']} emails, {c['unread']} unread, "
            f"{c['needs_reply']} need a reply, {c['high_importance']} high importance.",
            "",
            "Needs attention:",
        ]
        lines += [self._render_email(e) for e in highlighted] or ["- nothing urgent"]
        if quiet:
            lines += ["", "Quiet / low priority: " + "; ".join(f"{e['from_name']} — {e['subject']}" for e in quiet)]
        return "\n".join(lines)
