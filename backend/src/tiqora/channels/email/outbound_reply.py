"""Outbound agent email replies (TicketZoom compose / ArticleCreate channel=email).

Wires the existing :mod:`tiqora.channels.email.smtp` primitives into the agent
reply path. Shared prepare step (signature, From, Message-ID, threading):

1. Resolve From (queue system_address), signature, Message-ID, threading headers
2. If outbound mail is enabled (``Settings.smtp_enabled``): SMTP send first via
   injectable :class:`MailSender`, then store — on send failure store nothing
   and raise :class:`OutboundMailError` (HTTP 502). Matches Znuny AgentTicketCompose.
3. If outbound mail is **not** enabled (default): store the prepared article and
   log ``agent_email_not_dispatched`` — no SMTP attempt, no 502. Production often
   has no relay; losing the agent's typed text is worse than not sending.

The stored article content (incl. signature + threading headers) is identical
in both paths; only the send attempt is gated.
"""

from __future__ import annotations

import re
import secrets
import socket
from dataclasses import replace

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.email.placeholder import expand_placeholders
from tiqora.channels.email.smtp import MailSender, build_message
from tiqora.domain.ticket_write_service import ArticleIn, InvalidInput, add_article
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

_SIGNATURE_MARKERS = (
    "\n-- \n",
    "\n--\n",
    "\n<hr",
    '\n<div class="signature"',
)


class OutboundMailError(Exception):
    """SMTP delivery failed for an outgoing agent email reply."""


def generate_message_id(*, domain: str | None = None) -> str:
    """Return a bracketed Message-ID unique enough for Znuny follow-up MD5 lookup."""
    host = domain or socket.getfqdn() or "localhost"
    # Keep it ASCII and angle-bracketed — a_message_id_md5 digests the full form.
    return f"<tiqora.{secrets.token_hex(12)}@{host}>"


def _is_html(content_type: str) -> bool:
    return "html" in (content_type or "").lower()


def _signature_already_present(body: str, signature: str) -> bool:
    """True when the body already ends with the signature or a common sig marker."""
    body_s = (body or "").rstrip()
    sig_s = (signature or "").strip()
    if not sig_s:
        return True
    if sig_s in body_s:
        return True
    # Common "already signed" markers near the end of the body
    tail = body_s[-min(len(body_s), 400) :]
    return any(m.strip() in tail for m in _SIGNATURE_MARKERS if m.strip())


def append_signature(body: str, signature: str, *, content_type: str) -> str:
    """Append *signature* to *body* for the article content type (text vs html)."""
    if not (signature or "").strip():
        return body
    if _signature_already_present(body, signature):
        return body
    if _is_html(content_type):
        # Preserve plaintext signatures inside a preformatted block when the
        # reply body is HTML (queue signatures are often text/plain).
        if "<" not in signature:
            sig_html = (
                '<br />\n-- <br />\n<pre style="font-family: inherit; white-space: pre-wrap">'
                + _html_escape(signature.strip())
                + "</pre>"
            )
        else:
            sig_html = "<br />\n-- <br />\n" + signature.strip()
        return (body or "").rstrip() + "\n" + sig_html
    return (body or "").rstrip() + "\n\n-- \n" + signature.strip()


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


async def _queue_outbound_meta(
    session: AsyncSession, queue_id: int
) -> tuple[str, str, str | None, str | None]:
    """Return (from_line, queue_name, signature_text, signature_content_type)."""
    row = (
        await session.execute(
            text(
                "SELECT q.name AS queue_name, sa.value0 AS sa_addr, sa.value1 AS sa_name,"
                " s.text AS sig_text, s.content_type AS sig_ct"
                " FROM queue q"
                " LEFT JOIN system_address sa ON sa.id = q.system_address_id"
                " LEFT JOIN signature s ON s.id = q.signature_id AND s.valid_id = 1"
                " WHERE q.id = :qid LIMIT 1"
            ),
            {"qid": queue_id},
        )
    ).first()
    if row is None:
        raise InvalidInput(f"No queue with id={queue_id}")
    queue_name = str(row[0] or "")
    sa_addr = str(row[1] or "").strip()
    sa_name = str(row[2] or "").strip()
    sig_text = str(row[3]) if row[3] is not None else None
    sig_ct = str(row[4]) if row[4] is not None else None
    if sa_addr:
        from_line = f"{sa_name} <{sa_addr}>" if sa_name else sa_addr
    else:
        from_line = "Tiqora <noreply@localhost>"
    return from_line, queue_name, sig_text, sig_ct


