"""
Create Regina's three sub-agents as Managed Agents.

Each sub-agent is built from the SAME class the local orchestrator uses
(regina/subagents/*): same persona, same mock fixture embedded in its system
prompt. The roster is therefore identical whether Regina runs locally or as a
Managed Agents coordinator.

Saves IDs to .subagent_ids.json.

Usage:
    python managed_agents/create_subagents.py
"""

import json

from _common import SUBAGENT_IDS, client, config
from regina.subagents import ROSTER


def main() -> None:
    api = client()
    ids: dict[str, str] = {}
    for cls in ROSTER:
        agent_obj = cls(backend="mock")
        created = api.beta.agents.create(
            name=agent_obj.name,
            description=agent_obj.description,
            model=config.SUBAGENT_MODEL,
            system=agent_obj.system_prompt(),
            # Sub-agents only need to answer from their data: no bash, files or web.
            tools=[{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}],
            metadata={**config.MANAGED_AGENTS_METADATA, "role": cls.key},
        )
        ids[cls.key] = created.id
        print(f"  Created {agent_obj.name:24s} -> {created.id}")
    SUBAGENT_IDS.write_text(json.dumps(ids, indent=2))
    print(f"\nSaved {len(ids)} sub-agent IDs to {SUBAGENT_IDS.name}")
    print("Next: python managed_agents/create_orchestrator.py")


if __name__ == "__main__":
    main()
