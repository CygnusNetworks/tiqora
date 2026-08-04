"""Tiqora MCP server — FastMCP streamable-HTTP server exposing ticket tools.

Bearer auth: tiqora_api_key hash lookup → principal agent user.
Every tool builds the same permission context as the REST API.

Tools:
Ticket read:
- ticket_search: Meilisearch + permission filter (falls back to DB search)
- ticket_get: markdown-rendered ticket incl. plaintext articles
- ticket_get_by_number: resolve Znuny ticket number (TN) → same payload as ticket_get

Ticket write:
- ticket_create: create a new ticket
- ticket_reply: add customer-visible reply article
- ticket_note: add internal note (is_visible_for_customer=False by default)
- ticket_update_state: change ticket state
- ticket_update_queue: move ticket to a new queue
- ticket_update_priority: change ticket priority
- ticket_update_owner: assign ticket owner
- ticket_set_title: change ticket title
- ticket_set_customer: set customer_id / customer_user_id
- ticket_set_dynamic_field: set a dynamic field value
- ticket_lock / ticket_unlock: lock or unlock a ticket
- ticket_set_type / ticket_set_service / ticket_set_sla: type/service/SLA
- ticket_set_responsible: assign responsible agent
- ticket_watch / ticket_unwatch: subscribe/unsubscribe as watcher
- ticket_archive / ticket_unarchive: set archive flag
- list_attachments / get_attachment_meta: article attachment metadata
- ticket_forward / ticket_bounce: forward or resend-verbatim an article
- ticket_history: recent history rows
- ticket_merge / ticket_link: merge or link tickets

Reference / discovery:
- list_queues: queues the agent may act in (permission-scoped)
- list_states: valid ticket states
- list_priorities: valid priorities
- list_agents: valid agent users for owner/responsible assignment

Knowledge base:
- kb_search: search published knowledge base articles (permission-group scoped)
- kb_get_article: fetch a knowledge base article's full Markdown content
- kb_list: list articles by tag/category
- kb_upsert_article: create or update a KB article
- kb_publish_article: publish + index a KB article

Customer:
- customer_lookup: look up customer user details

All fully async — NO sync DB calls, requests, or time.sleep anywhere.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from contextvars import ContextVar
from datetime import datetime
from typing import Any

import structlog
from fastmcp import Context, FastMCP
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from tiqora.config import Settings, get_settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketStateType
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraApiKey
from tiqora.domain.api_key_scopes import mcp_scopes_allow_write
from tiqora.domain.ticket_service import (
    TicketAccessDenied as ReadAccessDenied,
)
from tiqora.domain.ticket_service import (
    TicketNotFound as ReadNotFound,
)
from tiqora.domain.ticket_service import TicketService
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TicketAccessDenied,
    TicketIn,
    TicketNotFound,
    TicketWriteService,
    _queue_name,  # noqa: PLC2701 -- deliberate reuse, see _assert_queue_permission
    _ticket_must_exist,  # noqa: PLC2701 -- deliberate reuse, see _assert_queue_permission
    unwatch_ticket,
    watch_ticket,
)
from tiqora.kb.schemas import ArticleIn as KbArticleIn
from tiqora.kb.schemas import ArticleUpdateIn as KbArticleUpdateIn
from tiqora.kb.service import KbForbidden, KbNotFound, KbService
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# Znuny's "valid" list id — 1 == valid (same as api/v1/reference.py).
_VALID = 1

# Set by auth middleware for the duration of each MCP request so mutation
# helpers can enforce mcp:rw without threading Context through every call.
_current_api_key_scopes: ContextVar[frozenset[str] | None] = ContextVar(
    "mcp_api_key_scopes", default=None
)


def _utcnow() -> datetime:
    """Naive UTC now — matches DateTime columns (server stores naive)."""
    return datetime.utcnow()  # noqa: DTZ003 — intentional naive UTC for DB columns


# ---------------------------------------------------------------------------
# App state shared by all tool handlers
# ---------------------------------------------------------------------------


class McpState:
    """Shared state created at lifespan and injected via Context.request.state."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )
        self._redis: Any | None = None

    async def redis(self) -> Any | None:
        """Lazy Redis client for API-key rate limiting (optional)."""
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as redis_async

            self._redis = redis_async.from_url(self.settings.redis_url)
            return self._redis
        except Exception:  # noqa: BLE001
            return None

    async def aclose(self) -> None:
        if self._redis is not None:
            with suppress(Exception):
                await self._redis.aclose()
        await self.engine.dispose()

    def sysconfig(self) -> SysConfig:
        async def _noop_fetch(name: str) -> None:
            return None

        return SysConfig(fetch=_noop_fetch)


_mcp_state: McpState | None = None


# ---------------------------------------------------------------------------
# Auth middleware: Bearer tiqora_api_key
# ---------------------------------------------------------------------------


