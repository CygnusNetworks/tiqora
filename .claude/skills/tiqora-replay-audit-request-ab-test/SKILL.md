---
name: tiqora-replay-audit-request-ab-test
description: |
  A/B-test why a Tiqora AI run (auto_reply/draft/summary) produced a wrong answer by
  replaying the exact recorded LLM request from tiqora_ai_audit_log against the same
  provider, N times per variant (original vs. unmasked PII vs. patched KB text / prompt).
  Use when: (1) a user says "die Antwort in Ticket X ist falsch" and asks whether some
  factor (PII masking, KB wording, prompt) caused it, (2) you need evidence rather than a
  guess before changing prompt/KB/masking, (3) placeholders like "[NAME_7]": { appear as
  JSON keys or inside KB text (over-masking of non-person words such as "user", "StudNet",
  "Bonn", "NetAdmin"). Includes how to decrypt pii_map_enc and the provider api key inside
  the prod container.
author: Claude Code
version: 1.0.0
date: 2026-09-19
---

# Replay a Tiqora audit-log LLM request to A/B-test a wrong answer

## Problem
A single wrong AI reply can't tell you *which* input caused it. Theorising ("the masking
broke it") is often wrong: in ticket 43087 the obvious suspect (JSON key `user` masked as
`[NAME_7]`) barely mattered; a misleading KB sentence was the real driver.

## Context / Trigger Conditions
- Wrong/hallucinated reply on a ticket; `tiqora_ai_audit_log` has rows for it
- You want to measure the effect of a fix before implementing it

## Solution
1. Dump audit rows for the ticket (`ticket_id` = internal id, not `tn`). Note: `trigger`
   is a MariaDB reserved word — quote it with backticks.
   The last row of a `run_id` holds the full message list (system, user, all tool results)
   that led to the final tool call; `response_json` is what the model answered.
2. Decrypt the placeholder map inside the prod container (ai-worker/api has env + secret):
   `decrypt_secret(get_settings().secret_key, row.pii_map_enc)` → `{"[NAME_7]": "user", ...}`.
   Provider key: `tiqora_llm_provider.api_key_enc` decrypted the same way; `base_url` from
   that row. `request_json` lacks `model` — add it from the audit row.
3. Build variants by string-replacing placeholders (keep real person names/customer mail
   masked) and/or patching a tool message (parse `header\n{json}` content, edit, re-dump).
4. POST each variant to `{base_url}/chat/completions` N≥6 times (same temperature as prod),
   concurrency ~4, and classify the final tool call (e.g. regex for the wrong claim and for
   the correct instruction). Read a few full bodies — naive regexes misclassify
   ("technically ready, but not activated").
5. Script: `scripts/replay.py` (run via `ssh … 'docker exec -i tiqora-ai-worker sh -c
   "cat > /tmp/r.py && /app/.venv/bin/python /tmp/r.py 8"' < replay.py`; knock first, same
   Bash call).

## Verification
Compare rates per variant. Ticket 43087 (Qwen3-235B, temp 0.2):
original 0/6 correct · masking fixed ~3/14 · masking fixed + KB clarification 8/8.

