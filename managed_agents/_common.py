"""Shared helpers for the Managed Agents scripts."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python managed_agents/x.py` from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regina import config  # noqa: E402

ENV_ID = ROOT / ".environment_id"
SUBAGENT_IDS = ROOT / ".subagent_ids.json"
ORCHESTRATOR_ID = ROOT / ".orchestrator_id"
SKILL_IDS = ROOT / ".skill_ids.json"
LAST_SESSION = ROOT / ".last_session_id"


def client():
    if not config.has_credentials():
        raise SystemExit("Set ANTHROPIC_API_KEY (or run `ant auth login`) before running.")
    from anthropic import Anthropic

    return Anthropic()


def read_id(path: Path, hint: str) -> str:
    if not path.exists():
        raise SystemExit(f"Missing {path.name}. {hint}")
    return path.read_text().strip()


__all__ = ["ROOT", "config", "client", "read_id", "ENV_ID", "SUBAGENT_IDS", "ORCHESTRATOR_ID", "SKILL_IDS", "LAST_SESSION", "os"]
