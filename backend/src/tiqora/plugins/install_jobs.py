"""Background jobs of the plugin installer (D9): install, uninstall, fetch, repository refresh.

All four run in the ``plugins`` lane (one slot: strictly one plugin operation at a time). This
module holds the payload models of all four and the install handler; the other handlers are
added by the tasks that implement them.

``core.plugin_install`` never imports plugin code in the worker: the package is unpacked, then
two short-lived child processes check the new code (``plugins verify --import --strict --path``)
and run its database changes (``plugins migrate --path``). Only after both succeeded is the
plugin's row written; the previously installed version stays active until then.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final

import structlog
from pydantic import BaseModel
from sqlalchemy import delete, select

from tiqora.config import Settings
from tiqora.db.tiqora.models import (
    TiqoraPlugin,
    TiqoraPluginData,
    TiqoraPluginHook,
    TiqoraPluginRepository,
    TiqoraPluginRuntime,
    TiqoraSettings,
)
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import utc_now
from tiqora.plugins import migrations, packages_store, runtime
from tiqora.plugins.inspect import PackageReport, inspect_read_package, installed_state
from tiqora.plugins.inspect import short_id_owners as load_short_id_owners
from tiqora.plugins.migrations import _child_env
from tiqora.plugins.package import PackageError, read_tqplugin
from tiqora.plugins.repositories import (
    FetchError,
    build_key_ring_named,
    download_package,
    refresh_repository,
    ring_for_source,
    with_repository_signer,
)
from tiqora.plugins.restart import request_restart, utcnow
from tiqora.plugins.runtime import materialize, runtime_dir_problem

log = structlog.get_logger(__name__)

PLUGIN_INSTALL_KIND: Final = "core.plugin_install"
PLUGIN_UNINSTALL_KIND: Final = "core.plugin_uninstall"
PLUGIN_REPOSITORY_REFRESH_KIND: Final = "core.plugin_repository_refresh"
PLUGIN_FETCH_KIND: Final = "core.plugin_fetch"
PLUGIN_LANE: Final = "plugins"

VERIFY_TIMEOUT_SECONDS: Final = 120
MIGRATE_TIMEOUT_SECONDS: Final = 900
#: exit code reported by ``_run_subprocess`` when the child was killed for running too long
TIMEOUT_EXIT_CODE: Final = 124
_OUTPUT_LINES: Final = 20
_MAX_OUTPUT: Final = 2000
_HEAD_RE: Final = re.compile(r", Stand (\S+)\s*$")

MISSING_PACKAGE_MESSAGE: Final = "Paket nicht mehr vorhanden – bitte erneut hochladen"
IN_IMAGE_MESSAGE: Final = (
    "Im Image eingebaut – bitte aus plugins.txt entfernen und das Image neu bauen"
)
NO_SHORT_ID_MESSAGE: Final = "Kurzname unbekannt – Daten können nicht zugeordnet werden"
EXPORT_TOO_LARGE_MESSAGE: Final = (
    "Die Daten der Erweiterung sind größer als 200 MB. Bitte sichern Sie die Datenbank und "
    "entfernen Sie die Tabellen tqp_{short_id}_* von Hand."
)


class PluginInstallPayload(BaseModel):
    package_sha256: str
    enable: bool = True
    restart: bool = True


class PluginUninstallPayload(BaseModel):
    plugin_id: str
    delete_data: bool = False
    restart: bool = True


class PluginFetchPayload(BaseModel):
    repository_id: int
    plugin_id: str
    version: str


class PluginRepositoryRefreshPayload(BaseModel):
    repository_id: int


def refresh_dedupe_key(repository_id: int) -> str:
    """One refresh per repository at a time (shared by the endpoint and the job)."""
    return f"repo:{repository_id}"


def fetch_dedupe_key(repository_id: int, plugin_id: str, version: str) -> str:
    """One download per repository, plugin and release at a time (endpoint and job share it)."""
    return f"fetch:{repository_id}:{plugin_id}:{version}"


def dedupe_key(plugin_id: str) -> str:
    """One install or uninstall per plugin id at a time (the endpoint checks both kinds)."""
    return f"plugin:{plugin_id}"


# ---------------------------------------------------------------- child processes

Runner = Callable[..., Awaitable[tuple[int, str]]]


def _tail(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-_OUTPUT_LINES:])[-_MAX_OUTPUT:]


async def _run_subprocess(
    argv: Sequence[str],
    timeout: float,  # noqa: ASYNC109 - enforced here, with a kill
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str]:
    """Run ``argv``; returns ``(exit code, last 20 lines of stdout+stderr)``.

    A child that runs longer than ``timeout`` seconds is killed and reported as
    ``TIMEOUT_EXIT_CODE``. Tests replace this function (module attribute) with a runner that
    calls the same CLI in-process.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=None if env is None else dict(env),
        )
    except OSError as exc:
        return 127, f"Prozess konnte nicht gestartet werden: {exc}"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return TIMEOUT_EXIT_CODE, f"Zeitüberschreitung: länger als {int(timeout)} Sekunden"
    except BaseException:  # cancelled (job timeout, worker shutdown): no orphaned child
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    return proc.returncode if proc.returncode is not None else 1, _tail(
        out.decode("utf-8", errors="replace")
    )


