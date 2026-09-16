# Call Intelligence Test Cases

Run each case with `python run_call_intelligence.py <file> "<me>"` and compare against the expected behavior.

| Transcript | Me | Tests | Expected behavior |
|---|---|---|---|
| `call-transcript-acme-sync.md` | Jordan Lee | Clean baseline | 4 mine, 3 delegated, 2 unassigned. Flags Sam's declined CRM offer as a delegation candidate. |
| `call-transcript-noisy-standup.txt` | Maya Okafor | Fragmented speech-to-text, conditional ownership | Token rotation marked conditional ("if I have access"). Runbook update and legal reply unassigned. Flags Jira cleanup → Leo (he offered). |
| `call-transcript-client-escalation.md` | Chloe Martin | External client, commitments by client side | Raj's change calendar listed as client-owned. Credit decision has no internal owner. Flags incident summary → Nina/Ben (they offered). |
| `call-transcript-no-actions.md` | Sofia Lind | No work items | All sections "None". No invented tasks. |
| `pkf-engine-3-daily-sync-transcript.txt` | Cristhian Garcia | Real, long, fragmented call | Engagement letter UI, deploy UI PR, test Gary's PRs, share Docker doc and skill path. Gary owns scheduling Sergey session. |
| `pkf-engine-3-daily-sync-transcript.txt` | Anna | "Me" absent from the call | Items that concern Anna: main-branch merge permissions, PR review/approval, CI/CD. Nothing marked as her explicit commitment. |
