"""Schema-ownership gate: activation of ``alembic/versions_owned`` (Phase 5).

Activation requires **both**:

1. An env/config flag: ``TIQORA_SCHEMA_OWNERSHIP=1`` (``settings.schema_ownership``).
2. A DB marker row: ``tiqora_settings`` key ``schema.ownership`` = ``"enabled"``,
   with a companion ``schema.ownership.enabled_at`` ISO-8601 timestamp.

Both gates must pass before ``alembic/env.py`` includes the ``versions_owned``
chain — see :func:`ownership_active`. This module also implements the
preflight checks run by ``tiqora ownership enable``: Znuny must be quiescent
(no recent ``ticket_history`` writes, no active sessions) before an operator
is allowed to flip the DB marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.config import Sessions
from tiqora.db.legacy.ticket import TicketHistory
from tiqora.domain.settings_store import get_setting, set_setting

KEY_OWNERSHIP_ENABLED = "schema.ownership"
KEY_OWNERSHIP_ENABLED_AT = "schema.ownership.enabled_at"
VALUE_ENABLED = "enabled"

# Exact confirmation phrase required by `tiqora ownership enable --confirm ...`.
REQUIRED_CONFIRM_PHRASE = "I have shut down Znuny"

DEFAULT_HISTORY_WATERMARK_MINUTES = 15
DEFAULT_SESSION_WATERMARK_MINUTES = 15


@dataclass(frozen=True)
class OwnershipState:
    env_flag: bool
    db_marker: bool
    enabled_at: str | None

    @property
    def active(self) -> bool:
        """Both gates must pass for the owned migration chain to be visible."""
        return self.env_flag and self.db_marker


@dataclass(frozen=True)
class PreflightReport:
    history_quiet: bool
    history_minutes_checked: int
    last_history_change: datetime | None
    sessions_quiet: bool
    sessions_minutes_checked: int
    active_session_count: int

    @property
    def passed(self) -> bool:
        return self.history_quiet and self.sessions_quiet

    def render(self) -> str:
        lines = [
            "Schema-ownership preflight report",
            "==================================",
            f"ticket_history quiet for >= {self.history_minutes_checked}min: "
            f"{'PASS' if self.history_quiet else 'FAIL'}"
            + (
                f" (last change: {self.last_history_change.isoformat()})"
                if self.last_history_change is not None
                else " (no rows)"
            ),
            f"sessions quiet for >= {self.sessions_minutes_checked}min: "
            f"{'PASS' if self.sessions_quiet else 'FAIL'}"
            f" (active sessions: {self.active_session_count})",
            "",
            "RESULT: " + ("PASS — safe to enable ownership" if self.passed else "FAIL — refused"),
        ]
        return "\n".join(lines)


async def get_ownership_state(session: AsyncSession, settings: Settings) -> OwnershipState:
    """Read both gates: env/config flag and DB marker row."""
    marker = await get_setting(session, KEY_OWNERSHIP_ENABLED)
    enabled_at = await get_setting(session, KEY_OWNERSHIP_ENABLED_AT)
    return OwnershipState(
        env_flag=settings.schema_ownership,
        db_marker=(marker == VALUE_ENABLED),
        enabled_at=enabled_at,
    )


async def run_preflight(
    session: AsyncSession,
    *,
    history_watermark_minutes: int = DEFAULT_HISTORY_WATERMARK_MINUTES,
    session_watermark_minutes: int = DEFAULT_SESSION_WATERMARK_MINUTES,
    now: datetime | None = None,
) -> PreflightReport:
    """Detect recent Znuny writes.

    - ``ticket_history`` watermark: the newest ``change_time`` must be older
      than ``history_watermark_minutes`` (or the table must be empty).
    - ``sessions``: Znuny does not timestamp session rows, so "active" is
      approximated by "any row present" — a session table drained for
      ``session_watermark_minutes`` after freezing the Znuny web frontend is
      the operator-visible signal (see docs/cutover.md). If any row exists,
      the check fails; this is deliberately conservative.
    """
    now = now or datetime.now(UTC)

    last_change = (
        await session.execute(select(func.max(TicketHistory.change_time)))
    ).scalar_one_or_none()
    if last_change is None:
        history_quiet = True
    else:
        last_change_aware = last_change if last_change.tzinfo else last_change.replace(tzinfo=UTC)
        age_minutes = (now - last_change_aware).total_seconds() / 60
        history_quiet = age_minutes >= history_watermark_minutes

    active_sessions = (
        await session.execute(select(func.count()).select_from(Sessions))
    ).scalar_one()
    sessions_quiet = active_sessions == 0

    return PreflightReport(
        history_quiet=history_quiet,
        history_minutes_checked=history_watermark_minutes,
        last_history_change=last_change,
        sessions_quiet=sessions_quiet,
        sessions_minutes_checked=session_watermark_minutes,
        active_session_count=active_sessions,
    )


class OwnershipConfirmError(ValueError):
    """Raised when ``--confirm`` does not match the required phrase exactly."""


class OwnershipPreflightError(RuntimeError):
    """Raised when preflight checks fail and ``--force`` was not passed."""

    def __init__(self, report: PreflightReport) -> None:
        self.report = report
        super().__init__("Preflight checks failed; refusing to enable schema ownership")


async def enable_ownership(
    session: AsyncSession,
    *,
    confirm: str,
    force: bool = False,
    history_watermark_minutes: int = DEFAULT_HISTORY_WATERMARK_MINUTES,
    session_watermark_minutes: int = DEFAULT_SESSION_WATERMARK_MINUTES,
) -> PreflightReport:
    """Set the DB marker after preflight checks pass (or ``force=True``).

    Does **not** touch the env/config flag — the operator must still set
    ``TIQORA_SCHEMA_OWNERSHIP=1`` and restart processes for
    :func:`get_ownership_state` / Alembic's ``env.py`` to see both gates as
    active.
    """
    if confirm != REQUIRED_CONFIRM_PHRASE:
        raise OwnershipConfirmError(f'--confirm must be exactly "{REQUIRED_CONFIRM_PHRASE}"')

    report = await run_preflight(
        session,
        history_watermark_minutes=history_watermark_minutes,
        session_watermark_minutes=session_watermark_minutes,
    )
    if not report.passed and not force:
        raise OwnershipPreflightError(report)

    await set_setting(session, KEY_OWNERSHIP_ENABLED, VALUE_ENABLED)
    await set_setting(session, KEY_OWNERSHIP_ENABLED_AT, datetime.now(UTC).isoformat())
    return report
