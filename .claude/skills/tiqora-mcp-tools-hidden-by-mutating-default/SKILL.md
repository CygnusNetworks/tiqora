---
name: tiqora-mcp-tools-hidden-by-mutating-default
description: |
  Tiqora/aurix: MCP tools are silently NOT offered to the LLM even though the
  client is assigned to the queue policy and the tools are enabled. Use when:
  (1) an AI run should have queried an MCP server but didn't and the model
  even notes "Zugriff auf den MCP Server nicht möglich"/escalates, (2) admin
  reports MCP integration "not working" for drafts/auto-reply, (3) debugging
  why request_json tools[] in tiqora_ai_audit_log lacks MCP tool names.
  Root cause: mutating-flag default + autonomy gate in tools.py.
author: Claude Code
version: 1.0.0
date: 2026-07-23
---

# Tiqora: MCP tools invisible below full autonomy (mutating default)

## Problem
An AI draft/auto-reply run never calls the configured MCP server. The model
may explicitly write "MCP server not reachable/automatable" and escalate —
misleading, because the tool was never in its schema at all.

## Context / Trigger Conditions
- Queue policy has the MCP client in `mcp_client_ids`, tool policies are
  `enabled=1` — yet the audit log (`tiqora_ai_audit_log.request_json.tools`)
  shows only builtin tools (propose_customer_message, add_internal_note, …).
- Filter chain: `_load_mcp_tools` (ai/runtime.py) loads only `enabled` tools;
  `ToolRegistry._callable_mcp_tools` (ai/tools.py) then drops every tool where
  `mutating AND autonomy != "full"` — for ALL features incl. manual assist.
- Discovery (`ai/mcp.py`) inserts new tools with `mutating=True` unless the
  server sends the MCP annotation `readOnlyHint: true`. Most read-only
  servers don't send it → their tools are born "mutating" → invisible on any
  queue not running autonomy `full`.

## Solution
1. Confirm via audit log: check `request_json` of the run_id (indexed) — if
   MCP tool names are absent from `tools[]`, it's config, not the model.
   Read-only prod query pattern (mysql CLI is absent on the host):
   `ssh root@<docker-host>… docker exec tiqora-api python -c "…sqlalchemy
   text SELECT…"` (use `await eng.dispose()` to avoid noisy teardown).
2. Fix: mark genuinely read-only tools non-mutating — Admin UI → MCP-Clients →
   "Mutierend" checkbox per tool (or UPDATE tiqora_mcp_tool_policy SET
   mutating=0). Leave real mutators (lock/unlock/…) mutating.
3. Durable fix: add `readOnlyHint: true` annotations on the MCP server so
   future discoveries classify correctly.
4. Model still may not call the tool: no built-in prompt hint exists — add a
   line to the queue system prompt ("bei Störungsmeldungen zuerst den
   Netzstatus prüfen").

## Verification
Re-run a draft; audit log `request_json.tools[]` now contains the MCP tool
names, and `response_json.tool_calls` shows the call.

## Notes
- FEATURE_MCP exists as an ACL feature but is NOT enforced in the runtime
  tool path — don't chase it as a cause.
- The gate is intentional: tool calls execute immediately during the run, so
  a discarded draft would already have executed a mutating call.

## Variant: MCP results stored as object repr (fixed 2026-07-24)
Symptom: tool_trace/audit content for MCP tools is an unreadable one-liner
like `"content=[TextContent(type='text', text='…')] …"`. Cause: fastmcp's
`client.call_tool()` returns a `CallToolResult` OBJECT; `json.dumps(raw,
default=str)` serializes its repr, not its payload. Fix: normalize first
(`_mcp_result_payload` in ai/tools.py — prefer `structured_content`/`data`,
else join `content[].text` and json-parse). Also matters for escalation
rules: field paths (`{"field": "status"}`) only resolve on dict payloads,
never on the result object. General fastmcp lesson: never json.dumps a
CallToolResult directly.
