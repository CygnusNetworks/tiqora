"""Admin view of installed plugins: status, capabilities, switch on/off, "try again" (P11).

The list is built from discovery (no plugin code is imported, so it also works in safe
mode), the database rows of all process roles and this process's registry.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Final, Literal

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginHook, TiqoraPluginRuntime
from tiqora.domain.schemas import UtcDateTime
from tiqora.extensions.core import get_registry
from tiqora.jobs.service import utc_now
from tiqora.plugins import store
from tiqora.plugins.compat import check_compat, release_core_version
from tiqora.plugins.discovery import DiscoveredPlugin, discovered_plugins
from tiqora.plugins.state import PLUGIN_ROLES, PluginStatus, RuntimeState, summarize_status
from tiqora.plugins.sync_hooks import SYNC_HOOKS
from tiqora.plugins.tasks import SCHEDULED_TASKS
from tiqora.settings_sections.section import SETTINGS_SECTIONS

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/plugins", tags=["admin:plugins"])

RESTART_MESSAGE: Final = "Änderung wird nach einem Neustart von Tiqora wirksam."
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
    kind: Literal["extension_point", "outbound_host", "frontend_slot"]
    value: str


class PluginContributionOut(BaseModel):
    point_key: str
    key: str


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


class PluginAdminListOut(BaseModel):
    safe_mode: bool
    plugins: list[PluginAdminOut]


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
) -> str | None:
    if state == "invalid":
        return problem
    if state == "incompatible":
        return compat_problem
    if state == "restart_required":
        return RESTART_MESSAGE
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
) -> PluginAdminOut:
    manifest = found.manifest if found is not None else None
    registry = get_registry()
    problem = found.problem if found is not None else GONE_MESSAGE
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
            state, problem=problem, compat_problem=compat_problem, runtime=ordered_rt
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
    )


async def _build_all(session: AsyncSession, *, safe_mode: bool) -> list[PluginAdminOut]:
    discovered: Mapping[str, DiscoveredPlugin] = {p.plugin_id: p for p in discovered_plugins()}
    rows = await store.list_plugin_rows(session)
    runtime: dict[str, list[TiqoraPluginRuntime]] = defaultdict(list)
    for rt in await store.list_runtime_rows(session):
        runtime[rt.plugin_id].append(rt)
    hooks: dict[str, list[TiqoraPluginHook]] = defaultdict(list)
    for hook in (await store.load_hooks(session)).values():
        hooks[hook.plugin_id].append(hook)
    out = [
        _build_one(
            pid, discovered.get(pid), rows.get(pid), runtime[pid], hooks[pid], safe_mode=safe_mode
        )
        for pid in sorted(set(discovered) | set(rows))
    ]
    return sorted(out, key=lambda p: (p.name.lower(), p.id))


async def _one(session: AsyncSession, plugin_id: str, *, safe_mode: bool) -> PluginAdminOut:
    for plugin in await _build_all(session, safe_mode=safe_mode):
        if plugin.id == plugin_id:
            return plugin
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Erweiterung nicht gefunden")


@router.get("", response_model=PluginAdminListOut)
async def list_plugins(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> PluginAdminListOut:
    _ = admin
    return PluginAdminListOut(
        safe_mode=settings.plugins_safe_mode,
        plugins=await _build_all(session, safe_mode=settings.plugins_safe_mode),
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
    return await _one(session, plugin_id, safe_mode=settings.plugins_safe_mode)


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
    return await _one(session, plugin_id, safe_mode=settings.plugins_safe_mode)
