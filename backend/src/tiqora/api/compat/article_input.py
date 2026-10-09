"""GenericInterface TicketCreate / TicketUpdate input: article lists and dynamic fields.

Ports the request-side logic of ``Operation/Ticket/TicketCreate.pm`` and
``TicketUpdate.pm`` (``Run``, ``_CheckArticle``, ``_CheckDynamicField``,
``_ReturnSuccess``) onto the Tiqora domain types. Everything here validates and
shapes data; the write order lives in ``operations.py``.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.compat.mapping.perl import perl_is_string, perl_str, perl_truthy
from tiqora.domain.dynamic_field_values import (
    DynamicFieldConfig,
    DynamicFieldValueError,
    load_dynamic_field,
    normalize_df_values,
    possible_values_of,
)
from tiqora.domain.ticket_write_service import ArticleIn
from tiqora.znuny.sysconfig import SysConfig

_AGENT_USER_TYPE = "User"
_TIME_UNIT_RE = re.compile(r"\d+(?:[.,]\d+)?", re.ASCII)
# Znuny channel names (Common.pm ValidateArticleCommunicationChannel) -> Tiqora channel
_CHANNEL_MAP = {"Internal": "note", "Email": "email", "Phone": "phone", "Chat": "note"}
_USER_LIST_KEYS = (
    "ForceNotificationToUserID",
    "ExcludeNotificationToUserID",
    "ExcludeMuteNotificationToUserID",
)

logger = structlog.get_logger(__name__)


def gi_error(code: str, message: str) -> dict[str, Any]:
    """A Znuny-style GenericInterface error envelope (``ReturnError``)."""
    return {"Error": {"ErrorCode": code, "ErrorMessage": message}}


@dataclass(frozen=True, slots=True)
class GiArticle:
    """One validated ``Article`` of a TicketCreate / TicketUpdate request."""

    article: ArticleIn
    send: bool  # ArticleSend (agents only): deliver by SMTP
    time_unit: str | None  # validated, Perl-true TimeUnit (booked with add_time_accounting)
    auto_response_type: str  # "" = none
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GiDynamicFields:
    """Validated request dynamic fields, split by the field's object type."""

    ticket: dict[str, list[str]]
    article: dict[str, list[str]]
    configs: dict[str, DynamicFieldConfig]


NO_DYNAMIC_FIELDS = GiDynamicFields(ticket={}, article={}, configs={})


def normalize_article_list(raw: Any) -> list[dict[str, Any]]:
    """``Article`` as a list of article dicts (single hash or list of hashes)."""
    if isinstance(raw, dict):
        return [raw] if raw else []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def _trim(value: Any) -> Any:
    """``s/(?:\\A\\s+|\\s+\\z)//gx`` on a scalar; lists and hashes stay untouched."""
    return value.strip() if isinstance(value, str) else value


def _scalar_text(value: Any) -> str | None:
    """A request scalar as text; ``None`` for undef / empty."""
    if value is None:
        return None
    text_value = perl_str(value) if not isinstance(value, str) else value
    return text_value or None


def _df_values(value: Any) -> list[str] | None:
    """DynamicField ``Value`` -> value list (``None`` = not a string or list of scalars)."""
    if isinstance(value, list):
        if not all(perl_is_string(item) for item in value):
            return None
        return [perl_str(item) for item in value]
    if perl_is_string(value):
        return [perl_str(_trim(value))]
    return None


async def _value_is_valid(
    session: AsyncSession, config: DynamicFieldConfig, raw_value: Any, values: list[str]
) -> bool:
    """Znuny ``FieldValueValidate``: storable value, and for selects a PossibleValues key.

    ``Dropdown.pm`` / ``Multiselect.pm``: the field needs PossibleValues; a list is no
    Dropdown value; an empty scalar passes, every element of a Multiselect list must
    be a key (``""`` only when it is one).
    """
    try:
        normalize_df_values(config.field_type, values)
    except DynamicFieldValueError:
        return False
    if config.field_type not in ("Dropdown", "Multiselect"):
        return True
    allowed = await possible_values_of(session, config)
    if not allowed:
        return False
    if isinstance(raw_value, list):
        return config.field_type == "Multiselect" and all(v in allowed for v in values)
    return values[0] == "" or values[0] in allowed


