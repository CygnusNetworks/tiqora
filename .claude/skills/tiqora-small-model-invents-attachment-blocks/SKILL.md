---
name: tiqora-small-model-invents-attachment-blocks
description: |
  Why a smaller/cheaper LLM must not be swapped in for Tiqora ticket summaries
  (tiqora.ai.summary), and how to measure such a swap before doing it. Use when:
  (1) someone asks "können wir für Zusammenfassungen ein günstigeres/kleineres
  Modell nutzen?" or proposes summary_provider_id / summary_model_override,
  (2) a summary names a "Dokumente:"-Absatz, a PDF filename, or an
  "[Anhang: … — ca. N Zeichen]" label for an attachment that does not exist,
  (3) you need the measured cost/latency split per AI feature (summary vs
  manual_assist vs auto_reply) before optimizing the wrong one,
  (4) you want to A/B two models on real prod tickets without writing to the DB.
author: Claude Code
version: 1.0.0
date: 2026-09-13
---

# Tiqora summaries: small models fabricate attachments

## Problem
Summaries look like the obvious place to save LLM money — plain completion, no
tool loop. Both assumptions behind the swap are wrong, and the failure mode is
a silent hallucination rather than a visible error.

## Context / Trigger Conditions
- A request to use a cheaper/smaller model for `tiqora.ai.summary`, or to add
  per-feature model columns to `tiqora_ai_queue_policy`
- A stored `summary_body` mentions a document, filename, or
  `[Anhang: <name> — ca. <n> Zeichen]` block, but the ticket has no such
  attachment (only an image, or nothing at all)
- Any "let's optimize AI cost" discussion for Tiqora

## Solution

### The measured verdict: don't swap

Nine real prod tickets (short / medium / with PDF / incremental), identical
prompt via the real `summarize_ticket()` helpers, three repeats per cell:

| | Qwen3-30B-A3B | gemma-3-27b-it | Qwen3-235B (current) |
|---|---|---|---|
| Fabricated `Dokumente:` on no-doc tickets | **7/9** | **4/9** | **0/9** |
| Correct `Dokumente:` when a doc IS present | 3/3 | 3/3 | 3/3 |
| Total latency, 9 tickets | 51.2 s | — | 56.1 s |
| Relative cost | 56 % | ~56 % | 100 % |

All three handle a *real* document correctly. Only the big one also knows when
there is none.

**The mechanism:** `_SYSTEM_PROMPT` in `tiqora/ai/summary.py` describes the exact
marker `'[Anhang: <filename> — ca. <n> Zeichen]'` and says "you MUST add a
paragraph starting with 'Dokumente:'". Small models reproduce that shape from the
instruction alone, inventing a filename, a plausible char count (observed
repeatedly: "ca. 1200 Zeichen"), and content. Real example — ticket whose only
attachment was a JPEG, vision off, so nothing was in the prompt:

> **Dokumente:** `[Anhang: Fujicar_FC8_Reinigungsbuerste_70_Rabatt.pdf — ca. 1200 Zeichen]`
> … technische Details wie die leistungsstarke Motorleistung … einen direkten Link
> zur Bestellseite und eine QR-Code-Verknüpfung …

Generalizes beyond Tiqora: **a prompt that specifies a literal marker format
teaches a weak model to emit that marker unprompted.** Expect this wherever a
system prompt describes a token the model is supposed to *recognize*.

**No latency win either.** The small model writes 1.5–3× more text on short
mails (the failure `_length_guidance()` at `summary.py:196` already documents),
which cancels its faster per-token speed. It was *slower* than the 235B on 2 of 4
tickets in the repeat run.

**No money either.** Summaries are already the cheap feature. Measured over 90
days of prod: summary 102 runs / ~2.1k prompt tokens avg / **$0.07 total**, vs
manual_assist ~23.5k avg and auto_reply ~45k avg per run. Halving the summary
model saves ~3 cents per quarter. The cost lever is the tool-loop features.

### How to run the A/B yourself (read-only, no DB writes)

Import the real prompt builders so you compare what production actually sends:

```python
from tiqora.ai.summary import (
    _collect_docs, _completion_budget, _length_guidance,
    _render_articles, _system_prompt_for_detail,
)
from tiqora.ai.context import (
    ticket_snapshot, load_articles, render_ticket_header,
    collect_known_names, ner_source_texts,
)
from tiqora.ai.attachment_context import build_attachment_context
```

Rules that keep it read-only:
- Use `session.get(TiqoraAiTicketState, tid)`, **never** `get_or_create_state()`
  (it INSERTs and commits).
- Call `build_attachment_context(..., vision_enabled=False, vision_llm_factory=None)`
  — vision would fire real LLM calls and write audit rows. PDF/DOCX text
  extraction is a separate path and still runs.
- Never call `usage_service.record_usage()`; construct
  `OpenAiCompatLlmClient` directly per model.

Run it inside the prod container (`docker exec tiqora-api
/app/.venv/bin/python`) so DB access and the decrypted provider key stay on the
host. See [[tiqora-production-deploy]] for the host, and note the aiomysql
"Event loop is closed" teardown traceback is harmless noise.

## Verification
Scan outputs for the tell: a `Dokumente:` paragraph or `[Anhang` label on a
ticket where `_collect_docs(rendered_articles, blocks)` returned `[]`. Confirm
against the DB that the ticket really has no text attachment:

```sql
SELECT att.filename, att.content_type, att.content_size
FROM article a LEFT JOIN article_data_mime_attachment att ON att.article_id = a.id
WHERE a.ticket_id = :tid;
```

An `image/*` row only is not a document — with vision off it never reaches the
prompt.

## Notes
- `docker exec … bash -s` with a heredoc over SSH silently truncated or dropped
  output on this host. Base64 the script and `echo <b64> | base64 -d > /tmp/x.sh
  && bash /tmp/x.sh > /tmp/x.out`, then `cat` the file.
- If cost really must come down, look at `manual_assist` / `auto_reply` prompt
  size (20k–45k tokens/run), not at the model for summaries.
- See also: [[nebius-token-factory-eu-model-selection]] — and note that picking
  any public Nebius model gives no EU processing guarantee regardless of size.
