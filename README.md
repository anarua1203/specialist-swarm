# 👑 Regina — the Prompt Queens personal assistant

Regina is an **orchestrator agent**. She never reads your inbox, your calendar,
or the news herself; she delegates to three specialist sub-agents, fans the
work out in parallel, and synthesises one answer.

```
                       ┌────────────────────────┐
   "What's on my       │        Regina          │   morning briefing
    plate today?" ───▶ │   orchestrator (Opus 5)│ ─▶ or a chat answer
                       └───┬────────┬───────┬───┘
              parallel     │        │       │
                 ┌─────────▼─┐ ┌────▼─────┐ ┌▼──────────────────┐
                 │Email Agent│ │ Calendar │ │ Anthropic News    │
                 │ (inbox)   │ │  Agent   │ │ Agent             │
                 └───────────┘ └──────────┘ └───────────────────┘
                   mock_data/emails.json  calendar.json  anthropic_news.json
```

The sub-agents are treated as **already built and working**: each is a black
box that takes a natural-language brief and returns a report. For the demo
they run over synthetic fixtures in `mock_data/`; swapping in real Gmail,
Google Calendar, or an RSS feed only touches `regina/subagents/*.py`.

## Quick start (60 seconds, no API key needed)

```bash
uv venv && uv pip install -r requirements.txt     # or: pip install -r requirements.txt
python regina_briefing.py --mock                  # morning briefing, offline
python regina_chat.py --mock                      # interactive chat, offline
```

With an API key (`cp .env.example .env`, set `ANTHROPIC_API_KEY`), drop the
`--mock` flag: Regina runs on Claude Opus 5 and decides for herself which
agents to call.

```bash
python regina_briefing.py                         # live briefing
python regina_chat.py --ask "Any conflicts today?"
```

## Web UI (for the demo)

```bash
python regina_web.py --mock                       # offline → http://127.0.0.1:8000
python regina_web.py                              # auto: live if credentials exist
```

One page, standard library only. Chat on the left; on the right, "the court":
Regina plus the three agent cards, which light up as she delegates, and the same
delegation trace the CLI prints, streamed live over Server-Sent Events. The
header badge shows **mock · offline** or **live · <model>** so you always know
whether API calls are happening. In mock mode the agents answer in ~1 ms, so the
page paces the reveal (all delegations first, then replies) to keep the
fan-out visible; timestamps are real. Markdown rendering loads `marked` from
cdnjs and falls back to plain text offline.

To put it on a URL (tunnel, Docker, or AWS App Runner), see [DEPLOY.md](DEPLOY.md).

## Modes

| Mode | Orchestrator | Sub-agents | When |
| --- | --- | --- | --- |
| `mock` | deterministic router + template | deterministic over fixtures | no credentials, tests, offline demo |
| `live` | Claude Opus 5, Messages API tool use | deterministic over fixtures (default) or `REGINA_SUBAGENTS=llm` for a Sonnet 5 call per agent | real demo |
| Managed Agents | Claude Opus 5 coordinator with a roster | three Managed Agents built from the same classes | cloud demo with the platform event stream |

`REGINA_MODE=auto` (default) picks `live` when a key or an `ant auth login`
profile exists, else `mock`. All knobs are in `.env.example`.

## What the demo looks like

```
   0.00s  [fan-out ×3]      Email Agent, Calendar Agent, Anthropic News Agent
   0.00s  [delegate →]       Email Agent: "Give me an inbox digest: counts, every high-importance…"
   0.00s  [delegate →]       Calendar Agent: "Give me today's full schedule with any conflicts…"
   0.00s  [delegate →]       Anthropic News Agent: "Top 3 Anthropic announcements from the last 7…"
   0.00s  [reply ←]          Calendar Agent (mock, 0 ms, 1180 chars)
   0.00s  [reply ←]          Anthropic News Agent (mock, 0 ms, 1069 chars)
   0.00s  [reply ←]          Email Agent (mock, 0 ms, 1919 chars)
   0.00s  [synthesise]       Regina is writing the answer

# Regina's briefing — Wednesday, September 16, 2026

Your Majesty, Sofia Ramirez needs "Pitch deck draft" handled first, and you
have a calendar conflict this afternoon.

## Needs your attention
- **Email** — Sofia Ramirez: "Pitch deck draft — please review section 3" (40m ago, needs a reply).
- **Calendar** — CONFLICT 13:00–14:00 "Client sync: Northwind" overlaps "Design review" …
...
```

The trace goes to stderr, the briefing to stdout, and a copy lands in
`outputs/briefing-<date>.md`.

## Demo script (5 minutes)

