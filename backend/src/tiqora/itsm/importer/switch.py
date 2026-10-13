"""Explicit switch of the ITSM data from Znuny (Mode A) to Tiqora (Mode B), rollback, status.

Controller ruling R6: the mode transition itself is itsm1's (``switch_to_tiqora`` /
``rollback_to_znuny`` through :class:`~tiqora.itsm.importer.ports.ItsmModePort`, which write
``itsm.mode``, ``itsm.mode_meta`` and ``itsm.switch``); this module owns the preconditions, the
status read-out with its drift count, and the rollback bookkeeping. The importer itself writes
only ``itsm.znuny_readonly``.

Everything here is bounded: counts on Znuny's ITSM tables go through the import source, the last
run is one ``ORDER BY started_at DESC LIMIT 1`` query and the active job one ``find_active_job``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraItsmImportRun, TiqoraSettings
from tiqora.itsm.importer import facades
from tiqora.itsm.importer.ports import ItsmModeName
from tiqora.itsm.mode import (
    SETTING_MODE,
    SETTING_SWITCH,
    SETTING_ZNUNY_READONLY,
    get_switch_info,
)
from tiqora.jobs.service import find_active_job, utc_now

SWITCH_KEY: Final = SETTING_SWITCH
READONLY_KEY: Final = SETTING_ZNUNY_READONLY
MODE_KEY: Final = SETTING_MODE

#: job kinds / dedupe key of :mod:`tiqora.itsm.importer.jobs` (that module imports this one)
_JOB_KINDS: Final = ("core.itsm_import", "core.itsm_switch")
_DEDUPE_KEY: Final = "itsm_import"

ALREADY_SWITCHED: Final = "Die ITSM-Daten werden bereits in Tiqora geführt."
NOT_SWITCHED: Final = "Die ITSM-Daten wurden nicht auf Tiqora umgestellt."
NOT_PRESENT: Final = "In der Datenbank wurde kein Znuny ITSM gefunden."
UNSUPPORTED: Final = "Die installierte Znuny-ITSM-Version wird vom Import nicht unterstützt."
BUSY: Final = "Ein ITSM-Import oder eine Umstellung läuft bereits."
WRONG_MODE: Final = "Die Umstellung ist nur aus dem Modus „Znuny-Durchgriff“ möglich."
NO_DONE_IMPORT: Final = (
    "Es gibt noch keinen erfolgreichen Import. Führen Sie zuerst einen Import durch."
)
EDITS_NOT_STOPPED: Final = (
    "Ohne das Znuny-Zusatzpaket müssen Sie bestätigen, dass in Znuny keine "
    "Konfigurationselemente mehr bearbeitet werden."
)
READONLY_STUCK: Final = (
    "Znuny ist für Konfigurationselemente gesperrt, obwohl keine Umstellung läuft. "
    "Heben Sie die Sperre auf."
)
WITHOUT_CI_DATA_REFUSED: Final = (
    "„Umstellen ohne CI-Daten“ ist nur möglich, wenn Tiqora die Znuny-ITSM-Version nicht lesen "
    "kann. Führen Sie einen Import durch und stellen Sie dann um."
)


class SwitchBlocked(Exception):
    """A precondition of import, switch or rollback fails; ``message`` is shown (German)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class SwitchInfo:
    switched_at: datetime | None
    by: int | None
    run_id: int | None


@dataclass(frozen=True, slots=True)
class ImportStatus:
    """Mirrors ``ItsmImportStatusOut`` (plan D8); ``last_run`` is the ORM row."""

    mode: ItsmModeName
    znuny_itsm_present: bool
    profile: str | None
    switched: SwitchInfo | None
    readonly_flag: bool
    last_run: TiqoraItsmImportRun | None
    active_job_id: int | None
    can_import: bool
    can_switch: bool
    blocked_reason: str | None
    znuny_counts: dict[str, int] | None
    znuny_changes_since_switch: int | None


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def _parse_switch(raw: dict[str, object] | None) -> SwitchInfo | None:
    if raw is None:
        return None
    switched_at: datetime | None = None
    at = raw.get("switched_at")
    if isinstance(at, str):
        try:
            switched_at = _naive_utc(datetime.fromisoformat(at))
        except ValueError:
            switched_at = None
    by, run_id = raw.get("by"), raw.get("run_id")
    return SwitchInfo(
        switched_at=switched_at,
        by=by if isinstance(by, int) else None,
        run_id=run_id if isinstance(run_id, int) else None,
    )


