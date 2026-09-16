"""
Create Regina's three sub-agents as Managed Agents.

Each sub-agent is built from the SAME class the local orchestrator uses
(regina/subagents/*): same persona, same mock fixture embedded in its system
prompt. The roster is therefore identical whether Regina runs locally or as a
Managed Agents coordinator.

The Email Agent follows the email-brief skill (skills/email-brief), which is
uploaded and attached here. Point it at a live Outlook/Exchange MCP with
EXCHANGE_MCP_URL (and EXCHANGE_MCP_TOKEN if it needs a bearer token); when
unset it answers from the mock inbox embedded in its system prompt, so the
default demo is unaffected.

Saves IDs to .subagent_ids.json.

Usage:
    python managed_agents/create_subagents.py
"""

import json
import os

from _common import SUBAGENT_IDS, client, config
from anthropic.lib import files_from_dir
from regina.subagents import ROSTER

EMAIL_SKILL_DIR = config.ROOT / "skills" / "email-brief"
EMAIL_SKILL_TITLE = "Email Brief"


def email_mcp_servers() -> list[dict]:
    """Optional remote email MCP for the Email Agent (Outlook/Exchange today;
    a Gmail MCP can be swapped in without changing the skill)."""
    url = os.environ.get("EXCHANGE_MCP_URL")
    if not url:
        return []
    server: dict = {"type": "url", "url": url, "name": "exchange"}
    token = os.environ.get("EXCHANGE_MCP_TOKEN")
    if token:
        server["authorization_token"] = token
    return [server]


def upload_email_skill(api) -> str:
    """Upload skills/email-brief once; reuse on re-runs (display names must be unique)."""
    for skill in api.skills.list(source="custom"):
        if skill.display_name == EMAIL_SKILL_TITLE:
            print(f"  Reusing skill {EMAIL_SKILL_TITLE}: {skill.id}")
            return skill.id
    skill = api.skills.create(display_name=EMAIL_SKILL_TITLE, files=files_from_dir(str(EMAIL_SKILL_DIR)))
    print(f"  Uploaded skill {EMAIL_SKILL_TITLE}: {skill.id}")
    return skill.id


def main() -> None:
    api = client()
    email_skill_id = upload_email_skill(api)
    mcp_servers = email_mcp_servers()
    ids: dict[str, str] = {}
    for cls in ROSTER:
        agent_obj = cls(backend="mock")
        kwargs: dict = dict(
            name=agent_obj.name,
            description=agent_obj.description,
            model=config.SUBAGENT_MODEL,
            system=agent_obj.system_prompt(),
            # Sub-agents only need to answer from their data: no bash, files or web.
            tools=[{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}],
            metadata={**config.MANAGED_AGENTS_METADATA, "role": cls.key},
        )
        if cls.key == "email":
            # The skill is attached via the Skills API, so keep it out of the prompt.
            kwargs["skills"] = [{"type": "custom", "skill_id": email_skill_id, "version": "latest"}]
            kwargs["system"] = agent_obj.system_prompt(live_mcp=bool(mcp_servers), include_skill=False)
            if mcp_servers:
                # Live inbox: read mail through the MCP instead of the embedded fixture.
                kwargs["mcp_servers"] = mcp_servers
                kwargs["tools"].append({"type": "mcp_toolset", "mcp_server_name": "exchange"})
        created = api.beta.agents.create(**kwargs)
        ids[cls.key] = created.id
        extra = " (Exchange MCP attached)" if cls.key == "email" and mcp_servers else ""
        print(f"  Created {agent_obj.name:24s} -> {created.id}{extra}")
    SUBAGENT_IDS.write_text(json.dumps(ids, indent=2))
    print(f"\nSaved {len(ids)} sub-agent IDs to {SUBAGENT_IDS.name}")
    print("Next: python managed_agents/create_orchestrator.py")


if __name__ == "__main__":
    main()
