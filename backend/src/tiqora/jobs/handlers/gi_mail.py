"""GenericInterface mails as background jobs (kind ``gi_mail``).

``TicketCreate`` / ``TicketUpdate`` (``tiqora.api.compat.operations``) store every article in
the request transaction and queue one job per mail in the same transaction, so a job exists
exactly when its ticket and article were committed. The request answers at once; the worker
sends the mail - Znuny does the same with its ``MailQueue`` (``Email.pm`` ``ArticleSend``
queues, ``Maint::Email::MailQueue`` sends).

The worker never receives a prepared mail object: it rebuilds the mail from what the request
stored (the article columns and, for a signed/encrypted mail, the secured bytes).

* ``article_send``: the stored ``ArticleSend`` article. A failure is recorded like Znuny's
  ``MailQueue::_SendError`` for a permanent error: the article's transmission error
  (``article_data_mime_send_error``) plus a ``failed`` row in ``tiqora_mail_log``; the job
  fails. There is one attempt (Znuny retries temporary 4xx errors up to
  ``MailQueue###ItemMaxAttempts``).
* ``auto_response``: the queue's auto reply / auto follow up for a request article
  (``ArticleCreate`` -> ``SendAutoResponse``). Znuny ignores its result; a failure only fails
  the job (``send_auto_response`` writes its own mail log row).
"""

from __future__ import annotations

from typing import Any, Literal

import structlog
from pydantic import BaseModel
from sqlalchemy import text

from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler

logger = structlog.get_logger(__name__)

GI_MAIL_KIND = "gi_mail"


class GiMailPayload(BaseModel):
    kind: Literal["article_send", "auto_response"]
    ticket_id: int
    article_id: int
    user_id: int  # the user the request wrote as (history / auto response article)
    auto_response_type: str = ""


async def _article_send(ctx: JobContext, payload: GiMailPayload) -> dict[str, Any]:
    from tiqora.channels.email.outbound_reply import (
        dispatch_pending_agent_email,
        load_pending_agent_email,
    )
    from tiqora.znuny.sysconfig import SysConfig

    async with ctx.session_factory() as session:
        sysconfig = SysConfig(session)
        pending = await load_pending_agent_email(session, sysconfig, article_id=payload.article_id)
        if pending is None:
            raise JobFailed("Der Artikel zu dieser E-Mail existiert nicht mehr.")
        try:
            await dispatch_pending_agent_email(session, sysconfig, pending)
        except Exception as exc:
            # the transmission error row is in this session: keep it
            await session.commit()
            logger.error(
                "gi_mail_article_send_failed",
                ticket_id=payload.ticket_id,
                article_id=payload.article_id,
                error=str(exc),
            )
            raise JobFailed(
                "Die E-Mail konnte nicht gesendet werden. Details stehen im Mail-Protokoll."
            ) from exc
        await session.commit()
    return {"article_id": payload.article_id}


async def _auto_response(ctx: JobContext, payload: GiMailPayload) -> dict[str, Any]:
    from tiqora.api.compat.article_input import send_gi_auto_response
    from tiqora.domain.quoting import html_to_plaintext
    from tiqora.znuny.sysconfig import SysConfig

    async with ctx.session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT t.queue_id, m.a_from, m.a_subject, m.a_body, m.a_content_type"
                    " FROM article a JOIN article_data_mime m ON m.article_id = a.id"
                    " JOIN ticket t ON t.id = a.ticket_id"
                    " WHERE a.id = :aid AND a.ticket_id = :tid"
                ),
                {"aid": payload.article_id, "tid": payload.ticket_id},
            )
        ).first()
        if row is None:
            raise JobFailed("Der Artikel zu dieser Antwort existiert nicht mehr.")
        queue_id, from_header, subject, body, content_type = row
        plain_body = str(body or "")
        if "html" in str(content_type or "").lower():
            plain_body = html_to_plaintext(plain_body)
        try:
            sent_id = await send_gi_auto_response(
                session,
                ctx.session_factory,
                SysConfig(session),
                ticket_id=payload.ticket_id,
                queue_id=int(queue_id),
                auto_response_type=payload.auto_response_type,
                from_header=str(from_header or ""),
                subject=str(subject or ""),
                body=plain_body,
                user_id=payload.user_id,
            )
            await session.commit()
        except Exception as exc:
            # nothing of a half-sent auto response is kept (no article, no loop protection)
            await session.rollback()
            logger.warning(
                "gi_mail_auto_response_failed",
                ticket_id=payload.ticket_id,
                type=payload.auto_response_type,
                error=str(exc),
            )
            raise JobFailed("Die automatische Antwort konnte nicht gesendet werden.") from exc
    return {"auto_response_article_id": sent_id}


async def run_gi_mail(ctx: JobContext, payload: GiMailPayload) -> dict[str, Any]:
    if payload.kind == "article_send":
        return await _article_send(ctx, payload)
    return await _auto_response(ctx, payload)


GI_MAIL_JOB = JobHandler(
    kind=GI_MAIL_KIND,
    payload_model=GiMailPayload,
    run=run_gi_mail,
    title_key="jobs.kinds.gi_mail",
    timeout_seconds=300,
)
