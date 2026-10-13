"""Ticket and article write service — sole write path for Tiqora.

This module ports the full Znuny write semantics for tickets and articles,
using only the invariant modules under ``tiqora.znuny.*`` for side-effects
(ticket numbering, history, escalation, ticket_index, cache_invalidation,
search_flag).  Every public method runs inside a single caller-supplied
transaction; the caller must ``await session.commit()`` or use an outer
``async with session.begin()``.

Key Znuny semantics honoured:
- TicketCreate: TN via ticket_create_number, NewTicket history, escalation
  recompute, ticket_index (StaticDB), cache_invalidation, dynamic fields,
  optional first article.
- ArticleCreate: article + article_data_mime + attachments via storage backend,
  insert_fingerprint, a_message_id + a_message_id_md5, search_flag,
  history (AddNote / PhoneCallAgent / EmailAgent by channel/sender).
- Field mutations: move_queue, change_state, change_priority, change_title,
  set_customer, assign_owner, assign_responsible, lock_ticket, unlock_ticket,
  watch_ticket, unwatch_ticket, archive_ticket, update_dynamic_field.
- merge_tickets: exact port of Kernel/System/Ticket.pm::TicketMerge.
- All write ops emit an event row to tiqora_event_outbox within the same
  transaction (Znuny-style event names: TicketCreate, ArticleCreate, etc.).
"""

from __future__ import annotations

import base64
import re
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.ai.handoff import (
    clear_ai_escalated,
    clear_ai_grant,
    clear_ai_paused,
    set_ai_grant,
    set_ai_paused,
)
from tiqora.channels.telegram.messages import ButtonSpec
from tiqora.db.utf8mb3 import replace_non_bmp
from tiqora.domain.auth import decode_preference_value
from tiqora.domain.dynamic_field_values import (
    DynamicFieldValueError,
    load_ticket_dynamic_field,
    set_ticket_dynamic_field,
    values_outside_possible_values,
)
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.cache_invalidation import invalidate_cache_type, invalidate_ticket_cache
from tiqora.znuny.escalation import escalation_index_build
from tiqora.znuny.history import (
    TYPE_ADD_NOTE,
    TYPE_EMAIL_AGENT,
    TYPE_FOLLOW_UP,
    TYPE_MISC,
    TYPE_PHONE_CALL_AGENT,
    TYPE_SEND_ANSWER,
    TYPE_TIME_ACCOUNTING,
    TYPE_WEB_REQUEST_CUSTOMER,
    add_archive_flag_update,
    add_article_history,
    add_customer_update,
    add_lock,
    add_merged,
    add_move,
    add_new_ticket,
    add_owner_update,
    add_pending_time,
    add_priority_update,
    add_responsible_update,
    add_service_update,
    add_sla_update,
    add_state_update,
    add_subscribe,
    add_title_update,
    add_type_update,
    add_unsubscribe,
    history_add,
)
from tiqora.znuny.link_config import read_link_config
from tiqora.znuny.perl_format import perl_num, perl_num_str
from tiqora.znuny.search_flag import mark_search_rebuild, message_id_md5
from tiqora.znuny.sysconfig import SysConfig
from tiqora.znuny.ticket_index import ticket_accelerator_add, ticket_accelerator_update
from tiqora.znuny.ticket_number import ticket_create_number

if TYPE_CHECKING:
    from tiqora.crypto.compose import EmailSecurityIn

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TicketNotFound(Exception):
    """Ticket id does not exist."""


class TicketAccessDenied(Exception):
    """User lacks permission on the ticket's queue group."""


_log = structlog.get_logger(__name__)


class InvalidInput(Exception):
    """Caller passed an invalid combination of parameters."""


class LinkConflict(InvalidInput):
    """``LinkObject.pm::LinkAdd`` refuses the link (Znuny logs and returns undef).

    ``reason``: ``opposite_direction`` (pointed type already linked the other way round),
    ``state`` (the pair already has a link of that type in another state), ``type_group``
    (another link between the pair sits in the same ``LinkObject::TypeGroup``),
    ``not_possible`` (the type is not a ``LinkObject::PossibleLink`` of the two objects).
    """

    def __init__(
        self,
        reason: Literal["opposite_direction", "state", "type_group", "not_possible"],
    ) -> None:
        super().__init__(f"Link not possible: {reason}")
        self.reason = reason


class ArticleNotDeletable(Exception):
    """Article is not an internal, non-customer-visible note (never deletable)."""


# ---------------------------------------------------------------------------
# Input dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TelegramSendOptions:
    """Chat-composer extras for an outgoing agent Telegram reply.

    *reply_to_article_id* quotes that article's Telegram message (when it has
    one in the same chat); *buttons* become an inline keyboard under the reply.
    """

    reply_to_article_id: int | None = None
    buttons: tuple[ButtonSpec, ...] = ()


@dataclass
class ArticleIn:
    """Parameters for creating one article (MIME backend)."""

    sender_type: str  # "agent" | "customer" | "system"
    is_visible_for_customer: bool
    subject: str
    body: str
    content_type: str = "text/plain; charset=utf-8"
    from_address: str | None = None
    to_address: str | None = None
    cc: str | None = None
    bcc: str | None = None
    reply_to: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    # "email" | "phone" | "note" | "internal" — affects history type
    channel: str = "note"
    # Machine-generated inbound mail (newsletter, bulk, spam-flagged) — see
    # tiqora.channels.email.machine_mail. Rides along on the ArticleCreate
    # outbox payload so the AI auto-reply can skip it (tiqora.ai.auto_worker);
    # nothing in the article schema itself records it, because Tiqora does not
    # store raw MIME and the headers are gone after ingest.
    auto_generated: bool = False
    # Binary attachments: list of (filename, content_type, content_bytes)
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)
    # Override the derived ticket_history type (e.g. postmaster auto-responses
    # use SendAutoReply/SendAutoFollowUp/SendAutoReject instead of the
    # channel/sender-derived EmailAgent/EmailCustomer names).
    history_type_override: str | None = None
    # Only read by the outgoing agent Telegram path (channels.telegram.outbound).
    telegram: TelegramSendOptions | None = None
    # Only read by the outgoing agent email path (channels.email.outbound_reply):
    # PGP / S/MIME signing and encryption of the sent mail.
    email_security: EmailSecurityIn | None = None
    # True: ``email_security`` is the sender's choice (``None`` = plain mail).
    # False: nobody chose (AI reply, process/MCP send, API call without the
    # field) — the queue's security defaults apply (crypto.queue_security).
    email_security_explicit: bool = False
    # GenericInterface / Znuny ArticleCreate flags (MIMEBase.pm). ``None`` =
    # today's "%% <subject>" history text; ``no_agent_notify`` = NoAgentNotify
    # (no legacy Notification* event at all); the three id lists are
    # ForceNotificationToUserID / ExcludeNotificationToUserID /
    # ExcludeMuteNotificationToUserID; ``append_signature`` = AppendSignatureToBody
    # (only read by the outgoing agent email path).
    history_comment_override: str | None = None
    no_agent_notify: bool = False
    force_notification_user_ids: tuple[int, ...] = ()
    exclude_notification_user_ids: tuple[int, ...] = ()
    exclude_mute_notification_user_ids: tuple[int, ...] = ()
    append_signature: bool = True
    # GenericInterface ArticleSend: the signature goes directly behind the body
    # (``Body . $Signature``, TicketCreate.pm) -- no "-- " separator, no
    # "already present" check like the agent reply composer does.
    signature_directly_after_body: bool = False


@dataclass
class TicketIn:
    """Parameters for creating one ticket."""

    title: str
    queue_id: int
    state_id: int
    priority_id: int
    owner_id: int
    lock_id: int = 1  # unlock
    type_id: int | None = None
    service_id: int | None = None
    sla_id: int | None = None
    responsible_id: int | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    archive_flag: int = 0
    # Dynamic fields: {field_name: [values]}
    dynamic_fields: dict[str, list[str]] = field(default_factory=dict)
    # Optional first article to attach inline
    article: ArticleIn | None = None
    # Pending-type initial state only (ignored otherwise): the reminder time,
    # set like Znuny AgentTicketPhone's TicketPendingTimeSet after create.
    pending_time: datetime | None = None


# ---------------------------------------------------------------------------
# Outbox helper
# ---------------------------------------------------------------------------


async def _emit_event(
    session: AsyncSession,
    event_type: str,
    ticket_id: int,
    payload: dict[str, Any] | None = None,
) -> None:
    """Write one row to tiqora_event_outbox within the current transaction."""
    import json

    await session.execute(
        text(
            "INSERT INTO tiqora_event_outbox"
            " (event_type, ticket_id, payload, created, processed)"
            " VALUES (:et, :tid, :pl, current_timestamp, :pr)"
        ),
        {
            "et": event_type,
            "tid": ticket_id,
            "pl": json.dumps(payload or {}),
            "pr": False,
        },
    )


# Internal outbox event: "re-index this ticket in search", nothing else. Used
# where rows change (or disappear) outside the normal write paths — e.g. GDPR
# erasure and its rollback. Not a Znuny event: no notification rule can bind
# to it, and the outbox drain keeps it away from webhooks and SSE.
SEARCH_REINDEX_EVENT: Final = "TiqoraSearchReindex"

# Internal outbox event: "answer this customer article now", written when an
# agent switches the autopilot back on with answer_latest. The AI auto worker
# treats it like an ArticleCreate for that article; it is not a second
# ArticleCreate because that would fan out to webhooks and SSE again.
AI_REPLY_REQUEST_EVENT: Final = "TiqoraAiReplyRequest"


# ---------------------------------------------------------------------------
# Legacy ``Notification*`` events
#
# Znuny fires a second, separate family of events purely for the notification
# engine (``Kernel::System::Ticket::Event::NotificationEvent``): ``Notification
# NewTicket``/``AddNote``/``FollowUp`` from ``MIMEBase::ArticleCreate`` and
# ``NotificationOwnerUpdate``/``ResponsibleUpdate``/``LockTimeout``/``Move``/
# ``ServiceUpdate`` from ``Ticket.pm``. They are NOT aliases of the modern
# ``Ticket*Update`` events -- they carry ``SkipRecipients`` and have extra
# preconditions -- and installations upgraded from older OTRS releases still
# have their notification rules bound to these names. Emitting them keeps
# those rules working after the takeover.
# ---------------------------------------------------------------------------

# Chat-like channels without a history type of their own in Znuny.
_CHAT_CHANNELS: Final = frozenset({"telegram", "sms", "whatsapp", "webchat"})

# ArticleCreate history types that make the *first* article of a ticket a
# ``NotificationNewTicket`` (MIMEBase.pm: EmailAgent|EmailCustomer|
# PhoneCallCustomer|WebRequestCustomer|SystemRequest).
_NEW_TICKET_HISTORY_TYPES: Final = frozenset(
    {
        "EmailAgent",
        "EmailCustomer",
        "PhoneCallCustomer",
        "WebRequestCustomer",
        "SystemRequest",
    }
)


# Every event name emitted by :func:`_emit_legacy_notification`. Consumers
# other than the notification engine must ignore them: each one accompanies a
# modern ``Ticket*``/``Article*`` event for the very same change.
LEGACY_NOTIFICATION_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "NotificationNewTicket",
        "NotificationAddNote",
        "NotificationFollowUp",
        "NotificationLockTimeout",
        "NotificationOwnerUpdate",
        "NotificationResponsibleUpdate",
        "NotificationMove",
        "NotificationServiceUpdate",
        "NotificationPendingReminder",
    }
)


async def _emit_legacy_notification(
    session: AsyncSession,
    event_type: str,
    ticket_id: int,
    payload: dict[str, Any] | None = None,
    *,
    skip_user_ids: Sequence[int] = (),
) -> None:
    """Emit one legacy ``Notification*`` event.

    ``skip_user_ids`` mirrors Znuny's ``SkipRecipients``: the acting agent is
    not notified about their own action.
    """
    data: dict[str, Any] = dict(payload or {})
    if skip_user_ids:
        data["skip_user_ids"] = [int(uid) for uid in skip_user_ids]
    await _emit_event(session, event_type, ticket_id, data)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


async def _lookup_row(session: AsyncSession, table: str, col: str, val: Any) -> dict[str, Any]:
    row = (
        (await session.execute(text(f"SELECT * FROM {table} WHERE {col} = :v LIMIT 1"), {"v": val}))
        .mappings()
        .first()
    )
    if row is None:
        raise InvalidInput(f"No {table} with {col}={val!r}")
    return dict(row)


async def _queue_name(session: AsyncSession, queue_id: int) -> str:
    row = await _lookup_row(session, "queue", "id", queue_id)
    return str(row["name"])


async def _state_name(session: AsyncSession, state_id: int) -> str:
    row = await _lookup_row(session, "ticket_state", "id", state_id)
    return str(row["name"])


async def _state_type_name(session: AsyncSession, state_id: int) -> str:
    row = (
        await session.execute(
            text(
                "SELECT tst.name FROM ticket_state ts"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE ts.id = :sid LIMIT 1"
            ),
            {"sid": state_id},
        )
    ).first()
    if row is None:
        return "open"
    return str(row[0])


async def _priority_name(session: AsyncSession, priority_id: int) -> str:
    row = await _lookup_row(session, "ticket_priority", "id", priority_id)
    return str(row["name"])


async def _user_login(session: AsyncSession, user_id: int) -> str:
    row = (
        await session.execute(
            text("SELECT login FROM users WHERE id = :uid LIMIT 1"), {"uid": user_id}
        )
    ).first()
    if row is None:
        raise InvalidInput(f"Unknown user id: {user_id!r}")
    return str(row[0])


