"""Telegram inbound update processing, wired to ticket_write_service.

``process_update`` handles a single Telegram Bot API update (one ``message``)
and is shared by both transports Task 3 adds (long-poll daemon, webhook
route) — neither of those exist yet, this module only turns an update dict
into a ticket/article.

Ticket resolution deliberately does *not* reuse
:func:`tiqora.channels.common.resolve_ticket_for_inbound` — Telegram chat_ids
are not looked up against ``customer_user`` the way phone numbers are, so an
unmapped contact (the common case: a Telegram user who never linked a portal
account) always resolves to the same ``default_customer_user``. Blindly
reusing the generic "most recent open ticket for this customer_user" fallback
would then merge different Telegram users' messages into one ticket. Instead
we key off the chat via an ``a_from`` marker embedded in every outbound
article's From address (``<chat_id>@telegram.invalid>``), and only fall back
to the customer_user-based lookup once a contact has a *real* mapped login.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.common import channel_setting, ensure_channel_row
from tiqora.channels.telegram.gateway import TelegramApiError, TelegramGateway
from tiqora.channels.telegram.messages import (
    CALLBACK_PREFIX,
    ButtonAction,
    buttons_from_json,
    get_by_message,
    record_message,
)
from tiqora.db.tiqora.models import TiqoraTelegramContact
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    TicketIn,
    add_article,
    change_state,
    create_ticket,
)
from tiqora.znuny.followup import detect_followup
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

CHANNEL_NAME = "telegram"
COMM_CHANNEL_NAME = "Telegram"
COMM_CHANNEL_MODULE = "Tiqora::CommunicationChannel::Telegram"

# Consent-accept inline button's callback_data — matched verbatim against
# ``callback_query.data``.
CONSENT_ACCEPT_CALLBACK_DATA = "tiqora_consent_accept"

# Minimum gap between two consent prompts to the same chat (spam guard for
# customers who keep messaging before tapping the button).
CONSENT_REPROMPT_SECONDS = 3600

# Answer-button taps (Task 5): callback answer texts (informal German, as
# every other Telegram-facing string) and the ticket_state name each
# ``resolve_*`` action moves the ticket to. ``reply`` records the tap as a
# customer article and only reopens a closed ticket.
_BUTTON_ANSWER_TEXT = "Danke!"
_BUTTON_INVALID_TEXT = "Diese Auswahl ist nicht mehr gültig."
_BUTTON_ALREADY_ANSWERED_TEXT = "Schon beantwortet 👍"
_BUTTON_RESOLVE_STATE_NAME: dict[ButtonAction, str] = {
    "resolve_yes": "closed successful",
    "resolve_no": "open",
}

# ``/start`` = explicit new-dialog reset (Telegram-Chat-UX). Compared against
# the stripped message text verbatim — no other command is understood yet.
START_COMMAND = "/start"

DEFAULT_START_TEXT = (
    "Hallo {first_name}! 👋 Schildere mir bitte kurz dein Anliegen – ich lege "
    "dafür einen neuen Vorgang an."
)

# System-prompt addendum for AI replies triggered from this channel (Task:
# Telegram-Chat-UX) — a Telegram chat reads as a conversation, not a letter,
# so the model is told to duze and drop formal-mail conventions. Read by
# ``tiqora.ai.runtime`` via the ``channel.telegram.tone_prompt`` setting.
DEFAULT_TONE_PROMPT = (
    "Dies ist ein Telegram-Chat: Duze die Nutzerin/den Nutzer konsequent und "
    "sprich sie/ihn mit Vornamen an. Schreibe kurz, freundlich und "
    "chat-gerecht – keine förmlichen Brief-Floskeln, keine Grußformeln wie "
    "'Sehr geehrte…'."
)

_DEFAULT_CONSENT_TEXT = (
    "Bevor wir Ihr Anliegen bearbeiten können, benötigen wir Ihre Zustimmung zur "
    "Verarbeitung Ihrer Daten (Chat-ID, Name, Nachrichteninhalt) zur Bearbeitung "
    "Ihrer Anfrage. Bitte bestätigen Sie über den Button unten. Senden Sie Ihr "
    "Anliegen danach bitte erneut.\n\n✅ Zustimmen"
)
_DEFAULT_CONSENT_CONFIRMED_TEXT = (
    "Vielen Dank! Ihre Zustimmung wurde gespeichert. Bitte senden Sie jetzt Ihr Anliegen."
)
_CONSENT_KEYBOARD = {
    "inline_keyboard": [[{"text": "✅ Zustimmen", "callback_data": CONSENT_ACCEPT_CALLBACK_DATA}]]
}

# Telegram media message keys that carry a downloadable attachment, in the
# order they're checked (a message has at most one of these).
_MEDIA_PLACEHOLDERS: dict[str, str] = {
    "photo": "[Foto]",
    "voice": "[Sprachnachricht]",
    "video": "[Video]",
    "sticker": "[Sticker]",
}


async def _lookup_id(session: AsyncSession, table: str, name_col: str, value: str) -> int | None:
    row = (
        await session.execute(
            text(f"SELECT id FROM {table} WHERE {name_col} = :v LIMIT 1"), {"v": value}
        )
    ).first()
    return int(row[0]) if row is not None else None


def _pick_media(message: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Return ``(kind, file_id, filename, content_type_hint)`` for the first
    supported attachment on *message*, or ``None``."""
    photo = message.get("photo")
    if photo:
        largest = max(photo, key=lambda p: p.get("file_size") or 0)
        return "photo", str(largest["file_id"]), "photo.jpg", "image/jpeg"
    document = message.get("document")
    if document:
        name = str(document.get("file_name") or "document")
        ct = str(document.get("mime_type") or "application/octet-stream")
        return "document", str(document["file_id"]), name, ct
    voice = message.get("voice")
    if voice:
        ct = str(voice.get("mime_type") or "audio/ogg")
        return "voice", str(voice["file_id"]), "voice.ogg", ct
    video = message.get("video")
    if video:
        name = str(video.get("file_name") or "video.mp4")
        ct = str(video.get("mime_type") or "video/mp4")
        return "video", str(video["file_id"]), name, ct
    sticker = message.get("sticker")
    if sticker:
        ext = "webm" if sticker.get("is_video") else "webp"
        ct = "video/webm" if sticker.get("is_video") else "image/webp"
        return "sticker", str(sticker["file_id"]), f"sticker.{ext}", ct
    return None