def _child_environment(settings: Settings) -> dict[str, str]:
    env = _child_env(settings.database_url)
    env.pop("TIQORA_PLUGINS_SAFE_MODE", None)
    return env


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


# ---------------------------------------------------------------- install


def _inspect(
    data: bytes,
    settings: Settings,
    ring: Any,
    installed: Any,
    images: frozenset[str],
    owners: dict[str, str],
) -> PackageReport:
    pkg = read_tqplugin(data, max_bytes=settings.plugins_max_package_bytes)
    return inspect_read_package(
        pkg,
        settings=settings,
        ring=ring,
        installed=installed,
        image_plugin_ids=images,
        short_id_owners=owners,
    )


async def _write_plugin_row(
    ctx: JobContext,
    payload: PluginInstallPayload,
    report: PackageReport,
    *,
    source: str,
    repository_id: int | None,
    head: str | None,
) -> None:
    """Step 5: one write transaction. ``enabled`` is set for a new row only."""
    now = utc_now().replace(microsecond=0)  # MariaDB DATETIME has no fraction
    async with ctx.session_factory() as session, session.begin():
        row = await session.get(TiqoraPlugin, report.plugin_id)
        if row is None:
            session.add(
                TiqoraPlugin(
                    id=report.plugin_id,
                    name=report.name[:200],
                    version=report.version[:64],
                    enabled=payload.enable,
                    source=source,
                    signature_status=report.trust.value,
                    installed_at=now,
                    updated_at=now,
                    short_id=report.short_id,
                    package_sha256=report.sha256,
                    repository_id=repository_id,
                    key_id=report.key_id,
                    signer=report.signer[:200] if report.signer else None,
                    installed_by=ctx.created_by,
                    migrated_version=report.version[:64],
                    migrated_head=head[:64] if head else None,
                    migrated_at=now,
                    migration_error=None,
                )
            )
        else:
            # an update keeps ``enabled`` and the hook cursors (R-REENABLE-BACKLOG)
            row.name = report.name[:200]
            row.version = report.version[:64]
            row.source = source
            row.signature_status = report.trust.value
            row.updated_at = now
            row.short_id = report.short_id
            row.package_sha256 = report.sha256
            row.repository_id = repository_id
            row.key_id = report.key_id
            row.signer = report.signer[:200] if report.signer else None
            row.installed_by = ctx.created_by
            row.migrated_version = report.version[:64]
            row.migrated_head = head[:64] if head else None
            row.migrated_at = now
            row.migration_error = None
        # the same plugin id adopts kept data again (F2)
        await session.execute(
            delete(TiqoraPluginData).where(
                TiqoraPluginData.short_id == report.short_id,
                TiqoraPluginData.plugin_id == report.plugin_id,
            )
        )
        await packages_store.mark_package_installed(session, report.sha256, now=now)


async def _recorded_head(ctx: JobContext, plugin_id: str) -> str | None:
    async with ctx.session_factory() as session:
        row = await session.get(TiqoraPlugin, plugin_id)
        head = row.migrated_head if row is not None else None
        await session.rollback()
    return head


async def _claim_short_id(ctx: JobContext, report: PackageReport) -> None:
    """Fresh install: own the short id before the migrate child creates tables (F2).

    MariaDB commits DDL implicitly, so a failed first install leaves ``tqp_<short>_*`` behind
    without a plugin row; the ownership row lets a retry of the same plugin adopt them.
    """
    async with ctx.session_factory() as session, session.begin():
        if await session.get(TiqoraPlugin, report.plugin_id) is not None:
            return
        if await session.get(TiqoraPluginData, report.short_id) is None:
            session.add(
                TiqoraPluginData(
                    short_id=report.short_id,
                    plugin_id=report.plugin_id,
                    kept_at=utc_now().replace(microsecond=0),
                )
            )