class TiqoraBearerAuth(BaseHTTPMiddleware):
    """Validate Authorization: Bearer <tiqora_api_key> and inject user_id."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        # Every MCP surface (including SSE stream establishment) requires a
        # valid Bearer API key — unauthenticated /sse was a resource-abuse
        # and negotiation surface without a principal.
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        raw_key = auth[7:].strip()
        # Align with REST: only opaque tiqora_* API keys, never session tokens.
        if not raw_key.startswith("tiqora_"):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        state = _mcp_state
        if state is None:
            return JSONResponse({"error": "Server not ready"}, status_code=503)

        resolved = await _resolve_api_key(state.session_factory, raw_key)
        if resolved is None:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        if resolved == "rate_limited":
            return JSONResponse(
                {"error": "API key rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )

        if not isinstance(resolved, tuple):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        user_id, scopes = resolved
        # Scoped keys need mcp:ro/mcp:rw (or legacy mcp/write/*).
        from tiqora.domain.api_key_scopes import mcp_scopes_allow_connect

        if not mcp_scopes_allow_connect(scopes):
            return JSONResponse({"error": "Forbidden: API key lacks mcp scope"}, status_code=403)

        request.state.user_id = user_id
        request.state.api_key_scopes = scopes
        token = _current_api_key_scopes.set(scopes)
        try:
            return await call_next(request)
        finally:
            _current_api_key_scopes.reset(token)


def _parse_api_key_scopes(raw: str | None) -> frozenset[str] | None:
    """Return scope set, or None for unrestricted (empty/null scopes column)."""
    from tiqora.domain.api_key_scopes import parse_api_key_scopes

    return parse_api_key_scopes(raw)


async def _resolve_api_key(
    factory: async_sessionmaker[AsyncSession], raw_key: str
) -> tuple[int, frozenset[str] | None] | None | str:
    """Resolve tiqora_api_key to (user_id, scopes) via SHA-256 hash lookup.

    Parity with ``domain.auth.AuthService.resolve_api_key``: reject expired keys
    and stamp ``last_used_at`` (non-fatal if the metadata write fails).
    ``scopes`` is None when the key is unrestricted (legacy / empty column).

    Returns the string ``"rate_limited"`` when the per-key throttle fires.
    """
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    async with factory() as session:
        row = (
            await session.execute(
                select(TiqoraApiKey).where(
                    TiqoraApiKey.key_hash == key_hash,
                    TiqoraApiKey.valid.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        now = _utcnow()
        if row.expires_at is not None and row.expires_at <= now:
            return None
        # Optional Redis throttle — soft-fail open if Redis is unavailable.
        try:
            from tiqora.security.ratelimit import ApiKeyRateLimiter

            st = _mcp_state
            if st is not None:
                r = await st.redis()
                decision = await ApiKeyRateLimiter(r, st.settings).check_and_incr(int(row.id))
                if not decision.allowed:
                    return "rate_limited"
        except Exception:  # noqa: BLE001 — fail open
            pass
        user = (
            await session.execute(select(Users).where(Users.id == row.user_id, Users.valid_id == 1))
        ).scalar_one_or_none()
        if user is None:
            return None
        scopes = _parse_api_key_scopes(getattr(row, "scopes", None))
        # Stamp last_used_at; auth must not fail if the metadata write fails.
        try:
            row.last_used_at = now
            await session.commit()
        except Exception:  # noqa: BLE001 — non-fatal metadata stamp
            await session.rollback()
        return user.id, scopes


# ---------------------------------------------------------------------------
# FastMCP server definition
# ---------------------------------------------------------------------------


mcp = FastMCP(
    "Tiqora",
    instructions=(
        "Tiqora ticket system MCP server. Provides tools for searching, viewing, "
        "and managing tickets. All operations respect the agent's queue/group permissions."
    ),
)


def _get_user_id(ctx: Context) -> int:
    """Extract user_id injected by auth middleware from request state."""
    request: Request | None = getattr(ctx, "request", None)
    if request is None:
        raise PermissionError("No request context available")
    uid = getattr(request.state, "user_id", None)
    if uid is None:
        raise PermissionError("Not authenticated")
    return int(uid)


def _get_state() -> McpState:
    if _mcp_state is None:
        raise RuntimeError("MCP server not initialised")
    return _mcp_state


def _assert_mcp_write_scope() -> None:
    """Raise ``TicketAccessDenied`` when the key is mcp:ro-only."""
    if not mcp_scopes_allow_write(_current_api_key_scopes.get()):
        raise TicketAccessDenied("API key lacks mcp:rw scope")


def _write_service(session: AsyncSession, state: McpState) -> TicketWriteService:
    """Permission-aware write path shared with REST (includes SMTP for email replies)."""
    return TicketWriteService(
        session,
        state.session_factory,
        state.sysconfig(),
        mail_sender=None,
    )


async def _assert_queue_permission(
    session: AsyncSession, *, ticket_id: int, user_id: int, key: str
) -> None:
    """Raise ``TicketNotFound``/``TicketAccessDenied`` unless *user_id* holds
    *key* on *ticket_id*'s queue.

    Used for tools that still need an explicit pre-check (e.g. history ``ro``,
    merge/link dual-ticket). Mutations that go through :class:`TicketWriteService`
    enforce the same gates inside the service class.
    """
    t = await _ticket_must_exist(session, ticket_id)
    await _assert_raw_queue_permission(
        session, queue_id=int(t["queue_id"]), user_id=user_id, key=key
    )


async def _assert_raw_queue_permission(
    session: AsyncSession, *, queue_id: int, user_id: int, key: str
) -> None:
    """Existence-then-permission gate for a raw queue id (not a ticket's own
    queue). Used where the queue is supplied directly by the caller — ticket
    creation and the *destination* of a move.

    Validates the queue exists first (``InvalidInput`` -> a "no such queue"
    error the caller can act on) before checking the permission, so a typo'd
    queue id is never reported as a misleading "access denied":
    ``PermissionEngine.check`` returns ``False`` indistinguishably for both a
    denied and a nonexistent queue.
    """
    _assert_mcp_write_scope()
    await _queue_name(session, queue_id)  # raises InvalidInput if the queue does not exist
    pe = PermissionEngine(session)
    if not await pe.check(user_id, queue_id, key):
        raise TicketAccessDenied(f"user {user_id} lacks {key!r} on queue {queue_id}")


def _assert_tool_allowed(tool_name: str) -> None:
    """When scopes include any ``tool:…`` token, only those tools may run."""
    scopes = _current_api_key_scopes.get()
    if scopes is None or "*" in scopes:
        return
    tool_tokens = {s[5:] for s in scopes if s.startswith("tool:")}
    if not tool_tokens:
        return
    if tool_name not in tool_tokens:
        raise TicketAccessDenied(f"API key tool allowlist excludes {tool_name!r}")


# ---------------------------------------------------------------------------
# Tool: ticket_search
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Search tickets by keyword or filter criteria. Falls back to DB query "
        "if Meilisearch is unavailable. Returns a list of matching ticket summaries."
    )
)
async def ticket_search(
    ctx: Context,
    query: str = "",
    queue_ids: list[int] | None = None,
    state_type: str | None = None,
    customer_user_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search tickets with optional filters.

    Args:
        query: Full-text search query (title, article body).
        queue_ids: Limit to specific queue IDs.
        state_type: Filter by state type name (e.g. 'new', 'open', 'closed').
        customer_user_id: Filter by customer user login.
        limit: Maximum results (1-100).
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    limit = max(1, min(limit, 100))

    async with state.session_factory() as session:
        pe = PermissionEngine(session)
        allowed_groups = await pe.groups_for_permission(user_id, "ro")
        if not allowed_groups:
            return []

        # Get allowed queue IDs
        allowed_q_rows = (
            (
                await session.execute(
                    select(Queue.id).where(
                        Queue.group_id.in_(allowed_groups),
                        Queue.valid_id == 1,
                    )
                )
            )
            .scalars()
            .all()
        )
        allowed_queues: set[int] = set(allowed_q_rows)

        if queue_ids:
            allowed_queues &= set(queue_ids)
        if not allowed_queues:
            return []

        # Try Meilisearch first
        try:
            results = await _meili_search(
                state.settings, query, allowed_queues, state_type, customer_user_id, limit
            )
            if results is not None:
                return results
        except Exception as exc:  # noqa: BLE001
            logger.debug("mcp_meili_search_failed", error=str(exc))

        # DB fallback
        return await _db_search(session, allowed_queues, query, state_type, customer_user_id, limit)


# Znuny default ticket_state_type names — used as an allowlist so a crafted
# state_type cannot break out of the Meili filter expression.
_MEILI_STATE_TYPES: frozenset[str] = frozenset(
    {
        "new",
        "open",
        "closed",
        "pending reminder",
        "pending auto",
        "removed",
        "merged",
    }
)


def _meili_escape_string(value: str) -> str:
    """Escape a string value for embedding in a Meilisearch filter expression.

    Meili string literals use single quotes; backslash escapes ``\\`` and ``'``.
    Without this, a crafted ``customer_user_id`` can break out of the mandatory
    ``queue_id IN [...]`` clause (Meili binds AND tighter than OR).
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _build_meili_ticket_filters(
    allowed_queues: set[int],
    state_type: str | None,
    customer_user_id: str | None,
) -> str:
    """Build a safe Meili filter that always keeps the queue-permission clause."""
    filters: list[str] = [f"queue_id IN [{','.join(str(q) for q in sorted(allowed_queues))}]"]
    if state_type is not None and state_type in _MEILI_STATE_TYPES:
        # Allowlisted tokens only — no quoting needed, but keep the same form.
        filters.append(f"state_type = '{state_type}'")
    if customer_user_id:
        filters.append(f"customer_user_id = '{_meili_escape_string(customer_user_id)}'")
    return " AND ".join(filters)