def _extract_body(message: dict[str, Any]) -> tuple[str, tuple[str, str, str, str] | None]:
    """Return ``(body_text, media)``. *body_text* is the message/caption text,
    or a placeholder like ``"[Foto]"``/``"[Dokument: name]"`` when the message
    carries only media."""
    text_value = message.get("text") or message.get("caption")
    media = _pick_media(message)
    if text_value:
        return str(text_value), media
    if media is not None:
        kind, _file_id, filename, _ct = media
        if kind == "document":
            return f"[Dokument: {filename}]", media
        return _MEDIA_PLACEHOLDERS[kind], media
    return "", None


async def _upsert_contact(session: AsyncSession, message: dict[str, Any]) -> TiqoraTelegramContact:
    """Insert or refresh the ``tiqora_telegram_contact`` row for this chat.

    ``customer_user_login`` is a manual/admin-set mapping and is never
    touched here.
    """
    chat = message.get("chat") or {}
    frm = message.get("from") or {}
    return await _upsert_contact_fields(session, chat_id=int(chat["id"]), frm=frm)


async def _upsert_contact_fields(
    session: AsyncSession, *, chat_id: int, frm: dict[str, Any]
) -> TiqoraTelegramContact:
    """Insert or refresh the contact row for *chat_id* from a Telegram
    ``from`` object. Identity fields only — used by both ``message`` and
    ``callback_query`` updates (see :func:`_upsert_contact`).

    ``customer_user_login`` is a manual/admin-set mapping and is never
    touched here.
    """
    telegram_user_id = frm.get("id")
    username = frm.get("username")
    first_name = str(frm.get("first_name") or "").strip()
    last_name = str(frm.get("last_name") or "").strip()
    display_name = " ".join(part for part in (first_name, last_name) if part) or None

    row = (
        await session.execute(
            select(TiqoraTelegramContact).where(TiqoraTelegramContact.chat_id == chat_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = TiqoraTelegramContact(
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            username=username,
            display_name=display_name,
        )
        session.add(row)
    else:
        if telegram_user_id is not None:
            row.telegram_user_id = telegram_user_id
        row.username = username
        row.display_name = display_name
        row.change_time = datetime.now(UTC).replace(tzinfo=None)
    await session.flush()
    return row


def _render_start_text(template: str, first_name: str) -> str:
    """``{first_name}`` via ``str.format``; an empty/missing name must not
    leave a doubled space behind (e.g. ``"Hallo  ! ..."``)."""
    try:
        rendered = template.format(first_name=first_name)
    except (KeyError, IndexError):
        # Admin-configured text with an unsupported placeholder — fall back
        # to the template verbatim rather than failing the whole update.
        rendered = template
    return re.sub(r"[ \t]{2,}", " ", rendered).strip()


async def _handle_start_command(
    session: AsyncSession,
    gateway: TelegramGateway | None,
    contact: TiqoraTelegramContact,
    frm: dict[str, Any],
) -> dict[str, Any]:
    """Handle ``/start``: reset per-chat ticket continuity and greet, without
    creating any ticket/article (see module docstring's stage (a)-(d) order
    and its interaction with ``new_dialog_since`` below)."""
    contact.new_dialog_since = datetime.now(UTC).replace(tzinfo=None)
    await session.flush()

    if gateway is not None:
        first_name = str(frm.get("first_name") or "").strip()
        template = (
            await channel_setting(session, CHANNEL_NAME, "start_text", DEFAULT_START_TEXT)
        ) or DEFAULT_START_TEXT
        greeting = _render_start_text(template, first_name)
        try:
            await gateway.send_message(contact.chat_id, greeting)
        except TelegramApiError as exc:
            logger.warning(
                "telegram_start_greeting_send_failed", chat_id=contact.chat_id, error=str(exc)
            )

    return {"command": "start"}


async def _resolve_ticket(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    chat_id: int,
    body_text: str,
    customer_no: str | None,
    customer_user_id: str | None,
    is_mapped_customer: bool,
    title: str,
    user_id: int,
    new_dialog_since: datetime | None = None,
) -> tuple[int, bool]:
    """Return ``(ticket_id, created)``.

    Order: (a) follow-up tag in the text, (b) most recent non-closed ticket
    that already has an article from this chat (per-chat continuity — see
    module docstring), (c) only for a *really* mapped contact, most recent
    open ticket for that customer_user (generic fallback), (d) create new.

    ``new_dialog_since`` (set by ``/start``, Task: Telegram-Chat-UX) makes
    stage (b) ignore any per-chat ticket whose newest Telegram article
    predates it, and skips stage (c) entirely — a customer who taps
    ``/start`` always gets a fresh ticket, even with an older ticket of
    theirs still open. Once that new ticket has its own article, it is
    younger than ``new_dialog_since`` and stage (b) picks it up again on the
    next message, same as before. Stage (a) (follow-up tag) always wins
    regardless — an explicit reply-to-this-ticket signal outranks a stale
    ``/start``.
    """
    from tiqora.domain.subject_hook import load_subject_config

    subject_cfg = await load_subject_config(session, sysconfig)
    followup = await detect_followup(
        session,
        sysconfig,
        subject=body_text,
        references=[],
        hook=subject_cfg.hook,
        hook_divider=subject_cfg.divider,
    )
    if followup is not None:
        _tn, ticket_id = followup
        return ticket_id, False

    chat_pattern = f"%<{chat_id}@telegram.invalid>%"
    stage_b_sql = (
        "SELECT t.id FROM ticket t"
        " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
        " JOIN ticket_state_type tst ON tst.id = ts.type_id"
        " JOIN article a ON a.ticket_id = t.id"
        " JOIN article_data_mime adm ON adm.article_id = a.id"
        " WHERE adm.a_from LIKE :pat AND tst.name NOT IN ('closed', 'removed')"
    )
    stage_b_params: dict[str, Any] = {"pat": chat_pattern}
    if new_dialog_since is not None:
        # DATETIME columns are second-resolution on MariaDB: a message sent
        # in the very same wall-clock second as /start is an unavoidable
        # edge case either way (a customer never types that fast in
        # practice) -- keep the strict '>' so an *older* per-chat article
        # from before the reset is never mistaken for a new one.
        stage_b_sql += " AND a.create_time > :nds"
        stage_b_params["nds"] = new_dialog_since
    stage_b_sql += " ORDER BY t.id DESC LIMIT 1"
    row = (await session.execute(text(stage_b_sql), stage_b_params)).first()
    if row is not None:
        return int(row[0]), False

    if new_dialog_since is None and is_mapped_customer and customer_user_id:
        row = (
            await session.execute(
                text(
                    "SELECT t.id FROM ticket t"
                    " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " WHERE t.customer_user_id = :cu AND tst.name NOT IN ('closed', 'removed')"
                    " ORDER BY t.id DESC LIMIT 1"
                ),
                {"cu": customer_user_id},
            )
        ).first()
        if row is not None:
            return int(row[0]), False

    resolved_queue = await channel_setting(session, CHANNEL_NAME, "queue_name")
    resolved_queue = resolved_queue or await sysconfig.postmaster_default_queue()
    state_name = await sysconfig.postmaster_default_state()
    priority_name = await sysconfig.postmaster_default_priority()

    queue_id = await _lookup_id(session, "queue", "name", resolved_queue) or 1
    state_id = await _lookup_id(session, "ticket_state", "name", state_name) or 1
    priority_id = await _lookup_id(session, "ticket_priority", "name", priority_name) or 3

    params = TicketIn(
        title=title[:255],
        queue_id=queue_id,
        state_id=state_id,
        priority_id=priority_id,
        owner_id=user_id,
        customer_id=customer_no,
        customer_user_id=customer_user_id,
    )
    ticket_id = await create_ticket(
        session, session_factory, sysconfig, params=params, user_id=user_id
    )
    return ticket_id, True


async def _consent_required(session: AsyncSession) -> bool:
    value = await channel_setting(session, CHANNEL_NAME, "consent_required")
    return value != "0"


async def _handle_consent_callback(
    session: AsyncSession, gateway: TelegramGateway | None, callback_query: dict[str, Any]
) -> dict[str, Any]:
    """Handle a ``callback_query`` update. Only the consent-accept button is
    understood — any other callback_query is skipped, same as before this
    update type was handled at all."""
    if callback_query.get("data") != CONSENT_ACCEPT_CALLBACK_DATA:
        return {"skipped": "unsupported_callback"}

    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    if "id" not in chat:
        return {"skipped": "unsupported_callback"}
    chat_id = int(chat["id"])
    frm = callback_query.get("from") or {}

    contact = await _upsert_contact_fields(session, chat_id=chat_id, frm=frm)
    contact.consent_time = datetime.now(UTC).replace(tzinfo=None)
    await session.flush()

    callback_query_id = str(callback_query.get("id") or "")
    confirmed_text = (
        await channel_setting(
            session, CHANNEL_NAME, "consent_confirmed_text", _DEFAULT_CONSENT_CONFIRMED_TEXT
        )
        or _DEFAULT_CONSENT_CONFIRMED_TEXT
    )
    if gateway is not None:
        await gateway.answer_callback_query(callback_query_id, confirmed_text)
        try:
            await gateway.send_message(chat_id, confirmed_text)
        except TelegramApiError as exc:
            logger.warning(
                "telegram_consent_confirmed_send_failed", chat_id=chat_id, error=str(exc)
            )

    return {"consent": "accepted"}


async def _ticket_title(session: AsyncSession, ticket_id: int) -> str:
    row = (
        await session.execute(text("SELECT title FROM ticket WHERE id = :tid"), {"tid": ticket_id})
    ).first()
    return str(row[0]) if row is not None and row[0] else ""


async def _handle_button_callback(
    session: AsyncSession,
    gateway: TelegramGateway | None,
    sysconfig: SysConfig,
    callback_query: dict[str, Any],
    user_id: int,
) -> dict[str, Any]:
    """Handle a customer tapping an answer-button keyboard (Task 5).

    The row is looked up by ``(chat_id, callback_query.message.message_id)``
    -- that's the map row of the *outbound* message which carried the
    keyboard (see ``messages.record_message``), not the tap itself. Order
    matters: the "already answered" check happens before anything is
    created, so two sequential taps on the same button (Telegram delivers
    updates sequentially per bot) never create a second article or change
    state twice -- see constraints.md's double-tap review focus.
    """
    callback_query_id = str(callback_query.get("id") or "")
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    if "id" not in chat or "message_id" not in message:
        # Structurally malformed callback_query -- nothing to answer either.
        return {"skipped": "unsupported_callback"}
    chat_id = int(chat["id"])
    message_id = int(message["message_id"])

    row = await get_by_message(session, chat_id, message_id)
    buttons = buttons_from_json(row.buttons_json) if row is not None else []
    try:
        index = int(str(callback_query.get("data") or "")[len(CALLBACK_PREFIX) :])
    except ValueError:
        index = -1

    if row is None or not (0 <= index < len(buttons)):
        if gateway is not None:
            await gateway.answer_callback_query(callback_query_id, _BUTTON_INVALID_TEXT)
        return {"skipped": "unknown_button"}

    if row.answered_at is not None:
        if gateway is not None:
            await gateway.answer_callback_query(callback_query_id, _BUTTON_ALREADY_ANSWERED_TEXT)
        return {"skipped": "already_answered"}

    button = buttons[index]
    frm = callback_query.get("from") or {}
    contact = await _upsert_contact_fields(session, chat_id=chat_id, frm=frm)
    display_name = contact.display_name or (
        f"@{contact.username}" if contact.username else str(contact.chat_id)
    )
    from_address = f"{display_name} <{contact.chat_id}@telegram.invalid>"
    title = await _ticket_title(session, row.ticket_id)

    article = ArticleIn(
        sender_type="customer",
        is_visible_for_customer=True,
        subject=title,
        body=button.label,
        content_type="text/plain; charset=utf-8",
        from_address=from_address,
        channel=CHANNEL_NAME,
        # A canned answer, not a question: without this the AI auto worker
        # would answer the tap (e.g. reply to "Ja" on a just-closed ticket).
        auto_generated=True,
    )
    article_id = await add_article(
        session, ticket_id=row.ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    # A tap has no Telegram message of its own -- message_id=None. The
    # unique index is on (chat_id, message_id); MariaDB treats NULL as
    # distinct from every other NULL there, so several tapped-answer rows
    # for the same chat never collide.
    await record_message(
        session,
        article_id=article_id,
        ticket_id=row.ticket_id,
        chat_id=chat_id,
        message_id=None,
        direction="in",
        reply_to_article_id=row.article_id,
    )

    row.answered_button = index
    row.answered_at = datetime.now(UTC).replace(tzinfo=None)
    await session.flush()

    current = (
        await session.execute(
            text(
                "SELECT t.ticket_state_id, tst.name FROM ticket t"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE t.id = :tid"
            ),
            {"tid": row.ticket_id},
        )
    ).first()
    current_state_type = str(current[1]) if current is not None else None
    state_name = _BUTTON_RESOLVE_STATE_NAME.get(button.action)
    if state_name is None and current_state_type == "closed":
        # A plain answer on a closed ticket is a customer follow-up: reopen,
        # like a typed message would.
        state_name = "open"
    # Never touch a merged/removed ticket: changing its state would bring
    # it back to life next to the ticket it was merged into.
    if state_name is not None and current_state_type not in (None, "merged", "removed"):
        target_state_id = await _lookup_id(session, "ticket_state", "name", state_name)
        if (
            target_state_id is not None
            and current is not None
            and int(current[0]) != target_state_id
        ):
            await change_state(
                session,
                ticket_id=row.ticket_id,
                new_state_id=target_state_id,
                user_id=user_id,
                sysconfig=sysconfig,
            )

    if gateway is not None:
        await gateway.answer_callback_query(callback_query_id, _BUTTON_ANSWER_TEXT)
        try:
            await gateway.edit_message_reply_markup(chat_id, message_id, None)
        except TelegramApiError as exc:
            logger.warning(
                "telegram_button_keyboard_clear_failed",
                chat_id=chat_id,
                message_id=message_id,
                error=str(exc),
            )

    return {"ticket_id": row.ticket_id, "article_id": article_id, "button_action": button.action}


async def _maybe_prompt_consent(
    session: AsyncSession, gateway: TelegramGateway | None, contact: TiqoraTelegramContact
) -> None:
    """Send the consent prompt to *contact*, unless one was already sent
    within :data:`CONSENT_REPROMPT_SECONDS` (spam guard)."""
    now = datetime.now(UTC).replace(tzinfo=None)
    if contact.consent_prompt_time is not None:
        elapsed = (now - contact.consent_prompt_time).total_seconds()
        if elapsed < CONSENT_REPROMPT_SECONDS:
            return

    contact.consent_prompt_time = now
    await session.flush()

    if gateway is None:
        return
    consent_text = (
        await channel_setting(session, CHANNEL_NAME, "consent_text", _DEFAULT_CONSENT_TEXT)
        or _DEFAULT_CONSENT_TEXT
    )
    try:
        await gateway.send_message(contact.chat_id, consent_text, reply_markup=_CONSENT_KEYBOARD)
    except TelegramApiError as exc:
        logger.warning(
            "telegram_consent_prompt_send_failed", chat_id=contact.chat_id, error=str(exc)
        )


async def process_update(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    gateway: TelegramGateway | None,
    update: dict[str, Any],
    user_id: int = 1,
) -> dict[str, Any]:
    """Process one Telegram update. Caller commits the session.

    Used by both the long-poll daemon and the webhook route (Task 3); this
    function itself is transport-agnostic. DSGVO consent (Task 13) is
    enforced right after the bot-loop check, before any ticket/article/
    identity logic runs — see ``callback_query``/consent handling below.
    """
    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        if str(callback_query.get("data") or "").startswith(CALLBACK_PREFIX):
            return await _handle_button_callback(
                session, gateway, sysconfig, callback_query, user_id
            )
        return await _handle_consent_callback(session, gateway, callback_query)

    message = update.get("message")
    if not isinstance(message, dict):
        # edited_message, channel_post, ... — not handled yet.
        return {"skipped": "unsupported"}

    frm = message.get("from") or {}
    if frm.get("is_bot"):
        # Loop protection: never react to another bot's message (including
        # our own echoes, if this bot is ever added to a group with itself).
        return {"skipped": "bot"}

    await ensure_channel_row(session, COMM_CHANNEL_NAME, COMM_CHANNEL_MODULE)

    # Contact-Upsert here only ever touches identity fields (chat_id,
    # telegram_user_id, username, display_name) plus, below,
    # consent_prompt_time — never the message text or a ticket/article, so
    # this same call is safe to make before consent is granted.
    contact = await _upsert_contact(session, message)

    if await _consent_required(session) and contact.consent_time is None:
        await _maybe_prompt_consent(session, gateway, contact)
        return {"skipped": "no_consent"}

    if (message.get("text") or "").strip() == START_COMMAND:
        return await _handle_start_command(session, gateway, contact, frm)

    default_customer = await channel_setting(session, CHANNEL_NAME, "default_customer_user")
    is_mapped_customer = bool(contact.customer_user_login)
    customer_user_id = contact.customer_user_login or default_customer
    customer_no = customer_user_id
    if not customer_user_id:
        logger.info("telegram_inbound_no_customer", chat_id=contact.chat_id)
        return {"skipped": "no_customer"}

    body_text, media = _extract_body(message)

    display_name = contact.display_name or (
        f"@{contact.username}" if contact.username else str(contact.chat_id)
    )
    from_address = f"{display_name} <{contact.chat_id}@telegram.invalid>"
    title = body_text[:60] if body_text else "Telegram-Nachricht"

    ticket_id, created = await _resolve_ticket(
        session,
        session_factory,
        sysconfig,
        chat_id=contact.chat_id,
        body_text=body_text,
        customer_no=customer_no,
        customer_user_id=customer_user_id,
        is_mapped_customer=is_mapped_customer,
        title=title,
        user_id=user_id,
        new_dialog_since=contact.new_dialog_since,
    )

    attachments: list[tuple[str, str, bytes]] = []
    if media is not None:
        kind, file_id, filename, content_type_hint = media
        if gateway is None:
            body_text = f"{body_text}\n[Anhang konnte nicht heruntergeladen werden]"
        else:
            try:
                content, mime_guess = await gateway.download_file(file_id)
                attachments.append((filename, mime_guess or content_type_hint, content))
            except TelegramApiError as exc:
                # A download failure must not cost the customer their message —
                # keep the placeholder text, drop the attachment, note it.
                logger.warning(
                    "telegram_media_download_failed",
                    chat_id=contact.chat_id,
                    kind=kind,
                    error=str(exc),
                )
                body_text = f"{body_text}\n[Anhang konnte nicht heruntergeladen werden]"

    article = ArticleIn(
        sender_type="customer",
        is_visible_for_customer=True,
        subject=title,
        body=body_text,
        content_type="text/plain; charset=utf-8",
        from_address=from_address,
        channel=CHANNEL_NAME,
        attachments=attachments,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )

    inbound_message_id = int(message["message_id"])
    # Duplicate delivery guard: the offset check in the poller/webhook caller
    # is the normal dedup path (see tiqora.worker.telegram_poller and
    # tiqora.api.v1.channels_telegram), but two webhook deliveries of the
    # same update racing each other could both get past that check before
    # either commits. Without this, the second insert would hit the unique
    # (chat_id, message_id) index and crash the request instead of just
    # leaving the first row in place.
    if await get_by_message(session, contact.chat_id, inbound_message_id) is None:
        reply_to_article_id: int | None = None
        reply_to_message = message.get("reply_to_message")
        if isinstance(reply_to_message, dict) and "message_id" in reply_to_message:
            reply_row = await get_by_message(
                session, contact.chat_id, int(reply_to_message["message_id"])
            )
            if reply_row is not None:
                reply_to_article_id = reply_row.article_id
        await record_message(
            session,
            article_id=article_id,
            ticket_id=ticket_id,
            chat_id=contact.chat_id,
            message_id=inbound_message_id,
            direction="in",
            reply_to_article_id=reply_to_article_id,
        )

    return {"ticket_id": ticket_id, "article_id": article_id, "created_ticket": created}


async def build_gateway(session: AsyncSession) -> TelegramGateway | None:
    bot_token = await channel_setting(session, CHANNEL_NAME, "bot_token")
    if not bot_token:
        return None
    return TelegramGateway(bot_token=bot_token)
