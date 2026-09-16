---
name: calendar-prep-framework
description: Preparation framework for calendar events. Defines action categories, grounding requirements, output schema, and quality criteria for generating daily event briefings. Use when generating preparation actions for meetings and calendar events.
---

# Calendar Preparation Framework

## Your role

You receive pre-ranked calendar events for today. For each event, generate 3–5 preparation actions grounded in the event data. Output a single JSON object — nothing else.

## Action categories

Evaluate which categories apply and include at least one action per applicable category:

**Pre-read** — what to review before the meeting
Ground in: description, attachment names, or title keywords
> "Review the Q3 KPI deck attached to this invite (attachment: Q3-Review.pptx)"

**Materials** — what to prepare, update, or bring
Ground in: event type, attendee list, or description content
> "Update the pricing slide with latest discount band before the client demo"

**Logistics** — practical coordination
Ground in: attendee list, location, or event timing
> "Send dial-in details to all 8 attendees — virtual link not yet in invite"

**Stakeholder alignment** — who to brief beforehand
Ground in: attendee seniority or external domain presence
> "Brief the partner before the call — board-level client sponsor is attending"

**Follow-up template** — prepare what comes after
Always include for events with external attendees
> "Draft follow-up email template: decisions made, next steps, owners, deadline"

## Grounding rules (mandatory)

Every action MUST include a `grounded_in` field citing its source:

| Value | When to use |
|---|---|
| `"description"` | Action derived from the event body/description |
| `"attachment:<filename>"` | Action derived from a named attachment |
| `"attendees"` | Action derived from who is attending |
| `"title"` | Action derived from keywords in the event title |

**Do not invent.** If you cannot cite a source field, do not include the action.
Never reference names, document titles, or facts not present in the event data you received.

## Output schema (strict)

Output ONLY valid JSON. No markdown. No explanation. No trailing text.

```
{
  "briefing": [
    {
      "rank": <integer 1–5>,
      "event": {
        "title": "<string>",
        "start": "<HH:MM>",
        "end": "<HH:MM>",
        "source": "<m365 or google>",
        "attendees": ["<Name (email)>"],
        "description_summary": "<max 100 characters>"
      },
      "priority": {
        "total": <float>,
        "attendee_score": <float>,
        "keyword_score": <float>,
        "signals": ["<signal string>"]
      },
      "prep_actions": [
        {
          "action": "<verb-led, event-specific action>",
          "grounded_in": "<source field>"
        }
      ]
    }
  ]
}
```

Preserve the rank, priority scores, and signals exactly as provided — do not re-order or recalculate them.

## Quality bar

- Specific over generic: "Review ACME Q3 KPIs in attached deck" > "Review documents"
- Actionable: every action starts with a verb
- Proportionate: rank-1 events get 4–5 actions; rank-5 may get 3
- Grounded: every action has a non-empty `grounded_in`
