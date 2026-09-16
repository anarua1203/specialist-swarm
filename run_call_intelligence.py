"""
Run the Call Intelligence agent on a transcript (Managed Agents prototype).

Reuses the system prompt from .claude/agents/call-intelligence.md so the
Claude Code subagent and the cloud agent stay in sync. The agent is created
once and its ID cached in .call_intel_agent_id. The environment is shared
with the Deal Desk via .environment_id.

Usage:
    python run_call_intelligence.py <transcript.md> "<your name>"
    python run_call_intelligence.py synthetic-data/call-transcript-acme-sync.md "Jordan Lee"
"""

import sys
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv


AGENT_PROMPT_PATH = Path(".claude/agents/call-intelligence.md")
AGENT_ID_PATH = Path(".call_intel_agent_id")
ENVIRONMENT_ID_PATH = Path(".environment_id")
OUTPUT_DIR = Path("outputs")


def load_system_prompt() -> str:
    # Drop the YAML frontmatter; the body is the system prompt.
    _, _, body = AGENT_PROMPT_PATH.read_text().split("---", 2)
    return body.strip()


def get_or_create_environment(client: Anthropic) -> str:
    if ENVIRONMENT_ID_PATH.exists():
        return ENVIRONMENT_ID_PATH.read_text().strip()
    environment = client.beta.environments.create(
        name="specialist-swarm-env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    ENVIRONMENT_ID_PATH.write_text(environment.id)
    print(f"Environment created: {environment.id}")
    return environment.id


def get_or_create_agent(client: Anthropic) -> str:
    system = load_system_prompt()
    if AGENT_ID_PATH.exists():
        agent_id = AGENT_ID_PATH.read_text().strip()
        agent = client.beta.agents.retrieve(agent_id)
        # Keep the cloud agent in sync with edits to the local prompt file.
        if agent.system != system:
            client.beta.agents.update(agent_id, version=agent.version, system=system)
            print(f"Agent prompt updated: {agent_id}")
        return agent_id
    agent = client.beta.agents.create(
        name="Call Intelligence Analyst",
        model="claude-opus-5",
        system=system,
        tools=[{"type": "agent_toolset_20260401"}],
        metadata={"track": "specialist-swarm", "role": "call-intelligence"},
    )
    AGENT_ID_PATH.write_text(agent.id)
    print(f"Agent created: {agent.id}")
    return agent.id


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    transcript_path, me = Path(sys.argv[1]), sys.argv[2]
    if not transcript_path.exists():
        raise SystemExit(f"Transcript not found: {transcript_path}")

    load_dotenv()  # reads ANTHROPIC_API_KEY from .env
    client = Anthropic()

    environment_id = get_or_create_environment(client)
    agent_id = get_or_create_agent(client)
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title=f"Call Intelligence: {transcript_path.name}",
    )
    print(f"Session: {session.id}\n")

    user_message = (
        f'"Me" is {me}. Analyze the transcript below. Return the full report '
        "as your reply, starting with the # title line. No preamble, no code fence, do not write files.\n\n"
        f"=====  TRANSCRIPT: {transcript_path.name}  =====\n"
        f"{transcript_path.read_text()}"
    )

    report_parts: list[str] = []
    # Stream-first: open the stream, then send, so no early events are missed.
    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": user_message}]}],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if block.type == "text":
                        report_parts.append(block.text)
                        print(block.text, end="", flush=True)
            elif event.type == "session.status_terminated":
                break
            elif event.type == "session.status_idle":
                # Idle can be transient; only stop when the agent isn't waiting on us.
                if event.stop_reason.type != "requires_action":
                    break

    OUTPUT_DIR.mkdir(exist_ok=True)
    me_slug = me.lower().replace(" ", "-")
    out_path = OUTPUT_DIR / f"call-intel-{transcript_path.stem}-{me_slug}.md"
    out_path.write_text("".join(report_parts))
    print(f"\n\nReport saved to {out_path}")


if __name__ == "__main__":
    main()
