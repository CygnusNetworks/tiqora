"""``Ticket::Generic`` invoker (G9, G10).

Port of ``Kernel/GenericInterface/Invoker/Ticket/Generic.pm``: ``PrepareRequest`` (G9) and
``HandleResponse`` / ``HandleError`` (G10b).

``handle_response`` runs the ``OTRS_*`` response actions in sorted key order as user 1 through
the existing write services (each call flushes only; one ``commit()`` at the end, ``rollback()``
on the first failure). Znuny key -> Tiqora call:

======================================  ================================================
``OTRS_TicketDynamicFieldSet_<Name>``    ``update_dynamic_field(field_name, values)``
``OTRS_TicketTitleUpdate``               ``change_title(new_title)``
``OTRS_TicketServiceSet``                ``change_service(new_service_id)``  (name lookup)
``OTRS_TicketTypeSet``                   ``change_type(new_type_id)``        (name lookup)
``OTRS_TicketQueueSet``                  ``move_queue(new_queue_id)``        (name lookup)
``OTRS_TicketSLASet``                    ``change_sla(new_sla_id)``          (name lookup)
``OTRS_TicketStateSet``                  ``change_state(new_state_id)``      (name lookup)
``OTRS_TicketPrioritySet``               ``change_priority(new_priority_id)`` (name lookup)
``OTRS_TicketCustomerSet`` ("No;User")   ``set_customer(customer_id, customer_user_id,
                                         only_given=True)``
``OTRS_TicketOwnerSet``                  ``assign_owner(new_owner_id)``      (login lookup)
``OTRS_TicketResponsibleSet``            ``assign_responsible(new_responsible_id)``
``OTRS_TicketHistoryAdd``                ``history_add(history_type, name, user_id=1)``
``OTRS_TicketArticleAdd``                ``add_article(ArticleIn(...))``
``OTRS_TicketCreate``                    ``create_ticket(TicketIn(...))``
``OTRS_AsynchronousInvokerExecution``    injected ``enqueue(payload)`` per item
``OTRS_TicketArticleCreateEvent``        unsupported (debugger error)
======================================  ================================================

``OTRS_TicketArticleAdd`` Znuny ``ArticleCreate`` key -> ``ArticleIn`` field: ``Subject`` ->
``subject``, ``Body`` -> ``body``, ``From`` -> ``from_address`` (default "Generic Interface"),
``To`` -> ``to_address``, ``Cc`` -> ``cc``, ``Bcc`` -> ``bcc``, ``ReplyTo`` -> ``reply_to``,
``MessageID`` -> ``message_id``, ``InReplyTo`` -> ``in_reply_to``, ``References`` ->
``references``, ``SenderType`` -> ``sender_type`` (default agent), ``IsVisibleForCustomer`` ->
``is_visible_for_customer`` (default 0), ``ChannelName`` (Internal/Phone/Email) -> ``channel``
(default internal), ``MimeType``/``ContentType`` -> ``content_type`` (always utf-8),
``HistoryType`` / ``HistoryComment`` -> ``history_type_override`` / ``history_comment_override``,
``NoAgentNotify`` -> ``no_agent_notify``, ``Force|Exclude|ExcludeMuteNotificationToUserID`` ->
the three id tuples, ``Attachment`` (hash or list, base64 ``Content``) -> ``attachments``. The
article never gets an agent signature (``append_signature=False``). Other keys (including
``UserID``, which would let a remote system act as someone else) are ignored with a debugger
notice.

``OTRS_TicketCreate`` Znuny ``TicketCreate`` key -> ``TicketIn`` field: ``Title`` -> ``title``,
``Queue``/``QueueID`` -> ``queue_id``, ``State``/``StateID`` -> ``state_id``,
``Priority``/``PriorityID`` -> ``priority_id``, ``Owner``/``OwnerID`` -> ``owner_id``,
``Responsible``/``ResponsibleID`` -> ``responsible_id``, ``Lock``/``LockID`` -> ``lock_id``,
``Type``/``TypeID`` -> ``type_id``, ``Service``/``ServiceID`` -> ``service_id``,
``SLA``/``SLAID`` -> ``sla_id``, ``CustomerID`` -> ``customer_id``, ``CustomerUser`` ->
``customer_user_id``. A missing title, queue, owner, state or priority is an error and nothing is
created (as in Znuny); a missing type is filled from SysConfig ``Ticket::Type::Default``. Any
other key is an error, nothing created. The ticket is created as user 1.
"""

