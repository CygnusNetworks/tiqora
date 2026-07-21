"""Notification engine — feature-flagged takeover of Znuny's event notifications.

Gated by the ``daemon.notifications.enabled`` tiqora_settings key (default
OFF — see ``tiqora.domain.settings_store``). Ports the parts of
``Kernel::System::Ticket::Event::NotificationEvent`` (+ its
``::Transport::Email`` backend) that matter for an unattended worker:

- ``notification_event`` / ``notification_event_item`` / ``notification_event_
  message`` (see ``scripts/database/schema.xml``): ``event_key`` rows configure
  matching (``Events``, ``StateID``/``QueueID``/``PriorityID``/``LockID``/
  ``TypeID``, ``ArticleSenderTypeID``/``ArticleIsVisibleForCustomer``),
  recipients (``Recipients`` incl. ``AgentOwner``/``AgentResponsible``/
  ``AgentWatcher``/``Customer``, plus explicit ``RecipientAgents``), and
  ``Transports`` (only ``Email`` is implemented — see Uncertainties).
- ``_NotificationFilter``: ticket-attribute OR-match per configured key, plus
  the three article filters (only evaluated for ``ArticleCreate``/
  ``ArticleSend`` events, using the ``article_id`` carried in the outbox
  event payload).
- ``_RecipientsGet`` (subset above) + per-recipient rendering via
  ``TemplateGenerator::NotificationEvent`` — ported using the same
  ``<OTRS_...>`` tag subset as postmaster auto-responses
  (``tiqora.channels.email.placeholder.expand_placeholders``, reused rather
  than duplicated).
- ``::Transport::Email``: agents get a plain notification email (Znuny writes
  no ticket_history for agent notifications; Tiqora deliberately writes one
  anyway — ``SendAgentNotification`` — for auditability, a documented
  divergence). Customers get an article via
  ``domain.ticket_write_service.add_article`` with history type
  ``SendCustomerNotification``, matching the ``ArticleSend`` call in
  ``Transport::Email::SendNotification``.

Loop-safety: each ``tiqora_event_outbox`` row is consumed exactly once via a
monotonic watermark (mirrors ``tiqora.worker.poller``'s watermark pattern),
so a rerun of the tick never re-sends for events already processed. Within
one event, recipients are deduplicated by resolved email address.

Documented simplifications (see docs/parallel-operation.md → Uncertainties):
``RecipientGroups``/``RecipientRoles`` resolve group *members* only (no
role→group expansion); ``AgentWatcher`` is resolved from the modelled
``ticket_watcher`` table (valid agents with a ``UserEmail`` preference);
``AgentMyQueues`` is not implemented (needs per-agent queue subscriptions
via personal-queues preferences, not yet consumed here); only the ``Email``
transport is supported; user notification preferences
(``Notification-<id>-Email``) are not consulted — every matched recipient
with the ``Email`` transport is notified.

Parallel-operation safety: this engine only drains ``tiqora_event_outbox``
(Tiqora-originated events). Znuny's daemon never sees those rows, so enabling
``AgentWatcher`` here cannot double-send with Znuny.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog
from prometheus_client import Counter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.email.parser import get_email_address
from tiqora.channels.email.placeholder import expand_placeholders
from tiqora.channels.email.smtp import MailSender, SmtpMailSender, build_message
from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.settings_store import (
    KEY_NOTIFICATIONS_ENABLED,
    get_setting_bool,
    get_setting_int,
    set_setting,
)
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.history import history_add
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

NOTIFICATIONS_EVENTS_PROCESSED = Counter(
    "tiqora_notifications_events_processed_total",
    "Outbox events evaluated against notification_event rows",
)
NOTIFICATIONS_SENT = Counter(
    "tiqora_notifications_sent_total", "Notifications sent", ["recipient_type"]
)
NOTIFICATIONS_ERRORS = Counter(
    "tiqora_notifications_errors_total", "Per-event or per-recipient notification errors"
)

KEY_NOTIFICATIONS_WATERMARK = "daemon.notifications.outbox_watermark"

_BATCH_SIZE = 500
_TICKET_ATTRIBUTE_FILTER_KEYS: dict[str, str] = {
    "StateID": "ticket_state_id",
    "QueueID": "queue_id",
    "PriorityID": "ticket_priority_id",
    "LockID": "ticket_lock_id",
    "TypeID": "type_id",
}
_ARTICLE_ONLY_EVENTS = {"ArticleCreate", "ArticleSend"}


@dataclass
class _Recipient:
    kind: str  # "Agent" | "Customer"
    email: str
    user_id: int | None = None
    language: str = "en"


@dataclass
class _NotificationRow:
    id: int
    name: str
    items: dict[str, list[str]] = field(default_factory=dict)


async def _next_outbox_batch(
    session: AsyncSession, after_id: int, batch_size: int
) -> list[tuple[int, str, int, dict[str, object]]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, event_type, ticket_id, payload FROM tiqora_event_outbox"
                " WHERE id > :after ORDER BY id ASC LIMIT :n"
            ),
            {"after": after_id, "n": batch_size},
        )
    ).fetchall()
    out: list[tuple[int, str, int, dict[str, object]]] = []
    for row in rows:
        payload = {}
        if row[3]:
            try:
                payload = json.loads(row[3])
            except (TypeError, ValueError):
                payload = {}
        out.append((int(row[0]), str(row[1]), int(row[2]), payload))
    return out


async def _notifications_for_event(
    session: AsyncSession, event_type: str
) -> list[_NotificationRow]:
    rows = (
        await session.execute(
            text(
                "SELECT ne.id, ne.name, nei.event_key, nei.event_value"
                " FROM notification_event ne"
                " JOIN notification_event_item nei ON nei.notification_id = ne.id"
                " WHERE ne.valid_id = 1"
                " AND ne.id IN ("
                "   SELECT notification_id FROM notification_event_item"
                "   WHERE event_key = 'Events' AND event_value = :evt"
                " )"
            ),
            {"evt": event_type},
        )
    ).fetchall()
    by_id: dict[int, _NotificationRow] = {}
    for nid, name, key, value in rows:
        nid = int(nid)
        entry = by_id.setdefault(nid, _NotificationRow(id=nid, name=str(name)))
        entry.items.setdefault(str(key), []).append(str(value))
    return list(by_id.values())


async def _ticket_row(session: AsyncSession, ticket_id: int) -> dict[str, object] | None:
    row = (
        (
            await session.execute(
                text("SELECT * FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


async def _article_row(session: AsyncSession, article_id: int) -> dict[str, object] | None:
    row = (
        await session.execute(
            text(
                "SELECT ast.name AS sender_type, a.is_visible_for_customer,"
                " a.communication_channel_id"
                " FROM article a"
                " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                " WHERE a.id = :aid"
            ),
            {"aid": article_id},
        )
    ).first()
    if row is None:
        return None
    return {
        "sender_type": str(row[0]),
        "is_visible_for_customer": int(row[1]),
        "communication_channel_id": int(row[2]) if row[2] is not None else None,
    }


def _passes_ticket_filter(notification: _NotificationRow, ticket: dict[str, object]) -> bool:
    for event_key, column in _TICKET_ATTRIBUTE_FILTER_KEYS.items():
        values = notification.items.get(event_key)
        if not values:
            continue
        ticket_value = ticket.get(column)
        if ticket_value is None or str(ticket_value) not in {str(v) for v in values}:
            return False
    return True


def _passes_article_filter(
    notification: _NotificationRow, event_type: str, article: dict[str, object] | None
) -> bool:
    if event_type not in _ARTICLE_ONLY_EVENTS:
        return True
    filters = {
        "ArticleSenderTypeID": "sender_type",
        "ArticleIsVisibleForCustomer": "is_visible_for_customer",
        "ArticleCommunicationChannelID": "communication_channel_id",
    }
    active = {k: v for k, v in filters.items() if notification.items.get(k)}
    if not active:
        return True
    if article is None:
        return False
    for event_key, field_name in active.items():
        values = {str(v) for v in notification.items[event_key]}
        if str(article.get(field_name)) not in values:
            return False
    return True


def _as_int(value: object) -> int:
    """Narrow a ``dict[str, object]`` row value (from ``.mappings()``/JSON) to int."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        return int(value)
    raise TypeError(f"Cannot convert {value!r} to int")


