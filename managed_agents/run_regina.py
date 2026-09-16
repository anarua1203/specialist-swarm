"""
Run Regina as a Managed Agents session and stream the events.

Watch for session.thread_created × 3 and the parallel replies: the visible
fan-out is the demo. Saves the briefing to outputs/briefing-<date>-managed.md.

Usage:
    python managed_agents/run_regina.py                       # morning briefing
    python managed_agents/run_regina.py "Any conflicts today?" # ask anything
"""

import sys

from _common import ENV_ID, LAST_SESSION, ORCHESTRATOR_ID, client, config, read_id
from regina.prompts import BRIEFING_REQUEST


def main() -> None:
    api = client()
    orchestrator_id = read_id(ORCHESTRATOR_ID, "Run managed_agents/create_orchestrator.py first.")
    environment_id = read_id(ENV_ID, "Run managed_agents/setup_environment.py first.")
    request = " ".join(sys.argv[1:]).strip() or BRIEFING_REQUEST
    request += f"\n\n(Today is {config.today().strftime('%A %Y-%m-%d')}, current time {config.now().strftime('%H:%M')}.)"

    session = api.beta.sessions.create(
        agent=orchestrator_id,
        environment_id=environment_id,
        title=f"Regina — {config.today().isoformat()}",
    )
    LAST_SESSION.write_text(session.id)
    print(f"Session {session.id}\n\n=== EVENT STREAM ===\n")

    text_parts: list[str] = []
    mid_text = False  # so event lines start on their own line after streamed text

    def event_line(line: str) -> None:
        nonlocal mid_text
        if mid_text:
            print()
            mid_text = False
        print(line, flush=True)

    with api.beta.sessions.events.stream(session.id) as stream:
        api.beta.sessions.events.send(
            session.id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": request}]}],
        )
        for event in stream:
            t = event.type
            if t == "session.thread_created":
                event_line(f"  [thread spawned]   {getattr(event, 'agent_name', '?')}")
            elif t == "session.thread_status_running":
                event_line(f"  [thread running]   {getattr(event, 'agent_name', '?')}")
            elif t == "agent.thread_message_sent":
                event_line(f"  [delegate →]       {getattr(event, 'to_agent_name', '?')}")
            elif t == "agent.thread_message_received":
                event_line(f"  [reply ←]          {getattr(event, 'from_agent_name', '?')}")
            elif t == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        text_parts.append(block.text)
                        print(block.text, end="", flush=True)
                        mid_text = True
            elif t == "session.status_idle":
                event_line("\n[session idle — done]")
                break

    config.OUTPUT_DIR.mkdir(exist_ok=True)
    out = config.OUTPUT_DIR / f"briefing-{config.today().isoformat()}-managed.md"
    out.write_text("".join(text_parts))
    print(f"\nSaved to {out.relative_to(config.ROOT)}")
    print(f"Full session with sub-agent threads: https://platform.claude.com/sessions/{session.id}")


if __name__ == "__main__":
    main()
