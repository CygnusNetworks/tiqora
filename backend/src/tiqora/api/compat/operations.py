"""Znuny GenericInterface-compatible operation handlers.

Translates Znuny REST GenericInterface wire format to Tiqora domain services.
Ports request/response semantics from:
  - Kernel/GenericInterface/Operation/Ticket/{TicketCreate,TicketUpdate,TicketGet,TicketSearch}.pm
  - Kernel/GenericInterface/Operation/Session/SessionCreate.pm
  - Kernel/GenericInterface/Operation/Common.pm
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.compat.article_input import (
    GiArticle,
    GiDynamicFields,
    article_id_result,
    normalize_article_list,
    parse_gi_articles,
    parse_gi_dynamic_fields,
)
from tiqora.api.compat.history_at import queues_at
from tiqora.api.compat.mapping import MappingError
from tiqora.api.compat.mapping.out_of_office import OutOfOfficeMapper
from tiqora.api.compat.mapping.perl import perl_is_string, perl_str, perl_truthy
from tiqora.api.compat.provider import OperationFault
from tiqora.api.compat.search_filters import (
    article_text_filters,
    create_time_settings,
    created_filters,
    sanitize_id_filters,
    time_filters,
)
from tiqora.api.compat.ticket_get import (
    build_article_entries,
    build_ticket_entry,
    load_ticket_get_context,
    znuny_num,
    znuny_ts,
)
from tiqora.api.compat.webservices import OperationConfig
from tiqora.api.compat.znuny_session import (
    session_get_key_allowed,
    write_znuny_session,
    znuny_agent_user_data,
    znuny_customer_user_data,
    znuny_session_writes_enabled,
)
from tiqora.channels.email.outbound_reply import OutboundMailError as _OutboundMailError
from tiqora.channels.email.outbound_reply import (
    store_agent_email_for_dispatch,
)
from tiqora.config import Settings, get_settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketPriority,
    TicketState,
    TicketStateType,
)
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraUserPasskey
from tiqora.domain.auth import AuthenticatedUser, SessionStore
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.dynamic_field_values import (
    set_article_dynamic_field,
    set_ticket_dynamic_field,
)
from tiqora.domain.portal_ticket_service import (
    PORTAL_SYSTEM_USER_ID,
    customer_can_access_ticket,
    customer_ticket_scope_filter,
)
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TicketAccessDenied,
    TicketIn,
    TicketNotFound,
    add_article,
    add_time_accounting,
    assign_owner,
    assign_responsible,
    change_priority,
    change_state,
    change_title,
    create_ticket,
    lock_ticket,
    move_queue,
    set_customer,
    set_ticket_lock,
    unlock_ticket,
)
from tiqora.domain.totp import TOTPService
from tiqora.permissions.engine import PermissionEngine
from tiqora.security.ratelimit import AuthRateLimiter, client_ip
from tiqora.znuny.cache_invalidation import invalidate_cache_type
from tiqora.znuny.escalation import escalation_index_build
from tiqora.znuny.history import (
    add_service_update,
    add_sla_update,
    add_type_update,
)
from tiqora.znuny.password import (
    hash_password,
    is_weak_scheme,
    needs_rehash,
    verify_password,
)
from tiqora.znuny.perl_format import perl_int
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Error helpers (Znuny wire format)
# ---------------------------------------------------------------------------


def _err(code: str, message: str) -> dict[str, Any]:
    """Return a Znuny-style GenericInterface error envelope."""
    return {"Error": {"ErrorCode": code, "ErrorMessage": message}}


# ---------------------------------------------------------------------------
# Auth helpers (shared by all operations)
# ---------------------------------------------------------------------------

_CUSTOMER_USER_TYPE = "Customer"
_AGENT_USER_TYPE = "User"
# Sentinel agent id for customer principals — never use root/agent id 1.
_CUSTOMER_SENTINEL_USER_ID = 0

_2FA_API_KEY_MSG = (
    "Agent has 2FA enabled or enforced; use a tiqora_* API key instead of password/SessionCreate"
)


async def _agent_password_blocked_by_2fa(session: AsyncSession, user_id: int) -> bool:
    """True when password/SessionCreate must be rejected (2FA enrolled or enforced)."""
    from tiqora.config import get_settings

    settings = get_settings()
    totp = TOTPService(session, settings)
    if await totp.is_enabled(user_id):
        return True
    has_passkey = (
        await session.execute(
            select(TiqoraUserPasskey.id).where(TiqoraUserPasskey.user_id == user_id).limit(1)
        )
    ).first()
    if has_passkey is not None:
        return True
    return await AuthConfigService(session).effective_enforce(user_id)


async def _load_customer_company_id(session: AsyncSession, login: str) -> str:
    row = (
        await session.execute(
            select(CustomerUser.customer_id).where(
                CustomerUser.login == login,
                CustomerUser.valid_id == 1,
            )
        )
    ).scalar_one_or_none()
    return str(row) if row is not None else ""


def _compat_rate_limited_err(op_prefix: str, retry_after: int) -> dict[str, Any]:
    err = _err(
        f"{op_prefix}.AuthFail",
        "Too many failed login attempts; try again later",
    )
    # Surface Retry-After for the HTTP layer (router maps AuthFail → 401; we
    # use a dedicated code so the router can emit 429 when present).
    err["Error"]["ErrorCode"] = f"{op_prefix}.RateLimited"
    err["Error"]["RetryAfter"] = max(1, int(retry_after))
    return err


async def _maybe_rehash_agent_pw(
    session: AsyncSession,
    row: Users,
    password: str,
    settings: Settings,
) -> None:
    if not settings.password_rehash_on_login:
        return
    stored = row.pw or ""
    if not needs_rehash(stored):
        return
    row.pw = hash_password(password)
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()


async def _maybe_rehash_customer_pw(
    session: AsyncSession,
    row: CustomerUser,
    password: str,
    settings: Settings,
) -> None:
    if not settings.password_rehash_on_login:
        return
    stored = row.pw or ""
    if not needs_rehash(stored):
        return
    row.pw = hash_password(password)
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()


async def _auth_from_params(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> tuple[int, str, str] | dict[str, Any]:
    """Resolve (user_id, login, user_type) from GenericInterface auth params.

    Returns an error dict on failure.  Accepts:
    - SessionID: validated against the Znuny `sessions` key-value table
    - UserLogin + Password: agent login (rejected when 2FA enabled/enforced)
    - CustomerUserLogin + Password: customer login (ownership-scoped; never agent id)

    Customer principals always return ``user_id=0`` and ``user_type=Customer``.
    They must never be elevated to the root agent (id 1).
    """
    op_prefix = "Auth"
    cfg = settings or get_settings()

    session_id = (data.get("SessionID") or "").strip()
    if session_id:
        # Validate against Znuny sessions table (key-value: UserID, UserLogin,
        # UserType) — including the idle/absolute expiry and remote-IP binding
        # Znuny's own CheckSessionID enforces.
        result = await _lookup_session(
            session, session_id, remote_addr=_peer_addr(request), settings=cfg
        )
        if result is not None:
            return result
        # Fall back to Tiqora's own session store: a SessionID issued by the
        # compat SessionCreate op must round-trip for subsequent compat calls
        # (golden-master finding — Znuny's SessionCreate token is always
        # usable for follow-up requests). Customer sessions are stored with
        # user_id=0; keep that sentinel — never rewrite 0→1 (C-1).
        session_payload = await session_store.get(session_id)
        if session_payload is not None:
            stored_user_id, stored_login = session_payload
            if stored_user_id == _CUSTOMER_SENTINEL_USER_ID:
                return (_CUSTOMER_SENTINEL_USER_ID, stored_login, _CUSTOMER_USER_TYPE)
            return (stored_user_id, stored_login, _AGENT_USER_TYPE)
        return _err(f"{op_prefix}.AuthFail", "Session invalid or expired")

    user_login = (data.get("UserLogin") or "").strip()
    customer_login = (data.get("CustomerUserLogin") or "").strip()
    password = (data.get("Password") or "").strip()

    limiter: AuthRateLimiter | None = None
    ip = "unknown"
    rate_login = user_login or customer_login
    if request is not None and rate_login:
        redis_client = getattr(getattr(request, "app", None), "state", None)
        redis_client = getattr(redis_client, "redis", None) if redis_client else None
        if redis_client is not None:
            limiter = AuthRateLimiter(redis_client, cfg)
            ip = client_ip(request)
            pre = await limiter.check(login=rate_login, ip=ip)
            if not pre.allowed:
                return _compat_rate_limited_err(op_prefix, pre.retry_after)

    if user_login:
        row = (
            await session.execute(
                select(Users).where(Users.login == user_login, Users.valid_id == 1)
            )
        ).scalar_one_or_none()
        pw_hash = (row.pw or "") if row is not None else ""
        if row is not None and cfg.password_reject_weak_hashes and is_weak_scheme(pw_hash):
            row = None
        if row is None or not verify_password(password, pw_hash):
            if limiter is not None:
                locked = await limiter.record_failure(login=user_login, ip=ip)
                if locked is not None:
                    return _compat_rate_limited_err(op_prefix, locked.retry_after)
            return _err(f"{op_prefix}.AuthFail", "UserLogin or Password is invalid!")
        if await _agent_password_blocked_by_2fa(session, row.id):
            return _err(f"{op_prefix}.AuthFail", _2FA_API_KEY_MSG)
        if limiter is not None:
            await limiter.reset(login=user_login, ip=ip)
        await _maybe_rehash_agent_pw(session, row, password, cfg)
        return (row.id, row.login, _AGENT_USER_TYPE)

    if customer_login:
        row2 = (
            await session.execute(
                select(CustomerUser).where(
                    CustomerUser.login == customer_login,
                    CustomerUser.valid_id == 1,
                )
            )
        ).scalar_one_or_none()
        pw_hash2 = (row2.pw or "") if row2 is not None else ""
        if row2 is not None and cfg.password_reject_weak_hashes and is_weak_scheme(pw_hash2):
            row2 = None
        if row2 is None or not verify_password(password, pw_hash2):
            if limiter is not None:
                locked = await limiter.record_failure(login=customer_login, ip=ip)
                if locked is not None:
                    return _compat_rate_limited_err(op_prefix, locked.retry_after)
            return _err(f"{op_prefix}.AuthFail", "CustomerUserLogin or Password is invalid!")
        if limiter is not None:
            await limiter.reset(login=customer_login, ip=ip)
        await _maybe_rehash_customer_pw(session, row2, password, cfg)
        # Never map customers onto an agent id (was root user_id=1 — C-1).
        return (_CUSTOMER_SENTINEL_USER_ID, customer_login, _CUSTOMER_USER_TYPE)

    return _err(f"{op_prefix}.AuthFail", "No UserLogin, CustomerUserLogin, or SessionID provided!")


def _peer_addr(request: Any | None) -> str | None:
    """Client address for the Znuny remote-IP session binding.

    ``client_ip`` falls back to the literal ``"unknown"`` when no peer can be
    determined; that must not be compared against a stored address or every
    session would fail the binding check. Return ``None`` instead so the caller
    skips the comparison.
    """
    if request is None:
        return None
    addr = client_ip(request)
    return addr if addr and addr != "unknown" else None


def _session_epoch(raw: str | None) -> int | None:
    """Parse a Znuny session timestamp (epoch seconds) from the kv table."""
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _znuny_session_expired(
    data: dict[str, str],
    *,
    now: int,
    max_idle_time: int,
    max_time: int,
) -> bool:
    """Whether Znuny's ``CheckSessionID`` would refuse this session.

    Ports the two time gates of ``Kernel::System::AuthSession::DB::CheckSessionID``
    (Znuny 6.x). Rows survive in the ``sessions`` table until
    ``SessionDeleteIfTimeToOld`` fires on a Znuny-side request or the cleanup
    daemon runs, so the mere presence of a row proves nothing — without these
    checks an abandoned session id stayed valid in Tiqora indefinitely.

    A session whose timestamps are missing or unparseable is treated as expired:
    a row we cannot date is a row we cannot vouch for.
    """
    last_request = _session_epoch(data.get("UserLastRequest"))
    if last_request is None or (now - max_idle_time) >= last_request:
        return True
    session_start = _session_epoch(data.get("UserSessionStart"))
    return session_start is None or (now - max_time) >= session_start


async def _lookup_session(
    session: AsyncSession,
    session_id: str,
    *,
    sysconfig: SysConfig | None = None,
    remote_addr: str | None = None,
    settings: Settings | None = None,
) -> tuple[int, str, str] | None:
    """Look up a Znuny session row in the `sessions` key-value table.

    The table has: session_id, data_key, data_value
    We need UserID, UserLogin, UserType, and the two lifetime stamps.

    Idle/absolute expiry and the optional remote-IP binding are enforced here
    exactly as Znuny's own ``CheckSessionID`` does — see
    :func:`_znuny_session_expired`.
    """
    rows = (
        await session.execute(
            text(
                "SELECT data_key, data_value FROM sessions"
                " WHERE session_id = :sid"
                "  AND data_key IN ('UserID', 'UserLogin', 'UserType',"
                "                   'UserLastRequest', 'UserSessionStart',"
                "                   'UserRemoteAddr')"
            ),
            {"sid": session_id},
        )
    ).fetchall()
    if not rows:
        return None
    data: dict[str, str] = {str(r[0]): str(r[1]) for r in rows}
    user_id_s = data.get("UserID")
    user_login = data.get("UserLogin")
    user_type = data.get("UserType", _AGENT_USER_TYPE)
    if not user_login:
        return None

    cfg = sysconfig if sysconfig is not None else SysConfig(session)
    if _znuny_session_expired(
        data,
        now=int(datetime.now(UTC).timestamp()),
        max_idle_time=await cfg.session_max_idle_time(),
        max_time=await cfg.session_max_time(),
    ):
        logger.info("compat_session_expired", session_id_prefix=session_id[:8])
        return None

    # Remote-IP binding (Znuny SessionCheckRemoteIP). Opt-in via
    # TIQORA_COMPAT_SESSION_CHECK_REMOTE_IP rather than read from the Znuny
    # SysConfig: Znuny recorded the address *its* webserver saw, which differs
    # from Tiqora's socket peer in most reverse-proxy setups, so honouring the
    # Znuny value would reject valid sessions. Also skipped when no peer address
    # is available, so internal dispatch is not locked out by a missing value.
    app_cfg = settings if settings is not None else get_settings()
    if remote_addr and app_cfg.compat_session_check_remote_ip:
        bound_addr = data.get("UserRemoteAddr")
        if bound_addr and bound_addr != remote_addr:
            logger.warning(
                "compat_session_remote_ip_mismatch",
                session_id_prefix=session_id[:8],
                expected=bound_addr,
                actual=remote_addr,
            )
            return None
    # Customer sessions: never treat UserID as an agent principal for ACL.
    if user_type == _CUSTOMER_USER_TYPE:
        if not await _principal_is_valid(session, user_login, customer=True):
            return None
        return (_CUSTOMER_SENTINEL_USER_ID, user_login, _CUSTOMER_USER_TYPE)
    if not user_id_s:
        return None
    try:
        agent_id = int(user_id_s)
    except ValueError:
        return None
    if not await _principal_is_valid(session, agent_id, customer=False):
        return None
    return (agent_id, user_login, user_type or _AGENT_USER_TYPE)


async def _principal_is_valid(session: AsyncSession, who: int | str, *, customer: bool) -> bool:
    """A disabled account must not keep working through a still-live session row."""
    if customer:
        stmt = select(CustomerUser.valid_id).where(CustomerUser.login == who)
    else:
        stmt = select(Users.valid_id).where(Users.id == who)
    valid_id = (await session.execute(stmt)).scalars().first()
    return valid_id == 1


# ---------------------------------------------------------------------------
# Lookup helpers (name→ID resolution)
# ---------------------------------------------------------------------------


async def _resolve_queue_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    row = (
        await session.execute(select(Queue.id).where(Queue.name == name, Queue.valid_id == 1))
    ).scalar_one_or_none()
    return row


async def _resolve_state_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    row = (
        await session.execute(select(TicketState.id).where(TicketState.name == name))
    ).scalar_one_or_none()
    return row


async def _resolve_priority_id(
    session: AsyncSession, name: str | None, id_: int | None
) -> int | None:
    if id_ is not None:
        return id_
    if not name:
        return None
    row = (
        await session.execute(select(TicketPriority.id).where(TicketPriority.name == name))
    ).scalar_one_or_none()
    return row


async def _resolve_user_id(session: AsyncSession, login: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return id_
    if not login:
        return None
    row = (
        await session.execute(select(Users.id).where(Users.login == login, Users.valid_id == 1))
    ).scalar_one_or_none()
    return row


async def _resolve_lock_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return int(id_)
    if not name:
        return None
    row = (
        await session.execute(
            text("SELECT id FROM ticket_lock_type WHERE name = :n AND valid_id = 1 LIMIT 1"),
            {"n": name},
        )
    ).first()
    return int(row[0]) if row else None


async def _resolve_type_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return int(id_)
    if not name:
        return None
    row = (
        await session.execute(
            text("SELECT id FROM ticket_type WHERE name = :n AND valid_id = 1 LIMIT 1"),
            {"n": name},
        )
    ).first()
    return int(row[0]) if row else None


async def _resolve_service_id(
    session: AsyncSession, name: str | None, id_: int | None
) -> int | None:
    if id_ is not None:
        return int(id_)
    if not name:
        return None
    row = (
        await session.execute(
            text("SELECT id FROM service WHERE name = :n AND valid_id = 1 LIMIT 1"),
            {"n": name},
        )
    ).first()
    return int(row[0]) if row else None


async def _resolve_sla_id(session: AsyncSession, name: str | None, id_: int | None) -> int | None:
    if id_ is not None:
        return int(id_)
    if not name:
        return None
    row = (
        await session.execute(
            text("SELECT id FROM sla WHERE name = :n AND valid_id = 1 LIMIT 1"),
            {"n": name},
        )
    ).first()
    return int(row[0]) if row else None


async def _lookup_name(session: AsyncSession, table: str, id_: int | None) -> str | None:
    if id_ is None:
        return None
    row = (
        await session.execute(
            text(f"SELECT name FROM {table} WHERE id = :id LIMIT 1"),  # noqa: S608
            {"id": id_},
        )
    ).first()
    return str(row[0]) if row else None


def _parse_pending_time(raw: Any) -> datetime | None:
    """Parse Znuny PendingTime {Year,Month,Day,Hour,Minute} or {Diff: minutes}."""
    if not raw or not isinstance(raw, dict):
        return None
    if raw.get("Diff") is not None:
        try:
            return datetime.now(tz=UTC) + timedelta(minutes=int(raw["Diff"]))
        except (ValueError, TypeError):
            return None
    try:
        return datetime(
            int(raw.get("Year", 0)),
            int(raw.get("Month", 0)),
            int(raw.get("Day", 0)),
            int(raw.get("Hour", 0)),
            int(raw.get("Minute", 0)),
            tzinfo=UTC,
        )
    except (ValueError, TypeError):
        return None


async def _state_ids_for_type(session: AsyncSession, type_name: str) -> list[int]:
    """Return all state IDs whose state type name matches (case-insensitive)."""
    rows = (
        (
            await session.execute(
                select(TicketState.id)
                .join(TicketStateType, TicketStateType.id == TicketState.type_id)
                .where(func.lower(TicketStateType.name) == type_name.lower())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _agent_in_group(session: AsyncSession, user_id: int, group_name: str) -> bool:
    """True if agent has rw (or any) membership on the named group."""
    from tiqora.db.legacy.profile import groups_table_sql

    pe = PermissionEngine(session)
    groups = await pe.groups_for_permission(user_id, "rw")
    if not groups:
        return False
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "mysql"
    gt = groups_table_sql(dialect=dialect)
    row = (
        await session.execute(
            text(f"SELECT id FROM {gt} WHERE name = :n AND valid_id = 1 LIMIT 1"),  # noqa: S608
            {"n": group_name},
        )
    ).first()
    if row is None:
        return False
    return int(row[0]) in set(groups)


def _like_pattern(val: Any) -> str:
    return str(val).replace("*", "%")


def _pref_param(session: AsyncSession, value: str) -> bytes | str:
    """Bind value for ``user_preferences.preferences_value`` (BLOB on MySQL, TEXT on PostgreSQL)."""
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        return value
    return value.encode("utf-8")


async def _set_user_preference(session: AsyncSession, user_id: int, key: str, value: str) -> None:
    """Upsert a user_preferences row (UTF-8 bytes; plain text on PostgreSQL's TEXT column)."""
    raw = _pref_param(session, value)
    existing = (
        await session.execute(
            text(
                "SELECT 1 FROM user_preferences"
                " WHERE user_id = :uid AND preferences_key = :k LIMIT 1"
            ),
            {"uid": user_id, "k": key},
        )
    ).first()
    if existing:
        await session.execute(
            text(
                "UPDATE user_preferences SET preferences_value = :v"
                " WHERE user_id = :uid AND preferences_key = :k"
            ),
            {"v": raw, "uid": user_id, "k": key},
        )
    else:
        await session.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:uid, :k, :v)"
            ),
            {"uid": user_id, "k": key, "v": raw},
        )