async def _meili_search(
    settings: Settings,
    query: str,
    allowed_queues: set[int],
    state_type: str | None,
    customer_user_id: str | None,
    limit: int,
) -> list[dict[str, Any]] | None:
    """Meilisearch-backed search. Returns None if Meili is unavailable."""
    from meilisearch_python_sdk import AsyncClient

    async with AsyncClient(settings.meili_url, settings.meili_master_key) as client:
        index = client.index(settings.meili_tickets_index)
        filter_expr = _build_meili_ticket_filters(allowed_queues, state_type, customer_user_id)

        resp = await index.search(
            query,
            filter=filter_expr,
            limit=limit,
        )
        return [
            {
                "ticket_id": h.get("id"),
                "tn": h.get("tn"),
                "title": h.get("title"),
                "queue": h.get("queue_name"),
                "state": h.get("state"),
                "state_type": h.get("state_type"),
                "priority": h.get("priority"),
                "customer_user_id": h.get("customer_user_id"),
                "create_time": h.get("create_time"),
            }
            for h in (resp.hits or [])
        ]


async def _db_search(
    session: AsyncSession,
    allowed_queues: set[int],
    query: str,
    state_type: str | None,
    customer_user_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """DB fallback search."""
    conditions: list[str] = []
    params: dict[str, Any] = {}

    q_list = list(allowed_queues)
    q_ph = ",".join(f":q{i}" for i in range(len(q_list)))
    conditions.append(f"t.queue_id IN ({q_ph})")
    for i, qid in enumerate(q_list):
        params[f"q{i}"] = qid

    if query:
        conditions.append("t.title LIKE :q")
        params["q"] = f"%{query}%"

    if state_type:
        sid_rows = (
            (
                await session.execute(
                    select(TicketState.id)
                    .join(TicketStateType, TicketStateType.id == TicketState.type_id)
                    .where(TicketStateType.name == state_type)
                )
            )
            .scalars()
            .all()
        )
        if not sid_rows:
            return []
        s_ph = ",".join(f":s{i}" for i in range(len(sid_rows)))
        conditions.append(f"t.ticket_state_id IN ({s_ph})")
        for i, sid in enumerate(sid_rows):
            params[f"s{i}"] = sid

    if customer_user_id:
        conditions.append("t.customer_user_id = :cuid")
        params["cuid"] = customer_user_id

    where_sql = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        f"SELECT t.id, t.tn, t.title, q.name as queue_name,"
        f" ts.name as state_name, tst.name as state_type,"
        f" tp.name as priority_name, t.customer_user_id, t.create_time"
        f" FROM ticket t"
        f" JOIN queue q ON q.id = t.queue_id"
        f" JOIN ticket_state ts ON ts.id = t.ticket_state_id"
        f" JOIN ticket_state_type tst ON tst.id = ts.type_id"
        f" JOIN ticket_priority tp ON tp.id = t.ticket_priority_id"
        f" WHERE {where_sql}"
        f" ORDER BY t.create_time DESC"
        f" LIMIT {limit}"
    )
    rows = (await session.execute(text(sql), params)).fetchall()
    return [
        {
            "ticket_id": r[0],
            "tn": r[1],
            "title": r[2],
            "queue": r[3],
            "state": r[4],
            "state_type": r[5],
            "priority": r[6],
            "customer_user_id": r[7],
            "create_time": r[8].isoformat() if r[8] else None,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Shared ticket markdown renderer (ticket_get / ticket_get_by_number)
# ---------------------------------------------------------------------------


async def _render_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    include_internal_notes: bool = True,
) -> str:
    """Markdown ticket payload, or a not-found / access-denied message string."""
    pe = PermissionEngine(session)
    allowed_groups = await pe.groups_for_permission(user_id, "ro")

    t_row = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.tn, t.title, q.name as queue,"
                    " ts.name as state_name, tst.name as state_type,"
                    " tp.name as priority, t.customer_id, t.customer_user_id,"
                    " t.create_time, t.change_time, q.group_id"
                    " FROM ticket t"
                    " JOIN queue q ON q.id = t.queue_id"
                    " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " JOIN ticket_priority tp ON tp.id = t.ticket_priority_id"
                    " WHERE t.id = :tid LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .first()
    )

    if t_row is None:
        return f"Ticket #{ticket_id} not found."

    if int(t_row["group_id"]) not in allowed_groups:
        return f"Access denied to ticket #{ticket_id}."

    lines: list[str] = [
        f"# Ticket #{ticket_id} — {t_row['tn']}",
        "",
        f"**Title:** {t_row['title']}",
        f"**Queue:** {t_row['queue']}",
        f"**State:** {t_row['state_name']} ({t_row['state_type']})",
        f"**Priority:** {t_row['priority']}",
        f"**Customer:** {t_row['customer_user_id'] or t_row['customer_id'] or 'N/A'}",
        f"**Created:** {t_row['create_time']}",
        f"**Changed:** {t_row['change_time']}",
        "",
        "## Articles",
        "",
    ]

    art_rows = (
        await session.execute(
            text(
                "SELECT a.id, a.is_visible_for_customer, adm.a_from, adm.a_subject,"
                " adm.a_body, adm.a_content_type, a.create_time, ast.name as sender_type"
                " FROM article a"
                " LEFT JOIN article_data_mime adm ON adm.article_id = a.id"
                " LEFT JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                " WHERE a.ticket_id = :tid"
                " ORDER BY a.id"
            ),
            {"tid": ticket_id},
        )
    ).fetchall()

    for art in art_rows:
        is_visible = bool(art[1])
        if not is_visible and not include_internal_notes:
            continue
        visibility = "customer-visible" if is_visible else "internal"
        lines.append(f"### Article #{art[0]} [{art[7] or 'unknown'}] [{visibility}]")
        lines.append(f"**From:** {art[2] or 'N/A'}")
        lines.append(f"**Subject:** {art[3] or '(no subject)'}")
        lines.append(f"**Date:** {art[6]}")
        lines.append("")
        body = art[4] or ""
        content_type = art[5] or ""
        if "html" in content_type.lower():
            import re

            body = re.sub(r"<[^>]+>", "", body)
            body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        lines.append(body[:2000])
        lines.append("")

    df_rows = (
        await session.execute(
            text(
                "SELECT df.name, df.label, dfv.value_text"
                " FROM dynamic_field df"
                " JOIN dynamic_field_value dfv ON dfv.field_id = df.id"
                " WHERE dfv.object_id = :tid AND df.object_type = 'Ticket'"
                "  AND df.valid_id = 1"
                " ORDER BY df.field_order"
            ),
            {"tid": ticket_id},
        )
    ).fetchall()

    if df_rows:
        lines.append("## Dynamic Fields")
        lines.append("")
        for df in df_rows:
            label = df[1] or df[0]
            lines.append(f"- **{label}:** {df[2]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: ticket_get
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Get a ticket by ID with full details including articles (plaintext), "
        "dynamic fields, and metadata. Returns markdown-formatted output."
    )
)
async def ticket_get(
    ctx: Context,
    ticket_id: int,
    include_internal_notes: bool = True,
) -> str:
    """Retrieve a ticket with all visible articles as markdown.

    Args:
        ticket_id: The numeric ticket ID.
        include_internal_notes: Include internal (not customer-visible) notes.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()

    async with state.session_factory() as session:
        return await _render_ticket(
            session,
            ticket_id=ticket_id,
            user_id=user_id,
            include_internal_notes=include_internal_notes,
        )


# ---------------------------------------------------------------------------
# Tool: ticket_get_by_number
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Get a ticket by Znuny ticket number (TN). Returns the same markdown "
        "payload as ticket_get. Use this when a human refers to a ticket by its "
        "visible number rather than the internal ticket_id."
    )
)
async def ticket_get_by_number(
    ctx: Context,
    tn: str,
    include_internal_notes: bool = True,
) -> str | dict[str, Any]:
    """Resolve a ticket number to the markdown ticket view.

    Args:
        tn: The Znuny ticket number (e.g. ``202406011200001``).
        include_internal_notes: Include internal (not customer-visible) notes.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()

    async with state.session_factory() as session:
        ticket_id = (
            await session.execute(
                text("SELECT id FROM ticket WHERE tn = :tn LIMIT 1"),
                {"tn": tn},
            )
        ).scalar_one_or_none()
        if ticket_id is None:
            return {"error": f"Ticket number {tn!r} not found"}

        rendered = await _render_ticket(
            session,
            ticket_id=int(ticket_id),
            user_id=user_id,
            include_internal_notes=include_internal_notes,
        )
        if rendered.startswith("Access denied") or rendered.endswith("not found."):
            return {"error": rendered}
        return rendered


