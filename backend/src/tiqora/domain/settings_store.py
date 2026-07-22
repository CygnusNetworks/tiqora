"""Key/value helpers for ``tiqora_settings`` (watermarks, index progress)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraSettings

# Watermark keys used by indexer / poller
KEY_HISTORY_WATERMARK = "poller.ticket_history.max_id"
KEY_ARTICLE_WATERMARK = "poller.article.max_id"
KEY_INDEX_REBUILD_WATERMARK = "index.rebuild.ticket_id"
KEY_INDEX_REBUILD_STATUS = "index.rebuild.status"

# Postmaster (Phase 4a) feature flags — daemon takeover switches. Default OFF:
# Znuny's own daemon task (Daemon::SchedulerCronTaskManager::Task###MailAccountFetch)
# must remain the sole mail-fetching path until an operator flips these keys.
# See docs/parallel-operation.md → "Taking over mail processing".
KEY_POSTMASTER_ENABLED = "daemon.postmaster.enabled"
KEY_POSTMASTER_LEAVE_ON_SERVER = "daemon.postmaster.leave_on_server"
KEY_POSTMASTER_INTERVAL_SECONDS = "daemon.postmaster.interval_seconds"

# Outbox drain (Phase 2c subtask 6) — Meilisearch re-index + webhook/pubsub
# fan-out for tiqora_event_outbox. Default ON (it is the only path that keeps
# search/webhooks/SSE in sync, unlike the other daemon.* takeovers here).
KEY_OUTBOX_ENABLED = "daemon.outbox.enabled"
KEY_OUTBOX_INTERVAL_SECONDS = "daemon.outbox.interval_seconds"

# Escalation sweep (Phase 4b subtask 1) — takes over the
# RebuildEscalationIndexOnline daemon task (Console::Command::Maint::Ticket::
# EscalationIndexRebuild, scheduled via Daemon::SchedulerCronTaskManager::Task
# ###EscalationCheck) plus TriggerEscalationStartEvents. Default OFF: see
# docs/parallel-operation.md → "Taking over escalation index rebuild".
KEY_ESCALATION_ENABLED = "daemon.escalation.enabled"
KEY_ESCALATION_BATCH_SIZE = "daemon.escalation.batch_size"
KEY_ESCALATION_NOTIFY_BEFORE_SECONDS = "daemon.escalation.notify_before_seconds"
KEY_ESCALATION_INTERVAL_SECONDS = "daemon.escalation.interval_seconds"

# Notification engine (Phase 4b subtask 2) — takes over
# Kernel::System::Ticket::Event::NotificationEvent (fired by Znuny's own event
# handler chain on every ticket/article event). Default OFF: see
# docs/parallel-operation.md → "Taking over event notifications".
KEY_NOTIFICATIONS_ENABLED = "daemon.notifications.enabled"
KEY_NOTIFICATIONS_INTERVAL_SECONDS = "daemon.notifications.interval_seconds"

# GenericAgent executor (Phase 4b subtask 3) — takes over
# Daemon::SchedulerCronTaskManager::Task###GenericAgent (bin/znuny.Console.pl
# Maint::Ticket::GenericAgent). Default OFF: see docs/parallel-operation.md →
# "Taking over GenericAgent".
KEY_GENERIC_AGENT_ENABLED = "daemon.generic_agent.enabled"
KEY_GENERIC_AGENT_INTERVAL_SECONDS = "daemon.generic_agent.interval_seconds"

# GDPR retention worker (Phase 2c) — applies config-driven retention rules
# (see tiqora.gdpr.retention.KEY_GDPR_RETENTION_RULES) on a schedule. Default
# OFF; also gated behind the schema-ownership write gate at run time
# (tiqora.gdpr.gate.require_write_gate).
KEY_GDPR_RETENTION_ENABLED = "gdpr.retention.enabled"

# GDPR erasure backup purge worker — deletes tiqora_gdpr_backup rows past the
# job's 30-day window and flips job status to purged. Default ON.
KEY_GDPR_ERASURE_PURGE_ENABLED = "gdpr.erasure.purge_enabled"

# Optional JSON list of extra customer_user column names treated as PII by the
# erasure engine (on top of information_schema discovery).
KEY_GDPR_CUSTOMER_EXTRA_PII = "gdpr.customer_extra_pii_columns"

# Global TOTP/2FA enforcement: when true, every agent without an enabled
# tiqora_user_totp row is forced through must-enroll after password login.
# Default OFF; per-agent ``tiqora_user_auth_config.enforce_2fa`` can also
# force enrollment without flipping this global switch.
KEY_TOTP_ENFORCE_ALL = "auth.totp.enforce_all"

# JSON list of permission_groups.id values. Members of any listed group are
# forced through must-enroll (same effect as per-agent enforce_2fa). Default
# empty list (stored as missing / "[]").
KEY_TOTP_ENFORCE_GROUP_IDS = "auth.totp.enforce_group_ids"

# AI subsystem (Tiqora LLM/MCP agent, see ~/TIQORA_LLM_PLAN.md). Readiness-Gate
# helpers live in tiqora.ai.gate, which consumes these keys.
KEY_OPERATION_MODE = "system.operation_mode"
KEY_AI_DISCLOSURE_DEFAULT = "ai.disclosure.default_text"
KEY_AI_GLOBAL_REPLIES_PER_HOUR = "ai.auto_reply.global_max_per_hour"
KEY_AI_WORKER_ENABLED = "daemon.ai_worker.enabled"
KEY_AI_WORKER_INTERVAL_SECONDS = "daemon.ai_worker.interval_seconds"
# Watermark cursor for the AI worker's own tiqora_event_outbox consumer
# (deliberately separate from the main worker's indexer/webhook cursor — see
# plan §3.9). Consumption itself lands in Phase D; Phase A only reserves the
# key so the settings_store surface is stable.
KEY_AI_OUTBOX_WATERMARK = "daemon.ai_worker.outbox_watermark"


async def get_setting_bool(session: AsyncSession, key: str, default: bool = False) -> bool:
    raw = await get_setting(session, key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


async def get_setting(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(select(TiqoraSettings.value).where(TiqoraSettings.key == key))
    return result.scalar_one_or_none()


async def get_setting_int(session: AsyncSession, key: str, default: int = 0) -> int:
    raw = await get_setting(session, key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    """Upsert a setting value (works on PostgreSQL and MySQL/MariaDB)."""
    existing = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == key))
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = value
    else:
        session.add(TiqoraSettings(key=key, value=value))
    await session.commit()
