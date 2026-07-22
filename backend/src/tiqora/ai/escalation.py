"""Escalation-Rule-Guard (plan §3.1/§3.4 step 9-10, §3.8).

A generic rule interpreter over **raw, unmasked** MCP tool results — the
guard must run *before* PII masking so masking never destroys a code the
rule is looking for (plan §3.1). Rules are pure queue configuration; the
core knows nothing about what any particular MCP tool returns.

Rule shape (one item of the ``escalation_rules`` JSON array on
``tiqora_ai_queue_policy``)::

    {
      "tool": "netadmin:diagnose_connection",  # "client:tool" | "client:*" | "*"
      "field": "lock_code",                    # optional dot-path; omitted ->
                                                #   the whole serialized result is scanned
      "match": "exact",                        # exact | substring | regex
      "values": ["COPYR", "DDOSA"]
    }

Semantics: OR across all rules and all values within a rule — any hit stops
autonomous sending. A malformed rule (bad shape, unknown ``match``, invalid
regex) is logged and treated as **no match** — a rule matching everything
by default would be the more dangerous failure mode for an escalation
allowlist a queue admin explicitly authored (plan: "defekte Regel = Treffer
wäre zu aggressiv").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_VALID_MATCH_KINDS = frozenset({"exact", "substring", "regex"})


class EscalationRuleError(ValueError):
    """Raised by :func:`validate_escalation_rules` for malformed rule JSON."""


@dataclass(frozen=True, slots=True)
class EscalationHit:
    rule_index: int
    tool: str
    field: str | None
    match: str
    value: str


def _tool_matches(rule_tool: str, tool_full_name: str) -> bool:
    if rule_tool == "*":
        return True
    if rule_tool.endswith(":*"):
        return tool_full_name.split(":", 1)[0] == rule_tool[:-2]
    return rule_tool == tool_full_name


def _resolve_field(result: Any, field: str | None) -> Any:
    if field is None:
        return result
    value: Any = result
    for part in field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return value


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _value_matches(value: Any, *, match: str, candidates: list[str]) -> bool:
    text = _stringify(value)
    if match == "exact":
        return any(text == c for c in candidates)
    if match == "substring":
        return any(c in text for c in candidates)
    if match == "regex":
        for c in candidates:
            try:
                if re.search(c, text):
                    return True
            except re.error:
                logger.warning("ai_escalation_bad_regex", pattern=c)
        return False
    return False


def validate_escalation_rules(rules: list[dict[str, Any]] | None) -> None:
    """Raise :class:`EscalationRuleError` for structurally invalid rules.

    Called at queue-policy save time so a broken rule is caught immediately
    instead of silently never matching at run time.
    """
    if not rules:
        return
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise EscalationRuleError(f"Rule {i}: expected an object")
        tool = rule.get("tool")
        if not isinstance(tool, str) or not tool:
            raise EscalationRuleError(f"Rule {i}: 'tool' must be a non-empty string")
        match = rule.get("match")
        if match not in _VALID_MATCH_KINDS:
            raise EscalationRuleError(
                f"Rule {i}: 'match' must be one of {sorted(_VALID_MATCH_KINDS)}"
            )
        values = rule.get("values")
        values_ok = isinstance(values, list) and values and all(isinstance(v, str) for v in values)
        if not values_ok:
            raise EscalationRuleError(f"Rule {i}: 'values' must be a non-empty list of strings")
        field = rule.get("field")
        if field is not None and not isinstance(field, str):
            raise EscalationRuleError(f"Rule {i}: 'field' must be a string when present")
        if match == "regex":
            assert isinstance(values, list)
            for v in values:
                try:
                    re.compile(v)
                except re.error as exc:
                    raise EscalationRuleError(f"Rule {i}: invalid regex {v!r}: {exc}") from exc


def check_escalation(
    rules: list[dict[str, Any]] | None,
    *,
    tool_full_name: str,
    raw_result: Any,
) -> EscalationHit | None:
    """Return the first matching rule (order irrelevant — pure OR), or ``None``.

    ``raw_result`` must be the **unmasked** tool result. Malformed individual
    rules are skipped (logged), never treated as an automatic hit.
    """
    if not rules:
        return None
    for i, rule in enumerate(rules):
        try:
            if not isinstance(rule, dict):
                raise EscalationRuleError("not an object")
            tool = rule["tool"]
            match = rule["match"]
            values = rule["values"]
            field = rule.get("field")
            if match not in _VALID_MATCH_KINDS or not isinstance(values, list):
                raise EscalationRuleError("invalid shape")
            if not _tool_matches(tool, tool_full_name):
                continue
            resolved = _resolve_field(raw_result, field)
            if _value_matches(resolved, match=match, candidates=[str(v) for v in values]):
                return EscalationHit(
                    rule_index=i, tool=tool, field=field, match=match, value=_stringify(resolved)
                )
        except (KeyError, EscalationRuleError, TypeError) as exc:
            logger.warning("ai_escalation_bad_rule", index=i, error=str(exc))
            continue
    return None


__all__ = ["EscalationHit", "EscalationRuleError", "check_escalation", "validate_escalation_rules"]