async def run_plugin_install(ctx: JobContext, payload: PluginInstallPayload) -> dict[str, Any]:
    settings = ctx.settings
    # 1. the stored package, judged again with today's keys and installed state
    async with ctx.session_factory() as session:
        stored = await packages_store.get_package(session, payload.package_sha256)
        data = await packages_store.get_package_data(session, payload.package_sha256)
        if stored is None or data is None:
            raise JobFailed(MISSING_PACKAGE_MESSAGE)
        source, repository_id = stored.source, stored.repository_id
        ring, names = await build_key_ring_named(session, settings)
        ring, names = ring_for_source(ring, names, source=source, repository_id=repository_id)
        installed, images = await installed_state(session, settings)
        owners = await load_short_id_owners(session)
        await session.rollback()
    try:
        report = await asyncio.to_thread(_inspect, data, settings, ring, installed, images, owners)
    except PackageError as exc:
        raise JobFailed(str(exc)) from None
    report = with_repository_signer(report, ring, names)
    if not report.installable:
        raise JobFailed("; ".join(report.problems)[:_MAX_OUTPUT] or "Nicht installierbar")
    reason = runtime_dir_problem(settings)
    if reason is not None:
        raise JobFailed(reason)

    # 2. unpack
    await ctx.progress(1, 4, "Paket wird ausgepackt")
    try:
        site = await asyncio.to_thread(
            materialize, settings, report.plugin_id, report.version, report.sha256, data
        )
    except PackageError as exc:
        raise JobFailed(str(exc)) from None
    except OSError as exc:
        raise JobFailed(f"Paketdateien konnten nicht geschrieben werden: {exc}") from None

    # 3. import check of the NEW code in a child (the installed version is not activated there)
    await ctx.progress(2, 4, "Paket wird geprüft")
    env = _child_environment(settings)
    code, output = await _run_subprocess(
        [
            sys.executable,
            "-m",
            "tiqora.main",
            "plugins",
            "verify",
            "--import",
            "--strict",
            "--path",
            str(site),
            "--only",
            report.plugin_id,
        ],
        VERIFY_TIMEOUT_SECONDS,
        env=env,
    )
    if code != 0:
        raise JobFailed(f"Prüfung fehlgeschlagen: {output}"[:_MAX_OUTPUT])

    # 4. database changes in a child; from here on the job is not requeued after an interruption
    await ctx.checkpoint("migrate")
    await _claim_short_id(ctx, report)
    await ctx.progress(3, 4, "Datenbank wird angepasst")
    code, output = await _run_subprocess(
        [
            sys.executable,
            "-m",
            "tiqora.main",
            "plugins",
            "migrate",
            report.plugin_id,
            "--path",
            str(site),
        ],
        MIGRATE_TIMEOUT_SECONDS,
        env=env,
    )
    if code != 0:
        detail = _last_line(output) or f"Exit-Code {code}"
        raise JobFailed(f"Datenbank-Änderungen fehlgeschlagen: {detail}"[:_MAX_OUTPUT])
    head = None
    for line in reversed(output.splitlines()):
        match = _HEAD_RE.search(line)
        if match:
            head = match.group(1)
            break
    if head is None and report.action == "update":
        head = await _recorded_head(ctx, report.plugin_id)  # the child recorded it already

    # 5. the plugin's row
    await _write_plugin_row(
        ctx, payload, report, source=source, repository_id=repository_id, head=head
    )
    await ctx.progress(4, 4, "Fertig")

    # 6. restart request, after the install is committed; a failure here must not undo it
    generation: int | None = None
    if payload.restart and settings.plugins_coordinated_restart:
        try:
            async with ctx.session_factory() as session:
                generation = await request_restart(
                    session,
                    by=ctx.created_by,
                    reason=f"Erweiterung „{report.name}“ {report.version} installiert",
                    now=utcnow().replace(microsecond=0),
                )
                await session.commit()
        except Exception:
            log.warning("plugin_install_restart_request_failed", plugin=report.plugin_id)
    log.info(
        "plugin_installed",
        plugin=report.plugin_id,
        version=report.version,
        action=report.action,
        restart_generation=generation,
    )
    return {
        "plugin_id": report.plugin_id,
        "version": report.version,
        "action": report.action,
        "restart_generation": generation,
        "migrated_head": head,
    }


# ---------------------------------------------------------------- uninstall