async def _sender_type_id(session: AsyncSession, name: str) -> int:
    row = (
        await session.execute(
            text("SELECT id FROM article_sender_type WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is None:
        raise InvalidInput(f"Unknown article_sender_type: {name!r}")
    return int(row[0])


async def _channel_id(session: AsyncSession, name: str) -> int:
    """Return communication_channel.id by channel name."""
    row = (
        await session.execute(
            text("SELECT id FROM communication_channel WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is None:
        # Fallback: use id=1 (Internal) if unknown
        return 1
    return int(row[0])


async def _ticket_must_exist(session: AsyncSession, ticket_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT t.id, t.tn, t.queue_id, t.ticket_state_id, t.ticket_priority_id,"
                    " t.user_id, t.responsible_user_id, t.ticket_lock_id, t.type_id,"
                    " t.service_id, t.sla_id,"
                    " t.customer_id, t.customer_user_id, t.archive_flag, t.title"
                    " FROM ticket t WHERE t.id = :tid LIMIT 1"
                ),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise TicketNotFound(ticket_id)
    return dict(row)


# ---------------------------------------------------------------------------
# Sub-task 1: create_ticket
# ---------------------------------------------------------------------------


async def create_ticket(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    params: TicketIn,
    user_id: int,
) -> int:
    """Create a ticket and return its id.

    Full invariant bundle:
    - TN via ticket_create_number (counter algorithm)
    - INSERT ticket row
    - NewTicket history (exact Znuny name format)
    - escalation_index_build
    - ticket_accelerator_add (StaticDB only)
    - invalidate_ticket_cache
    - dynamic fields (multivalue delete+insert)
    - optional first article (calls add_article)
    - emits TicketCreate outbox event
    """
    tn = await ticket_create_number(session_factory, sysconfig)

    title = (replace_non_bmp(params.title) or "")[:255]
    responsible_id = params.responsible_id if params.responsible_id is not None else 1
    type_id = params.type_id if params.type_id is not None else 1

    await session.execute(
        text(
            "INSERT INTO ticket"
            " (tn, title, type_id, queue_id, ticket_lock_id,"
            "  user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
            "  escalation_time, escalation_update_time, escalation_response_time,"
            "  escalation_solution_time, timeout, service_id, sla_id, until_time,"
            "  archive_flag, customer_id, customer_user_id,"
            "  create_time, create_by, change_time, change_by)"
            " VALUES"
            " (:tn, :title, :type_id, :queue_id, :lock_id,"
            "  :owner_id, :resp_id, :priority_id, :state_id,"
            "  0, 0, 0, 0, 0, :svc_id, :sla_id, 0,"
            "  :archive, :cid, :cuid,"
            "  current_timestamp, :uid, current_timestamp, :uid)"
        ),
        {
            "tn": tn,
            "title": title,
            "type_id": type_id,
            "queue_id": params.queue_id,
            "lock_id": params.lock_id,
            "owner_id": params.owner_id,
            "resp_id": responsible_id,
            "priority_id": params.priority_id,
            "state_id": params.state_id,
            "svc_id": params.service_id,
            "sla_id": params.sla_id,
            "archive": params.archive_flag,
            "cid": replace_non_bmp(params.customer_id),
            "cuid": replace_non_bmp(params.customer_user_id),
            "uid": user_id,
        },
    )

    # Read back ticket id
    row = (
        await session.execute(text("SELECT id FROM ticket WHERE tn = :tn LIMIT 1"), {"tn": tn})
    ).first()
    if row is None:
        raise RuntimeError("Ticket insert succeeded but id read-back failed")
    ticket_id = int(row[0])

    # NewTicket history
    queue_name = await _queue_name(session, params.queue_id)
    priority_name = await _priority_name(session, params.priority_id)
    state_name = await _state_name(session, params.state_id)
    await add_new_ticket(
        session,
        ticket_id=ticket_id,
        tn=tn,
        queue=queue_name,
        priority=priority_name,
        state=state_name,
        user_id=user_id,
        queue_id=params.queue_id,
    )

    # Customer data history: Znuny TicketCreate calls TicketCustomerSet when
    # CustomerID/CustomerUser is given, writing a CustomerUpdate row with BOTH
    # parts (empty string for a missing one — both params are defined in that
    # internal call). Golden-master validated against Znuny 6.5.22.
    if params.customer_id is not None or params.customer_user_id is not None:
        await add_customer_update(
            session,
            ticket_id=ticket_id,
            customer_id=params.customer_id or "",
            customer_user=params.customer_user_id or "",
            user_id=user_id,
        )

    if params.pending_time is not None:
        state_type = await _state_type_name(session, params.state_id)
        if state_type.lower().startswith("pending"):
            pt = params.pending_time
            await session.execute(
                text("UPDATE ticket SET until_time = :ut WHERE id = :tid"),
                {"ut": int(pt.timestamp()), "tid": ticket_id},
            )
            await add_pending_time(
                session,
                ticket_id=ticket_id,
                year=pt.year,
                month=pt.month,
                day=pt.day,
                hour=pt.hour,
                minute=pt.minute,
                user_id=user_id,
            )

    # Escalation
    await escalation_index_build(session, ticket_id, user_id, sysconfig)

    # Ticket index (StaticDB)
    await ticket_accelerator_add(session, ticket_id, sysconfig)

    # Cache invalidation
    await invalidate_ticket_cache(session, ticket_id)

    # Dynamic fields
    for fname, values in params.dynamic_fields.items():
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=fname, values=values, user_id=user_id
        )

    # Optional first article
    if params.article is not None:
        await add_article(
            session,
            ticket_id=ticket_id,
            article=params.article,
            user_id=user_id,
            sysconfig=sysconfig,
        )

    # Outbox event
    await _emit_event(session, "TicketCreate", ticket_id, {"tn": tn, "queue_id": params.queue_id})

    return ticket_id


# ---------------------------------------------------------------------------
# Sub-task 2: add_article
# ---------------------------------------------------------------------------


def _utf8mb3_article(article: ArticleIn) -> ArticleIn:
    """Strip supplementary-plane chars so MIME INSERTs survive utf8mb3 columns."""
    return replace(
        article,
        subject=replace_non_bmp(article.subject) or "",
        body=replace_non_bmp(article.body) or "",
        content_type=replace_non_bmp(article.content_type) or article.content_type,
        from_address=replace_non_bmp(article.from_address),
        to_address=replace_non_bmp(article.to_address),
        cc=replace_non_bmp(article.cc),
        bcc=replace_non_bmp(article.bcc),
        reply_to=replace_non_bmp(article.reply_to),
        message_id=replace_non_bmp(article.message_id),
        in_reply_to=replace_non_bmp(article.in_reply_to),
        references=replace_non_bmp(article.references),
        attachments=[
            (replace_non_bmp(filename) or "", content_type, content)
            for filename, content_type, content in article.attachments
        ],
    )


async def _with_forced_recipients_in_to(session: AsyncSession, article: ArticleIn) -> ArticleIn:
    """Port of ``MIMEBase.pm::ArticleCreate`` (ForceNotificationToUserID, bug#13422).

    Each forced agent (not root, not excluded, valid, with a mail address) is
    appended to the To header as ``"First Last" <email>``.
    """
    if not article.force_notification_user_ids:
        return article
    excluded = {int(uid) for uid in article.exclude_notification_user_ids}
    to_line = article.to_address or ""
    added = ""
    for uid in article.force_notification_user_ids:
        if int(uid) == 1 or int(uid) in excluded:
            continue
        row = (
            await session.execute(
                text(
                    "SELECT u.first_name, u.last_name, p.preferences_value FROM users u"
                    " LEFT JOIN user_preferences p ON p.user_id = u.id"
                    " AND p.preferences_key = 'UserEmail'"
                    " WHERE u.id = :uid AND u.valid_id = 1"
                ),
                {"uid": int(uid)},
            )
        ).first()
        if row is None:
            continue
        email = decode_preference_value(row[2]) or ""
        if not email:
            continue
        if to_line or added:
            added += ", "
        added += f'"{row[0] or ""} {row[1] or ""}" <{email}>'
    if not added:
        return article
    return replace(article, to_address=to_line + added)


async def add_article(
    session: AsyncSession,
    *,
    ticket_id: int,
    article: ArticleIn,
    user_id: int,
    sysconfig: SysConfig,
) -> int:
    """Add an article (MIME) to an existing ticket. Returns article_id.

    Ports MIMEBase.pm::ArticleCreate:
    - Generates insert_fingerprint (PID + random + MessageID)
    - Computes a_message_id_md5
    - Inserts article (meta) + article_data_mime
    - Stores attachments in article_data_mime_attachment
    - Sets search_index_needs_rebuild=1 via mark_search_rebuild
    - Writes history (AddNote / PhoneCallAgent / EmailAgent)
    - Emits ArticleCreate outbox event
    """
    await _ticket_must_exist(session, ticket_id)
    sender_type_id = await _sender_type_id(session, article.sender_type)

    # Agent notes arrive without a From header (unlike email replies, which
    # carry the system address). Znuny stamps the acting agent's full name
    # there; without it the UI shows the sender as "unbekannt".
    if not article.from_address and article.sender_type == "agent":
        agent_row = (
            await session.execute(
                text("SELECT first_name, last_name, login FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
        ).first()
        if agent_row is not None:
            full_name = " ".join(p for p in (agent_row[0], agent_row[1]) if p).strip()
            article = replace(article, from_address=full_name or agent_row[2])

    article = await _with_forced_recipients_in_to(session, article)
    article = _utf8mb3_article(article)

    # Resolve communication channel id by channel name
    channel_name_map: dict[str, str] = {
        "email": "Email",
        "phone": "Phone",
        "note": "Internal",
        "internal": "Internal",
        "sms": "SMS",
        "whatsapp": "WhatsApp",
        "telegram": "Telegram",
        "webchat": "WebChat",
    }
    ch_name = channel_name_map.get(article.channel.lower(), "Internal")
    plugin_channel = None
    if article.channel.lower() not in channel_name_map:
        # A channel a plugin registered (D11): its own row, never the Internal fallback.
        from tiqora.plugins.channels import driver_for

        plugin_channel = driver_for(article.channel)
        if plugin_channel is None and article.channel.lower().startswith("plugin_"):
            # The plugin is disabled, failed or not loaded in this process: storing the
            # article as Internal would look like a delivered reply that never left.
            raise InvalidInput(f"Kanal „{article.channel}“ ist derzeit nicht verfügbar")
    if plugin_channel is not None:
        from tiqora.plugins.channels import ensure_tiqora_channel_row

        comm_channel_id = await ensure_tiqora_channel_row(session, plugin_channel.name)
    elif ch_name == "WebChat":
        # Never fall back to Internal for a chat line (``_channel_id`` would):
        # register the row on first use (idempotent; cache cleanup inside).
        from tiqora.channels.common import ensure_webchat_channel_row

        comm_channel_id = await ensure_webchat_channel_row(session)
    else:
        comm_channel_id = await _channel_id(session, ch_name)

    # Generate fingerprint (Znuny: PID-RandomString-MessageID)
    fingerprint = f"{__import__('os').getpid()}-{secrets.token_hex(16)}-{article.message_id or ''}"
    fingerprint = fingerprint[:64]

    # Insert meta article row
    await session.execute(
        text(
            "INSERT INTO article"
            " (ticket_id, article_sender_type_id, communication_channel_id,"
            "  is_visible_for_customer, search_index_needs_rebuild, insert_fingerprint,"
            "  create_time, create_by, change_time, change_by)"
            " VALUES"
            " (:tid, :stid, :ccid, :vis, 1, :fp,"
            "  current_timestamp, :uid, current_timestamp, :uid)"
        ),
        {
            "tid": ticket_id,
            "stid": sender_type_id,
            "ccid": comm_channel_id,
            "vis": 1 if article.is_visible_for_customer else 0,
            "fp": fingerprint,
            "uid": user_id,
        },
    )

    # Read back article id via fingerprint
    art_row = (
        await session.execute(
            text("SELECT id FROM article WHERE insert_fingerprint = :fp LIMIT 1"),
            {"fp": fingerprint},
        )
    ).first()
    if art_row is None:
        raise RuntimeError("Article insert succeeded but id read-back failed")
    article_id = int(art_row[0])

    # Compute MD5 of MessageID
    msg_id_md5 = message_id_md5(article.message_id) if article.message_id else None

    incoming_time = int(time.time())

    # Insert MIME data
    await session.execute(
        text(
            "INSERT INTO article_data_mime"
            " (article_id, a_from, a_reply_to, a_to, a_cc, a_bcc,"
            "  a_subject, a_message_id, a_message_id_md5, a_in_reply_to, a_references,"
            "  a_content_type, a_body, incoming_time,"
            "  create_time, create_by, change_time, change_by)"
            " VALUES"
            " (:aid, :frm, :rto, :to, :cc, :bcc,"
            "  :subj, :msgid, :msgmd5, :inrto, :refs,"
            "  :ct, :body, :itime,"
            "  current_timestamp, :uid, current_timestamp, :uid)"
        ),
        {
            "aid": article_id,
            "frm": article.from_address,
            "rto": article.reply_to,
            "to": article.to_address,
            "cc": article.cc,
            "bcc": article.bcc,
            "subj": article.subject,
            "msgid": article.message_id,
            "msgmd5": msg_id_md5,
            "inrto": article.in_reply_to,
            "refs": article.references,
            "ct": article.content_type,
            "body": article.body,
            "itime": incoming_time,
            "uid": user_id,
        },
    )

    # Store attachments. Znuny keeps the bytes in a LONGBLOB on MySQL but as
    # base64 text in the TEXT column on PostgreSQL (ArticleStorageDB, no DirectBlob).
    postgres = session.get_bind().dialect.name == "postgresql"
    for filename, content_type, content in article.attachments:
        size_str = str(len(content))
        stored: bytes | str = base64.b64encode(content).decode("ascii") if postgres else content
        await session.execute(
            text(
                "INSERT INTO article_data_mime_attachment"
                " (article_id, filename, content_size, content_type, content,"
                "  create_time, create_by, change_time, change_by)"
                " VALUES"
                " (:aid, :fn, :sz, :ct, :content,"
                "  current_timestamp, :uid, current_timestamp, :uid)"
            ),
            {
                "aid": article_id,
                "fn": filename,
                "sz": size_str,
                "ct": content_type,
                "content": stored,
                "uid": user_id,
            },
        )

    # Search rebuild flag
    await mark_search_rebuild(session, article_id)

    # History type by channel and sender (or explicit override)
    history_type: str
    channel_lower = article.channel.lower()
    if article.history_type_override:
        history_type = article.history_type_override
    elif channel_lower == "email":
        if article.sender_type == "agent":
            history_type = TYPE_EMAIL_AGENT
        else:
            from tiqora.znuny.history import TYPE_EMAIL_CUSTOMER

            history_type = TYPE_EMAIL_CUSTOMER
    elif channel_lower == "phone":
        if article.sender_type == "agent":
            history_type = TYPE_PHONE_CALL_AGENT
        else:
            from tiqora.znuny.history import TYPE_PHONE_CALL_CUSTOMER

            history_type = TYPE_PHONE_CALL_CUSTOMER
    elif (
        channel_lower in _CHAT_CHANNELS or plugin_channel is not None
    ) and article.is_visible_for_customer:
        # Znuny has no chat history types; a chat message is a web request.
        # Customer: the first message opens the ticket (NotificationNewTicket),
        # later ones are follow-ups (NotificationFollowUp) -- as AddNote they
        # reached agents as a "new note" mail without its text. Agent: an
        # answer to the customer, like an email reply (no notification).
        if article.sender_type == "agent":
            history_type = TYPE_SEND_ANSWER
        elif article.sender_type == "customer":
            earlier = (
                await session.execute(
                    text("SELECT 1 FROM article WHERE ticket_id = :tid AND id < :aid LIMIT 1"),
                    {"tid": ticket_id, "aid": article_id},
                )
            ).first()
            history_type = TYPE_WEB_REQUEST_CUSTOMER if earlier is None else TYPE_FOLLOW_UP
        else:
            history_type = TYPE_ADD_NOTE
    else:
        history_type = TYPE_ADD_NOTE

    if article.history_comment_override is not None:
        history_comment = article.history_comment_override
    else:
        history_comment = f"%% {article.subject[:100]}" if article.subject else "%%"
    await add_article_history(
        session,
        ticket_id=ticket_id,
        article_id=article_id,
        history_type=history_type,
        name=history_comment,
        user_id=user_id,
    )

    # Unlock-timeout reset (port of MIMEBase::ArticleCreate → Ticket.pm::
    # TicketUnlockTimeoutUpdate). Golden-master validated against 6.5.22:
    # - agent article: always reset ticket.timeout to the article's
    #   incoming_time; when the value actually changes this writes a
    #   Misc "Reset of unlock time." history row.
    # - customer article: same reset, but only when the previous
    #   non-system article was from an agent.
    reset_unlock = False
    if article.sender_type == "agent":
        reset_unlock = True
    elif article.sender_type == "customer":
        prev = (
            await session.execute(
                text(
                    "SELECT st.name FROM article a"
                    " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                    " WHERE a.ticket_id = :tid AND a.id <> :aid AND st.name <> 'system'"
                    " ORDER BY a.id DESC LIMIT 1"
                ),
                {"tid": ticket_id, "aid": article_id},
            )
        ).first()
        reset_unlock = prev is not None and prev[0] == "agent"
    if reset_unlock:
        await ticket_unlock_timeout_update(
            session, ticket_id=ticket_id, unlock_timeout=incoming_time, user_id=user_id
        )

    # Legacy per-article notification events (MIMEBase.pm). Exactly one of
    # them fires, chosen by history type -- and NotificationNewTicket only for
    # the ticket's first article.
    legacy_event: str | None = None
    if history_type in _NEW_TICKET_HISTORY_TYPES:
        earlier = (
            await session.execute(
                text("SELECT 1 FROM article WHERE ticket_id = :tid AND id < :aid LIMIT 1"),
                {"tid": ticket_id, "aid": article_id},
            )
        ).first()
        if earlier is None:
            legacy_event = "NotificationNewTicket"
    elif history_type == TYPE_ADD_NOTE:
        legacy_event = "NotificationAddNote"
    elif history_type == TYPE_FOLLOW_UP:
        legacy_event = "NotificationFollowUp"
    if legacy_event is not None and not article.no_agent_notify:
        # NotificationEvent.pm never mails the agent who wrote the article
        # (unless AgentSelfNotifyOnAction) -- an agent's own Telegram reply
        # used to come back to them as a notification.
        self_notify = str(await sysconfig.get("AgentSelfNotifyOnAction")) in {"1", "True"}
        # MIMEBase.pm: SkipRecipients = Exclude + ExcludeMute; Recipients = the
        # forced agents (the worker still applies skip/valid/permission checks).
        # NotificationEvent.pm:1078-1085: the acting agent is skipped unless it is one of
        # the pre-calculated (forced) recipients.
        forced = {int(uid) for uid in article.force_notification_user_ids}
        skip_ids: list[int] = [] if self_notify or int(user_id) in forced else [user_id]
        for uid in (
            *article.exclude_notification_user_ids,
            *article.exclude_mute_notification_user_ids,
        ):
            if int(uid) not in skip_ids:
                skip_ids.append(int(uid))
        legacy_payload: dict[str, Any] = {"article_id": article_id}
        if article.force_notification_user_ids:
            legacy_payload["recipient_user_ids"] = [
                int(uid) for uid in article.force_notification_user_ids
            ]
        await _emit_legacy_notification(
            session, legacy_event, ticket_id, legacy_payload, skip_user_ids=skip_ids
        )

    # Outbox event
    await _emit_event(
        session,
        "ArticleCreate",
        ticket_id,
        {
            "article_id": article_id,
            "channel": article.channel,
            "auto_generated": article.auto_generated,
        },
    )

    # Invalidate Znuny's on-disk ticket/article cache so the new article shows
    # up in the Znuny GUI (the TiqoraSync daemon polls tiqora_cache_invalidation).
    # Without this, field changes propagate but externally-added articles do not.
    await invalidate_ticket_cache(session, ticket_id)

    # A human agent's reply no longer lifts an AI handoff: it stays until
    # someone switches the autopilot back on (start_ai_autopilot), so the AI
    # does not quietly pick the conversation up again after a human answered.

    return article_id


_TIME_UNIT_TRUNCATE: Final = re.compile(r"\A(\d{1,10}\.\d\d).+?\Z")
# DB.pm::Quote(..., 'Number')
_TIME_UNIT_NUMBER: Final = re.compile(r"\A[+-]?\d{1,20}(?:\.\d{1,20})?\Z")


def normalize_time_unit(time_unit: str | float) -> str:
    """``Ticket.pm::TicketAccountTime`` input handling.

    ``,`` becomes ``.``, spaces vanish, and anything after two decimals is cut
    off (``0,256`` -> ``0.25``; no rounding). Raises :class:`InvalidInput` for
    a missing unit (Perl-false: empty / ``0``) or a value DB.pm would refuse.
    """
    raw = perl_num_str(time_unit) if isinstance(time_unit, int | float) else str(time_unit)
    if raw in ("", "0"):
        raise InvalidInput("time_unit is required")
    unit = raw.replace(",", ".").replace(" ", "")
    unit = unit.rstrip("\n")  # chomp
    unit = _TIME_UNIT_TRUNCATE.sub(r"\1", unit)
    if not _TIME_UNIT_NUMBER.match(unit):
        raise InvalidInput(f"Invalid number in query '{unit}'!")
    return unit


async def add_time_accounting(
    session: AsyncSession,
    *,
    ticket_id: int,
    article_id: int | None,
    time_unit: str | float,
    user_id: int,
) -> int:
    """Book *time_unit* on a ticket; returns the row id.

    Port of ``Ticket.pm::TicketAccountTime``: normalised unit, ticket
    ``change_time`` bump, ``TimeAccounting`` history row ``%%<unit>%%<total>``
    (total = Perl sum of every booked unit), cache invalidation and the
    ``TicketAccountTime`` event.
    """
    unit = normalize_time_unit(time_unit)
    if article_id is not None:
        owner = (
            await session.execute(
                text("SELECT ticket_id FROM article WHERE id = :aid"), {"aid": article_id}
            )
        ).scalar_one_or_none()
        if owner is None or int(owner) != ticket_id:
            raise InvalidInput("article_id does not belong to this ticket")
    await session.execute(
        text("UPDATE ticket SET change_time = current_timestamp, change_by = :uid WHERE id = :tid"),
        {"uid": user_id, "tid": ticket_id},
    )
    await session.execute(
        text(
            "INSERT INTO time_accounting"
            " (ticket_id, article_id, time_unit, create_time, create_by,"
            "  change_time, change_by)"
            " VALUES (:tid, :aid, :units, current_timestamp, :uid,"
            "         current_timestamp, :uid)"
        ),
        {"tid": ticket_id, "aid": article_id, "units": Decimal(unit), "uid": user_id},
    )
    row = (
        await session.execute(
            text(
                "SELECT id FROM time_accounting WHERE ticket_id = :tid AND create_by = :uid"
                " ORDER BY id DESC LIMIT 1"
            ),
            {"tid": ticket_id, "uid": user_id},
        )
    ).first()
    if row is None:
        raise RuntimeError("time_accounting insert succeeded but id read-back failed")
    await invalidate_ticket_cache(session, ticket_id)
    # TicketAccountedTimeGet: Perl numeric sum, printed as Perl prints it.
    booked = (
        await session.execute(
            text("SELECT time_unit FROM time_accounting WHERE ticket_id = :tid"),
            {"tid": ticket_id},
        )
    ).fetchall()
    total = 0.0
    for (value,) in booked:
        total += perl_num(value)
    await history_add(
        session,
        ticket_id=ticket_id,
        article_id=article_id,
        history_type=TYPE_TIME_ACCOUNTING,
        name=f"%%{unit}%%{perl_num_str(total)}",
        user_id=user_id,
    )
    await _emit_event(session, "TicketAccountTime", ticket_id, {"article_id": article_id})
    return int(row[0])


async def resume_ai_automation(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Manually clear the AI->human handoff flag (``ai_escalated_at``).

    Normally that flag only clears when a human sends a customer-visible
    reply (see the check at the bottom of :func:`add_article`) or the ticket
    closes — deliberately, so the AI never silently resumes on its own (see
    tiqora.ai.auto_worker._cap_reason). This is the explicit override for
    when a human decides the ticket is safe to hand back without writing a
    customer-visible reply (e.g. after fixing the underlying KB/prompt
    issue that caused a bad escalation). Writes an *internal* note for the
    audit trail — internal notes don't trigger the automatic clear above,
    so the explicit call below is still required.
    """
    await add_article(
        session,
        ticket_id=ticket_id,
        article=ArticleIn(
            sender_type="agent",
            is_visible_for_customer=False,
            subject="AI-Automatisierung reaktiviert",
            body="Die KI-Automatisierung wurde manuell reaktiviert.",
            channel="note",
        ),
        user_id=user_id,
        sysconfig=sysconfig,
    )
    await clear_ai_escalated(session, ticket_id)


async def pause_ai_automation(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Stop all automatic AI actions on one ticket (auto-reply, triage, auto-summary).

    Manual Assist, refine and manual summaries stay available. Independent of
    the handoff flag; nothing but :func:`unpause_ai_automation` lifts it.
    Writes an *internal* note for the audit trail. Already paused: no-op
    (no second note).
    """
    # State first: if it fails, no orphan note is left behind. The note is
    # written only when this call really changed the state, so two concurrent
    # requests produce one note.
    if not await set_ai_paused(session, ticket_id, user_id):
        return
    await add_article(
        session,
        ticket_id=ticket_id,
        article=ArticleIn(
            sender_type="agent",
            is_visible_for_customer=False,
            subject="KI-Automatik pausiert",
            body=(
                "Die KI-Automatik wurde für dieses Ticket pausiert. "
                "Neue Kundennachrichten werden nicht automatisch bearbeitet."
            ),
            channel="note",
        ),
        user_id=user_id,
        sysconfig=sysconfig,
    )


async def unpause_ai_automation(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Lift the per-ticket AI pause. Not paused: no-op (no note).

    Customer messages that arrived during the pause are not processed
    retroactively (their outbox events were consumed); the note says so.
    """
    # State first (see pause_ai_automation); note only if this call lifted it.
    if not await clear_ai_paused(session, ticket_id):
        return
    await add_article(
        session,
        ticket_id=ticket_id,
        article=ArticleIn(
            sender_type="agent",
            is_visible_for_customer=False,
            subject="KI-Automatik fortgesetzt",
            body=(
                "Die KI-Automatik wurde für dieses Ticket fortgesetzt. "
                "Nachrichten, die während der Pause eingegangen sind, "
                "werden nicht nachträglich bearbeitet."
            ),
            channel="note",
        ),
        user_id=user_id,
        sysconfig=sysconfig,
    )


AUTOPILOT_MAX_RUNS: Final = 20
"""Upper bound for one release (``start_ai_autopilot(runs=…)``)."""


async def unanswered_customer_article(
    session: AsyncSession, ticket_id: int
) -> tuple[int, str] | None:
    """``(article_id, channel)`` of the newest customer article when no
    customer-visible agent article followed it, else ``None``. ``channel`` is
    the lower-cased communication channel, as the ArticleCreate payload has it."""
    row = (
        await session.execute(
            text(
                "SELECT a.id, LOWER(cc.name) FROM article a"
                " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                " LEFT JOIN communication_channel cc ON cc.id = a.communication_channel_id"
                " WHERE a.ticket_id = :tid AND st.name = 'customer'"
                " ORDER BY a.id DESC LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        return None
    answered = (
        await session.execute(
            text(
                "SELECT 1 FROM article a"
                " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                " WHERE a.ticket_id = :tid AND a.id > :aid"
                " AND st.name = 'agent' AND a.is_visible_for_customer = 1 LIMIT 1"
            ),
            {"tid": ticket_id, "aid": int(row[0])},
        )
    ).first()
    return None if answered is not None else (int(row[0]), str(row[1] or ""))


async def stop_ai_autopilot(
    session: AsyncSession, *, ticket_id: int, user_id: int, sysconfig: SysConfig
) -> None:
    """Stop the AI autopilot on one ticket: the pause of
    :func:`pause_ai_automation` (one note, idempotent) plus the end of any
    running release, so a later start begins from a fresh N."""
    await pause_ai_automation(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
    await clear_ai_grant(session, ticket_id)


async def start_ai_autopilot(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
    runs: int,
    answer_latest: bool = False,
) -> None:
    """Switch the AI autopilot back on for ``runs`` automatic replies.

    Lifts a pause and a handoff and grants a release that replaces the
    queue's per-ticket caps until it is used up (tiqora.ai.auto_worker). With
    ``answer_latest`` the newest customer article gets one run right away,
    unless an agent already answered it: its outbox event was consumed while
    the AI was off, so an internal event asks the worker again and the loop
    guard is reset for it. Writes one internal note. Does not commit.
    """
    if not 1 <= runs <= AUTOPILOT_MAX_RUNS:
        raise ValueError(f"runs must be between 1 and {AUTOPILOT_MAX_RUNS}")
    await clear_ai_paused(session, ticket_id)
    await clear_ai_escalated(session, ticket_id)
    await set_ai_grant(session, ticket_id, user_id, runs)
    await add_article(
        session,
        ticket_id=ticket_id,
        article=ArticleIn(
            sender_type="agent",
            is_visible_for_customer=False,
            subject="KI-Autopilot eingeschaltet",
            body=(
                f"Der KI-Autopilot wurde für {runs} automatische "
                f"{'Antwort' if runs == 1 else 'Antworten'} eingeschaltet. "
                "Danach übergibt die KI wieder an das Team."
            ),
            channel="note",
        ),
        user_id=user_id,
        sysconfig=sysconfig,
    )
    if not answer_latest:
        return
    pending = await unanswered_customer_article(session, ticket_id)
    if pending is None:
        return
    article_id, channel = pending
    await session.execute(
        text(
            "UPDATE tiqora_ai_ticket_state SET last_customer_article_id = NULL"
            " WHERE ticket_id = :tid AND last_customer_article_id >= :aid"
        ),
        {"tid": ticket_id, "aid": article_id},
    )
    await _emit_event(
        session, AI_REPLY_REQUEST_EVENT, ticket_id, {"article_id": article_id, "channel": channel}
    )


# ---------------------------------------------------------------------------
# Sub-task 3: field mutations
# ---------------------------------------------------------------------------


async def move_queue(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_queue_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Move ticket to a new queue."""
    t = await _ticket_must_exist(session, ticket_id)
    old_queue_id = int(t["queue_id"])
    old_queue = await _queue_name(session, old_queue_id)
    new_queue = await _queue_name(session, new_queue_id)

    await session.execute(
        text(
            "UPDATE ticket SET queue_id = :qid, change_time = current_timestamp, change_by = :uid"
            " WHERE id = :tid"
        ),
        {"qid": new_queue_id, "uid": user_id, "tid": ticket_id},
    )
    await add_move(
        session,
        ticket_id=ticket_id,
        new_queue=new_queue,
        new_queue_id=new_queue_id,
        old_queue=old_queue,
        old_queue_id=old_queue_id,
        user_id=user_id,
    )
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    # Znuny only notifies queue subscribers while the ticket is not closed.
    if await _state_type_name(session, int(t["ticket_state_id"])) != "closed":
        await _emit_legacy_notification(
            session, "NotificationMove", ticket_id, {"queue": new_queue}
        )
    await _emit_event(session, "TicketQueueUpdate", ticket_id, {"queue_id": new_queue_id})


async def change_state(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_state_id: int,
    user_id: int,
    sysconfig: SysConfig,
    pending_time: datetime | None = None,
) -> None:
    """Change ticket state; zero escalation on close; handle pending_time."""
    t = await _ticket_must_exist(session, ticket_id)
    old_state_name = await _state_name(session, int(t["ticket_state_id"]))
    new_state_name = await _state_name(session, new_state_id)
    new_state_type = await _state_type_name(session, new_state_id)

    until_time = 0
    if pending_time is not None and new_state_type.lower().startswith("pending"):
        until_time = int(pending_time.timestamp())

    await session.execute(
        text(
            "UPDATE ticket SET ticket_state_id = :sid, until_time = :ut,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"sid": new_state_id, "ut": until_time, "uid": user_id, "tid": ticket_id},
    )
    await add_state_update(
        session,
        ticket_id=ticket_id,
        old_state=old_state_name,
        new_state=new_state_name,
        user_id=user_id,
        state_id=new_state_id,
    )
    if pending_time is not None and new_state_type.lower().startswith("pending"):
        await add_pending_time(
            session,
            ticket_id=ticket_id,
            year=pending_time.year,
            month=pending_time.month,
            day=pending_time.day,
            hour=pending_time.hour,
            minute=pending_time.minute,
            user_id=user_id,
        )
    elif not new_state_type.lower().startswith("pending"):
        # Port of Ticket::EventModulePost###3300-TicketPendingTimeReset:
        # on every TicketStateUpdate to a non-pending state, Znuny resets the
        # pending time via TicketPendingTimeSet('0000-00-00 00:00:00'), which
        # writes a SetPendingTime history row `%%00-00-00 00:00` even if
        # until_time was already 0. Golden-master validated against 6.5.22.
        await add_pending_time(
            session,
            ticket_id=ticket_id,
            year=0,
            month=0,
            day=0,
            hour=0,
            minute=0,
            user_id=user_id,
        )
    # Recompute escalation (close zeroes it out)
    await escalation_index_build(session, ticket_id, user_id, sysconfig)
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketStateUpdate", ticket_id, {"state_id": new_state_id})

    # Ticket is done (closed), no longer relevant (merged) or gone (removed)
    # — clear the AI handoff flag (see tiqora.ai.handoff).
    if new_state_type.lower() in ("closed", "merged", "removed"):
        await clear_ai_escalated(session, ticket_id)
        await clear_ai_grant(session, ticket_id)


async def change_priority(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_priority_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Change ticket priority."""
    t = await _ticket_must_exist(session, ticket_id)
    old_prio_id = int(t["ticket_priority_id"])
    old_prio = await _priority_name(session, old_prio_id)
    new_prio = await _priority_name(session, new_priority_id)

    await session.execute(
        text(
            "UPDATE ticket SET ticket_priority_id = :pid, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"pid": new_priority_id, "uid": user_id, "tid": ticket_id},
    )
    await add_priority_update(
        session,
        ticket_id=ticket_id,
        old_priority=old_prio,
        old_priority_id=old_prio_id,
        new_priority=new_prio,
        new_priority_id=new_priority_id,
        user_id=user_id,
    )
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketPriorityUpdate", ticket_id, {"priority_id": new_priority_id})


async def _lookup_name_or_null(session: AsyncSession, table: str, row_id: int | None) -> str:
    """Return row name, or the Znuny sentinel ``NULL`` when missing."""
    if not row_id:
        return "NULL"
    try:
        row = await _lookup_row(session, table, "id", int(row_id))
    except InvalidInput:
        return "NULL"
    name = row.get("name")
    return str(name) if name else "NULL"


async def change_type(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_type_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Change ticket type (Znuny TypeUpdate history)."""
    del sysconfig
    t = await _ticket_must_exist(session, ticket_id)
    old_type_id = int(t["type_id"]) if t.get("type_id") is not None else 0
    if old_type_id == int(new_type_id):
        return
    # Validate target exists.
    new_type = await _lookup_name_or_null(session, "ticket_type", new_type_id)
    if new_type == "NULL":
        raise InvalidInput(f"unknown ticket type id {new_type_id}")
    old_type = await _lookup_name_or_null(session, "ticket_type", old_type_id or None)

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
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketTypeUpdate", ticket_id, {"type_id": new_type_id})


async def change_service(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_service_id: int | None,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Change ticket service (Znuny ServiceUpdate history).

    ``new_service_id=None`` clears the service (writes NULL to the column).
    """
    del sysconfig
    t = await _ticket_must_exist(session, ticket_id)
    old_svc_id = int(t["service_id"]) if t.get("service_id") is not None else 0
    new_id = int(new_service_id) if new_service_id is not None else 0
    if old_svc_id == new_id:
        return
    if new_service_id is not None:
        new_svc = await _lookup_name_or_null(session, "service", new_service_id)
        if new_svc == "NULL":
            raise InvalidInput(f"unknown service id {new_service_id}")
    else:
        new_svc = "NULL"
    old_svc = await _lookup_name_or_null(session, "service", old_svc_id or None)

    await session.execute(
        text(
            "UPDATE ticket SET service_id = :s, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"s": new_service_id, "uid": user_id, "tid": ticket_id},
    )
    await add_service_update(
        session,
        ticket_id=ticket_id,
        new_service=new_svc,
        new_service_id=new_id or "",
        old_service=old_svc,
        old_service_id=old_svc_id or "",
        user_id=user_id,
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_legacy_notification(session, "NotificationServiceUpdate", ticket_id)
    await _emit_event(session, "TicketServiceUpdate", ticket_id, {"service_id": new_service_id})


async def change_sla(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_sla_id: int | None,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Change ticket SLA (Znuny SLAUpdate history).

    ``new_sla_id=None`` clears the SLA. Callers should recompute escalation
    after SLA changes when needed (compat GI does the same sequence).
    """
    t = await _ticket_must_exist(session, ticket_id)
    old_sla_id = int(t["sla_id"]) if t.get("sla_id") is not None else 0
    new_id = int(new_sla_id) if new_sla_id is not None else 0
    if old_sla_id == new_id:
        return
    if new_sla_id is not None:
        new_sla = await _lookup_name_or_null(session, "sla", new_sla_id)
        if new_sla == "NULL":
            raise InvalidInput(f"unknown sla id {new_sla_id}")
    else:
        new_sla = "NULL"
    old_sla = await _lookup_name_or_null(session, "sla", old_sla_id or None)

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
        new_sla_id=new_id or "",
        old_sla=old_sla,
        old_sla_id=old_sla_id or "",
        user_id=user_id,
    )
    await escalation_index_build(session, ticket_id, user_id, sysconfig)
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketSLAUpdate", ticket_id, {"sla_id": new_sla_id})


async def change_title(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_title: str,
    user_id: int,
) -> None:
    """Change ticket title (port of ``Ticket.pm::TicketTitleUpdate``).

    No-op (no UPDATE, no history, no event) when the title is unchanged: Znuny returns
    early on ``$Ticket{Title} eq $Param{Title}`` (Ticket.pm:1883)."""
    t = await _ticket_must_exist(session, ticket_id)
    old_title = str(t.get("title") or "")
    title_truncated = (replace_non_bmp(new_title) or "")[:255]
    if t.get("title") is not None and old_title == title_truncated:
        return

    await session.execute(
        text(
            "UPDATE ticket SET title = :title, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"title": title_truncated, "uid": user_id, "tid": ticket_id},
    )
    await add_title_update(
        session,
        ticket_id=ticket_id,
        old_title=old_title,
        new_title=title_truncated,
        user_id=user_id,
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketTitleUpdate", ticket_id, {"title": title_truncated})


async def set_customer(
    session: AsyncSession,
    *,
    ticket_id: int,
    customer_id: str | None,
    customer_user_id: str | None,
    user_id: int,
    only_given: bool = False,
) -> None:
    """Set ticket customer (``Ticket.pm::TicketCustomerSet``).

    ``only_given=True`` is Znuny's exact shape: a ``None`` value leaves its column alone
    (``No`` / ``User`` are each optional, Ticket.pm:3488-3513). The default writes both
    columns, a ``None`` as NULL (the historical Tiqora behaviour other callers rely on)."""
    await _ticket_must_exist(session, ticket_id)
    if only_given:
        if customer_id is None and customer_user_id is None:
            return
        sets = [
            column
            for column, value in (
                ("customer_id = :cid", customer_id),
                ("customer_user_id = :cuid", customer_user_id),
            )
            if value is not None
        ]
        await session.execute(
            text(
                f"UPDATE ticket SET {', '.join(sets)},"  # noqa: S608 -- fixed column names
                " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
            ),
            {"cid": customer_id, "cuid": customer_user_id, "uid": user_id, "tid": ticket_id},
        )
    else:
        await session.execute(
            text(
                "UPDATE ticket SET customer_id = :cid, customer_user_id = :cuid,"
                " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
            ),
            {"cid": customer_id, "cuid": customer_user_id, "uid": user_id, "tid": ticket_id},
        )
    await add_customer_update(
        session,
        ticket_id=ticket_id,
        customer_id=customer_id,
        customer_user=customer_user_id,
        user_id=user_id,
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(
        session,
        "TicketCustomerUpdate",
        ticket_id,
        {"customer_id": customer_id, "customer_user_id": customer_user_id},
    )


async def assign_owner(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_owner_id: int,
    user_id: int,
    sysconfig: SysConfig,
    lock: bool = False,
) -> None:
    """Assign ticket owner (port of ``Ticket.pm::TicketOwnerSet``).

    Golden-master validated behaviours (Znuny 6.5.22):

    - No-op (no UPDATE, no history) when the ticket already has that owner —
      Znuny returns 2 without any write.
    - ``TicketOwnerSet`` itself never locks the ticket; auto-locking is a
      *frontend* behaviour (AgentTicketOwner with a lock config), not part of
      the core write. ``lock=True`` remains available for callers that want
      the explicit combined behaviour (writes the same rows Znuny's frontend
      would via a separate TicketLockSet call), but defaults to False.
    """
    t = await _ticket_must_exist(session, ticket_id)

    # Znuny: "check if update is needed!" — same owner is a silent no-op.
    if int(t["user_id"]) == new_owner_id:
        return

    new_login = await _user_login(session, new_owner_id)

    # Optional explicit lock (frontend-equivalent): lock if currently unlocked.
    current_lock_id = int(t["ticket_lock_id"])
    new_lock_id = current_lock_id
    if lock and current_lock_id == 1:  # 1=unlock
        new_lock_id = 2  # 2=lock

    await session.execute(
        text(
            "UPDATE ticket SET user_id = :oid, ticket_lock_id = :lid,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"oid": new_owner_id, "lid": new_lock_id, "uid": user_id, "tid": ticket_id},
    )
    await add_owner_update(
        session,
        ticket_id=ticket_id,
        new_user=new_login,
        new_user_id=new_owner_id,
        user_id=user_id,
    )
    if new_lock_id != current_lock_id:
        await add_lock(session, ticket_id=ticket_id, lock="lock", user_id=user_id)
        await ticket_unlock_timeout_update(
            session, ticket_id=ticket_id, unlock_timeout=int(time.time()), user_id=user_id
        )
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_legacy_notification(
        session,
        "NotificationOwnerUpdate",
        ticket_id,
        {"owner_id": new_owner_id},
        skip_user_ids=(user_id,) if user_id == new_owner_id else (),
    )
    await _emit_event(session, "TicketOwnerUpdate", ticket_id, {"owner_id": new_owner_id})


async def assign_responsible(
    session: AsyncSession,
    *,
    ticket_id: int,
    new_responsible_id: int,
    user_id: int,
) -> None:
    """Assign ticket responsible."""
    await _ticket_must_exist(session, ticket_id)
    new_login = await _user_login(session, new_responsible_id)

    await session.execute(
        text(
            "UPDATE ticket SET responsible_user_id = :rid,"
            " change_time = current_timestamp, change_by = :uid WHERE id = :tid"
        ),
        {"rid": new_responsible_id, "uid": user_id, "tid": ticket_id},
    )
    await add_responsible_update(
        session,
        ticket_id=ticket_id,
        new_user=new_login,
        new_user_id=new_responsible_id,
        user_id=user_id,
    )
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_legacy_notification(
        session,
        "NotificationResponsibleUpdate",
        ticket_id,
        {"responsible_id": new_responsible_id},
        skip_user_ids=(user_id,) if user_id == new_responsible_id else (),
    )
    await _emit_event(
        session, "TicketResponsibleUpdate", ticket_id, {"responsible_id": new_responsible_id}
    )


async def ticket_unlock_timeout_update(
    session: AsyncSession, *, ticket_id: int, unlock_timeout: int, user_id: int
) -> None:
    """Port of ``Ticket.pm::TicketUnlockTimeoutUpdate``.

    Silent no-op when ``ticket.timeout`` already equals ``unlock_timeout``; otherwise
    writes it (bumping change_time/change_by) plus a Misc "Reset of unlock time." row.
    """
    current_timeout = (
        await session.execute(
            text("SELECT timeout FROM ticket WHERE id = :tid"), {"tid": ticket_id}
        )
    ).scalar_one()
    if int(current_timeout or 0) == unlock_timeout:
        return
    await session.execute(
        text(
            "UPDATE ticket SET timeout = :to, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"to": unlock_timeout, "uid": user_id, "tid": ticket_id},
    )
    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_MISC,
        name="Reset of unlock time.",
        user_id=user_id,
    )
    # Ticket.pm:1960 clears the ticket cache and fires TicketUnlockTimeoutUpdate
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketUnlockTimeoutUpdate", ticket_id)


async def lock_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Lock a ticket (port of ``Ticket.pm::TicketLockSet``, Lock => 'lock').

    Znuny: "check if update is needed" — a ticket that is already locked
    (lock or tmp_lock) is a silent no-op: no UPDATE, no history row.
    """
    t = await _ticket_must_exist(session, ticket_id)
    if int(t["ticket_lock_id"]) != 1:  # 1=unlock
        return
    await session.execute(
        text(
            "UPDATE ticket SET ticket_lock_id = 2, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"uid": user_id, "tid": ticket_id},
    )
    await add_lock(session, ticket_id=ticket_id, lock="lock", user_id=user_id)
    # TicketLockSet (Lock => 'lock') restarts the unlock timeout at the current epoch second.
    await ticket_unlock_timeout_update(
        session, ticket_id=ticket_id, unlock_timeout=int(time.time()), user_id=user_id
    )
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketLockUpdate", ticket_id, {"lock": "lock"})


async def set_ticket_lock(
    session: AsyncSession,
    *,
    ticket_id: int,
    lock_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """``TicketLockSet`` for an arbitrary lock type (e.g. tmp_lock); no-op if unchanged.

    History type follows Ticket.pm:4226-4241 (Lock / Unlock, else Misc) with ``%%<name>``.
    """
    if lock_id == 1:
        await unlock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
        return
    if lock_id == 2:
        await lock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
        return
    t = await _ticket_must_exist(session, ticket_id)
    if int(t["ticket_lock_id"]) == lock_id:
        return
    name_row = (
        await session.execute(
            text("SELECT name FROM ticket_lock_type WHERE id = :i"), {"i": lock_id}
        )
    ).first()
    if name_row is None:
        raise InvalidInput(f"Unknown ticket lock id: {lock_id!r}")
    await session.execute(
        text(
            "UPDATE ticket SET ticket_lock_id = :l, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"l": lock_id, "uid": user_id, "tid": ticket_id},
    )
    await add_lock(session, ticket_id=ticket_id, lock=str(name_row[0]), user_id=user_id)
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketLockUpdate", ticket_id, {"lock": str(name_row[0])})


async def unlock_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Unlock a ticket (``TicketLockSet`` Lock => 'unlock'; same-state no-op)."""
    t = await _ticket_must_exist(session, ticket_id)
    if int(t["ticket_lock_id"]) == 1:  # already unlocked — Znuny no-op
        return
    await session.execute(
        text(
            "UPDATE ticket SET ticket_lock_id = 1, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"uid": user_id, "tid": ticket_id},
    )
    await add_lock(session, ticket_id=ticket_id, lock="unlock", user_id=user_id)
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    owner_id = int(t["user_id"]) if t.get("user_id") is not None else None
    await _emit_legacy_notification(
        session,
        "NotificationLockTimeout",
        ticket_id,
        skip_user_ids=(user_id,) if owner_id == user_id else (),
    )
    await _emit_event(session, "TicketLockUpdate", ticket_id, {"lock": "unlock"})


@dataclass(frozen=True)
class AcquireLockResult:
    """Outcome of a composer-open lock acquisition (see :func:`acquire_lock`)."""

    result: str  # not_required | acquired | already_mine | taken_over | locked_by_other
    locked_by_id: int | None = None
    locked_by_name: str | None = None


async def _user_display_name(session: AsyncSession, user_id: int) -> str:
    row = (
        await session.execute(
            text("SELECT first_name, last_name, login FROM users WHERE id = :uid LIMIT 1"),
            {"uid": user_id},
        )
    ).first()
    if row is None:
        return f"#{user_id}"
    first, last, login = row
    full = " ".join(p for p in [first, last] if p)
    return full or str(login)


async def acquire_lock(
    session: AsyncSession,
    *,
    ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
    action: str,
    takeover: bool = False,
) -> AcquireLockResult:
    """Composer-open lock acquisition (port of the Znuny frontend behaviour).

    Znuny screens with ``RequiredLock`` (AgentTicketCompose/Forward/Bounce/
    Close via AgentTicketActionCommon) lock an unlocked ticket on open and
    make the opening agent its owner — ``TicketLockSet('lock')`` first, then
    ``TicketOwnerSet(self)``, so the history order is Lock, OwnerUpdate.

    Outcomes:

    - ``not_required`` — the action's RequiredLock sysconfig is off; no write.
    - ``acquired`` — was unlocked: now locked, caller is owner.
    - ``already_mine`` — locked (lock/tmp_lock) and the caller is the owner.
    - ``locked_by_other`` — locked by someone else and ``takeover`` is False;
      ``locked_by_*`` name the holder. No write.
    - ``taken_over`` — locked by someone else and ``takeover`` is True: owner
      moves to the caller (Znuny AgentTicketOwner semantics), lock stays.
    """
    if not await sysconfig.required_lock(action):
        return AcquireLockResult(result="not_required")

    t = await _ticket_must_exist(session, ticket_id)
    owner_id = int(t["user_id"])
    locked = int(t["ticket_lock_id"]) != 1

    if locked:
        if owner_id == user_id:
            return AcquireLockResult(result="already_mine")
        if not takeover:
            return AcquireLockResult(
                result="locked_by_other",
                locked_by_id=owner_id,
                locked_by_name=await _user_display_name(session, owner_id),
            )
        await assign_owner(
            session,
            ticket_id=ticket_id,
            new_owner_id=user_id,
            user_id=user_id,
            sysconfig=sysconfig,
        )
        return AcquireLockResult(result="taken_over")

    # Unlocked: lock first, then owner — Znuny AgentTicketActionCommon order.
    await lock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
    if owner_id != user_id:
        await assign_owner(
            session,
            ticket_id=ticket_id,
            new_owner_id=user_id,
            user_id=user_id,
            sysconfig=sysconfig,
        )
    return AcquireLockResult(result="acquired")


async def watch_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    watcher_user_id: int,
    user_id: int,
) -> None:
    """Subscribe a user to a ticket (watcher)."""
    await _ticket_must_exist(session, ticket_id)
    # Upsert: ignore if already watching
    row = (
        await session.execute(
            text("SELECT 1 FROM ticket_watcher WHERE ticket_id = :tid AND user_id = :wuid LIMIT 1"),
            {"tid": ticket_id, "wuid": watcher_user_id},
        )
    ).first()
    if row is None:
        await session.execute(
            text(
                "INSERT INTO ticket_watcher (ticket_id, user_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :wuid, current_timestamp, :uid, current_timestamp, :uid)"
            ),
            {"tid": ticket_id, "wuid": watcher_user_id, "uid": user_id},
        )
    # Get full name for history
    name_row = (
        await session.execute(
            text("SELECT first_name, last_name FROM users WHERE id = :uid LIMIT 1"),
            {"uid": watcher_user_id},
        )
    ).first()
    fullname = f"{name_row[0]} {name_row[1]}".strip() if name_row else str(watcher_user_id)
    await add_subscribe(session, ticket_id=ticket_id, user_fullname=fullname, user_id=user_id)
    await _emit_event(session, "TicketSubscribe", ticket_id, {"watcher_user_id": watcher_user_id})
    await invalidate_ticket_cache(session, ticket_id)


async def unwatch_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    watcher_user_id: int,
    user_id: int,
) -> None:
    """Unsubscribe a user from a ticket."""
    await _ticket_must_exist(session, ticket_id)
    await session.execute(
        text("DELETE FROM ticket_watcher WHERE ticket_id = :tid AND user_id = :wuid"),
        {"tid": ticket_id, "wuid": watcher_user_id},
    )
    name_row = (
        await session.execute(
            text("SELECT first_name, last_name FROM users WHERE id = :uid LIMIT 1"),
            {"uid": watcher_user_id},
        )
    ).first()
    fullname = f"{name_row[0]} {name_row[1]}".strip() if name_row else str(watcher_user_id)
    await add_unsubscribe(session, ticket_id=ticket_id, user_fullname=fullname, user_id=user_id)
    await _emit_event(session, "TicketUnsubscribe", ticket_id, {"watcher_user_id": watcher_user_id})
    await invalidate_ticket_cache(session, ticket_id)


async def archive_ticket(
    session: AsyncSession,
    *,
    ticket_id: int,
    archive: bool,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Set archive flag on a ticket."""
    await _ticket_must_exist(session, ticket_id)
    flag_int = 1 if archive else 0
    flag_str = "y" if archive else "n"
    await session.execute(
        text(
            "UPDATE ticket SET archive_flag = :fl, change_time = current_timestamp,"
            " change_by = :uid WHERE id = :tid"
        ),
        {"fl": flag_int, "uid": user_id, "tid": ticket_id},
    )
    await add_archive_flag_update(
        session, ticket_id=ticket_id, archive_flag=flag_str, user_id=user_id
    )
    await ticket_accelerator_update(session, ticket_id, sysconfig)
    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "TicketArchiveFlagUpdate", ticket_id, {"archive": archive})


async def update_dynamic_field(
    session: AsyncSession,
    *,
    ticket_id: int,
    field_name: str,
    values: list[str],
    user_id: int,
) -> None:
    """Set a dynamic field like Znuny (typed columns, no-op when unchanged).

    Delegates to :func:`tiqora.domain.dynamic_field_values.set_ticket_dynamic_field`.
    Unknown field names are silently ignored (Znuny behaviour); an invalid
    value for the field type raises :class:`InvalidInput` and writes nothing.
    """
    field = await load_ticket_dynamic_field(session, field_name)
    if field is None:
        return  # Silently ignore unknown fields (Znuny behaviour)
    try:
        await set_ticket_dynamic_field(
            session, ticket_id=ticket_id, field=field, values=values, user_id=user_id
        )
    except DynamicFieldValueError as exc:
        raise InvalidInput(f"{field_name}: {exc}") from exc


async def update_dynamic_fields_strict(
    session: AsyncSession,
    *,
    ticket_id: int,
    values: Mapping[str, list[str]],
    user_id: int,
) -> None:
    """Agent-facing dynamic-field write (PATCH): like AgentTicketFreeText / GI TicketUpdate.

    Unlike :func:`update_dynamic_field` (silent for unknown names, used by internal
    callers such as process management), an unknown field is rejected and
    Dropdown / Multiselect values must be keys of the field's ``PossibleValues``
    (``BaseSelect.pm::EditFieldValueValidate``; empty values pass). Raises
    :class:`InvalidInput` before anything is written for the offending field; the
    caller's transaction rolls earlier fields back.
    """
    for field_name, field_values in values.items():
        field = await load_ticket_dynamic_field(session, field_name)
        if field is None:
            raise InvalidInput(f"Unbekanntes Feld „{field_name}“.")
        for v in await values_outside_possible_values(session, field, field_values):
            raise InvalidInput(f"{field_name}: „{v}“ ist kein erlaubter Wert.")
        await update_dynamic_field(
            session,
            ticket_id=ticket_id,
            field_name=field_name,
            values=field_values,
            user_id=user_id,
        )


# ---------------------------------------------------------------------------
# Sub-task 4: merge_tickets — exact port of TicketMerge
# ---------------------------------------------------------------------------


async def merge_tickets(
    session: AsyncSession,
    *,
    main_ticket_id: int,
    merge_ticket_id: int,
    user_id: int,
    sysconfig: SysConfig,
) -> None:
    """Merge merge_ticket_id into main_ticket_id.

    Exact port of Kernel/System/Ticket.pm::TicketMerge:
    1. Move articles (ticket_id) from merge → main
    2. Move article_search_index rows
    3. Move article-linked ticket_history rows (article_id IS NOT NULL)
    4. Move time_accounting rows
    4b. Move tiqora_telegram_message map rows (Tiqora-only) and the
        tiqora_chat_session / tiqora_chat_message rows
    5. Add merge-note article to merge ticket
    6. Write Merged history on both tickets
    7. Transfer watchers (skip duplicates)
    8. Update main ticket change_time
    9. Set merge ticket state to first 'merged' state type
    10. Unlock merge ticket
    11. Emits TicketMerge outbox event
    """
    main = await _ticket_must_exist(session, main_ticket_id)
    merge = await _ticket_must_exist(session, merge_ticket_id)
    main_tn = str(main["tn"])
    merge_tn = str(merge["tn"])

    # 1. Move articles
    await session.execute(
        text(
            "UPDATE article SET ticket_id = :main, change_time = current_timestamp,"
            " change_by = :uid WHERE ticket_id = :merge"
        ),
        {"main": main_ticket_id, "uid": user_id, "merge": merge_ticket_id},
    )

    # 2. Move article_search_index
    await session.execute(
        text("UPDATE article_search_index SET ticket_id = :main WHERE ticket_id = :merge"),
        {"main": main_ticket_id, "merge": merge_ticket_id},
    )

    # 3. Move article-linked history rows (WHERE article_id IS NOT NULL AND article_id != 0)
    await session.execute(
        text(
            "UPDATE ticket_history SET ticket_id = :main, change_time = current_timestamp,"
            " change_by = :uid WHERE ticket_id = :merge"
            " AND article_id IS NOT NULL AND article_id != 0"
        ),
        {"main": main_ticket_id, "uid": user_id, "merge": merge_ticket_id},
    )

    # 4. Move time_accounting
    await session.execute(
        text(
            "UPDATE time_accounting SET ticket_id = :main, change_time = current_timestamp,"
            " change_by = :uid WHERE ticket_id = :merge"
        ),
        {"main": main_ticket_id, "uid": user_id, "merge": merge_ticket_id},
    )

    # 4b. Tiqora: the articles' Telegram map rows follow them (not in Znuny;
    # the table may be unmigrated on an install, like in delete_article).
    if await _table_exists(session, "tiqora_telegram_message"):
        await session.execute(
            text("UPDATE tiqora_telegram_message SET ticket_id = :main WHERE ticket_id = :merge"),
            {"main": main_ticket_id, "merge": merge_ticket_id},
        )
    # 4c. Same for the WebChat transcript (messages first: they reference the
    # session by id only, no FK, so the order is irrelevant but kept stable).
    for chat_table in ("tiqora_chat_message", "tiqora_chat_session"):
        if await _table_exists(session, chat_table):
            await session.execute(
                text(f"UPDATE {chat_table} SET ticket_id = :main WHERE ticket_id = :merge"),
                {"main": main_ticket_id, "merge": merge_ticket_id},
            )

    # 5. Add merge-note article to merge ticket (internal channel, agent sender)
    merge_note = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject="Ticket Merged",
        body=f"Ticket {merge_tn} was merged into ticket {main_tn}.",
        content_type="text/plain; charset=ascii",
        channel="note",
    )
    await add_article(
        session,
        ticket_id=merge_ticket_id,
        article=merge_note,
        user_id=user_id,
        sysconfig=sysconfig,
    )

    # 6. Merged history on both tickets (same name format on both — Znuny does this)
    await add_merged(
        session,
        ticket_id=merge_ticket_id,
        main_tn=main_tn,
        main_ticket_id=main_ticket_id,
        merge_tn=merge_tn,
        merge_ticket_id=merge_ticket_id,
        user_id=user_id,
    )
    await add_merged(
        session,
        ticket_id=main_ticket_id,
        main_tn=main_tn,
        main_ticket_id=main_ticket_id,
        merge_tn=merge_tn,
        merge_ticket_id=merge_ticket_id,
        user_id=user_id,
    )

    # 7. Transfer watchers: remove duplicates, then reassign remaining
    main_watcher_rows = (
        await session.execute(
            text("SELECT user_id FROM ticket_watcher WHERE ticket_id = :tid"),
            {"tid": main_ticket_id},
        )
    ).fetchall()
    main_watcher_ids = {int(r[0]) for r in main_watcher_rows}

    # Delete from merge ticket any that already watch main
    for wid in main_watcher_ids:
        await session.execute(
            text("DELETE FROM ticket_watcher WHERE ticket_id = :tid AND user_id = :wuid"),
            {"tid": merge_ticket_id, "wuid": wid},
        )

    # Move remaining merge watchers to main
    await session.execute(
        text("UPDATE ticket_watcher SET ticket_id = :main WHERE ticket_id = :merge"),
        {"main": main_ticket_id, "merge": merge_ticket_id},
    )

    # 8. Update main ticket change_time
    await session.execute(
        text("UPDATE ticket SET change_time = current_timestamp, change_by = :uid WHERE id = :tid"),
        {"uid": user_id, "tid": main_ticket_id},
    )

    # 9. Set merge ticket state to first 'merged' state type
    merged_state_row = (
        await session.execute(
            text(
                "SELECT ts.id, ts.name FROM ticket_state ts"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE tst.name = 'merged' AND ts.valid_id = 1"
                " ORDER BY ts.id LIMIT 1"
            )
        )
    ).first()
    if merged_state_row is not None:
        merged_state_id = int(merged_state_row[0])
        await change_state(
            session,
            ticket_id=merge_ticket_id,
            new_state_id=merged_state_id,
            user_id=user_id,
            sysconfig=sysconfig,
        )

    # 10. Unlock merge ticket
    await unlock_ticket(session, ticket_id=merge_ticket_id, user_id=user_id, sysconfig=sysconfig)

    # Cache invalidation for both
    await invalidate_ticket_cache(session, main_ticket_id)
    await invalidate_ticket_cache(session, merge_ticket_id)

    # 11. Outbox event
    await _emit_event(
        session,
        "TicketMerge",
        merge_ticket_id,
        {"main_ticket_id": main_ticket_id, "merge_ticket_id": merge_ticket_id},
    )


async def forward_article(
    session: AsyncSession,
    *,
    ticket_id: int,
    subject: str,
    body: str,
    to_address: str,
    cc: str | None,
    user_id: int,
    sysconfig: SysConfig,
) -> int:
    """Forward: add a customer-visible email article, history type 'Forward'.

    Simplification vs Znuny's AgentTicketForward: attachments from the source
    article are NOT carried over (no MTA integration in this vertical).
    """
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject=subject,
        body=body,
        content_type="text/plain; charset=utf-8",
        to_address=to_address,
        cc=cc,
        channel="email",
        history_type_override="Forward",
    )
    return await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )


async def bounce_article(
    session: AsyncSession,
    *,
    ticket_id: int,
    subject: str,
    body: str,
    content_type: str,
    to_address: str,
    user_id: int,
    sysconfig: SysConfig,
) -> int:
    """Bounce: resend the original article body verbatim, history type 'Bounce'.

    Faithful subset of Znuny's AgentTicketBounce — no RFC822 header
    preservation / true remail (no outbound MTA in this vertical); the body is
    resent unchanged to the new recipient.
    """
    article = ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject=subject,
        body=body,
        content_type=content_type,
        to_address=to_address,
        channel="email",
        history_type_override="Bounce",
    )
    return await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )


async def _table_exists(session: AsyncSession, table: str) -> bool:
    """Whether *table* exists (Tiqora-owned tables may be unmigrated per install)."""
    row = (
        await session.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :t LIMIT 1"),
            {"t": table},
        )
    ).first()
    return row is not None


async def delete_article(
    session: AsyncSession,
    *,
    ticket_id: int,
    article_id: int,
    user_id: int,
) -> None:
    """Hard-delete one internal-note article and its dependent rows.

    Only articles on the ``Internal`` communication channel with
    ``is_visible_for_customer = 0`` may be deleted — customer communication is
    never removable via this path (raises :class:`ArticleNotDeletable`).
    Cascading delete order (children first): article_flag,
    article_data_mime_attachment, article_data_mime_plain,
    article_search_index, tiqora_ai_article_origin, tiqora_telegram_message
    (both if migrated),
    time_accounting (article_id set to NULL — booked time is kept, not lost),
    ticket_history rows for the article, article_data_mime, article.

    Writes a ``Misc`` ticket_history row (``%%NoteDeleted%%<id>%%<subject>``)
    and emits an ``ArticleDelete`` outbox event so Meilisearch drops the
    article from the index on next drain.
    """
    await _ticket_must_exist(session, ticket_id)

    art = (
        (
            await session.execute(
                text(
                    "SELECT a.ticket_id, a.is_visible_for_customer, cc.name AS channel_name"
                    " FROM article a"
                    " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
                    " WHERE a.id = :aid LIMIT 1"
                ),
                {"aid": article_id},
            )
        )
        .mappings()
        .first()
    )
    if art is None or int(art["ticket_id"]) != ticket_id:
        raise TicketNotFound(f"article {article_id}")
    if art["channel_name"] != "Internal" or int(art["is_visible_for_customer"]) != 0:
        raise ArticleNotDeletable(
            "Only internal notes (not visible to the customer) can be deleted"
        )

    mime = (
        await session.execute(
            text("SELECT a_subject FROM article_data_mime WHERE article_id = :aid LIMIT 1"),
            {"aid": article_id},
        )
    ).first()
    subject = str(mime[0] or "") if mime is not None else ""

    await session.execute(
        text("DELETE FROM article_flag WHERE article_id = :aid"), {"aid": article_id}
    )
    await session.execute(
        text("DELETE FROM article_data_mime_attachment WHERE article_id = :aid"),
        {"aid": article_id},
    )
    await session.execute(
        text("DELETE FROM article_data_mime_plain WHERE article_id = :aid"),
        {"aid": article_id},
    )
    await session.execute(
        text("DELETE FROM article_search_index WHERE article_id = :aid"),
        {"aid": article_id},
    )
    if await _table_exists(session, "tiqora_ai_article_origin"):
        await session.execute(
            text("DELETE FROM tiqora_ai_article_origin WHERE article_id = :aid"),
            {"aid": article_id},
        )
    if await _table_exists(session, "tiqora_telegram_message"):
        await session.execute(
            text("DELETE FROM tiqora_telegram_message WHERE article_id = :aid"),
            {"aid": article_id},
        )
    if await _table_exists(session, "tiqora_chat_message"):
        await session.execute(
            text("DELETE FROM tiqora_chat_message WHERE article_id = :aid"),
            {"aid": article_id},
        )
    await session.execute(
        text("UPDATE time_accounting SET article_id = NULL WHERE article_id = :aid"),
        {"aid": article_id},
    )
    await session.execute(
        text("DELETE FROM ticket_history WHERE article_id = :aid"), {"aid": article_id}
    )
    await session.execute(
        text("DELETE FROM article_data_mime WHERE article_id = :aid"), {"aid": article_id}
    )
    await session.execute(text("DELETE FROM article WHERE id = :aid"), {"aid": article_id})

    await history_add(
        session,
        ticket_id=ticket_id,
        history_type=TYPE_MISC,
        name=f"%%NoteDeleted%%{article_id}%%{subject[:100]}",
        user_id=user_id,
    )

    await invalidate_ticket_cache(session, ticket_id)
    await _emit_event(session, "ArticleDelete", ticket_id, {"article_id": article_id})


async def _link_object_id(session: AsyncSession, name: str) -> int:
    """Resolve link_object.id by name, inserting the row if absent (Ticket)."""
    row = (
        await session.execute(
            text("SELECT id FROM link_object WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is not None:
        return int(row[0])
    await session.execute(text("INSERT INTO link_object (name) VALUES (:n)"), {"n": name})
    row = (
        await session.execute(
            text("SELECT id FROM link_object WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is None:
        raise RuntimeError("link_object insert read-back failed")
    return int(row[0])


async def _link_type_id(session: AsyncSession, name: str, *, user_id: int | None = None) -> int:
    """Resolve ``link_type.id``; with *user_id* an unseen name is inserted
    (``LinkObject.pm::TypeLookup`` creates the row on first use)."""
    q = text("SELECT id FROM link_type WHERE name = :n LIMIT 1")
    row = (await session.execute(q, {"n": name})).first()
    if row is None and user_id is not None:
        await session.execute(
            text(
                "INSERT INTO link_type (name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:n, 1, current_timestamp, :u, current_timestamp, :u)"
            ),
            {"n": name, "u": user_id},
        )
        row = (await session.execute(q, {"n": name})).first()
    if row is None:
        raise InvalidInput(f"Unknown link type: {name}")
    return int(row[0])


async def _link_state_id(session: AsyncSession, name: str) -> int:
    row = (
        await session.execute(
            text("SELECT id FROM link_state WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    if row is None:
        raise InvalidInput(f"Unknown link state: {name}")
    return int(row[0])


async def _link_object_id_if_exists(session: AsyncSession, name: str) -> int | None:
    row = (
        await session.execute(
            text("SELECT id FROM link_object WHERE name = :n LIMIT 1"), {"n": name}
        )
    ).first()
    return None if row is None else int(row[0])


async def _ticket_link_history(
    session: AsyncSession,
    *,
    history_type: str,
    ticket_id: str,
    other_ticket_id: str,
    user_id: int,
) -> None:
    """``LinkObject/Ticket.pm`` ``LinkAddPost`` / ``LinkDeletePost``: history on *ticket_id*,
    named ``%%<TN of the other ticket>%%<other id>%%<this id>``."""
    if not (ticket_id.isdigit() and other_ticket_id.isdigit()):
        return
    rows = {
        int(r[0]): str(r[1])
        for r in (
            await session.execute(
                text("SELECT id, tn FROM ticket WHERE id IN (:a, :b)"),
                {"a": int(ticket_id), "b": int(other_ticket_id)},
            )
        ).all()
    }
    if int(ticket_id) not in rows or int(other_ticket_id) not in rows:
        return  # TicketNumberLookup of a vanished ticket -> nothing to write the history on
    await history_add(
        session,
        ticket_id=int(ticket_id),
        history_type=history_type,
        name=f"%%{rows[int(other_ticket_id)]}%%{other_ticket_id}%%{ticket_id}",
        user_id=user_id,
    )


async def link_objects(
    session: AsyncSession,
    *,
    source_object: str,
    source_key: str,
    target_object: str,
    target_key: str,
    link_type: str,
    user_id: int,
    state: str = "Valid",
) -> bool:
    """Insert a ``link_relation`` row between any two linkable objects (idempotent).

    Port of ``Kernel/System/LinkObject.pm::LinkAdd`` (``PossibleTypesList`` for the type,
    the existing-link check in both directions, the ``TypeGroup`` check) plus
    ``LinkObject/Ticket.pm::LinkAddPost`` (``TicketLinkAdd`` history on every Ticket end whose
    other end is a Ticket, not for ``Temporary`` links). Object, type and state ids are resolved
    by name (an unseen object or configured type gets its row, like Znuny's lookups); the stored
    direction is source -> target as given.

    Returns ``False`` when nothing was written (self link, or the pair already has this link:
    an unpointed type in the other direction counts as the same link). Raises
    :class:`LinkConflict` where Znuny logs a notice and refuses: type not possible for the two
    objects, same type in another state, pointed type in the opposite direction, or another
    type of the same ``TypeGroup``. Reads and writes run in the caller's transaction.
    """
    if source_object == target_object and str(source_key) == str(target_key):
        return False  # LinkObject.pm::LinkAdd: "Can't link an object with itself"
    cfg = await read_link_config(SysConfig(session))
    if not cfg.allows(link_type, source_object, target_object):
        raise LinkConflict("not_possible")
    src_obj_id = await _link_object_id(session, source_object)
    tgt_obj_id = await _link_object_id(session, target_object)
    type_id = await _link_type_id(session, link_type, user_id=user_id)
    state_id = await _link_state_id(session, state)
    params = {
        "so": src_obj_id,
        "to": tgt_obj_id,
        "sk": str(source_key),
        "tk": str(target_key),
        "t": type_id,
    }
    existing = (
        (
            await session.execute(
                text(
                    "SELECT source_object_id, source_key, state_id FROM link_relation"
                    " WHERE ((source_object_id = :so AND source_key = :sk"
                    " AND target_object_id = :to AND target_key = :tk)"
                    " OR (source_object_id = :to AND source_key = :tk"
                    " AND target_object_id = :so AND target_key = :sk))"
                    " AND type_id = :t LIMIT 1"
                ),
                params,
            )
        )
        .mappings()
        .first()
    )
    if existing is not None:
        if int(existing["state_id"]) != state_id:
            raise LinkConflict("state")
        if not cfg.pointed(link_type):
            return False
        if int(existing["source_object_id"]) == src_obj_id and str(existing["source_key"]) == str(
            source_key
        ):
            return False
        raise LinkConflict("opposite_direction")
    # Type groups: another link of this state between the same two objects.
    if cfg.groups:
        others = (
            await session.execute(
                text(
                    "SELECT DISTINCT lt.name FROM link_relation lr"
                    " JOIN link_type lt ON lt.id = lr.type_id"
                    " WHERE ((lr.source_object_id = :so AND lr.source_key = :sk"
                    " AND lr.target_object_id = :to AND lr.target_key = :tk)"
                    " OR (lr.source_object_id = :to AND lr.source_key = :tk"
                    " AND lr.target_object_id = :so AND lr.target_key = :sk))"
                    " AND lr.state_id = :s"
                ),
                {**params, "s": state_id},
            )
        ).all()
        for (other_type,) in others:
            if cfg.same_group(str(other_type), link_type):
                raise LinkConflict("type_group")
    await session.execute(
        text(
            "INSERT INTO link_relation (source_object_id, source_key, target_object_id,"
            " target_key, type_id, state_id, create_time, create_by)"
            " VALUES (:so, :sk, :to, :tk, :t, :s, current_timestamp, :uid)"
        ),
        {**params, "s": state_id, "uid": user_id},
    )
    # LinkObject.pm clears Cache::LinkListRaw...; the Znuny side drops the whole CacheType.
    await invalidate_cache_type(session, "LinkObject")
    if state != "Temporary" and source_object == "Ticket" and target_object == "Ticket":
        await _ticket_link_history(
            session,
            history_type="TicketLinkAdd",
            ticket_id=str(source_key),
            other_ticket_id=str(target_key),
            user_id=user_id,
        )
        await _ticket_link_history(
            session,
            history_type="TicketLinkAdd",
            ticket_id=str(target_key),
            other_ticket_id=str(source_key),
            user_id=user_id,
        )
    # Both linked tickets must refresh in Znuny (link list on each side).
    if source_object == "Ticket" and str(source_key).isdigit():
        if target_object == "Ticket":
            data: dict[str, Any] = {"target_ticket_id": int(target_key)}
        else:
            data = {"target_object": target_object, "target_key": str(target_key)}
        await _emit_event(session, "LinkAdd", int(source_key), data)
        await invalidate_ticket_cache(session, int(source_key))
    if target_object == "Ticket" and str(target_key).isdigit():
        if source_object != "Ticket":
            await _emit_event(
                session,
                "LinkAdd",
                int(target_key),
                {"source_object": source_object, "source_key": str(source_key)},
            )
        await invalidate_ticket_cache(session, int(target_key))
    return True


async def unlink_objects(
    session: AsyncSession,
    *,
    object1: str,
    key1: str,
    object2: str,
    key2: str,
    link_type: str,
    user_id: int,
) -> int:
    """Delete the link of *link_type* between two objects, stored in either direction.

    Port of ``Kernel/System/LinkObject.pm::LinkDelete`` (no state filter: a ``Valid`` and a
    ``Temporary`` link go alike; a missing link is no error) plus
    ``LinkObject/Ticket.pm::LinkDeletePost`` (``TicketLinkDelete`` history on every Ticket end
    whose other end is a Ticket, not for ``Temporary`` links). Returns the number of rows
    removed, ``0`` when there was nothing to remove.
    """
    obj1 = await _link_object_id_if_exists(session, object1)
    obj2 = await _link_object_id_if_exists(session, object2)
    type_row = (
        await session.execute(
            text("SELECT id FROM link_type WHERE name = :n LIMIT 1"), {"n": link_type}
        )
    ).first()
    if obj1 is None or obj2 is None or type_row is None:
        return 0
    params = {"o1": obj1, "k1": str(key1), "o2": obj2, "k2": str(key2), "t": int(type_row[0])}

    def _pair(alias: str) -> str:
        return (
            f" WHERE (({alias}source_object_id = :o1 AND {alias}source_key = :k1"
            f" AND {alias}target_object_id = :o2 AND {alias}target_key = :k2)"
            f" OR ({alias}source_object_id = :o2 AND {alias}source_key = :k2"
            f" AND {alias}target_object_id = :o1 AND {alias}target_key = :k1))"
            f" AND {alias}type_id = :t"
        )

    states = [
        str(r[0])
        for r in (
            await session.execute(
                text(
                    "SELECT ls.name FROM link_relation lr"
                    " JOIN link_state ls ON ls.id = lr.state_id" + _pair("lr.")
                ),
                params,
            )
        ).all()
    ]
    if not states:
        return 0
    result = await session.execute(text("DELETE FROM link_relation" + _pair("")), params)
    removed = int(getattr(result, "rowcount", 0) or 0) or len(states)
    await invalidate_cache_type(session, "LinkObject")
    if any(st != "Temporary" for st in states) and object1 == "Ticket" and object2 == "Ticket":
        for this, other in ((key1, key2), (key2, key1)):
            await _ticket_link_history(
                session,
                history_type="TicketLinkDelete",
                ticket_id=str(this),
                other_ticket_id=str(other),
                user_id=user_id,
            )
    for this_obj, this_key, other_obj, other_key in (
        (object1, key1, object2, key2),
        (object2, key2, object1, key1),
    ):
        if this_obj == "Ticket" and str(this_key).isdigit():
            if other_obj == "Ticket":
                data: dict[str, Any] = {"target_ticket_id": int(other_key)}
            else:
                data = {"target_object": other_obj, "target_key": str(other_key)}
            await _emit_event(session, "LinkDelete", int(this_key), data)
            await invalidate_ticket_cache(session, int(this_key))
    return removed


async def link_tickets(
    session: AsyncSession,
    *,
    source_ticket_id: int,
    target_ticket_id: int,
    link_type: str,
    user_id: int,
) -> None:
    """Insert a ticket↔ticket link_relation row (idempotent, state 'Valid')."""
    await link_objects(
        session,
        source_object="Ticket",
        source_key=str(source_ticket_id),
        target_object="Ticket",
        target_key=str(target_ticket_id),
        link_type=link_type,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# Permission-aware write service (used by REST endpoints)
# ---------------------------------------------------------------------------


def _article_op(ticket_id: int | None, article: ArticleIn) -> Any:
    """The hook-facing view of an article (no addresses besides to/cc, no attachment bytes)."""
    from tiqora.plugins.sync_hooks import ArticleOp

    return ArticleOp(
        ticket_id=ticket_id,
        channel=article.channel,
        sender_type=article.sender_type,
        visible_for_customer=article.is_visible_for_customer,
        subject=article.subject,
        body=article.body,
        content_type=article.content_type,
        to=article.to_address,
        cc=article.cc,
        attachment_names=tuple(a[0] for a in article.attachments),
    )


class TicketWriteService:
    """Session-scoped service for all ticket mutations, with permission checks."""

    def __init__(
        self,
        session: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
        sysconfig: SysConfig,
        mail_sender: Any | None = None,
    ) -> None:
        self._session = session
        self._factory = session_factory
        self._sysconfig = sysconfig
        self._mail_sender = mail_sender
        self._perms = PermissionEngine(session)

    async def _assert(self, user_id: int, queue_id: int, key: str) -> None:
        """Raise TicketAccessDenied unless *user_id* holds *key* on *queue_id*.

        ``rw`` on the queue's group satisfies any key (PermissionEngine).
        """
        if not await self._perms.check(user_id, queue_id, key):
            raise TicketAccessDenied(f"user {user_id} lacks {key} on queue {queue_id}")

    async def _assert_rw(self, user_id: int, queue_id: int) -> None:
        await self._assert(user_id, queue_id, "rw")

    async def _assert_create(self, user_id: int, queue_id: int) -> None:
        await self._assert(user_id, queue_id, "create")

    def _resolve_mail_sender(self) -> Any:
        """Return the test/injectable sender override, or ``None``.

        When ``None``, :func:`deliver_agent_email_reply` builds an
        :class:`SmtpMailSender` from DB outbound settings (preferred) or
        env ``TIQORA_SMTP_*``.
        """
        return self._mail_sender

    # -- plugin before-hooks (D4): only here, never in the module-level write functions ------

    async def _hook_ticket(self, ticket_id: int) -> Any:
        from tiqora.plugins.context import PluginTickets

        summary = await PluginTickets().get(ticket_id, session=self._session)
        if summary is None:  # cannot happen after _ticket_must_exist
            raise TicketNotFound(ticket_id)
        return summary

    async def _hook_create_ticket(self, user_id: int, params: TicketIn) -> TicketIn:
        """``ticket.create``: run the hooks, apply the (validated) enrichment to ``params``."""
        from tiqora.plugins.sync_hooks import TicketCreateOp, run_sync_hooks

        session = self._session
        art = params.article
        op = TicketCreateOp(
            queue_id=params.queue_id,
            queue=await _queue_name(session, params.queue_id),
            title=params.title,
            state=await _state_name(session, params.state_id),
            priority=await _priority_name(session, params.priority_id),
            type=(
                str((await _lookup_row(session, "ticket_type", "id", params.type_id))["name"])
                if params.type_id is not None
                else None
            ),
            customer_user=params.customer_user_id,
            customer_id=params.customer_id,
            dynamic_fields={k: tuple(v) for k, v in params.dynamic_fields.items()},
            article=_article_op(None, art) if art is not None else None,
        )
        new = await run_sync_hooks("ticket.create", op, agent_id=user_id, session=session)
        changes: dict[str, Any] = {}
        if new.title != op.title:
            changes["title"] = new.title
        if new.priority != op.priority:
            changes["priority_id"] = int(
                (await _lookup_row(session, "ticket_priority", "name", new.priority))["id"]
            )
        if new.type != op.type and new.type is not None:
            changes["type_id"] = int(
                (await _lookup_row(session, "ticket_type", "name", new.type))["id"]
            )
        if dict(new.dynamic_fields) != dict(op.dynamic_fields):
            changes["dynamic_fields"] = {k: list(v) for k, v in new.dynamic_fields.items()}
        return replace(params, **changes) if changes else params

    async def create_ticket(self, user_id: int, params: TicketIn) -> int:
        await self._assert_create(user_id, params.queue_id)
        from tiqora.plugins.sync_hooks import has_sync_hooks

        if has_sync_hooks("ticket.create"):
            params = await self._hook_create_ticket(user_id, params)
        return await create_ticket(
            self._session,
            self._factory,
            self._sysconfig,
            params=params,
            user_id=user_id,
        )

    async def add_article(self, user_id: int, ticket_id: int, article: ArticleIn) -> int:
        t = await _ticket_must_exist(self._session, ticket_id)
        # Znuny: reply / note require the ``note`` permission key (rw implies).
        await self._assert(user_id, int(t["queue_id"]), "note")
        # Plugin before-hooks run before ANY branch below: email/Telegram/chat are
        # send-then-store, so a veto after the send could not be undone.
        from tiqora.plugins.sync_hooks import has_sync_hooks, run_sync_hooks

        if has_sync_hooks("article.create"):
            await run_sync_hooks(
                "article.create",
                _article_op(ticket_id, article),
                agent_id=user_id,
                session=self._session,
            )
        # Outgoing agent reply on a plugin channel (D11): the plugin sends, then we store.
        if article.sender_type == "agent" and article.is_visible_for_customer:
            from tiqora.plugins.channels import deliver_plugin_channel_reply, driver_for

            plugin_driver = driver_for(article.channel)
            if plugin_driver is not None:
                return await deliver_plugin_channel_reply(
                    self._session,
                    self._sysconfig,
                    plugin_driver,
                    ticket_id=ticket_id,
                    user_id=user_id,
                    article=article,
                )
        # Outgoing agent email: SMTP deliver then store (see outbound_reply).
        if article.channel.lower() == "email" and article.sender_type == "agent":
            from tiqora.channels.email.outbound_reply import deliver_agent_email_reply

            return await deliver_agent_email_reply(
                self._session,
                self._sysconfig,
                self._resolve_mail_sender(),
                ticket_id=ticket_id,
                queue_id=int(t["queue_id"]),
                user_id=user_id,
                article=article,
            )
        # Outgoing agent Telegram reply: Bot API send then store (see
        # channels.telegram.outbound) -- send-then-store, same as email above.
        if article.channel.lower() == "telegram" and article.sender_type == "agent":
            from tiqora.channels.telegram.outbound import deliver_agent_telegram_reply

            return await deliver_agent_telegram_reply(
                self._session,
                self._sysconfig,
                ticket_id=ticket_id,
                user_id=user_id,
                article=article,
            )
        # An agent's customer-visible web-chat line also goes into the live chat
        # (transcript + visitor stream); only exactly this combination -- notes,
        # email and every other channel never reach the visitor.
        if (
            article.channel.lower() == "webchat"
            and article.sender_type == "agent"
            and article.is_visible_for_customer
        ):
            from tiqora.webchat.agent import ChatNotLive, deliver_agent_chat_reply

            try:
                return await deliver_agent_chat_reply(
                    self._session,
                    self._sysconfig,
                    ticket_id=ticket_id,
                    user_id=user_id,
                    article=article,
                )
            except ChatNotLive as exc:
                raise InvalidInput("chat_not_live") from exc
        return await add_article(
            self._session,
            ticket_id=ticket_id,
            article=article,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def move_queue(self, user_id: int, ticket_id: int, new_queue_id: int) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        # Znuny: move requires ``move_into`` on the SOURCE queue and on the
        # DESTINATION queue — Znuny builds the move-target list from the queues
        # the agent holds ``move_into`` on, so without the destination check an
        # agent could push tickets into queues they have no rights on. Validate
        # the destination exists first (``InvalidInput`` -> "no such queue")
        # before the permission check, so a bad queue id is never reported as a
        # misleading "access denied".
        await self._assert(user_id, int(t["queue_id"]), "move_into")
        await _queue_name(self._session, new_queue_id)
        await self._assert(user_id, new_queue_id, "move_into")
        from tiqora.plugins.sync_hooks import QueueMoveOp, has_sync_hooks, run_sync_hooks

        if has_sync_hooks("ticket.queue_move"):
            await run_sync_hooks(
                "ticket.queue_move",
                QueueMoveOp(
                    ticket=await self._hook_ticket(ticket_id),
                    new_queue_id=new_queue_id,
                    new_queue=await _queue_name(self._session, new_queue_id),
                ),
                agent_id=user_id,
                session=self._session,
            )
        await move_queue(
            self._session,
            ticket_id=ticket_id,
            new_queue_id=new_queue_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def validate_state_change(
        self,
        user_id: int,
        ticket_id: int,
        new_state_id: int,
        pending_time: datetime | None = None,
        run_hooks: bool = False,
    ) -> None:
        """Pre-flight :meth:`change_state` without writing anything.

        ``run_hooks=True`` also runs the plugins' ``ticket.state_change`` before-hooks (a veto
        raises ``OperationVetoed``); the caller then passes ``hooks_checked=True`` to
        :meth:`change_state` so they do not fire twice.

        Used by "reply + next state" (``POST /tickets/{id}/articles`` with
        ``state_id``) so a bad state is rejected BEFORE the article is sent —
        email/Telegram delivery is send-then-store and cannot be rolled back.
        Checks the same things :meth:`change_state` would fail on (ticket
        exists, ``rw`` on its queue, state exists) plus that a pending-type
        state comes with a ``pending_time`` (``InvalidInput`` otherwise).
        """
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await _state_name(self._session, new_state_id)  # InvalidInput if unknown
        new_state_type = await _state_type_name(self._session, new_state_id)
        if new_state_type.lower().startswith("pending") and pending_time is None:
            raise InvalidInput(f"pending_time is required for pending state {new_state_id}")
        if run_hooks:
            await self._hook_state_change(
                user_id, ticket_id, new_state_id, new_state_type, pending_time
            )

    async def check_state_change_hooks(
        self,
        user_id: int,
        ticket_id: int,
        new_state_id: int,
        pending_time: datetime | None = None,
    ) -> None:
        """Run only the plugins' ``ticket.state_change`` before-hooks for this change.

        A veto raises ``OperationVetoed``; the caller then passes ``hooks_checked=True`` to
        :meth:`change_state`. For callers that did their own checks and must hear a veto
        before an earlier write of the same unit (the ITSM linked-articles job writes the
        article first, R16). :meth:`validate_state_change` is the variant with all checks.
        """
        new_state_type = await _state_type_name(self._session, new_state_id)
        await self._hook_state_change(
            user_id, ticket_id, new_state_id, new_state_type, pending_time
        )

    async def _hook_state_change(
        self,
        user_id: int,
        ticket_id: int,
        new_state_id: int,
        new_state_type: str,
        pending_time: datetime | None,
    ) -> None:
        from tiqora.plugins.sync_hooks import StateChangeOp, has_sync_hooks, run_sync_hooks

        if not has_sync_hooks("ticket.state_change"):
            return
        await run_sync_hooks(
            "ticket.state_change",
            StateChangeOp(
                ticket=await self._hook_ticket(ticket_id),
                new_state=await _state_name(self._session, new_state_id),
                new_state_type=new_state_type,
                pending_until=pending_time,
            ),
            agent_id=user_id,
            session=self._session,
        )

    async def change_state(
        self,
        user_id: int,
        ticket_id: int,
        new_state_id: int,
        pending_time: datetime | None = None,
        hooks_checked: bool = False,
    ) -> None:
        """Change the state; plugins' ``ticket.state_change`` before-hooks run for every caller
        (REST, MCP, webchat close, plugin ``as_agent``) and a veto raises ``OperationVetoed``.

        ``hooks_checked=True``: ``validate_state_change(..., run_hooks=True)`` already ran
        the plugin before-hooks for exactly this change (reply + next state)."""
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        # Frontend close parity (AgentTicketActionCommon): the close screen
        # carries RequiredLock — opening it on an unlocked ticket locks it and
        # makes the agent owner — and after the state change a closed ticket is
        # unlocked again. Workers/process/AI call the module-level change_state
        # and stay core-faithful (no lock/owner side effects).
        new_state_type = await _state_type_name(self._session, new_state_id)
        if not hooks_checked:
            await self._hook_state_change(
                user_id, ticket_id, new_state_id, new_state_type, pending_time
            )
        closing = new_state_type.lower().startswith("close")
        if closing:
            await acquire_lock(
                self._session,
                ticket_id=ticket_id,
                user_id=user_id,
                sysconfig=self._sysconfig,
                action="close",
            )
        await change_state(
            self._session,
            ticket_id=ticket_id,
            new_state_id=new_state_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
            pending_time=pending_time,
        )
        if closing:
            await unlock_ticket(
                self._session,
                ticket_id=ticket_id,
                user_id=user_id,
                sysconfig=self._sysconfig,
            )

    async def change_priority(self, user_id: int, ticket_id: int, new_priority_id: int) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert(user_id, int(t["queue_id"]), "priority")
        await change_priority(
            self._session,
            ticket_id=ticket_id,
            new_priority_id=new_priority_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def change_type(self, user_id: int, ticket_id: int, new_type_id: int) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await change_type(
            self._session,
            ticket_id=ticket_id,
            new_type_id=new_type_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def change_service(
        self, user_id: int, ticket_id: int, new_service_id: int | None
    ) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await change_service(
            self._session,
            ticket_id=ticket_id,
            new_service_id=new_service_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def change_sla(self, user_id: int, ticket_id: int, new_sla_id: int | None) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await change_sla(
            self._session,
            ticket_id=ticket_id,
            new_sla_id=new_sla_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def change_title(self, user_id: int, ticket_id: int, new_title: str) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await change_title(
            self._session,
            ticket_id=ticket_id,
            new_title=new_title,
            user_id=user_id,
        )

    async def set_customer(
        self,
        user_id: int,
        ticket_id: int,
        *,
        customer_id: str | None,
        customer_user_id: str | None,
    ) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await set_customer(
            self._session,
            ticket_id=ticket_id,
            customer_id=customer_id,
            customer_user_id=customer_user_id,
            user_id=user_id,
        )

    async def assign_owner(
        self,
        user_id: int,
        ticket_id: int,
        new_owner_id: int,
        *,
        lock: bool = False,
    ) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert(user_id, int(t["queue_id"]), "owner")
        await assign_owner(
            self._session,
            ticket_id=ticket_id,
            new_owner_id=new_owner_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
            lock=lock,
        )

    async def assign_responsible(
        self, user_id: int, ticket_id: int, new_responsible_id: int
    ) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert(user_id, int(t["queue_id"]), "owner")
        await assign_responsible(
            self._session,
            ticket_id=ticket_id,
            new_responsible_id=new_responsible_id,
            user_id=user_id,
        )

    async def acquire_lock(
        self,
        user_id: int,
        ticket_id: int,
        *,
        action: str,
        takeover: bool = False,
    ) -> AcquireLockResult:
        """Composer-open acquisition — see module :func:`acquire_lock`.

        ``rw`` gates the acquisition itself (the composer screens are rw
        screens); a takeover is an owner change and therefore additionally
        requires the Znuny ``owner`` permission key, like ``assign_owner``.
        """
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        if takeover:
            await self._assert(user_id, int(t["queue_id"]), "owner")
        return await acquire_lock(
            self._session,
            ticket_id=ticket_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
            action=action,
            takeover=takeover,
        )

    async def lock_ticket(
        self, user_id: int, ticket_id: int, *, take_ownership: bool = False
    ) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await lock_ticket(
            self._session,
            ticket_id=ticket_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )
        # Znuny AgentTicketLock "Sperren": locking through the menu also makes
        # the agent the owner (TicketLockSet, then TicketOwnerSet). Unlock
        # deliberately leaves the owner untouched.
        if take_ownership and int(t["user_id"]) != user_id:
            await assign_owner(
                self._session,
                ticket_id=ticket_id,
                new_owner_id=user_id,
                user_id=user_id,
                sysconfig=self._sysconfig,
            )

    async def unlock_ticket(self, user_id: int, ticket_id: int) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await unlock_ticket(
            self._session,
            ticket_id=ticket_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def archive_ticket(self, user_id: int, ticket_id: int, archive: bool) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await archive_ticket(
            self._session,
            ticket_id=ticket_id,
            archive=archive,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def _hook_dynamic_fields(
        self, user_id: int, ticket_id: int, values: Mapping[str, Sequence[str]]
    ) -> None:
        from tiqora.plugins.sync_hooks import DynamicFieldsOp, has_sync_hooks, run_sync_hooks

        if not has_sync_hooks("ticket.dynamic_fields_update"):
            return
        await run_sync_hooks(
            "ticket.dynamic_fields_update",
            DynamicFieldsOp(
                ticket=await self._hook_ticket(ticket_id),
                values={k: tuple(v) for k, v in values.items()},
            ),
            agent_id=user_id,
            session=self._session,
        )

    async def update_dynamic_field(
        self,
        user_id: int,
        ticket_id: int,
        *,
        field_name: str,
        values: list[str],
    ) -> None:
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await self._hook_dynamic_fields(user_id, ticket_id, {field_name: values})
        await update_dynamic_fields_strict(
            self._session, ticket_id=ticket_id, values={field_name: values}, user_id=user_id
        )

    async def update_dynamic_fields(
        self,
        user_id: int,
        ticket_id: int,
        values: Mapping[str, list[str]],
    ) -> None:
        """Set several dynamic fields; ``rw`` is asserted once.

        Raises :class:`InvalidInput` on the first bad value; the caller's
        transaction (``session.begin()``) then rolls every field back.
        """
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await self._hook_dynamic_fields(user_id, ticket_id, values)
        await update_dynamic_fields_strict(
            self._session, ticket_id=ticket_id, values=values, user_id=user_id
        )

    async def merge_tickets(self, user_id: int, main_ticket_id: int, merge_ticket_id: int) -> None:
        main = await _ticket_must_exist(self._session, main_ticket_id)
        merge = await _ticket_must_exist(self._session, merge_ticket_id)
        await self._assert_rw(user_id, int(main["queue_id"]))
        await self._assert_rw(user_id, int(merge["queue_id"]))
        await merge_tickets(
            self._session,
            main_ticket_id=main_ticket_id,
            merge_ticket_id=merge_ticket_id,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )

    async def forward_article(
        self,
        user_id: int,
        ticket_id: int,
        *,
        subject: str,
        body: str,
        to_address: str,
        cc: str | None = None,
        email_security: EmailSecurityIn | None = None,
        email_security_explicit: bool = False,
    ) -> int:
        """Forward by email: SMTP send-then-store like a reply (history 'Forward').

        Goes through :func:`deliver_agent_email_reply`, so the forward is
        really sent when outbound mail is enabled (Znuny AgentTicketForward)
        and can be signed/encrypted (``email_security``).
        """
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        from tiqora.channels.email.outbound_reply import deliver_agent_email_reply

        return await deliver_agent_email_reply(
            self._session,
            self._sysconfig,
            self._resolve_mail_sender(),
            ticket_id=ticket_id,
            queue_id=int(t["queue_id"]),
            user_id=user_id,
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=True,
                subject=subject,
                body=body,
                content_type="text/plain; charset=utf-8",
                to_address=to_address,
                cc=cc,
                channel="email",
                history_type_override="Forward",
                email_security=email_security,
                email_security_explicit=email_security_explicit,
            ),
        )

    async def bounce_article(
        self,
        user_id: int,
        ticket_id: int,
        article_id: int,
        *,
        to_address: str,
        state_id: int | None = None,
    ) -> int:
        """Resend the given article's body verbatim to ``to_address``."""
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        mime = (
            await self._session.execute(
                text(
                    "SELECT a_subject, a_body, a_content_type FROM article_data_mime"
                    " WHERE article_id = :aid LIMIT 1"
                ),
                {"aid": article_id},
            )
        ).first()
        if mime is None:
            raise TicketNotFound(f"article {article_id}")
        aid = await bounce_article(
            self._session,
            ticket_id=ticket_id,
            subject=str(mime[0] or ""),
            body=str(mime[1] or ""),
            content_type=str(mime[2] or "text/plain; charset=utf-8"),
            to_address=to_address,
            user_id=user_id,
            sysconfig=self._sysconfig,
        )
        if state_id is not None:
            await change_state(
                self._session,
                ticket_id=ticket_id,
                new_state_id=state_id,
                user_id=user_id,
                sysconfig=self._sysconfig,
            )
        return aid

    async def split_article(
        self,
        user_id: int,
        ticket_id: int,
        article_id: int,
        *,
        queue_id: int,
        title: str | None = None,
        priority_id: int | None = None,
        state_id: int | None = None,
        customer_id: str | None = None,
        customer_user_id: str | None = None,
    ) -> int:
        """Create a new ticket seeded from an existing article; link the two.

        ``priority_id``/``state_id``/``customer_*`` override the source ticket's
        values when given (else inherited). Returns the new ticket id. Requires
        ``rw`` on the source ticket and ``create`` on the target queue.
        """
        src = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(src["queue_id"]))
        await self._assert_create(user_id, queue_id)
        mime = (
            await self._session.execute(
                text(
                    "SELECT a_subject, a_body, a_content_type, a_from, a_to"
                    " FROM article_data_mime WHERE article_id = :aid LIMIT 1"
                ),
                {"aid": article_id},
            )
        ).first()
        if mime is None:
            raise TicketNotFound(f"article {article_id}")
        seeded = ArticleIn(
            sender_type="customer",
            is_visible_for_customer=True,
            subject=str(mime[0] or ""),
            body=str(mime[1] or ""),
            content_type=str(mime[2] or "text/plain; charset=utf-8"),
            from_address=mime[3],
            to_address=mime[4],
            channel="note",
        )
        new_title = title or str(src.get("title") or mime[0] or "Split ticket")
        # A new customer is only applied when the user actually picked one
        # (customer_user_id present); otherwise inherit the source customer.
        new_customer_id: str | None
        new_customer_user_id: str | None
        if customer_user_id is not None:
            new_customer_id = customer_id
            new_customer_user_id = customer_user_id
        else:
            new_customer_id = src.get("customer_id")
            new_customer_user_id = src.get("customer_user_id")
        new_ticket_id = await create_ticket(
            self._session,
            self._factory,
            self._sysconfig,
            params=TicketIn(
                title=new_title,
                queue_id=queue_id,
                state_id=state_id if state_id is not None else int(src["ticket_state_id"]),
                priority_id=priority_id
                if priority_id is not None
                else int(src["ticket_priority_id"]),
                owner_id=user_id,
                customer_id=new_customer_id,
                customer_user_id=new_customer_user_id,
                article=seeded,
            ),
            user_id=user_id,
        )
        try:
            await link_tickets(
                self._session,
                source_ticket_id=ticket_id,
                target_ticket_id=new_ticket_id,
                link_type="ParentChild",
                user_id=user_id,
            )
        except LinkConflict as exc:
            # Znuny's split screens ignore a refused LinkAdd (it logs a notice); the new ticket
            # stays, e.g. when the admin removed the ParentChild PossibleLink.
            _log.warning(
                "split_link_skipped", reason=exc.reason, ticket_id=ticket_id, new=new_ticket_id
            )
        return new_ticket_id

    async def delete_article(self, user_id: int, ticket_id: int, article_id: int) -> None:
        """Hard-delete an internal note. Requires ``rw`` on the ticket's queue."""
        t = await _ticket_must_exist(self._session, ticket_id)
        await self._assert_rw(user_id, int(t["queue_id"]))
        await delete_article(
            self._session,
            ticket_id=ticket_id,
            article_id=article_id,
            user_id=user_id,
        )

    async def link_tickets(
        self, user_id: int, ticket_id: int, target_ticket_id: int, link_type: str = "Normal"
    ) -> None:
        src = await _ticket_must_exist(self._session, ticket_id)
        tgt = await _ticket_must_exist(self._session, target_ticket_id)
        await self._assert_rw(user_id, int(src["queue_id"]))
        await self._assert_rw(user_id, int(tgt["queue_id"]))
        await link_tickets(
            self._session,
            source_ticket_id=ticket_id,
            target_ticket_id=target_ticket_id,
            link_type=link_type,
            user_id=user_id,
        )

    async def unlink_tickets(
        self, user_id: int, ticket_id: int, target_ticket_id: int, link_type: str | None = None
    ) -> int:
        """Remove the link(s) between two tickets, whichever direction they are stored in.

        ``rw`` on both tickets (same rule as :meth:`link_tickets`). *link_type* ``None`` removes
        every type between the two. Returns the number of links removed (0 = none existed).
        """
        src = await _ticket_must_exist(self._session, ticket_id)
        tgt = await _ticket_must_exist(self._session, target_ticket_id)
        await self._assert_rw(user_id, int(src["queue_id"]))
        await self._assert_rw(user_id, int(tgt["queue_id"]))
        if link_type is not None:
            types = [link_type]
        else:
            ticket_obj = await _link_object_id_if_exists(self._session, "Ticket")
            if ticket_obj is None:
                return 0
            types = [
                str(r[0])
                for r in (
                    await self._session.execute(
                        text(
                            "SELECT DISTINCT lt.name FROM link_relation lr"
                            " JOIN link_type lt ON lt.id = lr.type_id"
                            " WHERE lr.source_object_id = :o AND lr.target_object_id = :o"
                            " AND ((lr.source_key = :a AND lr.target_key = :b)"
                            " OR (lr.source_key = :b AND lr.target_key = :a))"
                        ),
                        {"o": ticket_obj, "a": str(ticket_id), "b": str(target_ticket_id)},
                    )
                ).all()
            ]
        removed = 0
        for name in types:
            removed += await unlink_objects(
                self._session,
                object1="Ticket",
                key1=str(ticket_id),
                object2="Ticket",
                key2=str(target_ticket_id),
                link_type=name,
                user_id=user_id,
            )
        return removed

    async def list_links(self, user_id: int, ticket_id: int) -> list[dict[str, Any]]:
        """Tickets linked to *ticket_id*, as shown in the ticket header.

        Viewing needs ``ro`` on this ticket; linked tickets in queues the agent
        cannot read are left out (their number/title must not leak).
        """
        src = await _ticket_must_exist(self._session, ticket_id)
        await self._assert(user_id, int(src["queue_id"]), "ro")
        obj_id = await _link_object_id(self._session, "Ticket")
        rows = (
            (
                await self._session.execute(
                    text(
                        "SELECT lr.source_key, lr.target_key, lt.name AS ltype, ls.name AS lstate"
                        " FROM link_relation lr"
                        " JOIN link_type lt ON lt.id = lr.type_id"
                        " JOIN link_state ls ON ls.id = lr.state_id"
                        " WHERE lr.source_object_id = :o AND lr.target_object_id = :o"
                        " AND (lr.source_key = :k OR lr.target_key = :k)"
                        " AND ls.name = 'Valid'"
                    ),
                    {"o": obj_id, "k": str(ticket_id)},
                )
            )
            .mappings()
            .all()
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            other_is_source = r["target_key"] == str(ticket_id)
            other_id = int(r["source_key"] if other_is_source else r["target_key"])
            trow = (
                (
                    await self._session.execute(
                        text(
                            "SELECT t.tn, t.title, t.queue_id, ts.name AS state, tst.name AS stype"
                            " FROM ticket t"
                            " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                            " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                            " WHERE t.id = :id LIMIT 1"
                        ),
                        {"id": other_id},
                    )
                )
                .mappings()
                .first()
            )
            if trow is None or not await self._perms.check(user_id, int(trow["queue_id"]), "ro"):
                continue
            # Znuny ParentChild: source = parent, target = child.
            role = None
            if r["ltype"] == "ParentChild":
                role = "parent" if other_is_source else "child"
            out.append(
                {
                    "source_key": r["source_key"],
                    "target_key": r["target_key"],
                    "link_type": r["ltype"],
                    "state": r["lstate"],
                    "other_ticket_id": other_id,
                    "other_tn": trow["tn"],
                    "other_title": trow["title"],
                    "other_state": trow["state"],
                    "other_state_type": trow["stype"],
                    "other_role": role,
                }
            )
        return out


__all__ = [
    "ArticleIn",
    "ArticleNotDeletable",
    "InvalidInput",
    "TelegramSendOptions",
    "TicketAccessDenied",
    "TicketIn",
    "TicketNotFound",
    "TicketWriteService",
    "add_article",
    "archive_ticket",
    "assign_owner",
    "assign_responsible",
    "bounce_article",
    "change_priority",
    "change_state",
    "change_title",
    "create_ticket",
    "delete_article",
    "forward_article",
    "link_objects",
    "link_tickets",
    "LinkConflict",
    "lock_ticket",
    "merge_tickets",
    "move_queue",
    "resume_ai_automation",
    "set_customer",
    "unlink_objects",
    "unlock_ticket",
    "unwatch_ticket",
    "update_dynamic_field",
    "update_dynamic_fields_strict",
    "watch_ticket",
]
