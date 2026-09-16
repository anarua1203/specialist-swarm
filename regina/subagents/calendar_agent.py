"""Calendar Agent — mock schedule specialist."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from .. import config
from .base import Subagent, keywords

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class CalendarAgent(Subagent):
    key = "calendar"
    name = "Calendar Agent"
    tool_name = "ask_calendar_agent"
    fixture_file = "calendar.json"
    description = (
        "Reads the user's calendar. Can return today's or tomorrow's agenda, "
        "the week ahead, conflicts between events, free slots for focus work, "
        "upcoming deadlines, and the next meeting."
    )
    persona = (
        "You are the Calendar Agent, a specialist sub-agent of Regina (team "
        "Prompt Queens). You know the user's calendar and nothing else. Always "
        "flag overlapping events as CONFLICT, name the largest free block inside "
        "working hours, and list deadlines separately from meetings."
    )

    # ---- data access ----------------------------------------------------------

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.raw["events"]

    def events_on(self, offset: int) -> list[dict[str, Any]]:
        return sorted((e for e in self.events if e["day_offset"] == offset), key=lambda e: e["start"])

    def conflicts_on(self, offset: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        timed = [e for e in self.events_on(offset) if e["type"] != "deadline"]
        found = []
        for i, a in enumerate(timed):
            for b in timed[i + 1 :]:
                if _minutes(a["start"]) < _minutes(b["end"]) and _minutes(b["start"]) < _minutes(a["end"]):
                    found.append((a, b))
        return found

    def free_slots(self, offset: int, min_minutes: int = 45) -> list[tuple[str, str]]:
        wh = self.raw["working_hours"]
        cursor, end = _minutes(wh["start"]), _minutes(wh["end"])
        busy = [e for e in self.events_on(offset) if e["type"] in {"meeting", "focus", "milestone"}]
        slots = []
        for e in busy:
            s, f = _minutes(e["start"]), _minutes(e["end"])
            if s - cursor >= min_minutes:
                slots.append((_hhmm(cursor), _hhmm(s)))
            cursor = max(cursor, f)
        if end - cursor >= min_minutes:
            slots.append((_hhmm(cursor), _hhmm(end)))
        return slots

    def deadlines_within(self, days: int) -> list[dict[str, Any]]:
        return [e for e in sorted(self.events, key=lambda e: (e["day_offset"], e["start"]))
                if e["type"] == "deadline" and 0 <= e["day_offset"] <= days]

    def next_event(self) -> dict[str, Any] | None:
        now_min = config.now().hour * 60 + config.now().minute
        for e in self.events_on(0):
            if e["type"] != "deadline" and _minutes(e["start"]) >= now_min:
                return e
        upcoming = [e for e in self.events if e["day_offset"] > 0 and e["type"] != "deadline"]
        return min(upcoming, key=lambda e: (e["day_offset"], e["start"]), default=None)

    # ---- mock backend ---------------------------------------------------------

    def answer(self, brief: str) -> tuple[str, dict[str, Any]]:
        low = brief.lower()
        # "deadlines in the next 2 days" is a request for deadlines, not a week view.
        low = re.sub(r"deadlines? (?:in|within|for|over) the next \d+ days", "deadlines", low)
        base = config.today()

        if re.search(r"\bnext meeting\b|\bwhat'?s next\b|\bcoming up next\b", low):
            e = self.next_event()
            if not e:
                return "**Calendar Agent** — nothing else scheduled.", {"next": None}
            return f"**Calendar Agent** — next up: {self._render_event(e, base)}", {"next": e}

        wants_agenda = re.search(r"\bschedule\b|\bagenda\b|\bcalendar\b|what(?:'s| is) on|\bevents\b|\bmeetings\b|\btimeline\b", low)
        if re.search(r"\btoday\b", low) and re.search(r"\btomorrow\b", low):
            return self._render_range(range(0, 2), base)

        if re.search(r"\bconflicts?\b|\boverlap", low) and not wants_agenda:
            offset = 1 if "tomorrow" in low else 0
            conflicts = self.conflicts_on(offset)
            return self._render_conflicts(conflicts, offset, base), {"conflicts": conflicts}

        if re.search(r"\bfree\b|\bslot|\bavailab|\bfocus block", low) and not wants_agenda:
            offset = 1 if "tomorrow" in low else 0
            slots = self.free_slots(offset)
            label = self._day_label(offset, base)
            if not slots:
                return f"**Calendar Agent** — no free block of 45+ minutes {label}.", {"free": []}
            text = f"**Calendar Agent** — free blocks {label}: " + ", ".join(f"{s}–{f}" for s, f in slots)
            return text, {"free": slots}

        if re.search(r"\bweek\b|\bnext (\d+ )?days\b|\bupcoming\b", low):
            m = re.search(r"next (\d+) days", low)
            horizon = int(m.group(1)) if m else 6
            return self._render_range(range(0, horizon + 1), base)

        topic = keywords(re.sub(r"\b(today|tomorrow|schedule|agenda|calendar|meeting|meetings|deadline|deadlines|conflict|conflicts|free|block|blocks|largest)\b", " ", low))
        if topic and not re.search(r"\b(schedule|agenda|today|tomorrow)\b", low):
            hits = [e for e in self.events if any(t in self._haystack(e) for t in topic)]
            if hits:
                lines = [f"**Calendar Agent** — events matching {', '.join(sorted(topic))}:"]
                lines += [f"- {self._render_event(e, base)}" for e in sorted(hits, key=lambda e: (e['day_offset'], e['start']))]
                return "\n".join(lines), {"matched": hits}

        offset = 1 if "tomorrow" in low else 0
        return self._render_day(offset, base, include_deadlines=True)

    # ---- rendering ------------------------------------------------------------

    def _day_label(self, offset: int, base: date) -> str:
        d = base + timedelta(days=offset)
        prefix = {0: "today", 1: "tomorrow"}.get(offset, DAY_NAMES[d.weekday()])
        return f"{prefix} ({DAY_NAMES[d.weekday()]} {d.isoformat()})"

    @staticmethod
    def _haystack(e: dict[str, Any]) -> str:
        return " ".join([e["title"], e["notes"], e["location"], " ".join(e["attendees"]), e["type"]]).lower()

    def _render_event(self, e: dict[str, Any], base: date) -> str:
        d = base + timedelta(days=e["day_offset"])
        when = f"{DAY_NAMES[d.weekday()][:3]} {e['start']}" if e["day_offset"] else e["start"]
        span = when if e["type"] == "deadline" else f"{when}–{e['end']}"
        who = f" with {', '.join(e['attendees'])}" if e["attendees"] else ""
        where = f" @ {e['location']}" if e["location"] else ""
        tag = {"deadline": " [DEADLINE]", "milestone": " [MILESTONE]", "personal": " [personal]", "focus": " [focus]"}.get(e["type"], "")
        note = f" — {e['notes']}" if e["notes"] else ""
        return f"{span} {e['title']}{tag}{who}{where}{note}"

    def _render_conflicts(self, conflicts, offset: int, base: date) -> str:
        label = self._day_label(offset, base)
        if not conflicts:
            return f"**Calendar Agent** — no conflicts {label}."
        lines = [f"**Calendar Agent** — {len(conflicts)} conflict(s) {label}:"]
        for a, b in conflicts:
            lines.append(f"- CONFLICT: \"{a['title']}\" ({a['start']}–{a['end']}) overlaps \"{b['title']}\" ({b['start']}–{b['end']}). "
                         f"Suggest keeping \"{a['title']}\" (client-facing) and asking to join \"{b['title']}\" late or send notes.")
        return "\n".join(lines)

    def _render_day(self, offset: int, base: date, include_deadlines: bool) -> tuple[str, dict[str, Any]]:
        events = self.events_on(offset)
        conflicts = self.conflicts_on(offset)
        conflicted = {e["id"] for pair in conflicts for e in pair}
        slots = self.free_slots(offset)
        label = self._day_label(offset, base)
        lines = [f"**Calendar Agent** — schedule {label}: {len(events)} item(s)"]
        for e in events:
            marker = " ⚠ CONFLICT" if e["id"] in conflicted else ""
            lines.append(f"- {self._render_event(e, base)}{marker}")
        if conflicts:
            lines.append("")
            lines.append(self._render_conflicts(conflicts, offset, base).split("\n", 1)[1])
        if slots:
            biggest = max(slots, key=lambda s: _minutes(s[1]) - _minutes(s[0]))
            lines.append("")
            lines.append(f"Largest free block: {biggest[0]}–{biggest[1]} "
                         f"({_minutes(biggest[1]) - _minutes(biggest[0])} min). All free: " + ", ".join(f"{s}–{f}" for s, f in slots))
        deadlines = self.deadlines_within(2) if include_deadlines else []
        if deadlines:
            lines.append("")
            lines.append("Deadlines in the next 2 days:")
            lines += [f"- {self._render_event(e, base)}" for e in deadlines]
        return "\n".join(lines), {"events": events, "conflicts": conflicts, "free": slots, "deadlines": deadlines, "offset": offset}

    def _render_range(self, offsets, base: date) -> tuple[str, dict[str, Any]]:
        lines = ["**Calendar Agent** — week ahead:"]
        all_events = []
        for off in offsets:
            evs = self.events_on(off)
            if not evs:
                continue
            all_events += evs
            lines.append(f"\n{self._day_label(off, base)}:")
            conflicted = {e["id"] for pair in self.conflicts_on(off) for e in pair}
            for e in evs:
                lines.append(f"- {self._render_event(e, base)}{' ⚠ CONFLICT' if e['id'] in conflicted else ''}")
        return "\n".join(lines), {"events": all_events}