def in_image(settings: Settings, plugin_id: str) -> bool:
    """The plugin is shipped in the image (decided by location, F1) and cannot be removed here."""
    return plugin_id in runtime.image_plugin_ids(settings)


def _discovered_short_id(plugin_id: str) -> str | None:
    from tiqora.plugins.discovery import discovered_plugins

    for found in discovered_plugins():
        if found.plugin_id == plugin_id and found.manifest is not None:
            return found.manifest.short_id
    return None


async def _delete_plugin_records(
    ctx: JobContext,
    row_id: str,
    *,
    short_id: str | None,
    keep_data: bool,
    delete_data: bool,
) -> None:
    """One write transaction: the plugin's rows go, kept data gets an owner (F2)."""
    now = utc_now().replace(microsecond=0)
    async with ctx.session_factory() as session, session.begin():
        await session.execute(
            delete(TiqoraPluginRuntime).where(TiqoraPluginRuntime.plugin_id == row_id)
        )
        await session.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id == row_id))
        await packages_store.unmark_packages(session, row_id)
        if delete_data:
            # Only `plugins.<id>.<field>`: a longer plugin id (`<id>.extra`) shares the prefix.
            prefix = f"plugins.{row_id}."
            candidates = (
                await session.scalars(
                    select(TiqoraSettings.key).where(
                        TiqoraSettings.key.startswith(prefix, autoescape=True)
                    )
                )
            ).all()
            own_keys = [k for k in candidates if "." not in k[len(prefix) :]]
            if own_keys:
                await session.execute(
                    delete(TiqoraSettings).where(TiqoraSettings.key.in_(own_keys))
                )
            await session.execute(
                delete(TiqoraPluginHook).where(TiqoraPluginHook.plugin_id == row_id)
            )
        if short_id is not None:
            owner = await session.get(TiqoraPluginData, short_id)
            if keep_data:
                if owner is None:
                    session.add(TiqoraPluginData(short_id=short_id, plugin_id=row_id, kept_at=now))
                elif owner.plugin_id == row_id:
                    owner.kept_at = now
            elif delete_data and owner is not None and owner.plugin_id == row_id:
                await session.delete(owner)


async def run_plugin_uninstall(ctx: JobContext, payload: PluginUninstallPayload) -> dict[str, Any]:
    settings = ctx.settings
    plugin_id = payload.plugin_id
    async with ctx.session_factory() as session:
        row = await session.get(TiqoraPlugin, plugin_id)
        name = row.name if row is not None else ""
        recorded_short_id = row.short_id if row is not None else None
        await session.rollback()
    if row is None:
        raise JobFailed("Erweiterung ist nicht installiert")
    if in_image(settings, plugin_id):
        raise JobFailed(IN_IMAGE_MESSAGE)
    short_id = recorded_short_id or _discovered_short_id(plugin_id)

    deleted: list[str] = []
    export_file_id: int | None = None
    keep_data = False
    if payload.delete_data:
        if short_id is None:
            raise JobFailed(NO_SHORT_ID_MESSAGE)
        async with ctx.session_factory() as session:
            tables = await migrations.plugin_tables(session, short_id)
            data: bytes | None = None
            if tables:
                try:
                    data = await migrations.export_plugin_data(
                        session,
                        short_id,
                        max_bytes=migrations.EXPORT_MAX_BYTES,
                        plugin_id=plugin_id,
                    )
                except migrations.PluginExportTooLarge:
                    raise JobFailed(EXPORT_TOO_LARGE_MESSAGE.format(short_id=short_id)) from None
            await session.rollback()
        if data is not None:
            export_file_id = await ctx.add_file(
                f"{plugin_id}-daten-{utc_now().date().isoformat()}.jsonl.gz",
                "application/gzip",
                data,
            )
            await ctx.checkpoint("drop")
        # Also without tables: leftover PG enum types are dropped too.
        async with ctx.session_factory() as session:
            deleted = await migrations.drop_plugin_data(session, short_id)
            await session.commit()
    elif short_id is not None:
        async with ctx.session_factory() as session:
            keep_data = bool(await migrations.plugin_tables(session, short_id))
            await session.rollback()
    await _delete_plugin_records(
        ctx, plugin_id, short_id=short_id, keep_data=keep_data, delete_data=payload.delete_data
    )

    generation: int | None = None
    if payload.restart and settings.plugins_coordinated_restart:
        try:
            async with ctx.session_factory() as session:
                generation = await request_restart(
                    session,
                    by=ctx.created_by,
                    reason=f"Erweiterung „{name}“ entfernt",
                    now=utcnow().replace(microsecond=0),
                )
                await session.commit()
        except Exception:
            log.warning("plugin_uninstall_restart_request_failed", plugin=plugin_id)
    log.info(
        "plugin_uninstalled",
        plugin=plugin_id,
        data_deleted=payload.delete_data,
        tables=len(deleted),
        restart_generation=generation,
    )
    return {
        "plugin_id": plugin_id,
        "deleted_tables": deleted,
        "export_file_id": export_file_id,
        "restart_generation": generation,
    }