# ---------------------------------------------------------------------------
# Tool: ticket_create
# ---------------------------------------------------------------------------


@mcp.tool(description=("Create a new ticket. Returns the new TicketID and TicketNumber."))
async def ticket_create(
    ctx: Context,
    title: str,
    queue_id: int,
    state_id: int,
    priority_id: int,
    body: str = "",
    subject: str | None = None,
    customer_user_id: str | None = None,
    customer_id: str | None = None,
    is_visible_for_customer: bool = True,
) -> dict[str, Any]:
    """Create a new ticket with an optional first article.

    Args:
        title: Ticket title.
        queue_id: Target queue ID.
        state_id: Initial state ID.
        priority_id: Priority ID.
        body: Article body text (optional).
        subject: Article subject (defaults to ticket title).
        customer_user_id: Customer user login (optional).
        customer_id: Customer company ID (optional).
        is_visible_for_customer: Whether the article is customer-visible.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_create")

    article: ArticleIn | None = None
    if body:
        article = ArticleIn(
            sender_type="agent",
            is_visible_for_customer=is_visible_for_customer,
            subject=subject or title,
            body=body,
            channel="note",
        )

    ticket_in = TicketIn(
        title=title,
        queue_id=queue_id,
        state_id=state_id,
        priority_id=priority_id,
        owner_id=user_id,
        customer_user_id=customer_user_id,
        customer_id=customer_id,
        article=article,
    )

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                ticket_id = await svc.create_ticket(user_id, ticket_in)
            tn = (
                await session.execute(
                    text("SELECT tn FROM ticket WHERE id = :tid LIMIT 1"), {"tid": ticket_id}
                )
            ).scalar_one()
            return {"ticket_id": ticket_id, "ticket_number": tn}
        except TicketAccessDenied:
            return {"error": "Access denied — insufficient queue permissions"}
        except InvalidInput as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: ticket_reply
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Add a reply article to a ticket. Default channel is email (customer-visible); "
        "email replies require to_address (SMTP when outbound is enabled, else store-only)."
    )
)
async def ticket_reply(
    ctx: Context,
    ticket_id: int,
    body: str,
    subject: str = "Re:",
    is_visible_for_customer: bool = True,
    channel: str = "email",
    to_address: str | None = None,
) -> dict[str, Any]:
    """Add a reply to a ticket.

    Args:
        ticket_id: Target ticket ID.
        body: Reply body text.
        subject: Reply subject line.
        is_visible_for_customer: Default True (customer-visible reply).
        channel: Communication channel: 'email', 'phone', 'note'.
        to_address: Required for agent email channel (customer recipient).
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_reply")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                article_in = ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=is_visible_for_customer,
                    subject=subject,
                    body=body,
                    channel=channel,
                    to_address=to_address,
                )
                # TicketWriteService.add_article: agent email channel triggers SMTP
                # (REST parity — intentional product behaviour for MCP replies).
                article_id = await svc.add_article(user_id, ticket_id, article_in)
            return {"article_id": article_id, "ticket_id": ticket_id}
        except TicketNotFound:
            return {"error": f"Ticket #{ticket_id} not found"}
        except TicketAccessDenied:
            return {"error": "Access denied — insufficient queue permissions"}
        except InvalidInput as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: ticket_note
# ---------------------------------------------------------------------------


@mcp.tool(
    description=("Add an internal note to a ticket. Notes are NOT visible to customers by default.")
)
async def ticket_note(
    ctx: Context,
    ticket_id: int,
    body: str,
    subject: str = "Note",
    is_visible_for_customer: bool = False,
) -> dict[str, Any]:
    """Add an internal note to a ticket.

    Args:
        ticket_id: Target ticket ID.
        body: Note body text.
        subject: Note subject.
        is_visible_for_customer: Default False (internal note).
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_note")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                article_in = ArticleIn(
                    sender_type="agent",
                    is_visible_for_customer=is_visible_for_customer,
                    subject=subject,
                    body=body,
                    channel="note",
                )
                article_id = await svc.add_article(user_id, ticket_id, article_in)
            return {"article_id": article_id, "ticket_id": ticket_id}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: ticket_update_state
# ---------------------------------------------------------------------------


@mcp.tool(description="Change a ticket's state by state ID.")
async def ticket_update_state(
    ctx: Context,
    ticket_id: int,
    state_id: int,
) -> dict[str, Any]:
    """Change ticket state.

    Args:
        ticket_id: Target ticket ID.
        state_id: New state ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_update_state")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.change_state(user_id, ticket_id, state_id)
            return {"ok": True, "ticket_id": ticket_id, "state_id": state_id}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: ticket_update_queue
