"""Postmaster dispatch pipeline — port of ``Kernel::System::PostMaster::Run``.

Wires together: filter application, X-OTRS pseudo-header handling (only
honoured for trusted mail accounts), follow-up detection, loop protection, and
ticket create / follow-up-append / follow-up-reject / new-ticket-on-closed
dispatch. Auto-response sending (Sub-task 3) is invoked from here but
implemented in :mod:`tiqora.channels.email.autoresponse`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.email.filters import apply_filters
from tiqora.channels.email.parser import ParsedEmail, get_email_address, parse_email
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.domain.ticket_write_service import ArticleIn, TicketIn, add_article, create_ticket
from tiqora.znuny.followup import detect_followup
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

# Headers Znuny always scans (PostmasterX-Header default list, trimmed to the
# subset Tiqora acts on) plus the full X-OTRS-* namespace when trusted.
_X_OTRS_PREFIX = "x-otrs-"


@dataclass
class PipelineResult:
    outcome: str  # new_ticket|follow_up|follow_up_reject|follow_up_new_ticket|ignored|error
    ticket_id: int | None = None
    tn: str | None = None
    article_id: int | None = None
    auto_response_type: str | None = None
    queue_id: int | None = None
    recipient: str = ""
    error: str | None = None
    dynamic_fields_skipped: list[str] = field(default_factory=list)


async def _lookup_id(session: AsyncSession, table: str, name_col: str, value: str) -> int | None:
    row = (
        await session.execute(
            text(f"SELECT id FROM {table} WHERE {name_col} = :v LIMIT 1"), {"v": value}
        )
    ).first()
    return int(row[0]) if row is not None else None


async def _dest_queue_id(
    session: AsyncSession, get_param: dict[str, str], sysconfig: SysConfig
) -> int:
    """Port of ``DestQueue::GetQueueID`` — match recipient headers to system_address."""
    recipient = ", ".join(
        get_param[k]
        for k in ("Resent-To", "Envelope-To", "To", "Cc", "Delivered-To", "X-Original-To")
        if get_param.get(k)
    )
    for addr_part in recipient.split(","):
        addr = get_email_address(addr_part.strip())
        if not addr:
            continue
        row = (
            await session.execute(
                text(
                    "SELECT queue_id FROM system_address WHERE valid_id = 1"
                    " AND (LOWER(value0) = LOWER(:a) OR LOWER(value1) = LOWER(:a))"
                    " LIMIT 1"
                ),
                {"a": addr},
            )
        ).first()
        if row is not None:
            return int(row[0])

    default_queue = await sysconfig.postmaster_default_queue()
    qid = await _lookup_id(session, "queue", "name", default_queue)
    return qid or 1


async def _trusted_queue_id(session: AsyncSession, get_param: dict[str, str]) -> int | None:
    queue_name = get_param.get("X-OTRS-Queue")
    if not queue_name:
        return None
    return await _lookup_id(session, "queue", "name", queue_name)


def _build_get_param(parsed: ParsedEmail, *, trusted: bool) -> dict[str, str]:
    get_param: dict[str, str] = {
        "From": parsed.from_header,
        "To": parsed.to_header,
        "Cc": parsed.cc_header,
        "Subject": parsed.subject,
        "Body": parsed.body,
        "Message-ID": f"<{parsed.message_id}>" if parsed.message_id else "",
        "In-Reply-To": parsed.in_reply_to or "",
        "References": " ".join(f"<{r}>" for r in parsed.references),
        "SenderEmailAddress": parsed.from_address,
    }
    for key, value in parsed.headers.items():
        # Restore Header-Case for common headers; keep raw lowercase key too.
        pretty = "-".join(p.capitalize() for p in key.split("-"))
        if key.startswith(_X_OTRS_PREFIX) and not trusted:
            continue
        get_param.setdefault(pretty, value)
        get_param.setdefault(key, value)
    return get_param


async def _resolve_customer(
    session: AsyncSession, get_param: dict[str, str]
) -> tuple[str | None, str | None]:
    """Best-effort port of NewTicket.pm's CustomerID/CustomerUser resolution.

    Simplified: matches ``customer_user.login`` or ``.email`` against the
    sender address (Znuny's ``CustomerSearch(PostMasterSearch => ...)`` scans
    several configurable fields — documented as a Phase 4a simplification).
    """
    customer_no = get_param.get("X-OTRS-CustomerNo") or None
    customer_user = get_param.get("X-OTRS-CustomerUser") or None
    sender = get_param.get("SenderEmailAddress", "")

    if not customer_user and sender:
        row = (
            await session.execute(
                text(
                    "SELECT login, customer_id FROM customer_user"
                    " WHERE LOWER(login) = LOWER(:s) OR LOWER(email) = LOWER(:s) LIMIT 1"
                ),
                {"s": sender},
            )
        ).first()
        if row is not None:
            customer_user = customer_user or str(row[0])
            customer_no = customer_no or (str(row[1]) if row[1] else None)

    if not customer_no and sender:
        customer_no = sender
    if not customer_user:
        customer_user = sender

    return customer_no, customer_user


async def _apply_x_otrs_ticket_fields(
    session: AsyncSession, get_param: dict[str, str], sysconfig: SysConfig
) -> dict[str, Any]:
    """Resolve X-OTRS-{State,Priority,Type,Service,SLA,Owner,Responsible} → ids."""
    fields: dict[str, Any] = {}

    state_name = get_param.get("X-OTRS-State") or await sysconfig.postmaster_default_state()
    fields["state_id"] = await _lookup_id(session, "ticket_state", "name", state_name) or (
        await _lookup_id(
            session, "ticket_state", "name", await sysconfig.postmaster_default_state()
        )
    )

    prio_name = get_param.get("X-OTRS-Priority") or await sysconfig.postmaster_default_priority()
    fields["priority_id"] = await _lookup_id(session, "ticket_priority", "name", prio_name) or (
        await _lookup_id(
            session, "ticket_priority", "name", await sysconfig.postmaster_default_priority()
        )
    )

    if get_param.get("X-OTRS-Type"):
        fields["type_id"] = await _lookup_id(
            session, "ticket_type", "name", get_param["X-OTRS-Type"]
        )

    if get_param.get("X-OTRS-Owner"):
        row = (
            await session.execute(
                text("SELECT id FROM users WHERE login = :l LIMIT 1"),
                {"l": get_param["X-OTRS-Owner"]},
            )
        ).first()
        if row is not None:
            fields["owner_id"] = int(row[0])
    if "owner_id" not in fields and get_param.get("X-OTRS-OwnerID"):
        fields["owner_id"] = int(get_param["X-OTRS-OwnerID"])

    if get_param.get("X-OTRS-Responsible"):
        row = (
            await session.execute(
                text("SELECT id FROM users WHERE login = :l LIMIT 1"),
                {"l": get_param["X-OTRS-Responsible"]},
            )
        ).first()
        if row is not None:
            fields["responsible_id"] = int(row[0])
    elif get_param.get("X-OTRS-ResponsibleID"):
        fields["responsible_id"] = int(get_param["X-OTRS-ResponsibleID"])

    return fields


async def process_message(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    *,
    raw: bytes,
    account: MailAccount,
    user_id: int,
) -> PipelineResult:
    """Process one raw email against *account* within the caller's transaction."""
    parsed = parse_email(raw)
    get_param = _build_get_param(parsed, trusted=bool(account.trusted))

    await apply_filters(session, get_param)

    ignore = (get_param.get("X-OTRS-Ignore") or "").strip().lower()
    if ignore in ("yes", "true"):
        logger.info("postmaster_ignored", account_id=account.id, message_id=parsed.message_id)
        return PipelineResult(outcome="ignored")

    followup = await detect_followup(
        session,
        sysconfig,
        subject=get_param.get("Subject", ""),
        references=parsed.references,
    )

    is_visible = True
    if get_param.get("X-OTRS-IsVisibleForCustomer") not in (None, ""):
        is_visible = get_param["X-OTRS-IsVisibleForCustomer"] not in ("0", "false", "no")

    sender_type = "customer"
    if account.trusted and get_param.get("X-OTRS-SenderType"):
        sender_type = get_param["X-OTRS-SenderType"]

    if followup is not None:
        tn, ticket_id = followup
        ticket_row = (
            await session.execute(
                text(
                    "SELECT t.queue_id, tst.name AS state_type, t.ticket_state_id"
                    " FROM ticket t"
                    " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                    " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                    " WHERE t.id = :tid"
                ),
                {"tid": ticket_id},
            )
        ).first()
        if ticket_row is None:
            return PipelineResult(outcome="error", error=f"follow-up ticket {ticket_id} vanished")
        queue_id, state_type, _state_id = ticket_row
        state_type = str(state_type).lower()

        follow_up_row = (
            await session.execute(
                text(
                    "SELECT fup.name FROM queue q"
                    " JOIN follow_up_possible fup ON fup.id = q.follow_up_id"
                    " WHERE q.id = :qid"
                ),
                {"qid": queue_id},
            )
        ).first()
        follow_up_option = str(follow_up_row[0]).lower() if follow_up_row else "possible"
        is_closed = state_type in ("removed", "closed")
        bounce_as_followup = await sysconfig.postmaster_bounce_as_followup()

        if not bounce_as_followup and is_closed and follow_up_option == "new ticket":
            article = ArticleIn(
                sender_type=sender_type,
                is_visible_for_customer=is_visible,
                subject=get_param.get("Subject", ""),
                body=parsed.body,
                content_type=parsed.content_type,
                from_address=parsed.from_header,
                to_address=get_param.get("To"),
                cc=get_param.get("Cc") or None,
                message_id=parsed.message_id,
                in_reply_to=parsed.in_reply_to,
                references=get_param.get("References") or None,
                channel="email",
                attachments=[(a.filename, a.content_type, a.content) for a in parsed.attachments],
            )
            new_queue_id = await _trusted_queue_id(session, get_param) or await _dest_queue_id(
                session, get_param, sysconfig
            )
            customer_no, customer_user = await _resolve_customer(session, get_param)
            x_fields = await _apply_x_otrs_ticket_fields(session, get_param, sysconfig)
            params = TicketIn(
                title=get_param.get("X-OTRS-Title") or get_param.get("Subject", ""),
                queue_id=new_queue_id,
                state_id=x_fields["state_id"],
                priority_id=x_fields["priority_id"],
                owner_id=x_fields.get("owner_id", user_id),
                responsible_id=x_fields.get("responsible_id"),
                type_id=x_fields.get("type_id"),
                customer_id=customer_no,
                customer_user_id=customer_user,
                article=article,
            )
            new_ticket_id = await create_ticket(
                session, session_factory, sysconfig, params=params, user_id=user_id
            )
            return PipelineResult(
                outcome="follow_up_new_ticket",
                ticket_id=new_ticket_id,
                queue_id=new_queue_id,
                auto_response_type="auto reply/new ticket",
                recipient=parsed.from_address,
            )

        if not bounce_as_followup and is_closed and follow_up_option == "reject":
            logger.info("postmaster_followup_rejected", ticket_id=ticket_id, tn=tn)
            return PipelineResult(
                outcome="follow_up_reject",
                ticket_id=ticket_id,
                tn=tn,
                auto_response_type="auto reject",
                recipient=parsed.from_address,
            )

        # Normal follow-up: append article, reopen if closed.
        if is_closed:
            followup_state_name = await sysconfig.postmaster_followup_state_closed()
        else:
            followup_state_name = None
        if get_param.get("X-OTRS-FollowUp-State"):
            followup_state_name = get_param["X-OTRS-FollowUp-State"]
        elif not is_closed and state_type == "new":
            followup_state_name = await sysconfig.postmaster_followup_state()

        if followup_state_name:
            from tiqora.domain.ticket_write_service import change_state

            new_state_id = await _lookup_id(session, "ticket_state", "name", followup_state_name)
            if new_state_id:
                await change_state(
                    session,
                    ticket_id=ticket_id,
                    new_state_id=new_state_id,
                    user_id=user_id,
                    sysconfig=sysconfig,
                )

        article = ArticleIn(
            sender_type=sender_type,
            is_visible_for_customer=is_visible,
            subject=get_param.get("Subject", ""),
            body=parsed.body,
            content_type=parsed.content_type,
            from_address=parsed.from_header,
            to_address=get_param.get("To"),
            cc=get_param.get("Cc") or None,
            message_id=parsed.message_id,
            in_reply_to=parsed.in_reply_to,
            references=get_param.get("References") or None,
            channel="email",
            attachments=[(a.filename, a.content_type, a.content) for a in parsed.attachments],
        )
        article_id = await add_article(
            session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
        )
        return PipelineResult(
            outcome="follow_up",
            ticket_id=ticket_id,
            tn=tn,
            article_id=article_id,
            auto_response_type="auto follow up",
            queue_id=queue_id,
            recipient=parsed.from_address,
        )

    # New ticket.
    queue_id = await _trusted_queue_id(session, get_param) or await _dest_queue_id(
        session, get_param, sysconfig
    )
    customer_no, customer_user = await _resolve_customer(session, get_param)
    x_fields = await _apply_x_otrs_ticket_fields(session, get_param, sysconfig)
    article = ArticleIn(
        sender_type=sender_type,
        is_visible_for_customer=is_visible,
        subject=get_param.get("Subject", ""),
        body=parsed.body,
        content_type=parsed.content_type,
        from_address=parsed.from_header,
        to_address=get_param.get("To"),
        cc=get_param.get("Cc") or None,
        message_id=parsed.message_id,
        in_reply_to=parsed.in_reply_to,
        references=get_param.get("References") or None,
        channel="email",
        attachments=[(a.filename, a.content_type, a.content) for a in parsed.attachments],
    )
    params = TicketIn(
        title=get_param.get("X-OTRS-Title") or get_param.get("Subject", ""),
        queue_id=queue_id,
        state_id=x_fields["state_id"],
        priority_id=x_fields["priority_id"],
        owner_id=x_fields.get("owner_id", user_id),
        responsible_id=x_fields.get("responsible_id"),
        type_id=x_fields.get("type_id"),
        customer_id=customer_no,
        customer_user_id=customer_user,
        article=article,
    )
    ticket_id = await create_ticket(
        session, session_factory, sysconfig, params=params, user_id=user_id
    )
    return PipelineResult(
        outcome="new_ticket",
        ticket_id=ticket_id,
        queue_id=queue_id,
        auto_response_type="auto reply",
        recipient=parsed.from_address,
    )
