"""Postmaster scheduler — feature-flagged takeover of Znuny's mail-fetch daemon.

Gated by the ``daemon.postmaster.enabled`` tiqora_settings key (default OFF —
see ``tiqora.domain.settings_store``). When enabled, iterates all valid
``mail_account`` rows once per tick, fetching and dispatching each
independently: **one broken account must never stop the others** (Znuny's own
daemon task has the same per-account isolation).
"""

from __future__ import annotations

import structlog
from prometheus_client import Counter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.channels.email.fetch import fetch_account, list_valid_mail_accounts
from tiqora.channels.email.pipeline import process_message_and_respond
from tiqora.channels.email.smtp import MailSender, SmtpMailSender
from tiqora.config import Settings, get_settings
from tiqora.db.engine import get_session_factory
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.domain.settings_store import (
    KEY_POSTMASTER_ENABLED,
    KEY_POSTMASTER_LEAVE_ON_SERVER,
    get_setting_bool,
)
from tiqora.znuny.sysconfig import SysConfig

logger = structlog.get_logger(__name__)

POSTMASTER_FETCHED = Counter(
    "tiqora_postmaster_messages_fetched_total", "Messages fetched from mail accounts"
)
POSTMASTER_CREATED = Counter(
    "tiqora_postmaster_tickets_created_total", "New tickets created by the postmaster pipeline"
)
POSTMASTER_FOLLOWUPS = Counter(
    "tiqora_postmaster_followups_total", "Follow-up articles appended by the postmaster pipeline"
)
POSTMASTER_REJECTED = Counter(
    "tiqora_postmaster_rejected_total", "Follow-ups rejected (closed ticket, reject policy)"
)
POSTMASTER_IGNORED = Counter(
    "tiqora_postmaster_ignored_total", "Messages dropped via X-OTRS-Ignore"
)
POSTMASTER_AUTOREPLIES = Counter(
    "tiqora_postmaster_autoreplies_total", "Auto-response articles sent"
)
POSTMASTER_ERRORS = Counter(
    "tiqora_postmaster_errors_total", "Per-account or per-message postmaster errors", ["stage"]
)


async def process_account(
    session_factory: async_sessionmaker[AsyncSession],
    account: MailAccount,
    *,
    settings: Settings,
    mail_sender: MailSender,
    leave_on_server: bool,
) -> dict[str, int]:
    """Fetch and process all messages for one mail account. Errors are isolated per-message."""
    account_id = account.id
    stats = {"fetched": 0, "created": 0, "followups": 0, "rejected": 0, "ignored": 0, "errors": 0}

    try:
        fetch_result = await fetch_account(
            account,
            max_size_kb=await _max_email_size_kb(session_factory),
            leave_on_server=leave_on_server,
        )
    except Exception:
        logger.exception("postmaster_fetch_failed", account_id=account_id)
        POSTMASTER_ERRORS.labels(stage="fetch").inc()
        return stats

    for err in fetch_result.errors:
        logger.warning("postmaster_fetch_error", account_id=account_id, error=err)
        POSTMASTER_ERRORS.labels(stage="fetch").inc()

    stats["fetched"] = len(fetch_result.messages)
    POSTMASTER_FETCHED.inc(len(fetch_result.messages))

    for message in fetch_result.messages:
        try:
            async with session_factory() as session, session.begin():
                sysconfig = SysConfig(session)
                user_id = await sysconfig.postmaster_user_id()
                result = await process_message_and_respond(
                    session,
                    session_factory,
                    sysconfig,
                    mail_sender,
                    raw=message.raw,
                    account=account,
                    user_id=user_id,
                )
            if result.outcome == "new_ticket":
                stats["created"] += 1
                POSTMASTER_CREATED.inc()
            elif result.outcome in ("follow_up", "follow_up_new_ticket"):
                stats["followups"] += 1
                POSTMASTER_FOLLOWUPS.inc()
                if result.outcome == "follow_up_new_ticket":
                    stats["created"] += 1
                    POSTMASTER_CREATED.inc()
            elif result.outcome == "follow_up_reject":
                stats["rejected"] += 1
                POSTMASTER_REJECTED.inc()
            elif result.outcome == "ignored":
                stats["ignored"] += 1
                POSTMASTER_IGNORED.inc()
            if result.autoresponse_article_id is not None:
                POSTMASTER_AUTOREPLIES.inc()
            logger.info(
                "postmaster_message_processed",
                account_id=account_id,
                outcome=result.outcome,
                ticket_id=result.ticket_id,
                uid=message.uid,
            )
        except Exception:
            logger.exception("postmaster_message_failed", account_id=account_id, uid=message.uid)
            stats["errors"] += 1
            POSTMASTER_ERRORS.labels(stage="dispatch").inc()

    return stats


async def _max_email_size_kb(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return await SysConfig(session).postmaster_max_email_size_kb()


async def run_postmaster_tick(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    mail_sender: MailSender | None = None,
) -> dict[str, int]:
    """One scheduler tick: check the feature flag, then process every account."""
    cfg = settings or get_settings()
    factory = session_factory or get_session_factory()
    sender = mail_sender or SmtpMailSender(cfg)

    async with factory() as session:
        enabled = await get_setting_bool(session, KEY_POSTMASTER_ENABLED, False)
        leave_on_server = await get_setting_bool(session, KEY_POSTMASTER_LEAVE_ON_SERVER, False)

    if not enabled:
        logger.debug("postmaster_disabled")
        return {"enabled": 0}

    async with factory() as session:
        accounts = await list_valid_mail_accounts(session)

    totals = {"accounts": len(accounts), "fetched": 0, "created": 0, "followups": 0, "rejected": 0}
    for account in accounts:
        try:
            stats = await process_account(
                factory,
                account,
                settings=cfg,
                mail_sender=sender,
                leave_on_server=leave_on_server,
            )
        except Exception:  # noqa: BLE001 — one broken account must not stop the others
            logger.exception("postmaster_account_failed", account_id=account.id)
            POSTMASTER_ERRORS.labels(stage="account").inc()
            continue
        for key in ("fetched", "created", "followups", "rejected"):
            totals[key] += stats.get(key, 0)

    logger.info("postmaster_tick", **totals)
    return totals