# ---------------------------------------------------------------------------


@mcp.tool(description="Move a ticket to a different queue by queue ID.")
async def ticket_update_queue(
    ctx: Context,
    ticket_id: int,
    queue_id: int,
) -> dict[str, Any]:
    """Move ticket to a new queue.

    Args:
        ticket_id: Target ticket ID.
        queue_id: Target queue ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_update_queue")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.move_queue(user_id, ticket_id, queue_id)
            return {"ok": True, "ticket_id": ticket_id, "queue_id": queue_id}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: ticket_update_priority
# ---------------------------------------------------------------------------


@mcp.tool(description="Change a ticket's priority by priority ID.")
async def ticket_update_priority(
    ctx: Context,
    ticket_id: int,
    priority_id: int,
) -> dict[str, Any]:
    """Change ticket priority.

    Args:
        ticket_id: Target ticket ID.
        priority_id: New priority ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_update_priority")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.change_priority(user_id, ticket_id, priority_id)
            return {"ok": True, "ticket_id": ticket_id, "priority_id": priority_id}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: ticket_update_owner
# ---------------------------------------------------------------------------


@mcp.tool(description="Assign ticket to a new owner agent by user ID.")
async def ticket_update_owner(
    ctx: Context,
    ticket_id: int,
    owner_id: int,
    lock: bool = True,
) -> dict[str, Any]:
    """Assign ticket owner.

    Args:
        ticket_id: Target ticket ID.
        owner_id: New owner's user ID.
        lock: Lock the ticket to the new owner (default True).
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_update_owner")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.assign_owner(user_id, ticket_id, owner_id, lock=lock)
            return {"ok": True, "ticket_id": ticket_id, "owner_id": owner_id}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: ticket_set_title / ticket_set_customer / ticket_set_dynamic_field
# ---------------------------------------------------------------------------


@mcp.tool(description="Change a ticket's title.")
async def ticket_set_title(
    ctx: Context,
    ticket_id: int,
    title: str,
) -> dict[str, Any]:
    """Change ticket title.

    Args:
        ticket_id: Target ticket ID.
        title: New title (truncated to 255 characters).
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_set_title")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.change_title(user_id, ticket_id, title)
            return {"ok": True, "ticket_id": ticket_id, "title": title[:255]}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Set a ticket's customer (customer_user_id and optional customer_id).")
async def ticket_set_customer(
    ctx: Context,
    ticket_id: int,
    customer_user_id: str,
    customer_id: str | None = None,
) -> dict[str, Any]:
    """Set ticket customer fields.

    Args:
        ticket_id: Target ticket ID.
        customer_user_id: Customer user login.
        customer_id: Optional customer company ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_set_customer")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.set_customer(
                    user_id,
                    ticket_id,
                    customer_id=customer_id,
                    customer_user_id=customer_user_id,
                )
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "customer_user_id": customer_user_id,
                "customer_id": customer_id,
            }
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(
    description=(
        "Set a ticket dynamic field value by field name. Returns an error if the "
        "field does not exist. Pass null value to clear the field."
    )
)
async def ticket_set_dynamic_field(
    ctx: Context,
    ticket_id: int,
    field_name: str,
    value: str | None = None,
) -> dict[str, Any]:
    """Set a dynamic field on a ticket.

    Args:
        ticket_id: Target ticket ID.
        field_name: Dynamic field name (not label).
        value: New value as a string, or null to clear.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_set_dynamic_field")
    values: list[str] = [] if value is None else [value]

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                field_id = (
                    await session.execute(
                        text(
                            "SELECT id FROM dynamic_field WHERE name = :n"
                            " AND object_type = 'Ticket' AND valid_id = 1 LIMIT 1"
                        ),
                        {"n": field_name},
                    )
                ).scalar_one_or_none()
                if field_id is None:
                    raise InvalidInput(f"Dynamic field {field_name!r} not found")
                svc = _write_service(session, state)
                await svc.update_dynamic_field(
                    user_id, ticket_id, field_name=field_name, values=values
                )
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "field_name": field_name,
                "value": value,
            }
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: ticket_lock / ticket_unlock
# ---------------------------------------------------------------------------


