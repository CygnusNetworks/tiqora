"""Ticket-pinned tool registry + executor (plan §3.4 step 8, §3.8).

Every tool call in an agent run is pinned to one ticket (``ticket_id`` is
never a model-supplied argument) and goes through this module's hard
allowlist — the executor rejects any tool name it does not recognise, so a
model that "invents" a tool name (prompt injection, hallucination) can never
reach a real side effect (plan §3.8).

The model has exactly **one** way to hand a customer-facing text to the
runtime: :data:`TOOL_PROPOSE_CUSTOMER_MESSAGE`. There is no ``send`` tool —
the autonomy → draft/send mapping happens in :mod:`tiqora.ai.runtime`, never
here and never in the model.

MCP passthrough tools are looked up by ``"{client_name}:{tool_name}"``; a
*mutating* MCP tool is only ever exposed in the schema (and thus callable)
when ``autonomy == full`` (plan §3.3/§3.4). The Escalation-Rule-Guard runs
here, on the **raw** MCP result, before the caller ever sees a masked
version (plan §3.1) — a hit is surfaced as ``escalated=True`` and the
outcome is terminal, exactly like the model calling ``escalate_to_human``
itself.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.escalation import check_escalation
from tiqora.ai.listfields import parse_str_list
from tiqora.ai.models import AUTONOMY_FULL, DEFAULT_ALLOWED_STATE_TYPES
from tiqora.ai.pii import PiiMapper
from tiqora.domain.ticket_write_service import ArticleIn, add_article, change_priority
from tiqora.domain.ticket_write_service import change_state as _change_state
from tiqora.domain.ticket_write_service import set_customer as _set_customer
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)


def resolve_allowed_state_types(raw: str | None) -> list[str]:
    """Tolerant-parse ``tiqora_ai_queue_policy.allowed_state_types``.

    ``None``/blank (never configured) falls back to
    :data:`tiqora.ai.models.DEFAULT_ALLOWED_STATE_TYPES` — reopen allowed,
    nothing else. An explicit empty JSON array (``"[]"``) is a deliberate
    admin choice to disable state changes entirely and is returned as-is.
    """
    if raw is None or not raw.strip():
        return list(DEFAULT_ALLOWED_STATE_TYPES)
    return parse_str_list(raw)


TOOL_PROPOSE_CUSTOMER_MESSAGE = "propose_customer_message"
TOOL_ADD_INTERNAL_NOTE = "add_internal_note"
TOOL_UPDATE_TICKET_FIELDS = "update_ticket_fields"
TOOL_ESCALATE_TO_HUMAN = "escalate_to_human"
TOOL_KB_SEARCH = "kb_search"
TOOL_KB_GET_ARTICLE = "kb_get_article"

LOCAL_TOOL_NAMES = frozenset(
    {
        TOOL_PROPOSE_CUSTOMER_MESSAGE,
        TOOL_ADD_INTERNAL_NOTE,
        TOOL_UPDATE_TICKET_FIELDS,
        TOOL_ESCALATE_TO_HUMAN,
        TOOL_KB_SEARCH,
        TOOL_KB_GET_ARTICLE,
    }
)


class UnknownToolError(Exception):
    """The model called a tool name that is not in the registry/allowlist."""


class ToolArgumentError(Exception):
    """The model called a known tool with invalid/missing arguments."""


@dataclass(frozen=True, slots=True)
class McpToolSpec:
    client_name: str
    client_url: str
    auth_token: str | None
    tool_name: str
    mutating: bool
    description: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.client_name}:{self.tool_name}"


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    name: str
    content_for_model: str
    terminal: bool = False
    proposal: dict[str, str] | None = None  # {"kind", "subject", "body"} — unmasked
    escalate_reason: str | None = None
    raw_result: Any = None


# Injectable seams (tests fake these; production wires real fastmcp/KB calls).
McpCaller = Callable[[str, str | None, str, dict[str, Any]], Awaitable[Any]]


class KbSearchFn(Protocol):
    async def __call__(self, query: str, *, limit: int) -> list[dict[str, Any]]: ...


class KbGetArticleFn(Protocol):
    async def __call__(self, article_id: int) -> dict[str, Any] | None: ...


async def _default_mcp_call(
    url: str, auth_token: str | None, tool_name: str, arguments: dict[str, Any]
) -> Any:
    from fastmcp import Client

    async with Client(url, auth=auth_token, timeout=30.0) as client:
        return await client.call_tool(tool_name, arguments)


def _mcp_result_payload(raw: Any) -> Any:
    """Normalize a fastmcp ``CallToolResult`` into plain data before it is
    JSON-serialized for the model/trace — ``json.dumps(raw, default=str)``
    on the result object itself would store its repr
    (``"content=[TextContent(...)］"``), which neither the model nor the UI
    formatter can read. Duck-typed so test fakes returning dicts/lists/str
    pass through untouched."""
    if raw is None or isinstance(raw, (dict, list, str, int, float, bool)):
        return raw
    structured = getattr(raw, "structured_content", None)
    if isinstance(structured, (dict, list)):
        return structured
    data = getattr(raw, "data", None)
    if isinstance(data, (dict, list, str, int, float, bool)):
        return data
    content = getattr(raw, "content", None)
    if isinstance(content, list):
        texts = [t for t in (getattr(part, "text", None) for part in content) if t]
        if texts:
            joined = "\n".join(texts)
            try:
                return json.loads(joined)
            except ValueError:
                return joined
    return str(raw)


def _local_tool_schemas(*, kb_enabled: bool) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": TOOL_PROPOSE_CUSTOMER_MESSAGE,
                "description": (
                    "Deliver a customer-facing message. This is the ONLY way to send "
                    "text to the customer; the runtime decides (based on queue "
                    "autonomy) whether it is sent immediately or kept as a draft for "
                    "a human to review."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["reply", "clarify"]},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["kind", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_ADD_INTERNAL_NOTE,
                "description": (
                    "Add an internal (agent-only, never customer-visible) note with "
                    "meta information — e.g. why no reply was sent. Never use this "
                    "to draft a customer answer."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"body": {"type": "string"}},
                    "required": ["body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_UPDATE_TICKET_FIELDS,
                "description": (
                    "Set ticket state/priority/customer_id. Pass at most one of "
                    "'state' (state name, e.g. \"open\") or 'state_id' (numeric id) — "
                    "never both. Which target states are allowed is a queue policy "
                    "setting; an unlisted state is rejected."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "state": {"type": "string"},
                        "state_id": {"type": "integer"},
                        "priority_id": {"type": "integer"},
                        "customer_id": {"type": "string"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": TOOL_ESCALATE_TO_HUMAN,
                "description": (
                    "Stop autonomous handling and hand the ticket to a human agent. "
                    "Use this whenever you are uncertain, or an escalation condition "
                    "applies."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ["reason"],
                },
            },
        },
    ]
    if kb_enabled:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": TOOL_KB_SEARCH,
                    "description": "Search the knowledge base.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        )
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": TOOL_KB_GET_ARTICLE,
                    "description": "Fetch one knowledge base article by id.",
                    "parameters": {
                        "type": "object",
                        "properties": {"article_id": {"type": "integer"}},
                        "required": ["article_id"],
                    },
                },
            }
        )
    return schemas


class ToolRegistry:
    """Builds the tool JSON-schema list the model sees, gated by autonomy."""

    def __init__(
        self,
        *,
        autonomy: str,
        mcp_tools: list[McpToolSpec] | None = None,
        kb_enabled: bool = True,
    ) -> None:
        self._autonomy = autonomy
        self._mcp_tools = {t.full_name: t for t in (mcp_tools or [])}
        self._kb_enabled = kb_enabled

    def _callable_mcp_tools(self) -> dict[str, McpToolSpec]:
        return {
            name: spec
            for name, spec in self._mcp_tools.items()
            if not spec.mutating or self._autonomy == AUTONOMY_FULL
        }

    def build_schemas(self) -> list[dict[str, Any]]:
        schemas = _local_tool_schemas(kb_enabled=self._kb_enabled)
        for name, spec in self._callable_mcp_tools().items():
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": spec.description or f"MCP tool {name}",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        },
                    },
                }
            )
        return schemas

    def is_known(self, name: str) -> bool:
        if name in (TOOL_KB_SEARCH, TOOL_KB_GET_ARTICLE):
            return self._kb_enabled
        if name in LOCAL_TOOL_NAMES:
            return True
        return name in self._callable_mcp_tools()

    def mcp_spec(self, name: str) -> McpToolSpec | None:
        return self._callable_mcp_tools().get(name)


class ToolExecutor:
    """Executes one tool call. Ticket/user context is fixed at construction —
    the model can never redirect a call to another ticket."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        sysconfig: SysConfig,
        registry: ToolRegistry,
        ticket_id: int,
        acting_user_id: int,
        pii: PiiMapper,
        escalation_rules: list[dict[str, Any]] | None,
        mcp_caller: McpCaller | None = None,
        kb_search_fn: KbSearchFn | None = None,
        kb_get_article_fn: KbGetArticleFn | None = None,
        allowed_state_types_raw: str | None = None,
        mask_results: bool = True,
    ) -> None:
        self._session = session
        self._sysconfig = sysconfig
        self._registry = registry
        self._ticket_id = ticket_id
        self._acting_user_id = acting_user_id
        self._pii = pii
        self._escalation_rules = escalation_rules
        self._mcp_caller = mcp_caller or _default_mcp_call
        self._kb_search_fn = kb_search_fn
        self._kb_get_article_fn = kb_get_article_fn
        self._allowed_state_types = resolve_allowed_state_types(allowed_state_types_raw)
        # Mirrors the queue policy's pii_masking flag: with masking off, tool
        # results reach the model verbatim (before this flag, results were
        # ALWAYS pattern-masked — timestamps etc. got shredded even on
        # queues with PII masking disabled).
        self._mask_results = mask_results

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        if not self._registry.is_known(name):
            raise UnknownToolError(f"Tool not registered/allowed: {name!r}")

        if name == TOOL_PROPOSE_CUSTOMER_MESSAGE:
            return await self._propose_customer_message(arguments)
        if name == TOOL_ADD_INTERNAL_NOTE:
            return await self._add_internal_note(arguments)
        if name == TOOL_UPDATE_TICKET_FIELDS:
            return await self._update_ticket_fields(arguments)
        if name == TOOL_ESCALATE_TO_HUMAN:
            return await self._escalate_to_human(arguments)
        if name == TOOL_KB_SEARCH:
            return await self._kb_search(arguments)
        if name == TOOL_KB_GET_ARTICLE:
            return await self._kb_get_article(arguments)

        spec = self._registry.mcp_spec(name)
        if spec is None:
            # Known-but-blocked (e.g. mutating tool while autonomy != full) —
            # still reject hard rather than silently no-op.
            raise UnknownToolError(f"Tool not callable in this run: {name!r}")
        return await self._call_mcp(spec, arguments)

    async def _propose_customer_message(self, arguments: dict[str, Any]) -> ToolOutcome:
        kind = arguments.get("kind")
        body = arguments.get("body")
        if kind not in ("reply", "clarify") or not isinstance(body, str) or not body.strip():
            raise ToolArgumentError(
                "propose_customer_message requires kind in {reply, clarify} and a non-empty body"
            )
        subject = arguments.get("subject")
        proposal = {
            "kind": kind,
            "subject": self._pii.unmask(subject) if isinstance(subject, str) else "",
            "body": self._pii.unmask(body),
        }
        return ToolOutcome(
            name=TOOL_PROPOSE_CUSTOMER_MESSAGE,
            content_for_model="Message proposal recorded.",
            terminal=True,
            proposal=proposal,
        )

    async def _add_internal_note(self, arguments: dict[str, Any]) -> ToolOutcome:
        body = arguments.get("body")
        if not isinstance(body, str) or not body.strip():
            raise ToolArgumentError("add_internal_note requires a non-empty body")
        unmasked = self._pii.unmask(body)
        await add_article(
            self._session,
            ticket_id=self._ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject="AI agent note",
                body=unmasked,
                channel="note",
            ),
            user_id=self._acting_user_id,
            sysconfig=self._sysconfig,
        )
        return ToolOutcome(name=TOOL_ADD_INTERNAL_NOTE, content_for_model="Internal note added.")

    async def _resolve_state_id(self, *, state_id: Any, state_name: Any) -> int:
        """Resolve the ``state``/``state_id`` argument pair to a concrete
        ticket_state id, enforcing the policy's ``allowed_state_types``
        whitelist against the target state's *type* — for both ways in
        (name or numeric id) equally."""
        if state_id is not None and state_name is not None:
            raise ToolArgumentError("update_ticket_fields: pass only one of 'state' or 'state_id'")
        if state_name is not None:
            if not isinstance(state_name, str) or not state_name.strip():
                raise ToolArgumentError("update_ticket_fields: 'state' must be a non-empty string")
            row = (
                await self._session.execute(
                    text(
                        "SELECT ts.id, tst.name FROM ticket_state ts"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " WHERE LOWER(ts.name) = LOWER(:name) LIMIT 1"
                    ),
                    {"name": state_name.strip()},
                )
            ).first()
            if row is None:
                raise ToolArgumentError(f"Unknown ticket state: {state_name!r}")
            resolved_id, type_name = int(row[0]), str(row[1])
        else:
            row = (
                await self._session.execute(
                    text(
                        "SELECT tst.name FROM ticket_state ts"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " WHERE ts.id = :sid LIMIT 1"
                    ),
                    {"sid": int(state_id)},
                )
            ).first()
            if row is None:
                raise ToolArgumentError(f"Unknown ticket state id: {state_id!r}")
            resolved_id, type_name = int(state_id), str(row[0])

        if type_name not in self._allowed_state_types:
            raise ToolArgumentError(
                f"State change to type {type_name!r} is not allowed by policy "
                f"(allowed: {sorted(self._allowed_state_types)})"
            )
        return resolved_id

    async def _update_ticket_fields(self, arguments: dict[str, Any]) -> ToolOutcome:
        applied: list[str] = []
        state_id_arg = arguments.get("state_id")
        state_name_arg = arguments.get("state")
        if state_id_arg is not None or state_name_arg is not None:
            resolved_state_id = await self._resolve_state_id(
                state_id=state_id_arg, state_name=state_name_arg
            )
            await _change_state(
                self._session,
                ticket_id=self._ticket_id,
                new_state_id=resolved_state_id,
                user_id=self._acting_user_id,
                sysconfig=self._sysconfig,
            )
            applied.append("state")
        priority_id = arguments.get("priority_id")
        if priority_id is not None:
            await change_priority(
                self._session,
                ticket_id=self._ticket_id,
                new_priority_id=int(priority_id),
                user_id=self._acting_user_id,
                sysconfig=self._sysconfig,
            )
            applied.append("priority_id")
        customer_id = arguments.get("customer_id")
        if customer_id is not None:
            unmasked_cid = self._pii.unmask(str(customer_id))
            await _set_customer(
                self._session,
                ticket_id=self._ticket_id,
                customer_id=unmasked_cid,
                customer_user_id=None,
                user_id=self._acting_user_id,
            )
            applied.append("customer_id")
        if not applied:
            raise ToolArgumentError(
                "update_ticket_fields requires at least one of state_id/priority_id/customer_id"
            )
        return ToolOutcome(
            name=TOOL_UPDATE_TICKET_FIELDS,
            content_for_model=f"Updated: {', '.join(applied)}.",
        )

    async def _escalate_to_human(self, arguments: dict[str, Any]) -> ToolOutcome:
        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ToolArgumentError("escalate_to_human requires a non-empty reason")
        unmasked = self._pii.unmask(reason)
        await add_article(
            self._session,
            ticket_id=self._ticket_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=False,
                subject="AI agent escalation",
                body=f"Escalated to human: {unmasked}",
                channel="note",
            ),
            user_id=self._acting_user_id,
            sysconfig=self._sysconfig,
        )
        return ToolOutcome(
            name=TOOL_ESCALATE_TO_HUMAN,
            content_for_model="Escalated to human.",
            terminal=True,
            escalate_reason=unmasked,
        )

    async def _kb_search(self, arguments: dict[str, Any]) -> ToolOutcome:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolArgumentError("kb_search requires a non-empty query")
        if self._kb_search_fn is None:
            return ToolOutcome(name=TOOL_KB_SEARCH, content_for_model="[]")
        results = await self._kb_search_fn(self._pii.unmask(query), limit=5)
        content = json.dumps(results, default=str)
        if self._mask_results:
            content = self._pii.mask(content)
        return ToolOutcome(name=TOOL_KB_SEARCH, content_for_model=content, raw_result=results)

    async def _kb_get_article(self, arguments: dict[str, Any]) -> ToolOutcome:
        article_id = arguments.get("article_id")
        if article_id is None:
            raise ToolArgumentError("kb_get_article requires article_id")
        if self._kb_get_article_fn is None:
            return ToolOutcome(name=TOOL_KB_GET_ARTICLE, content_for_model="null")
        result = await self._kb_get_article_fn(int(article_id))
        content = json.dumps(result, default=str)
        if self._mask_results:
            content = self._pii.mask(content)
        return ToolOutcome(name=TOOL_KB_GET_ARTICLE, content_for_model=content, raw_result=result)

    async def _call_mcp(self, spec: McpToolSpec, arguments: dict[str, Any]) -> ToolOutcome:
        unmasked_args = {
            k: (self._pii.unmask(v) if isinstance(v, str) else v) for k, v in arguments.items()
        }
        raw_result = _mcp_result_payload(
            await self._mcp_caller(spec.client_url, spec.auth_token, spec.tool_name, unmasked_args)
        )
        hit = check_escalation(
            self._escalation_rules, tool_full_name=spec.full_name, raw_result=raw_result
        )
        if hit is not None:
            logger.info(
                "ai_escalation_rule_hit",
                ticket_id=self._ticket_id,
                tool=spec.full_name,
                rule_index=hit.rule_index,
            )
            return ToolOutcome(
                name=spec.full_name,
                content_for_model="Escalation rule matched; handing off to a human.",
                terminal=True,
                escalate_reason=f"Escalation rule matched for tool {spec.full_name}",
                raw_result=raw_result,
            )
        content = json.dumps(raw_result, default=str)
        if self._mask_results:
            content = self._pii.mask(content)
        return ToolOutcome(name=spec.full_name, content_for_model=content, raw_result=raw_result)


__all__ = [
    "LOCAL_TOOL_NAMES",
    "TOOL_ADD_INTERNAL_NOTE",
    "TOOL_ESCALATE_TO_HUMAN",
    "TOOL_KB_GET_ARTICLE",
    "TOOL_KB_SEARCH",
    "TOOL_PROPOSE_CUSTOMER_MESSAGE",
    "TOOL_UPDATE_TICKET_FIELDS",
    "KbGetArticleFn",
    "KbSearchFn",
    "McpCaller",
    "McpToolSpec",
    "ToolArgumentError",
    "ToolExecutor",
    "ToolOutcome",
    "ToolRegistry",
    "UnknownToolError",
    "resolve_allowed_state_types",
]
