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

from tiqora.db.legacy.article import Article, ArticleDataMimeAttachment
from tiqora.db.legacy.article import ArticleDataMime as ArticleDataMimeModel
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import (
    TicketPriority,
    TicketState,
    TicketStateType,
)
from tiqora.db.legacy.user import Users
from tiqora.domain.auth import AuthenticatedUser, SessionStore
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
    move_queue,
    set_customer,
    update_dynamic_field,
)
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.password import verify_password
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


async def _auth_from_params(
    data: dict[str, Any],
    session: AsyncSession,
    session_store: SessionStore,
) -> tuple[int, str, str] | dict[str, Any]:
    """Resolve (user_id, login, user_type) from GenericInterface auth params.

    Returns an error dict on failure.  Accepts:
    - SessionID: validated against the Znuny `sessions` key-value table
    - UserLogin + Password: agent login
    - CustomerUserLogin + Password: customer login (limited perms)
    """
    op_prefix = "Auth"

    session_id = (data.get("SessionID") or "").strip()
    if session_id:
        # Validate against Znuny sessions table (key-value: UserID, UserLogin, UserType)
        result = await _lookup_session(session, session_id)
        if result is not None:
            return result
        # Fall back to Tiqora's own session store: a SessionID issued by the
        # compat SessionCreate op must round-trip for subsequent compat calls
        # (golden-master finding — Znuny's SessionCreate token is always
        # usable for follow-up requests). Customer sessions are stored with
        # user_id=0 and map to the system user (id 1) per the documented
        # Phase 2c convention.
        stored = await session_store.get(session_id)
        if stored is not None:
            stored_user_id, stored_login = stored
            if stored_user_id == 0:
                return (1, stored_login, _CUSTOMER_USER_TYPE)
            return (stored_user_id, stored_login, _AGENT_USER_TYPE)
        return _err(f"{op_prefix}.AuthFail", "Session invalid or expired")

    user_login = (data.get("UserLogin") or "").strip()
    customer_login = (data.get("CustomerUserLogin") or "").strip()
    password = (data.get("Password") or "").strip()

    if user_login:
        row = (
            await session.execute(
                select(Users).where(Users.login == user_login, Users.valid_id == 1)
            )
        ).scalar_one_or_none()
        if row is None or not verify_password(password, row.pw or ""):
            return _err(f"{op_prefix}.AuthFail", "UserLogin or Password is invalid!")
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
        if row2 is None or not verify_password(password, row2.pw or ""):
            return _err(f"{op_prefix}.AuthFail", "CustomerUserLogin or Password is invalid!")
        # Map customer to system user_id=1 (root) — customers have no direct agent user_id
        return (1, customer_login, _CUSTOMER_USER_TYPE)

    return _err(f"{op_prefix}.AuthFail", "No UserLogin, CustomerUserLogin, or SessionID provided!")