# ---------------------------------------------------------------------------
# Article delivery helpers (the article payload itself: compat/article_input.py)
# ---------------------------------------------------------------------------


def _as_send_article(article_in: ArticleIn, art_data: dict[str, Any]) -> ArticleIn:
    """The article as an outgoing agent email (ArticleSend forces channel Email)."""
    from dataclasses import replace

    from tiqora.crypto.outbound import email_security_from_znuny

    return replace(
        article_in,
        channel="email",
        sender_type="agent",
        is_visible_for_customer=True,
        email_security=email_security_from_znuny(art_data.get("EmailSecurity")),
        # Without an EmailSecurity hash the queue's security defaults apply.
        email_security_explicit="EmailSecurity" in art_data,
    )


async def _queue_gi_mail(
    session: AsyncSession,
    *,
    kind: str,
    ticket_id: int,
    article_id: int,
    user_id: int,
    auto_response_type: str = "",
) -> None:
    """Queue one mail of the request as a worker job, in the request transaction.

    The job row commits together with the ticket and article (never a job whose article
    could still roll back, never a committed article whose mail is lost). The request
    answers without waiting for SMTP - Znuny queues these mails in its MailQueue too.
    """
    from tiqora.jobs.handlers.gi_mail import GI_MAIL_KIND, GiMailPayload
    from tiqora.jobs.service import enqueue_job

    await enqueue_job(
        session,
        kind=GI_MAIL_KIND,
        payload=GiMailPayload(
            kind=kind,  # type: ignore[arg-type]
            ticket_id=ticket_id,
            article_id=article_id,
            user_id=user_id,
            auto_response_type=auto_response_type,
        ),
        created_by=None,  # a system job: not listed as anyone's own background task
    )


async def _write_gi_article(
    session: AsyncSession,
    sysconfig: SysConfig,
    settings: Settings | None,
    gi: GiArticle,
    *,
    ticket_id: int,
    user_id: int,
) -> int:
    """Store one request article, book its TimeUnit and queue its mail for the worker.

    ``ArticleSend`` stores the prepared (and secured) article now; the worker sends it.
    Every other article queues its auto response (Znuny sends it from ``ArticleCreate``,
    never for ``ArticleSend``) - only while an outbound relay is configured, because without
    one no auto response is ever sent.
    """
    if gi.send:
        queue_row = (
            await session.execute(
                text("SELECT queue_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
            )
        ).first()
        pending = await store_agent_email_for_dispatch(
            session,
            sysconfig,
            ticket_id=ticket_id,
            queue_id=int(queue_row[0]) if queue_row else 0,
            user_id=user_id,
            article=_as_send_article(gi.article, gi.raw),
        )
        article_id = pending.article_id
        await _queue_gi_mail(
            session,
            kind="article_send",
            ticket_id=ticket_id,
            article_id=article_id,
            user_id=user_id,
        )
    else:
        article_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=await _apply_stored_email_security(gi.article, gi.raw, sysconfig, settings),
            user_id=user_id,
            sysconfig=sysconfig,
        )
        if gi.auto_response_type and await _outbound_relay_enabled(session):
            await _queue_gi_mail(
                session,
                kind="auto_response",
                ticket_id=ticket_id,
                article_id=article_id,
                user_id=user_id,
                auto_response_type=gi.auto_response_type,
            )
    if gi.time_unit is not None:
        await add_time_accounting(
            session,
            ticket_id=ticket_id,
            article_id=article_id,
            time_unit=gi.time_unit,
            user_id=user_id,
        )
    return article_id


async def _outbound_relay_enabled(session: AsyncSession) -> bool:
    from tiqora.domain import mail_outbound

    return bool((await mail_outbound.resolve_outbound_smtp(session)).enabled)


