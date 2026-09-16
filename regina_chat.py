"""
Regina — interactive chat.

Ask anything; Regina routes each question to the right sub-agent(s) and shows
the delegation trace as it happens.

Commands inside the chat:
    /briefing   run the morning briefing
    /reset      forget the conversation
    /quit       exit

Usage:
    python regina_chat.py [--mock | --live] [--quiet]
    python regina_chat.py --ask "Do I have any conflicts today?"   # one-shot
"""

from __future__ import annotations

import argparse
import sys

from regina import Regina
from regina.trace import Trace

SUGGESTIONS = [
    "What's on my plate today?",
    "Any emails I need to reply to?",
    "Do I have any conflicts today?",
    "When is my biggest free block?",
    "What did Anthropic ship about agents this week?",
    "Marcus wants to move our 1:1 to 3pm — does that work?",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with Regina")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_true")
    group.add_argument("--live", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="hide the delegation trace")
    parser.add_argument("--ask", metavar="QUESTION", help="ask one question and exit")
    args = parser.parse_args()

    mode = "mock" if args.mock else "live" if args.live else None
    trace = Trace(enabled=not args.quiet)
    regina = Regina(mode=mode, on_event=trace)

    if args.ask:
        print(regina.ask(args.ask))
        return

    print(f"👑 {regina.name} — {regina.mode} mode. Type /quit to leave, /briefing for the morning briefing.")
    print("Try: " + " | ".join(SUGGESTIONS[:3]) + "\n")
    while True:
        try:
            line = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in {"/quit", "/exit", "/q"}:
            break
        if line == "/reset":
            regina.reset()
            print("regina > Forgotten. Clean slate.\n")
            continue
        if line == "/help":
            print("regina > Try: " + " | ".join(SUGGESTIONS) + "\n")
            continue
        answer = regina.briefing() if line == "/briefing" else regina.ask(line)
        print(f"\nregina > {answer}\n")
    print("Bye 👋", file=sys.stderr)


if __name__ == "__main__":
    main()