def _decode_pref(value: object) -> str:
    """user_preferences.preferences_value is LONGBLOB — decode bytes to str."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def _resolve_recipients(
    session: AsyncSession, notification: _NotificationRow, ticket: dict[str, object]
) -> list[_Recipient]:
    recipients: list[_Recipient] = []
    seen: set[tuple[str, str]] = set()

    async def _add_agent(user_id: int) -> None:
        # Only valid agents (mirrors Znuny: invalid users never receive mail).
        valid_row = (
            await session.execute(
                text("SELECT 1 FROM users WHERE id = :uid AND valid_id = 1"),
                {"uid": user_id},
            )
        ).first()
        if valid_row is None:
            return
        row = (
            await session.execute(
                text(
                    "SELECT preferences_key, preferences_value FROM user_preferences"
                    " WHERE user_id = :uid AND preferences_key IN ('UserEmail', 'UserLanguage')"
                ),
                {"uid": user_id},
            )
        ).fetchall()
        prefs = {str(k): _decode_pref(v) for k, v in row}
        email = prefs.get("UserEmail", "")
        if not email:
            return
        key = ("Agent", email.lower())
        if key in seen:
            return
        seen.add(key)
        recipients.append(
            _Recipient(
                kind="Agent",
                email=email,
                user_id=user_id,
                language=prefs.get("UserLanguage") or "en",
            )
        )

    recipient_kinds = set(notification.items.get("Recipients", []))
    if "AgentOwner" in recipient_kinds and ticket.get("user_id"):
        await _add_agent(_as_int(ticket["user_id"]))
    if "AgentResponsible" in recipient_kinds and ticket.get("responsible_user_id"):
        await _add_agent(_as_int(ticket["responsible_user_id"]))

    if "AgentWatcher" in recipient_kinds:
        watcher_rows = (
            await session.execute(
                text("SELECT user_id FROM ticket_watcher WHERE ticket_id = :tid"),
                {"tid": ticket["id"]},
            )
        ).fetchall()
        for (uid,) in watcher_rows:
            await _add_agent(int(uid))

    for raw_id in notification.items.get("RecipientAgents", []):
        try:
            await _add_agent(int(raw_id))
        except ValueError:
            continue

    for raw_gid in notification.items.get("RecipientGroups", []):
        try:
            group_id = int(raw_gid)
        except ValueError:
            continue
        member_rows = (
            await session.execute(
                text("SELECT user_id FROM group_user WHERE group_id = :gid"),
                {"gid": group_id},
            )
        ).fetchall()
        for (uid,) in member_rows:
            await _add_agent(int(uid))

    if "Customer" in recipient_kinds:
        email = None
        customer_login = ticket.get("customer_user_id")
        if customer_login:
            row = (
                await session.execute(
                    text("SELECT email FROM customer_user WHERE login = :login"),
                    {"login": str(customer_login)},
                )
            ).first()
            if row is not None:
                email = str(row[0])
        if not email:
            art_row = (
                await session.execute(
                    text(
                        "SELECT adm.a_from FROM article a"
                        " JOIN article_sender_type ast ON ast.id = a.article_sender_type_id"
                        " JOIN article_data_mime adm ON adm.article_id = a.id"
                        " WHERE a.ticket_id = :tid AND ast.name = 'customer'"
                        " ORDER BY a.create_time DESC LIMIT 1"
                    ),
                    {"tid": ticket["id"]},
                )
            ).first()
            if art_row is not None and art_row[0]:
                email = get_email_address(str(art_row[0]))
        if email:
            key = ("Customer", email.lower())
            if key not in seen:
                seen.add(key)
                recipients.append(_Recipient(kind="Customer", email=email))

    return recipients


async def _render_message(
    session: AsyncSession,
    sysconfig: SysConfig,
    notification: _NotificationRow,
    ticket: dict[str, object],
    language: str,
) -> tuple[str, str, str] | None:
    row = (
        await session.execute(
            text(
                "SELECT subject, text, content_type FROM notification_event_message"
                " WHERE notification_id = :nid AND language = :lang LIMIT 1"
            ),
            {"nid": notification.id, "lang": language},
        )
    ).first()
    if row is None:
        row = (
            await session.execute(
                text(
                    "SELECT subject, text, content_type FROM notification_event_message"
                    " WHERE notification_id = :nid ORDER BY language LIMIT 1"
                ),
                {"nid": notification.id},
            )
        ).first()
    if row is None:
        return None
    subject_tpl, body_tpl, content_type = str(row[0]), str(row[1]), str(row[2])

    ticket_vars = {
        "TicketNumber": str(ticket.get("tn", "")),
        "Title": str(ticket.get("title") or ""),
    }
    subject = await expand_placeholders(
        session,
        sysconfig,
        subject_tpl,
        ticket=ticket_vars,
        queue_name="",
        customer_subject="",
        customer_email_lines=[],
    )
    body = await expand_placeholders(
        session,
        sysconfig,
        body_tpl,
        ticket=ticket_vars,
        queue_name="",
        customer_subject="",
        customer_email_lines=[],
    )
    return subject, body, content_type


async def _send_to_recipient(
    session: AsyncSession,
    sysconfig: SysConfig,
    mail_sender: MailSender,
    *,
    ticket: dict[str, object],
    notification: _NotificationRow,
    recipient: _Recipient,
    user_id: int,
) -> bool:
    rendered = await _render_message(session, sysconfig, notification, ticket, recipient.language)
    if rendered is None:
        logger.info("notification_no_message_for_language", notification_id=notification.id)
        return False
    subject, body, content_type = rendered

    message = build_message(
        from_addr="Tiqora Notifications <notifications@localhost>",
        to_addrs=recipient.email,
        cc_addrs=None,
        subject=subject,
        body=body,
        content_type=content_type,
        in_reply_to=None,
    )
    await mail_sender.send(message)

    if recipient.kind == "Agent":
        await history_add(
            session,
            ticket_id=_as_int(ticket["id"]),
            history_type="SendAgentNotification",
            name=f"%%{recipient.email}"[:200],
            user_id=user_id,
        )
        NOTIFICATIONS_SENT.labels(recipient_type="agent").inc()
    else:
        visible_values = notification.items.get("IsVisibleForCustomer")
        is_visible = bool(int(visible_values[0])) if visible_values else True
        article = ArticleIn(
            sender_type="system",
            is_visible_for_customer=is_visible,
            subject=subject,
            body=body,
            content_type=content_type or "text/plain; charset=utf-8",
            from_address="Tiqora Notifications <notifications@localhost>",
            to_address=recipient.email,
            message_id=None,
            in_reply_to=None,
            channel="email",
            history_type_override="SendCustomerNotification",
        )
        await add_article(
            session,
            ticket_id=_as_int(ticket["id"]),
            article=article,
            user_id=user_id,
            sysconfig=sysconfig,
        )
        NOTIFICATIONS_SENT.labels(recipient_type="customer").inc()

    return True


async def process_event(
    session: AsyncSession,
    sysconfig: SysConfig,
    mail_sender: MailSender,
    *,
    event_type: str,
    ticket_id: int,
    payload: dict[str, object],
    user_id: int,
) -> int:
    """Evaluate all matching notification_event rows for one outbox event. Returns sent count."""
    notifications = await _notifications_for_event(session, event_type)
    if not notifications:
        return 0

    ticket = await _ticket_row(session, ticket_id)
    if ticket is None:
        return 0

    article = None
    if event_type in _ARTICLE_ONLY_EVENTS and payload.get("article_id"):
        article = await _article_row(session, _as_int(payload["article_id"]))

    sent = 0
    for notification in notifications:
        if not _passes_ticket_filter(notification, ticket):
            continue
        if not _passes_article_filter(notification, event_type, article):
            continue
        transports = set(notification.items.get("Transports", []))
        if transports and "Email" not in transports:
            continue

        recipients = await _resolve_recipients(session, notification, ticket)
        for recipient in recipients:
            try:
                if await _send_to_recipient(
                    session,
                    sysconfig,
                    mail_sender,
                    ticket=ticket,
                    notification=notification,
                    recipient=recipient,
                    user_id=user_id,
                ):
                    sent += 1
            except Exception:  # noqa: BLE001 — one bad recipient must not stop the others
                logger.exception(
                    "notification_send_failed",
                    notification_id=notification.id,
                    ticket_id=ticket_id,
                    recipient=recipient.email,
                )
                NOTIFICATIONS_ERRORS.inc()

    return sent


async def run_notifications_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    mail_sender: MailSender | None = None,
) -> dict[str, int]:
    """One scheduler tick: check the feature flag, drain new outbox events, send notifications."""
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()
    sender = mail_sender or SmtpMailSender(cfg)

    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_NOTIFICATIONS_ENABLED, False)
        if not enabled:
            logger.debug("notifications_disabled")
            return {"enabled": 0}
        watermark = await get_setting_int(session, KEY_NOTIFICATIONS_WATERMARK, 0)
        sysconfig = SysConfig(session)
        user_id = await sysconfig.postmaster_user_id()
        batch = await _next_outbox_batch(session, watermark, _BATCH_SIZE)

    totals = {"events": 0, "sent": 0, "errors": 0}
    last_id = watermark
    for event_id, event_type, ticket_id, payload in batch:
        last_id = event_id
        try:
            async with factory() as session, session.begin():
                sysconfig = SysConfig(session)
                sent = await process_event(
                    session,
                    sysconfig,
                    sender,
                    event_type=event_type,
                    ticket_id=ticket_id,
                    payload=payload,
                    user_id=user_id,
                )
            totals["events"] += 1
            totals["sent"] += sent
            NOTIFICATIONS_EVENTS_PROCESSED.inc()
        except Exception:  # noqa: BLE001 — one broken event must not stop the batch
            logger.exception("notifications_event_failed", event_id=event_id, ticket_id=ticket_id)
            totals["errors"] += 1
            NOTIFICATIONS_ERRORS.inc()

    if last_id != watermark:
        async with factory() as session:
            await set_setting(session, KEY_NOTIFICATIONS_WATERMARK, str(last_id))

    logger.info("notifications_tick", **totals)
    return totals


__all__ = ["process_event", "run_notifications_tick"]