def split_dynamic_fields(
    items: Any, article_field_names: frozenset[str]
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Request ``DynamicField`` -> ``(ticket_fields, article_fields)`` as name -> values.

    ``items`` is one ``{Name, Value}`` hash or a list of them; ``Value`` may be a
    list (Multiselect), a scalar or ``None`` (no values).
    """
    ticket_fields: dict[str, list[str]] = {}
    article_fields: dict[str, list[str]] = {}
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return ticket_fields, article_fields
    for item in items:
        if not isinstance(item, dict):
            continue
        name = _trim(item.get("Name"))
        if not isinstance(name, str) or not name:
            continue
        value = item.get("Value")
        values = [] if value is None else (_df_values(value) or [])
        target = article_fields if name in article_field_names else ticket_fields
        target[name] = values
    return ticket_fields, article_fields


async def parse_gi_dynamic_fields(
    raw: Any, *, op: str, articles_present: bool, session: AsyncSession
) -> GiDynamicFields | dict[str, Any]:
    """Validate request dynamic fields before anything is written.

    Port of ``_CheckDynamicField`` (unknown name, missing/invalid value; on
    TicketUpdate an Article field needs an article) plus the wave-0 strictness
    for Dropdown / Multiselect values (``PossibleValues``, string keys).
    """
    if raw is None:
        return NO_DYNAMIC_FIELDS
    items = raw if isinstance(raw, list) else [raw]
    # Create has a double space in its "missing" text, Update a single one.
    gap = "  " if op == "TicketCreate" else " "
    configs: dict[str, DynamicFieldConfig] = {}
    for item in items:
        if not isinstance(item, dict) or not item:
            return gi_error(
                f"{op}.InvalidParameter", f"{op}: Ticket->DynamicField parameter is invalid!"
            )
        name = _trim(item.get("Name"))
        value = item.get("Value")
        for needed, got in (("Name", name), ("Value", value)):
            if got is None or not (perl_is_string(got) or isinstance(got, list)):
                return gi_error(
                    f"{op}.MissingParameter",
                    f"{op}: DynamicField->{needed}{gap}parameter is missing!",
                )
        config = await load_dynamic_field(session, name) if isinstance(name, str) else None
        # the database may match case-insensitively; Znuny's lookup is an exact hash key
        if config is None or config.name != name:
            return gi_error(
                f"{op}.InvalidParameter", f"{op}: DynamicField->Name parameter is invalid!"
            )
        if op == "TicketUpdate" and config.object_type == "Article" and not articles_present:
            return gi_error(
                f"{op}.MissingParameter",
                f"{op}: To create an article DynamicField an article is required!",
            )
        values = _df_values(value)
        valid = values is not None and await _value_is_valid(session, config, value, values)
        if not valid:
            return gi_error(
                f"{op}.InvalidParameter", f"{op}: DynamicField->Value parameter is invalid!"
            )
        configs[config.name] = config
    article_names = frozenset(n for n, c in configs.items() if c.object_type == "Article")
    ticket_fields, article_fields = split_dynamic_fields(items, article_names)
    return GiDynamicFields(ticket=ticket_fields, article=article_fields, configs=configs)


def article_id_result(result: dict[str, Any], ids: list[int], *, op: str) -> dict[str, Any]:
    """Add ``ArticleID`` / ``ArticleIDs`` the way Znuny's create and update answers do."""
    out = dict(result)
    if op == "TicketCreate":
        # TicketCreate.pm $ArticleIDNew: the bare id for one article, the list otherwise
        if len(ids) == 1:
            out["ArticleID"] = ids[0]
        elif ids:
            out["ArticleID"] = list(ids)
    elif len(ids) > 1:
        out["ArticleIDs"] = list(ids)
    elif len(ids) == 1:
        out["ArticleID"] = ids[0]
    return out


async def _name_exists(session: AsyncSession, table: str, name: str) -> bool:
    row = (
        await session.execute(
            text(f"SELECT 1 FROM {table} WHERE name = :n LIMIT 1"),  # noqa: S608 - fixed table
            {"n": name},
        )
    ).first()
    return row is not None


async def _valid_user(session: AsyncSession, raw_id: Any) -> bool:
    """``ValidateUserID``: a non-zero id of a valid agent."""
    if not perl_truthy(raw_id) or not isinstance(raw_id, str | int) or isinstance(raw_id, bool):
        return False
    try:
        user_id = int(raw_id)
    except ValueError:
        return False
    row = (
        await session.execute(
            text("SELECT 1 FROM users WHERE id = :i AND valid_id = 1 LIMIT 1"), {"i": user_id}
        )
    ).first()
    return row is not None


async def _check_user_lists(
    session: AsyncSession, raw: dict[str, Any], *, op: str
) -> dict[str, Any] | None:
    """``Force*`` / ``Exclude*`` notification user ids: validated like Znuny, never forwarded."""
    for key in _USER_LIST_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, dict) and value:
            return gi_error(f"{op}.InvalidParameter", f"{op}: Article->{key} parameter is invalid!")
        ids = value if isinstance(value, list) and value else [value]
        for user_id in ids:
            if not await _valid_user(session, user_id):
                shown = "ARRAY(0x0)" if isinstance(user_id, list | dict) else perl_str(user_id)
                return gi_error(
                    f"{op}.InvalidParameter",
                    f"{op}: Article->{key} UserID={shown} parameter is invalid!",
                )
    return None