from __future__ import annotations

import base64
import binascii
import copy
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketLockType, TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TicketIn,
    TicketNotFound,
    _ticket_must_exist,  # noqa: PLC2701
    add_article,
    assign_owner,
    assign_responsible,
    change_priority,
    change_service,
    change_sla,
    change_state,
    change_title,
    change_type,
    create_ticket,
    move_queue,
    set_customer,
    update_dynamic_field,
)
from tiqora.gi.config import InvokerConfig
from tiqora.gi.debugger import GiDebugger
from tiqora.gi.deep_get import (
    base64_deep_encode,
    data_structure_remove_elements,
    ticket_deep_get,
)
from tiqora.util.perl import perl_truthy
from tiqora.znuny.history import history_add
from tiqora.znuny.sysconfig import SysConfig

__all__ = ["handle_error", "handle_response", "prepare_request"]

_OMITTED: Final = "GenericInterface::Invoker::Ticket::Generic::PrepareRequest::OmittedFields"
_BASE64: Final = "GenericInterface::Invoker::Ticket::Generic::PrepareRequest::Base64EncodedFields"

_MONTH_ABBR: Final = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_DAY_ABBR: Final = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


async def _field_list(sysconfig: SysConfig, setting: str, invoker_name: str) -> list[str]:
    configured = await sysconfig.get(setting)
    if not isinstance(configured, dict):
        return []
    value = configured.get(invoker_name)
    if value is None or not str(value):
        return []
    # Perl: split /\s*;\s*/ (trailing empty fields are dropped)
    parts = re.split(r"\s*;\s*", str(value))
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _date_time_parts(seconds: int, zone: ZoneInfo, now: datetime) -> dict[str, Any]:
    """``Kernel::System::DateTime->Get()`` of *now* plus *seconds*."""
    moment = (now + timedelta(seconds=seconds)).astimezone(zone)
    return {
        "Year": moment.year,
        "Month": moment.month,
        "MonthAbbr": _MONTH_ABBR[moment.month - 1],
        "Day": moment.day,
        "Hour": moment.hour,
        "Minute": moment.minute,
        "Second": moment.second,
        "DayOfWeek": moment.isoweekday(),
        "DayAbbr": _DAY_ABBR[moment.weekday()],
        "TimeZone": zone.key,
    }


