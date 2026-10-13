"""Admin API for runtime plugin packages: upload, inspection, coordinated restart (D11).

Install and uninstall endpoints join this router in later tasks. The restart routes live here
so ``POST /plugins/restart`` is registered next to ``POST /plugins/{plugin_id}/uninstall``
(different suffix, no path clash).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Annotated, Final, Literal

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.uploads import read_upload_within_limit
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraRestart
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.schemas import UtcDateTime
from tiqora.jobs.service import utc_now
from tiqora.plugins import packages_store
from tiqora.plugins.inspect import (
    InstalledPlugin,
    PackageReport,
    inspect_read_package,
    installed_state,
    short_id_owners,
)
from tiqora.plugins.package import PackageError, read_tqplugin
from tiqora.plugins.repositories import build_key_ring_named, with_repository_signer
from tiqora.plugins.restart import (
    BUSY_MESSAGE,
    RestartStatusOut,
    plugin_job_active,
    request_restart,
    restart_status,
    utcnow,
)
from tiqora.plugins.signing import KeyRing

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/plugins", tags=["admin:plugins"])

DEFAULT_REASON: Final = "Manuell angefordert"
RUNNING_MESSAGE: Final = "Ein Neustart läuft bereits"
MANUAL_MESSAGE: Final = (
    "Der koordinierte Neustart ist abgeschaltet. Bitte starten Sie Tiqora selbst neu."
)
#: an unfinished request older than this many timeouts is stuck and may be superseded
STUCK_AFTER_TIMEOUTS: Final = 4


WEB_ONLY_MESSAGE: Final = "Nur in der Weboberfläche möglich"


async def get_web_admin(admin: AdminUser) -> AuthenticatedUser:
    """F13: a browser session is required; an API key is refused whatever its scope."""
    if admin.auth_method == "api_key":
        raise HTTPException(status.HTTP_403_FORBIDDEN, WEB_ONLY_MESSAGE)
    return admin


WebAdmin = Annotated[AuthenticatedUser, Depends(get_web_admin)]


class CapabilityLineOut(BaseModel):
    kind: Literal["extension_point", "outbound_host", "frontend_slot", "table"]
    value: str


class PluginPackageOut(BaseModel):
    sha256: str
    size_bytes: int
    plugin_id: str
    short_id: str
    name: str
    version: str
    publisher: str
    publisher_id: str | None
    description: str
    license: str | None
    homepage: str | None
    support: str | None
    categories: list[str]
    changelog: str | None
    trust: Literal["official", "verified_publisher", "local", "unsigned", "invalid"]
    key_id: str | None
    signer: str | None
    capabilities: list[CapabilityLineOut]
    problems: list[str]
    warnings: list[str]
    installed_version: str | None
    action: Literal["install", "update", "reinstall", "downgrade", "blocked_image"]
    installable: bool
    source: Literal["upload", "repository"]
    uploaded_at: UtcDateTime


def _package_out(
    report: PackageReport, *, source: str, uploaded_at: datetime, extra_problems: list[str]
) -> PluginPackageOut:
    data = report.model_dump(mode="json")
    data["problems"] = [*data["problems"], *extra_problems]
    data["installable"] = data["installable"] and not extra_problems
    return PluginPackageOut(**data, source=source, uploaded_at=uploaded_at)  # type: ignore[arg-type]


def _runtime_problem(settings: Settings) -> str | None:
    """German reason why nothing can be installed here, or None."""
    from tiqora.plugins.runtime import plugins_dir_status

    status_ = plugins_dir_status(settings)
    if not status_.usable or not status_.writable:
        return status_.reason or "Installation zur Laufzeit ist nicht möglich"
    return None


# F4: one inspection at a time per process, off the event loop.
_inspect_gate: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def _gate() -> asyncio.Semaphore:
    global _inspect_gate
    loop = asyncio.get_running_loop()
    if _inspect_gate is None or _inspect_gate[0] is not loop:
        _inspect_gate = (loop, asyncio.Semaphore(1))
    return _inspect_gate[1]


async def _evaluate(
    data: bytes,
    settings: Settings,
    ring: KeyRing,
    installed: dict[str, InstalledPlugin],
    images: frozenset[str],
    owners: dict[str, str],
) -> tuple[PackageReport, str]:
    """Read and inspect ``data`` in a worker thread; raises ``PackageError`` (-> 422)."""

    def work() -> tuple[PackageReport, str]:
        pkg = read_tqplugin(data, max_bytes=settings.plugins_max_package_bytes)
        report = inspect_read_package(
            pkg,
            settings=settings,
            ring=ring,
            installed=installed,
            image_plugin_ids=images,
            short_id_owners=owners,
        )
        return report, pkg.manifest_text

    async with _gate():
        return await asyncio.to_thread(work)


async def _context(
    session: AsyncSession, settings: Settings
) -> tuple[KeyRing, dict[int, str], dict[str, InstalledPlugin], frozenset[str], dict[str, str]]:
    ring, names = await build_key_ring_named(session, settings)
    installed, images = await installed_state(session, settings)
    owners = await short_id_owners(session)
    await session.rollback()  # end the read transaction before CPU work
    return ring, names, installed, images, owners


@router.post("/packages", response_model=PluginPackageOut, status_code=status.HTTP_201_CREATED)
async def upload_package(
    admin: WebAdmin,
    session: DbSession,
    settings: AppSettings,
    response: Response,
    file: Annotated[UploadFile, File()],
) -> PluginPackageOut:
    max_mb = settings.plugins_max_package_mb
    try:
        data = await read_upload_within_limit(file, settings.plugins_max_package_bytes)
    except HTTPException as exc:
        if exc.status_code == 413:
            raise HTTPException(413, f"Das Paket ist größer als {max_mb} MB") from None
        raise
    ring, names, installed, images, owners = await _context(session, settings)
    try:
        report, manifest_text = await _evaluate(data, settings, ring, installed, images, owners)
    except PackageError as exc:
        raise HTTPException(422, str(exc)) from None
    report = with_repository_signer(report, ring, names)
    runtime_problem = _runtime_problem(settings)
    existing = await packages_store.get_package(session, report.sha256)
    if existing is not None:
        source, uploaded_at = existing.source, existing.uploaded_at
        await session.rollback()
        response.status_code = status.HTTP_200_OK
    else:
        try:
            row = await packages_store.store_package(
                session,
                data=data,
                report=report,
                manifest_text=manifest_text,
                source="upload",
                repository_id=None,
                uploaded_by=admin.id,
                now=utc_now().replace(microsecond=0),  # MariaDB DATETIME has no fraction
            )
            source, uploaded_at = row.source, row.uploaded_at
            await session.commit()
        except packages_store.PackageTooLargeForDatabase as exc:
            await session.rollback()
            raise HTTPException(413, str(exc)) from None
        response.status_code = status.HTTP_201_CREATED
    return _package_out(
        report,
        source=source,
        uploaded_at=uploaded_at,
        extra_problems=[runtime_problem] if runtime_problem else [],
    )


@router.get("/packages/{sha256}", response_model=PluginPackageOut)
async def get_package(
    sha256: str, admin: AdminUser, session: DbSession, settings: AppSettings
) -> PluginPackageOut:
    _ = admin
    row = await packages_store.get_package(session, sha256)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paket nicht gefunden")
    source, uploaded_at = row.source, row.uploaded_at
    data = await packages_store.get_package_data(session, sha256)
    if data is None:  # pragma: no cover - deferred column of an existing row
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paket nicht gefunden")
    ring, names, installed, images, owners = await _context(session, settings)
    try:
        report, _manifest = await _evaluate(data, settings, ring, installed, images, owners)
    except PackageError as exc:
        raise HTTPException(422, str(exc)) from None
    report = with_repository_signer(report, ring, names)
    runtime_problem = _runtime_problem(settings)
    return _package_out(
        report,
        source=source,
        uploaded_at=uploaded_at,
        extra_problems=[runtime_problem] if runtime_problem else [],
    )


@router.delete("/packages/{sha256}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(sha256: str, admin: AdminUser, session: DbSession) -> Response:
    _ = admin
    if await packages_store.get_package(session, sha256) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paket nicht gefunden")
    if not await packages_store.delete_package(session, sha256):
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Das Paket ist installiert; bitte zuerst die Erweiterung deinstallieren",
        )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RestartIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@router.get("/restart", response_model=RestartStatusOut | None)
async def get_restart(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> RestartStatusOut | None:
    _ = admin
    return await restart_status(
        session,
        now=utcnow(),
        coordinated=settings.plugins_coordinated_restart,
        timeout_seconds=settings.plugins_restart_timeout_seconds,
    )


@router.post("/restart", response_model=RestartStatusOut, status_code=status.HTTP_201_CREATED)
async def post_restart(
    body: RestartIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> RestartStatusOut:
    if not settings.plugins_coordinated_restart:
        raise HTTPException(status.HTTP_409_CONFLICT, MANUAL_MESSAGE)
    if await plugin_job_active(session):
        raise HTTPException(status.HTTP_409_CONFLICT, BUSY_MESSAGE)
    now = utcnow()
    open_requested = (
        (
            await session.execute(
                select(TiqoraRestart.requested_at).where(
                    TiqoraRestart.finished_at.is_(None), TiqoraRestart.failed_message.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    stuck_before = now - timedelta(
        seconds=STUCK_AFTER_TIMEOUTS * settings.plugins_restart_timeout_seconds
    )
    if any(requested >= stuck_before for requested in open_requested):
        raise HTTPException(status.HTTP_409_CONFLICT, RUNNING_MESSAGE)
    generation = await request_restart(
        session,
        by=admin.id,
        reason=(body.reason or DEFAULT_REASON).strip() or DEFAULT_REASON,
        now=now,
        ensure_roles=("api",),
    )
    await session.commit()
    log.info("plugin_restart_requested", generation=generation, by=admin.id)
    out = await restart_status(
        session,
        now=now,
        coordinated=settings.plugins_coordinated_restart,
        timeout_seconds=settings.plugins_restart_timeout_seconds,
    )
    if out is None:  # pragma: no cover - the row was committed above
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Neustart nicht gefunden")
    return out
