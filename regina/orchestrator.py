"""
Regina — the orchestrator.

Regina never reads data herself. She delegates to the roster (Email Agent,
Calendar Agent, Anthropic News Agent), fans the delegations out in parallel,
and synthesises the reports.

Two modes, same public API:

- live: Claude (Messages API, tool use) decides which agents to call and
        writes the final answer. Every tool_use block in a turn is executed
        concurrently, so the trace shows the fan-out.
- mock: a deterministic router picks the agents and a template composes the
        answer from their structured data. Zero API calls; the demo always works.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Callable

from . import config
from .prompts import BRIEFING_BRIEFS, BRIEFING_REQUEST, build_system_prompt
from .subagents import SubagentReply, build_roster

EventHandler = Callable[[str, dict[str, Any]], None]

ROUTES = {
    "email": r"\b(email|emails|e-mail|inbox|mail|message|messages|reply|replies|respond|sent|unread|draft)\b",
    "calendar": r"\b(calendar|meeting|meetings|schedule|agenda|free|slot|slots|conflict|conflicts|deadline|deadlines|1:1|demo slot|busy|available)\b",
    "news": r"\b(anthropic|news|announce|announced|announcement|announcements|claude|release|released|launch|launched|ship|shipped|model|models|api|skills|managed agents)\b",
}
# Time words alone point at the calendar only when nothing else matched
# ("what did Anthropic ship this week" is news, "what's next?" is calendar).
CALENDAR_TIME_WORDS = r"\b(today|tomorrow|week|next|when|afternoon|morning|evening)\b"
BRIEFING_TRIGGER = r"\b(briefing|brief me|my day|on my plate|catch me up|start my day|morning)\b"


class Regina:
    name = config.ASSISTANT_NAME

    def __init__(
        self,
        mode: str | None = None,
        model: str | None = None,
        subagent_backend: str | None = None,
        on_event: EventHandler | None = None,
        client: Any = None,
    ) -> None:
        self.mode = config.resolve_mode(mode)
        self.model = model or config.MODEL
        self.on_event = on_event or (lambda kind, payload: None)
        self._client = client
        if self.mode == "live" and self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic()
        backend = subagent_backend or config.SUBAGENT_BACKEND
        if self.mode == "mock" and backend == "llm":
            backend = "mock"  # no credentials, no LLM sub-agents
        self.roster = build_roster(backend=backend, client=self._client)
        self.tools = [agent.tool_definition() for agent in self.roster.values()]
        self._by_tool = {agent.tool_name: agent for agent in self.roster.values()}
        self.system_prompt = build_system_prompt()
        self.history: list[dict[str, Any]] = []
        self.last_replies: dict[str, SubagentReply] = {}

    # ---- delegation -----------------------------------------------------------

    def delegate(self, key: str, brief: str) -> SubagentReply:
        agent = self.roster[key]
        self.on_event("delegate", {"agent": agent.name, "brief": brief})
        reply = agent.run(brief)
        self.on_event("reply", {"agent": agent.name, "elapsed_s": reply.elapsed_s, "backend": reply.backend, "chars": len(reply.text)})
        self.last_replies[key] = reply
        return reply

    def fan_out(self, briefs: dict[str, str]) -> dict[str, SubagentReply]:
        """Run several delegations concurrently. Order of the result matches `briefs`."""
        if not briefs:
            return {}
        self.on_event("fan_out", {"agents": [self.roster[k].name for k in briefs]})
        with ThreadPoolExecutor(max_workers=len(briefs)) as pool:
            futures = {key: pool.submit(self.delegate, key, brief) for key, brief in briefs.items()}
            return {key: fut.result() for key, fut in futures.items()}

    # ---- public API -----------------------------------------------------------

    def briefing(self) -> str:
        """The morning briefing: fan out to all three agents, synthesise."""
        if self.mode == "live":
            return self.ask(BRIEFING_REQUEST)
        replies = self.fan_out(BRIEFING_BRIEFS)
        self.on_event("synthesize", {"agents": [r.agent for r in replies.values()]})
        return compose_briefing(replies)

    def ask(self, message: str) -> str:
        """Conversational entry point. Keeps history across calls."""
        if self.mode == "live":
            return self._ask_live(message)
        return self._ask_mock(message)

    def reset(self) -> None:
        self.history.clear()

    # ---- live mode: Messages API tool-use loop --------------------------------

    def _ask_live(self, message: str) -> str:
        messages = self.history + [{"role": "user", "content": message}]
        final_text = ""
        for _ in range(8):  # hard stop on runaway loops
            self.on_event("thinking", {"model": self.model})
            response = self._client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[
                    # Stable prefix first so it caches; the volatile date goes after the breakpoint.
                    {"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": f"Today is {config.today().strftime('%A %Y-%m-%d')}, current time {config.now().strftime('%H:%M')}."},
                ],
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                tools=self.tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                final_text = "Regina can't help with that one."
                break
            if response.stop_reason != "tool_use":
                final_text = "".join(b.text for b in response.content if b.type == "text").strip()
                break

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            briefs = {b.id: (self._by_tool[b.name].key, b.input["brief"]) for b in tool_uses}
            self.on_event("fan_out", {"agents": [self.roster[k].name for k, _ in briefs.values()]})
            with ThreadPoolExecutor(max_workers=len(briefs)) as pool:
                futures = {tid: pool.submit(self.delegate, key, brief) for tid, (key, brief) in briefs.items()}
                results = [
                    {"type": "tool_result", "tool_use_id": tid, "content": fut.result().text}
                    for tid, fut in futures.items()
                ]
            messages.append({"role": "user", "content": results})
        else:
            final_text = "Regina hit the delegation limit for this request."

        self.on_event("synthesize", {"agents": []})
        self.history = messages
        return final_text

    # ---- mock mode: deterministic router + templates -------------------------

    def route(self, message: str) -> list[str]:
        low = message.lower()
        if re.search(BRIEFING_TRIGGER, low):
            return ["email", "calendar", "news"]
        picked = [key for key, pattern in ROUTES.items() if re.search(pattern, low)]
        if not picked and re.search(CALENDAR_TIME_WORDS, low):
            picked = ["calendar"]
        return picked or ["email", "calendar", "news"]

    def _ask_mock(self, message: str) -> str:
        keys = self.route(message)
        if re.search(BRIEFING_TRIGGER, message.lower()):
            answer = self.briefing()
        else:
            replies = self.fan_out({key: message for key in keys})
            self.on_event("synthesize", {"agents": [r.agent for r in replies.values()]})
            parts = [r.text for r in replies.values()]
            answer = f"Here is what the roster found, {config.USER_NAME}:\n\n" + "\n\n".join(parts)
        self.history += [{"role": "user", "content": message}, {"role": "assistant", "content": answer}]
        return answer


# ---- briefing composer (mock mode) ------------------------------------------------


def compose_briefing(replies: dict[str, SubagentReply]) -> str:
    """Build the briefing from the agents' structured data, following the skill."""
    email = replies["email"].data
    cal = replies["calendar"].data
    news = replies["news"].data
    today = config.today()
    conflicted = {e["id"] for pair in cal.get("conflicts", []) for e in pair}

    attention: list[str] = []
    for e in email.get("matched", [])[:3]:
        age = _age(e["received_minutes_ago"])
        need = "needs a reply" if e["needs_reply"] else "FYI"
        attention.append(f"- **Email** — {e['from_name']}: \"{e['subject']}\" ({age}, {need}).")
    for d in cal.get("deadlines", [])[:2]:
        when = "today" if d["day_offset"] == 0 else ("tomorrow" if d["day_offset"] == 1 else (today + timedelta(days=d["day_offset"])).strftime("%A"))
        attention.append(f"- **Calendar** — deadline {when} {d['start']}: {d['title']}.")
    for a, b in cal.get("conflicts", [])[:1]:
        attention.append(f"- **Calendar** — CONFLICT {a['start']}–{a['end']} \"{a['title']}\" overlaps \"{b['title']}\" ({b['start']}–{b['end']}). Keep the client meeting, join the other late.")
    attention = attention[:5]

    schedule = []
    for e in cal.get("events", []):
        span = e["start"] if e["type"] == "deadline" else f"{e['start']}–{e['end']}"
        flag = " ⚠ CONFLICT" if e["id"] in conflicted else ""
        schedule.append(f"- {span} {e['title']}{flag}")
    free = cal.get("free", [])
    free_line = ""
    if free:
        s, f = max(free, key=lambda x: _span(x))
        free_line = f"\nBiggest free block: {s}–{f}."

    news_lines = [f"- **{i['title']}** ({i['published_days_ago']}d ago) — {i['why_it_matters']}" for i in news.get("items", [])[:3]]
    drafts = [f"- To {e['from_name']}: \"{e['draft_reply']}\"" for e in email.get("matched", []) if e.get("draft_reply")]
    quiet = email.get("quiet", [])

    top = (email.get("matched") or [None])[0]
    headline = (f"{top['from_name']} needs \"{top['subject'].split(' — ')[0]}\" handled first"
                if top else "A quiet day: protect your focus block")
    if cal.get("conflicts"):
        headline += ", and you have a calendar conflict this afternoon"

    lines = [
        f"# Regina's briefing — {today.strftime('%A, %B %d, %Y')}",
        "",
        f"{config.USER_NAME}, {headline}.",
        "",
        "## Needs your attention",
        *(attention or ["- Nothing urgent."]),
        "",
        "## Today's schedule",
        *(schedule or ["- Nothing on the calendar."]),
        free_line.strip(),
        "",
        "## Anthropic news worth 30 seconds",
        *(news_lines or ["- Nothing new this week."]),
        "",
        "## Suggested replies",
        *(drafts or ["- Nothing needs a reply."]),
    ]
    if quiet:
        lines += ["", f"Quiet noise: {len(quiet)} low-priority emails (newsletters, reminders) parked."]
    return "\n".join(line for line in lines if line is not None)


def _age(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 1440:
        return f"{minutes // 60}h ago"
    return "yesterday" if minutes < 2880 else f"{minutes // 1440}d ago"


def _span(slot: tuple[str, str]) -> int:
    (s, f) = slot
    to_min = lambda t: int(t[:2]) * 60 + int(t[3:])  # noqa: E731
    return to_min(f) - to_min(s)