@mcp.tool(description="Lock a ticket (ticket_lock_id = lock).")
async def ticket_lock(
    ctx: Context,
    ticket_id: int,
) -> dict[str, Any]:
    """Lock a ticket.

    Args:
        ticket_id: Target ticket ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_lock")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.lock_ticket(user_id, ticket_id)
            return {"ok": True, "ticket_id": ticket_id, "lock": "lock"}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Unlock a ticket (ticket_lock_id = unlock).")
async def ticket_unlock(
    ctx: Context,
    ticket_id: int,
) -> dict[str, Any]:
    """Unlock a ticket.

    Args:
        ticket_id: Target ticket ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_unlock")

    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.unlock_ticket(user_id, ticket_id)
            return {"ok": True, "ticket_id": ticket_id, "lock": "unlock"}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Reference / discovery tools (parity with api/v1/reference.py)
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "List valid queues the agent may access. Use movable=true for queues "
        "the agent can move tickets into (rw). Default is queues with at least ro. "
        "Returns id, name, group_id."
    )
)
async def list_queues(
    ctx: Context,
    movable: bool = False,
) -> list[dict[str, Any]]:
    """Valid queues the current agent may access, filtered by permission.

    Args:
        movable: If true, only queues with ``rw``; otherwise queues with ``ro``.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    perm = "rw" if movable else "ro"

    async with state.session_factory() as session:
        pe = PermissionEngine(session)
        group_ids = await pe.groups_for_permission(user_id, perm)
        if not group_ids:
            return []
        rows = (
            await session.execute(
                select(Queue)
                .where(Queue.group_id.in_(group_ids), Queue.valid_id == _VALID)
                .order_by(Queue.name)
            )
        ).scalars()
        return [{"id": q.id, "name": q.name, "group_id": q.group_id} for q in rows]


@mcp.tool(description="List valid ticket states (id, name, type_name). Global reference data.")
async def list_states(ctx: Context) -> list[dict[str, Any]]:
    """Valid ticket states for update pickers.

    Args:
        (none — auth required)
    """
    _get_user_id(ctx)
    state = _get_state()

    async with state.session_factory() as session:
        rows = (
            await session.execute(
                select(TicketState.id, TicketState.name, TicketStateType.name)
                .join(TicketStateType, TicketState.type_id == TicketStateType.id)
                .where(TicketState.valid_id == _VALID)
                .order_by(TicketState.id)
            )
        ).all()
        return [{"id": r[0], "name": r[1], "type_name": r[2]} for r in rows]


@mcp.tool(description="List valid ticket priorities (id, name). Global reference data.")
async def list_priorities(ctx: Context) -> list[dict[str, Any]]:
    """Valid priorities for update pickers.

    Args:
        (none — auth required)
    """
    _get_user_id(ctx)
    state = _get_state()

    async with state.session_factory() as session:
        rows = (
            await session.execute(
                select(TicketPriority)
                .where(TicketPriority.valid_id == _VALID)
                .order_by(TicketPriority.id)
            )
        ).scalars()
        return [{"id": p.id, "name": p.name} for p in rows]


@mcp.tool(
    description=(
        "List valid agent users for owner/responsible assignment "
        "(id, login, full_name). Global list of valid users."
    )
)
async def list_agents(ctx: Context) -> list[dict[str, Any]]:
    """Valid agents for owner/responsible pickers.

    Args:
        (none — auth required)
    """
    _get_user_id(ctx)
    state = _get_state()

    async with state.session_factory() as session:
        rows = (
            await session.execute(
                select(Users).where(Users.valid_id == _VALID).order_by(Users.login)
            )
        ).scalars()
        return [
            {
                "id": u.id,
                "login": u.login,
                "full_name": f"{u.first_name} {u.last_name}".strip(),
            }
            for u in rows
        ]


# ---------------------------------------------------------------------------
# Tool: kb_search / kb_get_article
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Search the knowledge base for articles relevant to a query. Results are "
        "scoped to the agent's permission groups (articles with no group restriction "
        "are visible to everyone). Returns matching chunks with heading breadcrumbs."
    )
)
async def kb_search(
    ctx: Context,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search published KB articles.

    Args:
        query: Full-text search query.
        limit: Maximum results (1-100).
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    limit = max(1, min(limit, 100))

    async with state.session_factory() as session:
        svc = KbService(session, state.settings)
        try:
            result = await svc.search_agent(user_id, query, limit=limit)
        finally:
            await svc.close()

    return [
        {
            "article_id": h.article_id,
            "chunk_id": h.chunk_id,
            "title": h.title,
            "heading_path": h.heading_path,
            "anchor": h.anchor,
            "content": h.content,
            "language": h.language,
        }
        for h in result.hits
    ]


@mcp.tool(description="Get a knowledge base article's full Markdown content by article ID.")
async def kb_get_article(
    ctx: Context,
    article_id: int,
) -> dict[str, Any]:
    """Fetch a KB article (permission-group scoped to the caller).

    Args:
        article_id: The KB article ID (as returned by ``kb_search``).
    """
    user_id = _get_user_id(ctx)
    state = _get_state()

    async with state.session_factory() as session:
        svc = KbService(session, state.settings)
        try:
            row = await svc.get_article_scoped(user_id, article_id)
        except KbNotFound:
            return {"error": f"KB article {article_id} not found"}
        except KbForbidden:
            return {"error": f"KB article {article_id} is not readable by this agent"}
        tags = await svc.get_tags(article_id)

    return {
        "id": row.id,
        "category_id": row.category_id,
        "title": row.title,
        "slug": row.slug,
        "language": row.language,
        "state": row.state,
        "content_md": row.content_md,
        "version": row.version,
        "tags": tags,
    }


@mcp.tool(
    description=(
        "List knowledge base articles filtered by tag and/or category, scoped to "
        "the agent's permission groups. Use this to gather a knowledge set (e.g. all "
        "articles tagged 'billing') for grounding. Returns article metadata; call "
        "kb_get_article for full content."
    )
)
async def kb_list(
    ctx: Context,
    tag: str | None = None,
    category_id: int | None = None,
    state: str | None = "published",
) -> list[dict[str, Any]]:
    """List readable KB articles by tag/category.

    Args:
        tag: Restrict to articles carrying this tag name.
        category_id: Restrict to this category.
        state: Lifecycle state filter (default ``published``; pass null for any).
    """
    user_id = _get_user_id(ctx)
    state_obj = _get_state()

    async with state_obj.session_factory() as session:
        svc = KbService(session, state_obj.settings)
        rows = await svc.list_articles(
            category_id=category_id, state=state, tag=tag, user_id=user_id
        )
        return [
            {
                "id": r.id,
                "category_id": r.category_id,
                "title": r.title,
                "slug": r.slug,
                "language": r.language,
                "state": r.state,
                "version": r.version,
            }
            for r in rows
        ]


@mcp.tool(
    description=(
        "Create or update a knowledge base article. Omit article_id to create; "
        "provide it to update. Content is Markdown. Newly created articles start as "
        "drafts — call kb_publish_article to index them for search."
    )
)
async def kb_upsert_article(
    ctx: Context,
    title: str,
    content_md: str,
    category_id: int | None = None,
    article_id: int | None = None,
    language: str = "en",
    tags: list[str] | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """Create or update a KB article.

    Args:
        title: Article title.
        content_md: Markdown body.
        category_id: Category id (required when creating).
        article_id: Existing article id to update; omit to create.
        language: Language code (default ``en``).
        tags: Tag names to set on the article.
        state: Lifecycle state (draft/review/published/archived).
    """
    user_id = _get_user_id(ctx)
    try:
        _assert_mcp_write_scope()
    except TicketAccessDenied as exc:
        return {"error": str(exc)}
    state_obj = _get_state()

    async with state_obj.session_factory() as session:
        svc = KbService(session, state_obj.settings)
        try:
            async with session.begin():
                if article_id is None:
                    if category_id is None:
                        return {"error": "category_id is required when creating an article"}
                    row = await svc.create_article(
                        user_id,
                        KbArticleIn(
                            category_id=category_id,
                            title=title,
                            content_md=content_md,
                            language=language,
                            state=state,
                            tags=tags or [],
                        ),
                    )
                    new_id = row.id
                else:
                    await svc.get_article_scoped(user_id, article_id)
                    await svc.update_article(
                        user_id,
                        article_id,
                        KbArticleUpdateIn(
                            title=title,
                            content_md=content_md,
                            category_id=category_id,
                            language=language,
                            state=state,
                            tags=tags,
                        ),
                    )
                    new_id = article_id
            row = await svc.get_article(new_id)
            article_tags = await svc.get_tags(new_id)
        except KbNotFound:
            return {"error": f"KB article {article_id} not found"}
        except KbForbidden:
            return {"error": f"KB article {article_id} is not editable by this agent"}
        except ValueError as exc:
            return {"error": str(exc)}

    return {
        "id": row.id,
        "category_id": row.category_id,
        "title": row.title,
        "slug": row.slug,
        "language": row.language,
        "state": row.state,
        "version": row.version,
        "tags": article_tags,
    }


@mcp.tool(
    description=(
        "Publish a knowledge base article: mark it published and (re)index it for "
        "search. Call after kb_upsert_article to make an article searchable."
    )
)
async def kb_publish_article(
    ctx: Context,
    article_id: int,
) -> dict[str, Any]:
    """Publish + index a KB article.

    Args:
        article_id: The KB article id to publish.
    """
    user_id = _get_user_id(ctx)
    try:
        _assert_mcp_write_scope()
    except TicketAccessDenied as exc:
        return {"error": str(exc)}
    state_obj = _get_state()

    async with state_obj.session_factory() as session:
        svc = KbService(session, state_obj.settings)
        try:
            async with session.begin():
                await svc.get_article_scoped(user_id, article_id)
                row = await svc.publish(user_id, article_id)
            result = {"id": row.id, "state": row.state, "version": row.version}
        except KbNotFound:
            return {"error": f"KB article {article_id} not found"}
        except KbForbidden:
            return {"error": f"KB article {article_id} is not editable by this agent"}
        finally:
            await svc.close()

    return result


# ---------------------------------------------------------------------------
# Tool: type / service / SLA / history / merge / link
# ---------------------------------------------------------------------------


@mcp.tool(description="Change a ticket's type by type ID.")
async def ticket_set_type(ctx: Context, ticket_id: int, type_id: int) -> dict[str, Any]:
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_set_type")
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.change_type(user_id, ticket_id, type_id)
            return {"ok": True, "ticket_id": ticket_id, "type_id": type_id}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Change a ticket's service by service ID (0 or omit to clear).")
async def ticket_set_service(
    ctx: Context, ticket_id: int, service_id: int | None = None
) -> dict[str, Any]:
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_set_service")
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.change_service(user_id, ticket_id, service_id if service_id else None)
            return {"ok": True, "ticket_id": ticket_id, "service_id": service_id}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Change a ticket's SLA by SLA ID (0 or omit to clear).")
async def ticket_set_sla(ctx: Context, ticket_id: int, sla_id: int | None = None) -> dict[str, Any]:
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_set_sla")
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.change_sla(user_id, ticket_id, sla_id if sla_id else None)
            return {"ok": True, "ticket_id": ticket_id, "sla_id": sla_id}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(
    description=("Return recent ticket history entries as a list of {type, name, create_time}.")
)
async def ticket_history(
    ctx: Context, ticket_id: int, limit: int = 30
) -> dict[str, Any] | list[dict[str, Any]]:
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_history")
    async with state.session_factory() as session:
        try:
            await _assert_queue_permission(session, ticket_id=ticket_id, user_id=user_id, key="ro")
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT tht.name AS history_type, th.name, th.create_time"
                            " FROM ticket_history th"
                            " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                            " WHERE th.ticket_id = :tid"
                            " ORDER BY th.id DESC LIMIT :lim"
                        ),
                        {"tid": ticket_id, "lim": max(1, min(limit, 200))},
                    )
                )
                .mappings()
                .all()
            )
            return [
                {
                    "history_type": r["history_type"],
                    "name": r["name"],
                    "create_time": str(r["create_time"]) if r["create_time"] else None,
                }
                for r in rows
            ]
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Merge merge_ticket_id into main_ticket_id (Znuny TicketMerge).")
async def ticket_merge(ctx: Context, main_ticket_id: int, merge_ticket_id: int) -> dict[str, Any]:
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_merge")
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.merge_tickets(user_id, main_ticket_id, merge_ticket_id)
            return {
                "ok": True,
                "main_ticket_id": main_ticket_id,
                "merge_ticket_id": merge_ticket_id,
            }
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Link two tickets (default type Normal).")
async def ticket_link(
    ctx: Context,
    ticket_id: int,
    target_ticket_id: int,
    link_type: str = "Normal",
) -> dict[str, Any]:
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_link")
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.link_tickets(user_id, ticket_id, target_ticket_id, link_type=link_type)
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "target_ticket_id": target_ticket_id,
                "link_type": link_type,
            }
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: responsible / watch / archive / attachments / forward / bounce
# ---------------------------------------------------------------------------


@mcp.tool(description="Assign a ticket's responsible agent by user ID.")
async def ticket_set_responsible(
    ctx: Context,
    ticket_id: int,
    responsible_id: int,
) -> dict[str, Any]:
    """Assign ticket responsible (Znuny TicketResponsibleSet / owner-key gate).

    Args:
        ticket_id: Target ticket ID.
        responsible_id: Agent user ID to set as responsible.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_set_responsible")
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.assign_responsible(user_id, ticket_id, responsible_id)
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "responsible_id": responsible_id,
            }
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Watch (subscribe to) a ticket. Defaults to the authenticated agent.")
async def ticket_watch(
    ctx: Context,
    ticket_id: int,
    watcher_user_id: int | None = None,
) -> dict[str, Any]:
    """Subscribe a user as ticket watcher. Requires ``ro`` on the ticket's queue.

    Args:
        ticket_id: Target ticket ID.
        watcher_user_id: Optional agent to subscribe; defaults to the caller.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_watch")
    watcher = watcher_user_id if watcher_user_id is not None else user_id
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                await _assert_queue_permission(
                    session, ticket_id=ticket_id, user_id=user_id, key="ro"
                )
                await watch_ticket(
                    session,
                    ticket_id=ticket_id,
                    watcher_user_id=watcher,
                    user_id=user_id,
                )
            return {"ok": True, "ticket_id": ticket_id, "watcher_user_id": watcher}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Stop watching (unsubscribe from) a ticket.")
async def ticket_unwatch(
    ctx: Context,
    ticket_id: int,
    watcher_user_id: int | None = None,
) -> dict[str, Any]:
    """Unsubscribe a user from a ticket. Requires ``ro`` on the ticket's queue.

    Args:
        ticket_id: Target ticket ID.
        watcher_user_id: Optional agent to unsubscribe; defaults to the caller.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_unwatch")
    watcher = watcher_user_id if watcher_user_id is not None else user_id
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                await _assert_queue_permission(
                    session, ticket_id=ticket_id, user_id=user_id, key="ro"
                )
                await unwatch_ticket(
                    session,
                    ticket_id=ticket_id,
                    watcher_user_id=watcher,
                    user_id=user_id,
                )
            return {"ok": True, "ticket_id": ticket_id, "watcher_user_id": watcher}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Archive a ticket (archive_flag = 1).")
