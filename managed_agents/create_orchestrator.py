"""
Create Regina as a Managed Agents coordinator with the three sub-agents in
her roster, and attach the regina-briefing skill.

Saves the coordinator ID to .orchestrator_id.

Usage:
    python managed_agents/create_orchestrator.py
"""

import json

from _common import ORCHESTRATOR_ID, SKILL_IDS, SUBAGENT_IDS, client, config, read_id
from anthropic.lib import files_from_dir
from regina.prompts import build_system_prompt

SKILL_DIR = config.SKILL_PATH.parent
SKILL_TITLE = "Regina Briefing"


def upload_skill(api) -> str:
    """Upload skills/regina-briefing once; reuse on re-runs (titles must be unique)."""
    # Skills API is out of beta: client.skills.*, display_name (not display_title).
    for skill in api.skills.list(source="custom"):
        if skill.display_name == SKILL_TITLE:
            print(f"Reusing skill {SKILL_TITLE}: {skill.id}")
            return skill.id
    skill = api.skills.create(display_name=SKILL_TITLE, files=files_from_dir(str(SKILL_DIR)))
    print(f"Uploaded skill {SKILL_TITLE}: {skill.id}")
    return skill.id


def main() -> None:
    api = client()
    read_id(SUBAGENT_IDS, "Run managed_agents/create_subagents.py first.")
    subagent_ids = json.loads(SUBAGENT_IDS.read_text())

    skill_id = upload_skill(api)
    SKILL_IDS.write_text(json.dumps({"regina-briefing": skill_id}, indent=2))

    # The coordinator prompt is the local one minus nothing: the Managed Agents
    # delegation tools (list_agents / send_to_agent) replace the ask_*_agent tools.
    system = build_system_prompt().replace(
        "Delegate to every relevant agent AT THE SAME TIME (issue all tool calls in\n   one turn).",
        "Delegate to every relevant agent AT THE SAME TIME with send_to_agent (one\n   call per agent, all in the same turn).",
    )

    coordinator = api.beta.agents.create(
        name=config.ASSISTANT_NAME,
        description="Personal assistant orchestrator built by team Prompt Queens.",
        model=config.MODEL,
        system=system,
        tools=[{"type": "agent_toolset_20260401"}],
        skills=[{"type": "custom", "skill_id": skill_id, "version": "latest"}],
        multiagent={
            "type": "coordinator",
            "agents": [{"type": "agent", "id": agent_id} for agent_id in subagent_ids.values()],
        },
        metadata={**config.MANAGED_AGENTS_METADATA, "role": "orchestrator"},
    )
    ORCHESTRATOR_ID.write_text(coordinator.id)
    print(f"Orchestrator created: {coordinator.id}")
    print(f"Roster: {list(subagent_ids)}")
    print("Next: python managed_agents/setup_environment.py (once), then python managed_agents/run_regina.py")


if __name__ == "__main__":
    main()
