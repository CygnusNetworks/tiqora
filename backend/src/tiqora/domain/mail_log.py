"""Best-effort email communication log (inbound + outbound).

Writes rows to ``tiqora_mail_log``. Failures here must never block mail
processing or SMTP delivery — every public entry point swallows exceptions
and logs a WARNING.

Commits use a **separate** short-lived session derived from the caller's
engine (when available) so outbound failure rows survive the request
transaction rolling back after :class:`OutboundMailError`.
"""

from __future__ import annotations

from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraMailLog

logger = structlog.get_logger(__name__)

MailDirection = Literal["in", "out"]
# out: queued|sent|failed ; in: received|filtered|failed
MailLogStatus = Literal["queued", "sent", "failed", "received", "filtered"]


def _truncate(value: str | None, limit: int) -> str:
    if not value:
        return ""
    s = str(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"


async def write_mail_log(
    session: AsyncSession | None = None,
    *,
    direction: MailDirection,
    status: str,
    from_addr: str = "",
    to_addr: str = "",
    cc_addr: str | None = None,
    subject: str = "",
    message_id: str | None = None,
    ticket_id: int | None = None,
    article_id: int | None = None,
    queue: str | None = None,
    smtp_code: int | None = None,
    detail: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Insert one communication-log row. Never raises."""
    try:
        row = TiqoraMailLog(
            direction=direction,
            status=status,
            from_addr=_truncate(from_addr, 500),
            to_addr=_truncate(to_addr, 1000),
            cc_addr=_truncate(cc_addr, 1000) or None,
            subject=_truncate(subject, 500),
            message_id=_truncate(message_id, 255) or None,
            ticket_id=ticket_id,
            article_id=article_id,
            queue=_truncate(queue, 200) or None,
            smtp_code=smtp_code,
            detail=detail,
            duration_ms=duration_ms,
        )
        # Prefer an independent commit so failure logs survive outer rollback.
        bind = session.bind if session is not None else None
        if bind is not None:
            factory = async_sessionmaker(bind, expire_on_commit=False, class_=AsyncSession)
            async with factory() as s, s.begin():
                s.add(
                    TiqoraMailLog(
                        direction=row.direction,
                        status=row.status,
                        from_addr=row.from_addr,
                        to_addr=row.to_addr,
                        cc_addr=row.cc_addr,
                        subject=row.subject,
                        message_id=row.message_id,
                        ticket_id=row.ticket_id,
                        article_id=row.article_id,
                        queue=row.queue,
                        smtp_code=row.smtp_code,
                        detail=row.detail,
                        duration_ms=row.duration_ms,
                    )
                )
            return

        if session is not None:
            session.add(row)
            await session.flush()
            return

        from tiqora.db.engine import get_session_factory

        factory = get_session_factory()
        async with factory() as s, s.begin():
            s.add(row)
    except Exception:
        logger.warning(
            "mail_log_write_failed",
            direction=direction,
            status=status,
            ticket_id=ticket_id,
            exc_info=True,
        )


__all__ = [
    "MailDirection",
    "MailLogStatus",
    "write_mail_log",
]
