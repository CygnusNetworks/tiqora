---
name: tiqora-piimapper-masks-no-names-by-default
description: |
  Tiqora tiqora.ai.pii.PiiMapper masks person NAMES only when the caller
  passes known_names — a bare PiiMapper() silently masks nothing but
  regex identifiers (mail, phone, IPv4/IPv6, MAC). Use when: (1) adding a new
  AI feature that sends ticket, article or agent-composed text to an LLM
  provider, (2) reviewing whether an existing feature honours pii_masking,
  (3) someone asks "wird da eigentlich PII maskiert?" and the answer looks
  like yes because the policy flag is read, (4) a customer name appears in
  tiqora_ai_audit_log.request_json although the queue has pii_masking on.
  Also covers the enabled_<feature> / pii_masking interaction that can leave
  one queue unmasked.
author: Claude Code
version: 1.0.0
date: 2026-09-15
---

# PiiMapper masks no names unless you hand it the names

## Problem

A new AI feature reads the queue policy, honours `pii_masking`, and masks
text through `PiiMapper`. It looks complete. It is not: person names go to
the provider in clear text.

```python
pii = PiiMapper()            # regex identifiers only
pii.mask("Frau Mustermann, erreichbar unter 0228-1234")
# -> "Frau Mustermann, erreichbar unter [PHONE_1]"
```

## Context / Trigger Conditions

- Any new caller of `tiqora.ai.pii.PiiMapper` in `backend/src/tiqora/ai/`
- Feature sends ticket/article/agent text to an LLM provider
- The queue policy's `pii_masking` is on, yet names show up in
  `tiqora_ai_audit_log.request_json`
- Code review question: "does this feature mask PII?" — reading the flag is
  not the answer; check how the mapper is constructed

## Root cause

`PiiMapper.__init__` builds its name pattern purely from the caller's
`known_names`. This is deliberate (plan §3.7: "only names explicitly provided
by the caller are ever masked — no NER, no heuristics"), which makes the
omission silent: nothing errors, nothing warns, the masking simply covers
less than it looks like it does.

Regex kinds it *does* cover without help: `EMAIL`, `PHONE`, `IPV4`, `IPV6`,
`MAC`.

## Solution

Construct it the way `tiqora.ai.summary` does:

```python
ner_texts = ner_source_texts(articles, attachment_blocks) if policy.pii_ner_enabled else None
known_names = await collect_known_names(session, ticket, articles, extra_texts=ner_texts)
never_mask = {v for v in (ticket.customer_id, ticket.customer_user_id) if v}
pii = PiiMapper(known_names=known_names or None, never_mask=never_mask or None)
```

**When there is no ticket** (a composer for a ticket that does not exist yet),
`collect_known_names` has nothing to read. Two workable sources:

- the customer the form was opened for — have the client send its
  `customer_user_id` and resolve the name server-side with
  `customer_user_name(session, login)`, rather than trusting a client-sent
  name string;
- `extract_person_names()` over the text actually being sent.

Reject a client-supplied customer when a `ticket_id` is also given, so the two
can never disagree about whose name to mask.

Keep it additive: an unresolvable source should yield fewer candidates, never
an error.

## Verification

Assert on the prompt the provider would receive, not on the mapper:

```python
segments = [Segment(kind="own", text="Frau Mustermann hat sich gemeldet\n")]
await refine_text(session, llm=scripted, ..., ticket_id=seed["ticket_id"])
assert "Mustermann" not in scripted.user_messages[0]
```

In production, `tiqora_ai_audit_log.request_json` for the feature shows
exactly what was sent — grep it for a known surname.

## Notes

- **Check the flag interaction when enabling a feature per queue.** A queue
  can have `enabled_<feature> = true` while `pii_masking = false`; the feature
  then runs completely unmasked on that queue. Found in prod on
  `studentenwerk-bonn` right after enabling refine there. Query both columns
  together before switching a feature on:
  `SELECT queue_id, pii_masking, enabled_refine FROM tiqora_ai_queue_policy`.
- Quoted customer text counts. A composer that sends quotes to the model as
  context is sending customer PII, even though the agent is the one being
  helped.
- `never_mask` keeps `customer_id` / `customer_user_id` readable for tools
  that need them; harmless to omit in a tool-free feature, but cheap to set.

## See also

- `llm-pii-audit-pick-the-authoritative-artifact` — deciding which artifact to
  audit when checking what actually left the system.