async def ticket_archive(ctx: Context, ticket_id: int) -> dict[str, Any]:
    """Archive a ticket. Requires ``rw`` on the ticket's queue.

    Args:
        ticket_id: Target ticket ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_archive")
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.archive_ticket(user_id, ticket_id, True)
            return {"ok": True, "ticket_id": ticket_id, "archive": True}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(description="Unarchive a ticket (archive_flag = 0).")
async def ticket_unarchive(ctx: Context, ticket_id: int) -> dict[str, Any]:
    """Unarchive a ticket. Requires ``rw`` on the ticket's queue.

    Args:
        ticket_id: Target ticket ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_unarchive")
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                await svc.archive_ticket(user_id, ticket_id, False)
            return {"ok": True, "ticket_id": ticket_id, "archive": False}
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(
    description=(
        "List attachment metadata for a ticket article (id, filename, content_type, "
        "content_size, disposition). Requires ``ro`` on the ticket's queue."
    )
)
async def list_attachments(
    ctx: Context,
    ticket_id: int,
    article_id: int,
) -> dict[str, Any] | list[dict[str, Any]]:
    """List non-body-part attachments on an article.

    Args:
        ticket_id: Ticket ID that owns the article.
        article_id: Article ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("list_attachments")
    async with state.session_factory() as session:
        try:
            await _assert_queue_permission(session, ticket_id=ticket_id, user_id=user_id, key="ro")
            atts = await TicketService(session).list_attachments(user_id, ticket_id, article_id)
            return [
                {
                    "id": a.id,
                    "article_id": a.article_id,
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "content_size": a.content_size,
                    "content_id": a.content_id,
                    "disposition": a.disposition,
                    "inline": a.inline,
                }
                for a in atts
            ]
        except (
            TicketNotFound,
            TicketAccessDenied,
            InvalidInput,
            ReadNotFound,
            ReadAccessDenied,
        ) as e:
            return {"error": str(e)}


@mcp.tool(
    description=(
        "Get metadata for one attachment by id within a ticket article. "
        "Does not return binary content."
    )
)
async def get_attachment_meta(
    ctx: Context,
    ticket_id: int,
    article_id: int,
    attachment_id: int,
) -> dict[str, Any]:
    """Return one attachment's metadata, or an error if not found.

    Args:
        ticket_id: Ticket ID that owns the article.
        article_id: Article ID.
        attachment_id: Attachment row ID.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("get_attachment_meta")
    async with state.session_factory() as session:
        try:
            await _assert_queue_permission(session, ticket_id=ticket_id, user_id=user_id, key="ro")
            atts = await TicketService(session).list_attachments(user_id, ticket_id, article_id)
            for a in atts:
                if a.id == attachment_id:
                    return {
                        "id": a.id,
                        "article_id": a.article_id,
                        "filename": a.filename,
                        "content_type": a.content_type,
                        "content_size": a.content_size,
                        "content_id": a.content_id,
                        "disposition": a.disposition,
                        "inline": a.inline,
                    }
            return {"error": f"Attachment {attachment_id} not found on article {article_id}"}
        except (
            TicketNotFound,
            TicketAccessDenied,
            InvalidInput,
            ReadNotFound,
            ReadAccessDenied,
        ) as e:
            return {"error": str(e)}