async def _agent_names(session: AsyncSession, user_id: int) -> tuple[str, str]:
    row = (
        await session.execute(
            text("SELECT first_name, last_name FROM users WHERE id = :uid LIMIT 1"),
            {"uid": user_id},
        )
    ).first()
    if row is None:
        return "", ""
    return str(row[0] or ""), str(row[1] or "")


async def _ticket_vars(session: AsyncSession, ticket_id: int) -> dict[str, str]:
    row = (
        await session.execute(
            text(
                "SELECT t.tn, t.title, q.name AS queue_name, ts.name AS state_name,"
                " tp.name AS priority_name"
                " FROM ticket t"
                " JOIN queue q ON q.id = t.queue_id"
                " JOIN ticket_state ts ON ts.id = t.ticket_state_id"
                " JOIN ticket_priority tp ON tp.id = t.ticket_priority_id"
                " WHERE t.id = :tid LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if row is None:
        return {}
    return {
        "TicketNumber": str(row[0] or ""),
        "Title": str(row[1] or ""),
        "Queue": str(row[2] or ""),
        "State": str(row[3] or ""),
        "Priority": str(row[4] or ""),
    }


_AGENT_TAG_RE = re.compile(r"<OTRS_AGENT_(UserFirstname|UserLastname|UserFullname)>", re.IGNORECASE)


def _expand_agent_signature_tags(text: str, first_name: str, last_name: str) -> str:
    """Minimal Agent_* tags used by the default Znuny system signature."""

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        if key == "userfirstname":
            return first_name
        if key == "userlastname":
            return last_name
        if key == "userfullname":
            return f"{first_name} {last_name}".strip()
        return match.group(0)

    return _AGENT_TAG_RE.sub(_sub, text)


async def _latest_customer_message_id(session: AsyncSession, ticket_id: int) -> str | None:
    """Most recent customer-visible email Message-ID on the ticket (for In-Reply-To fallback)."""
    row = (
        await session.execute(
            text(
                "SELECT m.a_message_id FROM article a"
                " JOIN article_data_mime m ON m.article_id = a.id"
                " JOIN article_sender_type st ON st.id = a.article_sender_type_id"
                " JOIN communication_channel cc ON cc.id = a.communication_channel_id"
                " WHERE a.ticket_id = :tid AND st.name = 'customer'"
                " AND cc.name = 'Email' AND m.a_message_id IS NOT NULL"
                " AND m.a_message_id <> ''"
                " ORDER BY a.id DESC LIMIT 1"
            ),
            {"tid": ticket_id},
        )
    ).first()
    if row is None or not row[0]:
        return None
    mid = str(row[0]).strip()
    if mid and not mid.startswith("<"):
        mid = f"<{mid}>"
    return mid or None


async def prepare_outgoing_agent_email(
    session: AsyncSession,
    sysconfig: SysConfig,
    *,
    ticket_id: int,
    queue_id: int,
    user_id: int,
    article: ArticleIn,
) -> ArticleIn:
    """Return a copy of *article* ready to send and store (sig, From, Message-ID, visibility)."""
    from_line, queue_name, sig_text, _sig_ct = await _queue_outbound_meta(session, queue_id)
    first_name, last_name = await _agent_names(session, user_id)
    ticket_vars = await _ticket_vars(session, ticket_id)

    body = article.body or ""
    if sig_text and sig_text.strip():
        expanded = await expand_placeholders(
            session,
            sysconfig,
            sig_text,
            ticket=ticket_vars,
            queue_name=queue_name or ticket_vars.get("Queue", ""),
            customer_subject=article.subject or "",
            customer_email_lines=[],
        )
        expanded = _expand_agent_signature_tags(expanded, first_name, last_name)
        body = append_signature(body, expanded, content_type=article.content_type)

    message_id = article.message_id
    if not message_id or not str(message_id).strip():
        message_id = generate_message_id()
    elif not str(message_id).startswith("<"):
        message_id = f"<{str(message_id).strip('<>')}>"

    in_reply_to = article.in_reply_to
    references = article.references
    if not (in_reply_to or "").strip():
        in_reply_to = await _latest_customer_message_id(session, ticket_id)
    if not (references or "").strip() and in_reply_to:
        references = in_reply_to

    from_address = article.from_address or from_line
    # Agent → customer email replies must be customer-visible (AgentTicketZoom + portal).
    is_visible = True if article.channel.lower() == "email" else article.is_visible_for_customer

    if not (article.to_address or "").strip():
        raise InvalidInput("Agent email reply requires to_address")

    return replace(
        article,
        body=body,
        from_address=from_address,
        message_id=message_id,
        in_reply_to=in_reply_to,
        references=references,
        is_visible_for_customer=is_visible,
        # Ensure content_type carries charset for storage parity with Znuny
        content_type=article.content_type or "text/plain; charset=utf-8",
    )


async def send_prepared_agent_email(mail_sender: MailSender, article: ArticleIn) -> None:
    """SMTP-send a prepared agent email article. Raises :class:`OutboundMailError` on failure."""
    if not article.to_address:
        raise InvalidInput("Agent email reply requires to_address")
    message = build_message(
        from_addr=article.from_address or "Tiqora <noreply@localhost>",
        to_addrs=article.to_address,
        cc_addrs=article.cc,
        bcc_addrs=article.bcc,
        subject=article.subject,
        body=article.body,
        content_type=article.content_type or "text/plain; charset=utf-8",
        in_reply_to=article.in_reply_to,
        references=article.references or article.in_reply_to,
        reply_to=article.reply_to,
        message_id=article.message_id,
        loop_hint=False,  # human agent reply, not an automated bounce/auto-response
    )
    try:
        await mail_sender.send(message)
    except OutboundMailError:
        raise
    except Exception as exc:
        logger.exception(
            "agent_email_smtp_failed",
            to=article.to_address,
            subject=article.subject,
        )
        raise OutboundMailError(f"SMTP send failed: {exc}") from exc


async def deliver_agent_email_reply(
    session: AsyncSession,
    sysconfig: SysConfig,
    mail_sender: MailSender,
    *,
    ticket_id: int,
    queue_id: int,
    user_id: int,
    article: ArticleIn,
    dispatch: bool | None = None,
) -> int:
    """Prepare → optional SMTP send → store. Returns the new article id.

    When outbound mail is enabled (``Settings.smtp_enabled`` / ``dispatch=True``):
    **send-then-store** — a failed SMTP call leaves no article row so the agent
    can retry without a false "sent" customer-visible note.

    When outbound mail is **not** enabled (default): store the fully prepared
    article (signature + threading headers identical to the send path) and log
    ``agent_email_not_dispatched`` — never attempt SMTP, never 502. Production
    often has no relay; losing the agent's typed text is worse than not sending.
    """
    from tiqora.config import get_settings

    prepared = await prepare_outgoing_agent_email(
        session,
        sysconfig,
        ticket_id=ticket_id,
        queue_id=queue_id,
        user_id=user_id,
        article=article,
    )
    should_dispatch = get_settings().smtp_enabled if dispatch is None else dispatch
    if should_dispatch:
        await send_prepared_agent_email(mail_sender, prepared)
    else:
        logger.warning(
            "agent_email_not_dispatched",
            reason="smtp_disabled",
            ticket_id=ticket_id,
            to=prepared.to_address,
            subject=prepared.subject,
            message_id=prepared.message_id,
        )
    article_id = await add_article(
        session,
        ticket_id=ticket_id,
        article=prepared,
        user_id=user_id,
        sysconfig=sysconfig,
    )
    if should_dispatch:
        logger.info(
            "agent_email_reply_sent",
            ticket_id=ticket_id,
            article_id=article_id,
            to=prepared.to_address,
            message_id=prepared.message_id,
        )
    else:
        logger.info(
            "agent_email_reply_stored",
            ticket_id=ticket_id,
            article_id=article_id,
            to=prepared.to_address,
            message_id=prepared.message_id,
            dispatched=False,
        )
    return article_id


__all__ = [
    "OutboundMailError",
    "append_signature",
    "deliver_agent_email_reply",
    "generate_message_id",
    "prepare_outgoing_agent_email",
    "send_prepared_agent_email",
]
