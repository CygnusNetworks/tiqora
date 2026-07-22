"""Tiqora AI subsystem — LLM providers, MCP client registry, queue AI policies,
drafts, usage metering and the readiness gate (see ``~/TIQORA_LLM_PLAN.md``).

Phase A ships the data model, admin API, and an inert worker skeleton. The
agent runtime (tool loop, PII masking, autonomy guards) lands in Phase B.
"""

from __future__ import annotations
