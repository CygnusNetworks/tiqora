---
name: tiqora-ai-escalation-survives-queue-move
description: |
  Tiqora (aurix repo): why the AI agent stays silent on a ticket even after
  moving it to a different queue, and how to un-stick it. Use when: (1) a
  ticket was escalated by the AI (escalate_to_human / an escalation-rule
  hit) and now the AI still won't auto-reply after the ticket is moved to
  another queue, even one with autonomy enabled, (2) debugging "why isn't
  the AI answering this ticket" when the queue policy looks correct,
  (3) working in backend/src/tiqora/ai/auto_worker.py, handoff.py, or
  domain/ticket_write_service.py's move_queue/change_state, (4) deciding
  whether a ticket needs a human reply vs. a manual "Resume AI" click
  (POST /tickets/{id}/ai/resume, added 2026-09-14).
author: Claude Code
version: 1.0.0
date: 2026-09-14
---

# Tiqora: AI escalation flag survives a queue move

## Problem

A ticket that the AI agent escalated to a human (`escalate_to_human` tool
call, or an escalation-rule hit) stops getting auto-replies — by design.
Moving the ticket to a different queue, even one with an AI autonomy policy
that should apply, does **not** bring auto-replies back. This looks like a
bug ("I fixed the underlying issue and moved the ticket, why is the AI still
silent?") but is deliberate.

## Context / Trigger Conditions

- Ticket was escalated (visible via `tiqora_ai_ticket_state.ai_escalated_at`
  being non-null, or the "AI escalated" dashboard KPI tile).
- The ticket was then moved to a new queue (`move_queue` in
  `ticket_write_service.py`), possibly one with `autonomy=full` and
  auto-reply enabled.
- No new customer message has triggered a run yet, or one has and the AI
  still didn't reply.
- Example: ticket 43103 (2026-09-14) — AI escalated to human too early
  (KB gap, see `[[studnet-kb-llm-consumers]]`), was moved to a different
  queue, and stayed silent because moving queues doesn't touch the flag.

## Solution

Two independent mechanisms are in play — don't conflate them:

1. **The autonomy *policy* is always looked up live.**
   `tiqora/ai/auto_worker.py` calls
   `get_queue_policy_by_queue(session, ticket.queue_id)` fresh on every run,
   reading the ticket's *current* `queue_id`. A queue move alone is picked
   up automatically — no caching problem here.

2. **The handoff flag (`ai_escalated_at`) is queue-independent and sticky.**
   Set by `tiqora/ai/handoff.mark_ai_escalated()` when the agent calls
   `escalate_to_human` or an escalation rule matches. Checked first in
   `auto_worker._cap_reason()` — if set, the run is skipped with reason
   `"escalated_to_human"`, before the queue policy is even consulted.
   It is cleared ONLY by `tiqora/ai/handoff.clear_ai_escalated()`, called
   from exactly two places in `domain/ticket_write_service.py`:
   - `add_article()`, when a human agent sends a **customer-visible**
     reply (`sender_type == "agent" and is_visible_for_customer`) — "a
     human agent sending a customer-visible reply is the human taking the
     ticket back over."
   - `change_state()`, when the ticket becomes closed/merged/removed.

   `move_queue()` calls neither. This is explicit in the
   `auto_worker._cap_reason()` code comment: "a ticket resumes automation
   exactly when a human has picked it up" — a queue move is not "picking
   it up."

**To resume AI on a stuck ticket**, one of:
- Have a human agent send any customer-visible reply (the intended path).
- Close/merge/remove the ticket.
- Since 2026-09-14: `POST /tickets/{id}/ai/resume` (agent-facing, gated by
  the same `note` permission as posting a reply/note) — the explicit
  override for "I fixed the root cause, let the AI resume without writing
  a customer-visible reply." Backend:
  `domain.ticket_write_service.resume_ai_automation()` (writes an internal
  audit note, then calls `clear_ai_escalated`). Frontend: a "Resume AI"
  banner/button in `AiPanel.tsx`, shown when `ai_escalated_at` is set
  (also newly exposed on `AiStateOut` — it wasn't returned by `GET
  /tickets/{id}/ai` at all before this).

## Verification

```sql
SELECT ticket_id, ai_escalated_at FROM tiqora_ai_ticket_state WHERE ticket_id = <id>;
```
Non-null ⇒ auto-reply is blocked regardless of queue/policy.

Or via the API: `GET /tickets/{id}/ai` → `ai_escalated_at` field.

## Notes

- Don't "fix" this by making `move_queue` clear the flag — the stickiness
  is intentional (see the `_cap_reason` comment); a queue move is a
  routing decision, not evidence a human reviewed the ticket.
- Any new backend route in `api/v1/ai.py` needs `packages/api-client/openapi.json`
  regenerated — see `[[tiqora-openapi-regen-or-ci-wipes-schema]]`.
  `frontend/src/lib/ticketAiApi.ts` is hand-written for this route family
  (see its own module docstring), so add the method/type there directly
  rather than waiting on generated bindings.
- New i18n keys need propagating to all ~49 locale files — see
  `[[tiqora-i18n-key-propagation]]`; there's no propagation script, only
  `check-i18n-keys.mjs` to verify, so write one (English fallback text is
  the accepted placeholder for untranslated locales).

## See also
- [[studnet-kb-llm-consumers]] — the KB-truncation bug that caused a bad
  early escalation on ticket 43103 in the first place.
