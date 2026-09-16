"""
Email Agent — inbox specialist that follows the `email-brief` skill.

The skill (skills/email-brief/SKILL.md, by Élise Sauvé) defines the contract:
triage cheaply from metadata, collapse by conversation, summarise in EXACTLY
3 bullets, pick the 3 most important actions, create drafts only (never
send), and return JSON + a short markdown briefing.

Backends:
- mock: the rubric applied deterministically to mock_data/emails.json
- llm:  Claude with the skill + fixture in its system prompt
- Managed Agents + EXCHANGE_MCP_URL: the same persona over a live Outlook/
  Exchange MCP (see managed_agents/create_subagents.py)
"""

from __future__ import annotations

import json
import re
from datetime import timedelta, timezone
from typing import Any

from .. import config
from .base import Subagent, humanize_minutes, keywords

IMPORTANCE_RANK = {"high": 0, "medium": 1, "low": 2}
VIP_LABELS = {"client", "manager", "hackathon"}
URGENCY_KEYWORDS = re.compile(
    r"\b(by (?:eod|end of day|thursday|friday|monday|tomorrow|\d{1,2}(?::\d{2})?\s?(?:am|pm)))\b|\bdue\b|\bdeadline\b|\bbefore (?:then|friday|thursday)\b|\basap\b",
    re.IGNORECASE,
)
ASK_KEYWORDS = re.compile(r"\bcan you\b|\bcould you\b|\bplease\b|\bconfirm\b|\bapprove\b|\?", re.IGNORECASE)
READ_CAP = 15
WINDOW_HOURS = 24


def _skill_body() -> str:
    text = (config.ROOT / "skills" / "email-brief" / "SKILL.md").read_text()
    return text.split("---", 2)[2].strip() if text.startswith("---") else text