async def _put_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(TiqoraSettings, key)
    if row is None:
        session.add(TiqoraSettings(key=key, value=value))
    else:
        row.value = value


async def lock_import_start(session: AsyncSession) -> None:
    """Lock the importer-owned ``itsm.znuny_readonly`` row, creating it ("0") when absent.

    Serialises every start of an import or switch with rollback and "Sperre aufheben": whoever
    holds the lock sees the others' committed jobs (with a locking read, see ``_active_job_id``).
    """
    try:
        async with session.begin_nested():
            session.add(TiqoraSettings(key=READONLY_KEY, value="0"))
            await session.flush()
    except IntegrityError:
        pass  # another start inserted it first
    await session.execute(
        select(TiqoraSettings.key).where(TiqoraSettings.key == READONLY_KEY).with_for_update()
    )


async def _active_job_id(session: AsyncSession, *, for_update: bool = False) -> int | None:
    """``for_update``: a locking read, which on MariaDB sees jobs committed after the session's
    snapshot (needed behind :func:`lock_import_start`)."""
    for kind in _JOB_KINDS:
        job = await find_active_job(
            session, kind=kind, dedupe_key=_DEDUPE_KEY, for_update=for_update
        )
        if job is not None:
            return job.id
    return None


async def _has_done_import(session: AsyncSession) -> bool:
    row = await session.execute(
        select(TiqoraItsmImportRun.id)
        .where(TiqoraItsmImportRun.kind == "import", TiqoraItsmImportRun.status == "done")
        .limit(1)
    )
    return row.first() is not None


