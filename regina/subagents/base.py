"""
Base class for Regina's sub-agents.

A sub-agent is a black box to the orchestrator: it receives a natural-language
brief and returns a report. Two backends share the same interface:

- mock: deterministic answer computed over the JSON fixture (offline demo)
- llm:  a Claude call on SUBAGENT_MODEL with the fixture as context

The same persona + fixture also feed managed_agents/create_subagents.py, so the
Managed Agents roster is built from exactly these classes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .. import config


@dataclass
class SubagentReply:
    agent: str
    brief: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    elapsed_s: float = 0.0
    backend: str = "mock"


class Subagent:
    key: str = ""
    name: str = ""
    tool_name: str = ""
    description: str = ""
    persona: str = ""
    fixture_file: str = ""

    def __init__(self, backend: str | None = None, client: Any = None) -> None:
        self.backend = (backend or config.SUBAGENT_BACKEND).lower()
        self._client = client
        self.raw = json.loads((config.MOCK_DATA_DIR / self.fixture_file).read_text())

    # ---- public API used by the orchestrator ---------------------------------

    def run(self, brief: str) -> SubagentReply:
        start = time.perf_counter()
        if self.backend == "llm":
            text, data = self._answer_llm(brief), {}
        else:
            text, data = self.answer(brief)
        return SubagentReply(
            agent=self.name,
            brief=brief,
            text=text,
            data=data,
            elapsed_s=time.perf_counter() - start,
            backend=self.backend,
        )

    def tool_definition(self) -> dict[str, Any]:
        """Messages API tool the orchestrator uses to delegate to this agent."""
        return {
            "name": self.tool_name,
            "description": (
                f"Delegate a task to the {self.name}. {self.description} "
                "The agent cannot see the conversation, so the brief must be "
                "self-contained: say what you need and in what shape."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "brief": {
                        "type": "string",
                        "description": "A self-contained natural-language task for the agent.",
                    }
                },
                "required": ["brief"],
                "additionalProperties": False,
            },
            "strict": True,
        }

    def system_prompt(self) -> str:
        """Persona + data. Used by the llm backend and by Managed Agents."""
        return (
            f"{self.persona.strip()}\n\n"
            "Today's date is "
            f"{config.today().isoformat()} and the current time is "
            f"{config.now().strftime('%H:%M')}.\n\n"
            "You answer briefs from Regina, the orchestrator. Reply with a "
            "compact markdown report: no preamble, no questions back, only "
            "facts from your data. If nothing matches, say so in one line.\n\n"
            f"# Your data ({self.fixture_file})\n\n```json\n"
            f"{json.dumps(self.raw, indent=2, ensure_ascii=False)}\n```"
        )

    # ---- to implement per agent ---------------------------------------------

    def answer(self, brief: str) -> tuple[str, dict[str, Any]]:
        raise NotImplementedError

    # ---- llm backend ---------------------------------------------------------

    def _answer_llm(self, brief: str) -> str:
        if self._client is None:
            from anthropic import Anthropic

            self._client = Anthropic()
        response = self._client.messages.create(
            model=config.SUBAGENT_MODEL,
            max_tokens=4000,
            system=[
                {
                    "type": "text",
                    "text": self.system_prompt(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": brief}],
        )
        if response.stop_reason == "refusal":
            return f"{self.name} declined this brief."
        return "".join(b.text for b in response.content if b.type == "text").strip()


# ---- small helpers shared by the mock agents ---------------------------------

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "me",
    "my", "i", "you", "your", "is", "are", "was", "with", "about", "from",
    "give", "get", "show", "list", "tell", "find", "what", "which", "who",
    "any", "all", "please", "one", "line", "each", "top", "last", "next",
    "days", "day", "week", "today", "tomorrow", "this", "that", "it", "its",
    "do", "does", "have", "has", "need", "needs", "into", "by", "as", "be",
    "brief", "report", "summary", "digest", "full", "every", "with", "their",
}


def keywords(text: str) -> set[str]:
    words = {w.strip(".,;:!?()'\"") for w in text.lower().split()}
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def humanize_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60}m ago" if minutes % 60 else f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago" if days > 1 else "yesterday"