# ---------------------------------------------------------------- repository refresh


async def run_plugin_repository_refresh(
    ctx: JobContext, payload: PluginRepositoryRefreshPayload
) -> dict[str, Any]:
    """Fetch and store one repository's index; a refused index is a result, not a failure."""
    async with ctx.session_factory() as session:
        repo = await session.get(TiqoraPluginRepository, payload.repository_id)
        if repo is None:
            raise JobFailed("Paketquelle nicht gefunden")
        result = await refresh_repository(
            session, repo, settings=ctx.settings, now=utc_now().replace(microsecond=0)
        )
        await session.commit()
    return {"ok": result.ok, "plugins": result.plugins, "error": result.error}


async def run_plugin_fetch(ctx: JobContext, payload: PluginFetchPayload) -> dict[str, Any]:
    """Download one release from a repository into the package store (D8)."""
    try:
        result = await download_package(
            ctx.session_factory,
            repository_id=payload.repository_id,
            plugin_id=payload.plugin_id,
            version=payload.version,
            settings=ctx.settings,
            uploaded_by=ctx.created_by,
        )
    except FetchError as exc:
        raise JobFailed(str(exc)) from None
    return {"package_sha256": result.package_sha256, "installable": result.installable}


PLUGIN_FETCH_JOB = JobHandler(
    kind=PLUGIN_FETCH_KIND,
    payload_model=PluginFetchPayload,
    run=run_plugin_fetch,
    title_key="jobs.kinds.core.plugin_fetch",
    timeout_seconds=600,
    lane=PLUGIN_LANE,
    max_attempts=1,
    requeue_if_interrupted=False,
    result_private=False,
)

PLUGIN_REPOSITORY_REFRESH_JOB = JobHandler(
    kind=PLUGIN_REPOSITORY_REFRESH_KIND,
    payload_model=PluginRepositoryRefreshPayload,
    run=run_plugin_repository_refresh,
    title_key="jobs.kinds.core.plugin_repository_refresh",
    timeout_seconds=300,
    lane=PLUGIN_LANE,
    max_attempts=1,
    requeue_if_interrupted=False,
    result_private=False,
)

PLUGIN_UNINSTALL_JOB = JobHandler(
    kind=PLUGIN_UNINSTALL_KIND,
    payload_model=PluginUninstallPayload,
    run=run_plugin_uninstall,
    title_key="jobs.kinds.core.plugin_uninstall",
    timeout_seconds=1500,
    lane=PLUGIN_LANE,
    max_attempts=1,
    requeue_if_interrupted=False,
    result_private=False,
)

PLUGIN_INSTALL_JOB = JobHandler(
    kind=PLUGIN_INSTALL_KIND,
    payload_model=PluginInstallPayload,
    run=run_plugin_install,
    title_key="jobs.kinds.core.plugin_install",
    timeout_seconds=1500,
    lane=PLUGIN_LANE,
    max_attempts=1,
    requeue_if_interrupted=False,
    result_private=False,
)

__all__ = [
    "PLUGIN_FETCH_JOB",
    "PLUGIN_FETCH_KIND",
    "PLUGIN_INSTALL_JOB",
    "PLUGIN_INSTALL_KIND",
    "PLUGIN_REPOSITORY_REFRESH_JOB",
    "PLUGIN_REPOSITORY_REFRESH_KIND",
    "PLUGIN_UNINSTALL_JOB",
    "PLUGIN_LANE",
    "PLUGIN_UNINSTALL_KIND",
    "PluginFetchPayload",
    "PluginInstallPayload",
    "PluginRepositoryRefreshPayload",
    "PluginUninstallPayload",
    "dedupe_key",
    "fetch_dedupe_key",
    "in_image",
    "refresh_dedupe_key",
    "run_plugin_fetch",
    "run_plugin_install",
    "run_plugin_repository_refresh",
    "run_plugin_uninstall",
]
