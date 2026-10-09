"""GenericInterface mails as background jobs (kind ``gi_mail``).

``TicketCreate`` / ``TicketUpdate`` (``tiqora.api.compat.operations``) store every article in
the request transaction and queue one job per mail in the same transaction, so a job exists
exactly when its ticket and article were committed. The request answers at once; the worker
sends the mail - Znuny does the same with its ``MailQueue`` (``Email.pm`` ``ArticleSend``
queues, ``Maint::Email::MailQueue`` sends).

The worker never receives a prepared mail object: it rebuilds the mail from what the request
stored (the article columns and, for a signed/encrypted mail, the secured bytes).

Delivery follows ``Kernel/System/MailQueue.pm`` with its default settings:

* Lane ``mail`` with one slot: the mails go out one after the other in job id order (Znuny's
  single ``MailQueue`` daemon task), never blocked by long jobs such as ticket exports. A
  mail waiting for its retry does not hold back the mails behind it (Znuny skips items whose
  ``DueTime`` lies ahead too).
* At most :data:`MAX_ATTEMPTS` attempts (``MailQueue###ItemMaxAttempts`` = 3). A temporary
  error (4xx reply, connection error, timeout, no SMTP reply at all) is tried again after
  ``attempt * 2`` minutes (``IncrementAttemptDelayInMinutes`` = 2); a 5xx reply or the last
  attempt is final (``_SendError``). Tiqora uses Znuny's defaults; it does not read the
  ``MailQueue`` SysConfig hash.
* ``article_send``: a final failure is recorded like ``MailQueue::_SendError``: the article's
  transmission error (``article_data_mime_send_error``) plus a ``failed`` row in
  ``tiqora_mail_log``; the job fails. Every temporary failure writes a ``failed`` log row
  whose detail says that the mail will be tried again.
* ``auto_response``: the queue's auto reply / auto follow up for a request article
  (``ArticleCreate`` -> ``SendAutoResponse``). Queue and type are decided when the article
  is stored (payload), like Znuny. The auto response article is only stored after a
  successful send, so a failure leaves no article and no loop-protection row behind, only a
  ``failed`` mail-log row for the ticket; temporary errors are retried like any mail. A mail
  that went out but could not be stored afterwards is never sent again: the job fails with
  :data:`FAILED_AFTER_SEND_MESSAGE`, a ``sent`` mail-log row carries the error and the loop
  protection still counts it.
* Interruption (worker restart or crash, or the job timeout): the handler sets the job's
  checkpoint ``sending`` right before SMTP starts (after the sender was built). A job
  interrupted before it goes back into the queue and is sent later; one interrupted after it
  is never sent again (it may or may not have reached the relay) and is recorded as failed
  like a final send error (:func:`on_gi_mail_interrupted`, with a restart or a timeout text).
  A worker that dies after committing the ``sent`` row but before finishing the job leaves
  that row plus a transmission error behind - the mail is not sent twice, which is the side
  to err on.
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any, Literal

import structlog
from pydantic import BaseModel
from sqlalchemy import text

from tiqora.jobs.context import JobCancelled, JobContext, JobFailed, JobRetry
from tiqora.jobs.registry import MAIL_LANE, JobHandler

logger = structlog.get_logger(__name__)

GI_MAIL_KIND = "gi_mail"
# Znuny Framework.xml MailQueue: ItemMaxAttempts 3, IncrementAttemptDelayInMinutes 2
MAX_ATTEMPTS = 3
ATTEMPT_DELAY_MINUTES = 2
CHECKPOINT_SENDING = "sending"
FAILED_AFTER_SEND_MESSAGE = (
    "Die E-Mail wurde gesendet, danach ist ein Fehler aufgetreten. Details stehen im Protokoll"
    " des Hintergrunddienstes."
)
INTERRUPTED_SEND_ERROR = (
    "Sending was interrupted by a restart of the worker; the mail may not have been delivered."
)
TIMEOUT_SEND_ERROR = (
    "Sending did not finish within the job's time limit; the mail may not have been delivered."
)


class GiMailPayload(BaseModel):
    kind: Literal["article_send", "auto_response"]
    ticket_id: int
    article_id: int
    user_id: int  # the user the request wrote as (history / auto response article)
    auto_response_type: str = ""
    # the ticket's queue when the article was stored (auto responses); None in jobs queued
    # before this field existed -> the ticket's current queue
    queue_id: int | None = None


def _retry_delay_minutes(attempt: int) -> int:
    return attempt * ATTEMPT_DELAY_MINUTES


def _retry_note(ctx: JobContext, exc: BaseException) -> str | None:
    """A note for the log when this failure is retried, ``None`` when it is final."""
    from tiqora.channels.email.smtp import is_permanent_smtp_failure

    if ctx.last_attempt or is_permanent_smtp_failure(exc):
        return None
    return (
        f"temporary error, attempt {ctx.attempt} of {ctx.max_attempts},"
        f" next attempt in {_retry_delay_minutes(ctx.attempt)} min"
    )


def _retry(ctx: JobContext, message: str) -> JobRetry:
    return JobRetry(message, delay_seconds=_retry_delay_minutes(ctx.attempt) * 60)


class _Progress:
    """Whether this run passed its ``sending`` checkpoint, and whether the mail went out."""

    def __init__(self, ctx: JobContext) -> None:
        self._ctx = ctx
        self.sending = False
        self.sent = False
        self.message: EmailMessage | None = None

    async def checkpoint(self) -> None:
        await self._ctx.checkpoint(CHECKPOINT_SENDING)
        self.sending = True

    async def mark_sent(self, message: EmailMessage | None = None) -> None:
        self.sent = True
        self.message = message


async def _article_send(
    ctx: JobContext, payload: GiMailPayload, progress: _Progress
) -> dict[str, Any]:
    from tiqora.channels.email.outbound_reply import (
        dispatch_pending_agent_email,
        load_pending_agent_email,
    )
    from tiqora.znuny.sysconfig import SysConfig

    failed_message = "Die E-Mail konnte nicht gesendet werden. Details stehen im Mail-Protokoll."
    async with ctx.session_factory() as session:
        sysconfig = SysConfig(session)
        pending = await load_pending_agent_email(session, sysconfig, article_id=payload.article_id)
        if pending is None:
            raise JobFailed("Der Artikel zu dieser E-Mail existiert nicht mehr.")
        notes: list[str | None] = []

        def retry_note(exc: BaseException) -> str | None:
            notes.append(_retry_note(ctx, exc))
            return notes[-1]

        try:
            # the checkpoint is set right before SMTP starts (after the sender was built)
            await dispatch_pending_agent_email(
                session,
                sysconfig,
                pending,
                retry_note=retry_note,
                before_send=progress.checkpoint,
            )
        except Exception as exc:
            if not notes:  # failed before a send was tried and logged: run_gi_mail records it
                raise
            # the mail log row (and on a final failure the transmission error) is in this
            # session: keep it
            await session.commit()
            temporary = notes[-1] is not None
            logger.error(
                "gi_mail_article_send_failed",
                ticket_id=payload.ticket_id,
                article_id=payload.article_id,
                attempt=ctx.attempt,
                retry=temporary,
                error=str(exc),
            )
            if temporary:
                raise _retry(ctx, failed_message) from exc
            raise JobFailed(failed_message) from exc
        await progress.mark_sent()
        await session.commit()
    return {"article_id": payload.article_id}


async def _auto_response(
    ctx: JobContext, payload: GiMailPayload, progress: _Progress
) -> dict[str, Any]:
    from tiqora.api.compat.article_input import send_gi_auto_response
    from tiqora.channels.email.smtp import is_permanent_smtp_failure
    from tiqora.domain.quoting import html_to_plaintext
    from tiqora.znuny.sysconfig import SysConfig

    failed_message = "Die automatische Antwort konnte nicht gesendet werden."
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
        current_queue_id, from_header, subject, body, content_type = row
        queue_id = payload.queue_id if payload.queue_id is not None else int(current_queue_id)
        plain_body = str(body or "")
        if "html" in str(content_type or "").lower():
            plain_body = html_to_plaintext(plain_body)

        try:
            sent_id = await send_gi_auto_response(
                session,
                ctx.session_factory,
                SysConfig(session),
                ticket_id=payload.ticket_id,
                queue_id=queue_id,
                auto_response_type=payload.auto_response_type,
                from_header=str(from_header or ""),
                subject=str(subject or ""),
                body=plain_body,
                user_id=payload.user_id,
                before_send=progress.checkpoint,
                after_send=progress.mark_sent,
            )
            await session.commit()
        except Exception as exc:
            # nothing of a half-sent auto response is kept (no article, no loop protection)
            await session.rollback()
            if isinstance(exc, JobCancelled):  # the checkpoint found the job taken away
                raise
            if progress.sent:
                # sent, but storing the auto response failed: never send it twice, say so
                await _record_sent_unstored(ctx, payload, progress, exc)
                raise JobFailed(FAILED_AFTER_SEND_MESSAGE) from exc
            temporary = (
                not progress.sent and not ctx.last_attempt and not is_permanent_smtp_failure(exc)
            )
            logger.warning(
                "gi_mail_auto_response_failed",
                ticket_id=payload.ticket_id,
                type=payload.auto_response_type,
                attempt=ctx.attempt,
                retry=temporary,
                error=str(exc),
            )
            note = _retry_note(ctx, exc) if temporary else None
            await _record_lost_mail(
                ctx,
                payload,
                f"Sending failed: {exc}" + (f" ({note})" if note else ""),
            )
            if temporary:
                raise _retry(ctx, failed_message) from exc
            raise JobFailed(failed_message) from exc
    return {"auto_response_article_id": sent_id}


async def run_gi_mail(ctx: JobContext, payload: GiMailPayload) -> dict[str, Any]:
    progress = _Progress(ctx)
    try:
        if payload.kind == "article_send":
            return await _article_send(ctx, payload, progress)
        return await _auto_response(ctx, payload, progress)
    except (JobFailed, JobRetry, JobCancelled):
        raise
    except Exception as exc:
        # e.g. the database went away: before the checkpoint nothing was sent -> try again;
        # after it (or on the last attempt) the mail is recorded as failed, never resent
        logger.exception(
            "gi_mail_unexpected_error",
            kind=payload.kind,
            ticket_id=payload.ticket_id,
            article_id=payload.article_id,
            attempt=ctx.attempt,
            sending=progress.sending,
        )
        message = "Die E-Mail konnte nicht gesendet werden. Details stehen im Mail-Protokoll."
        if progress.sent:  # the mail is out; only the bookkeeping after it failed
            await _record_sent_unstored(ctx, payload, progress, exc)
            raise JobFailed(FAILED_AFTER_SEND_MESSAGE) from exc
        if not progress.sending and not ctx.last_attempt:
            raise _retry(ctx, message) from exc
        await _record_lost_mail(ctx, payload, f"Sending failed: {exc}")
        raise JobFailed(message) from exc


async def on_gi_mail_interrupted(ctx: JobContext, payload: GiMailPayload) -> None:
    """Record a mail whose job was interrupted for good (after its checkpoint, out of
    attempts, or cut off by the job timeout): never lost silently."""
    reason = TIMEOUT_SEND_ERROR if ctx.interrupted_by == "timeout" else INTERRUPTED_SEND_ERROR
    await _record_lost_mail(ctx, payload, reason)


async def _record_sent_unstored(
    ctx: JobContext, payload: GiMailPayload, progress: _Progress, exc: BaseException
) -> None:
    """The mail went out, but what the job writes afterwards was rolled back: log the send
    (``sent`` row, with the error) and, for an auto response, keep its loop protection
    (``ticket_loop_protection`` counts every auto response that left)."""
    from email.utils import getaddresses

    from tiqora.channels.email import loop_protection
    from tiqora.domain.mail_log import write_mail_log

    logger.error(
        "gi_mail_sent_but_not_stored",
        kind=payload.kind,
        ticket_id=payload.ticket_id,
        article_id=payload.article_id,
        error=str(exc),
    )
    try:
        async with ctx.session_factory() as session:
            message = progress.message
            if payload.kind == "auto_response" and message is not None:
                to_line = str(message.get("To", ""))
                await write_mail_log(
                    session,
                    direction="out",
                    status="sent",
                    from_addr=str(message.get("From", "")),
                    to_addr=to_line,
                    subject=str(message.get("Subject", "")),
                    message_id=str(message.get("Message-ID", "")) or None,
                    ticket_id=payload.ticket_id,
                    detail=(
                        f"auto-response ({payload.auto_response_type}) sent; storing its"
                        f" article failed: {exc}"
                    ),
                )
                for _name, addr in getaddresses([to_line]):
                    if addr:
                        await loop_protection.record(session, to=addr)
            else:
                row = (
                    await session.execute(
                        text(
                            "SELECT a_from, a_to, a_cc, a_subject, a_message_id"
                            " FROM article_data_mime WHERE article_id = :aid"
                        ),
                        {"aid": payload.article_id},
                    )
                ).first()
                from_addr, to_addr, cc_addr, subject, message_id = row or ("", "", None, "", None)
                await write_mail_log(
                    session,
                    direction="out",
                    status="sent",
                    from_addr=str(from_addr or ""),
                    to_addr=str(to_addr or ""),
                    cc_addr=cc_addr or None,
                    subject=str(subject or ""),
                    message_id=message_id or None,
                    ticket_id=payload.ticket_id,
                    article_id=payload.article_id,
                    detail=f"sent; recording it failed afterwards: {exc}",
                )
            await session.commit()
    except Exception:  # noqa: BLE001 — the job fails either way; the log has the error
        logger.exception("gi_mail_sent_record_failed", ticket_id=payload.ticket_id)


async def _record_lost_mail(ctx: JobContext, payload: GiMailPayload, reason: str) -> None:
    """``article_send``: the article's transmission error and a ``failed`` mail-log row, like
    a final send error. ``auto_response``: a ``failed`` mail-log row for the ticket (there is
    no auto response article to attach an error to; also written for every failed auto
    response attempt)."""
    from tiqora.channels.email.outbound_reply import record_transmission_error
    from tiqora.domain.mail_log import write_mail_log

    logger.error(
        "gi_mail_failure_recorded",
        kind=payload.kind,
        ticket_id=payload.ticket_id,
        article_id=payload.article_id,
        attempt=ctx.attempt,
        reason=reason,
    )
    async with ctx.session_factory() as session:
        if payload.kind == "auto_response":
            await write_mail_log(
                session,
                direction="out",
                status="failed",
                ticket_id=payload.ticket_id,
                detail=f"auto-response ({payload.auto_response_type}): {reason}",
            )
            await session.commit()
            return
        row = (
            await session.execute(
                text(
                    "SELECT m.a_from, m.a_to, m.a_cc, m.a_subject, m.a_message_id"
                    " FROM article_data_mime m WHERE m.article_id = :aid"
                ),
                {"aid": payload.article_id},
            )
        ).first()
        if row is None:
            return
        from_addr, to_addr, cc_addr, subject, message_id = row
        await write_mail_log(
            session,
            direction="out",
            status="failed",
            from_addr=str(from_addr or ""),
            to_addr=str(to_addr or ""),
            cc_addr=cc_addr or None,
            subject=str(subject or ""),
            message_id=message_id or None,
            ticket_id=payload.ticket_id,
            article_id=payload.article_id,
            detail=reason,
        )
        await record_transmission_error(
            session,
            article_id=payload.article_id,
            message_id=message_id or None,
            message=reason,
        )
        await session.commit()


GI_MAIL_JOB = JobHandler(
    kind=GI_MAIL_KIND,
    payload_model=GiMailPayload,
    run=run_gi_mail,
    title_key="jobs.kinds.gi_mail",
    timeout_seconds=300,
    lane=MAIL_LANE,
    max_attempts=MAX_ATTEMPTS,
    requeue_if_interrupted=True,
    on_interrupted=on_gi_mail_interrupted,
)