@mcp.tool(
    description=(
        "Forward an article by email: create a customer-visible email article "
        "with history type Forward. Requires to_address and rw on the queue."
    )
)
async def ticket_forward(
    ctx: Context,
    ticket_id: int,
    to_address: str,
    body: str,
    subject: str | None = None,
    cc: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Forward ticket content to a new recipient.

    Args:
        ticket_id: Source ticket ID.
        to_address: Recipient email address (required).
        body: Forward body (typically the original article text).
        subject: Optional subject (default ``Fwd:``).
        cc: Optional CC addresses.
        note: Optional note prepended to the body.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_forward")
    if not to_address or not str(to_address).strip():
        return {"error": "to_address is required"}
    fwd_body = f"{note}\n\n{body}" if note else body
    subj = subject or "Fwd:"
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                article_id = await svc.forward_article(
                    user_id,
                    ticket_id,
                    subject=subj,
                    body=fwd_body,
                    to_address=str(to_address).strip(),
                    cc=cc,
                )
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "article_id": article_id,
                "to_address": str(to_address).strip(),
            }
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


@mcp.tool(
    description=(
        "Bounce (resend verbatim) an existing article to a new recipient. "
        "History type Bounce. Requires to_address and rw on the queue."
    )
)
async def ticket_bounce(
    ctx: Context,
    ticket_id: int,
    article_id: int,
    to_address: str,
    state_id: int | None = None,
) -> dict[str, Any]:
    """Resend an article's body verbatim to ``to_address``.

    Args:
        ticket_id: Ticket that owns the article.
        article_id: Source article to bounce.
        to_address: Recipient email address (required).
        state_id: Optional new ticket state after bounce.
    """
    user_id = _get_user_id(ctx)
    state = _get_state()
    _assert_tool_allowed("ticket_bounce")
    if not to_address or not str(to_address).strip():
        return {"error": "to_address is required"}
    async with state.session_factory() as session:
        try:
            async with session.begin():
                _assert_mcp_write_scope()
                svc = _write_service(session, state)
                new_aid = await svc.bounce_article(
                    user_id,
                    ticket_id,
                    article_id,
                    to_address=str(to_address).strip(),
                    state_id=state_id,
                )
            return {
                "ok": True,
                "ticket_id": ticket_id,
                "source_article_id": article_id,
                "article_id": new_aid,
                "to_address": str(to_address).strip(),
            }
        except (TicketNotFound, TicketAccessDenied, InvalidInput) as e:
            return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool: customer_lookup
# ---------------------------------------------------------------------------


@mcp.tool(description="Look up a customer user by login to get their details.")
async def customer_lookup(
    ctx: Context,
    customer_login: str,
) -> dict[str, Any]:
    """Look up a customer user.

    Args:
        customer_login: Customer user login (username).
    """
    _get_user_id(ctx)  # auth check
    state = _get_state()

    async with state.session_factory() as session:
        row = (
            await session.execute(
                select(CustomerUser).where(
                    CustomerUser.login == customer_login,
                    CustomerUser.valid_id == 1,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            return {"error": f"Customer user {customer_login!r} not found"}

        return {
            "login": row.login,
            "email": row.email,
            "first_name": row.first_name,
            "last_name": row.last_name,
            "customer_id": row.customer_id,
            "phone": row.phone,
            "city": row.city,
            "country": row.country,
        }


# ---------------------------------------------------------------------------
# App factory and lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _app_lifespan(app: Any) -> AsyncIterator[None]:
    """Initialise McpState on startup, dispose on shutdown."""
    global _mcp_state
    settings = get_settings()
    _mcp_state = McpState(settings)
    logger.info("tiqora_mcp_started", database=settings.database_url[:30])
    try:
        yield
    finally:
        await _mcp_state.aclose()
        _mcp_state = None
        logger.info("tiqora_mcp_stopped")


def create_mcp_http_app() -> Any:
    """Build the FastMCP HTTP ASGI app with auth middleware and lifespan."""
    return mcp.http_app(
        transport="streamable-http",
        middleware=[Middleware(TiqoraBearerAuth)],
    )


def run_mcp_server(host: str = "0.0.0.0", port: int = 8001) -> None:
    """Start the standalone MCP server process."""
    import uvicorn
    from starlette.applications import Starlette

    http_app = create_mcp_http_app()

    @asynccontextmanager
    async def combined_lifespan(app_: Any) -> AsyncIterator[None]:
        async with _app_lifespan(app_), http_app.lifespan(http_app):
            yield

    app = Starlette(routes=[], lifespan=combined_lifespan)
    app.mount("/mcp", http_app)

    uvicorn.run(app, host=host, port=port, log_level="info")


__all__ = [
    "create_mcp_http_app",
    "mcp",
    "run_mcp_server",
]