1. **Pitch (30s).** "Regina is the senior partner. She doesn't do the work,
   she runs the people who do." Show the diagram above.
2. **Briefing (90s).** `python regina_briefing.py`. Narrate the trace: three
   delegations leave in the same instant, three replies come back, then one
   synthesis. Point at the cross-references: Marcus's email vs the 11:30
   1:1, the Northwind email vs the 13:00 client sync vs tomorrow's SOW deadline.
3. **Chat (90s).** `python regina_chat.py`. Ask "Do I have any conflicts
   today?" (one agent), then "Marcus wants to move our 1:1 to 3pm, does that
   work?" (email + calendar), then "What did Anthropic ship about agents?"
4. **Under the hood (60s).** Open `regina/subagents/email_agent.py`: a
   sub-agent is one class with a persona, a fixture, and an `answer()`.
   Adding a Slack agent is one file and one line in the roster.
5. **Cloud (optional, 60s).** `python managed_agents/run_regina.py` and show
   the platform session with three sub-agent threads.

## Project layout

```
regina/
  orchestrator.py        Regina: tool-use loop (live) or router + composer (mock), parallel fan-out
  prompts.py             system prompt, roster description, canned briefs
  config.py              env-driven settings (models, mode, pinned date)
  trace.py               terminal trace of delegations
  subagents/
    base.py              Subagent: brief -> report; mock and llm backends; tool + system prompt
    email_agent.py       Email Agent (follows the email-brief skill: 3 bullets, 3 actions, drafts only)
    calendar_agent.py    Calendar Agent (conflicts, free slots, deadlines)
    news_agent.py        Anthropic News Agent
mock_data/               synthetic inbox, calendar, news (relative dates, always "fresh")
skills/regina-briefing/  SKILL.md — the briefing format, shared by local and Managed Agents
skills/email-brief/      SKILL.md — the Email Agent's triage rubric, output contract, drafts-only guardrails
managed_agents/          create_subagents / create_orchestrator / setup_environment / run_regina
regina_briefing.py       CLI: morning briefing
regina_chat.py           CLI: interactive chat
regina_web.py            web UI: SSE server over the same Regina (serves web/index.html)
web/index.html           the demo page: chat, agent cards, live delegation trace
tests/                   pytest, offline
examples/deal-desk/      the original specialist-swarm baseline this was built from
```

## Managed Agents path

Same roster, same prompts, but Anthropic runs the loop and each sub-agent is
its own thread inside one session.

```bash
python managed_agents/create_subagents.py      # 3 agents, fixtures embedded in their system prompts
python managed_agents/create_orchestrator.py   # Regina as coordinator + regina-briefing skill
python managed_agents/setup_environment.py     # once per workspace
python managed_agents/run_regina.py            # stream the session; thread_created × 3 is the money shot
```

Needs a workspace with the multi-agent research preview enabled.

## Email Agent and the `email-brief` skill

The Email Agent follows [`skills/email-brief/SKILL.md`](skills/email-brief/SKILL.md)
(by Élise Sauvé): triage from metadata, collapse by conversation, **exactly 3
summary bullets**, the **3 most important actions** ranked by hard deadline,
who is blocked on you, sender seniority, then explicit ask, and **drafts only,
never send**. It returns the skill's JSON contract plus a markdown briefing.

- Mock and `llm` backends apply the rubric to `mock_data/emails.json`, whose
  `addressed` / `flagged` / `focused` / `conversation_id` fields mirror the
  Outlook signals the skill scores on.
- Managed Agents: `create_subagents.py` uploads and attaches the skill. Set
  `EXCHANGE_MCP_URL` (and `EXCHANGE_MCP_TOKEN` if needed) before running it
  and the Email Agent reads a live Outlook/Exchange mailbox through the MCP
  instead of the fixture. A Gmail MCP can be swapped in without touching the skill.

```bash
export EXCHANGE_MCP_URL="https://<your-exchange-mcp>/mcp"
export EXCHANGE_MCP_TOKEN="..."        # only if the MCP needs a bearer token
python managed_agents/create_subagents.py
```

## Extending the roster

1. Add `regina/subagents/slack_agent.py` subclassing `Subagent` with `key`,
   `name`, `tool_name`, `persona`, `fixture_file`, and an `answer()`.
2. Append it to `ROSTER` in `regina/subagents/__init__.py`.
3. Mention it in `ROSTER_DESCRIPTION` in `regina/prompts.py`.

The live orchestrator picks up the new tool automatically; the Managed Agents
scripts create it on the next run.

## Tests

```bash
python -m pytest -q
```

Tests pin `REGINA_TODAY=2026-09-16` so fixture offsets are reproducible and
never touch the network.