async def _last_run(session: AsyncSession) -> TiqoraItsmImportRun | None:
    return (
        (
            await session.execute(
                select(TiqoraItsmImportRun)
                .order_by(TiqoraItsmImportRun.started_at.desc(), TiqoraItsmImportRun.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def check_can_import(session: AsyncSession) -> None:
    """Refuse an import after the switch (the data is kept in Tiqora from then on)."""
    if await facades.get_ports(session).mode.is_switched():
        raise SwitchBlocked(ALREADY_SWITCHED)


async def _switch_denial(
    session: AsyncSession,
    *,
    ignore_job_id: int | None,
    without_ci_data: bool,
) -> str | None:
    """First reason why the switch cannot start (``None``: it can); no body parameters."""
    mode = facades.get_ports(session).mode
    if await mode.is_switched():
        return ALREADY_SWITCHED
    if not await mode.znuny_itsm_present():
        return NOT_PRESENT
    if without_ci_data and await facades.get_ports(session).source.profile() is not None:
        # a readable Znuny ITSM goes through the import; this path would skip it entirely
        return WITHOUT_CI_DATA_REFUSED
    if await mode.effective_mode() != "znuny_readthrough":
        return WRONG_MODE
    active = await _active_job_id(session)
    if active is not None and active != ignore_job_id:
        return BUSY
    if not without_ci_data and not await _has_done_import(session):
        return NO_DONE_IMPORT
    return None


async def check_can_switch(
    session: AsyncSession,
    *,
    addon_installed: bool,
    edits_stopped: bool,
    without_ci_data: bool = False,
    ignore_job_id: int | None = None,
) -> None:
    """Preconditions of D7 (the API checks them, the switch job re-checks them).

    ``without_ci_data`` is the explicit "Umstellen ohne CI-Daten" path for Znuny ITSM releases
    the importer cannot read (itsm1 F19): no successful import is required then, and it is
    refused when the release is readable (the import path applies).
    ``ignore_job_id`` is the calling switch job itself.
    """
    reason = await _switch_denial(
        session, ignore_job_id=ignore_job_id, without_ci_data=without_ci_data
    )
    if reason is None and not addon_installed and not edits_stopped:
        reason = EDITS_NOT_STOPPED
    if reason is not None:
        raise SwitchBlocked(reason)


async def rollback(session: AsyncSession, *, user_id: int) -> None:
    """Back to Mode A: itsm1 ``rollback_to_znuny`` (drops ``itsm.switch``), Znuny writable again,
    a ``rollback`` run row. The imported CMDB rows stay; Mode A hides them (409 ``cmdb_in_znuny``).
    Flush only; the caller commits.
    """
    ports = facades.get_ports(session)
    await lock_import_start(session)
    if not await ports.mode.is_switched():
        raise SwitchBlocked(NOT_SWITCHED)
    if await _active_job_id(session, for_update=True) is not None:
        raise SwitchBlocked(BUSY)
    now = utc_now()
    run = TiqoraItsmImportRun(
        kind="rollback",
        status="done",
        job_id=None,
        started_by=user_id,
        started_at=now,
        finished_at=now,
        profile=None,
        summary="{}",
        message=None,
    )
    session.add(run)
    await session.flush()
    await ports.mode.rollback_to_znuny(user_id=user_id, import_run_id=run.id)
    await _put_setting(session, READONLY_KEY, "0")
    await session.flush()


async def import_status(session: AsyncSession) -> ImportStatus:
    """Everything the admin page shows; no Znuny access when Znuny has no ITSM."""
    ports = facades.get_ports(session)
    mode = await ports.mode.effective_mode()
    present = await ports.mode.znuny_itsm_present()
    switch = _parse_switch(await get_switch_info(session))
    flag_row = await session.get(TiqoraSettings, READONLY_KEY)
    readonly = bool(flag_row is not None and flag_row.value == "1")
    active = await _active_job_id(session)
    last_run = await _last_run(session)

    profile: str | None = None
    counts: dict[str, int] | None = None
    drift: int | None = None
    if present:
        source = ports.source
        info = await source.info()
        profile = info.profile if info.core_installed else None
        if profile is not None:
            changes = await source.change_counts()
            # ITSMCore without the CMDB package (R6): no CI tables to count, the import still runs
            cmdb = info.cmdb_installed
            counts = {
                "cis": await source.ci_count() if cmdb else 0,
                "classes": await source.class_count() if cmdb else 0,
                "changes": changes.get("change", 0),
                "workorders": changes.get("workorder", 0),
            }
            if switch is not None and switch.switched_at is not None:
                # CIs changed and CI links created in Znuny since the switch (Assumption 9)
                drift = (
                    await source.count_cis_changed_since(switch.switched_at)
                    + await source.count_links_changed_since(switch.switched_at)
                    if cmdb
                    else 0
                )

    reason: str | None = None
    if switch is not None:
        reason = ALREADY_SWITCHED
    elif not present:
        reason = NOT_PRESENT
    elif profile is None:
        reason = UNSUPPORTED
    elif active is not None:
        reason = BUSY
    elif readonly:
        reason = READONLY_STUCK
    elif mode != "znuny_readthrough":
        reason = WRONG_MODE
    can_import = reason is None
    can_switch = (
        switch is None
        and present
        and mode == "znuny_readthrough"
        and active is None
        and not readonly
        and (profile is None or await _has_done_import(session))
    )
    return ImportStatus(
        mode=mode,
        znuny_itsm_present=present,
        profile=profile,
        switched=switch,
        readonly_flag=readonly,
        last_run=last_run,
        active_job_id=active,
        can_import=can_import,
        can_switch=can_switch,
        blocked_reason=reason,
        znuny_counts=counts,
        znuny_changes_since_switch=drift,
    )


async def release_readonly(session: AsyncSession) -> None:
    """ "Sperre aufheben": clear a stuck ``itsm.znuny_readonly`` flag (flush only).

    Refused while a job is active (it owns the flag) and after the switch (the flag is then
    meant to stay set).
    """
    await lock_import_start(session)
    if await facades.get_ports(session).mode.is_switched():
        raise SwitchBlocked(ALREADY_SWITCHED)
    if await _active_job_id(session, for_update=True) is not None:
        raise SwitchBlocked(BUSY)
    await _put_setting(session, READONLY_KEY, "0")
    await session.flush()