## Variant: comparing models (same request, other `model`)
Swapping `body["model"]` on the last request of a run answers "would a stronger model
only for the final answer help?" directly — tool history stays the cheap model's, only
the synthesis step changes. Set `max_tokens` ≥16000 for reasoning models. Caveats seen
on 43087 (Nebius, 2026-09): GLM-5.3/-Flash always did one more `kb_search` (single-step
replay can't judge them; needs a multi-turn loop), Kimi-K2.6 sometimes returned empty
text or only an internal note. Also grep the bodies for leaked identifiers (PKZ) —
stronger models copy them into the customer mail more often.

Since Tiqora v0.12.0 a run can hand the final step to another model
(`final_answer_llm_provider_id`); audit rows after the handover carry that provider, so
the "last request of the run" may already be the final model's. Filter by `provider_id`.
Also check `kb_category_ids` of the policy: a category/tag mismatch leaves the KB
bundle empty, so KB Kurzfassungen never reach the model (seen in prod 2026-09-19).

## Notes
- Over-masking root causes found here (PiiMapper matches known_names as `\bword\b`,
  IGNORECASE, everywhere incl. JSON keys and KB text):
  - placeholder customer_user `*-invalid` has first/last name `Invalid`/`User` → "user" masked
  - own agent From header `Netadmin StudNet Bonn <netadmin@…>` → NetAdmin/StudNet/Bonn masked
    (breaks URLs: `stw-[NAME_8].de`)
  - NER tagged "Abteilung Wohnen" as a person
  - PHONE regex eats ticket numbers and big counters (`free_traffic`)
  - meanwhile the actual customer name (From header in CJK, invalid customer_user) was NOT masked
- KB trap found: studnet-kb `01_diagnose_lesen.md` says history `aac` = activation; netadmin
  also writes `aac` rows from `admin: mieterliste` for moves ("Der Mieter … zieht um"), which
  the model read as "account activated" despite `user.active = 0`.
- Sending unmasked non-person terms to the provider is fine; do not unmask real names.

See also: tiqora-piimapper-masks-no-names-by-default, llm-pii-audit-pick-the-authoritative-artifact

## Variant: triage routing (queue moves), incl. tickets that were never triaged
Triage only ran on the newest tickets, so there is often no audit row for the reference
cases. `scripts/simulate_triage.py` dry-runs the real `triage.decide()` (real
`load_candidates`, PiiMapper, prompt, model) with the RAW llm client — no audit, no
`_persist_and_apply`, session rolled back. Source queue is forced (edit `SOURCE_QUEUE`,
`CASES` = (ticket_id, expected key)); it uses the first `customer` article, which for
threads we opened ourselves is the customer's first reply (easy cases — the hard ones
are customer-initiated). Variants: env `VARIANTS_JSON={"name":{"q10":"...","q5":"..."}}`
replaces the source description (`qN` = source) and candidate descriptions.
Run: `ssh … 'docker exec -i -e VARIANTS_JSON=… tiqora-ai-worker sh -c "cat > /tmp/sim.py
&& cd /app && /app/.venv/bin/python /tmp/sim.py 6"' < simulate_triage.py`.
Finding 2026-09-22 (ticket 43113, StW-IT "Netzproblem am Standort" → auto-moved to
stw-bn at 85): NOT the "ignore sender name" prompt rule — the model saw and even cited
"IT-Mitarbeiter des Studierendenwerks". Cause: the source queue's description named only
*who/what kind of communication* ("Abstimmungen mit der IT"), while the target listed
concrete technical topics (Router, WLAN-Technik, kein Internet) → topic beats sender.
Listing the source queue's own technical topics (StW infrastructure: Standortnetz,
Netzwerkschrank, Verkabelung/Drittfirmen, WLAN in Gemeinschaftsräumen, DMZ, Mail-Gateway,
Mieterliste) fixed it: 0/12 → 12/12, resident cases unchanged.
Follow-up (30 hand-labelled historical tickets, labels decided WITH the user because the
live queue placement was partly wrong): prod 19/30 (7 wrong auto-moves) → final 30/30.
Lessons for writing routing_description text:
- Name the deciding axis explicitly ("Entscheidend ist der Anlass, nicht der Ort") —
  a word like "Bewohnernetz" made the model send everything located in a dorm to stw-bn,
  incl. new installations and staff workplaces.
- Long "Nicht hierher:" lists in a CANDIDATE description get read as inclusions
  ("laut q_5 gehört Planung ... dazu"). Keep the target's exclusions short (point to the
  other queue's topics); put detail as positives in the queue it belongs to.
- Don't trust current queue placement as ground truth; ask the user for the rule on
  inconsistent clusters first (here: Unterlassungserklärungen, site outages, common rooms).
`run_sim.sh <variants.json> <out> <samples>` wraps knock+ssh+env; cases in cases.json.
