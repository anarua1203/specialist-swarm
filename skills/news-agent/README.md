# News Agent

A specialist skill that fetches AI news via RSS and returns scored, structured JSON for the orchestrator.

## Quick Start

```bash
pip install feedparser pyyaml

# Live fetch (last 24h, JSON to stdout)
python skills/news-agent/fetch.py

# Save a fixture for offline use
python skills/news-agent/fetch.py --save-fixture

# Run from fixture (no network)
python skills/news-agent/fetch.py --fixture

# Custom lookback and per-source cap
python skills/news-agent/fetch.py --lookback-hours 48 --per-source 5
```

## Files

- `SKILL.md` — model instructions: digest/query entry points, scoring pipeline, output contract
- `fetch.py` — RSS fetcher (concurrent, fault-tolerant)
- `sources.yaml` — feed list (edit to add/remove sources)
- `profile.yaml` — your role, employer, stack, watchlist (edit with your real context)
- `brief.yaml` — relevance weights, ordering, length caps, verbosity presets
- `fixtures/snapshot.json` — saved fetch for offline/demo use
