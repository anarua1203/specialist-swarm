"""
Regina — morning briefing.

Fans out to the Email, Calendar and News agents in parallel, prints
the delegation trace (this is the demo), then the briefing. Saves a copy to
outputs/briefing-<date>.md.

Usage:
    python regina_briefing.py            # auto: live if credentials exist, else mock
    python regina_briefing.py --mock     # force offline mode
    python regina_briefing.py --live     # force Claude API mode
    python regina_briefing.py --quiet    # no trace, just the briefing
"""

from __future__ import annotations

import argparse
import sys
import time

from regina import Regina, config
from regina.trace import Trace


def main() -> None:
    parser = argparse.ArgumentParser(description="Regina's morning briefing")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_true", help="offline mode, no API calls")
    group.add_argument("--live", action="store_true", help="Claude API mode")
    parser.add_argument("--quiet", action="store_true", help="hide the delegation trace")
    parser.add_argument("--no-save", action="store_true", help="don't write outputs/briefing-<date>.md")
    args = parser.parse_args()

    mode = "mock" if args.mock else "live" if args.live else None
    trace = Trace(enabled=not args.quiet)
    regina = Regina(mode=mode, on_event=trace)

    print(f"👑 {regina.name} ({regina.mode} mode, roster: {', '.join(a.name for a in regina.roster.values())})\n", file=sys.stderr)
    started = time.perf_counter()
    briefing = regina.briefing()
    elapsed = time.perf_counter() - started

    if not args.quiet:
        print(f"\n[synthesised in {elapsed:.1f}s]\n", file=sys.stderr)
    print(briefing)

    if not args.no_save:
        config.OUTPUT_DIR.mkdir(exist_ok=True)
        path = config.OUTPUT_DIR / f"briefing-{config.today().isoformat()}.md"
        path.write_text(briefing + "\n")
        print(f"\nSaved to {path.relative_to(config.ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
