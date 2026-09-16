"""
Create the Calendar Intelligence Specialist agent.

This specialist receives pre-ranked calendar events and generates a structured
daily briefing with grounded preparation actions for each event.

The agent is added to the coordinator's roster so it can be delegated to when
the deal desk coordinator needs today's schedule context.

Saves the agent ID to .calendar_agent_id.

Usage:
    python create_calendar_agent.py
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic
from anthropic.lib import files_from_dir
from dotenv import load_dotenv

load_dotenv()

SKILL_DIR = Path("skills/calendar-prep-framework")
SKILL_DISPLAY_TITLE = "Calendar Prep Framework"

CALENDAR_AGENT_SYSTEM = """\
You are the Calendar Intelligence Specialist. Your only job is to receive
pre-ranked calendar events for today and produce a structured daily briefing.

You will receive:
- Today's date
- Up to 5 calendar events, already ranked by priority score (rank 1 = highest)
- For each event: title, time range, attendees (name + email), description, and
  any attachment names or content previews

Your calendar-prep-framework skill defines your action categories, grounding
rules, and the exact JSON schema to output.

Rules you must follow:
1. Output ONLY the JSON object described in your skill. No prose before or after.
2. Preserve the provided rank, priority scores, and signals exactly — do not
   recalculate or reorder.
3. Every prep action must cite a grounded_in field referencing real source data.
4. Never invent facts, names, or document titles not present in the input.
5. Generate 3–5 prep actions per event, proportionate to its rank.
"""


def _get_or_upload_skill(client: Anthropic) -> str:
    print("Checking for existing calendar skill...")
    for page in client.beta.skills.list(source="custom"):
        if page.display_title == SKILL_DISPLAY_TITLE:
            print(f"  Reusing existing skill: {page.id}")
            return page.id

    if not (SKILL_DIR / "SKILL.md").exists():
        raise SystemExit(f"Skill not found at {SKILL_DIR}/SKILL.md")

    print(f"  Uploading {SKILL_DIR}...")
    skill = client.beta.skills.create(
        display_title=SKILL_DISPLAY_TITLE,
        files=files_from_dir(str(SKILL_DIR)),
    )
    print(f"  -> {skill.id}")
    return skill.id


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    client = Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )

    skill_id = _get_or_upload_skill(client)

    # Check if we already have an agent from a previous run
    agent_id_path = Path(".calendar_agent_id")
    if agent_id_path.exists():
        existing_id = agent_id_path.read_text().strip()
        print(f"Calendar agent already exists: {existing_id}")
        print("(remove .calendar_agent_id to create a new one)")
        return

    agent = client.beta.agents.create(
        name="Calendar Intelligence Specialist",
        model="claude-sonnet-4-6",
        system=CALENDAR_AGENT_SYSTEM,
        tools=[{"type": "agent_toolset_20260401"}],
        skills=[{"type": "custom", "skill_id": skill_id, "version": "latest"}],
        metadata={
            "hackathon": "partner-basecamp-2026",
            "track": "specialist-swarm",
            "role": "calendar_intelligence",
        },
    )

    agent_id_path.write_text(agent.id)
    print(f"Calendar Intelligence Specialist created: {agent.id}")

    # Append to specialist_ids.json so the coordinator can reference it
    specialist_ids_path = Path(".specialist_ids.json")
    if specialist_ids_path.exists():
        ids = json.loads(specialist_ids_path.read_text())
        ids["calendar_intelligence"] = agent.id
        specialist_ids_path.write_text(json.dumps(ids, indent=2))
        print(f"Added to .specialist_ids.json as 'calendar_intelligence'")
        print("Re-run create_coordinator.py to include it in the coordinator's roster.")
    else:
        print("No .specialist_ids.json found — run create_specialists.py first if using the full swarm.")

    print(f"\nNext: python run_calendar_agent.py")


if __name__ == "__main__":
    main()