class EmailAgent(Subagent):
    key = "email"
    name = "Email Agent"
    tool_name = "ask_email_agent"
    fixture_file = "emails.json"
    description = (
        "Reads the user's inbox following the email-brief skill. Default brief "
        "returns an inbox briefing: exactly 3 summary bullets, the 3 most "
        "important actions ranked by deadline / who is blocked / seniority / "
        "explicit ask, and reply drafts (never sent). Can also filter mail by "
        "sender, topic, urgency, unread, or needs-reply."
    )
    persona = (
        "You are the Email Agent, a specialist sub-agent of Regina (team Prompt "
        "Queens), also known as the Inbox Brief Specialist. You know the user's "
        "inbox and nothing else. Follow the email-brief skill exactly: triage "
        "from metadata, collapse by conversation, summarise in EXACTLY 3 "
        "bullets, pick the 3 most important actions, create drafts only and "
        "never send, delete or move mail. Never invent facts, names or numbers; "
        "use [PLACEHOLDER] for anything only the user can confirm."
    )

    # ---- prompts --------------------------------------------------------------

    def system_prompt(self, live_mcp: bool = False) -> str:
        """Persona + the email-brief skill. With live_mcp the agent reads mail
        through the Exchange MCP tools instead of the embedded fixture."""
        head = (
            f"{self.persona}\n\n"
            f"Today's date is {config.today().isoformat()} and the current time is "
            f"{config.now().strftime('%H:%M')}.\n\n"
            "You answer briefs from Regina, the orchestrator. Reply with the JSON "
            "output contract followed by a short markdown briefing; no preamble, "
            "no questions back.\n\n"
            "# The email-brief skill\n\n" + _skill_body() + "\n\n"
        )
        if live_mcp:
            return head + (
                "# Your data\n\nUse the Exchange MCP tools named in the skill "
                "(get-current-user, list-mail-folder-messages, get-mail-message, "
                "create-reply-draft, create-reply-all-draft, create-draft-email)."
            )
        return head + (
            "# Your data (mock inbox, stands in for the email MCP)\n\n"
            "Treat the JSON below as the result of list-mail-folder-messages. "
            "`addressed` is To vs Cc, `flagged`/`focused` mirror Outlook flags, "
            "`conversation_id` groups threads, `draft_reply` is a draft you may "
            "'create' (report it under drafts_created with id draft-<email id>).\n\n"
            f"```json\n{json.dumps(self.raw, indent=2, ensure_ascii=False)}\n```"
        )

    # ---- data -----------------------------------------------------------------

    @property
    def emails(self) -> list[dict[str, Any]]:
        return self.raw["emails"]

    # ---- mock backend ---------------------------------------------------------

    def answer(self, brief: str) -> tuple[str, dict[str, Any]]:
        low = brief.lower()
        pool = list(self.emails)
        filters: list[str] = []

        sender = re.search(r"from ([A-Z][a-zA-Z'.-]+(?: [A-Z][a-zA-Z'.-]+)*)", brief)
        if sender:
            name = sender.group(1).lower()
            pool = [e for e in pool if name in e["from_name"].lower()]
            filters.append(f"from {sender.group(1)}")
        if re.search(r"\b(urgent|important|priority|high[- ]importance)\b", low) and not re.search(r"\b(brief|briefing|digest|summar)", low):
            pool = [e for e in pool if e["importance"] == "high"]
            filters.append("high importance")
        if "unread" in low:
            pool = [e for e in pool if e["unread"]]
            filters.append("unread")
        if re.search(r"\b(reply|replies|respond|answer)\b", low) and not re.search(r"\b(brief|briefing|digest|summar|inbox)", low):
            pool = [e for e in pool if e["needs_reply"]]
            filters.append("needs reply")
        topic = re.search(r"\b(?:about|regarding|mentioning|search for|search|on the topic of) ([^,.?]+)", low)
        if topic:
            terms = keywords(topic.group(1))
            if terms:
                pool = [e for e in pool if any(t in self._haystack(e) for t in terms)]
                filters.append("about " + " ".join(sorted(terms)))

        if not filters:
            return self.inbox_brief()

        pool = sorted(pool, key=self._rank)
        header = f"**Email Agent** — {len(pool)} email(s) matching: {', '.join(filters)}"
        if not pool:
            return header + "\n\nNothing in the inbox matches.", {"matched": [], "counts": self._counts()}
        return "\n".join([header, ""] + [self._render_email(e) for e in pool]), {"matched": pool, "counts": self._counts()}

    # ---- the email-brief procedure --------------------------------------------

    def triage(self) -> list[dict[str, Any]]:
        """Steps 2-3 of the skill: score threads from metadata, assign tiers."""
        threads: dict[str, dict[str, Any]] = {}
        for e in self.emails:
            # Collapse by conversation: keep the newest message per thread.
            current = threads.get(e["conversation_id"])
            if current is None or e["received_minutes_ago"] < current["received_minutes_ago"]:
                threads[e["conversation_id"]] = e
        scored = []
        for e in threads.values():
            text = f"{e['subject']} {e['body']}"
            to_me = e["addressed"] == "to"
            ask = bool(ASK_KEYWORDS.search(text))
            deadline = bool(URGENCY_KEYWORDS.search(text))
            vip = bool(VIP_LABELS & set(e["labels"]))
            in_window = e["received_minutes_ago"] <= WINDOW_HOURS * 60
            if e["flagged"] or (e["importance"] == "high" and to_me) or (e["unread"] and e["focused"] and to_me and ask):
                tier = "A"
            elif e["unread"] or e["focused"] or vip or e["needs_reply"]:
                tier = "B"
            else:
                tier = "C"
            # Ranking for actions: hard deadline > blocked on the user > seniority > explicit ask.
            score = (4 if deadline else 0) + (3 if e["needs_reply"] else 0) + (2 if vip else 0) + (1 if ask else 0) + (2 if tier == "A" else 0) + (1 if in_window else 0)
            scored.append({**e, "tier": tier, "score": score, "deadline": deadline, "ask": ask, "vip": vip, "in_window": in_window})
        return sorted(scored, key=lambda e: (-e["score"], e["tier"], e["received_minutes_ago"]))

    def inbox_brief(self) -> tuple[str, dict[str, Any]]:
        """Steps 4-7: 3 bullets, 3 actions, drafts only, JSON + markdown."""
        threads = self.triage()
        read = [t for t in threads if t["tier"] in {"A", "B"}][:READ_CAP]
        quiet = [t for t in threads if t["tier"] == "C"]

        actionable = [t for t in read if t["needs_reply"]][:3]
        actions, drafts = [], []
        for rank, t in enumerate(actionable, start=1):
            draft_id = f"draft-{t['id']}" if t.get("draft_reply") else None
            reasons = [r for r, flag in [("hard deadline", t["deadline"]), ("they're blocked on you", t["needs_reply"]), ("VIP sender", t["vip"]), ("explicit ask", t["ask"])] if flag]
            actions.append({
                "rank": rank,
                "action": f"Reply to {t['from_name']} re: {self._short_subject(t)}",
                "reason": "; ".join(reasons) or "awaiting your reply",
                "source_email_id": t["id"],
                "draft_id": draft_id,
            })
            if draft_id:
                subject = re.sub(r"^(re|fwd?):\s*", "", t["subject"], flags=re.IGNORECASE)
                drafts.append({"draft_id": draft_id, "subject": f"RE: {subject}", "to": [t["from_email"]], "body": t["draft_reply"]})

        bullets = [self._summary_bullet(t) for t in read[:3]]
        while len(bullets) < 3:
            bullets.append("Nothing else in the last 24h needs your attention.")

        counts = self._counts()
        generated_at = config.now().replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        contract = {
            "generated_at": generated_at,
            "user": self.raw.get("mailbox_owner", "you"),
            "provider": "mock",
            "window": {"since_hours": WINDOW_HOURS, "includes_unresolved": True},
            "summary_bullets": bullets,
            "priority_actions": actions,
            "drafts_created": [{k: v for k, v in d.items() if k != "body"} for d in drafts],
            "notes": [
                "auth_ok (mock inbox)",
                f"{counts['total']} messages scanned, {len(threads)} threads, {len(read)} read in full, "
                f"{sum(1 for t in threads if t['flagged'])} unresolved (flagged)",
                "drafts only — nothing was sent",
            ],
        }

        lines = [
            f"**Email Agent** — inbox brief ({counts['total']} emails, {counts['unread']} unread, {counts['needs_reply']} need a reply)",
            "",
            "Summary:",
            *[f"- {b}" for b in bullets],
            "",
            "Priority actions:",
            *([f"{a['rank']}. {a['action']} — {a['reason']}" + (f" (draft {a['draft_id']})" if a["draft_id"] else "") for a in actions] or ["- nothing needs a reply"]),
            "",
            "Drafts created (not sent):",
            *([f"- {d['draft_id']} → {d['to'][0]}: \"{d['body']}\"" for d in drafts] or ["- none"]),
        ]
        if quiet:
            lines += ["", "Tier C (skimmed, skipped): " + "; ".join(f"{t['from_name']} — {self._short_subject(t)}" for t in quiet)]
        lines += ["", "```json", json.dumps(contract, indent=2, ensure_ascii=False), "```"]

        matched = actionable + [t for t in read if t not in actionable and t["importance"] == "high"]
        data = {"brief": contract, "drafts": drafts, "matched": matched, "quiet": quiet, "counts": counts, "threads": threads}
        return "\n".join(lines), data

    # ---- helpers --------------------------------------------------------------

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
    def _short_subject(e: dict[str, Any]) -> str:
        return re.sub(r"^(re|fwd?):\s*", "", e["subject"], flags=re.IGNORECASE).split(" — ")[0]

    def _summary_bullet(self, t: dict[str, Any]) -> str:
        # Quote the sentence that carries the ask or deadline, else the opener.
        sentences = re.split(r"(?<=[.!?])\s", t["body"].strip())
        key = next((x for x in sentences if URGENCY_KEYWORDS.search(x) or ASK_KEYWORDS.search(x)), sentences[0])
        when = humanize_minutes(t["received_minutes_ago"])
        need = "needs your reply" if t["needs_reply"] else "FYI"
        return f"**{t['from_name']}** — {self._short_subject(t)} ({when}, {need}): {key}"

    @staticmethod
    def _render_email(e: dict[str, Any]) -> str:
        flags = [f for f, on in [("needs reply", e["needs_reply"]), ("unread", e["unread"]), ("flagged", e.get("flagged"))] if on]
        flag_text = f" ({', '.join(flags)})" if flags else ""
        line = (
            f"- [{e['importance'].upper()}] **{e['from_name']}** — \"{e['subject']}\" "
            f"· {humanize_minutes(e['received_minutes_ago'])}{flag_text}\n"
            f"  {e['body'][:160].rstrip()}{'…' if len(e['body']) > 160 else ''}"
        )
        if e.get("draft_reply"):
            line += f"\n  Draft reply (not sent): \"{e['draft_reply']}\""
        return line
