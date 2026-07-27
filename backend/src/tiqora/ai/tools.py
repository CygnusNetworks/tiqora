"""Ticket-pinned tool registry + executor (plan §3.4 step 8, §3.8 + A/B/C).

Every tool call in an agent run is pinned to one ticket (``ticket_id`` is
never a model-supplied argument) and goes through this module's hard
allowlist — the executor rejects any tool name it does not recognise, so a
model that "invents" a tool name (prompt injection, hallucination) can never
reach a real side effect (plan §3.8).

The model has exactly **one** way to hand a customer-facing text to the
runtime: :data:`TOOL_PROPOSE_CUSTOMER_MESSAGE`. There is no ``send`` tool —
the autonomy → draft/send mapping happens in :mod:`tiqora.ai.runtime`, never
here and never in the model.

Which local side-effect tools and which MCP tools are exposed is gated by
:class:`~tiqora.ai.capabilities.AgentCapabilities` (derived from queue
autonomy, optionally overridden). Mutating MCP tools require
``mcp_mutating``; local field updates require the matching capability bits.

MCP passthrough tools are looked up by ``"{client_name}:{tool_name}"``. The
Escalation-Rule-Guard runs here on the **raw** MCP result, before the caller
ever sees a masked version (plan §3.1). Server-side ticket context is
injected under ``_tiqora_*`` keys; model-supplied scope ids are rejected (H2).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.capabilities import AgentCapabilities, capabilities_for_autonomy
from tiqora.ai.escalation import check_escalation
from tiqora.ai.listfields import parse_str_list
from tiqora.ai.models import DEFAULT_ALLOWED_STATE_TYPES
from tiqora.ai.output_guards import CustomerMessageGuardError, validate_customer_message
from tiqora.ai.pii import PiiMapper
from tiqora.ai.prompt_safety import with_untrusted_tool_prefix
from tiqora.domain.ticket_write_service import ArticleIn, add_article, change_priority
from tiqora.domain.ticket_write_service import change_state as _change_state
from tiqora.domain.ticket_write_service import set_customer as _set_customer
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# Server-injected MCP context keys (model may not set or override these).
MCP_CONTEXT_TICKET_ID = "_tiqora_ticket_id"
MCP_CONTEXT_CUSTOMER_ID = "_tiqora_customer_id"
MCP_CONTEXT_CUSTOMER_USER_ID = "_tiqora_customer_user_id"
_MCP_CONTEXT_PREFIX = "_tiqora_"


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

# Argument keys an MCP tool call may NOT carry from the model — they would let
# a prompt-injected model retarget the tool at a foreign ticket/customer/user
# (security review H2). Compared after stripping non-alphanumerics + lowercasing.
_MCP_FORBIDDEN_ARG_KEYS = frozenset(
    {
        "ticketid",
        "ticketnumber",
        "ticketnr",
        "customerid",
        "customeruserid",
        "customeruserlogin",
        "userid",
        "userlogin",
        "ownerid",
        "agentid",
    }
)


def _norm_arg_key(key: str) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


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
    # JSON-schema object (or null) from MCP discovery — used to reject unknown
    # argument keys when the schema declares properties.
    parameters_schema: dict[str, Any] | None = None

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

    from tiqora.security.outbound import OutboundURLError, validate_outbound_url

    try:
        validate_outbound_url(url, allow_private_networks=True)
    except OutboundURLError as exc:
        raise RuntimeError(f"MCP URL rejected: {exc}") from exc

    async with Client(url, auth=auth_token, timeout=30.0) as client:
        return await client.call_tool(tool_name, arguments)


def _mcp_result_payload(raw: Any) -> Any:
    """Normalize a fastmcp ``CallToolResult`` into plain data before it is
    JSON-serialized for the model/trace — ``json.dumps(raw, default=str)``
    on the result object itself would store its repr
    (``"content=[TextContent(...)]"``), which neither the model nor the UI
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


def validate_mcp_arguments_against_schema(
    arguments: dict[str, Any], parameters_schema: dict[str, Any] | None
) -> None:
    """Reject model args that are not in the discovered JSON schema properties.

    When no schema / no properties are available, this is a no-op (the
    forbidden-key blacklist still applies). Required fields are enforced when
    listed. Server-injected ``_tiqora_*`` keys are not part of *arguments*
    here — validation runs on model-supplied args only.
    """
    if not parameters_schema or not isinstance(parameters_schema, dict):
        return
    props = parameters_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return
    allowed = set(props.keys())
    unknown = [k for k in arguments if k not in allowed]
    if unknown:
        raise ToolArgumentError(
            f"MCP tool argument(s) not in schema: {sorted(unknown)} "
            f"(allowed: {sorted(allowed)})"
        )
    required = parameters_schema.get("required")
    if isinstance(required, list):
        missing = [r for r in required if isinstance(r, str) and r not in arguments]
        if missing:
            raise ToolArgumentError(f"MCP tool missing required argument(s): {sorted(missing)}")