async def _channel_name(session: AsyncSession, raw: dict[str, Any]) -> str:
    name = _scalar_text(raw.get("CommunicationChannel"))
    if name:
        return name
    channel_id = raw.get("CommunicationChannelID")
    if channel_id is not None and perl_truthy(channel_id):
        try:
            row = (
                await session.execute(
                    text("SELECT name FROM communication_channel WHERE id = :i"),
                    {"i": int(channel_id)},
                )
            ).first()
        except (TypeError, ValueError):
            row = None
        if row is not None:
            return str(row[0])
    return "Internal"


def _attachments(raw: Any) -> list[tuple[str, str, bytes]]:
    items = [raw] if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    out: list[tuple[str, str, bytes]] = []
    for att in items:
        if not isinstance(att, dict):
            continue
        try:
            content = base64.b64decode(att.get("Content") or "")
        except Exception:
            content = b""
        out.append(
            (
                att.get("Filename") or "attachment",
                att.get("ContentType") or "application/octet-stream",
                content,
            )
        )
    return out


async def parse_gi_articles(
    raw: Any,
    *,
    op: str,
    user_type: str,
    has_owner: bool,
    session: AsyncSession,
    sysconfig: SysConfig,
) -> list[GiArticle] | dict[str, Any]:
    """Validate every ``Article`` of the request (nothing is written) and map it to domain input.

    ``_CheckArticle`` of TicketCreate / TicketUpdate: SysConfig defaults, then
    ``AutoResponseType``, ``ArticleSend`` recipient, ``HistoryType``, ``TimeUnit``,
    ``NoAgentNotify`` and the ``Force*/Exclude*`` user id lists. Returns the
    error envelope of the first failing article.
    """
    prefix = f"GenericInterface::Operation::{op}###"
    cfg_auto = str(await sysconfig.get(prefix + "AutoResponseType") or "")
    cfg_history_type = str(await sysconfig.get(prefix + "HistoryType") or "")
    cfg_history_comment = str(await sysconfig.get(prefix + "HistoryComment") or "")
    cfg_visible = await sysconfig.get(prefix + "IsVisibleForCustomer")
    visible_default = True if cfg_visible is None else perl_truthy(cfg_visible)
    account_time = perl_truthy(await sysconfig.get("Ticket::Frontend::AccountTime"))
    need_time = perl_truthy(await sysconfig.get("Ticket::Frontend::NeedAccountedTime"))

    out: list[GiArticle] = []
    for original in normalize_article_list(raw):
        art = {key: _trim(value) for key, value in original.items()}

        auto_response = _scalar_text(art.get("AutoResponseType")) or cfg_auto
        history_type = _scalar_text(art.get("HistoryType")) or cfg_history_type
        history_comment = _scalar_text(art.get("HistoryComment")) or cfg_history_comment

        if auto_response and not await _name_exists(session, "auto_response_type", auto_response):
            return gi_error(
                f"{op}.InvalidParameter", f"{op}: Article->AutoResponseType parameter is invalid!"
            )

        send = user_type == _AGENT_USER_TYPE and perl_truthy(art.get("ArticleSend"))
        if send and not (_scalar_text(art.get("To")) or "").strip():
            return gi_error(
                f"{op}.InvalidParameter",
                f"{op}: Article->To parameter must be a valid email address"
                " when Article->ArticleSend is set!",
            )

        if not history_type:
            return gi_error(
                f"{op}.InvalidParameter",
                f"{op}: Article-> HistoryType is required and Sysconfig"
                " HistoryType setting could not be read!",
            )
        if not await _name_exists(session, "ticket_history_type", history_type):
            return gi_error(
                f"{op}.InvalidParameter", f"{op}: Article->HistoryType parameter is invalid!"
            )

        time_raw = art.get("TimeUnit")
        time_text = None if time_raw is None else perl_str(time_raw)
        if (time_text is None or time_text == "") and account_time and need_time:
            return gi_error(
                f"{op}.MissingParameter",
                f"{op}: Article->TimeUnit is required by sysconfig option!",
            )
        time_unit: str | None = None
        if perl_truthy(time_text):
            if not _TIME_UNIT_RE.fullmatch(time_text or ""):
                return gi_error(
                    f"{op}.InvalidParameter", f"{op}: Article->TimeUnit parameter is invalid!"
                )
            time_unit = time_text

        no_notify_raw = art.get("NoAgentNotify")
        if perl_truthy(no_notify_raw) and perl_str(no_notify_raw) != "1":
            return gi_error(
                f"{op}.InvalidParameter", f"{op}: Article->NoAgent parameter is invalid!"
            )

        if (err := await _check_user_lists(session, art, op=op)) is not None:
            return err

        # TicketCreate: an undefined NoAgentNotify means "no agent mail when an owner was given".
        no_agent_notify = (
            has_owner
            if no_notify_raw is None and op == "TicketCreate"
            else perl_truthy(no_notify_raw)
        )

        channel_name = await _channel_name(session, art)
        sender = _scalar_text(art.get("SenderType"))
        sender_type = (
            sender.lower() if sender else ("agent" if user_type == _AGENT_USER_TYPE else "customer")
        )
        visible_raw = art.get("IsVisibleForCustomer")
        visible = visible_default if visible_raw is None else perl_truthy(visible_raw)

        content_type = _scalar_text(art.get("ContentType"))
        if not content_type:
            mime_type = _scalar_text(art.get("MimeType"))
            if mime_type:
                content_type = f"{mime_type}; charset={_scalar_text(art.get('Charset')) or 'utf-8'}"

        signature_raw = art.get("AppendSignatureToBody")
        article = ArticleIn(
            sender_type=sender_type,
            is_visible_for_customer=visible,
            subject=_scalar_text(art.get("Subject")) or "",
            body=perl_str(art.get("Body")) if art.get("Body") is not None else "",
            content_type=content_type or "text/plain; charset=utf-8",
            from_address=_scalar_text(art.get("From")),
            to_address=_scalar_text(art.get("To")),
            cc=_scalar_text(art.get("Cc")),
            bcc=_scalar_text(art.get("Bcc")),
            message_id=_scalar_text(art.get("MessageID")),
            in_reply_to=_scalar_text(art.get("InReplyTo")),
            references=_scalar_text(art.get("References")),
            channel=_CHANNEL_MAP.get(channel_name, "note"),
            attachments=_attachments(art.get("Attachment")),
            history_type_override=history_type,
            history_comment_override=history_comment or "%%",
            no_agent_notify=no_agent_notify,
            append_signature=True if signature_raw is None else perl_truthy(signature_raw),
            signature_directly_after_body=True,
        )
        out.append(
            GiArticle(
                article=article,
                send=send,
                time_unit=time_unit,
                auto_response_type=auto_response,
                raw=art,
            )
        )
    return out