async def prepare_request(
    session: AsyncSession,
    *,
    invoker_name: str,
    invoker: InvokerConfig,
    data: dict[str, Any],
    sysconfig: SysConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    """``{"Ticket": <TicketDeepGet or {}>, "Event": <data>}`` after the omit/base64 lists.

    *data* is the event payload (``TicketID``, optional ``ArticleID``, ``GetAllArticleAttachments``
    and the ``Event`` name the requester added); it is copied, never changed. The SysConfig lists
    ``...PrepareRequest::OmittedFields`` and ``::Base64EncodedFields`` (hash: invoker name -> paths
    separated by ``;``) are applied to the ticket AND the event payload; other invoker names are
    untouched.
    """
    event = copy.deepcopy(data)
    get_all = (
        perl_truthy(event.get("GetAllArticleAttachments")) or invoker.get_all_article_attachments
    )

    ticket: dict[str, Any] = {}
    ticket_id = event.get("TicketID")
    if ticket_id is not None and perl_truthy(ticket_id):
        article_id = event.get("ArticleID")
        deep = await ticket_deep_get(
            session,
            int(ticket_id),
            article_id=int(article_id)
            if article_id is not None and perl_truthy(article_id)
            else None,
            all_attachments=get_all,
            sysconfig=sysconfig,
        )
        ticket = deep or {}
        until = ticket.get("UntilTime")
        if until:
            zone_name = await sysconfig.otrs_time_zone()
            try:
                zone = ZoneInfo(zone_name or "UTC")
            except (KeyError, ValueError, OSError):
                zone = ZoneInfo("UTC")
            ticket["UntilTimeDateTimeParts"] = _date_time_parts(
                int(until), zone, now or datetime.now(UTC)
            )

    omitted = await _field_list(sysconfig, _OMITTED, invoker_name)
    if omitted:
        data_structure_remove_elements(ticket, omitted)
        data_structure_remove_elements(event, omitted)

    encoded = await _field_list(sysconfig, _BASE64, invoker_name)
    if encoded:
        base64_deep_encode(ticket, encoded)
        base64_deep_encode(event, encoded)

    return {"Ticket": ticket, "Event": event}


# ---------------------------------------------------------------------------
# HandleResponse / HandleError (G10b)
# ---------------------------------------------------------------------------

_DF_KEY_RE: Final = re.compile(r"OTRS_TicketDynamicFieldSet_(.*)", re.IGNORECASE | re.DOTALL)
_ASYNC_KEY: Final = "OTRS_AsynchronousInvokerExecution"
_ARTICLE_EVENT_KEY: Final = "OTRS_TicketArticleCreateEvent"
_B64_ALPHABET: Final = re.compile(r"[^A-Za-z0-9+/]")
_UNLOCK_ID: Final = 1
_RESPONSE_USER: Final = 1  # Znuny: UserID => 1

_ARTICLE_CHANNELS: Final = {"internal": "internal", "phone": "phone", "email": "email"}
_ARTICLE_KEYS: Final = frozenset(
    {
        "Subject",
        "Body",
        "From",
        "To",
        "Cc",
        "Bcc",
        "ReplyTo",
        "MessageID",
        "InReplyTo",
        "References",
        "SenderType",
        "IsVisibleForCustomer",
        "ChannelName",
        "MimeType",
        "ContentType",
        "Charset",
        "HistoryType",
        "HistoryComment",
        "NoAgentNotify",
        "ForceNotificationToUserID",
        "ExcludeNotificationToUserID",
        "ExcludeMuteNotificationToUserID",
        "Attachment",
    }
)
_CREATE_KEYS: Final = frozenset(
    {
        "Title",
        "Queue",
        "QueueID",
        "State",
        "StateID",
        "Priority",
        "PriorityID",
        "Owner",
        "OwnerID",
        "Responsible",
        "ResponsibleID",
        "Lock",
        "LockID",
        "Type",
        "TypeID",
        "Service",
        "ServiceID",
        "SLA",
        "SLAID",
        "CustomerID",
        "CustomerNo",
        "CustomerUser",
        "ArchiveFlag",
        # Znuny's TicketCreate requires UserID; accepted but ignored (user 1 stays the actor).
        "UserID",
        "TN",
    }
)

Enqueue = Callable[..., Awaitable[None]]

#: ``OTRS_AsynchronousInvokerExecution`` guards (Tiqora deviation, ruling F13): at most this
#: many calls are queued per response (the rest is dropped with a debugger error) ...
MAX_ASYNC_ITEMS: Final = 10
#: ... and a call queued by a response action is one level deeper than the call whose response
#: queued it (event, transition, resend: depth 0). Items that would land at this depth or
#: deeper are refused, so a remote can never build an endless A -> A chain.
MAX_CHAIN_DEPTH: Final = 3


class _ActionError(Exception):
    """A response action failed; the text goes into the debugger line's content."""


# Error texts that are safe to show (no SQL statement or bound values, which could hold PII).
_SAFE_ERRORS: Final = (_ActionError, InvalidInput, TicketNotFound)


def _perl_str(value: Any) -> str:
    """How Znuny interpolates *value* into the error text (references without the address)."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return "HASH"
    if isinstance(value, list):
        return "ARRAY"
    return str(value)


def _perl_split_semicolon(value: str) -> list[str]:
    parts = value.split(";")
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _perl_decode_base64(text: str) -> bytes:
    """``MIME::Base64::decode_base64``: ignores characters outside the alphabet and stops at
    the first ``=``."""
    head = text.split("=", 1)[0]
    clean = _B64_ALPHABET.sub("", head)
    if len(clean) % 4 == 1:
        clean = clean[:-1]
    clean += "=" * (-len(clean) % 4)
    try:
        return base64.b64decode(clean)
    except (binascii.Error, ValueError):
        return b""


async def _lookup_id(
    session: AsyncSession, model: Any, name: Any, *, valid_only: bool, what: str
) -> int:
    """Name -> id (copy of the ``process/engine.py`` lookups, which are private)."""
    if not isinstance(name, str | int | float) or not perl_truthy(name):
        raise _ActionError(f"{what} fehlt.")
    column = model.login if model is Users else model.name
    stmt = select(model.id).where(column == str(name))
    if valid_only:
        stmt = stmt.where(model.valid_id == 1)
    found = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if found is None:
        raise _ActionError(f"{what} '{name}' ist unbekannt.")
    return int(found)


async def _optional_id(
    session: AsyncSession,
    data: dict[str, Any],
    *,
    name_key: str,
    id_key: str,
    model: Any,
    valid_only: bool,
    what: str,
) -> int | None:
    """``*ID`` wins over the name; ``None`` when neither is given."""
    if perl_truthy(data.get(id_key)):
        try:
            return int(data[id_key])
        except (TypeError, ValueError) as exc:
            raise _ActionError(f"{id_key} ist keine Zahl.") from exc
    if perl_truthy(data.get(name_key)):
        return await _lookup_id(session, model, data[name_key], valid_only=valid_only, what=what)
    return None


def _id_tuple(value: Any) -> tuple[int, ...]:
    items = value if isinstance(value, list) else [value]
    out: list[int] = []
    for item in items:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _article_attachments(raw: Any) -> list[tuple[str, str, bytes]]:
    items = [raw] if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[tuple[str, str, bytes]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("Content")
        if not isinstance(content, str) or content == "":
            continue
        filename = str(item.get("Filename") or "file")
        content_type = str(item.get("ContentType") or "application/octet-stream")
        out.append((filename, content_type, _perl_decode_base64(content)))
    return out


def _build_article(data: dict[str, Any], debugger_notes: list[str]) -> ArticleIn:
    mime = "text/plain"
    for key in ("ContentType", "MimeType"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            mime = value.split(";", 1)[0].strip().lower()
            break
    channel = "internal"
    raw_channel = data.get("ChannelName")
    if raw_channel is not None:
        mapped = _ARTICLE_CHANNELS.get(str(raw_channel).lower())
        if mapped is None:
            debugger_notes.append(
                f"ChannelName '{_perl_str(raw_channel)}' wird von Tiqora nicht unterstützt;"
                " der Artikel wird intern angelegt."
            )
        else:
            channel = mapped

    def text_or_none(key: str) -> str | None:
        value = data.get(key)
        return None if value is None else _perl_str(value)

    return ArticleIn(
        sender_type=str(data.get("SenderType") or "agent"),
        is_visible_for_customer=perl_truthy(data.get("IsVisibleForCustomer")),
        subject=_perl_str(data["Subject"]),
        body=_perl_str(data["Body"]),
        content_type=f"{mime}; charset=utf-8",
        from_address=text_or_none("From") or "Generic Interface",
        to_address=text_or_none("To"),
        cc=text_or_none("Cc"),
        bcc=text_or_none("Bcc"),
        reply_to=text_or_none("ReplyTo"),
        message_id=text_or_none("MessageID"),
        in_reply_to=text_or_none("InReplyTo"),
        references=text_or_none("References"),
        channel=channel,
        attachments=_article_attachments(data.get("Attachment")),
        history_type_override=text_or_none("HistoryType"),
        history_comment_override=text_or_none("HistoryComment"),
        no_agent_notify=perl_truthy(data.get("NoAgentNotify")),
        force_notification_user_ids=_id_tuple(data.get("ForceNotificationToUserID") or []),
        exclude_notification_user_ids=_id_tuple(data.get("ExcludeNotificationToUserID") or []),
        exclude_mute_notification_user_ids=_id_tuple(
            data.get("ExcludeMuteNotificationToUserID") or []
        ),
        append_signature=False,
    )


async def _create_ticket_action(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    data: dict[str, Any],
    debugger: GiDebugger,
) -> None:
    unknown = sorted(str(k) for k in data if k not in _CREATE_KEYS)
    if unknown:
        raise _ActionError(f"Nicht unterstützte Schlüssel: {', '.join(unknown)}")
    ignored = [k for k in ("UserID", "TN") if k in data]
    if ignored:
        await debugger.notice(
            f"Action 'OTRS_TicketCreate': Ignorierte Schlüssel: {', '.join(ignored)}"
        )
    title = data.get("Title")
    if not perl_truthy(title):
        raise _ActionError("Title fehlt.")

    queue_id = await _optional_id(
        session,
        data,
        name_key="Queue",
        id_key="QueueID",
        model=Queue,
        valid_only=True,
        what="Queue",
    )
    state_id = await _optional_id(
        session,
        data,
        name_key="State",
        id_key="StateID",
        model=TicketState,
        valid_only=False,
        what="State",
    )
    priority_id = await _optional_id(
        session,
        data,
        name_key="Priority",
        id_key="PriorityID",
        model=TicketPriority,
        valid_only=False,
        what="Priority",
    )
    owner_id = await _optional_id(
        session,
        data,
        name_key="Owner",
        id_key="OwnerID",
        model=Users,
        valid_only=True,
        what="Owner",
    )
    for given, label in (
        (queue_id, "Queue/QueueID"),
        (state_id, "State/StateID"),
        (priority_id, "Priority/PriorityID"),
        (owner_id, "Owner/OwnerID"),
    ):
        if given is None:
            raise _ActionError(f"{label} fehlt.")

    responsible_id = await _optional_id(
        session,
        data,
        name_key="Responsible",
        id_key="ResponsibleID",
        model=Users,
        valid_only=True,
        what="Responsible",
    )
    type_id = await _optional_id(
        session,
        data,
        name_key="Type",
        id_key="TypeID",
        model=TicketType,
        valid_only=True,
        what="Type",
    )
    if type_id is None:
        default_type = await sysconfig.get_str("Ticket::Type::Default", "")
        if default_type:
            type_id = await _lookup_id(
                session, TicketType, default_type, valid_only=True, what="Type"
            )
    service_id = await _optional_id(
        session,
        data,
        name_key="Service",
        id_key="ServiceID",
        model=Service,
        valid_only=True,
        what="Service",
    )
    sla_id = await _optional_id(
        session, data, name_key="SLA", id_key="SLAID", model=Sla, valid_only=True, what="SLA"
    )
    lock_id = await _optional_id(
        session,
        data,
        name_key="Lock",
        id_key="LockID",
        model=TicketLockType,
        valid_only=True,
        what="Lock",
    )

    # Znuny: ``CustomerNo || CustomerID``.
    customer_id = data.get("CustomerNo") or data.get("CustomerID")
    customer_user = data.get("CustomerUser")
    archive_flag = 1 if str(data.get("ArchiveFlag", "")).lower() in ("y", "1", "yes") else 0
    await create_ticket(
        session,
        session_factory,
        sysconfig,
        params=TicketIn(
            title=_perl_str(title)[:255],
            queue_id=queue_id,  # type: ignore[arg-type]  # checked above
            state_id=state_id,  # type: ignore[arg-type]
            priority_id=priority_id,  # type: ignore[arg-type]
            owner_id=owner_id,  # type: ignore[arg-type]
            lock_id=lock_id if lock_id is not None else _UNLOCK_ID,
            type_id=type_id,
            service_id=service_id,
            sla_id=sla_id,
            responsible_id=responsible_id,
            customer_id=None if customer_id is None else _perl_str(customer_id),
            customer_user_id=None if customer_user is None else _perl_str(customer_user),
            archive_flag=archive_flag,
        ),
        user_id=_RESPONSE_USER,
    )


async def _enqueue_invocations(
    key: str, value: Any, enqueue: Enqueue, debugger: GiDebugger, *, chain_depth: int
) -> dict[str, Any] | None:
    """``OTRS_AsynchronousInvokerExecution``; returns a failure dict or ``None``.

    The remote chooses webservice, invoker and data of every item, so two guards apply
    (:data:`MAX_CHAIN_DEPTH`, :data:`MAX_ASYNC_ITEMS`). Both write a debugger error but do not
    fail the response: a failure would let ``RequestRetry`` repeat the call, and the repeated
    answer would be refused again. ``PastExecutionData``/``Asynchronous`` of an item are never
    taken from the remote (always a first, asynchronous run).
    """
    if not isinstance(value, list) or not value:
        return await debugger.error(f"Parameter '{key}' needs to be an array with data.")
    depth = chain_depth + 1
    if depth >= MAX_CHAIN_DEPTH:
        await debugger.error(
            f"Action '{key}' abgelehnt: Aufrufe, die von Antworten ausgelöst werden, dürfen"
            f" höchstens {MAX_CHAIN_DEPTH - 1} Stufen tief verkettet sein.",
            f"{len(value)} Aufruf(e) nicht eingereiht.",
        )
        return None
    if len(value) > MAX_ASYNC_ITEMS:
        await debugger.error(
            f"Action '{key}': Tiqora reiht höchstens {MAX_ASYNC_ITEMS} Aufrufe pro Antwort ein.",
            f"{len(value) - MAX_ASYNC_ITEMS} weitere Aufruf(e) verworfen.",
        )
        value = value[:MAX_ASYNC_ITEMS]
    for item in value:
        if not isinstance(item, dict) or not item:
            return await debugger.error(
                f"Parameter '{key}' needs to be an array of hashes with data."
            )
        try:
            webservice_id = int(item["WebserviceID"])
            invoker = item["Invoker"]
            payload_data = item.get("Data", {})
            if not isinstance(invoker, str) or not invoker or not isinstance(payload_data, dict):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return await debugger.error(
                f"Parameter '{key}' needs to be an array of hashes with data."
            )
        try:
            await enqueue(
                {
                    "webservice_id": webservice_id,
                    "invoker": invoker,
                    "data": payload_data,
                    "asynchronous": True,
                    "origin": "response_action",
                    "chain_depth": depth,
                }
            )
        except Exception:
            return await debugger.error(
                "Error executing asynchronous call of Kernel::GenericInterface::Requester::Run()."
            )
    return None


async def _unchanged(session: AsyncSession, ticket_id: int, column: str, new_id: int) -> bool:
    """Znuny's ``Ticket{Queue,State,Priority,Responsible}Set`` write nothing when the value
    is unchanged (Ticket.pm:2157, 4531, 5088, 5382). Without this a remote echoing the current
    value would add history rows/events on every call (and loop event-driven invokers)."""
    ticket = await _ticket_must_exist(session, ticket_id)
    return int(ticket[column]) == new_id


async def _run_action(
    session: AsyncSession,
    key: str,
    value: Any,
    *,
    ticket_id: int | None,
    debugger: GiDebugger,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
) -> None:
    """One response action; raises :class:`_ActionError` (or a service error) on failure,
    returns normally when the action succeeded or Znuny would have skipped it."""
    user = _RESPONSE_USER
    df_match = _DF_KEY_RE.search(key)
    if df_match is None and key not in _TICKET_ACTION_KEYS:
        raise _ActionError("Unbekannte Aktion.")
    if ticket_id is None:
        raise _ActionError("Es gibt kein Ticket, auf das sich die Aktion beziehen kann.")

    if df_match is not None:
        values = (
            []
            if value is None
            else [_perl_str(v) for v in value]
            if isinstance(value, list)
            else [_perl_str(value)]
        )
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=df_match.group(1), values=values, user_id=user
        )
    elif key == "OTRS_TicketTitleUpdate":
        if value is None:
            raise _ActionError("Title fehlt.")
        await change_title(session, ticket_id=ticket_id, new_title=_perl_str(value), user_id=user)
    elif key == "OTRS_TicketServiceSet":
        service_id = await _lookup_id(session, Service, value, valid_only=True, what="Service")
        await change_service(
            session,
            ticket_id=ticket_id,
            new_service_id=service_id,
            user_id=user,
            sysconfig=sysconfig,
        )
    elif key == "OTRS_TicketTypeSet":
        type_id = await _lookup_id(session, TicketType, value, valid_only=True, what="Type")
        await change_type(
            session, ticket_id=ticket_id, new_type_id=type_id, user_id=user, sysconfig=sysconfig
        )
    elif key == "OTRS_TicketQueueSet":
        queue_id = await _lookup_id(session, Queue, value, valid_only=True, what="Queue")
        if not await _unchanged(session, ticket_id, "queue_id", queue_id):
            await move_queue(
                session,
                ticket_id=ticket_id,
                new_queue_id=queue_id,
                user_id=user,
                sysconfig=sysconfig,
            )
    elif key == "OTRS_TicketSLASet":
        sla_id = await _lookup_id(session, Sla, value, valid_only=True, what="SLA")
        await change_sla(
            session, ticket_id=ticket_id, new_sla_id=sla_id, user_id=user, sysconfig=sysconfig
        )
    elif key == "OTRS_TicketCustomerSet":
        parts = _perl_split_semicolon(_perl_str(value))
        await set_customer(
            session,
            ticket_id=ticket_id,
            customer_id=parts[0] if len(parts) > 0 else None,
            customer_user_id=parts[1] if len(parts) > 1 else None,
            user_id=user,
            only_given=True,
        )
    elif key == "OTRS_TicketStateSet":
        state_id = await _lookup_id(session, TicketState, value, valid_only=False, what="State")
        if not await _unchanged(session, ticket_id, "ticket_state_id", state_id):
            await change_state(
                session,
                ticket_id=ticket_id,
                new_state_id=state_id,
                user_id=user,
                sysconfig=sysconfig,
            )
    elif key == "OTRS_TicketOwnerSet":
        owner_id = await _lookup_id(session, Users, value, valid_only=True, what="Owner")
        await assign_owner(
            session, ticket_id=ticket_id, new_owner_id=owner_id, user_id=user, sysconfig=sysconfig
        )
    elif key == "OTRS_TicketResponsibleSet":
        responsible_id = await _lookup_id(
            session, Users, value, valid_only=True, what="Responsible"
        )
        if not await _unchanged(session, ticket_id, "responsible_user_id", responsible_id):
            await assign_responsible(
                session, ticket_id=ticket_id, new_responsible_id=responsible_id, user_id=user
            )
    elif key == "OTRS_TicketPrioritySet":
        priority_id = await _lookup_id(
            session, TicketPriority, value, valid_only=False, what="Priority"
        )
        if not await _unchanged(session, ticket_id, "ticket_priority_id", priority_id):
            await change_priority(
                session,
                ticket_id=ticket_id,
                new_priority_id=priority_id,
                user_id=user,
                sysconfig=sysconfig,
            )
    elif key == "OTRS_TicketArticleAdd":
        # Reached only with the required keys present (checked by the caller).
        assert isinstance(value, dict)  # noqa: S101 -- narrowed for mypy
        notes: list[str] = []
        unknown = sorted(str(k) for k in value if k not in _ARTICLE_KEYS)
        if unknown:
            notes.append(f"Ignorierte Schlüssel: {', '.join(unknown)}")
        article = _build_article(value, notes)
        for note in notes:
            await debugger.notice(f"Action '{key}': {note}")
        await add_article(
            session, ticket_id=ticket_id, article=article, user_id=user, sysconfig=sysconfig
        )


_TICKET_ACTION_KEYS: Final = frozenset(
    {
        "OTRS_TicketTitleUpdate",
        "OTRS_TicketServiceSet",
        "OTRS_TicketTypeSet",
        "OTRS_TicketQueueSet",
        "OTRS_TicketSLASet",
        "OTRS_TicketCustomerSet",
        "OTRS_TicketStateSet",
        "OTRS_TicketOwnerSet",
        "OTRS_TicketResponsibleSet",
        "OTRS_TicketPrioritySet",
        "OTRS_TicketArticleAdd",
    }
)


async def handle_response(
    session: AsyncSession,
    *,
    ticket_id: int | None,
    success: bool,
    data: Any,
    error_message: str | None,
    debugger: GiDebugger,
    enqueue: Enqueue,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    chain_depth: int = 0,
) -> dict[str, Any]:
    """``Generic.pm`` ``HandleResponse``: execute the ``OTRS_*`` actions in *data*, then forward
    a response error. Returns the Znuny result shape (``Success`` plus ``Data`` or
    ``ErrorMessage``).

    Everything runs in *session* as user 1: one ``commit()`` at the end (also when the response
    itself was an error - Znuny runs the actions in that case, too), ``rollback()`` of the whole
    response on the first failing action. *enqueue* receives a plain dict (``webservice_id``,
    ``invoker``, ``data``, ``asynchronous=True``, ``origin="response_action"``,
    ``chain_depth=chain_depth + 1``) per ``OTRS_AsynchronousInvokerExecution`` item; the caller
    adapts it to ``GiInvokePayload``. *chain_depth* is the depth of the current call.
    Znuny's ``MaximumParallelInstances`` is not honoured (the GI job lanes limit concurrency).
    """
    if not isinstance(data, dict) or not data:
        return {"Success": 1, "Data": data}

    for key in sorted(data):
        value = data[key]
        failure: dict[str, Any] | None = None
        try:
            if key == _ASYNC_KEY:
                failure = await _enqueue_invocations(
                    key, value, enqueue, debugger, chain_depth=chain_depth
                )
            elif key == _ARTICLE_EVENT_KEY:
                failure = await debugger.error(f"Action '{key}' wird von Tiqora nicht unterstützt.")
            elif key == "OTRS_TicketHistoryAdd":
                if not isinstance(value, dict) or value.get("HistoryType") is None:
                    failure = await debugger.error(
                        f"Missing parameter 'HistoryType' on action '{key}' with value"
                        f" '{_perl_str(value)}'. Failed to execute!"
                    )
                elif ticket_id is None:
                    raise _ActionError("Es gibt kein Ticket, auf das sich die Aktion bezieht.")
                else:
                    await history_add(
                        session,
                        ticket_id=ticket_id,
                        history_type=_perl_str(value.get("HistoryType")) or "AddNote",
                        name=_perl_str(
                            value.get("Name")
                            if perl_truthy(value.get("Name"))
                            else value.get("HistoryComment")
                            if perl_truthy(value.get("HistoryComment"))
                            else " "
                        ),
                        user_id=_RESPONSE_USER,
                    )
            elif key == "OTRS_TicketCreate":
                if not isinstance(value, dict):
                    raise _ActionError("Die Daten müssen ein Hash sein.")
                await _create_ticket_action(session, session_factory, sysconfig, value, debugger)
            elif key == "OTRS_TicketArticleAdd":
                if not isinstance(value, dict):
                    raise _ActionError("Die Daten müssen ein Hash sein.")
                for needed in ("Subject", "Body"):
                    if value.get(needed) is None:
                        failure = await debugger.error(
                            f"Missing parameter '{needed}' on action '{key}'. Failed to execute!"
                        )
                        break
                else:
                    await _run_action(
                        session,
                        key,
                        value,
                        ticket_id=ticket_id,
                        debugger=debugger,
                        session_factory=session_factory,
                        sysconfig=sysconfig,
                    )
            elif _DF_KEY_RE.search(key) is not None or key in _TICKET_ACTION_KEYS:
                await _run_action(
                    session,
                    key,
                    value,
                    ticket_id=ticket_id,
                    debugger=debugger,
                    session_factory=session_factory,
                    sysconfig=sysconfig,
                )
            # Other keys are ordinary response data: Znuny ignores them (Success stays 1).
        except Exception as exc:
            await session.rollback()
            return await debugger.error(
                f"Error on response action '{key}' with value '{_perl_str(value)}'."
                " Failed to execute!",
                str(exc) if isinstance(exc, _SAFE_ERRORS) else type(exc).__name__,
            )
        if failure is not None:
            await session.rollback()
            return failure

    await session.commit()

    if not success:
        if not isinstance(error_message, str) or not error_message:
            return await debugger.error("Got response error, but no response error message!")
        return {"Success": 0, "ErrorMessage": error_message}
    return {"Success": 1, "Data": data}


async def handle_error(
    session: AsyncSession,
    *,
    ticket_id: int | None,
    data: Any,
    debugger: GiDebugger,
    enqueue: Enqueue,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    chain_depth: int = 0,
) -> dict[str, Any]:
    """``Generic.pm`` ``HandleError``: ``handle_response`` with ``success=False`` and no message.

    The error data may carry further ``OTRS_*`` actions, which run as usual; the result is
    a failure by design (Znuny reports "no response error message").
    """
    return await handle_response(
        session,
        ticket_id=ticket_id,
        success=False,
        data=data,
        error_message=None,
        debugger=debugger,
        enqueue=enqueue,
        session_factory=session_factory,
        sysconfig=sysconfig,
        chain_depth=chain_depth,
    )