def _local_tool_schemas(*, capabilities: AgentCapabilities, kb_enabled: bool) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    if capabilities.propose_message:
        schemas.append(
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
            }
        )
    if capabilities.internal_note:
        schemas.append(
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
            }
        )
    if capabilities.allows_update_ticket_fields():
        field_props: dict[str, Any] = {}
        if capabilities.update_state:
            field_props["state"] = {"type": "string"}
            field_props["state_id"] = {"type": "integer"}
        if capabilities.update_priority:
            field_props["priority_id"] = {"type": "integer"}
        if capabilities.set_customer:
            field_props["customer_id"] = {"type": "string"}
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": TOOL_UPDATE_TICKET_FIELDS,
                    "description": (
                        "Set ticket state/priority/customer_id. Pass at most one of "
                        "'state' (state name, e.g. \"open\") or 'state_id' (numeric id) — "
                        "never both. Which target states are allowed is a queue policy "
                        "setting; an unlisted state is rejected. Which fields are "
                        "available depends on queue autonomy/capabilities."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": field_props,
                    },
                },
            }
        )
    if capabilities.escalate:
        schemas.append(
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
            }
        )
    if kb_enabled and capabilities.kb_read:
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
    """Builds the tool JSON-schema list the model sees, gated by capabilities."""

    def __init__(
        self,
        *,
        autonomy: str | None = None,
        capabilities: AgentCapabilities | None = None,
        mcp_tools: list[McpToolSpec] | None = None,
        kb_enabled: bool = True,
    ) -> None:
        if capabilities is None:
            if autonomy is None:
                raise TypeError("ToolRegistry requires autonomy= or capabilities=")
            capabilities = capabilities_for_autonomy(autonomy)
        self._capabilities = capabilities
        self._mcp_tools = {t.full_name: t for t in (mcp_tools or [])}
        self._kb_enabled = kb_enabled

    @property
    def capabilities(self) -> AgentCapabilities:
        return self._capabilities

    def _callable_mcp_tools(self) -> dict[str, McpToolSpec]:
        out: dict[str, McpToolSpec] = {}
        for name, spec in self._mcp_tools.items():
            if spec.mutating:
                if self._capabilities.mcp_mutating:
                    out[name] = spec
            elif self._capabilities.mcp_readonly:
                out[name] = spec
        return out

    def build_schemas(self) -> list[dict[str, Any]]:
        schemas = _local_tool_schemas(
            capabilities=self._capabilities, kb_enabled=self._kb_enabled
        )
        for name, spec in self._callable_mcp_tools().items():
            params: dict[str, Any]
            if isinstance(spec.parameters_schema, dict) and spec.parameters_schema:
                params = spec.parameters_schema
            else:
                params = {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                }
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": spec.description or f"MCP tool {name}",
                        "parameters": params,
                    },
                }
            )
        return schemas

    def is_known(self, name: str) -> bool:
        if name == TOOL_PROPOSE_CUSTOMER_MESSAGE:
            return self._capabilities.propose_message
        if name == TOOL_ADD_INTERNAL_NOTE:
            return self._capabilities.internal_note
        if name == TOOL_UPDATE_TICKET_FIELDS:
            return self._capabilities.allows_update_ticket_fields()
        if name == TOOL_ESCALATE_TO_HUMAN:
            return self._capabilities.escalate
        if name in (TOOL_KB_SEARCH, TOOL_KB_GET_ARTICLE):
            return self._kb_enabled and self._capabilities.kb_read
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
        ticket_customer_id: str | None = None,
        ticket_customer_user_id: str | None = None,
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
        self._ticket_customer_id = ticket_customer_id
        self._ticket_customer_user_id = ticket_customer_user_id

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
        subject_str = subject if isinstance(subject, str) else ""
        try:
            validate_customer_message(kind=kind, subject=subject_str, body=body)
        except CustomerMessageGuardError as exc:
            raise ToolArgumentError(str(exc)) from exc
        proposal = {
            "kind": kind,
            "subject": self._pii.unmask(subject_str) if subject_str else "",
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
        caps = self._registry.capabilities
        applied: list[str] = []
        state_id_arg = arguments.get("state_id")
        state_name_arg = arguments.get("state")
        if state_id_arg is not None or state_name_arg is not None:
            if not caps.update_state:
                raise ToolArgumentError(
                    "update_ticket_fields: state changes are not allowed by capabilities"
                )
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
            if not caps.update_priority:
                raise ToolArgumentError(
                    "update_ticket_fields: priority changes are not allowed by capabilities"
                )
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
            if not caps.set_customer:
                raise ToolArgumentError(
                    "update_ticket_fields: customer_id changes are not allowed by capabilities"
                )
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
                "update_ticket_fields requires at least one allowed field "
                "(state_id/state, priority_id, and/or customer_id)"
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
            return ToolOutcome(
                name=TOOL_KB_SEARCH,
                content_for_model=with_untrusted_tool_prefix("[]"),
            )
        results = await self._kb_search_fn(self._pii.unmask(query), limit=5)
        content = json.dumps(results, default=str)
        if self._mask_results:
            content = self._pii.mask(content)
        return ToolOutcome(
            name=TOOL_KB_SEARCH,
            content_for_model=with_untrusted_tool_prefix(content),
            raw_result=results,
        )

    async def _kb_get_article(self, arguments: dict[str, Any]) -> ToolOutcome:
        article_id = arguments.get("article_id")
        if article_id is None:
            raise ToolArgumentError("kb_get_article requires article_id")
        if self._kb_get_article_fn is None:
            return ToolOutcome(
                name=TOOL_KB_GET_ARTICLE,
                content_for_model=with_untrusted_tool_prefix("null"),
            )
        result = await self._kb_get_article_fn(int(article_id))
        content = json.dumps(result, default=str)
        if self._mask_results:
            content = self._pii.mask(content)
        return ToolOutcome(
            name=TOOL_KB_GET_ARTICLE,
            content_for_model=with_untrusted_tool_prefix(content),
            raw_result=result,
        )

    def _mcp_server_context(self) -> dict[str, Any]:
        """Pinned ticket identity injected into every MCP call (plan B #3)."""
        ctx: dict[str, Any] = {MCP_CONTEXT_TICKET_ID: self._ticket_id}
        if self._ticket_customer_id:
            ctx[MCP_CONTEXT_CUSTOMER_ID] = self._ticket_customer_id
        if self._ticket_customer_user_id:
            ctx[MCP_CONTEXT_CUSTOMER_USER_ID] = self._ticket_customer_user_id
        return ctx

    async def _call_mcp(self, spec: McpToolSpec, arguments: dict[str, Any]) -> ToolOutcome:
        # Ticket-pinning boundary (security review H2): MCP tools get an
        # unconstrained (or schema-bound) argument object, so a prompt-injected
        # ticket/attachment could make the model call an MCP tool with a
        # *foreign* ticket/customer id. Reject model-supplied scope keys and
        # any attempt to set server-owned ``_tiqora_*`` context.
        for key in arguments:
            if str(key).startswith(_MCP_CONTEXT_PREFIX):
                raise ToolArgumentError(
                    f"MCP tool argument '{key}' is reserved for server-injected context"
                )
            if _norm_arg_key(key) in _MCP_FORBIDDEN_ARG_KEYS:
                raise ToolArgumentError(
                    f"MCP tool argument '{key}' is not allowed — a tool call may not "
                    "target a ticket, customer or user by id."
                )
        validate_mcp_arguments_against_schema(arguments, spec.parameters_schema)
        unmasked_args = {
            k: (self._pii.unmask(v) if isinstance(v, str) else v) for k, v in arguments.items()
        }
        # Server context wins on key collision (should not happen after strip).
        call_args = {**unmasked_args, **self._mcp_server_context()}
        raw_result = _mcp_result_payload(
            await self._mcp_caller(spec.client_url, spec.auth_token, spec.tool_name, call_args)
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
        return ToolOutcome(
            name=spec.full_name,
            content_for_model=with_untrusted_tool_prefix(content),
            raw_result=raw_result,
        )


__all__ = [
    "LOCAL_TOOL_NAMES",
    "MCP_CONTEXT_CUSTOMER_ID",
    "MCP_CONTEXT_CUSTOMER_USER_ID",
    "MCP_CONTEXT_TICKET_ID",
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
    "validate_mcp_arguments_against_schema",
]
