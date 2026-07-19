"""Auto-response sending — port of ``TemplateGenerator::AutoResponse`` +
``Ticket::Article::Backend::Email::SendAutoResponse``.

Dispatch entry point is :func:`send_auto_response`, called from the pipeline
after a new ticket / follow-up has been created. Behaviour ported:

- ``queue_auto_response`` → ``auto_response`` lookup by (queue_id, type name);
  ``auto_response_type`` ids from ``initial_insert.xml``: 1=auto reply,
  2=auto reject, 3=auto follow up, 4=auto reply/new ticket, 5=auto remove.
- No auto-response for ``auto reply`` on a closed/removed ticket (Misc history
  row, matching Znuny's early-return).
- ``X-OTRS-Loop`` on the *inbound* mail suppresses the response (with a Misc
  history row) — this is Znuny's own anti-loop convention, independent of the
  ``ticket_loop_protection`` day-counter.
- Per-recipient ``ticket_loop_protection`` check + record (reuses
  ``channels.email.loop_protection``).
- Writes the outgoing article via ``domain.ticket_write_service.add_article``
  (sender_type="system", visible to customer) with the exact
  SendAutoReply/SendAutoFollowUp/SendAutoReject/SendAutoReply history type
  Znuny uses (not the generic Email* channel/sender-derived name).
"""

from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.email import loop_protection
from tiqora.channels.email.parser import get_email_address, split_address_line
from tiqora.channels.email.placeholder import expand_placeholders
from tiqora.channels.email.smtp import MailSender, build_message
from tiqora.domain.ticket_write_service import ArticleIn, add_article
from tiqora.znuny.history import history_add
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

_HISTORY_TYPE_BY_RESPONSE_TYPE = {
    "auto follow up": "SendAutoFollowUp",
    "auto reply": "SendAutoReply",
    "auto reply/new ticket": "SendAutoReply",
    "auto reject": "SendAutoReject",
    "auto remove": "SendAutoReply",
}


async def send_auto_response(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    sysconfig: SysConfig,
    mail_sender: MailSender,
    *,
    ticket_id: int,
    queue_id: int,
    auto_response_type: str,
    recipient_from_header: str,
    orig_subject: str,
    orig_body: str,
    orig_message_id: str | None,
    orig_x_otrs_loop: str | None,
    user_id: int,
) -> int | None:
    """Send the configured auto-response for *auto_response_type*; return article_id or None."""
    ticket_row = (
        await session.execute(
            text(
                "SELECT t.tn, t.title, q.name AS queue_name, ts.name AS state_name,"
                " tp.name AS priority_name, tst.name AS state_type, t.customer_user_id"
                " FROM ticket t"
                " JOIN queue q ON q.id = t.queue_id"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_priority tp ON tp.id = t.ticket_priority_id"
                " JOIN ticket_state_type tst ON tst.id = ts.type_id"
                " WHERE t.id = :tid"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if ticket_row is None:
        logger.warning("autoresponse_ticket_missing", ticket_id=ticket_id)
        return None
    tn, title, queue_name, state_name, priority_name, state_type, customer_user_id = ticket_row

    if auto_response_type == "auto reply" and str(state_type).lower() in ("closed", "removed"):
        await history_add(
            session,
            ticket_id=ticket_id,
            history_type="Misc",
            name=f"Sent no auto response because ticket is state-type '{state_type}'!",
            user_id=user_id,
        )
        return None

    if orig_x_otrs_loop and orig_x_otrs_loop.strip().lower() not in ("false", "no"):
        await history_add(
            session,
            ticket_id=ticket_id,
            history_type="Misc",
            name="Sent no auto-response because the sender doesn't want an auto-response",
            user_id=user_id,
        )
        return None

    auto_response_row = (
        await session.execute(
            text(
                "SELECT ar.text0, ar.text1, ar.content_type, sa.value0, sa.value1"
                " FROM queue_auto_response qar"
                " JOIN auto_response ar ON ar.id = qar.auto_response_id"
                " JOIN auto_response_type art ON art.id = ar.type_id"
                " JOIN system_address sa ON sa.id = ar.system_address_id"
                " WHERE qar.queue_id = :qid AND art.name = :t LIMIT 1"
            ),
            {"qid": queue_id, "t": auto_response_type},
        )
    ).first()
    if auto_response_row is None:
        logger.info("autoresponse_not_configured", queue_id=queue_id, type=auto_response_type)
        return None
    subject_tpl, body_tpl, content_type, sender_address, sender_realname = auto_response_row

    recipients = [
        get_email_address(a)
        for a in split_address_line(recipient_from_header)
        if get_email_address(a)
    ]
    if not recipients:
        return None

    max_emails = await sysconfig.postmaster_max_emails()
    max_per_address = await sysconfig.postmaster_max_emails_per_address()

    allowed: list[str] = []
    for addr in recipients:
        if not await loop_protection.check(
            session, to=addr, max_emails=max_emails, max_emails_per_address=max_per_address
        ):
            await history_add(
                session,
                ticket_id=ticket_id,
                history_type="LoopProtection",
                name=f"%%{addr}",
                user_id=user_id,
            )
            logger.warning("autoresponse_loop_protected", ticket_id=ticket_id, to=addr)
            continue
        allowed.append(addr)

    if not allowed:
        return None

    ticket_vars = {
        "TicketNumber": str(tn),
        "Title": str(title or ""),
        "Queue": str(queue_name),
        "State": str(state_name),
        "Priority": str(priority_name),
    }
    subject = await expand_placeholders(
        session,
        sysconfig,
        subject_tpl or "",
        ticket=ticket_vars,
        queue_name=str(queue_name),
        customer_subject=orig_subject,
        customer_email_lines=orig_body.splitlines(),
    )
    body = await expand_placeholders(
        session,
        sysconfig,
        body_tpl or "",
        ticket=ticket_vars,
        queue_name=str(queue_name),
        customer_subject=orig_subject,
        customer_email_lines=orig_body.splitlines(),
    )

    to_line = ", ".join(allowed)
    from_line = f"{sender_realname} <{sender_address}>" if sender_realname else str(sender_address)

    message = build_message(
        from_addr=from_line,
        to_addrs=to_line,
        cc_addrs=None,
        subject=subject,
        body=body,
        content_type=content_type or "text/plain",
        in_reply_to=orig_message_id,
    )
    await mail_sender.send(message)

    for addr in allowed:
        await loop_protection.record(session, to=addr)

    history_type = _HISTORY_TYPE_BY_RESPONSE_TYPE.get(auto_response_type, "Misc")
    article = ArticleIn(
        sender_type="system",
        is_visible_for_customer=True,
        subject=subject,
        body=body,
        content_type=content_type or "text/plain; charset=utf-8",
        from_address=from_line,
        to_address=to_line,
        message_id=None,
        in_reply_to=orig_message_id,
        channel="email",
        history_type_override=history_type,
    )
    article_id = await add_article(
        session, ticket_id=ticket_id, article=article, user_id=user_id, sysconfig=sysconfig
    )
    logger.info(
        "autoresponse_sent",
        ticket_id=ticket_id,
        type=auto_response_type,
        to=to_line,
        article_id=article_id,
    )
    return article_id