async def _write_ticket_dynamic_fields(
    session: AsyncSession, fields: GiDynamicFields, *, ticket_id: int, user_id: int
) -> None:
    for name, values in fields.ticket.items():
        await set_ticket_dynamic_field(
            session, ticket_id=ticket_id, field=fields.configs[name], values=values, user_id=user_id
        )


async def _write_article_dynamic_fields(
    session: AsyncSession,
    fields: GiDynamicFields,
    *,
    ticket_id: int,
    article_id: int,
    user_id: int,
) -> None:
    for name, values in fields.article.items():
        await set_article_dynamic_field(
            session,
            ticket_id=ticket_id,
            article_id=article_id,
            field=fields.configs[name],
            values=values,
            user_id=user_id,
        )


async def _apply_stored_email_security(
    article_in: ArticleIn,
    art_data: dict[str, Any],
    sysconfig: SysConfig,
    settings: Settings | None,
) -> ArticleIn:
    """No ArticleSend: EmailSecurity rewrites the stored body (best effort)."""
    security = art_data.get("EmailSecurity")
    if not (isinstance(security, dict) and security):
        return article_in
    from tiqora.config import get_settings
    from tiqora.crypto.config import resolve_crypto_config
    from tiqora.crypto.outbound import apply_email_security

    crypto_settings = settings or get_settings()
    return await apply_email_security(
        article_in,
        security,
        crypto_settings,
        await resolve_crypto_config(crypto_settings, sysconfig),
    )


# ---------------------------------------------------------------------------
# SessionCreate
# ---------------------------------------------------------------------------


