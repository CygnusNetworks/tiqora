---
name: tiqora-pii-ipv6-regex-eats-zulu-timestamps
description: |
  Fix for ISO-8601 timestamps arriving at the LLM mangled as
  "2026-09-10T14[IPV6_1]36Z" in Tiqora AI tool calls / drafts. Use when:
  (1) a `timestamp_utc` (or any "Z"-terminated ISO time) in an MCP tool
  result comes back with an [IPV6_n] placeholder swallowing the middle
  colon(s), so events can no longer be correlated, (2) you are about to
  blame the MCP client's PII filter for numbered placeholders in a Tiqora
  run — Tiqora masks tool results itself in tiqora.ai.tools, (3) you add a
  "looks like a time of day" guard to a loose IPv6 regex and it fires for
  "07:53:55+00:00" but not for "14:36:36Z", (4) a masker guard that tests
  `match.group(0)` behaves differently depending on what character FOLLOWS
  the matched value. Root cause: a \b-anchored regex can match only a PARTIAL
  span (":36:") when the surrounding characters are word characters.
author: Claude Code
version: 1.0.0
date: 2026-09-10
---

# Tiqora's PII masker eats "Z"-terminated ISO timestamps

## Problem

A tool result reaches the model as

```json
{"timestamp_utc": "2026-09-10T14[IPV6_1]36Z"}
```

The timestamp is unusable — and timestamps in tool output exist for exactly
one purpose: correlating events across tools (log line vs. activation time vs.
session start). The same run's `2026-07-24T07:53:55+00:00` timestamps are
untouched, which makes the bug look random.

## Context / Trigger Conditions

- Numbered placeholders (`[IPV6_1]`, `[PHONE_2]`) in a **Tiqora** agent run.
  Do **not** conclude "the client's PII filter did it" here: Tiqora masks MCP
  tool results itself in `backend/src/tiqora/ai/tools.py` (`self._pii.mask(content)`
  in `_call_mcp`, `_kb_search`, `_kb_get_article`) with
  `tiqora.ai.pii.PiiMapper`. See also `mcp-output-redacted-by-client-pii-filter`
  for the case where masking really is external.
- Only *some* timestamps are hit: `…+00:00` survives, `…Z` does not.
- The masker already HAS a time-of-day guard and it still happens.

## Solution

### Why the guard misses

`_IPV6_RE = \b(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}\b` is anchored on
word boundaries, so the matched span depends on the character *after* the
seconds:

| Text | IPv6 candidate | Why |
|---|---|---|
| `2026-07-24T07:53:55+00:00` | `:53:55` | `+` is a non-word char → `\b` closes after `55` |
| `2026-09-10T14:36:36.123Z` | `:36:36` | `.` closes the boundary |
| `2026-09-10T14:36:36Z` | `:36:` | `Z` is a **word char** — no `\b` after `36`, so the match must end right before the `3` |

A validator that tests `match.group(0)` against a clock-time pattern
(`\A:?\d{1,2}(:\d{2}){1,2}\Z`) recognises `:53:55` and `:36:36` but not the
truncated `:36:` — so the Zulu form gets masked.

### The fix

Widen the span over the digits the match cut into *before* testing it
(`backend/src/tiqora/ai/pii.py`):

```python
def _widen_over_digits(text: str, start: int, end: int) -> str:
    while start > 0 and text[start - 1].isdigit():
        start -= 1
    while end < len(text) and text[end].isdigit():
        end += 1
    return text[start:end]


def _validate_ipv6(match: re.Match[str]) -> bool:
    start, end = match.span()
    return not _TIME_LIKE_RE.match(_widen_over_digits(match.string, start, end).strip())
```

`:36:` widens to `14:36:36` and is recognised. O(1) per candidate (digit runs
are short) — unlike rescanning the whole text for clock times per candidate,
which is quadratic on a log-heavy diagnose payload.

The general rule: **a guard on a `\b`-anchored regex must validate the
surrounding context, not just `match.group(0)`** — the span you get is not
necessarily the value you meant to reject.

## Verification

```sh
cd backend && uv run python -c "
import sys; sys.path.insert(0,'src')
from tiqora.ai.pii import PiiMapper
t = '{\"timestamp_utc\": \"2026-09-10T14:36:36Z\"}'
print(PiiMapper().mask(t) == t)                       # True
print('2a01:238:4d5c::1' not in PiiMapper().mask('ip 2a01:238:4d5c::1'))  # True
"
cd backend && uv run python -m pytest tests/test_ai_pii.py -q
```

Regression test: `test_zulu_timestamps_in_tool_results_are_not_masked_as_ipv6`.

## Notes

- Ironic interaction: the netadmin MCP server switched its timestamps to
  ISO-8601 **because** of the external PII filter (skill
  `mcp-output-redacted-by-client-pii-filter`, `%d.%m.%Y %H:%M:%S` → `[PHONE_n]`).
  Emitting the compact `…Z` form then walked straight into Tiqora's own
  masker. `+00:00` and `.123456Z` happen to be safe; `Z` is not.
- Check masking *end to end* with the shape the producing tool really emits —
  a unit test written against `+00:00` proves nothing about `Z`.
- The same partial-span trap applies to any loose pattern in `pii.py`
  (`_PHONE_RE`, `_MAC_RE`): whenever a validator rejects "false positives",
  ask what the match looks like when the value is embedded in a longer token.
- Ticket 43099 (StudNet/NetAdmin queue).

## References

- `backend/src/tiqora/ai/pii.py` (`_validate_ipv6`, `_TIME_LIKE_RE`)
- `backend/src/tiqora/ai/tools.py` (`_call_mcp`, `_mask_results`)
- See also: `mcp-output-redacted-by-client-pii-filter`, `studnet-kb-llm-consumers`,
  `pii-email-regex-quadratic-on-big-text` (same ported `pii.py`, performance trap
  in `_EMAIL_RE` when masking full tool results / e-mail bodies)