async def send_gi_auto_response(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    queue_id: int,
    auto_response_type: str,
    from_header: str,
    subject: str,
    body: str,
    user_id: int,
) -> int | None:
    """The queue's auto response for a request article (Znuny ``ArticleCreate`` →
    ``SendAutoResponse``, ``OrigHeader`` = the article's From / Subject / Body).

    Only for articles stored with ``add_article``; the caller does not call it for
    ``ArticleSend``. Sent through the configured outbound relay; without one nothing is
    sent (never the localhost:25 fallback from a request). Caller commits. Returns the
    id of the stored auto-response article, or ``None`` when nothing was sent.
    """
    from tiqora.channels.email.autoresponse import send_auto_response
    from tiqora.domain import mail_outbound

    if not (await mail_outbound.resolve_outbound_smtp(session)).enabled:
        logger.info("compat_auto_response_no_outbound_relay", ticket_id=ticket_id)
        return None
    sender = await mail_outbound.build_outbound_sender(
        session, sendmail_bcc=(await sysconfig.sendmail_bcc()) or None
    )
    return await send_auto_response(
        session,
        session_factory,
        sysconfig,
        sender,
        ticket_id=ticket_id,
        queue_id=queue_id,
        auto_response_type=auto_response_type,
        recipient_from_header=from_header,
        orig_subject=subject,
        orig_body=body,
        orig_message_id=None,
        orig_x_otrs_loop=None,
        user_id=user_id,
    )