async def op_session_create(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """SessionCreate operation — returns a SessionID.

    Agent password auth is rejected when 2FA is enabled or enforced; non-interactive
    callers must use a ``tiqora_*`` API key (H-1). Customer sessions use sentinel
    ``user_id=0`` and must never be rewritten to the root agent (C-1).
    Password attempts are rate-limited per login/IP (M-7 / H-01).
    """
    cfg = settings or get_settings()
    user_login = (data.get("UserLogin") or "").strip()
    customer_login = (data.get("CustomerUserLogin") or "").strip()
    password = (data.get("Password") or "").strip()

    if not user_login and not customer_login:
        return _err(
            "SessionCreate.MissingParameter",
            "SessionCreate: UserLogin or CustomerUserLogin is required!",
        )
    if not password:
        return _err("SessionCreate.MissingParameter", "SessionCreate: Password is required!")

    limiter: AuthRateLimiter | None = None
    ip = "unknown"
    rate_login = user_login or customer_login
    if request is not None:
        redis_client = getattr(getattr(request, "app", None), "state", None)
        redis_client = getattr(redis_client, "redis", None) if redis_client else None
        if redis_client is not None:
            limiter = AuthRateLimiter(redis_client, cfg)
            ip = client_ip(request)
            pre = await limiter.check(login=rate_login, ip=ip)
            if not pre.allowed:
                return _compat_rate_limited_err("SessionCreate", pre.retry_after)

    if user_login:
        row = (
            await session.execute(
                select(Users).where(Users.login == user_login, Users.valid_id == 1)
            )
        ).scalar_one_or_none()
        stored = (row.pw or "") if row is not None else ""
        if row is not None and cfg.password_reject_weak_hashes and is_weak_scheme(stored):
            row = None
        if row is None or not verify_password(password, stored):
            if limiter is not None:
                locked = await limiter.record_failure(login=user_login, ip=ip)
                if locked is not None:
                    return _compat_rate_limited_err("SessionCreate", locked.retry_after)
            return _err("SessionCreate.AuthFail", "SessionCreate: Authorization failing!")
        if await _agent_password_blocked_by_2fa(session, row.id):
            return _err("SessionCreate.AuthFail", _2FA_API_KEY_MSG)
        if limiter is not None:
            await limiter.reset(login=user_login, ip=ip)
        await _maybe_rehash_agent_pw(session, row, password, cfg)
        user = AuthenticatedUser(
            id=row.id,
            login=row.login,
            first_name=row.first_name,
            last_name=row.last_name,
            auth_method="session",
        )
    else:
        # Customer user session — sentinel id 0, not root/agent 1.
        row2 = (
            await session.execute(
                select(CustomerUser).where(
                    CustomerUser.login == customer_login,
                    CustomerUser.valid_id == 1,
                )
            )
        ).scalar_one_or_none()
        stored2 = (row2.pw or "") if row2 is not None else ""
        if row2 is not None and cfg.password_reject_weak_hashes and is_weak_scheme(stored2):
            row2 = None
        if row2 is None or not verify_password(password, stored2):
            if limiter is not None:
                locked = await limiter.record_failure(login=customer_login, ip=ip)
                if locked is not None:
                    return _compat_rate_limited_err("SessionCreate", locked.retry_after)
            return _err("SessionCreate.AuthFail", "SessionCreate: Authorization failing!")
        if limiter is not None:
            await limiter.reset(login=customer_login, ip=ip)
        await _maybe_rehash_customer_pw(session, row2, password, cfg)
        user = AuthenticatedUser(
            id=_CUSTOMER_SENTINEL_USER_ID,
            login=customer_login,
            first_name=row2.first_name,
            last_name=row2.last_name,
            auth_method="session",
        )

    sysconfig = SysConfig(session)
    if await znuny_session_writes_enabled(cfg, sysconfig):
        if user_login:
            user_data = await znuny_agent_user_data(session, user.id, sysconfig=sysconfig)
            user_type = _AGENT_USER_TYPE
        else:
            user_data = await znuny_customer_user_data(session, customer_login)
            user_type = _CUSTOMER_USER_TYPE
        headers = getattr(request, "headers", None)
        session_id = await write_znuny_session(
            session,
            user_data=user_data,
            user_type=user_type,
            remote_addr=_peer_addr(request) or "none",
            user_agent=(headers.get("user-agent") if headers is not None else "") or "none",
            now_epoch=int(datetime.now(UTC).timestamp()),
        )
        await session.commit()
        return {"SessionID": session_id}

    token = await session_store.create(user.id, user.login)
    return {"SessionID": token}


async def op_session_get(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """SessionGet — return SessionData key/value list for a SessionID.

    Matches Znuny SessionGet: no auth params beyond SessionID; the session is checked
    first (``CheckSessionID``: idle / absolute expiry, here also a still valid account and
    the optional remote-IP binding - the same check as every authenticated call) and an
    invalid one answers ``SessionGet.SessionInvalid``. Returns the allow-listed session
    key/value pairs.
    """
    op = "SessionGet"
    invalid = _err(f"{op}.SessionInvalid", f"{op}: SessionID is Invalid!")
    session_id = (data.get("SessionID") or "").strip()
    if not session_id:
        return _err(f"{op}.MissingParameter", f"{op}: SessionID is missing!")

    # Prefer Znuny sessions table (full key dump).
    rows = (
        await session.execute(
            text("SELECT data_key, data_value, serialized FROM sessions WHERE session_id = :sid"),
            {"sid": session_id},
        )
    ).fetchall()
    if rows:
        valid = await _lookup_session(
            session, session_id, remote_addr=_peer_addr(request), settings=settings
        )
        if valid is None:
            return invalid
        session_data: list[dict[str, Any]] = []
        for key, val, serialized in rows:
            k = str(key)
            if not session_get_key_allowed(k):
                continue
            entry: dict[str, Any] = {"Key": k, "Value": val if val is not None else ""}
            if serialized:
                entry["Serialized"] = 1
            session_data.append(entry)
        session_data.sort(key=lambda e: str(e["Key"]))
        return {"SessionData": session_data}

    # Fall back to Tiqora Redis session store.
    payload = await session_store.get(session_id)
    if payload is None:
        return invalid
    user_id, login = payload
    if user_id == _CUSTOMER_SENTINEL_USER_ID:
        user_type = _CUSTOMER_USER_TYPE
        user_id_out = 0
    else:
        user_type = _AGENT_USER_TYPE
        user_id_out = user_id
    return {
        "SessionData": [
            {"Key": "UserID", "Value": str(user_id_out)},
            {"Key": "UserLogin", "Value": login},
            {"Key": "UserType", "Value": user_type},
        ]
    }


async def op_session_remove(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """SessionRemove / SessionDelete — drop a SessionID from both stores."""
    op = "SessionRemove"
    session_id = (data.get("SessionID") or "").strip()
    if not session_id:
        return _err(
            f"{op}.MissingParameter",
            f"{op}: Parameter 'SessionID' in 'Data' is missing or empty.",
        )

    removed = False
    # Znuny sessions table
    del_result = await session.execute(
        text("DELETE FROM sessions WHERE session_id = :sid"),
        {"sid": session_id},
    )
    # CursorResult.rowcount is not on the generic Result type stub.
    deleted_rows = int(getattr(del_result, "rowcount", 0) or 0)
    if deleted_rows > 0:
        removed = True
        await session.commit()

    # Tiqora Redis session
    if await session_store.get(session_id) is not None:
        await session_store.delete(session_id)
        removed = True
    else:
        # delete even if get returned None (pending/enroll tokens etc.)
        await session_store.delete(session_id)

    if not removed:
        # Znuny fails when RemoveSessionID returns false (unknown id).
        # Still succeed if Redis delete was a no-op but session never existed —
        # match Znuny Fail only when we found nothing to remove.
        return _err(
            f"{op}.Fail",
            f"{op}: Could not remove session with ID '{session_id}'.",
        )
    return {"Success": 1}


def _reprefix_auth_error(op: str, auth_err: dict[str, Any]) -> dict[str, Any]:
    """Re-prefix an Auth.* error for a Ticket* operation, preserving rate-limit."""
    err = auth_err.get("Error") or {}
    code = str(err.get("ErrorCode") or "")
    message = str(err.get("ErrorMessage") or "Authorization failing!")
    if "RateLimited" in code:
        out = _err(f"{op}.RateLimited", message)
        if "RetryAfter" in err:
            out["Error"]["RetryAfter"] = err["RetryAfter"]
        return out
    return _err(f"{op}.AuthFail", message)


# ---------------------------------------------------------------------------
# TicketCreate
# ---------------------------------------------------------------------------


async def op_ticket_create(
    data: dict[str, Any],
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    session_store: SessionStore,
    sysconfig: SysConfig,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
    operation_config: OperationConfig | None = None,
) -> dict[str, Any]:
    """TicketCreate operation."""
    op = "TicketCreate"

    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, login, user_type = auth
    is_customer = user_type == _CUSTOMER_USER_TYPE

    ticket = data.get("Ticket") or {}
    if not ticket:
        return _err(f"{op}.MissingParameter", f"{op}: Ticket data is missing!")

    title = (ticket.get("Title") or "").strip()
    if not title:
        return _err(f"{op}.MissingParameter", f"{op}: Ticket->Title is missing!")

    queue_id = await _resolve_queue_id(session, ticket.get("Queue"), ticket.get("QueueID"))
    if queue_id is None:
        return _err(f"{op}.MissingParameter", f"{op}: Ticket->Queue or QueueID is missing!")

    state_id = await _resolve_state_id(session, ticket.get("State"), ticket.get("StateID"))
    if state_id is None:
        return _err(f"{op}.MissingParameter", f"{op}: Ticket->State or StateID is missing!")

    priority_id = await _resolve_priority_id(
        session, ticket.get("Priority"), ticket.get("PriorityID")
    )
    if priority_id is None:
        return _err(f"{op}.MissingParameter", f"{op}: Ticket->Priority or PriorityID is missing!")

    requested_owner_id = await _resolve_user_id(session, ticket.get("Owner"), ticket.get("OwnerID"))
    # Znuny: an owner given in the request means "no agent mail for the first article".
    has_owner = perl_truthy(requested_owner_id)  # TicketCreate.pm:1862: "0" is no owner

    responsible_id = await _resolve_user_id(
        session, ticket.get("Responsible"), ticket.get("ResponsibleID")
    )

    lock_for_owner = False
    lock_id = await _resolve_lock_id(session, ticket.get("Lock"), ticket.get("LockID"))
    if lock_id is None:
        lock_id = 1
        # TicketCreate.pm:1865-1870: an owner given without Lock/LockID locks the ticket for
        # them, after TicketOwnerSet and the articles (see below)
        lock_for_owner = has_owner

    type_id = await _resolve_type_id(session, ticket.get("Type"), ticket.get("TypeID"))
    service_id = await _resolve_service_id(session, ticket.get("Service"), ticket.get("ServiceID"))
    sla_id = await _resolve_sla_id(session, ticket.get("SLA"), ticket.get("SLAID"))
    pending_time = _parse_pending_time(ticket.get("PendingTime"))

    if is_customer:
        # Ownership-scoped create: force customer identity; never agent queue-group ACL.
        customer_company = await _load_customer_company_id(session, login)
        customer_id_val: str | None = customer_company or ticket.get("CustomerID")
        customer_user_val: str | None = login
        write_user_id = PORTAL_SYSTEM_USER_ID
    else:
        pe = PermissionEngine(session)
        if not await pe.check(user_id, queue_id, "create"):
            # TicketCreate.pm:519
            return _err(
                f"{op}.AccessDenied", f"{op}: Can not create tickets in given Queue or QueueID!"
            )
        # TicketCreate.pm:1318-1361: CustomerUser by login, else by e-mail; CustomerID from
        # the customer user unless the request names a non-empty one
        customer_user_val, customer_id_val = await _resolve_customer_defaults(
            session, ticket.get("CustomerUser"), ticket.get("CustomerID")
        )
        write_user_id = user_id

    # Everything is validated before the first write (Run + _CheckArticle + _CheckDynamicField).
    articles = await parse_gi_articles(
        data.get("Article"),
        op=op,
        user_type=user_type,
        has_owner=has_owner,
        session=session,
        sysconfig=sysconfig,
    )
    if isinstance(articles, dict):
        return articles
    fields = await parse_gi_dynamic_fields(
        data.get("DynamicField"), op=op, articles_present=True, session=session
    )
    if isinstance(fields, dict):
        return fields

    # A closed ticket is never created locked (TicketCreate.pm: forced unlock).
    closed_state = state_id in await _state_ids_for_type(session, "closed")

    ticket_in = TicketIn(
        title=title,
        queue_id=queue_id,
        state_id=state_id,
        priority_id=priority_id,
        # TicketCreate.pm:1396 creates with OwnerID 1 and unlocked; owner and lock follow as
        # TicketOwnerSet / TicketLockSet so the history matches (see below)
        owner_id=1,
        lock_id=1,
        responsible_id=None,
        customer_id=customer_id_val,
        customer_user_id=customer_user_val,
        type_id=type_id,
        service_id=service_id,
        sla_id=sla_id,
        dynamic_fields={},
        article=None,
    )

    try:
        async with session.begin_nested():
            ticket_id = await create_ticket(
                session, session_factory, sysconfig, params=ticket_in, user_id=write_user_id
            )
            if lock_id == 2:
                # TicketCreate.pm:1417-1426: explicit Lock is a TicketLockSet after the create
                await lock_ticket(
                    session, ticket_id=ticket_id, user_id=write_user_id, sysconfig=sysconfig
                )
            elif lock_id != 1:
                await set_ticket_lock(
                    session,
                    ticket_id=ticket_id,
                    lock_id=lock_id,
                    user_id=write_user_id,
                    sysconfig=sysconfig,
                )
            if closed_state and lock_id != 1:
                await unlock_ticket(
                    session, ticket_id=ticket_id, user_id=write_user_id, sysconfig=sysconfig
                )
            # PendingTime on create: set until_time via change_state with same state.
            if pending_time is not None:
                await change_state(
                    session,
                    ticket_id=ticket_id,
                    new_state_id=state_id,
                    user_id=write_user_id,
                    sysconfig=sysconfig,
                    pending_time=pending_time,
                )
            await _write_ticket_dynamic_fields(
                session, fields, ticket_id=ticket_id, user_id=write_user_id
            )
            article_ids: list[int] = []
            queue_label = await _queue_label(session, ticket.get("Queue"), queue_id)
            for gi in articles:
                if not gi.send:
                    # TicketCreate.pm:1544-1558: without ArticleSend To is the queue and Cc/Bcc
                    # are not passed on
                    gi = replace(
                        gi,
                        article=replace(gi.article, to_address=queue_label, cc=None, bcc=None),
                    )
                if not gi.article.from_address and not gi.send:
                    gi = replace(
                        gi,
                        article=replace(
                            gi.article,
                            from_address=await _default_from(session, customer_user_val, None),
                        ),
                    )
                article_id = await _write_gi_article(
                    session,
                    sysconfig,
                    settings,
                    gi,
                    ticket_id=ticket_id,
                    user_id=write_user_id,
                )
                article_ids.append(article_id)
                await _write_article_dynamic_fields(
                    session,
                    fields,
                    ticket_id=ticket_id,
                    article_id=article_id,
                    user_id=write_user_id,
                )
            final_owner_id = int(requested_owner_id or 0) if has_owner else write_user_id
            # TicketCreate.pm:1853-1880: TicketOwnerSet after the articles, to the requested
            # owner, else (no notification) to the calling user; then the lock for an owner
            await assign_owner(
                session,
                ticket_id=ticket_id,
                new_owner_id=final_owner_id,
                user_id=write_user_id,
                sysconfig=sysconfig,
            )
            if lock_for_owner:
                # then locked (even a closed ticket)
                await lock_ticket(
                    session, ticket_id=ticket_id, user_id=write_user_id, sysconfig=sysconfig
                )
            # TicketCreate.pm:1883: TicketResponsibleSet after owner and lock (a no-op when
            # it is the creation default, root)
            if responsible_id and int(responsible_id) != 1:
                await assign_responsible(
                    session,
                    ticket_id=ticket_id,
                    new_responsible_id=int(responsible_id),
                    user_id=write_user_id,
                )
            # Ticket::EventModulePost###9990-EscalationIndex (TicketCreate, ArticleCreate; runs
            # as a transaction handler, i.e. once after everything above) rebuilds the
            # escalation columns; the last article is the "last sender" of the update time
            await escalation_index_build(session, ticket_id, write_user_id, sysconfig)
        await session.commit()
    except TicketAccessDenied:
        await session.rollback()
        return _err(f"{op}.AccessDenied", f"{op}: No permission!")
    except InvalidInput as e:
        await session.rollback()
        return _err(f"{op}.InvalidParameter", str(e))
    except _OutboundMailError as e:
        await session.rollback()
        return _err(f"{op}.OperationFailed", f"{op}: Could not send article: {e}")

    return await _with_ticket_data(
        article_id_result(
            {"TicketID": ticket_id, "TicketNumber": await _get_tn(session, ticket_id)},
            article_ids,
            op=op,
        ),
        session,
        operation_config,
        op=op,
        ticket_id=ticket_id,
        article_ids=article_ids,
        attachments=bool(data.get("Attachment")),
    )


async def _resolve_customer_defaults(
    session: AsyncSession, customer_user: Any, customer_id: Any
) -> tuple[str | None, str | None]:
    """``(CustomerUser, CustomerID)`` for a GI TicketCreate (TicketCreate.pm:1318-1361).

    The customer user is looked up by login, else (``PostMasterSearch``) by e-mail address
    (first login in sort order); a found record yields its canonical login and its
    ``customer_id`` as the default CustomerID. A non-empty CustomerID in the request wins.
    An unknown customer user stays as sent.
    """
    login = str(customer_user) if customer_user not in (None, "") else None
    company: str | None = None
    if login:
        row = (
            await session.execute(
                text("SELECT login, customer_id FROM customer_user WHERE login = :l LIMIT 1"),
                {"l": login},
            )
        ).first()
        if row is None:
            row = (
                await session.execute(
                    text(
                        "SELECT login, customer_id FROM customer_user"
                        " WHERE LOWER(email) = LOWER(:e) AND valid_id = 1"
                        " ORDER BY login LIMIT 1"
                    ),
                    {"e": login},
                )
            ).first()
        if row is not None:
            login, company = str(row[0]), (str(row[1]) if row[1] else None)
    explicit = str(customer_id) if customer_id not in (None, "") else None
    # '' (not NULL) with a customer user but no company: TicketCustomerSet stores what it got
    return login, explicit or company or ("" if login else None)


async def _queue_label(session: AsyncSession, queue_name: Any, queue_id: int) -> str:
    """Queue name of the request as sent, else the looked-up one (TicketCreate.pm:1552-1558)."""
    if queue_name:
        return str(queue_name)
    row = (
        await session.execute(text("SELECT name FROM queue WHERE id = :i"), {"i": queue_id})
    ).first()
    return str(row[0]) if row else ""


async def _default_from(
    session: AsyncSession, customer_login: str | None, fallback: str | None
) -> str | None:
    """Znuny's From for an article without one (TicketCreate.pm 1512-1545).

    The customer user's ``"Full Name" <mail>``; the raw customer user when there is no
    such record; ``fallback`` when the request names no customer.
    """
    if not customer_login:
        return fallback
    row = (
        await session.execute(
            text("SELECT first_name, last_name, email FROM customer_user WHERE login = :l LIMIT 1"),
            {"l": customer_login},
        )
    ).first()
    if row is None:
        return customer_login
    full_name = " ".join(p for p in (row[0], row[1]) if p).strip()
    return f'"{full_name}" <{row[2]}>' if row[2] else (full_name or customer_login)


async def _with_ticket_data(
    result: dict[str, Any],
    session: AsyncSession,
    operation_config: OperationConfig | None,
    *,
    op: str,
    ticket_id: int,
    article_ids: list[int],
    attachments: bool,
) -> dict[str, Any]:
    """``IncludeTicketData`` of the webservice operation: answer with ``Ticket``.

    ``TicketGet`` (with dynamic fields) plus the ``ArticleGet`` of just the articles this call
    created. TicketCreate lists them under ``Article`` always and with attachments only when
    the request had a top-level ``Attachment`` (``attachments``); TicketUpdate uses ``Article``
    (a hash) for one, ``Articles`` for several, and always includes attachments.
    """
    if operation_config is None or not operation_config.include_ticket_data:
        return result
    now = datetime.now(UTC).replace(tzinfo=None)
    ctx = await load_ticket_get_context(session, dynamic_fields=True, articles=bool(article_ids))
    ticket = await build_ticket_entry(
        session, ticket_id, dynamic_fields=True, extended=False, now=now, ctx=ctx
    )
    if ticket is None:
        return result
    if article_ids:
        entries = await build_article_entries(
            session,
            ticket_id,
            customer_only=False,
            sender_types=None,
            order="ASC",
            limit=0,
            dynamic_fields=True,
            attachments=attachments,
            get_attachment_contents=True,
            html_body_as_attachment=False,
            ctx=ctx,
            only_article_ids=article_ids,
        )
        if entries:
            if op == "TicketCreate":
                ticket["Article"] = entries
            elif len(article_ids) == 1:
                ticket["Article"] = entries[0]
            else:
                ticket["Articles"] = entries
    return {**result, "Ticket": ticket}


async def _get_tn(session: AsyncSession, ticket_id: int) -> str:
    row = (
        await session.execute(
            text("SELECT tn FROM ticket WHERE id = :tid LIMIT 1"), {"tid": ticket_id}
        )
    ).first()
    return str(row[0]) if row else ""


# ---------------------------------------------------------------------------
# TicketUpdate
# ---------------------------------------------------------------------------


async def op_ticket_update(
    data: dict[str, Any],
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    session_store: SessionStore,
    sysconfig: SysConfig,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
    operation_config: OperationConfig | None = None,
) -> dict[str, Any]:
    """TicketUpdate operation."""
    op = "TicketUpdate"

    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, login, user_type = auth
    is_customer = user_type == _CUSTOMER_USER_TYPE

    ticket_id_raw = data.get("TicketID")
    ticket_number = data.get("TicketNumber")
    if ticket_id_raw is None and ticket_number is None:
        return _err(f"{op}.MissingParameter", f"{op}: TicketID or TicketNumber is required!")

    ticket_id: int
    if ticket_id_raw is not None:
        # TicketUpdate.pm:416-436: an id that is not a ticket (empty, "0", not a number) fails
        # the TicketGet with AccessDenied
        raw_id = perl_str(ticket_id_raw).strip()
        if not perl_truthy(ticket_id_raw) or not re.fullmatch(r"[0-9]{1,18}", raw_id):
            return _err(f"{op}.AccessDenied", f"{op}: User does not have access to the ticket!")
        ticket_id = int(raw_id)
    else:
        row = (
            await session.execute(
                text("SELECT id FROM ticket WHERE tn = :tn LIMIT 1"), {"tn": ticket_number}
            )
        ).first()
        if row is None:
            return _err(
                f"{op}.InvalidParameter",
                f"{op}: Ticket not found for TicketNumber {ticket_number!r}",
            )
        ticket_id = int(row[0])

    # Check ticket exists + get queue_id for permission check
    t_row = (
        (
            await session.execute(
                text("SELECT queue_id FROM ticket WHERE id = :tid LIMIT 1"),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .first()
    )
    if t_row is None:
        return _err(f"{op}.InvalidParameter", f"{op}: Ticket {ticket_id} not found!")
    queue_id = int(t_row["queue_id"])

    ticket = data.get("Ticket") or {}
    art_data = data.get("Article")

    if is_customer:
        # Portal customers may only add a follow-up article on owned tickets —
        # never agent queue-group rw, never field mutations (C-1).
        company_id = await _load_customer_company_id(session, login)
        if not await customer_can_access_ticket(
            session, login=login, customer_id=company_id, ticket_id=ticket_id
        ):
            return _err(f"{op}.AccessDenied", f"{op}: No permission to update ticket!")
        if ticket:
            return _err(
                f"{op}.AccessDenied",
                f"{op}: Customers may only add follow-up articles, not change ticket fields!",
            )
        if not normalize_article_list(art_data):
            return _err(
                f"{op}.MissingParameter",
                f"{op}: Article is required for customer follow-up!",
            )
        follow_ups = await parse_gi_articles(
            art_data,
            op=op,
            user_type=user_type,
            has_owner=False,
            session=session,
            sysconfig=sysconfig,
        )
        if isinstance(follow_ups, dict):
            return follow_ups
        article_ids: list[int] = []
        customer_from = await _default_from(session, login, None)
        try:
            async with session.begin_nested():
                for gi in follow_ups:
                    if not gi.article.from_address:
                        gi = replace(gi, article=replace(gi.article, from_address=customer_from))
                    article_ids.append(
                        await _write_gi_article(
                            session,
                            sysconfig,
                            settings,
                            gi,
                            ticket_id=ticket_id,
                            user_id=PORTAL_SYSTEM_USER_ID,
                        )
                    )
            await session.commit()
        except TicketNotFound:
            await session.rollback()
            return _err(f"{op}.InvalidParameter", f"{op}: Ticket {ticket_id} not found!")
        except TicketAccessDenied:
            await session.rollback()
            return _err(f"{op}.AccessDenied", f"{op}: No permission!")
        except InvalidInput as e:
            await session.rollback()
            return _err(f"{op}.InvalidParameter", str(e))
        return await _with_ticket_data(
            article_id_result(
                {"TicketID": ticket_id, "TicketNumber": await _get_tn(session, ticket_id)},
                article_ids,
                op=op,
            ),
            session,
            operation_config,
            op=op,
            ticket_id=ticket_id,
            article_ids=article_ids,
            attachments=True,
        )

    pe = PermissionEngine(session)
    if not await pe.check(user_id, queue_id, "rw"):
        return _err(f"{op}.AccessDenied", f"{op}: No permission to update ticket!")

    # Everything is validated before the first write (Run + _CheckArticle + _CheckDynamicField).
    articles = await parse_gi_articles(
        art_data,
        op=op,
        user_type=user_type,
        has_owner=False,
        session=session,
        sysconfig=sysconfig,
    )
    if isinstance(articles, dict):
        return articles
    fields = await parse_gi_dynamic_fields(
        data.get("DynamicField"), op=op, articles_present=bool(articles), session=session
    )
    if isinstance(fields, dict):
        return fields
    article_fields: list[GiDynamicFields] = []
    for gi in articles:
        own = await parse_gi_dynamic_fields(
            gi.raw.get("DynamicField"), op=op, articles_present=True, session=session
        )
        if isinstance(own, dict):
            return own
        article_fields.append(own)

    try:
        async with session.begin_nested():
            # Title
            if title := ticket.get("Title"):
                await change_title(session, ticket_id=ticket_id, new_title=title, user_id=user_id)

            # Queue
            new_queue_id = await _resolve_queue_id(
                session, ticket.get("Queue"), ticket.get("QueueID")
            )
            if new_queue_id is not None and new_queue_id != queue_id:
                await move_queue(
                    session,
                    ticket_id=ticket_id,
                    new_queue_id=new_queue_id,
                    user_id=user_id,
                    sysconfig=sysconfig,
                )

            # State + optional PendingTime (Year/Month/... or Diff minutes)
            new_state_id = await _resolve_state_id(
                session, ticket.get("State"), ticket.get("StateID")
            )
            pending_time = _parse_pending_time(ticket.get("PendingTime"))
            if new_state_id is not None:
                await change_state(
                    session,
                    ticket_id=ticket_id,
                    new_state_id=new_state_id,
                    user_id=user_id,
                    sysconfig=sysconfig,
                    pending_time=pending_time,
                )
            elif pending_time is not None:
                # PendingTime alone: re-apply current state with new until_time
                cur = (
                    await session.execute(
                        text("SELECT ticket_state_id FROM ticket WHERE id = :tid"),
                        {"tid": ticket_id},
                    )
                ).first()
                if cur:
                    await change_state(
                        session,
                        ticket_id=ticket_id,
                        new_state_id=int(cur[0]),
                        user_id=user_id,
                        sysconfig=sysconfig,
                        pending_time=pending_time,
                    )

            # Priority
            new_prio_id = await _resolve_priority_id(
                session, ticket.get("Priority"), ticket.get("PriorityID")
            )
            if new_prio_id is not None:
                await change_priority(
                    session,
                    ticket_id=ticket_id,
                    new_priority_id=new_prio_id,
                    user_id=user_id,
                    sysconfig=sysconfig,
                )

            # Owner
            new_owner_id = await _resolve_user_id(
                session, ticket.get("Owner"), ticket.get("OwnerID")
            )
            if new_owner_id is not None:
                # Znuny GI TicketUpdate calls TicketOwnerSet only — it never
                # auto-locks on an owner change (golden-master validated).
                await assign_owner(
                    session,
                    ticket_id=ticket_id,
                    new_owner_id=new_owner_id,
                    user_id=user_id,
                    sysconfig=sysconfig,
                )

            # Responsible
            new_resp_id = await _resolve_user_id(
                session, ticket.get("Responsible"), ticket.get("ResponsibleID")
            )
            if new_resp_id is not None:
                await assign_responsible(
                    session, ticket_id=ticket_id, new_responsible_id=new_resp_id, user_id=user_id
                )

            # Lock
            new_lock_id = await _resolve_lock_id(session, ticket.get("Lock"), ticket.get("LockID"))
            if new_lock_id is not None:
                if new_lock_id == 2:
                    await lock_ticket(
                        session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig
                    )
                else:
                    await unlock_ticket(
                        session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig
                    )

            # Type
            new_type_id = await _resolve_type_id(session, ticket.get("Type"), ticket.get("TypeID"))
            if new_type_id is not None:
                snap = (
                    await session.execute(
                        text("SELECT type_id FROM ticket WHERE id = :tid"),
                        {"tid": ticket_id},
                    )
                ).first()
                old_type_id = int(snap[0]) if snap and snap[0] is not None else 0
                old_type = (await _lookup_name(session, "ticket_type", old_type_id)) or "NULL"
                new_type = (await _lookup_name(session, "ticket_type", new_type_id)) or "NULL"
                await session.execute(
                    text(
                        "UPDATE ticket SET type_id = :ty, change_time = current_timestamp,"
                        " change_by = :uid WHERE id = :tid"
                    ),
                    {"ty": new_type_id, "uid": user_id, "tid": ticket_id},
                )
                await add_type_update(
                    session,
                    ticket_id=ticket_id,
                    new_type=new_type,
                    new_type_id=new_type_id,
                    old_type=old_type,
                    old_type_id=old_type_id or "",
                    user_id=user_id,
                )

            # Service
            new_svc_id = await _resolve_service_id(
                session, ticket.get("Service"), ticket.get("ServiceID")
            )
            if new_svc_id is not None:
                snap = (
                    await session.execute(
                        text("SELECT service_id FROM ticket WHERE id = :tid"),
                        {"tid": ticket_id},
                    )
                ).first()
                old_svc_id = int(snap[0]) if snap and snap[0] is not None else 0
                old_svc = (await _lookup_name(session, "service", old_svc_id)) or "NULL"
                new_svc = (await _lookup_name(session, "service", new_svc_id)) or "NULL"
                await session.execute(
                    text(
                        "UPDATE ticket SET service_id = :s, change_time = current_timestamp,"
                        " change_by = :uid WHERE id = :tid"
                    ),
                    {"s": new_svc_id, "uid": user_id, "tid": ticket_id},
                )
                await add_service_update(
                    session,
                    ticket_id=ticket_id,
                    new_service=new_svc,
                    new_service_id=new_svc_id,
                    old_service=old_svc,
                    old_service_id=old_svc_id or "",
                    user_id=user_id,
                )

            # SLA
            new_sla_id = await _resolve_sla_id(session, ticket.get("SLA"), ticket.get("SLAID"))
            if new_sla_id is not None:
                snap = (
                    await session.execute(
                        text("SELECT sla_id FROM ticket WHERE id = :tid"),
                        {"tid": ticket_id},
                    )
                ).first()
                old_sla_id = int(snap[0]) if snap and snap[0] is not None else 0
                old_sla = (await _lookup_name(session, "sla", old_sla_id)) or "NULL"
                new_sla = (await _lookup_name(session, "sla", new_sla_id)) or "NULL"
                await session.execute(
                    text(
                        "UPDATE ticket SET sla_id = :s, change_time = current_timestamp,"
                        " change_by = :uid WHERE id = :tid"
                    ),
                    {"s": new_sla_id, "uid": user_id, "tid": ticket_id},
                )
                await add_sla_update(
                    session,
                    ticket_id=ticket_id,
                    new_sla=new_sla,
                    new_sla_id=new_sla_id,
                    old_sla=old_sla,
                    old_sla_id=old_sla_id or "",
                    user_id=user_id,
                )

            # Customer
            cid = ticket.get("CustomerID")
            cuid = ticket.get("CustomerUser")
            if cid is not None or cuid is not None:
                await set_customer(
                    session,
                    ticket_id=ticket_id,
                    customer_id=cid,
                    customer_user_id=cuid,
                    user_id=user_id,
                )

            # Articles first, then the request's dynamic fields (TicketUpdate.pm: Article
            # fields land on the first article, Ticket fields on the ticket).
            article_ids = []
            for gi, own_fields in zip(articles, article_fields, strict=True):
                article_id = await _write_gi_article(
                    session,
                    sysconfig,
                    settings,
                    gi,
                    ticket_id=ticket_id,
                    user_id=user_id,
                )
                article_ids.append(article_id)
                await _write_ticket_dynamic_fields(
                    session, own_fields, ticket_id=ticket_id, user_id=user_id
                )
                await _write_article_dynamic_fields(
                    session, own_fields, ticket_id=ticket_id, article_id=article_id, user_id=user_id
                )
            await _write_ticket_dynamic_fields(
                session, fields, ticket_id=ticket_id, user_id=user_id
            )
            if article_ids:
                await _write_article_dynamic_fields(
                    session,
                    fields,
                    ticket_id=ticket_id,
                    article_id=article_ids[0],
                    user_id=user_id,
                )

        await session.commit()
    except TicketNotFound:
        await session.rollback()
        return _err(f"{op}.InvalidParameter", f"{op}: Ticket {ticket_id} not found!")
    except TicketAccessDenied:
        await session.rollback()
        return _err(f"{op}.AccessDenied", f"{op}: No permission!")
    except InvalidInput as e:
        await session.rollback()
        return _err(f"{op}.InvalidParameter", str(e))
    except _OutboundMailError as e:
        await session.rollback()
        return _err(f"{op}.OperationFailed", f"{op}: Could not send article: {e}")

    return await _with_ticket_data(
        article_id_result(
            {"TicketID": ticket_id, "TicketNumber": await _get_tn(session, ticket_id)},
            article_ids,
            op=op,
        ),
        session,
        operation_config,
        op=op,
        ticket_id=ticket_id,
        article_ids=article_ids,
        attachments=True,
    )


_MAX_BIGINT = 2**63 - 1


def _gi_ticket_id_tokens(raw: Any, *, split_pattern: str) -> list[str] | None:
    """The ``TicketID`` parameter of TicketGet/TicketHistoryGet as strings.

    A non-empty scalar is split like Perl ``split`` (trailing empty fields dropped), a
    non-empty list is taken as is; anything else is ``None`` (``WrongStructure``).
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (str, int, float)) and str(raw) != "":
        parts = re.split(split_pattern, str(raw))
        while parts and parts[-1] == "":
            parts.pop()
        return parts
    if isinstance(raw, (list, tuple)) and raw:
        return [perl_str(x) if not isinstance(x, (dict, list)) else "" for x in raw]
    return None


# ---------------------------------------------------------------------------
# TicketGet
# ---------------------------------------------------------------------------


async def op_ticket_get(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """TicketGet operation.

    Supports flags: AllArticles, Attachments, DynamicFields, Extended,
    ArticleOrder, ArticleLimit, ArticleSenderType, GetAttachmentContents,
    HTMLBodyAsAttachment.
    """
    op = "TicketGet"

    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, login, user_type = auth
    is_customer = user_type == _CUSTOMER_USER_TYPE

    # TicketGet.pm:264-297
    ticket_ids_raw = data.get("TicketID")
    if not perl_truthy(ticket_ids_raw):
        return _err(f"{op}.MissingParameter", f"{op}: TicketID parameter is missing!")
    parsed_ids = _gi_ticket_id_tokens(ticket_ids_raw, split_pattern=r"\s*,\s*")
    if parsed_ids is None:
        return _err(f"{op}.WrongStructure", f"{op}: Structure for TicketID is not correct!")
    if not all(re.fullmatch(r"[1-9][0-9]*", t) for t in parsed_ids):
        return _err(f"{op}.WrongStructure", f"{op}: Invalid ticket ID parameter(s) found!")
    ticket_ids = [int(t) for t in parsed_ids]

    all_articles = perl_truthy(data.get("AllArticles"))
    with_attachments = perl_truthy(data.get("Attachments"))
    with_dynamic_fields = perl_truthy(data.get("DynamicFields"))
    extended = perl_truthy(data.get("Extended"))
    get_attachment_contents = data.get("GetAttachmentContents")
    get_attachment_contents = (
        True if get_attachment_contents is None else perl_truthy(get_attachment_contents)
    )
    article_order = str(data.get("ArticleOrder") or "ASC")
    try:
        article_limit = int(data.get("ArticleLimit") or 0)
    except (ValueError, TypeError):
        article_limit = 0
    sender_type_filter = data.get("ArticleSenderType")
    if sender_type_filter is not None:
        sender_type_filter = [str(s) for s in _to_list(sender_type_filter)]

    customer_company = ""
    allowed_groups: set[int] = set()
    if is_customer:
        customer_company = await _load_customer_company_id(session, login)
    else:
        pe = PermissionEngine(session)
        allowed_groups = set(await pe.groups_for_permission(user_id, "ro"))

    html_body_as_attachment = perl_truthy(data.get("HTMLBodyAsAttachment"))
    now = datetime.now(UTC).replace(tzinfo=None)

    # TicketGet.pm checks every requested ticket first; the first one the caller may not
    # read (unknown ids included) fails the whole call.
    for tid in ticket_ids:
        if tid > _MAX_BIGINT:  # no such ticket; never bound (column is BIGINT)
            return _err(f"{op}.AccessDenied", f"{op}: User does not have access to the ticket!")
        q_group = (
            await session.execute(
                text(
                    "SELECT q.group_id FROM ticket t JOIN queue q ON q.id = t.queue_id"
                    " WHERE t.id = :tid"
                ),
                {"tid": tid},
            )
        ).scalar_one_or_none()
        if q_group is None:
            allowed = False
        elif is_customer:
            allowed = await customer_can_access_ticket(
                session, login=login, customer_id=customer_company, ticket_id=tid
            )
        else:
            allowed = q_group in allowed_groups
        if not allowed:
            return _err(f"{op}.AccessDenied", f"{op}: User does not have access to the ticket!")

    ctx = await load_ticket_get_context(
        session, dynamic_fields=with_dynamic_fields, articles=all_articles
    )
    tickets_out: list[dict[str, Any]] = []
    for tid in ticket_ids:
        ticket_dict = await build_ticket_entry(
            session,
            tid,
            dynamic_fields=with_dynamic_fields,
            extended=extended,
            now=now,
            ctx=ctx,
        )
        if ticket_dict is None:
            continue

        if all_articles:
            articles = await build_article_entries(
                session,
                tid,
                customer_only=is_customer,
                sender_types=sender_type_filter,
                order=article_order,
                limit=article_limit,
                dynamic_fields=with_dynamic_fields,
                attachments=with_attachments,
                get_attachment_contents=get_attachment_contents,
                html_body_as_attachment=html_body_as_attachment,
                ctx=ctx,
            )
            if articles:
                ticket_dict["Article"] = articles

        tickets_out.append(ticket_dict)

    if not tickets_out:
        return _err(f"{op}.AccessDenied", f"{op}: No access or tickets not found!")

    return {"Ticket": tickets_out}


# ---------------------------------------------------------------------------
# TicketSearch
# ---------------------------------------------------------------------------


async def op_ticket_search(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """TicketSearch operation.

    Supports core Znuny TicketSearch filters: TicketNumber, Title, Queues,
    States, StateType (string or list), Priorities, Types, Locks, Services,
    SLAs, OwnerIDs/ResponsibleIDs/WatchUserIDs, Customer*, TicketID, Created*
    (NewTicket history row), ticket create/change/last-change/close/pending/escalation
    time windows (see ``search_filters``), MIMEBase_* / Fulltext article filters,
    SortBy/OrderBy, UseSubQueues, SearchInArchive, DynamicField_X (Equals/Like/
    comparison ops). No hit answers ``{}`` like Znuny (no ``TicketID`` key).

    ``Result`` (COUNT/ARRAY), ticket/article flag filters and mention filters are not
    GenericInterface parameters in Znuny (the operation always uses ``Result => ARRAY``
    and its ``_GetParams`` whitelist drops them), so they are ignored here as well.
    """
    op = "TicketSearch"

    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, login, user_type = auth
    is_customer = user_type == _CUSTOMER_USER_TYPE

    # an id outside its column width can never match: no match, never bound
    data, no_id_match = sanitize_id_filters(data)
    if no_id_match:
        return {}

    conditions: list[str] = []
    params: dict[str, Any] = {}
    joins: list[str] = []
    join_set: set[str] = set()

    def _add_join(sql: str, key: str) -> None:
        if key not in join_set:
            join_set.add(key)
            joins.append(sql)

    # --- permission / queue scope ---
    if is_customer:
        company_id = await _load_customer_company_id(session, login)
        scope = await customer_ticket_scope_filter(session, login=login, customer_id=company_id)
        owned_ids = set((await session.execute(select(Ticket.id).where(scope))).scalars().all())
        if not owned_ids:
            return {}
        o_list = list(owned_ids)
        o_ph = ",".join(f":own{i}" for i in range(len(o_list)))
        conditions.append(f"t.id IN ({o_ph})")
        for i, oid in enumerate(o_list):
            params[f"own{i}"] = oid
        req_queues: set[int] = set()
        if data.get("QueueIDs"):
            for qid in _to_list(data["QueueIDs"]):
                req_queues.add(int(qid))
        if data.get("Queues"):
            for qname in _to_list(data["Queues"]):
                row = (
                    await session.execute(select(Queue.id).where(Queue.name == qname))
                ).scalar_one_or_none()
                if row:
                    req_queues.add(int(row))
        if req_queues:
            q_list = list(req_queues)
            placeholders = ",".join(f":q{i}" for i in range(len(q_list)))
            conditions.append(f"t.queue_id IN ({placeholders})")
            for i, qid in enumerate(q_list):
                params[f"q{i}"] = qid
    else:
        pe = PermissionEngine(session)
        allowed_groups = await pe.groups_for_permission(user_id, "ro")
        if not allowed_groups:
            return {}

        allowed_queue_rows = (
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
        allowed_queues: set[int] = set(allowed_queue_rows)
        queue_filter: set[int] = set(allowed_queues)
        req_queues = set()
        if data.get("QueueIDs"):
            for qid in _to_list(data["QueueIDs"]):
                req_queues.add(int(qid))
        if data.get("Queues"):
            for qname in _to_list(data["Queues"]):
                row = (
                    await session.execute(select(Queue.id).where(Queue.name == qname))
                ).scalar_one_or_none()
                if row:
                    req_queues.add(int(row))
                    # UseSubQueues: include child queues (name prefix "Parent::")
                    if data.get("UseSubQueues"):
                        kids = (
                            (
                                await session.execute(
                                    select(Queue.id).where(
                                        Queue.name.like(f"{qname}::%"),
                                        Queue.valid_id == 1,
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                        req_queues.update(int(k) for k in kids)
        if req_queues:
            queue_filter = queue_filter & req_queues

        if not queue_filter:
            return {}

        q_list = list(queue_filter)
        placeholders = ",".join(f":q{i}" for i in range(len(q_list)))
        conditions.append(f"t.queue_id IN ({placeholders})")
        for i, qid in enumerate(q_list):
            params[f"q{i}"] = qid

    # TicketNumber (string or list)
    if tn := data.get("TicketNumber"):
        tn_list = _to_list(tn)
        parts = []
        for i, v in enumerate(tn_list):
            parts.append(f"t.tn LIKE :tn{i}")
            params[f"tn{i}"] = _like_pattern(v)
        conditions.append("(" + " OR ".join(parts) + ")")

    # Title
    if title := data.get("Title"):
        title_list = _to_list(title)
        parts = []
        for i, v in enumerate(title_list):
            parts.append(f"t.title LIKE :title{i}")
            params[f"title{i}"] = _like_pattern(v)
        conditions.append("(" + " OR ".join(parts) + ")")

    # States
    state_ids: set[int] = set()
    if data.get("StateIDs"):
        for sid in _to_list(data["StateIDs"]):
            state_ids.add(int(sid))
    if data.get("States"):
        for sname in _to_list(data["States"]):
            row = (
                await session.execute(select(TicketState.id).where(TicketState.name == sname))
            ).scalar_one_or_none()
            if row:
                state_ids.add(int(row))

    # StateType: Znuny accepts string OR list
    state_type_raw = data.get("StateType")
    if state_type_raw is not None:
        type_names = (
            [state_type_raw]
            if isinstance(state_type_raw, str)
            else [str(x) for x in _to_list(state_type_raw)]
        )
        all_type_sids: set[int] = set()
        for st in type_names:
            all_type_sids |= set(await _state_ids_for_type(session, st))
        if state_ids:
            state_ids &= all_type_sids
        else:
            state_ids = all_type_sids
        if not state_ids:
            return {}

    # StateTypes (Tiqora extension) + StateTypeIDs
    if data.get("StateTypes"):
        all_type_sids = set()
        for st in _to_list(data["StateTypes"]):
            all_type_sids |= set(await _state_ids_for_type(session, str(st)))
        if state_ids:
            state_ids &= all_type_sids
        else:
            state_ids = all_type_sids
        if not state_ids:
            return {}
    if data.get("StateTypeIDs"):
        # Map type_ids → state ids
        type_id_list = [int(x) for x in _to_list(data["StateTypeIDs"])]
        sids = (
            (
                await session.execute(
                    select(TicketState.id).where(TicketState.type_id.in_(type_id_list))
                )
            )
            .scalars()
            .all()
        )
        type_sids = set(int(s) for s in sids)
        if state_ids:
            state_ids &= type_sids
        else:
            state_ids = type_sids
        if not state_ids:
            return {}

    if state_ids:
        s_list = list(state_ids)
        s_ph = ",".join(f":s{i}" for i in range(len(s_list)))
        conditions.append(f"t.ticket_state_id IN ({s_ph})")
        for i, sid in enumerate(s_list):
            params[f"s{i}"] = sid

    # Priorities
    prio_ids: set[int] = set()
    if data.get("PriorityIDs"):
        for pid in _to_list(data["PriorityIDs"]):
            prio_ids.add(int(pid))
    if data.get("Priorities"):
        for pname in _to_list(data["Priorities"]):
            row = (
                await session.execute(select(TicketPriority.id).where(TicketPriority.name == pname))
            ).scalar_one_or_none()
            if row:
                prio_ids.add(int(row))
    if prio_ids:
        p_list = list(prio_ids)
        p_ph = ",".join(f":p{i}" for i in range(len(p_list)))
        conditions.append(f"t.ticket_priority_id IN ({p_ph})")
        for i, pid in enumerate(p_list):
            params[f"p{i}"] = pid

    # Types
    type_ids: set[int] = set()
    if data.get("TypeIDs"):
        for tid in _to_list(data["TypeIDs"]):
            type_ids.add(int(tid))
    if data.get("Types"):
        for tname in _to_list(data["Types"]):
            type_row = (
                await session.execute(
                    text("SELECT id FROM ticket_type WHERE name = :n AND valid_id = 1 LIMIT 1"),
                    {"n": tname},
                )
            ).first()
            if type_row:
                type_ids.add(int(type_row[0]))
    if type_ids:
        tl = list(type_ids)
        ph = ",".join(f":ty{i}" for i in range(len(tl)))
        conditions.append(f"t.type_id IN ({ph})")
        for i, tid in enumerate(tl):
            params[f"ty{i}"] = tid

    # Locks
    lock_ids: set[int] = set()
    if data.get("LockIDs"):
        for lid in _to_list(data["LockIDs"]):
            lock_ids.add(int(lid))
    if data.get("Locks"):
        for lname in _to_list(data["Locks"]):
            lid = await _resolve_lock_id(session, str(lname), None)
            if lid is not None:
                lock_ids.add(lid)
    if lock_ids:
        ll = list(lock_ids)
        ph = ",".join(f":lk{i}" for i in range(len(ll)))
        conditions.append(f"t.ticket_lock_id IN ({ph})")
        for i, lid in enumerate(ll):
            params[f"lk{i}"] = lid

    # Services
    svc_ids: set[int] = set()
    if data.get("ServiceIDs"):
        for sid in _to_list(data["ServiceIDs"]):
            svc_ids.add(int(sid))
    if data.get("Services"):
        for sname in _to_list(data["Services"]):
            sid = await _resolve_service_id(session, str(sname), None)
            if sid is not None:
                svc_ids.add(sid)
    if svc_ids:
        sl = list(svc_ids)
        ph = ",".join(f":sv{i}" for i in range(len(sl)))
        conditions.append(f"t.service_id IN ({ph})")
        for i, sid in enumerate(sl):
            params[f"sv{i}"] = sid

    # SLAs
    sla_ids: set[int] = set()
    if data.get("SLAIDs"):
        for sid in _to_list(data["SLAIDs"]):
            sla_ids.add(int(sid))
    if data.get("SLAs"):
        for sname in _to_list(data["SLAs"]):
            sid = await _resolve_sla_id(session, str(sname), None)
            if sid is not None:
                sla_ids.add(sid)
    if sla_ids:
        sl = list(sla_ids)
        ph = ",".join(f":sla{i}" for i in range(len(sl)))
        conditions.append(f"t.sla_id IN ({ph})")
        for i, sid in enumerate(sl):
            params[f"sla{i}"] = sid

    # Owner / Responsible
    if data.get("OwnerIDs"):
        ol = [int(x) for x in _to_list(data["OwnerIDs"])]
        ph = ",".join(f":ow{i}" for i in range(len(ol)))
        conditions.append(f"t.user_id IN ({ph})")
        for i, oid in enumerate(ol):
            params[f"ow{i}"] = oid
    if data.get("ResponsibleIDs"):
        rl = [int(x) for x in _to_list(data["ResponsibleIDs"])]
        ph = ",".join(f":rs{i}" for i in range(len(rl)))
        conditions.append(f"t.responsible_user_id IN ({ph})")
        for i, rid in enumerate(rl):
            params[f"rs{i}"] = rid

    # WatchUserIDs
    if data.get("WatchUserIDs"):
        wl = [int(x) for x in _to_list(data["WatchUserIDs"])]
        ph = ",".join(f":wu{i}" for i in range(len(wl)))
        _add_join(
            f"JOIN ticket_watcher tw ON tw.ticket_id = t.id AND tw.user_id IN ({ph})",
            "watcher",
        )
        for i, wid in enumerate(wl):
            params[f"wu{i}"] = wid

    # CreatedUserIDs
    if data.get("CreatedUserIDs"):
        cl = [int(x) for x in _to_list(data["CreatedUserIDs"])]
        ph = ",".join(f":cuu{i}" for i in range(len(cl)))
        conditions.append(f"t.create_by IN ({ph})")
        for i, cid in enumerate(cl):
            params[f"cuu{i}"] = cid

    # CustomerUserLogin
    if cul := data.get("CustomerUserLogin"):
        cu_list = _to_list(cul)
        cu_ph = ",".join(f":cu{i}" for i in range(len(cu_list)))
        conditions.append(f"t.customer_user_id IN ({cu_ph})")
        for i, cu in enumerate(cu_list):
            params[f"cu{i}"] = cu

    # CustomerID / CustomerIDRaw (list or string)
    if data.get("CustomerIDRaw") is not None:
        cid_list = _to_list(data["CustomerIDRaw"])
        ph = ",".join(f":cidr{i}" for i in range(len(cid_list)))
        conditions.append(f"t.customer_id IN ({ph})")
        for i, c in enumerate(cid_list):
            params[f"cidr{i}"] = c
    elif data.get("CustomerID") is not None:
        cid_list = _to_list(data["CustomerID"])
        ph = ",".join(f":cid{i}" for i in range(len(cid_list)))
        conditions.append(f"t.customer_id IN ({ph})")
        for i, c in enumerate(cid_list):
            params[f"cid{i}"] = c

    # Archive
    archive = data.get("SearchInArchive") or data.get("ArchiveFlags")
    if archive == "ArchivedTickets":
        conditions.append("t.archive_flag = 1")
    elif archive == "AllTickets":
        pass  # no filter
    else:
        # default: non-archived only (Znuny Ticket::ArchiveSystem behaviour)
        if data.get("ArchiveFlags") is None and data.get("SearchInArchive") is None:
            conditions.append("t.archive_flag = 0")

    # Ticket time windows (create / change / close / pending / escalation), Znuny semantics
    now_naive = datetime.now(tz=UTC).replace(tzinfo=None)
    time_data = create_time_settings(data, now=now_naive)
    if not time_filters(time_data, conditions, params, joins, now=now_naive):
        return {}
    await created_filters(session, data, conditions, params)

    # TicketID: limit the search to one id or a list of ids
    if (tid_raw := data.get("TicketID")) not in (None, "", []):
        try:
            tid_list = [int(x) for x in _to_list(tid_raw)]
        except (TypeError, ValueError):
            return {}
        ph = ",".join(f":tid{i}" for i in range(len(tid_list)))
        conditions.append(f"t.id IN ({ph})")
        for i, tid_val in enumerate(tid_list):
            params[f"tid{i}"] = tid_val

    # MIMEBase / Fulltext / ContentSearch article filters (see search_filters)
    await article_text_filters(session, SysConfig(session), data, conditions, params, _add_join)

    # Article create-time filters
    if any(
        data.get(k)
        for k in (
            "ArticleCreateTimeNewerMinutes",
            "ArticleCreateTimeOlderMinutes",
            "ArticleCreateTimeNewerDate",
            "ArticleCreateTimeOlderDate",
        )
    ):
        _add_join("JOIN article a_ct ON a_ct.ticket_id = t.id", "article_ct")
        if m := data.get("ArticleCreateTimeNewerMinutes"):
            conditions.append("a_ct.create_time >= :act_nm")
            params["act_nm"] = now_naive - timedelta(minutes=perl_int(m))
        if m := data.get("ArticleCreateTimeOlderMinutes"):
            conditions.append("a_ct.create_time <= :act_om")
            params["act_om"] = now_naive - timedelta(minutes=perl_int(m))
        if m := data.get("ArticleCreateTimeNewerDate"):
            conditions.append("a_ct.create_time >= :act_nd")
            params["act_nd"] = m
        if m := data.get("ArticleCreateTimeOlderDate"):
            conditions.append("a_ct.create_time <= :act_od")
            params["act_od"] = m

    # DynamicField_X filters (Equals, Like, GreaterThan, SmallerThan, …)
    df_idx = 0
    for key, val in data.items():
        m = re.match(r"^DynamicField_([a-zA-Z0-9]+)$", key)
        if not m:
            continue
        field_name = m.group(1)
        if not isinstance(val, dict):
            continue
        df_row = (
            await session.execute(
                text(
                    "SELECT id FROM dynamic_field WHERE name = :n"
                    " AND object_type = 'Ticket' AND valid_id = 1 LIMIT 1"
                ),
                {"n": field_name},
            )
        ).first()
        if df_row is None:
            continue
        fid = int(df_row[0])
        alias = f"dfv{df_idx}"
        df_idx += 1
        op_map = {
            "Equals": "=",
            "Like": "LIKE",
            "GreaterThan": ">",
            "GreaterThanEquals": ">=",
            "SmallerThan": "<",
            "SmallerThanEquals": "<=",
        }
        applied = False
        for op_name, sql_op in op_map.items():
            if op_name not in val or val[op_name] is None:
                continue
            raw_v = val[op_name]
            if sql_op == "LIKE":
                params[f"{alias}_v"] = _like_pattern(raw_v)
                col_expr = f"{alias}.value_text"
            else:
                params[f"{alias}_v"] = str(raw_v)
                # Prefer numeric compare when both sides look numeric
                try:
                    float(raw_v)
                    col_expr = f"CAST({alias}.value_text AS DECIMAL(20,4))"
                    params[f"{alias}_v"] = float(raw_v)
                except (ValueError, TypeError):
                    col_expr = f"{alias}.value_text"
            joins.append(
                f"JOIN dynamic_field_value {alias}"
                f" ON {alias}.object_id = t.id"
                f" AND {alias}.field_id = {fid}"
                f" AND {col_expr} {sql_op} :{alias}_v"
            )
            applied = True
            break
        if not applied:
            # empty filter dict — ignore
            pass

    # SortBy / OrderBy
    sort_map = {
        "Age": "t.create_time",
        "Created": "t.create_time",
        "Changed": "t.change_time",
        "TicketNumber": "t.tn",
        "Title": "t.title",
        "Queue": "t.queue_id",
        "Priority": "t.ticket_priority_id",
        "State": "t.ticket_state_id",
        "Owner": "t.user_id",
        "Responsible": "t.responsible_user_id",
        "CustomerID": "t.customer_id",
        "Type": "t.type_id",
        "Lock": "t.ticket_lock_id",
        "Service": "t.service_id",
        "SLA": "t.sla_id",
        "PendingTime": "t.until_time",
        "EscalationTime": "t.escalation_time",
        "EscalationUpdateTime": "t.escalation_update_time",
        "EscalationResponseTime": "t.escalation_response_time",
        "EscalationSolutionTime": "t.escalation_solution_time",
    }
    # tickets without pending/escalation time store 0: always sort them last (Znuny)
    zero_last = {
        "PendingTime",
        "EscalationTime",
        "EscalationUpdateTime",
        "EscalationResponseTime",
        "EscalationSolutionTime",
    }
    sort_list = _to_list(data.get("SortBy") or "Age")
    order_list = _to_list(data.get("OrderBy") or "Down")
    order_parts: list[str] = []
    select_cols: list[str] = []  # PostgreSQL: ORDER BY expressions must be in the DISTINCT list
    for i, sort_key in enumerate(sort_list):
        col = sort_map.get(str(sort_key), "t.create_time")
        direction = str(order_list[i] if i < len(order_list) else order_list[-1]).lower()
        dir_sql = "ASC" if direction in ("up", "asc") else "DESC"
        if str(sort_key) in zero_last:
            flag = f"zero_{len(select_cols)}"
            select_cols.append(f"(CASE WHEN {col} = 0 THEN 1 ELSE 0 END) AS {flag}")
            order_parts.append(f"{flag} ASC")
        order_parts.append(f"{col} {dir_sql}")
        if col not in select_cols:
            select_cols.append(col)
    if not order_parts:
        order_parts = ["t.create_time DESC"]
        select_cols = ["t.create_time"]
    order_sql = ", ".join(order_parts)
    order_cols = select_cols

    limit_raw = data.get("Limit") or 500
    try:
        limit = min(int(limit_raw), 2000)
    except (ValueError, TypeError):
        limit = 500

    joins_sql = " ".join(joins)
    where_sql = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        # PostgreSQL requires every ORDER BY expression in a SELECT DISTINCT list
        f"SELECT DISTINCT t.id{''.join(', ' + c for c in order_cols if c != 't.id')}"
        f" FROM ticket t {joins_sql}"
        f" WHERE {where_sql}"
        f" ORDER BY {order_sql}"
        f" LIMIT {limit}"
    )

    rows = (await session.execute(text(sql), params)).fetchall()
    ticket_ids = [int(r[0]) for r in rows]

    if not ticket_ids:
        return {}
    return {"TicketID": ticket_ids}


def _to_list(val: Any) -> list[Any]:
    """Coerce a scalar or list value to a list."""
    if isinstance(val, (list, tuple)):
        return list(val)
    return [val]


# ---------------------------------------------------------------------------
# TicketHistoryGet
# ---------------------------------------------------------------------------


async def op_ticket_history_get(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """TicketHistoryGet — agent-only history dump for one or more tickets."""
    op = "TicketHistoryGet"

    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, _login, user_type = auth
    if user_type != _AGENT_USER_TYPE:
        return _err(f"{op}.AuthFail", f"{op}: User needs to an Agent!")

    # TicketHistoryGet.pm:120-143
    ticket_ids_raw = data.get("TicketID")
    if not perl_truthy(ticket_ids_raw):
        return _err(f"{op}.MissingParameter", f"{op}: TicketID parameter is missing!")
    tokens = _gi_ticket_id_tokens(ticket_ids_raw, split_pattern=",")
    if tokens is None:
        return _err(f"{op}.WrongStructure", f"{op}: Structure for TicketID is not correct!")

    pe = PermissionEngine(session)
    allowed_groups = set(await pe.groups_for_permission(user_id, "ro"))

    histories: list[dict[str, Any]] = []
    for tid_token in tokens:
        # a non-numeric id has no access, like CheckAccessPermissions (TicketHistoryGet.pm:146-160)
        if not re.fullmatch(r"[0-9]{1,18}", tid_token):
            return _err(
                f"{op}.AccessDenied",
                f"{op}: User does not have access to the ticket {tid_token}!",
            )
        tid = int(tid_token)
        t_row = (
            await session.execute(
                text(
                    "SELECT t.queue_id, q.group_id FROM ticket t"
                    " JOIN queue q ON q.id = t.queue_id WHERE t.id = :tid LIMIT 1"
                ),
                {"tid": tid},
            )
        ).first()
        if t_row is None:
            return _err(
                f"{op}.AccessDenied",
                f"{op}: User does not have access to the ticket {tid}!",
            )
        if int(t_row[1]) not in allowed_groups:
            return _err(
                f"{op}.AccessDenied",
                f"{op}: User does not have access to the ticket {tid}!",
            )

        rows = (
            (
                await session.execute(
                    text(
                        "SELECT th.ticket_id, th.article_id, th.name, th.create_by, th.create_time,"
                        " tht.name AS history_type, th.queue_id, th.owner_id, th.priority_id,"
                        " th.state_id, th.history_type_id, th.type_id"
                        " FROM ticket_history th"
                        " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                        " WHERE th.ticket_id = :tid"
                        " ORDER BY th.id ASC"
                    ),
                    {"tid": tid},
                )
            )
            .mappings()
            .all()
        )

        lines: list[dict[str, Any]] = []
        for r in rows:
            lines.append(
                {
                    "TicketID": r["ticket_id"],
                    "ArticleID": r["article_id"],
                    "Name": r["name"],
                    "CreateBy": r["create_by"],
                    "CreateTime": znuny_ts(r["create_time"]),
                    "HistoryType": r["history_type"],
                    "QueueID": r["queue_id"],
                    "OwnerID": r["owner_id"],
                    "PriorityID": r["priority_id"],
                    "StateID": r["state_id"],
                    "HistoryTypeID": r["history_type_id"],
                    "TypeID": r["type_id"],
                }
            )
        histories.append({"TicketID": tid, "History": lines})

    if not histories:
        return _err(f"{op}.NotTicketData", f"{op}: Could not get Ticket history data")

    return {"TicketHistory": histories}


# ---------------------------------------------------------------------------
# TimeAccountingGet
# ---------------------------------------------------------------------------


def _parse_znuny_datetime(raw: Any) -> datetime | None:
    value = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)  # noqa: DTZ007 - Znuny timestamps are naive UTC
        except ValueError:
            continue
    try:  # ISO variants ("2024-06-01T12:00:00", "...Z", "...+02:00")
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


async def op_time_accounting_get(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """TimeAccountingGet — accounted time rows for a user in a date range.

    Requires agent membership in group ``timeaccounting_webservice`` (rw).
    """
    op = "TimeAccountingGet"

    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, _login, user_type = auth
    if user_type != _AGENT_USER_TYPE:
        return _err(f"{op}.AuthFail", f"{op}: Authentication failed!")

    if not await _agent_in_group(session, user_id, "timeaccounting_webservice"):
        return _err(f"{op}.NoPermission", f"{op}: No permission!")

    for needed in ("TimeAccountingUserLogin", "TimeAccountingStart", "TimeAccountingEnd"):
        if not data.get(needed):
            return _err(f"{op}.MissingParameter", f"{op}: {needed} parameter is missing!")

    target_login = str(data["TimeAccountingUserLogin"])
    target_id = await _resolve_user_id(session, target_login, None)
    if target_id is None:
        return {"TimeAccountingResult": []}

    # PostgreSQL (asyncpg) refuses strings for timestamp bounds: bind datetimes on both dialects.
    start = _parse_znuny_datetime(data["TimeAccountingStart"])
    end = _parse_znuny_datetime(data["TimeAccountingEnd"])
    if start is None or end is None:
        return {}

    rows = (
        (
            await session.execute(
                text(
                    "SELECT t.id AS ticket_id, t.tn, t.customer_id, t.title,"
                    " ta.time_unit, ta.create_time"
                    " FROM time_accounting ta"
                    " JOIN ticket t ON t.id = ta.ticket_id"
                    " WHERE ta.create_by = :uid"
                    " AND ta.create_time BETWEEN :start AND :end"
                    " ORDER BY ta.create_time ASC, t.id ASC, ta.id ASC"
                ),
                {"uid": target_id, "start": start, "end": end},
            )
        )
        .mappings()
        .all()
    )

    # Znuny reports the queue the ticket was in when the time was booked (no TicketID key).
    # The history of each ticket is read once, not once per accounting row.
    moments: dict[int, list[datetime]] = {}
    for r in rows:
        moments.setdefault(int(r["ticket_id"]), []).append(r["create_time"])
    queues = {tid: await queues_at(session, tid, at) for tid, at in moments.items()}
    entries = [
        {
            "TicketNumber": r["tn"],
            "TicketCustomerID": r["customer_id"],
            "TicketTitle": r["title"],
            "TimeUnit": perl_str(znuny_num(r["time_unit"])),
            "Created": znuny_ts(r["create_time"]),
            "Queue": queues[int(r["ticket_id"])][r["create_time"]],
        }
        for r in rows
    ]
    out: dict[str, Any] = {}
    if entries:
        out["TimeAccountingResult"] = entries
    return out


# ---------------------------------------------------------------------------
# OutOfOffice
# ---------------------------------------------------------------------------


def _string_with_data(value: Any) -> bool:
    """``IsStringWithData``: a defined, non-empty plain scalar."""
    return perl_is_string(value) and perl_str(value) != ""


def _like_escape(value: str) -> str:
    """``Quote($Value, 'Like')``: ``_`` is literal (backslash = LIKE escape on both dialects)."""
    return value.replace("\\", "\\\\").replace("_", "\\_")


async def _out_of_office_user_ids(session: AsyncSession, entry: dict[str, Any]) -> list[int]:
    """User IDs ``User::UserSearch`` finds for an OutOfOffice entry (``Valid`` users only).

    Znuny checks ``Search`` (``UserSearch``), then ``PostMasterSearch`` (``UserEmail``), then
    ``UserLogin`` - the first one given decides. The caller needs exactly one id.

    * ``UserSearch``: whitespace -> ``+`` (OutOfOffice.pm:196), ``QueryCondition`` over
      login / first_name / last_name: every word must match one of the columns, case
      insensitively, as a whole value; ``*`` is the wildcard. Znuny's ``|``, ``AND``/``OR``,
      ``!`` and parentheses are not supported (the words are AND-ed).
    * ``UserEmail``: ``SearchPreferences(Key => 'UserEmail')`` - a LIKE on the preference,
      ``LOWER()`` on both sides where the database is case sensitive (PostgreSQL); on
      MariaDB the BLOB column compares byte-wise, exactly like Znuny there.
    * ``UserLogin``: ``LOWER(login) LIKE lower(value)`` with ``*`` -> ``%``.
    """
    search = entry.get("UserSearch")
    if _string_with_data(search):
        words = [w for w in re.split(r"[\s+]+", perl_str(search)) if w]
        if not words:
            return []
        conditions: list[str] = []
        params: dict[str, Any] = {}
        for i, word in enumerate(words):
            params[f"w{i}"] = _like_escape(word.lower()).replace("*", "%")
            conditions.append(
                f"(LOWER(login) LIKE :w{i} OR LOWER(first_name) LIKE :w{i}"
                f" OR LOWER(last_name) LIKE :w{i})"
            )
        rows = await session.execute(
            text(f"SELECT id FROM users WHERE {' AND '.join(conditions)} AND valid_id = 1"),
            params,
        )
        return [int(r[0]) for r in rows.all()]

    email = entry.get("UserEmail")
    if _string_with_data(email):
        bind = session.get_bind()
        postgres = bind is not None and bind.dialect.name == "postgresql"
        cond = (
            "LOWER(p.preferences_value) LIKE LOWER(:e)"
            if postgres
            else "p.preferences_value LIKE :e"
        )
        rows = await session.execute(
            text(
                "SELECT p.user_id FROM user_preferences p WHERE p.preferences_key = 'UserEmail'"
                f" AND {cond}"
            ),
            {"e": _pref_param(session, perl_str(email))},
        )
        ids = sorted({int(r[0]) for r in rows.all()})
        if not ids:
            return []
        # UserSearch returns every match as soon as one of them is a user GetUserData finds
        # (OutOfOffice passes no Valid, so validity is not checked on this path)
        known = await session.execute(
            text("SELECT id FROM users WHERE id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": ids},
        )
        return ids if known.first() is not None else []

    login = perl_str(entry.get("UserLogin"))
    rows = await session.execute(
        text("SELECT id FROM users WHERE LOWER(login) LIKE :l AND valid_id = 1"),
        {"l": _like_escape(login.lower()).replace("*", "%")},
    )
    return [int(r[0]) for r in rows.all()]


async def op_out_of_office(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
    *,
    request: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """OutOfOffice — set agent out-of-office preferences (admin group only)."""
    op = "OutOfOffice"

    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, _login, user_type = auth
    if user_type != _AGENT_USER_TYPE:
        return _err(f"{op}.AuthFail", f"{op}: User needs to be an agent.")
    if not await _agent_in_group(session, user_id, "admin"):
        return _err(f"{op}.AuthFail", f"{op}: User needs to be in group admin.")

    # Same result as a webservice with `MappingInbound: {Type: OutOfOffice}`.
    if (
        data.get("OutOfOfficeEntriesCSVString") is not None
        and data.get("OutOfOfficeEntries") is None
    ):
        try:
            data = await asyncio.to_thread(OutOfOfficeMapper().map, data)
        except MappingError as exc:
            return _err(f"{op}.WrongInputStructure", str(exc))

    entries = data.get("OutOfOfficeEntries")
    if not isinstance(entries, list) or not entries:
        # OutOfOffice.pm:149-170: Success => 0 -> the provider answers with a fault
        raise OperationFault(
            "Payload does not contain out-of-office entries or they could not be parsed."
        )

    written = False
    for entry in entries:
        # OutOfOffice.pm:174-192: a hash with at least one of the three lookup keys
        if (
            not isinstance(entry, dict)
            or not entry
            or not any(
                _string_with_data(entry.get(k)) for k in ("UserEmail", "UserSearch", "UserLogin")
            )
        ):
            continue
        user_ids = await _out_of_office_user_ids(session, entry)
        if len(user_ids) != 1:
            continue  # OutOfOffice.pm:207-218: "Error while looking up unique user ID."
        target_id = user_ids[0]

        pref: dict[str, str] = {}
        # OutOfOffice.pm:228-236: unanchored match; the first missing / unparsable part ends
        # the loop (`last DATEPART`), so an invalid StartDate also drops EndDate
        for part in ("Start", "End"):
            date_s = entry.get(f"{part}Date")
            if not _string_with_data(date_s):
                break
            match = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(date_s))
            if match is None:
                break
            pref[f"OutOfOffice{part}Year"] = match.group(1)
            pref[f"OutOfOffice{part}Month"] = match.group(2)
            pref[f"OutOfOffice{part}Day"] = match.group(3)
        # `$Entry->{OutOfOffice} ? 1 : 0` - Perl truthiness ("0" and "" are off)
        pref["OutOfOffice"] = "1" if perl_truthy(entry.get("OutOfOffice")) else "0"

        for k, v in pref.items():
            await _set_user_preference(session, target_id, k, v)
        written = True

    if written:
        # User.pm SetPreferences -> _UserCacheClear: Znuny must not keep serving the old
        # out-of-office state from its User cache
        await invalidate_cache_type(session, "User")
    await session.commit()
    # OutOfOffice.pm:260-264: the answer carries no per-entry detail
    return {"Success": 1}


__all__ = [
    "op_out_of_office",
    "op_session_create",
    "op_session_get",
    "op_session_remove",
    "op_ticket_create",
    "op_ticket_get",
    "op_ticket_history_get",
    "op_ticket_search",
    "op_ticket_update",
    "op_time_accounting_get",
]