async def _lookup_session(session: AsyncSession, session_id: str) -> tuple[int, str, str] | None:
    """Look up a Znuny session row in the `sessions` key-value table.

    The table has: session_id, data_key, data_value
    We need UserID, UserLogin, and UserType.
    """
    rows = (
        await session.execute(
            text(
                "SELECT data_key, data_value FROM sessions"
                " WHERE session_id = :sid"
                "  AND data_key IN ('UserID', 'UserLogin', 'UserType')"
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
    if not user_id_s or not user_login:
        return None
    try:
        return (int(user_id_s), user_login, user_type)
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
) -> dict[str, Any]:
    """SessionCreate operation — returns a SessionID."""
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

    if user_login:
        row = (
            await session.execute(
                select(Users).where(Users.login == user_login, Users.valid_id == 1)
            )
        ).scalar_one_or_none()
        if row is None or not verify_password(password, row.pw or ""):
            return _err("SessionCreate.AuthFail", "SessionCreate: Authorization failing!")
        user = AuthenticatedUser(
            id=row.id,
            login=row.login,
            first_name=row.first_name,
            last_name=row.last_name,
            auth_method="session",
        )
    else:
        # Customer user session
        row2 = (
            await session.execute(
                select(CustomerUser).where(
                    CustomerUser.login == customer_login,
                    CustomerUser.valid_id == 1,
                )
            )
        ).scalar_one_or_none()
        if row2 is None or not verify_password(password, row2.pw or ""):
            return _err("SessionCreate.AuthFail", "SessionCreate: Authorization failing!")
        user = AuthenticatedUser(
            id=0,  # customer has no agent id
            login=customer_login,
            first_name=row2.first_name,
            last_name=row2.last_name,
            auth_method="session",
        )

    token = await session_store.create(user.id, user.login)
    return {"SessionID": token}


# ---------------------------------------------------------------------------
# TicketCreate
# ---------------------------------------------------------------------------


async def op_ticket_create(
    data: dict[str, Any],
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    session_store: SessionStore,
    sysconfig: SysConfig,
) -> dict[str, Any]:
    """TicketCreate operation."""
    op = "TicketCreate"

    auth = await _auth_from_params(data, session, session_store)
    if isinstance(auth, dict):
        # Re-prefix error code with TicketCreate
        err = auth["Error"]
        return _err(f"{op}.AuthFail", err["ErrorMessage"])
    user_id, _login, user_type = auth

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
        owner_id = 1  # default owner = root/system

    responsible_id = await _resolve_user_id(
        session, ticket.get("Responsible"), ticket.get("ResponsibleID")
    )

    # Permission check
    pe = PermissionEngine(session)
    if not await pe.check(user_id, queue_id, "create"):
        return _err(f"{op}.AccessDenied", f"{op}: No permission to create tickets in Queue!")

    # Dynamic fields
    dynamic_fields: dict[str, list[str]] = {}
    for df in data.get("DynamicField") or []:
        if isinstance(df, dict):
            fname = df.get("Name") or ""
            val = df.get("Value")
            if fname:
                dynamic_fields[fname] = [str(val)] if val is not None else []

    # Build optional article
    article_in: ArticleIn | None = None
    art_data = data.get("Article")
    if art_data and isinstance(art_data, dict):
        article_in = _build_article_in(art_data, user_type)

    ticket_in = TicketIn(
        title=title,
        queue_id=queue_id,
        state_id=state_id,
        priority_id=priority_id,
        owner_id=owner_id,
        responsible_id=responsible_id,
        customer_id=ticket.get("CustomerID"),
        customer_user_id=ticket.get("CustomerUser"),
        type_id=ticket.get("TypeID") or ticket.get("Type"),
        service_id=ticket.get("ServiceID"),
        sla_id=ticket.get("SLAID"),
        dynamic_fields=dynamic_fields,
        article=article_in,
    )

    # type_id may be a name string — ignore for now (use default 1)
    if isinstance(ticket_in.type_id, str):
        # Look up by name
        type_row = (
            await session.execute(
                text("SELECT id FROM ticket_type WHERE name = :n AND valid_id = 1 LIMIT 1"),
                {"n": ticket_in.type_id},
            )
        ).first()
        object.__setattr__(ticket_in, "type_id", type_row[0] if type_row else None)

    try:
        async with session.begin_nested():
            ticket_id = await create_ticket(
                session, session_factory, sysconfig, params=ticket_in, user_id=user_id
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
) -> dict[str, Any]:
    """TicketUpdate operation."""
    op = "TicketUpdate"

    auth = await _auth_from_params(data, session, session_store)
    if isinstance(auth, dict):
        err = auth["Error"]
        return _err(f"{op}.AuthFail", err["ErrorMessage"])
    user_id, _login, user_type = auth

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

    pe = PermissionEngine(session)
    if not await pe.check(user_id, queue_id, "rw"):
        return _err(f"{op}.AccessDenied", f"{op}: No permission to update ticket!")

    ticket = data.get("Ticket") or {}

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

            # State
            new_state_id = await _resolve_state_id(
                session, ticket.get("State"), ticket.get("StateID")
            )
            if new_state_id is not None:
                pending_raw = ticket.get("PendingTime")
                pending_time: datetime | None = None
                if pending_raw and isinstance(pending_raw, dict):
                    import contextlib

                    with contextlib.suppress(ValueError, TypeError):
                        pending_time = datetime(
                            int(pending_raw.get("Year", 0)),
                            int(pending_raw.get("Month", 0)),
                            int(pending_raw.get("Day", 0)),
                            int(pending_raw.get("Hour", 0)),
                            int(pending_raw.get("Minute", 0)),
                            tzinfo=UTC,
                        )
                await change_state(
                    session,
                    ticket_id=ticket_id,
                    new_state_id=new_state_id,
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
            if art_data and isinstance(art_data, dict):
                # TicketUpdate note: IsVisibleForCustomer defaults to 0 (internal)
                if art_data.get("IsVisibleForCustomer") is None:
                    art_data = {**art_data, "IsVisibleForCustomer": 0}
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
) -> dict[str, Any]:
    """TicketGet operation.

    Supports flags: AllArticles, Attachments, DynamicFields.
    """
    op = "TicketGet"

    auth = await _auth_from_params(data, session, session_store)
    if isinstance(auth, dict):
        err = auth["Error"]
        return _err(f"{op}.AuthFail", err["ErrorMessage"])
    user_id, _login, _user_type = auth

    ticket_ids_raw = data.get("TicketID")
    if ticket_ids_raw is None:
        return _err(f"{op}.MissingParameter", f"{op}: TicketID is required!")

    # TicketID can be a single value or a list
    if isinstance(ticket_ids_raw, (list, tuple)):
        ticket_ids = [int(t) for t in ticket_ids_raw]
    else:
        ticket_ids = [int(ticket_ids_raw)]

    all_articles = bool(int(data.get("AllArticles") or 0))
    with_attachments = bool(int(data.get("Attachments") or 0))
    with_dynamic_fields = bool(int(data.get("DynamicFields") or 0))

    pe = PermissionEngine(session)
    allowed_groups = await pe.groups_for_permission(user_id, "ro")

    tickets_out: list[dict[str, Any]] = []
    for tid in ticket_ids:
        t = (
            (
                await session.execute(
                    text(
                        "SELECT t.*, ts.name as state_name, tp.name as priority_name,"
                        " q.name as queue_name, tst.name as state_type"
                        " FROM ticket t"
                        " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                        " JOIN ticket_priority tp ON tp.id = t.ticket_priority_id"
                        " JOIN queue q ON q.id = t.queue_id"
                        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
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
            "OwnerID": t["user_id"],
            "CustomerID": t["customer_id"],
            "CustomerUserID": t["customer_user_id"],
            "CreateTime": t["create_time"].isoformat() if t["create_time"] else None,
            "ChangeTime": t["change_time"].isoformat() if t["change_time"] else None,
            "ArchiveFlag": "n" if not t["archive_flag"] else "y",
            "UntilTime": t["until_time"] or 0,
        }

        if with_dynamic_fields:
            ticket_dict["DynamicField"] = await _load_dynamic_fields_gi(session, int(t["id"]))

        if all_articles:
            ticket_dict["Article"] = await _load_articles_gi(
                session, int(t["id"]), with_attachments=with_attachments
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
    session: AsyncSession, ticket_id: int, *, with_attachments: bool
) -> list[dict[str, Any]]:
    """Load articles in Znuny GI format."""
    art_rows = (
        (
            await session.execute(
                select(Article).where(Article.ticket_id == ticket_id).order_by(Article.id)
            )
        )
        .scalars()
        .all()
    )

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
        art_dict: dict[str, Any] = {
            "ArticleID": a.id,
            "TicketID": a.ticket_id,
            "IsVisibleForCustomer": int(a.is_visible_for_customer or 0),
            "SenderTypeID": a.article_sender_type_id,
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
                content_b64 = base64.b64encode(att.content or b"").decode("ascii")
                atts.append(
                    {
                        "Filename": att.filename,
                        "ContentType": att.content_type,
                        "ContentSize": att.content_size,
                        "Content": content_b64,
                    }
                )
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
) -> dict[str, Any]:
    """TicketSearch operation.

    Supports: TicketNumber, Title, Queues/QueueIDs, States/StateIDs,
    StateType (singular, filters by type), StateTypes (extension, list),
    Priorities/PriorityIDs, CustomerUserLogin, DynamicField_X,
    TicketCreateTimeNewerDate, TicketCreateTimeOlderDate,
    TicketCreateTimeNewerMinutes, TicketCreateTimeOlderMinutes.
    Returns TicketIDs sorted by create_time desc (default).
    """
    op = "TicketSearch"

    auth = await _auth_from_params(data, session, session_store)
    if isinstance(auth, dict):
        err = auth["Error"]
        return _err(f"{op}.AuthFail", err["ErrorMessage"])
    user_id, _login, _user_type = auth

    pe = PermissionEngine(session)
    allowed_groups = await pe.groups_for_permission(user_id, "ro")
    if not allowed_groups:
        return {"TicketID": []}

    # Get allowed queue IDs
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

    # Build filter conditions as a list of SQL clauses
    conditions: list[str] = []
    params: dict[str, Any] = {}

    # Queue filter
    queue_filter: set[int] = set(allowed_queues)
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
        queue_filter = queue_filter & req_queues

    if not queue_filter:
        return {"TicketID": []}

    q_list = list(queue_filter)
    placeholders = ",".join(f":q{i}" for i in range(len(q_list)))
    conditions.append(f"t.queue_id IN ({placeholders})")
    for i, qid in enumerate(q_list):
        params[f"q{i}"] = qid

    # TicketNumber
    if tn := data.get("TicketNumber"):
        conditions.append("t.tn LIKE :tn")
        params["tn"] = str(tn).replace("*", "%")

    # Title
    if title := data.get("Title"):
        conditions.append("t.title LIKE :title")
        params["title"] = str(title).replace("*", "%")

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

    # StateType (singular — Znuny gotcha: it's a string, not a list)
    state_type_raw = data.get("StateType")
    if state_type_raw and isinstance(state_type_raw, str):
        type_sids = await _state_ids_for_type(session, state_type_raw)
        if state_ids:
            state_ids &= set(type_sids)
        else:
            state_ids = set(type_sids)
        if not state_ids:
            return {"TicketID": []}

    # StateTypes (extension: list form)
    state_types_raw = data.get("StateTypes")
    if state_types_raw:
        all_type_sids: set[int] = set()
        for st in _to_list(state_types_raw):
            all_type_sids |= set(await _state_ids_for_type(session, str(st)))
        if state_ids:
            state_ids &= all_type_sids
        else:
            state_ids = all_type_sids
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

    # CustomerUserLogin
    if cul := data.get("CustomerUserLogin"):
        cu_list = _to_list(cul)
        cu_ph = ",".join(f":cu{i}" for i in range(len(cu_list)))
        conditions.append(f"t.customer_user_id IN ({cu_ph})")
        for i, cu in enumerate(cu_list):
            params[f"cu{i}"] = cu

    # CustomerID
    if cid := data.get("CustomerID"):
        conditions.append("t.customer_id = :cid")
        params["cid"] = cid

    # Time filters
    now_ts = datetime.now(tz=UTC)
    if tct_newer_min := data.get("TicketCreateTimeNewerMinutes"):
        from datetime import timedelta

        cutoff = now_ts - timedelta(minutes=int(tct_newer_min))
        conditions.append("t.create_time >= :ct_newer_min")
        params["ct_newer_min"] = cutoff

    if tct_older_min := data.get("TicketCreateTimeOlderMinutes"):
        from datetime import timedelta

        cutoff = now_ts - timedelta(minutes=int(tct_older_min))
        conditions.append("t.create_time <= :ct_older_min")
        params["ct_older_min"] = cutoff

    if tct_newer := data.get("TicketCreateTimeNewerDate"):
        conditions.append("t.create_time >= :ct_newer")
        params["ct_newer"] = tct_newer

    if tct_older := data.get("TicketCreateTimeOlderDate"):
        conditions.append("t.create_time <= :ct_older")
        params["ct_older"] = tct_older

    # DynamicField_X filters
    df_joins: list[str] = []
    df_idx = 0
    for key, val in data.items():
        m = re.match(r"^DynamicField_([a-zA-Z0-9]+)$", key)
        if not m:
            continue
        field_name = m.group(1)
        if not isinstance(val, dict):
            continue
        # Resolve field id
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

        equals = val.get("Equals")
        like = val.get("Like")
        if equals is not None:
            df_joins.append(
                f"JOIN dynamic_field_value {alias}"
                f" ON {alias}.object_id = t.id"
                f" AND {alias}.field_id = {fid}"
                f" AND {alias}.value_text = :{alias}_eq"
            )
            params[f"{alias}_eq"] = str(equals)
        elif like is not None:
            df_joins.append(
                f"JOIN dynamic_field_value {alias}"
                f" ON {alias}.object_id = t.id"
                f" AND {alias}.field_id = {fid}"
                f" AND {alias}.value_text LIKE :{alias}_like"
            )
            params[f"{alias}_like"] = str(like).replace("*", "%")

    limit_raw = data.get("Limit") or 500
    try:
        limit = min(int(limit_raw), 2000)
    except (ValueError, TypeError):
        limit = 500

    joins_sql = " ".join(df_joins)
    where_sql = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        f"SELECT t.id FROM ticket t {joins_sql}"
        f" WHERE {where_sql}"
        f" ORDER BY t.create_time DESC"
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


__all__ = [
    "op_session_create",
    "op_ticket_create",
    "op_ticket_get",
    "op_ticket_search",
    "op_ticket_update",
]
