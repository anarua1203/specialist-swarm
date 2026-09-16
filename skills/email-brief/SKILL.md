---
name: email-brief
description: Morning inbox briefing and draft preparation. Use whenever someone asks to summarize their inbox, catch up on email, or prepare replies/follow-ups. Scans the last 24h plus unresolved threads, returns a 3-bullet summary and the 3 most important actions, and creates drafts — never sends. Works with an Outlook/Exchange email MCP today; provider-agnostic by design.
---

# Email Brief

You produce a concise morning briefing and prepare — **never send** — the most important emails the user owes.

## Tools you rely on

An email MCP (Outlook/Exchange tool names shown; a Gmail MCP can be swapped in):

- `get-current-user` — identify the running user (never hardcode a person)
- `list-mail-folder-messages` (or `list-mail-messages`) — cheap triage from metadata + preview
- `get-mail-message` — full body, only for the few that matter
- `create-reply-draft` / `create-reply-all-draft` / `create-draft-email` — draft only

## Procedure

### 1. Identify the user
Call `get-current-user`. Note address + timezone.

### 2. Triage cheaply first — no full reads
List the Inbox for the last 24h requesting **only signal fields** via `select`:
`subject, from, toRecipients, ccRecipients, receivedDateTime, isRead, flag, importance, inferenceClassification, bodyPreview, hasAttachments, conversationId`
with `orderby: receivedDateTime desc` and `filter: receivedDateTime ge <now-24h>`.

**Collapse messages by `conversationId` into threads** — reason about threads, not individual replies.

Score each thread on signals available **without** a full body read:

| Signal | Meaning |
| --- | --- |
| `inferenceClassification = focused` | Outlook's own per-user relevance |
| `flag.flagStatus = flagged` | Explicitly unresolved |
| `importance = high` | Sender flagged urgency |
| `isRead = false` | Unattended |
| user in `toRecipients` (not cc-only) | Action likely on the user |
| ask/deadline language in `subject` + `bodyPreview` | "can you", "approve", "by EOD", "?" |
| sender is leadership / client / known VIP | vs bulk `no-reply@` / newsletter |
| user is **named/mentioned in the body** though only cc'd | user is the SME on the hook |

Tiers: **A** = flagged OR (high & to-you) OR (unread + Focused + to-you + ask) → read. **B** = unread / Focused / active thread / VIP sender → read up to the cap. **C** = cc-only / newsletter / `no-reply` / already-read-and-unflagged → skim preview, skip.

> Note: when everything is already read/unflagged, the flags stop discriminating — lean on **content signals, directness (To vs Cc), thread position, and sender seniority.**

### 3. Read only the top candidates
`get-mail-message` on Tier A + the top of Tier B, capped at ~15 (`READ_CAP`). Optionally confirm "awaiting your reply" by checking **Sent Items** for a later message from the user in that `conversationId`.

### 4. Summarize in EXACTLY 3 bullets
The most important things happening, key asks/decisions, and deadlines. Grouped by thread. No fluff.

### 5. Pick the 3 most important actions
Filter to items that need a response **from the user**. Rank by: hard deadline → someone is blocked on the user → sender seniority → explicit ask.

### 6. Create drafts only — never send
- Reply to existing mail → `create-reply-draft`; group thread → `create-reply-all-draft`; new outreach → `create-draft-email`.
- Concise, professional, matches the user's tone. Use clear `[PLACEHOLDER]` markers for facts only the user knows.
- Never send, delete, or move mail.

### 7. Return output
Return **both** a JSON object matching the contract below **and** a short markdown briefing for humans.

## Output contract

```json
{
  "generated_at": "2026-09-17T13:00:00Z",
  "user": "name@company.com",
  "provider": "outlook",
  "window": { "since_hours": 24, "includes_unresolved": true },
  "summary_bullets": ["...", "...", "..."],
  "priority_actions": [
    {
      "rank": 1,
      "action": "Reply to Jane re: Q3 budget sign-off",
      "reason": "Blocks her deadline today; you're the approver",
      "source_email_id": "AAMk...",
      "draft_id": "AAMk..."
    }
  ],
  "drafts_created": [
    { "draft_id": "AAMk...", "subject": "RE: Q3 budget", "to": ["jane@..."] }
  ],
  "notes": ["auth_ok", "12 messages scanned, 3 unresolved threads"]
}
```

## Tuning (per user / org)

- `VIP_SENDERS`: addresses/domains to always elevate
- `URGENCY_KEYWORDS`: extra deadline/ask phrases to weight
- `READ_CAP`: max full-body reads per run (default 15)
- `WINDOW_HOURS`: recency window (default 24), always plus unresolved

## Guardrails

- Read-only except creating drafts. Never send, delete, or move mail.
- Never invent facts, names, numbers, or commitments — prefer `[PLACEHOLDER]`.
- If the email MCP is unavailable or unauthenticated, return empty results with an explanatory note rather than guessing.
