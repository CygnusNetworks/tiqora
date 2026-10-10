"""Chat session service: start a chat, visitor lines and uploads, offline form, contact, end.

Transaction contract (R-TX) — every function here:

- runs inside the CALLER's transaction and never calls ``db.begin()``, ``commit()`` or
  ``rollback()`` (SAVEPOINTs via ``begin_nested()`` only). An autobegun transaction is fine,
  so is ``async with db.begin():`` around the call — as long as the caller has not read on
  the session before opening that block (do pre-checks in their own block and commit it).
- raises every ``ChatError`` BEFORE its first write, so on a ``ChatError`` the caller may
  roll back (or commit an empty transaction) without leaving anything half-written;
- only queues notifications (``notify.queue_wakeup`` / ``queue_agent_event``); the caller
  commits and then calls ``notify.flush_chat_notifications(db, redis)``. Queued items of a
  transaction that is rolled back are dropped by ``notify``.

Lock order (R-LOCK-ORDER): (2) the ``tiqora_chat_session`` row (``get_chat_session(...,
for_update=True)``, then ``allocate_seq`` re-uses that lock), (3) Znuny ticket writes
(``add_article``, ``set_customer``), (4) ``insert_message``. A NEW chat writes the ticket
first and then inserts its session row — no other transaction can know that row yet, so
the order cannot deadlock.

Znuny compatibility: tickets and articles are written only through
``tiqora.domain.ticket_write_service`` (history, cache invalidation, outbox events, the
``WebChat`` channel row — ``add_article`` registers it on first use, R-CHANNEL-ROW).
Visitor lines are customer articles on the ``WebChat`` channel; the first one is a
``WebRequestCustomer`` (``NotificationNewTicket``), later ones are ``FollowUp`` with
``NoAgentNotify`` (no mail per chat line; the agent bus covers them). Chat tickets use the
Postmaster default state/priority and user 1 (deviation R-DEVIATIONS b).

GDPR: visitor data lands only in ``tiqora_chat_session`` / ``tiqora_chat_message`` and in
the ticket (all erased by ``gdpr/erasure``); notifications and logs carry ids only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

import structlog
from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from tiqora.db.tiqora.models import (
    TiqoraChatConfig,
    TiqoraChatMessage,
    TiqoraChatSession,
)
from tiqora.domain.dynamic_field_values import (
    DynamicFieldValueError,
    load_ticket_dynamic_field,
    normalize_df_values,
    values_outside_possible_values,
)
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    add_article,
    create_ticket,
    set_customer,
)
from tiqora.jobs.service import utc_now
from tiqora.webchat.availability import eligible_agent_ids, topic_ref, within_business_hours
from tiqora.webchat.models import ChatConfigValues, ChatTopic, PrechatField
from tiqora.webchat.notify import queue_agent_event, queue_wakeup
from tiqora.webchat.origins import origin_allowed
from tiqora.webchat.prechat import (
    CHECKBOX_NO,
    CHECKBOX_YES,
    EMAIL_KEY,
    PrechatInvalid,
    PrechatResult,
    resolve_chat_customer,
    validate_prechat,
)
from tiqora.webchat.schemas import ChatOfflineIn, ChatSessionCreateIn
from tiqora.webchat.store import (
    LIVE_STATUSES,
    find_by_client_id,
    get_chat_session,
    insert_message,
)
from tiqora.webchat.text import (
    CONTROL_CHARS,
    MessageRejected,
    clean_page_url,
    quoted_display_name,
    sanitize_message,
)
from tiqora.webchat.tokens import VISITOR_ID_RE, issue_visitor_token, new_session_id
from tiqora.webchat.uploads import UploadRejected, check_upload
from tiqora.znuny.sysconfig import SysConfig

__all__ = [
    "ARTICLE_CHANNEL",
    "CHANNEL",
    "COMM_CHANNEL_MODULE",
    "COMM_CHANNEL_NAME",
    "CONTACT_END_REASONS",
    "CONTACT_WINDOW_SECONDS",
    "LAST_SEEN_THROTTLE_SECONDS",
    "LIVE_STATUSES",
    "PRECHAT_BLOCK_HEADER",
    "SYSTEM_USER_ID",
    "ChatError",
    "VisitorContext",
    "add_system_message",
    "create_session",
    "current_queue_id",
    "end_by_visitor",
    "post_visitor_attachment",
    "post_visitor_message",
    "set_contact",
    "store_visitor_attachment",
    "submit_offline",
    "touch_visitor_last_seen",
    "visitor_from_address",
]

logger = structlog.get_logger(__name__)

#: ``communication_channel.name`` of webchat articles (row created on demand,
#: unregistered, ``package_name`` ``Tiqora``; see ``ensure_channel_row``).
COMM_CHANNEL_NAME: Final = "WebChat"
#: Znuny reads a webchat article through its Internal backend (plain MIME
#: article, no mail transport), so the golden test finds the right class.
COMM_CHANNEL_MODULE: Final = "Kernel::System::CommunicationChannel::Internal"
#: ``Article.channel`` value used by the ticket write service.
ARTICLE_CHANNEL: Final = "webchat"
CHANNEL: Final = ARTICLE_CHANNEL
SYSTEM_USER_ID: Final = 1

#: ``set_contact`` is allowed for this long after a chat ended without an agent's answer
#: (R-PLAN-HYGIENE): status ``ended``/``abandoned`` with one of these end reasons.
CONTACT_WINDOW_SECONDS: Final = 7200
CONTACT_END_REASONS: Final = frozenset({"no_agent", "idle", "visitor"})
_CONTACT_STATUSES: Final = frozenset({"ended", "abandoned"})
#: ``visitor_last_seen`` is written at most once per session per minute (R-LASTSEEN).
LAST_SEEN_THROTTLE_SECONDS: Final = 60

PRECHAT_BLOCK_HEADER: Final = "— Angaben aus dem Vorab-Formular —"
TITLE_PREFIX: Final = "Chat: "
TITLE_FIRST_LINE_CHARS: Final = 60
VISITOR_FALLBACK_NAME: Final = "Besucher"
#: Reserved TLD (RFC 2606): an address no mail path will ever deliver to (R-NOTIFY a).
VISITOR_ADDRESS_DOMAIN: Final = "chat.invalid"
MAX_VISITOR_NAME: Final = 200
MAX_USER_AGENT: Final = 300
_LANGUAGE_RE: Final = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8}){0,3}$")


class ChatError(Exception):
    """A refusal the API maps to ``http_status`` + ``{"detail": code[, "errors": …]}``.

    Codes: disabled 404, not_found 404, origin 403, offline 409, session_closed 409,
    invalid_topic 422, invalid_prechat 422 (errors per field), message_rejected 422,
    upload_rejected 422 (413 for "size"), forbidden 403, already_taken 409, at_capacity 409,
    not_waiting 409, chat_not_live 409, contact_not_allowed 409.
    """

    def __init__(self, code: str, http_status: int, *, errors: dict[str, str] | None = None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.errors = errors


@dataclass(frozen=True, slots=True)
class VisitorContext:
    ip_hash: str
    user_agent: str | None
    page_url: str | None
    language: str | None


# -- small pure helpers --------------------------------------------------------------------


def visitor_from_address(name: str | None, email: str | None, visitor_id: str) -> str:
    """``From`` of a visitor article: ``"Name" <email>`` (or the bare address); without an
    address ``"Name or Besucher" <visitor-<12 chars of visitor_id>@chat.invalid>``."""
    display = quoted_display_name(name)
    if email:
        return f'"{display}" <{email}>' if display else email
    local = "visitor-" + (re.sub(r"[^A-Za-z0-9_-]", "", visitor_id)[:12] or "anonym")
    return f'"{display or VISITOR_FALLBACK_NAME}" <{local}@{VISITOR_ADDRESS_DOMAIN}>'


def _clean_message(raw: str, *, key: str = "message") -> str:
    try:
        return sanitize_message(raw)
    except MessageRejected as exc:
        raise ChatError("message_rejected", 422, errors={key: exc.code}) from exc


def _clean_caption(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return sanitize_message(raw)
    except MessageRejected as exc:
        if exc.code == "empty":
            return None
        raise ChatError("message_rejected", 422, errors={"caption": exc.code}) from exc


def _clean_client_id(raw: str | None) -> str | None:
    value = CONTROL_CHARS.sub("", raw or "").strip()[:64]
    return value or None


def _clean_line(raw: str | None, limit: int) -> str | None:
    value = " ".join(CONTROL_CHARS.sub(" ", raw or "").split())[:limit]
    return value or None


def _clean_language(raw: str | None) -> str | None:
    value = (raw or "").strip()
    return value if len(value) <= 16 and _LANGUAGE_RE.fullmatch(value) else None


def _title(message: str) -> str:
    first = message.split("\n", 1)[0].strip()[:TITLE_FIRST_LINE_CHARS]
    return TITLE_PREFIX + first


def _article_body(message: str, extra_lines: Sequence[tuple[str, str]]) -> str:
    if not extra_lines:
        return message
    lines = "\n".join(f"{label}: {value}" for label, value in extra_lines)
    return f"{message}\n\n{PRECHAT_BLOCK_HEADER}\n{lines}"


def _pick_topic(values: ChatConfigValues, key: str | None) -> ChatTopic:
    if not values.topics:
        raise ChatError("invalid_topic", 422)
    if key is None or key == "":
        return values.topics[0]
    topic = next((t for t in values.topics if t.key == key), None)
    if topic is None:
        raise ChatError("invalid_topic", 422)
    return topic


def _check_origin(origin: str, values: ChatConfigValues) -> None:
    if not origin_allowed(origin, values.allowed_origins):
        raise ChatError("origin", 403)


def _check_visitor_id(visitor_id: str) -> None:
    if VISITOR_ID_RE.fullmatch(visitor_id or "") is None:
        raise ChatError("invalid_prechat", 422, errors={"visitor_id": "type"})


def _validate(
    fields: Sequence[PrechatField], values: dict[str, str | bool], *, require_email: bool
) -> PrechatResult:
    try:
        return validate_prechat(fields, values, require_email=require_email)
    except PrechatInvalid as exc:
        raise ChatError("invalid_prechat", 422, errors=exc.errors) from exc


def _df_display(field: PrechatField | None, values: Sequence[str]) -> str:
    raw = ", ".join(values)
    if field is None:
        return raw
    if field.kind == "select":
        labels = {o.value: o.label for o in field.options}
        return ", ".join(labels.get(v, v) for v in values)
    if field.kind == "checkbox":
        return ", ".join(CHECKBOX_YES if v == "1" else CHECKBOX_NO for v in values)
    return raw


# -- DB helpers ----------------------------------------------------------------------------


async def _checked_df_values(
    db: AsyncSession, fields: Sequence[PrechatField], prechat: PrechatResult
) -> tuple[dict[str, list[str]], list[tuple[str, str]]]:
    """Dynamic-field values that Znuny will accept; the rest go to the article block.

    A field deleted/invalidated since the admin saved the config, a value of the wrong
    shape, or a Dropdown option removed meanwhile must not fail the visitor — and
    ``update_dynamic_field`` would silently drop an unknown field, so those answers are
    kept as ``(label, value)`` lines instead.
    """
    by_df = {f.dynamic_field: f for f in fields if f.maps_to == "dynamic_field"}
    accepted: dict[str, list[str]] = {}
    extra = list(prechat.extra_lines)
    for name, values in prechat.df_values.items():
        field = by_df.get(name)
        line = (field.label if field else name, _df_display(field, values))
        config = await load_ticket_dynamic_field(db, name)
        if config is None:
            extra.append(line)
            continue
        try:
            normalised = normalize_df_values(config.field_type, values)
        except DynamicFieldValueError:
            extra.append(line)
            continue
        if await values_outside_possible_values(db, config, normalised):
            extra.append(line)
            continue
        accepted[config.name] = normalised
    return accepted, extra


async def _lookup_id(db: AsyncSession, table: str, name: str) -> int | None:
    row = (
        await db.execute(
            text(f"SELECT id FROM {table} WHERE name = :v LIMIT 1"),  # noqa: S608 - fixed tables
            {"v": name},
        )
    ).first()
    return int(row[0]) if row is not None else None


async def _ticket_defaults(
    db: AsyncSession, sysconfig: SysConfig, topic: ChatTopic
) -> tuple[int, int]:
    state_id = await _lookup_id(db, "ticket_state", await sysconfig.postmaster_default_state())
    priority_id = topic.priority_id or await _lookup_id(
        db, "ticket_priority", await sysconfig.postmaster_default_priority()
    )
    if state_id is None or priority_id is None:
        logger.warning(
            "webchat_postmaster_default_unresolved",
            state_found=state_id is not None,
            priority_found=priority_id is not None,
        )
    return state_id or 1, priority_id or 3


async def current_queue_id(
    db: AsyncSession, chat: TiqoraChatSession, *, lock_ticket_row: bool = False
) -> int:
    """The ticket's queue now (R-QUEUE-SOT); refreshes the session's copy when it moved.

    Called with the session row locked (the UPDATE stays inside that lock).
    ``lock_ticket_row`` reads the ticket ``FOR UPDATE`` by primary key (R-LOCK-ORDER step 3):
    a locking read returns the newest committed queue even when MariaDB's REPEATABLE READ
    snapshot of this transaction is older (``webchat.agent.accept_session``).
    """
    sql = "SELECT queue_id FROM ticket WHERE id = :tid"
    if lock_ticket_row:
        sql += " FOR UPDATE"
    row = (await db.execute(text(sql), {"tid": int(chat.ticket_id)})).first()
    if row is None:
        return int(chat.queue_id)
    queue_id = int(row[0])
    if queue_id != int(chat.queue_id):
        await db.execute(
            update(TiqoraChatSession)
            .where(TiqoraChatSession.id == chat.id)
            .values(queue_id=queue_id)
            .execution_options(synchronize_session=False)
        )
        set_committed_value(chat, "queue_id", queue_id)
    return queue_id


async def touch_visitor_last_seen(db: AsyncSession, chat: TiqoraChatSession, now: datetime) -> bool:
    """Record visitor activity (R-LASTSEEN): at most one UPDATE per session per 60 s.

    For every visitor request (Task 7/8 call it on reads, the write paths here call it under
    the row lock). The WHERE clause keeps concurrent requests from writing twice. Returns
    whether a row was written.
    """
    cutoff = now - timedelta(seconds=LAST_SEEN_THROTTLE_SECONDS)
    seen = chat.visitor_last_seen
    if seen is not None and seen > cutoff:
        return False
    result = await db.execute(
        update(TiqoraChatSession)
        .where(TiqoraChatSession.id == chat.id, TiqoraChatSession.visitor_last_seen <= cutoff)
        .values(visitor_last_seen=now)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 0):
        set_committed_value(chat, "visitor_last_seen", now)
        return True
    return False


async def _lock(db: AsyncSession, session_id: str) -> TiqoraChatSession:
    locked = await get_chat_session(db, session_id, for_update=True)
    if locked is None or locked.purged_at is not None:
        raise ChatError("not_found", 404)
    return locked


def _agent_event(chat: TiqoraChatSession, event_type: str, queue_id: int) -> dict[str, Any]:
    return {
        "type": event_type,
        "session_id": chat.id,
        "ticket_id": int(chat.ticket_id),
        "queue_id": int(queue_id),
    }


async def _store_visitor_line(
    db: AsyncSession,
    sysconfig: SysConfig,
    chat: TiqoraChatSession,
    *,
    article: ArticleIn,
    body: str,
    attachments: Sequence[dict[str, Any]],
    client_id: str | None,
    now: datetime,
) -> tuple[TiqoraChatMessage, bool]:
    """Article + transcript line in ONE SAVEPOINT; ``(message, created)``.

    A duplicate ``client_id`` (a retried POST that raced past ``find_by_client_id``) rolls
    back the article and the ``seq`` increment together — no gap, no second article — and
    returns the row the other request stored.
    """
    seq_before = int(chat.last_seq)
    try:
        async with db.begin_nested():
            article_id = await add_article(
                db,
                ticket_id=int(chat.ticket_id),
                article=article,
                user_id=SYSTEM_USER_ID,
                sysconfig=sysconfig,
            )
            message = await insert_message(
                db,
                chat=chat,
                sender="visitor",
                body=body,
                article_id=article_id,
                attachments=attachments,
                client_id=client_id,
                now=now,
            )
            return message, True
    except IntegrityError:
        set_committed_value(chat, "last_seq", seq_before)
        if client_id is None:
            raise
        existing = await find_by_client_id(db, chat.id, client_id, locking=True)
        if existing is None:
            raise
        return existing, False


# -- service functions ---------------------------------------------------------------------


async def _open_ticket_and_session(
    db: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    config: TiqoraChatConfig,
    values: ChatConfigValues,
    topic: ChatTopic,
    visitor_id: str,
    prechat: PrechatResult,
    name: str | None,
    message: str,
    ctx: VisitorContext,
    page_url: str | None,
    language: str | None,
    offline: bool,
    now: datetime,
) -> tuple[TiqoraChatSession, str, int]:
    """Ticket (+ dynamic fields), first customer article, session row; ``(row, token, article)``."""
    df_values, extra_lines = await _checked_df_values(db, values.prechat_fields, prechat)
    customer_id, customer_user = await resolve_chat_customer(
        db, email=prechat.email, default_customer_user=values.default_customer_user
    )
    state_id, priority_id = await _ticket_defaults(db, sysconfig, topic)
    title = _title(message)
    ticket_id = await create_ticket(
        db,
        factory,
        sysconfig,
        params=TicketIn(
            title=title,
            queue_id=topic.queue_id,
            state_id=state_id,
            priority_id=priority_id,
            owner_id=SYSTEM_USER_ID,
            customer_id=customer_id,
            customer_user_id=customer_user,
            dynamic_fields=df_values,
        ),
        user_id=SYSTEM_USER_ID,
    )
    article_id = await add_article(
        db,
        ticket_id=ticket_id,
        article=ArticleIn(
            sender_type="customer",
            is_visible_for_customer=True,
            subject=title,
            body=_article_body(message, extra_lines),
            channel=CHANNEL,
            from_address=visitor_from_address(name, prechat.email, visitor_id),
        ),
        user_id=SYSTEM_USER_ID,
        sysconfig=sysconfig,
    )

    session_id = new_session_id()
    token, token_hash = issue_visitor_token(session_id)
    chat = TiqoraChatSession(
        id=session_id,
        config_id=int(config.id),
        topic_key=topic.key,
        queue_id=topic.queue_id,
        ticket_id=ticket_id,
        status="ended" if offline else "waiting",
        end_reason="offline_form" if offline else None,
        token_hash=token_hash,
        visitor_id=visitor_id,
        visitor_name=name,
        visitor_email=prechat.email,
        customer_user_login=customer_user,
        last_seq=0,
        waiting_since=None if offline else now,
        ended_at=now if offline else None,
        visitor_last_seen=now,
        page_url=clean_page_url(ctx.page_url if ctx.page_url is not None else page_url),
        user_agent=_clean_line(ctx.user_agent, MAX_USER_AGENT),
        ip_hash=ctx.ip_hash[:32] or None,
        language=_clean_language(ctx.language if ctx.language is not None else language),
        created=now,
    )
    db.add(chat)
    await db.flush([chat])
    return chat, token, article_id


async def create_session(
    db: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    redis_client: Any,
    *,
    config: TiqoraChatConfig,
    values: ChatConfigValues,
    payload: ChatSessionCreateIn,
    ctx: VisitorContext,
    now: datetime,
) -> tuple[TiqoraChatSession, str, TiqoraChatMessage]:
    """Start a chat: ticket + first visitor article + session row + message ``seq`` 1.

    Returns ``(session, token, first_message)``; the token is shown to the visitor once,
    only its SHA-256 is stored. Refusals (all before any write): ``origin`` (page origin not
    allowed), ``invalid_topic``, ``message_rejected``, ``invalid_prechat``, ``offline``
    (outside business hours or no eligible agent — checked live, not from the 10 s cache).
    *factory* is needed for the ticket-number counter. Queues ``chat_waiting`` + a wake.
    """
    _check_origin(payload.origin, values)
    _check_visitor_id(payload.visitor_id)
    topic = _pick_topic(values, payload.topic)
    body = _clean_message(payload.message)
    prechat = _validate(values.prechat_fields, dict(payload.prechat), require_email=False)
    client_id = _clean_client_id(payload.client_id)
    if not await within_business_hours(sysconfig, values.business_calendar, now):
        raise ChatError("offline", 409)
    if not await eligible_agent_ids(db, redis_client, config_id=int(config.id), topic=topic):
        raise ChatError("offline", 409)

    chat, token, article_id = await _open_ticket_and_session(
        db,
        factory,
        sysconfig,
        config=config,
        values=values,
        topic=topic,
        visitor_id=payload.visitor_id,
        prechat=prechat,
        name=prechat.name,
        message=body,
        ctx=ctx,
        page_url=payload.page_url,
        language=payload.language,
        offline=False,
        now=now,
    )
    first = await insert_message(
        db,
        chat=chat,
        sender="visitor",
        body=body,
        article_id=article_id,
        client_id=client_id,
        now=now,
    )
    queue_wakeup(db, chat.id)
    event = _agent_event(chat, "chat_waiting", topic.queue_id)
    event["topic_ref"] = topic_ref(int(config.id), topic.key)
    queue_agent_event(db, event)
    return chat, token, first


async def post_visitor_message(
    db: AsyncSession,
    sysconfig: SysConfig,
    *,
    chat: TiqoraChatSession,
    body: str,
    client_id: str | None,
    now: datetime,
) -> TiqoraChatMessage:
    """A follow-up line of the visitor: ``FollowUp`` article (NoAgentNotify) + message.

    *chat* is only a handle (its id); the row is re-read under its lock. A retried POST with
    the same ``client_id`` returns the stored message (also after the chat ended).
    Refusals: ``message_rejected``, ``not_found``, ``session_closed``.
    """
    text_body = _clean_message(body, key="body")
    cid = _clean_client_id(client_id)
    locked = await _lock(db, chat.id)
    if cid is not None:
        existing = await find_by_client_id(db, locked.id, cid)
        if existing is not None:
            return existing
    if locked.status not in LIVE_STATUSES:
        raise ChatError("session_closed", 409)
    await touch_visitor_last_seen(db, locked, now)
    queue_id = await current_queue_id(db, locked)
    message, created = await _store_visitor_line(
        db,
        sysconfig,
        locked,
        article=_follow_up_article(locked, text_body, attachments=()),
        body=text_body,
        attachments=(),
        client_id=cid,
        now=now,
    )
    if created:
        queue_wakeup(db, locked.id)
        queue_agent_event(db, _agent_event(locked, "chat_message", queue_id))
    return message


def _follow_up_article(
    chat: TiqoraChatSession, body: str, *, attachments: Sequence[tuple[str, str, bytes]]
) -> ArticleIn:
    return ArticleIn(
        sender_type="customer",
        is_visible_for_customer=True,
        subject=_title(body),
        body=body,
        channel=CHANNEL,
        from_address=visitor_from_address(chat.visitor_name, chat.visitor_email, chat.visitor_id),
        attachments=list(attachments),
        no_agent_notify=True,
    )


async def post_visitor_attachment(
    db: AsyncSession,
    sysconfig: SysConfig,
    *,
    chat: TiqoraChatSession,
    values: ChatConfigValues,
    filename: str,
    content: bytes,
    caption: str | None,
    client_id: str | None,
    now: datetime,
) -> TiqoraChatMessage:
    """``store_visitor_attachment`` without the ``created`` flag."""
    message, _created = await store_visitor_attachment(
        db,
        sysconfig,
        chat=chat,
        values=values,
        filename=filename,
        content=content,
        caption=caption,
        client_id=client_id,
        now=now,
    )
    return message


async def store_visitor_attachment(
    db: AsyncSession,
    sysconfig: SysConfig,
    *,
    chat: TiqoraChatSession,
    values: ChatConfigValues,
    filename: str,
    content: bytes,
    caption: str | None,
    client_id: str | None,
    now: datetime,
) -> tuple[TiqoraChatMessage, bool]:
    """One upload = one visitor line; returns ``(message, created)`` — ``created`` is False
    for an idempotent ``client_id`` retry that returned the stored line.

    One upload = one visitor line: the file as article attachment (stored like every
    Tiqora attachment, ``article_data_mime_attachment``) + a message listing it.

    The type is sniffed from the bytes, the name made safe with the canonical extension
    (``webchat.uploads.check_upload``). The body is the caption, else the file name.
    Refusals: ``upload_rejected`` (``errors.file``: disabled/empty/type → 422, size → 413),
    ``message_rejected`` (caption), ``not_found``, ``session_closed``.
    """
    try:
        safe_name, content_type = check_upload(content, filename, values.upload_policy)
    except UploadRejected as exc:
        status = 413 if exc.code == "size" else 422
        raise ChatError("upload_rejected", status, errors={"file": exc.code}) from exc
    caption_text = _clean_caption(caption)
    cid = _clean_client_id(client_id)
    locked = await _lock(db, chat.id)
    if cid is not None:
        existing = await find_by_client_id(db, locked.id, cid)
        if existing is not None:
            return existing, False
    if locked.status not in LIVE_STATUSES:
        raise ChatError("session_closed", 409)
    await touch_visitor_last_seen(db, locked, now)
    queue_id = await current_queue_id(db, locked)
    body = caption_text or safe_name
    listing = [
        {"index": 0, "filename": safe_name, "content_type": content_type, "size": len(content)}
    ]
    message, created = await _store_visitor_line(
        db,
        sysconfig,
        locked,
        article=_follow_up_article(locked, body, attachments=[(safe_name, content_type, content)]),
        body=body,
        attachments=listing,
        client_id=cid,
        now=now,
    )
    if created:
        queue_wakeup(db, locked.id)
        queue_agent_event(db, _agent_event(locked, "chat_message", queue_id))
    return message, created


async def submit_offline(
    db: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    config: TiqoraChatConfig,
    values: ChatConfigValues,
    payload: ChatOfflineIn,
    ctx: VisitorContext,
    now: datetime,
) -> None:
    """Offline form: a ticket with the visitor's message and an already ``ended`` session row
    (``end_reason`` ``offline_form``; the token is never handed out). The address is
    required (``invalid_prechat`` / ``{"email": "required"}``). No agent-bus event: the
    ticket's ``NotificationNewTicket`` is the agents' signal. Refusals before any write:
    ``disabled`` (offline form switched off), ``origin``, ``invalid_topic``,
    ``message_rejected``, ``invalid_prechat``.
    """
    if values.offline_mode != "form":
        raise ChatError("disabled", 404)
    _check_origin(payload.origin, values)
    _check_visitor_id(payload.visitor_id)
    topic = _pick_topic(values, payload.topic)
    body = _clean_message(payload.message)
    answers: dict[str, str | bool] = dict(payload.prechat)
    email_field = next((f for f in values.prechat_fields if f.maps_to == "email"), None)
    name_field = next((f for f in values.prechat_fields if f.maps_to == "name"), None)
    email_key = email_field.key if email_field else EMAIL_KEY
    if payload.email or email_key not in answers:
        answers[email_key] = payload.email
    if name_field is not None and payload.name and not answers.get(name_field.key):
        answers[name_field.key] = payload.name
    prechat = _validate(values.prechat_fields, answers, require_email=True)
    name = prechat.name or _clean_line(payload.name, MAX_VISITOR_NAME)

    await _open_ticket_and_session(
        db,
        factory,
        sysconfig,
        config=config,
        values=values,
        topic=topic,
        visitor_id=payload.visitor_id,
        prechat=prechat,
        name=name,
        message=body,
        ctx=ctx,
        page_url=payload.page_url,
        language=payload.language,
        offline=True,
        now=now,
    )


async def add_system_message(
    db: AsyncSession,
    *,
    chat: TiqoraChatSession,
    code: str,
    params: dict[str, Any],
    now: datetime | None = None,
) -> TiqoraChatMessage:
    """A system line (``sender="system"``, ``body`` = the code, params in ``meta``); not an
    article. Takes the session row lock through ``allocate_seq`` if not held yet; queues a
    wake for the visitor streams."""
    message = await insert_message(
        db, chat=chat, sender="system", body=code, meta=params, now=now or utc_now()
    )
    queue_wakeup(db, chat.id)
    return message


async def end_by_visitor(db: AsyncSession, *, chat: TiqoraChatSession, now: datetime) -> None:
    """The visitor closes the chat: ``ended`` / ``visitor`` / ``ended_at``, system line
    ``visitor_left``, wake + ``chat_ended``. Idempotent: an already closed chat is left
    alone. Refusal: ``not_found``."""
    locked = await _lock(db, chat.id)
    if locked.status not in LIVE_STATUSES:
        return
    queue_id = await current_queue_id(db, locked)
    locked.status = "ended"
    locked.end_reason = "visitor"
    locked.ended_at = now
    locked.visitor_last_seen = now
    await db.flush([locked])
    await add_system_message(db, chat=locked, code="visitor_left", params={}, now=now)
    queue_agent_event(db, _agent_event(locked, "chat_ended", queue_id))


async def set_contact(
    db: AsyncSession,
    sysconfig: SysConfig,
    *,
    chat: TiqoraChatSession,
    name: str | None,
    email: str,
    now: datetime,
) -> None:
    """Contact address after a chat ended without an answer (anonymous visitor).

    Allowed for ``CONTACT_WINDOW_SECONDS`` after ``ended_at`` when the chat is
    ``ended``/``abandoned`` with end reason ``no_agent``/``idle``/``visitor`` and no address
    was given yet. Sets ``visitor_email``/``visitor_name`` and the ticket customer
    (``set_customer``: ``CustomerUpdate`` history, same matching as a new chat); wakes the
    visitor streams. Refusals: ``invalid_prechat`` (``errors.email``), ``not_found``,
    ``contact_not_allowed``.
    """
    # Same address rule and error codes as the pre-chat / offline form.
    address = _validate([], {EMAIL_KEY: email or ""}, require_email=True).email
    clean_name = _clean_line(name, MAX_VISITOR_NAME)

    locked = await _lock(db, chat.id)
    ended_at = locked.ended_at
    if (
        locked.status not in _CONTACT_STATUSES
        or locked.end_reason not in CONTACT_END_REASONS
        or ended_at is None
        or (now - ended_at).total_seconds() >= CONTACT_WINDOW_SECONDS
        or locked.visitor_email
    ):
        raise ChatError("contact_not_allowed", 409)
    customer_id, customer_user = await resolve_chat_customer(
        db, email=address, default_customer_user=None
    )
    await set_customer(
        db,
        ticket_id=int(locked.ticket_id),
        customer_id=customer_id,
        customer_user_id=customer_user,
        user_id=SYSTEM_USER_ID,
    )
    locked.visitor_email = address
    if clean_name:
        locked.visitor_name = clean_name
    locked.customer_user_login = customer_user
    await db.flush([locked])
    await touch_visitor_last_seen(db, locked, now)
    queue_wakeup(db, locked.id)
