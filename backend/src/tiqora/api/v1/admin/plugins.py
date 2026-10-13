"""Admin view of installed plugins: status, capabilities, switch on/off, "try again" (P11).

The list is built from discovery (no plugin code is imported, so it also works in safe
mode), the database rows of all process roles and this process's registry.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Literal

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.config import Settings
from tiqora.db.tiqora.models import (
    TiqoraPlugin,
    TiqoraPluginHook,
    TiqoraPluginPackage,
    TiqoraPluginRepository,
    TiqoraPluginRuntime,
)
from tiqora.domain.schemas import UtcDateTime
from tiqora.domain.settings_store import set_setting
from tiqora.extensions.core import get_registry
from tiqora.jobs.service import utc_now
from tiqora.plugins import store
from tiqora.plugins.compat import check_compat, release_core_version
from tiqora.plugins.discovery import DiscoveredPlugin, discovered_plugins
from tiqora.plugins.inspect import PackageReport
from tiqora.plugins.install_checks import migration_tables
from tiqora.plugins.migrations import migrations_dir
from tiqora.plugins.number_generators import (
    SETTING_KEY as NUMBER_GENERATOR_SETTING,
)
from tiqora.plugins.number_generators import (
    TICKET_NUMBER_GENERATORS,
    active_generator_in,
    build_example,
)
from tiqora.plugins.packages_store import RUNTIME_SOURCES
from tiqora.plugins.repositories import UpdateInfo, available_updates
from tiqora.plugins.runtime import (
    RuntimeActivation,
    activate_runtime_plugins_in,
    plugins_dir_status,
)
from tiqora.plugins.signing import TrustLevelName
from tiqora.plugins.state import (
    PLUGIN_ROLES,
    PluginStatus,
    RuntimeState,
    is_active,
    summarize_status,
)
from tiqora.plugins.sync_hooks import SYNC_HOOKS
from tiqora.plugins.tasks import SCHEDULED_TASKS
from tiqora.settings_sections.section import SETTINGS_SECTIONS
from tiqora.znuny.sysconfig import SysConfig
from tiqora.znuny.ticket_number import generator_short_name, is_date_based

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/plugins", tags=["admin:plugins"])

RESTART_MESSAGE: Final = (
    "Änderung wird nach einem Neustart von Tiqora wirksam. Sie können Tiqora oben neu starten."
)
#: used when the coordinated restart is switched off (the admin has to restart by hand)
RESTART_MANUAL_MESSAGE: Final = "Änderung wird nach einem Neustart von Tiqora wirksam."
QUARANTINE_MESSAGE: Final = (
    "Beim Start wiederholt abgestürzt – wird übersprungen, bis Sie „Erneut versuchen“ wählen."
)
UNKNOWN_MESSAGE: Final = "Noch nicht gestartet."
GONE_MESSAGE: Final = "Nicht mehr im Image enthalten"
SAFE_MODE_MESSAGE: Final = "Abgesicherter Modus: nicht geladen"
_ROLE_LABELS: Final = {
    "api": "Weboberfläche und Schnittstelle",
    "worker": "Hintergrunddienst",
    "mcp": "KI-Schnittstelle (MCP)",
    "ai-worker": "KI-Hintergrunddienst",
}


class PluginRuntimeOut(BaseModel):
    role: Literal["api", "worker", "mcp", "ai-worker"]
    state: str
    version: str | None
    error: str | None
    boot_attempts: int
    updated_at: UtcDateTime


class PluginHookOut(BaseModel):
    key: str
    kind: Literal["event", "task", "sync"]
    name: str
    schedule: str | None
    tripped: bool
    consecutive_failures: int
    total_failures: int
    last_error: str | None
    last_error_at: UtcDateTime | None
    last_ok_at: UtcDateTime | None
    last_run_at: UtcDateTime | None


class PluginCapabilityOut(BaseModel):
    kind: Literal["extension_point", "outbound_host", "frontend_slot", "table"]
    value: str


class PluginContributionOut(BaseModel):
    point_key: str
    key: str


class PluginUpdateOut(BaseModel):
    version: str
    repository_id: int
    repository_name: str
    #: baked into the image: information only ("bitte das Image neu bauen")
    image_plugin: bool


class PluginAdminOut(BaseModel):
    id: str
    name: str
    version: str
    publisher: str
    description: str
    license: str | None
    homepage: str | None
    support: str | None
    categories: list[str]
    enabled: bool
    status: PluginStatus
    status_message: str | None
    source: str
    installed_at: UtcDateTime | None
    runtime: list[PluginRuntimeOut]
    hooks: list[PluginHookOut]
    capabilities: list[PluginCapabilityOut]
    contributions: list[PluginContributionOut]
    settings_section: str | None
    #: trust level of the installed package; None for plugins baked into the image
    signature_status: TrustLevelName | None
    signer: str | None
    #: runtime source, or an image row whose plugin is gone from the image
    can_uninstall: bool
    migration_error: str | None
    update: PluginUpdateOut | None
    #: package source of a repository plugin (None when the repository was deleted)
    repository_id: int | None
    repository_name: str | None


class PluginAdminListOut(BaseModel):
    safe_mode: bool
    plugins: list[PluginAdminOut]
    #: runtime install is possible here (plugins directory usable and writable in the api)
    runtime_install: bool
    runtime_install_reason: str | None
    allow_unsigned: bool
    max_package_mb: int
    coordinated_restart: bool


class PluginEnabledIn(BaseModel):
    enabled: bool


def _runtime_state(row: TiqoraPluginRuntime | None) -> RuntimeState | None:
    if row is None:
        return None
    try:
        return RuntimeState(row.state)
    except ValueError:
        return None


def _status_message(
    state: PluginStatus,
    *,
    problem: str | None,
    compat_problem: str | None,
    runtime: Sequence[TiqoraPluginRuntime],
    coordinated: bool = True,
) -> str | None:
    if state == "invalid":
        return problem
    if state == "incompatible":
        return compat_problem
    if state == "restart_required":
        return RESTART_MESSAGE if coordinated else RESTART_MANUAL_MESSAGE
    if state == "unknown":
        return f"{_ROLE_LABELS['api']}: {UNKNOWN_MESSAGE}"
    if state == "quarantined":
        for row in runtime:
            if row.state == RuntimeState.QUARANTINED.value:
                return f"{_ROLE_LABELS.get(row.role, row.role)}: {QUARANTINE_MESSAGE}"
    if state == "failed":
        for row in runtime:
            if row.state == RuntimeState.FAILED.value:
                label = _ROLE_LABELS.get(row.role, row.role)
                return f"{label}: {row.error or 'Start fehlgeschlagen'}"
    return None


_TrustLevel = TrustLevelName
_TRUST_LEVELS: Final[tuple[_TrustLevel, ...]] = (
    "official",
    "verified_publisher",
    "local",
    "unsigned",
    "invalid",
)


def _signature_status(plugin_id: str, row: TiqoraPlugin | None) -> _TrustLevel:
    """Trust level of a runtime package; unknown or missing data reads as unsigned."""
    value = row.signature_status if row is not None else None
    for level in _TRUST_LEVELS:
        if value == level:
            return level
    log.warning("plugin_signature_status_unknown", plugin=plugin_id, value=value)
    return "unsigned"


def _image_tables(found: DiscoveredPlugin | None) -> list[str]:
    """Tables created by the migrations of a plugin shipped in the image."""
    path = migrations_dir(found) if found is not None else None
    if path is None:
        return []
    files: dict[str, bytes] = {}
    try:
        for item in sorted(path.iterdir()):
            if item.suffix == ".py" and item.is_file():
                files[f"migrations/{item.name}"] = item.read_bytes()
    except OSError:
        return []
    return migration_tables(files)


def _report_tables(report_json: str) -> list[str] | None:
    try:
        report = PackageReport.model_validate_json(report_json)
    except ValueError:
        return None
    return sorted(c.value for c in report.capabilities if c.kind == "table")


def _hook_out(hook: TiqoraPluginHook, schedule: str | None) -> PluginHookOut:
    prefix = f"{hook.kind}:{hook.plugin_id}:"
    name = hook.hook_key.removeprefix(prefix)
    return PluginHookOut(
        key=hook.hook_key,
        kind="task" if hook.kind == "task" else "sync" if hook.kind == "sync" else "event",
        name=name,
        schedule=schedule,
        tripped=hook.tripped_at is not None,
        consecutive_failures=hook.consecutive_failures,
        total_failures=hook.total_failures,
        last_error=hook.last_error,
        last_error_at=hook.last_error_at,
        last_ok_at=hook.last_ok_at,
        last_run_at=hook.last_run_at,
    )


def _build_one(
    plugin_id: str,
    found: DiscoveredPlugin | None,
    row: TiqoraPlugin | None,
    runtime: Sequence[TiqoraPluginRuntime],
    hooks: Sequence[TiqoraPluginHook],
    *,
    safe_mode: bool,
    activation_problem: str | None = None,
    coordinated: bool = True,
    update: UpdateInfo | None = None,
    package_tables: Sequence[str] | None = None,
    repository_names: Mapping[int, str] | None = None,
) -> PluginAdminOut:
    manifest = found.manifest if found is not None else None
    repository_id = row.repository_id if row is not None and row.source == "repository" else None
    registry = get_registry()
    problem = found.problem if found is not None else (activation_problem or GONE_MESSAGE)
    compat_problem = (
        check_compat(manifest, core=release_core_version()) if manifest is not None else None
    )
    by_role = {r.role: r for r in runtime}
    ordered_rt = [by_role[r] for r in PLUGIN_ROLES if r in by_role]
    enabled = row.enabled if row is not None else False
    state = summarize_status(
        enabled=enabled,
        problem=problem,
        api_state=_runtime_state(by_role.get("api")),
        compat_problem=compat_problem,
        other_states=[
            s for r in ordered_rt if r.role != "api" if (s := _runtime_state(r)) is not None
        ],
    )
    safe_mode_hides = safe_mode and enabled and state not in ("invalid", "incompatible")
    if safe_mode_hides:  # R-PLUGINS-SAFEMODE-STATUS: nothing is loaded, "Aktiv" would mislead
        state = "unknown"

    capabilities: list[PluginCapabilityOut] = []
    if manifest is not None:
        caps = manifest.capabilities
        capabilities += [
            PluginCapabilityOut(kind="extension_point", value=v)
            for v in sorted(caps.extension_points)
        ]
        capabilities += [
            PluginCapabilityOut(kind="outbound_host", value=v) for v in sorted(caps.outbound_hosts)
        ]
        capabilities += [
            PluginCapabilityOut(kind="frontend_slot", value=v) for v in sorted(caps.frontend_slots)
        ]
    tables = package_tables if package_tables is not None else _image_tables(found)
    capabilities += [PluginCapabilityOut(kind="table", value=v) for v in tables]
    runtime_source = row is not None and row.source in RUNTIME_SOURCES
    contributions = sorted(
        (
            PluginContributionOut(point_key=reg.point_key, key=reg.key)
            for reg in registry.registrations()
            if reg.owner == plugin_id
        ),
        key=lambda c: (c.point_key, c.key),
    )
    section_key = f"plugins.{plugin_id}"
    settings_section = (
        section_key
        if any(
            r.owner == plugin_id and r.key == section_key
            for r in registry.registrations(SETTINGS_SECTIONS)
        )
        else None
    )
    hook_rows = []
    for hook in sorted(hooks, key=lambda h: h.hook_key):
        schedule: str | None = None
        if hook.kind == "task":
            task = registry.get(SCHEDULED_TASKS, hook.hook_key.removeprefix("task:"))
            schedule = task.schedule.describe() if task is not None else None
        elif hook.kind == "sync":  # the "schedule" of a sync hook is its operation
            sync = registry.get(SYNC_HOOKS, hook.hook_key.removeprefix("sync:"))
            schedule = sync.operation if sync is not None else None
        hook_rows.append(_hook_out(hook, schedule))

    return PluginAdminOut(
        id=plugin_id,
        name=(manifest.name if manifest else (row.name if row else None))
        or (found.dist_name if found else "")
        or plugin_id,
        version=manifest.version
        if manifest
        else (row.version if row else (found.dist_version if found else "")),
        publisher=manifest.publisher if manifest else "",
        description=manifest.description if manifest else "",
        license=manifest.license if manifest else None,
        homepage=manifest.homepage if manifest else None,
        support=manifest.support if manifest else None,
        categories=sorted(manifest.categories) if manifest else [],
        enabled=enabled,
        status=state,
        status_message=SAFE_MODE_MESSAGE
        if safe_mode_hides
        else _status_message(
            state,
            problem=problem,
            compat_problem=compat_problem,
            runtime=ordered_rt,
            coordinated=coordinated,
        ),
        source=row.source if row is not None else "image",
        installed_at=row.installed_at if row is not None else None,
        runtime=[
            PluginRuntimeOut(
                role=r.role,  # type: ignore[arg-type]  # ordered_rt only holds PLUGIN_ROLES
                state=r.state,
                version=r.version,
                error=r.error,
                boot_attempts=r.boot_attempts,
                updated_at=r.updated_at,
            )
            for r in ordered_rt
        ],
        hooks=hook_rows,
        capabilities=capabilities,
        contributions=contributions,
        settings_section=settings_section,
        signature_status=_signature_status(plugin_id, row) if runtime_source else None,
        signer=row.signer if runtime_source and row is not None else None,
        can_uninstall=runtime_source
        or (row is not None and row.source == "image" and found is None),
        migration_error=row.migration_error if row is not None else None,
        update=PluginUpdateOut(
            version=update.version,
            repository_id=update.repository_id,
            repository_name=update.repository_name,
            image_plugin=update.image_plugin,
        )
        if update is not None
        else None,
        repository_id=repository_id,
        repository_name=(repository_names or {}).get(repository_id)
        if repository_id is not None
        else None,
    )


async def _activate_for_list(session: AsyncSession, settings: Settings) -> RuntimeActivation:
    """Paths only (no import, unpacking or writes); also finds a crashing plugin in safe mode."""
    try:
        return await activate_runtime_plugins_in(
            session, settings, materialize=False, status_only=True
        )
    except Exception as exc:  # noqa: BLE001 - the list must still render
        log.warning("plugin_runtime_activation_failed", error=f"{type(exc).__name__}: {exc}"[:300])
        await session.rollback()
        return RuntimeActivation()


async def _updates_for_list(session: AsyncSession, settings: Settings) -> dict[str, UpdateInfo]:
    try:
        return await available_updates(session, settings=settings)
    except Exception as exc:  # noqa: BLE001 - the list must still render
        log.warning("plugin_updates_failed", error=f"{type(exc).__name__}: {exc}"[:300])
        await session.rollback()
        return {}


async def _package_tables(
    session: AsyncSession, rows: Iterable[TiqoraPlugin]
) -> dict[str, list[str]]:
    """plugin id -> tables from the stored package report (runtime plugins only)."""
    shas = {
        r.package_sha256: r.id
        for r in rows
        if r.source in RUNTIME_SOURCES and r.package_sha256 is not None
    }
    if not shas:
        return {}
    found = await session.execute(
        select(TiqoraPluginPackage.sha256, TiqoraPluginPackage.report).where(
            TiqoraPluginPackage.sha256.in_(list(shas))
        )
    )
    out: dict[str, list[str]] = {}
    for sha, report_json in found.all():
        tables = _report_tables(report_json)
        if tables is not None:
            out[shas[sha]] = tables
    return out


async def _repository_names(session: AsyncSession, rows: Iterable[TiqoraPlugin]) -> dict[int, str]:
    """repository id -> name for the repository plugins (an IN list, no join)."""
    ids = sorted({r.repository_id for r in rows if r.repository_id is not None})
    if not ids:
        return {}
    found = await session.execute(
        select(TiqoraPluginRepository.id, TiqoraPluginRepository.name).where(
            TiqoraPluginRepository.id.in_(ids)
        )
    )
    return {int(rid): str(name) for rid, name in found.all()}


async def _build_all(
    session: AsyncSession, *, safe_mode: bool, settings: Settings
) -> list[PluginAdminOut]:
    activation = await _activate_for_list(session, settings)
    updates = await _updates_for_list(session, settings)  # before loading rows: may roll back
    discovered: Mapping[str, DiscoveredPlugin] = {p.plugin_id: p for p in discovered_plugins()}
    rows = await store.list_plugin_rows(session)
    package_tables = await _package_tables(session, rows.values())
    repository_names = await _repository_names(session, rows.values())
    runtime: dict[str, list[TiqoraPluginRuntime]] = defaultdict(list)
    for rt in await store.list_runtime_rows(session):
        runtime[rt.plugin_id].append(rt)
    hooks: dict[str, list[TiqoraPluginHook]] = defaultdict(list)
    for hook in (await store.load_hooks(session)).values():
        hooks[hook.plugin_id].append(hook)
    out = [
        _build_one(
            pid,
            discovered.get(pid),
            rows.get(pid),
            runtime[pid],
            hooks[pid],
            safe_mode=safe_mode,
            activation_problem=activation.problems.get(pid),
            coordinated=settings.plugins_coordinated_restart,
            update=updates.get(pid),
            package_tables=package_tables.get(pid),
            repository_names=repository_names,
        )
        for pid in sorted(set(discovered) | set(rows))
    ]
    return sorted(out, key=lambda p: (p.name.lower(), p.id))


async def _one(
    session: AsyncSession, plugin_id: str, *, safe_mode: bool, settings: Settings
) -> PluginAdminOut:
    for plugin in await _build_all(session, safe_mode=safe_mode, settings=settings):
        if plugin.id == plugin_id:
            return plugin
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Erweiterung nicht gefunden")


class NumberGeneratorOut(BaseModel):
    key: str
    label: str
    plugin_id: str
    example: str | None


class NumberGeneratorsOut(BaseModel):
    active: str | None
    znuny_generator: str
    available: list[NumberGeneratorOut]


class NumberGeneratorIn(BaseModel):
    key: str | None = None


async def _number_generators_out(session: AsyncSession) -> NumberGeneratorsOut:
    sysconfig = SysConfig(session)
    znuny = generator_short_name(await sysconfig.ticket_number_generator())
    system_id = await sysconfig.system_id()
    active = await active_generator_in(session)
    generators = [
        gen
        for gen in sorted(
            get_registry().items(TICKET_NUMBER_GENERATORS), key=lambda g: (g.label.lower(), g.key)
        )
        if is_active(gen.plugin_id)
    ]
    # In parallel: each example is capped at 1 s, so the page takes ~1 s, not N x 1 s.
    examples = await asyncio.gather(*(build_example(g, system_id=system_id) for g in generators))
    available = [
        NumberGeneratorOut(key=gen.key, label=gen.label, plugin_id=gen.plugin_id, example=example)
        for gen, example in zip(generators, examples, strict=True)
    ]
    return NumberGeneratorsOut(
        active=active.key if active is not None else None,
        znuny_generator=znuny,
        available=available,
    )


@router.get("/number-generators", response_model=NumberGeneratorsOut)
async def get_number_generators(admin: AdminUser, session: DbSession) -> NumberGeneratorsOut:
    _ = admin
    return await _number_generators_out(session)


@router.put("/number-generators", response_model=NumberGeneratorsOut)
async def put_number_generator(
    body: NumberGeneratorIn, admin: AdminUser, session: DbSession
) -> NumberGeneratorsOut:
    if body.key is None or body.key == "":
        await set_setting(session, NUMBER_GENERATOR_SETTING, "")
        log.info("plugin_number_generator_changed", generator=None, by=admin.id)
        return await _number_generators_out(session)
    gen = next(
        (g for g in get_registry().items(TICKET_NUMBER_GENERATORS) if g.key == body.key), None
    )
    if gen is None or not is_active(gen.plugin_id):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Dieses Ticketnummer-Schema gibt es nicht oder seine Erweiterung ist nicht aktiv.",
        )
    znuny = await SysConfig(session).ticket_number_generator()
    if (
        gen.uses_counter
        and generator_short_name(znuny) != "Random"
        and gen.date_based != is_date_based(znuny)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Dieses Ticketnummer-Schema zählt "
            + ("täglich neu" if gen.date_based else "durchgehend")
            + f", das Znuny-Schema „{generator_short_name(znuny)}“ dagegen "
            + ("durchgehend" if gen.date_based else "täglich neu")
            + ". Beide teilen sich denselben Zähler; wählen Sie ein Schema mit gleicher Zählweise.",
        )
    await set_setting(session, NUMBER_GENERATOR_SETTING, gen.key)
    log.info("plugin_number_generator_changed", generator=gen.key, by=admin.id)
    return await _number_generators_out(session)


@router.get("", response_model=PluginAdminListOut)
async def list_plugins(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> PluginAdminListOut:
    _ = admin
    dir_status = plugins_dir_status(settings)
    usable = dir_status.usable and dir_status.writable
    return PluginAdminListOut(
        safe_mode=settings.plugins_safe_mode,
        plugins=await _build_all(session, safe_mode=settings.plugins_safe_mode, settings=settings),
        runtime_install=usable,
        runtime_install_reason=None if usable else dir_status.reason,
        allow_unsigned=settings.plugins_allow_unsigned,
        max_package_mb=settings.plugins_max_package_mb,
        coordinated_restart=settings.plugins_coordinated_restart,
    )


@router.put("/{plugin_id}/enabled", response_model=PluginAdminOut)
async def set_plugin_enabled(
    plugin_id: str,
    body: PluginEnabledIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> PluginAdminOut:
    found = next((p for p in discovered_plugins() if p.plugin_id == plugin_id), None)
    if found is not None:  # discovered but never booted: make sure the row exists
        await store.sync_discovered(session, [found], auto_enable=False, now=utc_now())
    row = await store.set_enabled(session, plugin_id, body.enabled, by=admin.id, now=utc_now())
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Erweiterung nicht gefunden")
    await session.commit()
    log.info("plugin_enabled_changed", plugin=plugin_id, enabled=body.enabled, by=admin.id)
    return await _one(session, plugin_id, safe_mode=settings.plugins_safe_mode, settings=settings)


@router.post("/{plugin_id}/reset", response_model=PluginAdminOut)
async def reset_plugin(
    plugin_id: str, admin: AdminUser, session: DbSession, settings: AppSettings
) -> PluginAdminOut:
    known = any(p.plugin_id == plugin_id for p in discovered_plugins()) or (
        plugin_id in await store.list_plugin_rows(session)
    )
    if not known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Erweiterung nicht gefunden")
    await store.reset_plugin(session, plugin_id, now=utc_now())
    await session.commit()
    log.info("plugin_reset", plugin=plugin_id, by=admin.id)
    return await _one(session, plugin_id, safe_mode=settings.plugins_safe_mode, settings=settings)
