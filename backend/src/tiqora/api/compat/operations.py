"""Znuny GenericInterface-compatible operation handlers.

Translates Znuny REST GenericInterface wire format to Tiqora domain services.
Ports request/response semantics from:
  - Kernel/GenericInterface/Operation/Ticket/{TicketCreate,TicketUpdate,TicketGet,TicketSearch}.pm
  - Kernel/GenericInterface/Operation/Session/SessionCreate.pm
  - Kernel/GenericInterface/Operation/Common.pm
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings, get_settings
from tiqora.db.legacy.article import Article, ArticleDataMimeAttachment
from tiqora.db.legacy.article import ArticleDataMime as ArticleDataMimeModel
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
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
    assign_owner,
    assign_responsible,
    change_priority,
    change_state,
    change_title,
    create_ticket,
    lock_ticket,
    move_queue,
    set_customer,
    unlock_ticket,
    update_dynamic_field,
)
from tiqora.domain.totp import TOTPService
from tiqora.permissions.engine import PermissionEngine
from tiqora.security.ratelimit import AuthRateLimiter, client_ip
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
        return (_CUSTOMER_SENTINEL_USER_ID, user_login, _CUSTOMER_USER_TYPE)
    if not user_id_s:
        return None
    try:
        return (int(user_id_s), user_login, user_type or _AGENT_USER_TYPE)
    except ValueError:
        return None


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
            from datetime import timedelta

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


async def _set_user_preference(session: AsyncSession, user_id: int, key: str, value: str) -> None:
    """Upsert a user_preferences row (value stored as UTF-8 bytes)."""
    raw = value.encode("utf-8")
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
# Article payload builder
# ---------------------------------------------------------------------------


def _build_article_in(art_data: dict[str, Any], user_type: str) -> ArticleIn:
    """Build ArticleIn from GenericInterface article sub-object.

    Znuny defaults (TicketCreate.pm lines 547-554):
    - CommunicationChannel defaults to 'Internal' if not given
    - IsVisibleForCustomer defaults from config (usually 1 for agent)
    - SenderType defaults to 'agent' for User, 'customer' for Customer

    For TicketUpdate notes (not email/phone), IsVisibleForCustomer should
    default to 0 (internal) — per the Znuny TicketUpdate op behaviour.
    """
    channel_raw = (art_data.get("CommunicationChannel") or "Internal").strip()
    # Map Znuny channel names to our internal names
    channel_map = {
        "Internal": "note",
        "Email": "email",
        "Phone": "phone",
        "Chat": "note",
    }
    channel = channel_map.get(channel_raw, "note")

    # IsVisibleForCustomer: explicit value wins; fallback: 0 (internal) for notes
    is_visible_raw = art_data.get("IsVisibleForCustomer")
    if is_visible_raw is not None:
        is_visible = bool(int(is_visible_raw))
    else:
        # For email channel from agent, default visible; for notes default internal
        is_visible = channel == "email" and user_type == _AGENT_USER_TYPE

    sender_type_raw = art_data.get("SenderType")
    if not sender_type_raw:
        sender_type = "agent" if user_type == _AGENT_USER_TYPE else "customer"
    else:
        sender_type = sender_type_raw.lower()

    # Attachments: list of {Filename, ContentType, Content(base64)}
    attachments: list[tuple[str, str, bytes]] = []
    for att in art_data.get("Attachment") or []:
        if not isinstance(att, dict):
            continue
        try:
            content_b64 = att.get("Content") or ""
            content_bytes = base64.b64decode(content_b64)
        except Exception:
            content_bytes = b""
        attachments.append(
            (
                att.get("Filename") or "attachment",
                att.get("ContentType") or "application/octet-stream",
                content_bytes,
            )
        )

    return ArticleIn(
        sender_type=sender_type,
        is_visible_for_customer=is_visible,
        subject=art_data.get("Subject") or "",
        body=art_data.get("Body") or "",
        content_type=art_data.get("ContentType") or "text/plain; charset=utf-8",
        from_address=art_data.get("From"),
        to_address=art_data.get("To"),
        cc=art_data.get("Cc"),
        bcc=art_data.get("Bcc"),
        message_id=art_data.get("MessageID"),
        in_reply_to=art_data.get("InReplyTo"),
        references=art_data.get("References"),
        channel=channel,
        attachments=attachments,
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

    Matches Znuny SessionGet: no auth params beyond SessionID; returns all
    session key/value pairs (UserPw / UserChallengeToken filtered).
    """
    op = "SessionGet"
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
        session_data: list[dict[str, Any]] = []
        for key, val, serialized in rows:
            k = str(key)
            if k in ("UserPw", "UserChallengeToken"):
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
        return _err(f"{op}.SessionInvalid", f"{op}: SessionID is Invalid!")
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

    owner_id = await _resolve_user_id(session, ticket.get("Owner"), ticket.get("OwnerID"))
    if owner_id is None:
        owner_id = PORTAL_SYSTEM_USER_ID if is_customer else 1

    responsible_id = await _resolve_user_id(
        session, ticket.get("Responsible"), ticket.get("ResponsibleID")
    )

    lock_id = await _resolve_lock_id(session, ticket.get("Lock"), ticket.get("LockID"))
    if lock_id is None:
        lock_id = 1  # unlock

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
            return _err(f"{op}.AccessDenied", f"{op}: No permission to create tickets in Queue!")
        customer_id_val = ticket.get("CustomerID")
        customer_user_val = ticket.get("CustomerUser")
        write_user_id = user_id

    # Dynamic fields
    dynamic_fields: dict[str, list[str]] = {}
    for df in data.get("DynamicField") or []:
        if isinstance(df, dict):
            fname = df.get("Name") or ""
            val = df.get("Value")
            if fname:
                dynamic_fields[fname] = [str(val)] if val is not None else []

    # Build optional article (single dict or first of list)
    article_in: ArticleIn | None = None
    art_data = data.get("Article")
    if isinstance(art_data, list) and art_data:
        art_data = art_data[0]
    if art_data and isinstance(art_data, dict):
        # MimeType + Charset → ContentType when ContentType omitted
        if not art_data.get("ContentType") and art_data.get("MimeType"):
            charset = art_data.get("Charset") or "utf-8"
            art_data = {
                **art_data,
                "ContentType": f"{art_data['MimeType']}; charset={charset}",
            }
        # Attachment may be a single dict
        att = art_data.get("Attachment")
        if isinstance(att, dict):
            art_data = {**art_data, "Attachment": [att]}
        article_in = _build_article_in(art_data, user_type)
        security = art_data.get("EmailSecurity")
        if isinstance(security, dict) and security:
            from tiqora.config import get_settings
            from tiqora.crypto.outbound import apply_email_security

            article_in = await apply_email_security(article_in, security, get_settings())

    ticket_in = TicketIn(
        title=title,
        queue_id=queue_id,
        state_id=state_id,
        priority_id=priority_id,
        owner_id=owner_id,
        lock_id=lock_id,
        responsible_id=responsible_id,
        customer_id=customer_id_val,
        customer_user_id=customer_user_val,
        type_id=type_id,
        service_id=service_id,
        sla_id=sla_id,
        dynamic_fields=dynamic_fields,
        article=article_in,
    )

    try:
        async with session.begin_nested():
            ticket_id = await create_ticket(
                session, session_factory, sysconfig, params=ticket_in, user_id=write_user_id
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
        await session.commit()
    except TicketAccessDenied:
        await session.rollback()
        return _err(f"{op}.AccessDenied", f"{op}: No permission!")
    except InvalidInput as e:
        await session.rollback()
        return _err(f"{op}.InvalidParameter", str(e))

    return {"TicketID": ticket_id, "TicketNumber": await _get_tn(session, ticket_id)}


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
        ticket_id = int(ticket_id_raw)
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
        if not art_data or not isinstance(art_data, dict):
            return _err(
                f"{op}.MissingParameter",
                f"{op}: Article is required for customer follow-up!",
            )
        try:
            async with session.begin_nested():
                article_in = _build_article_in(
                    {**art_data, "IsVisibleForCustomer": art_data.get("IsVisibleForCustomer", 1)},
                    user_type,
                )
                await add_article(
                    session,
                    ticket_id=ticket_id,
                    article=article_in,
                    user_id=PORTAL_SYSTEM_USER_ID,
                    sysconfig=sysconfig,
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
        return {"TicketID": ticket_id, "TicketNumber": await _get_tn(session, ticket_id)}

    pe = PermissionEngine(session)
    if not await pe.check(user_id, queue_id, "rw"):
        return _err(f"{op}.AccessDenied", f"{op}: No permission to update ticket!")

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

            # Dynamic fields
            for df in data.get("DynamicField") or []:
                if isinstance(df, dict):
                    fname = df.get("Name") or ""
                    val = df.get("Value")
                    if fname:
                        values = [str(val)] if val is not None else []
                        await update_dynamic_field(
                            session,
                            ticket_id=ticket_id,
                            field_name=fname,
                            values=values,
                            user_id=user_id,
                        )

            # Article
            art_data = data.get("Article")
            if isinstance(art_data, list) and art_data:
                art_data = art_data[0]
            if art_data and isinstance(art_data, dict):
                # TicketUpdate note: IsVisibleForCustomer defaults to 0 (internal)
                if art_data.get("IsVisibleForCustomer") is None:
                    art_data = {**art_data, "IsVisibleForCustomer": 0}
                if not art_data.get("ContentType") and art_data.get("MimeType"):
                    charset = art_data.get("Charset") or "utf-8"
                    art_data = {
                        **art_data,
                        "ContentType": f"{art_data['MimeType']}; charset={charset}",
                    }
                att = art_data.get("Attachment")
                if isinstance(att, dict):
                    art_data = {**art_data, "Attachment": [att]}
                article_in = _build_article_in(art_data, user_type)
                await add_article(
                    session,
                    ticket_id=ticket_id,
                    article=article_in,
                    user_id=user_id,
                    sysconfig=sysconfig,
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

    return {"TicketID": ticket_id, "TicketNumber": await _get_tn(session, ticket_id)}


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

    ticket_ids_raw = data.get("TicketID")
    if ticket_ids_raw is None:
        return _err(f"{op}.MissingParameter", f"{op}: TicketID is required!")

    # TicketID can be a single value, CSV string, or a list
    if isinstance(ticket_ids_raw, (list, tuple)):
        ticket_ids = [int(t) for t in ticket_ids_raw]
    elif isinstance(ticket_ids_raw, str) and "," in ticket_ids_raw:
        ticket_ids = [int(t.strip()) for t in ticket_ids_raw.split(",") if t.strip()]
    else:
        ticket_ids = [int(ticket_ids_raw)]

    all_articles = bool(int(data.get("AllArticles") or 0))
    with_attachments = bool(int(data.get("Attachments") or 0))
    with_dynamic_fields = bool(int(data.get("DynamicFields") or 0))
    extended = bool(int(data.get("Extended") or 0))
    get_attachment_contents = data.get("GetAttachmentContents")
    if get_attachment_contents is None:
        get_attachment_contents = 1
    get_attachment_contents = bool(int(get_attachment_contents))
    article_order = str(data.get("ArticleOrder") or "ASC").upper()
    if article_order not in ("ASC", "DESC"):
        article_order = "ASC"
    try:
        article_limit = int(data.get("ArticleLimit") or 0)
    except (ValueError, TypeError):
        article_limit = 0
    sender_type_filter = data.get("ArticleSenderType")
    if sender_type_filter is not None:
        sender_type_filter = [str(s).lower() for s in _to_list(sender_type_filter)]

    customer_company = ""
    allowed_groups: set[int] = set()
    if is_customer:
        customer_company = await _load_customer_company_id(session, login)
    else:
        pe = PermissionEngine(session)
        allowed_groups = set(await pe.groups_for_permission(user_id, "ro"))

    tickets_out: list[dict[str, Any]] = []
    for tid in ticket_ids:
        t = (
            (
                await session.execute(
                    text(
                        "SELECT t.*, ts.name as state_name, tp.name as priority_name,"
                        " q.name as queue_name, tst.name as state_type,"
                        " lt.name as lock_name,"
                        " u.login as owner_login,"
                        " ru.login as responsible_login,"
                        " tt.name as type_name,"
                        " svc.name as service_name,"
                        " sla.name as sla_name"
                        " FROM ticket t"
                        " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                        " JOIN ticket_priority tp ON tp.id = t.ticket_priority_id"
                        " JOIN queue q ON q.id = t.queue_id"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " LEFT JOIN ticket_lock_type lt ON lt.id = t.ticket_lock_id"
                        " LEFT JOIN users u ON u.id = t.user_id"
                        " LEFT JOIN users ru ON ru.id = t.responsible_user_id"
                        " LEFT JOIN ticket_type tt ON tt.id = t.type_id"
                        " LEFT JOIN service svc ON svc.id = t.service_id"
                        " LEFT JOIN sla ON sla.id = t.sla_id"
                        " WHERE t.id = :tid LIMIT 1"
                    ),
                    {"tid": tid},
                )
            )
            .mappings()
            .first()
        )
        if t is None:
            continue

        if is_customer:
            if not await customer_can_access_ticket(
                session, login=login, customer_id=customer_company, ticket_id=tid
            ):
                continue
        else:
            # Permission check via queue→group
            q_group = (
                await session.execute(select(Queue.group_id).where(Queue.id == int(t["queue_id"])))
            ).scalar_one_or_none()
            if q_group not in allowed_groups:
                continue

        ticket_dict: dict[str, Any] = {
            "TicketID": t["id"],
            "TicketNumber": t["tn"],
            "Title": t["title"],
            "QueueID": t["queue_id"],
            "Queue": t["queue_name"],
            "StateID": t["ticket_state_id"],
            "State": t["state_name"],
            "StateType": t["state_type"],
            "PriorityID": t["ticket_priority_id"],
            "Priority": t["priority_name"],
            "LockID": t["ticket_lock_id"],
            "Lock": t["lock_name"],
            "OwnerID": t["user_id"],
            "Owner": t["owner_login"],
            "ResponsibleID": t["responsible_user_id"],
            "Responsible": t["responsible_login"],
            "TypeID": t["type_id"],
            "Type": t["type_name"],
            "ServiceID": t["service_id"],
            "Service": t["service_name"],
            "SLAID": t["sla_id"],
            "SLA": t["sla_name"],
            "CustomerID": t["customer_id"],
            "CustomerUserID": t["customer_user_id"],
            "CreateTime": t["create_time"].isoformat() if t["create_time"] else None,
            "ChangeTime": t["change_time"].isoformat() if t["change_time"] else None,
            "ArchiveFlag": "n" if not t["archive_flag"] else "y",
            "UntilTime": t["until_time"] or 0,
        }

        if extended:
            ticket_dict["EscalationTime"] = t["escalation_time"] or 0
            ticket_dict["EscalationResponseTime"] = t["escalation_response_time"] or 0
            ticket_dict["EscalationUpdateTime"] = t["escalation_update_time"] or 0
            ticket_dict["EscalationSolutionTime"] = t["escalation_solution_time"] or 0
            # First response / closed timestamps from history when available
            fr = (
                await session.execute(
                    text(
                        "SELECT MIN(th.create_time) FROM ticket_history th"
                        " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                        " WHERE th.ticket_id = :tid"
                        " AND tht.name IN ('SendAnswer','EmailAgent','PhoneCallAgent','AddNote')"
                    ),
                    {"tid": tid},
                )
            ).first()
            if fr and fr[0]:
                ticket_dict["FirstResponse"] = fr[0].isoformat()
            closed = (
                await session.execute(
                    text(
                        "SELECT MIN(th.create_time) FROM ticket_history th"
                        " JOIN ticket_state ts ON ts.id = th.state_id"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                        " WHERE th.ticket_id = :tid AND LOWER(tst.name) LIKE 'closed%'"
                    ),
                    {"tid": tid},
                )
            ).first()
            if closed and closed[0]:
                ticket_dict["Closed"] = closed[0].isoformat()

        if with_dynamic_fields:
            ticket_dict["DynamicField"] = await _load_dynamic_fields_gi(session, int(t["id"]))

        if all_articles:
            ticket_dict["Article"] = await _load_articles_gi(
                session,
                int(t["id"]),
                with_attachments=with_attachments,
                get_attachment_contents=get_attachment_contents,
                article_order=article_order,
                article_limit=article_limit,
                sender_type_filter=sender_type_filter,
            )

        tickets_out.append(ticket_dict)

    if not tickets_out:
        return _err(f"{op}.AccessDenied", f"{op}: No access or tickets not found!")

    return {"Ticket": tickets_out}


async def _load_dynamic_fields_gi(session: AsyncSession, ticket_id: int) -> list[dict[str, Any]]:
    """Load dynamic fields in Znuny GI format."""
    df_rows = (
        (
            await session.execute(
                select(DynamicField).where(
                    DynamicField.object_type == "Ticket",
                    DynamicField.valid_id == 1,
                )
            )
        )
        .scalars()
        .all()
    )

    if not df_rows:
        return []

    field_by_id = {f.id: f for f in df_rows}
    values_rows = (
        (
            await session.execute(
                select(DynamicFieldValue).where(
                    DynamicFieldValue.object_id == ticket_id,
                    DynamicFieldValue.field_id.in_(field_by_id.keys()),
                )
            )
        )
        .scalars()
        .all()
    )

    grouped: dict[int, list[Any]] = {fid: [] for fid in field_by_id}
    for v in values_rows:
        val: Any
        if v.value_text is not None:
            val = v.value_text
        elif v.value_int is not None:
            val = v.value_int
        elif v.value_date is not None:
            val = v.value_date.isoformat()
        else:
            continue
        grouped.setdefault(v.field_id, []).append(val)

    out: list[dict[str, Any]] = []
    for fid, field in field_by_id.items():
        vals = grouped.get(fid, [])
        out.append({"Name": field.name, "Value": vals[0] if len(vals) == 1 else vals})
    return out


async def _load_articles_gi(
    session: AsyncSession,
    ticket_id: int,
    *,
    with_attachments: bool,
    get_attachment_contents: bool = True,
    article_order: str = "ASC",
    article_limit: int = 0,
    sender_type_filter: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Load articles in Znuny GI format."""
    order_clause = Article.id.desc() if article_order == "DESC" else Article.id.asc()
    art_rows = (
        (
            await session.execute(
                select(Article).where(Article.ticket_id == ticket_id).order_by(order_clause)
            )
        )
        .scalars()
        .all()
    )

    if not art_rows:
        return []

    # Map sender_type_id → name for filter + response
    st_rows = (await session.execute(text("SELECT id, name FROM article_sender_type"))).fetchall()
    st_name_by_id = {int(r[0]): str(r[1]).lower() for r in st_rows}

    if sender_type_filter:
        allowed = {s.lower() for s in sender_type_filter}
        art_rows = [
            a
            for a in art_rows
            if st_name_by_id.get(int(a.article_sender_type_id or 0), "") in allowed
        ]

    if article_limit and article_limit > 0:
        art_rows = list(art_rows)[:article_limit]

    if not art_rows:
        return []

    art_ids = [a.id for a in art_rows]
    mime_rows = (
        (
            await session.execute(
                select(ArticleDataMimeModel).where(ArticleDataMimeModel.article_id.in_(art_ids))
            )
        )
        .scalars()
        .all()
    )
    mime_by_id = {m.article_id: m for m in mime_rows}

    out: list[dict[str, Any]] = []
    for a in art_rows:
        m = mime_by_id.get(a.id)
        st_name = st_name_by_id.get(int(a.article_sender_type_id or 0))
        art_dict: dict[str, Any] = {
            "ArticleID": a.id,
            "TicketID": a.ticket_id,
            "IsVisibleForCustomer": int(a.is_visible_for_customer or 0),
            "SenderTypeID": a.article_sender_type_id,
            "SenderType": st_name,
            "CommunicationChannelID": a.communication_channel_id,
            "CreateTime": a.create_time.isoformat() if a.create_time else None,
        }
        if m:
            art_dict.update(
                {
                    "From": m.a_from,
                    "To": m.a_to,
                    "Cc": m.a_cc,
                    "Subject": m.a_subject,
                    "Body": m.a_body,
                    "ContentType": m.a_content_type,
                    "MessageID": m.a_message_id,
                }
            )

        if with_attachments:
            att_rows = (
                (
                    await session.execute(
                        select(ArticleDataMimeAttachment).where(
                            ArticleDataMimeAttachment.article_id == a.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            atts: list[dict[str, Any]] = []
            for att in att_rows:
                entry: dict[str, Any] = {
                    "Filename": att.filename,
                    "ContentType": att.content_type,
                    "ContentSize": att.content_size,
                }
                if get_attachment_contents:
                    entry["Content"] = base64.b64encode(att.content or b"").decode("ascii")
                atts.append(entry)
            art_dict["Attachment"] = atts

        out.append(art_dict)

    return out


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
    SLAs, OwnerIDs/ResponsibleIDs/WatchUserIDs, Customer*, create-time windows,
    MIMEBase_* / Fulltext article filters, SortBy/OrderBy, UseSubQueues,
    SearchInArchive, DynamicField_X (Equals/Like/comparison ops).
    """
    from datetime import timedelta

    op = "TicketSearch"

    auth = await _auth_from_params(data, session, session_store, request=request, settings=settings)
    if isinstance(auth, dict):
        return _reprefix_auth_error(op, auth)
    user_id, login, user_type = auth
    is_customer = user_type == _CUSTOMER_USER_TYPE

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
            return {"TicketID": []}
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
            return {"TicketID": []}

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
            return {"TicketID": []}

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
            return {"TicketID": []}

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
            return {"TicketID": []}
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
            return {"TicketID": []}

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

    # Ticket create-time filters
    now_ts = datetime.now(tz=UTC)
    if tct_newer_min := data.get("TicketCreateTimeNewerMinutes"):
        cutoff = now_ts - timedelta(minutes=int(tct_newer_min))
        conditions.append("t.create_time >= :ct_newer_min")
        params["ct_newer_min"] = cutoff
    if tct_older_min := data.get("TicketCreateTimeOlderMinutes"):
        cutoff = now_ts - timedelta(minutes=int(tct_older_min))
        conditions.append("t.create_time <= :ct_older_min")
        params["ct_older_min"] = cutoff
    if tct_newer := data.get("TicketCreateTimeNewerDate"):
        conditions.append("t.create_time >= :ct_newer")
        params["ct_newer"] = tct_newer
    if tct_older := data.get("TicketCreateTimeOlderDate"):
        conditions.append("t.create_time <= :ct_older")
        params["ct_older"] = tct_older

    # MIMEBase / Fulltext article filters via article_data_mime
    mime_filters: list[tuple[str, str]] = []
    for field, col in (
        ("MIMEBase_From", "a_from"),
        ("MIMEBase_To", "a_to"),
        ("MIMEBase_Cc", "a_cc"),
        ("MIMEBase_Subject", "a_subject"),
        ("MIMEBase_Body", "a_body"),
    ):
        if data.get(field):
            mime_filters.append((col, _like_pattern(data[field])))
    if data.get("Fulltext"):
        ft = _like_pattern(data["Fulltext"])
        mime_filters.append(("__fulltext__", ft))

    if mime_filters:
        _add_join(
            "JOIN article a ON a.ticket_id = t.id"
            " JOIN article_data_mime adm ON adm.article_id = a.id",
            "mime",
        )
        mime_parts = []
        for i, (col, pat) in enumerate(mime_filters):
            if col == "__fulltext__":
                mime_parts.append(
                    f"(adm.a_from LIKE :mf{i} OR adm.a_to LIKE :mf{i}"
                    f" OR adm.a_cc LIKE :mf{i} OR adm.a_subject LIKE :mf{i}"
                    f" OR adm.a_body LIKE :mf{i} OR t.title LIKE :mf{i})"
                )
            else:
                mime_parts.append(f"adm.{col} LIKE :mf{i}")
            params[f"mf{i}"] = pat
        conditions.append("(" + " AND ".join(mime_parts) + ")")

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
            params["act_nm"] = now_ts - timedelta(minutes=int(m))
        if m := data.get("ArticleCreateTimeOlderMinutes"):
            conditions.append("a_ct.create_time <= :act_om")
            params["act_om"] = now_ts - timedelta(minutes=int(m))
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
    }
    sort_raw = data.get("SortBy") or "Age"
    order_raw = data.get("OrderBy") or "Down"
    sort_list = _to_list(sort_raw)
    order_list = _to_list(order_raw)
    order_parts: list[str] = []
    for i, s in enumerate(sort_list):
        col = sort_map.get(str(s), "t.create_time")
        direction = str(order_list[i] if i < len(order_list) else order_list[-1]).lower()
        dir_sql = "ASC" if direction in ("up", "asc") else "DESC"
        order_parts.append(f"{col} {dir_sql}")
    if not order_parts:
        order_parts = ["t.create_time DESC"]
    order_sql = ", ".join(order_parts)

    limit_raw = data.get("Limit") or 500
    try:
        limit = min(int(limit_raw), 2000)
    except (ValueError, TypeError):
        limit = 500

    joins_sql = " ".join(joins)
    where_sql = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        f"SELECT DISTINCT t.id FROM ticket t {joins_sql}"
        f" WHERE {where_sql}"
        f" ORDER BY {order_sql}"
        f" LIMIT {limit}"
    )

    rows = (await session.execute(text(sql), params)).fetchall()
    ticket_ids = [int(r[0]) for r in rows]

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

    ticket_ids_raw = data.get("TicketID")
    if ticket_ids_raw is None:
        return _err(f"{op}.MissingParameter", f"{op}: TicketID parameter is missing!")

    if isinstance(ticket_ids_raw, str):
        if "," in ticket_ids_raw:
            ticket_ids = [int(x.strip()) for x in ticket_ids_raw.split(",") if x.strip()]
        else:
            ticket_ids = [int(ticket_ids_raw)]
    elif isinstance(ticket_ids_raw, (list, tuple)):
        ticket_ids = [int(x) for x in ticket_ids_raw]
    else:
        try:
            ticket_ids = [int(ticket_ids_raw)]
        except (ValueError, TypeError):
            return _err(
                f"{op}.WrongStructure",
                f"{op}: Structure for TicketID is not correct!",
            )

    pe = PermissionEngine(session)
    allowed_groups = set(await pe.groups_for_permission(user_id, "ro"))

    histories: list[dict[str, Any]] = []
    for tid in ticket_ids:
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
                    "CreateTime": r["create_time"].isoformat() if r["create_time"] else None,
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

    start = data["TimeAccountingStart"]
    end = data["TimeAccountingEnd"]

    rows = (
        (
            await session.execute(
                text(
                    "SELECT t.id AS ticket_id, t.tn, t.customer_id, t.title,"
                    " ta.time_unit, ta.create_time, q.name AS queue_name"
                    " FROM time_accounting ta"
                    " JOIN ticket t ON t.id = ta.ticket_id"
                    " LEFT JOIN queue q ON q.id = t.queue_id"
                    " WHERE ta.create_by = :uid"
                    " AND ta.create_time BETWEEN :start AND :end"
                    " ORDER BY ta.create_time ASC, t.id ASC"
                ),
                {"uid": target_id, "start": start, "end": end},
            )
        )
        .mappings()
        .all()
    )

    entries = [
        {
            "TicketID": r["ticket_id"],
            "TicketNumber": r["tn"],
            "TicketCustomerID": r["customer_id"],
            "TicketTitle": r["title"],
            "TimeUnit": r["time_unit"],
            "Created": r["create_time"].isoformat() if r["create_time"] else None,
            "Queue": r["queue_name"],
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

    entries = data.get("OutOfOfficeEntries")
    if not isinstance(entries, list) or not entries:
        return _err(
            f"{op}.WrongInputStructure",
            f"{op}: OutOfOfficeEntries must be a non-empty list!",
        )

    results: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            results.append({"Error": f"{op}.WrongInputStructure", "Entry": entry})
            continue

        # Resolve target agent
        target_id: int | None = None
        if entry.get("UserLogin"):
            target_id = await _resolve_user_id(session, str(entry["UserLogin"]), None)
        elif entry.get("UserEmail"):
            row = (
                await session.execute(
                    text(
                        "SELECT user_id FROM user_preferences"
                        " WHERE preferences_key = 'UserEmail'"
                        " AND preferences_value = :e LIMIT 1"
                    ),
                    {"e": str(entry["UserEmail"]).encode("utf-8")},
                )
            ).first()
            if row:
                target_id = int(row[0])
        elif entry.get("UserSearch"):
            search = str(entry["UserSearch"]).replace(" ", "%")
            row = (
                await session.execute(
                    text(
                        "SELECT id FROM users WHERE valid_id = 1"
                        " AND (login LIKE :s OR first_name LIKE :s OR last_name LIKE :s)"
                        " LIMIT 1"
                    ),
                    {"s": f"%{search}%"},
                )
            ).first()
            if row:
                target_id = int(row[0])

        if target_id is None:
            results.append(
                {
                    "OutOfOffice": int(bool(entry.get("OutOfOffice"))),
                    "Error": "User not found",
                    "Entry": entry,
                }
            )
            continue

        # Parse StartDate / EndDate as YYYY-MM-DD
        pref: dict[str, str] = {
            "OutOfOffice": "1" if entry.get("OutOfOffice") else "0",
        }
        for part, key in (("Start", "StartDate"), ("End", "EndDate")):
            date_s = entry.get(key) or entry.get(f"{part}Date")
            if date_s and re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_s)):
                y, mo, d = str(date_s).split("-")
                pref[f"OutOfOffice{part}Year"] = y
                pref[f"OutOfOffice{part}Month"] = mo
                pref[f"OutOfOffice{part}Day"] = d

        for k, v in pref.items():
            await _set_user_preference(session, target_id, k, v)

        results.append(
            {
                "OutOfOffice": int(bool(entry.get("OutOfOffice"))),
                "UserLogin": (
                    await session.execute(
                        text("SELECT login FROM users WHERE id = :id"), {"id": target_id}
                    )
                ).scalar_one(),
            }
        )

    await session.commit()
    return {"OutOfOfficeEntries": results}


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
