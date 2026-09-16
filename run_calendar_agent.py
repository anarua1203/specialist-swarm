"""
Run the Calendar Intelligence Specialist against today's two calendar accounts.

Pipeline:
  Phase 1 (Python)       — Fetch events from M365 + Google, score + rank top 5
  Phase 2 (Managed Agent) — Calendar agent generates grounded prep actions
  Phase 3 (LLM Judge)    — Eval judge scores the output on 4 dimensions

Outputs:
  outputs/calendar-briefing-YYYY-MM-DD.json   — full structured briefing + eval
  outputs/calendar-briefing-YYYY-MM-DD.md     — human-readable markdown

Usage:
    python run_calendar_agent.py

    # Skip live calendar fetch (uses mock events for testing):
    python run_calendar_agent.py --mock

Required env vars (non-mock mode):
    ANTHROPIC_API_KEY
    AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, M365_USER_EMAIL
    GOOGLE_CREDENTIALS_JSON [, GOOGLE_DELEGATED_EMAIL, GOOGLE_CALENDAR_ID]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

from calendar_agent.eval_judge import run_eval
from calendar_agent.priority_scorer import rank_events
from calendar_agent.schemas import (
    Attachment,
    Attendee,
    CalendarEvent,
    DailyBriefing,
    EventBriefing,
    PrepAction,
    ScoredEvent,
)

load_dotenv()

OUTPUT_DIR = Path("outputs")


# ---------------------------------------------------------------------------
# Mock data — used with --mock for testing without live calendar credentials
# ---------------------------------------------------------------------------

def _mock_events(today: date) -> list[CalendarEvent]:
    def dt(h: int, m: int = 0) -> datetime:
        return datetime(today.year, today.month, today.day, h, m, tzinfo=timezone.utc)

    return [
        CalendarEvent(
            event_id="mock-1",
            title="ACME Corp Quarterly Business Review",
            start=dt(10),
            end=dt(11, 30),
            attendees=(
                Attendee("Sarah Chen", "sarah.chen@acme.com"),
                Attendee("James Park (VP Sales)", "james.park@acme.com"),
                Attendee("Jenny Park", "jenny.park@capgemini.com", is_organiser=True),
            ),
            description=(
                "Q3 QBR with ACME leadership. Agenda: Q3 KPI review, "
                "renewal discussion, and roadmap preview. Deck attached."
            ),
            location="Teams",
            source="m365",
            attachments=(
                Attachment("ACME-Q3-QBR-Deck.pptx", "application/vnd.ms-powerpoint"),
            ),
        ),
        CalendarEvent(
            event_id="mock-2",
            title="Internal Pricing Review — Deal Desk",
            start=dt(9),
            end=dt(9, 30),
            attendees=(
                Attendee("Tom Reyes", "tom.reyes@capgemini.com"),
                Attendee("Jenny Park", "jenny.park@capgemini.com", is_organiser=True),
            ),
            description="Weekly pricing review for active deals. Bring the discount tracker.",
            location="",
            source="google",
        ),
        CalendarEvent(
            event_id="mock-3",
            title="Board Update Prep Call",
            start=dt(14),
            end=dt(14, 45),
            attendees=(
                Attendee("Lena Müller (MD)", "lena.muller@capgemini.com"),
                Attendee("Jenny Park", "jenny.park@capgemini.com", is_organiser=True),
                Attendee("David Kim", "david.kim@capgemini.com"),
            ),
            description=(
                "Prep call before Thursday's board presentation. "
                "Deadline: slides locked by EOD today."
            ),
            location="Room 3B",
            source="m365",
        ),
        CalendarEvent(
            event_id="mock-4",
            title="Lunch — team standup",
            start=dt(12, 30),
            end=dt(13),
            attendees=(
                Attendee("Jenny Park", "jenny.park@capgemini.com"),
            ),
            description="Informal team sync over lunch. No agenda.",
            location="Canteen",
            source="google",
        ),
        CalendarEvent(
            event_id="mock-5",
            title="Competitive Intel Update — Proposal for Globex",
            start=dt(15, 30),
            end=dt(16),
            attendees=(
                Attendee("Rachel Wong", "rachel.wong@capgemini.com"),
                Attendee("Jenny Park", "jenny.park@capgemini.com"),
            ),
            description=(
                "Finalise competitive positioning for the Globex proposal due Friday. "
                "Focus: differentiation against key competitors on cloud migration scope."
            ),
            location="",
            source="m365",
        ),
    ]


# ---------------------------------------------------------------------------
# Phase 1: fetch + score
# ---------------------------------------------------------------------------

def fetch_all_events(today: date) -> list[CalendarEvent]:
    from calendar_agent.m365_client import fetch_today_events as fetch_m365
    from calendar_agent.google_client import fetch_today_events as fetch_google

    print("  Fetching M365 calendar...", flush=True)
    m365_events = fetch_m365(today)
    print(f"    {len(m365_events)} events")

    print("  Fetching Google calendar...", flush=True)
    google_events = fetch_google(today)
    print(f"    {len(google_events)} events")

    return m365_events + google_events


# ---------------------------------------------------------------------------
# Phase 2: build agent prompt + call managed agent
# ---------------------------------------------------------------------------

def _format_events_for_agent(scored_events: list[ScoredEvent], today: date) -> str:
    lines: list[str] = [f"Today's date: {today.isoformat()}", ""]
    for se in scored_events:
        e = se.event
        lines.append(f"=== EVENT RANK {se.rank} ===")
        lines.append(f"Title: {e.title}")
        lines.append(f"Time: {e.start.strftime('%H:%M')} – {e.end.strftime('%H:%M')} UTC")
        lines.append(f"Source: {e.source}")
        lines.append(f"Location: {e.location or '(none)'}")

        attendee_str = "; ".join(
            f"{a.name} ({a.email})" if a.name else a.email
            for a in e.attendees
        ) or "(none listed)"
        lines.append(f"Attendees: {attendee_str}")

        lines.append(f"Description: {e.description or '(empty)'}")

        if e.attachments:
            for att in e.attachments:
                preview = f"\n  Content preview: {att.text_preview[:500]}" if att.text_preview else ""
                lines.append(f"Attachment: {att.name} ({att.content_type}){preview}")
        else:
            lines.append("Attachments: none")

        lines.append(f"Priority score: {se.score.total} "
                     f"(attendee={se.score.attendee_score}, keyword={se.score.keyword_score})")
        lines.append(f"Signals: {', '.join(se.score.signals) or 'none'}")
        lines.append("")

    lines.append(
        "Generate the briefing JSON for all events above. "
        "Follow your calendar-prep-framework skill exactly."
    )
    return "\n".join(lines)


def call_calendar_agent(
    client: Anthropic,
    agent_id: str,
    environment_id: str,
    scored_events: list[ScoredEvent],
    today: date,
) -> str:
    context = _format_events_for_agent(scored_events, today)

    print(f"\n  Starting session against calendar agent {agent_id}...")
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title=f"Calendar Briefing — {today.isoformat()}",
    )
    Path(".last_calendar_session_id").write_text(session.id)

    print("\n=== AGENT EVENT STREAM ===\n")
    response_parts: list[str] = []

    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": context}]}],
        )
        for event in stream:
            t = event.type
            if t == "session.thread_status_running":
                print("  [agent running]", flush=True)
            elif t == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        response_parts.append(block.text)
                        print(block.text, end="", flush=True)
            elif t == "agent.tool_use":
                print(f"\n  [tool: {getattr(event, 'name', '?')}]", flush=True)
            elif t == "session.status_idle":
                print("\n\n[agent finished]")
                break

    return "".join(response_parts)


# ---------------------------------------------------------------------------
# Parse agent JSON → DailyBriefing
# ---------------------------------------------------------------------------

def _parse_agent_response(raw: str, scored_events: list[ScoredEvent]) -> DailyBriefing:
    # Strip markdown fences if the model added them
    cleaned = re.sub(r"^```[^\n]*\n|```$", "", raw, flags=re.MULTILINE).strip()
    data = json.loads(cleaned)

    event_map = {se.rank: se for se in scored_events}

    briefings: list[EventBriefing] = []
    for item in data.get("briefing", []):
        rank = item["rank"]
        se = event_map.get(rank)
        if se is None:
            continue

        prep_actions = tuple(
            PrepAction(
                action=pa["action"],
                grounded_in=pa.get("grounded_in", "unknown"),
            )
            for pa in item.get("prep_actions", [])
        )

        briefings.append(EventBriefing(
            rank=rank,
            event=se.event,
            priority=se.score,
            prep_actions=prep_actions,
        ))

    return DailyBriefing(
        generated_at=datetime.now(tz=timezone.utc),
        date=str(date.today()),
        events=tuple(sorted(briefings, key=lambda b: b.rank)),
    )


# ---------------------------------------------------------------------------
# Render markdown
# ---------------------------------------------------------------------------

def _render_markdown(briefing: DailyBriefing) -> str:
    lines: list[str] = [
        f"# Daily Calendar Briefing — {briefing.date}",
        f"_Generated at {briefing.generated_at.strftime('%H:%M UTC')}_",
        "",
    ]
    for eb in briefing.events:
        e = eb.event
        lines.append(f"## #{eb.rank} — {e.title}")
        lines.append(
            f"**{e.start.strftime('%H:%M')} – {e.end.strftime('%H:%M')}** "
            f"| {e.source.upper()} "
            + (f"| {e.location}" if e.location else "")
        )
        attendees = ", ".join(a.name or a.email for a in e.attendees) or "—"
        lines.append(f"**Attendees:** {attendees}")
        lines.append(f"**Priority:** {eb.priority.total} "
                     f"(signals: {', '.join(eb.priority.signals) or 'none'})")
        lines.append("")
        lines.append("**Preparation actions:**")
        for pa in eb.prep_actions:
            lines.append(f"- [ ] {pa.action}  _(source: {pa.grounded_in})_")
        lines.append("")

    if briefing.eval_result:
        ev = briefing.eval_result
        lines.append("---")
        lines.append("## Eval scores")
        lines.append(f"| Dimension | Score |")
        lines.append(f"|---|---|")
        lines.append(f"| Prep relevance (primary) | {ev.prep_relevance}/10 |")
        lines.append(f"| Hallucination | {ev.hallucination_score}/10 |")
        lines.append(f"| Schema consistency | {ev.schema_consistency}/10 |")
        lines.append(f"| Priority correctness | {ev.priority_correctness}/10 |")
        lines.append(f"| **Overall** | **{ev.overall}/10** |")
        if ev.issues:
            lines.append("")
            lines.append("**Issues flagged:**")
            for issue in ev.issues:
                lines.append(f"- {issue}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Use mock events (no live calendar fetch)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    agent_id_path = Path(".calendar_agent_id")
    if not agent_id_path.exists():
        raise SystemExit("Run create_calendar_agent.py first.")
    agent_id = agent_id_path.read_text().strip()

    environment_id_path = Path(".environment_id")
    if not environment_id_path.exists():
        raise SystemExit("Run setup_environment.py first.")
    environment_id = environment_id_path.read_text().strip()

    client = Anthropic(default_headers={"anthropic-beta": "managed-agents-2026-04-01"})
    today = date.today()

    # Phase 1: fetch + rank
    print(f"\n=== PHASE 1: Fetch + Rank ({today}) ===\n")
    if args.mock:
        print("  Using mock events (--mock flag set)")
        all_events = _mock_events(today)
    else:
        all_events = fetch_all_events(today)

    print(f"\n  Total events fetched: {len(all_events)}")
    scored_events = rank_events(all_events, top_n=5)
    print(f"\n  Top {len(scored_events)} events by priority:")
    for se in scored_events:
        print(f"    #{se.rank} [{se.score.total:.1f}] {se.event.title}")

    # Phase 2: agent generates prep actions
    print(f"\n=== PHASE 2: Prep Action Generation ===")
    raw_response = call_calendar_agent(client, agent_id, environment_id, scored_events, today)

    try:
        briefing = _parse_agent_response(raw_response, scored_events)
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"\n[ERROR] Failed to parse agent response: {exc}")
        print("Raw response saved to outputs/calendar-agent-raw.txt")
        OUTPUT_DIR.mkdir(exist_ok=True)
        (OUTPUT_DIR / "calendar-agent-raw.txt").write_text(raw_response)
        sys.exit(1)

    # Phase 3: eval
    print(f"\n=== PHASE 3: Eval Judge ===\n")
    eval_client = Anthropic()  # no beta header needed for direct messages
    eval_result = run_eval(briefing, scored_events, eval_client)
    briefing = DailyBriefing(
        generated_at=briefing.generated_at,
        date=briefing.date,
        events=briefing.events,
        eval_result=eval_result,
    )
    print(f"  Prep relevance:      {eval_result.prep_relevance}/10")
    print(f"  Hallucination:       {eval_result.hallucination_score}/10")
    print(f"  Schema consistency:  {eval_result.schema_consistency}/10")
    print(f"  Priority correctness:{eval_result.priority_correctness}/10")
    print(f"  Overall:             {eval_result.overall}/10")
    if eval_result.issues:
        print("  Issues:")
        for issue in eval_result.issues:
            print(f"    - {issue}")

    # Save outputs
    OUTPUT_DIR.mkdir(exist_ok=True)
    stem = f"calendar-briefing-{today.isoformat()}"

    json_out = OUTPUT_DIR / f"{stem}.json"
    md_out = OUTPUT_DIR / f"{stem}.md"

    def _briefing_to_dict(b: DailyBriefing) -> dict:
        return {
            "generated_at": b.generated_at.isoformat(),
            "date": b.date,
            "events": [
                {
                    "rank": eb.rank,
                    "event": {
                        "title": eb.event.title,
                        "start": eb.event.start.isoformat(),
                        "end": eb.event.end.isoformat(),
                        "source": eb.event.source,
                        "attendees": [{"name": a.name, "email": a.email} for a in eb.event.attendees],
                        "description": eb.event.description,
                        "location": eb.event.location,
                    },
                    "priority": {
                        "total": eb.priority.total,
                        "attendee_score": eb.priority.attendee_score,
                        "keyword_score": eb.priority.keyword_score,
                        "signals": list(eb.priority.signals),
                    },
                    "prep_actions": [
                        {"action": pa.action, "grounded_in": pa.grounded_in}
                        for pa in eb.prep_actions
                    ],
                }
                for eb in b.events
            ],
            "eval": {
                "prep_relevance": b.eval_result.prep_relevance,
                "hallucination_score": b.eval_result.hallucination_score,
                "schema_consistency": b.eval_result.schema_consistency,
                "priority_correctness": b.eval_result.priority_correctness,
                "overall": b.eval_result.overall,
                "issues": list(b.eval_result.issues),
            } if b.eval_result else None,
        }

    json_out.write_text(json.dumps(_briefing_to_dict(briefing), indent=2))
    md_out.write_text(_render_markdown(briefing))

    print(f"\nOutputs saved:")
    print(f"  {json_out}")
    print(f"  {md_out}")
    print(f"\nView session at: https://platform.claude.com/sessions/{Path('.last_calendar_session_id').read_text().strip()}")


if __name__ == "__main__":
    main()
