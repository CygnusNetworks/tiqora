"""Phone call logging.

:func:`log_phone_call_on_ticket` is the one place a phone call becomes an
article -- the port of Znuny's ``AgentTicketPhoneInbound`` /
``AgentTicketPhoneOutbound`` screens (``AgentTicketPhoneCommon``):

- a ``Phone``-channel article, sender ``customer`` (inbound) or ``agent``
  (outbound), history ``PhoneCallCustomer`` / ``PhoneCallAgent``;
- the booked time bound to that article (``TicketAccountTime``);
- ticket dynamic fields, attachments;
- the next state (+ pending time), with the close screen's lock/unlock;
- ``RequiredLock`` per direction (sysconfig, outbound on by default): an
  unlocked ticket is locked and the agent becomes owner, a ticket locked by
  someone else raises :class:`PhoneCallLockedByOther`.

It is used by the agent endpoint (``POST /tickets/{id}/phone-calls``), the
new phone ticket flow (``POST /tickets`` with ``phone_call``) and the CTI
``/channels/phone/note`` webhook (:func:`log_phone_call`, which additionally
resolves or creates the ticket from the caller number). Callers own the
transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.common import (
    channel_setting,
    resolve_customer_by_phone,
    resolve_ticket_for_inbound,
)
from tiqora.domain.ticket_write_service import (
    ArticleIn,
    InvalidInput,
    TicketAccessDenied,
    _state_name,
    _state_type_name,
    _ticket_must_exist,
    acquire_lock,
    add_article,
    add_time_accounting,
    change_state,
    unlock_ticket,
    update_dynamic_field,
)
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

CHANNEL_NAME = "phone"

Direction = Literal["inbound", "outbound"]


@dataclass(frozen=True, slots=True)
class PhoneNoteResult:
    ticket_id: int
    article_id: int
    created: bool


@dataclass
class PhoneCallIn:
    """One logged call. ``time_unit`` in Znuny time units (minutes here)."""

    direction: Direction
    subject: str
    body: str
    content_type: str = "text/plain; charset=utf-8"
    is_visible_for_customer: bool = True
    state_id: int | None = None
    pending_time: datetime | None = None
    time_unit: float | None = None
    #: Ticket dynamic fields ``{name: [values]}``; unknown names are ignored
    #: (Znuny ``DynamicFieldValueSet`` behaviour).
    dynamic_fields: dict[str, list[str]] = field(default_factory=dict)
    #: ``(filename, content_type, content)``
    attachments: list[tuple[str, str, bytes]] = field(default_factory=list)
    #: The other party's number, used as From/To when no customer is known.
    caller_number: str | None = None


@dataclass(frozen=True, slots=True)
class PhoneCallResult:
    ticket_id: int
    article_id: int
    time_accounting_id: int | None
    #: True when this call locked the ticket (outbound RequiredLock).
    locked: bool


class PhoneCallLockedByOther(Exception):
    """RequiredLock applies and another agent holds the ticket lock."""

    def __init__(self, locked_by_id: int | None, locked_by_name: str | None) -> None:
        super().__init__(f"ticket locked by {locked_by_name or locked_by_id}")
        self.locked_by_id = locked_by_id
        self.locked_by_name = locked_by_name


def _validate_direction(direction: str) -> None:
    if direction not in ("inbound", "outbound"):
        raise ValueError("direction must be 'inbound' or 'outbound'")


async def customer_address(session: AsyncSession, login: str | None) -> str | None:
    """``"Full Name" <email>`` of a customer user (Znuny's phone From)."""
    if not login:
        return None
    row = (
        await session.execute(
            text(
                "SELECT first_name, last_name, email FROM customer_user"
                " WHERE login = :login LIMIT 1"
            ),
            {"login": login},
        )
    ).first()
    if row is None or not row[2]:
        return None
    full = " ".join(p for p in (row[0], row[1]) if p).strip()
    return f'"{full}" <{row[2]}>' if full else str(row[2])


async def validate_next_state(
    session: AsyncSession, state_id: int, pending_time: datetime | None
) -> None:
    """Unknown state or a pending state without time -> :class:`InvalidInput`."""
    await _state_name(session, state_id)
    state_type = await _state_type_name(session, state_id)
    if state_type.lower().startswith("pending") and pending_time is None:
        raise InvalidInput(f"pending_time is required for pending state {state_id}")


async def log_phone_call_on_ticket(
    session: AsyncSession,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    user_id: int,
    call: PhoneCallIn,
    enforce_permissions: bool = True,
) -> PhoneCallResult:
    """Log *call* on *ticket_id* (see module docstring). Caller commits.

    Everything that can be refused (permission, state, lock) is checked
    before the first write, so a failure leaves nothing behind even for a
    caller that does not roll back.

    ``enforce_permissions=False`` is for unattended integrations (CTI) that
    authenticate with a shared secret instead of an agent session; they skip
    the queue permission and the RequiredLock handling.
    """
    _validate_direction(call.direction)
    ticket = await _ticket_must_exist(session, ticket_id)
    queue_id = int(ticket["queue_id"])

    # Znuny's phone screens require the ``phone`` key; Tiqora's permission
    # engine knows the System::Permission defaults only, so ``rw``.
    if enforce_permissions and not await PermissionEngine(session).check(user_id, queue_id, "rw"):
        raise TicketAccessDenied(f"user {user_id} lacks rw on queue {queue_id}")
    if call.state_id is not None:
        await validate_next_state(session, call.state_id, call.pending_time)
    if call.time_unit is not None and call.time_unit < 0:
        raise InvalidInput("time_unit must not be negative")

    locked = False
    if enforce_permissions:
        lock = await acquire_lock(
            session,
            ticket_id=ticket_id,
            user_id=user_id,
            sysconfig=sysconfig,
            action=f"phone_{call.direction}",
        )
        if lock.result == "locked_by_other":
            raise PhoneCallLockedByOther(lock.locked_by_id, lock.locked_by_name)
        locked = lock.result == "acquired"

    other_party = (
        await customer_address(session, ticket.get("customer_user_id")) or call.caller_number
    )
    inbound = call.direction == "inbound"
    article_id = await add_article(
        session,
        ticket_id=ticket_id,
        article=ArticleIn(
            sender_type="customer" if inbound else "agent",
            is_visible_for_customer=call.is_visible_for_customer,
            subject=call.subject,
            body=call.body,
            content_type=call.content_type,
            # Outbound: add_article stamps the agent's name as From.
            from_address=other_party if inbound else None,
            to_address=None if inbound else other_party,
            channel=CHANNEL_NAME,
            attachments=list(call.attachments),
        ),
        user_id=user_id,
        sysconfig=sysconfig,
    )

    time_accounting_id: int | None = None
    if call.time_unit:
        time_accounting_id = await add_time_accounting(
            session,
            ticket_id=ticket_id,
            article_id=article_id,
            time_unit=call.time_unit,
            user_id=user_id,
        )

    for name, values in call.dynamic_fields.items():
        await update_dynamic_field(
            session, ticket_id=ticket_id, field_name=name, values=values, user_id=user_id
        )

    if call.state_id is not None:
        closing = (await _state_type_name(session, call.state_id)).lower().startswith("close")
        if closing and enforce_permissions:
            # Same as the close screen: closing needs the lock first.
            await acquire_lock(
                session,
                ticket_id=ticket_id,
                user_id=user_id,
                sysconfig=sysconfig,
                action="close",
            )
        await change_state(
            session,
            ticket_id=ticket_id,
            new_state_id=call.state_id,
            user_id=user_id,
            sysconfig=sysconfig,
            pending_time=call.pending_time,
        )
        if closing:
            # AgentTicketPhoneCommon: "should i set an unlock? yes if the
            # ticket is closed".
            await unlock_ticket(session, ticket_id=ticket_id, user_id=user_id, sysconfig=sysconfig)
            locked = False

    return PhoneCallResult(
        ticket_id=ticket_id,
        article_id=article_id,
        time_accounting_id=time_accounting_id,
        locked=locked,
    )


async def log_phone_call(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    direction: str,  # "inbound" | "outbound"
    caller_number: str,
    note: str,
    ticket_id: int | None,
    user_id: int,
    subject: str | None = None,
) -> PhoneNoteResult:
    """CTI entry point: log a call note, resolving/creating the ticket from
    *caller_number* when no ``ticket_id`` is given."""
    _validate_direction(direction)

    created = False
    target_ticket_id = ticket_id
    if target_ticket_id is None:
        customer_no, customer_user_id = await resolve_customer_by_phone(session, caller_number)
        if customer_user_id is None:
            default_cu = await channel_setting(session, CHANNEL_NAME, "default_customer_user")
            customer_user_id = default_cu
            customer_no = default_cu
        title = (
            f"Phone call from {caller_number}"
            if direction == "inbound"
            else f"Phone call to {caller_number}"
        )
        target_ticket_id, created = await resolve_ticket_for_inbound(
            session,
            session_factory,
            sysconfig,
            channel=CHANNEL_NAME,
            body_text=note,
            customer_no=customer_no,
            customer_user_id=customer_user_id,
            title=title,
            user_id=user_id,
        )

    result = await log_phone_call_on_ticket(
        session,
        sysconfig,
        ticket_id=target_ticket_id,
        user_id=user_id,
        call=PhoneCallIn(
            direction="inbound" if direction == "inbound" else "outbound",
            subject=subject or f"Phone call ({direction})",
            body=note,
            caller_number=caller_number,
        ),
        enforce_permissions=False,
    )
    return PhoneNoteResult(
        ticket_id=target_ticket_id, article_id=result.article_id, created=created
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


async def send_phone_ticket_auto_response(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    mail_sender: Any | None,
    *,
    ticket_id: int,
    user_id: int,
    subject: str,
    body: str,
) -> int | None:
    """The queue's "auto reply" for a new inbound phone ticket (Znuny
    AgentTicketPhone with ``AutoResponseForWebTickets``). Caller commits.

    Same send path as the email pipeline -- template placeholders, loop
    protection, ``SendAutoReply`` history -- addressed to the ticket's
    customer user. Returns the auto-reply article id, or ``None`` when
    switched off, the customer has no address, nothing is configured for the
    queue, or no outbound relay is configured (``mail_sender`` unset).
    """
    from tiqora.channels.email.autoresponse import send_auto_response

    if not _truthy(await sysconfig.get("AutoResponseForWebTickets", 1)):
        return None
    ticket = await _ticket_must_exist(session, ticket_id)
    recipient = await customer_address(session, ticket.get("customer_user_id"))
    if recipient is None:
        return None
    sender = mail_sender
    if sender is None:
        from tiqora.domain.mail_outbound import build_outbound_sender, resolve_outbound_smtp

        if not (await resolve_outbound_smtp(session)).enabled:
            # Same policy as agent email replies without a relay: never
            # fall back to localhost:25 from a request.
            logger.info("phone_auto_response_no_outbound_relay", ticket_id=ticket_id)
            return None
        sender = await build_outbound_sender(
            session, sendmail_bcc=(await sysconfig.sendmail_bcc()) or None
        )
    return await send_auto_response(
        session,
        session_factory,
        sysconfig,
        sender,
        ticket_id=ticket_id,
        queue_id=int(ticket["queue_id"]),
        auto_response_type="auto reply",
        recipient_from_header=recipient,
        orig_subject=subject,
        orig_body=body,
        orig_message_id=None,
        orig_x_otrs_loop=None,
        user_id=user_id,
    )
