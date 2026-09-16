#!/usr/bin/env python3
"""Fetch RSS feeds concurrently, emit JSON items to stdout."""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
SOURCES_PATH = SCRIPT_DIR / "sources.yaml"
FIXTURE_PATH = SCRIPT_DIR / "fixtures" / "snapshot.json"


def parse_published(entry):
    """Return an ISO-8601 UTC string for the entry's published date, or None."""
    for attr in ("published_parsed", "updated_parsed"):
        tp = getattr(entry, attr, None)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                continue
    return None


def fetch_one(feed_cfg, timeout=5):
    """Fetch a single feed. Returns (name, [items]) or (name, error_str)."""
    url = feed_cfg["url"]
    name = feed_cfg.get("name", url)
    try:
        import socket
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            d = feedparser.parse(url)
        finally:
            socket.setdefaulttimeout(old_timeout)

        if d.bozo and not d.entries:
            return name, f"parse error: {d.bozo_exception}"

        items = []
        for entry in d.entries:
            items.append({
                "title": entry.get("title", "").strip(),
                "url": entry.get("link", "").strip(),
                "publisher": name,
                "published_at": parse_published(entry),
                "summary": (entry.get("summary") or entry.get("description") or "").strip()[:500],
            })
        return name, items
    except Exception as e:
        return name, f"fetch error: {e}"


def load_sources():
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["feeds"]


def filter_by_lookback(items, lookback_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    kept = []
    for item in items:
        if item["published_at"] is None:
            kept.append(item)  # keep undated items rather than silently drop
            continue
        try:
            pub = datetime.fromisoformat(item["published_at"])
            if pub >= cutoff:
                kept.append(item)
        except Exception:
            kept.append(item)
    return kept


def main():
    parser = argparse.ArgumentParser(description="Fetch RSS feeds, emit JSON.")
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--per-source", type=int, default=8)
    parser.add_argument("--fixture", action="store_true",
                        help="Read from fixtures/snapshot.json instead of network")
    parser.add_argument("--save-fixture", action="store_true",
                        help="Save fetched items to fixtures/snapshot.json")
    args = parser.parse_args()

    if args.fixture:
        if not FIXTURE_PATH.exists():
            print(json.dumps({"error": "fixture not found", "path": str(FIXTURE_PATH)}),
                  file=sys.stderr)
            sys.exit(1)
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        json.dump(items, sys.stdout, indent=2, ensure_ascii=False)
        return

    sources = load_sources()
    all_items = []
    errors = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_one, src): src for src in sources}
        for future in as_completed(futures):
            name, result = future.result()
            if isinstance(result, str):
                errors.append(f"[{name}] {result}")
            else:
                capped = result[:args.per_source]
                all_items.extend(capped)

    for err in errors:
        print(err, file=sys.stderr)

    all_items = filter_by_lookback(all_items, args.lookback_hours)

    # drop items with no URL
    all_items = [i for i in all_items if i.get("url")]

    # sort by published_at descending, undated last
    def sort_key(item):
        if item["published_at"]:
            return (0, item["published_at"])
        return (1, "")
    all_items.sort(key=sort_key, reverse=True)

    if args.save_fixture:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FIXTURE_PATH, "w", encoding="utf-8") as f:
            json.dump(all_items, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(all_items)} items to {FIXTURE_PATH}", file=sys.stderr)

    json.dump(all_items, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
