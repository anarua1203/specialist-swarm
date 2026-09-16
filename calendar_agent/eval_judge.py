"""Phase 3: LLM judge that evaluates the calendar agent's briefing output.

Four dimensions (prep_relevance is primary):
  1. prep_relevance     – are actions specific to this event, not generic?
  2. hallucination      – do actions cite sources that actually exist in context?
  3. schema_consistency – does every briefing entry have the required fields?
  4. priority_correctness – does the rank order match the computed priority scores?

Returns an EvalResult with scores 0–10 per dimension and an overall (weighted average).
"""

from __future__ import annotations

import json
import re

from anthropic import Anthropic

from .schemas import DailyBriefing, EvalResult, ScoredEvent

WEIGHTS = {
    "prep_relevance": 0.40,
    "hallucination": 0.30,
    "schema_consistency": 0.15,
    "priority_correctness": 0.15,
}

EVAL_SYSTEM = """\
You are an objective evaluator of AI-generated calendar briefings.
You score outputs on four dimensions, each 0–10.
Return ONLY a JSON object — no prose, no markdown fences.
"""

EVAL_PROMPT_TEMPLATE = """\
You are evaluating a calendar briefing produced by an AI assistant.

## Source events passed to the AI (ground truth for grounding checks)
{source_context}

## AI-generated briefing (what you are evaluating)
{briefing_json}

## Priority ranking computed algorithmically (ground truth for rank check)
{rank_context}

## Scoring rubric

### prep_relevance (0–10; this is the primary metric)
10 – every action is specific to this exact event; could not apply to a different meeting
7  – most actions are specific; 1–2 are generic but not wrong
4  – half the actions are generic ("review relevant documents", "prepare notes")
0  – all actions are generic boilerplate with no event specificity

### hallucination (0–10; higher = cleaner output)
10 – every grounded_in field references a field that actually contained that information
5  – 1–2 actions cite "description" or "attachment" when the source was empty/absent
0  – actions reference names, documents, or facts not present anywhere in the source data

### schema_consistency (0 or 10)
10 – every briefing entry has: rank, event(title/start/end/source/attendees/description_summary), priority, prep_actions(action+grounded_in)
0  – any required field is missing or misnamed

### priority_correctness (0–10)
10 – AI rank order exactly matches algorithmic rank order
5  – 1 position is swapped
0  – rank order is completely different from algorithmic scores

## Your response
Return ONLY this JSON (no markdown, no explanation):
{{
  "prep_relevance": <0–10 float>,
  "hallucination": <0–10 float>,
  "schema_consistency": <0 or 10>,
  "priority_correctness": <0–10 float>,
  "issues": ["<specific issue if score < 8, else empty list>"]
}}
"""


def _build_source_context(scored_events: list[ScoredEvent]) -> str:
    lines: list[str] = []
    for se in scored_events:
        e = se.event
        lines.append(f"--- Event rank {se.rank}: {e.title} ({e.source}) ---")
        lines.append(f"Time: {e.start.strftime('%H:%M')} – {e.end.strftime('%H:%M')}")
        lines.append(f"Attendees: {', '.join(a.name or a.email for a in e.attendees) or 'none listed'}")
        lines.append(f"Description: {e.description or '(empty)'}")
        for att in e.attachments:
            preview = f" | preview: {att.text_preview[:200]}" if att.text_preview else ""
            lines.append(f"Attachment: {att.name} ({att.content_type}){preview}")
        lines.append("")
    return "\n".join(lines)


def _build_rank_context(scored_events: list[ScoredEvent]) -> str:
    lines: list[str] = []
    for se in scored_events:
        lines.append(
            f"Rank {se.rank}: {se.event.title} | total={se.score.total} "
            f"(attendee={se.score.attendee_score}, keyword={se.score.keyword_score})"
        )
    return "\n".join(lines)


def run_eval(
    briefing: DailyBriefing,
    scored_events: list[ScoredEvent],
    client: Anthropic,
) -> EvalResult:
    briefing_data = [
        {
            "rank": eb.rank,
            "event": {
                "title": eb.event.title,
                "start": eb.event.start.strftime("%H:%M"),
                "end": eb.event.end.strftime("%H:%M"),
                "source": eb.event.source,
                "attendees": [f"{a.name} ({a.email})" for a in eb.event.attendees],
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
        for eb in briefing.events
    ]

    prompt = EVAL_PROMPT_TEMPLATE.format(
        source_context=_build_source_context(scored_events),
        briefing_json=json.dumps(briefing_data, indent=2),
        rank_context=_build_rank_context(scored_events),
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # fast + cheap for eval
        max_tokens=512,
        system=EVAL_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if the model added them despite instructions
    raw = re.sub(r"^```[^\n]*\n|```$", "", raw, flags=re.MULTILINE).strip()
    scores = json.loads(raw)

    overall = round(
        sum(scores[dim] * weight for dim, weight in WEIGHTS.items()),
        2,
    )

    return EvalResult(
        prep_relevance=float(scores["prep_relevance"]),
        hallucination_score=float(scores["hallucination"]),
        schema_consistency=float(scores["schema_consistency"]),
        priority_correctness=float(scores["priority_correctness"]),
        issues=tuple(scores.get("issues", [])),
        overall=overall,
    )
