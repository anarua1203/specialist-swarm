"""
Central configuration for Regina.

Everything is env-driven so the same code runs offline (mock) at the hackathon
table and live (Claude API) when a key is available. See .env.example.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

ASSISTANT_NAME = "Regina"
TEAM_NAME = "Prompt Queens"

MOCK_DATA_DIR = ROOT / "mock_data"
OUTPUT_DIR = ROOT / "outputs"
SKILL_PATH = ROOT / "skills" / "regina-briefing" / "SKILL.md"

# Orchestrator model for the Messages API loop. Opus 5 is the default seat for
# the coordinator; sub-agents (when LLM-backed) run on Sonnet 5.
MODEL = os.environ.get("REGINA_MODEL", "claude-opus-5")
SUBAGENT_MODEL = os.environ.get("REGINA_SUBAGENT_MODEL", "claude-sonnet-5")

# mock = deterministic answers computed over the fixtures (no API calls)
# llm  = each sub-agent is a Claude call with its fixture as context
SUBAGENT_BACKEND = os.environ.get("REGINA_SUBAGENTS", "mock").lower()

USER_NAME = os.environ.get("REGINA_USER_NAME", "Your Majesty")

# Managed Agents scripts tag every object they create with this metadata.
MANAGED_AGENTS_METADATA = {
    "hackathon": "partner-basecamp-2026",
    "team": "prompt-queens",
    "assistant": "regina",
}


def today() -> date:
    """Today's date, or the pinned REGINA_TODAY for reproducible demos/tests."""
    pinned = os.environ.get("REGINA_TODAY", "").strip()
    if pinned:
        return date.fromisoformat(pinned)
    return date.today()


def now() -> datetime:
    """Current time. When REGINA_TODAY is pinned, 'now' is 10:00 on that day."""
    pinned = os.environ.get("REGINA_TODAY", "").strip()
    if pinned:
        return datetime.combine(date.fromisoformat(pinned), datetime.min.time()).replace(hour=10)
    return datetime.now()


def has_credentials() -> bool:
    """True when the Anthropic SDK will find a credential without help."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    # `ant auth login` stores a profile the SDK reads automatically.
    profile_dir = Path.home() / ".config" / "anthropic"
    return profile_dir.exists() and any(profile_dir.iterdir())


def resolve_mode(override: str | None = None) -> str:
    """Return 'mock' or 'live'. Precedence: explicit override > REGINA_MODE > auto-detect."""
    mode = (override or os.environ.get("REGINA_MODE", "auto")).lower()
    if mode == "auto":
        return "live" if has_credentials() else "mock"
    if mode not in {"mock", "live"}:
        raise ValueError(f"Unknown REGINA_MODE {mode!r}; use auto, mock or live.")
    return mode
