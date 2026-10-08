---
name: tiqora-terminal-force-invents-a-reply
description: |
  Tiqora/aurix: the AI auto-reply sent a customer mail although the model had
  CORRECTLY classified the ticket as needing no answer (advertising,
  newsletter, automated notification, nothing to act on). Use when: (1) an
  admin reports "die KI hat den Status richtig erkannt, trotzdem ging eine
  Mail raus", (2) a ticket carries several tiqora "AI agent note" articles
  saying "keine Aktion erforderlich" / "keine Kommunikation notwendig" and
  then a customer-visible reply anyway, (3) an auto-reply went to a noreply@
  sender or a mailing list, (4) internal notes show update_ticket_fields
  failing with "State change to type 'closed' is not allowed by policy",
  (5) you need to reconstruct what an AI run actually did on production
  (which tools it called, which queue policy applied, why the ticket is
  still in the state it is in).
  Root cause: runtime.py's terminal-force step offers ONLY
  propose_customer_message and escalate_to_human, so "done, nothing to
  answer" is unrepresentable and the model writes a message instead.
author: Claude Code
version: 1.1.0
date: 2026-09-13
---

# Tiqora terminal-force makes the model invent a customer reply

## Problem

`tiqora.ai.runtime.run_ticket_agent` ends its tool loop only on a *terminal*
tool. If the model burns every round without calling one, a **terminal-force**
step re-prompts it with a restricted tool list and the instruction "You MUST
now call exactly one tool".

Until this was fixed that list held exactly two entries —
`propose_customer_message` and `escalate_to_human`. A model that has concluded
"this is advertising, nobody needs to do anything" can express neither. It
picks `propose_customer_message`, and under `autonomy=full` that goes straight
out by mail — often to a `noreply@` address, so it also bounces or loops.

The queue policy makes it worse: `allowed_state_types` defaults to `["open"]`
(`DEFAULT_ALLOWED_STATE_TYPES` in `ai/models.py`). A model trying to close a
junk ticket gets rejected twice, burns rounds on retries and explanatory
notes, and lands in the terminal-force it would otherwise never have reached.

## Context / Trigger Conditions

- Ticket shows a chain of `AI agent note` articles that say, in substance,
  "no action required" / "no customer communication necessary", immediately
  followed by a customer-visible article.
- Notes mention a rejected state change: *"Statusänderung auf 'closed
  successful' fehlgeschlagen, da laut Policy nur 'open' erlaubt ist."*
- `tiqora_ai_audit_log` for the run has exactly `max_tool_rounds` rows plus
  one more whose `request_json` contains `research budget is exhausted`.
- The sender is `noreply@…`, a newsletter, or a mailing list.

## Solution

Three layers; the first is the actual root cause, the others stop the class of
mail from ever costing an LLM run.

1. **`no_reply_needed(reason)` terminal tool** (`ai/tools.py`). Writes an
   internal note, returns `ToolOutcome(terminal=True, no_reply_reason=...)`,
   creates no customer article. It must be:
   - **not capability-gated** — `is_known()` returns `True` unconditionally
     and the schema is appended outside every `if capabilities.…` branch;
   - **in the terminal-force list** in `runtime.py` (`terminal_schemas` filter)
     *and* named in `_TERMINAL_FORCE_PROMPT` and `_PLAIN_TEXT_NUDGE`.
     Adding the tool without updating those three is the trap: the forced step
     still offers only the old two.
   - handled in `run_ticket_agent` before the `assert outcome.proposal is not
     None`, returning `STATUS_NO_REPLY` and advancing
     `state.last_customer_article_id` (same as the drafted path) so the loop
     guard does not re-decide on the next tick.

   Deliberately it does **not** change the ticket state — `allowed_state_types`
   exists precisely to constrain that, and a tool bypassing it defeats the
   setting. Closing junk is enabled by listing `closed` there instead.

2. **Machine-mail flag at ingest** (`channels/email/machine_mail.py`).
   `_build_get_param` in `pipeline.py` already folds
   `Precedence`/`Auto-Submitted`/`Mailing-List`/`X-Loop` into `X-OTRS-Loop`
   (Znuny parity — that header suppresses the *autoresponse*). The AI never
   read it. `is_auto_generated()` is a deliberate **superset**: `X-OTRS-Loop`
   plus `List-Unsubscribe`/`List-Id`/`List-Post` plus `X-Spam-Flag`/
   `X-Spam-Status` starting with "yes". Widening the loop folding itself would
   change autoresponse behaviour and drift from the Znuny port.

   Critical constraint: **Tiqora stores no raw MIME** — `article_data_mime_plain`
   is empty, so these headers are gone after ingest. The flag must ride along
   on `ArticleIn.auto_generated` → the `ArticleCreate` outbox payload, where
   `ai/auto_worker.py::_process_customer_article_event` reads
   `event.payload.get("auto_generated")`. Old outbox rows lack the key and
   fall through unchanged.

