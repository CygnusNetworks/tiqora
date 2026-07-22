"""Outbound agent email replies (TicketZoom compose / ArticleCreate channel=email).

Wires the existing :mod:`tiqora.channels.email.smtp` primitives into the agent
reply path. Shared prepare step (signature, From, Message-ID, threading):

1. Resolve From (queue system_address), signature, Message-ID, threading headers
2. If outbound mail is enabled (DB ``tiqora_mail_outbound`` when ``enabled``,
   else env ``Settings.smtp_enabled``): SMTP send first via injectable
   :class:`MailSender` (or a DB/env-resolved :class:`SmtpMailSender`), then
   store — on send failure store nothing and raise :class:`OutboundMailError`
   (HTTP 502). Matches Znuny AgentTicketCompose.
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
import time
from dataclasses import replace

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.email.placeholder import expand_placeholders
from tiqora.channels.email.smtp import MailSender, build_message
from tiqora.domain.mail_log import write_mail_log
from tiqora.domain.quoting import build_ticket_subject
from tiqora.domain.subject_hook import load_subject_config
from tiqora.domain.ticket_write_service import ArticleIn, InvalidInput, add_article
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

_SIGNATURE_MARKERS = (
    "\n-- \n",
    "\n--\n",
    "\n<hr",
    '\n<div class="signature"',
)

# Leading RFC-3676 sig delimiter: "--" or "-- " plus optional trailing whitespace.
_LEADING_SIG_DELIMITER_RE = re.compile(r"^-- ?\s*$")


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


def _strip_leading_signature_delimiter(signature: str) -> str:
    """Drop a leading ``--`` / ``-- `` delimiter line so only the canonical one remains.

    Stored Znuny signatures often begin with their own RFC-3676 delimiter; we
    always prepend ``\\n\\n-- \\n`` ourselves, so a second leading line would
    produce a double separator in the sent mail.
    """
    text = (signature or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    if lines and _LEADING_SIG_DELIMITER_RE.match(lines[0]):
        return "\n".join(lines[1:]).strip()
    return text


def append_signature(body: str, signature: str, *, content_type: str) -> str:
    """Append *signature* to *body* for the article content type (text vs html)."""
    sig = _strip_leading_signature_delimiter(signature)
    if not sig:
        return body
    if _signature_already_present(body, sig):
        return body
    if _is_html(content_type):
        # Preserve plaintext signatures inside a preformatted block when the
        # reply body is HTML (queue signatures are often text/plain).
        if "<" not in sig:
            sig_html = (
                '<br />\n-- <br />\n<pre style="font-family: inherit; white-space: pre-wrap">'
                + _html_escape(sig)
                + "</pre>"
            )
        else:
            sig_html = "<br />\n-- <br />\n" + sig
        return (body or "").rstrip() + "\n" + sig_html
    return (body or "").rstrip() + "\n\n-- \n" + sig


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

    body = article.body or ""
    if sig_text and sig_text.strip():
        # Expand against full ticket context (customer_user, agents, queue) so
        # signature tags like OTRS_AGENT_* / OTRS_CUSTOMER_DATA_* / OTRS_TICKET_*
        # resolve the same way as Answer templates.
        expanded = await expand_placeholders(
            session,
            sysconfig,
            sig_text,
            ticket_id=ticket_id,
            user_id=user_id,
            queue_name=queue_name or "",
            customer_subject=article.subject or "",
            customer_email_lines=[],
        )
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

    # Ensure exactly one correct ticket-number hook tag (Znuny TicketSubjectBuild).
    # Agent's subject already has Re:; strip-then-add keeps this idempotent.
    subject = article.subject
    hook_cfg = await load_subject_config(session, sysconfig)
    if hook_cfg.enabled:
        tn_row = (
            await session.execute(
                text("SELECT tn FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        ).first()
        if tn_row is not None and tn_row[0]:
            subject = build_ticket_subject(
                article.subject,
                hook=hook_cfg.hook,
                divider=hook_cfg.divider,
                tn=str(tn_row[0]),
                subject_format=hook_cfg.subject_format,
                add_re=False,
                add_fwd=False,
            )

    return replace(
        article,
        body=body,
        subject=subject,
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
    mail_sender: MailSender | None,
    *,
    ticket_id: int,
    queue_id: int,
    user_id: int,
    article: ArticleIn,
    dispatch: bool | None = None,
) -> int:
    """Prepare → optional SMTP send → store. Returns the new article id.

    When outbound mail is enabled (DB ``tiqora_mail_outbound.enabled`` first,
    else ``Settings.smtp_enabled`` / ``dispatch=True``): **send-then-store** —
    a failed SMTP call leaves no article row so the agent can retry without a
    false "sent" customer-visible note.

    When outbound mail is **not** enabled (default): store the fully prepared
    article (signature + threading headers identical to the send path) and log
    ``agent_email_not_dispatched`` — never attempt SMTP, never 502. Production
    often has no relay; losing the agent's typed text is worse than not sending.
    """
    from tiqora.channels.email.smtp import SmtpMailSender
    from tiqora.domain.mail_outbound import resolve_outbound_smtp

    prepared = await prepare_outgoing_agent_email(
        session,
        sysconfig,
        ticket_id=ticket_id,
        queue_id=queue_id,
        user_id=user_id,
        article=article,
    )
    resolved = await resolve_outbound_smtp(session)
    should_dispatch = resolved.enabled if dispatch is None else dispatch
    queue_name: str | None = None
    try:
        _from, queue_name, _sig, _sig_ct = await _queue_outbound_meta(session, queue_id)
    except Exception:
        queue_name = None

    if should_dispatch:
        sender: MailSender
        if mail_sender is not None:
            sender = mail_sender
        elif resolved.enabled:
            sender = SmtpMailSender.from_resolved(resolved)
        else:
            # Explicit dispatch=True without resolved config: env-shaped default.
            from tiqora.config import get_settings

            sender = SmtpMailSender(get_settings())
        t0 = time.perf_counter()
        try:
            await send_prepared_agent_email(sender, prepared)
        except OutboundMailError as exc:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            # Log BEFORE re-raising so the failure is captured even when the
            # outer request transaction rolls back (independent session commit).
            await write_mail_log(
                session,
                direction="out",
                status="failed",
                from_addr=prepared.from_address or "",
                to_addr=prepared.to_address or "",
                cc_addr=prepared.cc,
                subject=prepared.subject or "",
                message_id=prepared.message_id,
                ticket_id=ticket_id,
                article_id=None,
                queue=queue_name,
                smtp_code=None,
                detail=str(exc),
                duration_ms=duration_ms,
            )
            raise
        duration_ms = int((time.perf_counter() - t0) * 1000)
        smtp_code = getattr(sender, "last_smtp_code", None)
        smtp_detail = getattr(sender, "last_smtp_detail", None)
        article_id = await add_article(
            session,
            ticket_id=ticket_id,
            article=prepared,
            user_id=user_id,
            sysconfig=sysconfig,
        )
        await write_mail_log(
            session,
            direction="out",
            status="sent",
            from_addr=prepared.from_address or "",
            to_addr=prepared.to_address or "",
            cc_addr=prepared.cc,
            subject=prepared.subject or "",
            message_id=prepared.message_id,
            ticket_id=ticket_id,
            article_id=article_id,
            queue=queue_name,
            smtp_code=smtp_code if isinstance(smtp_code, int) else None,
            detail=str(smtp_detail) if smtp_detail else None,
            duration_ms=duration_ms,
        )
        logger.info(
            "agent_email_reply_sent",
            ticket_id=ticket_id,
            article_id=article_id,
            to=prepared.to_address,
            message_id=prepared.message_id,
            source=resolved.source,
        )
        return article_id

    logger.warning(
        "agent_email_not_dispatched",
        reason="smtp_disabled",
        source=resolved.source,
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
    # SMTP disabled: still record a queued row so the admin log shows the
    # prepared outbound message (no SMTP attempt, no 502).
    await write_mail_log(
        session,
        direction="out",
        status="queued",
        from_addr=prepared.from_address or "",
        to_addr=prepared.to_address or "",
        cc_addr=prepared.cc,
        subject=prepared.subject or "",
        message_id=prepared.message_id,
        ticket_id=ticket_id,
        article_id=article_id,
        queue=queue_name,
        detail="smtp_disabled",
        duration_ms=None,
    )
    logger.info(
        "agent_email_reply_stored",
        ticket_id=ticket_id,
        article_id=article_id,
        to=prepared.to_address,
        message_id=prepared.message_id,
        dispatched=False,
    )
    return article_id


# Public alias so callers outside this module (e.g. the reference compose-context
# endpoint, which needs the same From/signature resolution for a preview) don't
# have to reach into a leading-underscore name.
queue_outbound_meta = _queue_outbound_meta

__all__ = [
    "OutboundMailError",
    "append_signature",
    "deliver_agent_email_reply",
    "generate_message_id",
    "prepare_outgoing_agent_email",
    "queue_outbound_meta",
    "send_prepared_agent_email",
]