3. **Queue config**: `ignored_senders` (`*@domain` globs, see `ai/senders.py`)
   and `allowed_state_types` including `closed`.

Frontend: `STATUS_NO_REPLY` reaches `manual_run_status` verbatim
(`api/v1/ai.py` passes `run_status=result.status`). Add `"no_reply"` to the
`ManualRunStatus` union in `frontend/src/lib/ticketAiApi.ts` **and** to the
`manualRunSkipped` condition in `AiPanel.tsx`, or the panel spins forever.
`schema.d.ts` types it as plain `string | null`, so no OpenAPI regen needed —
the literal union is hand-written frontend-side.

## Verification

Reconstruct a real run from the production DB (read-only):

```bash
/usr/local/bin/knock-py -d 20 -u <docker-host-fqdn> 8472 65129 2038 && \
  ssh root@<docker-host-fqdn> 'docker exec otrs65-db mysql -u… otrs -e "…"'
```

- Articles + senders:
  `SELECT a.id, st.name, a.is_visible_for_customer, ad.a_subject, ad.a_from
   FROM article a JOIN article_sender_type st ON st.id=a.article_sender_type_id
   LEFT JOIN article_data_mime ad ON ad.article_id=a.id WHERE a.ticket_id=…`
- Note bodies: `SELECT a_body FROM article_data_mime WHERE article_id IN (…)`
  — run this through `rtk proxy`, the RTK grep filter truncates them mid-sentence.
- **What did the run actually do?** Start here, not with the audit log — one
  row per run, already a compact list:
  `SELECT extra_json FROM tiqora_ai_usage WHERE ticket_id=… ORDER BY id`
  → `{"tools_executed": ["Netadmin:diagnose_connection", "kb_search", …,
  "propose_customer_message"], "tool_chain_alerts": [...]}`. A missing
  `update_ticket_fields` there is proof the model never tried to set a state
  (as opposed to trying and being rejected by policy).
- **Which switches applied?** `SELECT * FROM tiqora_ai_queue_policy WHERE
  queue_id=…\G` — `autonomy`, `allowed_state_types`, `capabilities_json`,
  `max_auto_replies`, `ignored_senders`, `reply_language_*` decide most
  "why did it do that" questions before you read a single LLM message.
- Was it the terminal force?
  `SELECT request_json LIKE '%research budget is exhausted%',
          response_json LIKE '%propose_customer_message%'
     FROM tiqora_ai_audit_log WHERE ticket_id=… ORDER BY id`
  → `1  1` on the last row proves it.

Note `tiqora_ai_run` and `tiqora_ai_audit` do **not** exist. The run log is
`tiqora_ai_audit_log` (one row per LLM call, grouped by `run_id`); the
per-run tool trace is `tiqora_ai_usage.extra_json`. Worker container logs
rotate away quickly — both tables are the durable record.
`SHOW TABLES LIKE 'tiqora%'` settles the name in one round trip instead of
guessing.

Tests: `tests/test_email_machine_mail.py` (detector),
`tests/test_postmaster_auto_generated.py` (flag survives into the outbox
payload), `tests/test_ai_auto_worker.py::test_auto_generated_*` (consumer
skips *before* any LLM call — assert `llm.calls == 0`, not just
`auto_replies == 0`), `tests/test_ai_runtime.py::test_no_reply_needed_*`.

## Example

Ticket 2026091010000013, queue 5 (`autonomy=full`, `allowed_state_types=NULL`),
inbound `sipgate GmbH <noreply@sipgate.de>`, "Produkt-Update August/September
2026":

| Round | What the model did |
|---|---|
| 1 | note: *"Marketing-E-Mail … Keine Aktion durch NetAdmin erforderlich."* |
| 2–3 | `update_ticket_fields` → `closed`, then `closed successful` — both rejected |
| 4–12 | four more notes, all "keine Kommunikation notwendig" |
| force | **only propose/escalate offered** → customer mail "Keine Aktion erforderlich" to `noreply@sipgate.de` |

## Notes

- `escalate_to_human` is not a substitute: it sets `ai_escalated_at` and hands
  a human a ticket with nothing to do. The prompt also tells the model to use
  it only when it "genuinely cannot help", so a model that *has* understood the
  ticket honestly rejects it.
- `DEFAULT_MAX_TOOL_ROUNDS = 12`. A run with exactly 12 audit rows + 1 is the
  fingerprint of the forced path; fewer rows means the model chose to propose.
- Watch the `no_reply` counter in the `ai_auto_worker_tick` log line to see how
  often the new exit is taken.

## See also

- `tiqora-mcp-tools-hidden-by-mutating-default` — same subsystem, different
  root cause (tools missing from the schema rather than the outcome missing).
- `tiqora-openapi-regen-or-ci-wipes-schema` — when a backend model change *does*
  require regenerating `packages/api-client/openapi.json`.
- `knock-py-window-immediate-ssh` — chain knock+ssh for the DB queries above.
