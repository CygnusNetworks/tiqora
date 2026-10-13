"""Admin API for plugin repositories (package sources), D11.

Listing syncs the environment-defined repositories first (D8). Creating, changing and removing
need a browser session (F13): a repository vouches for publisher keys, so an integration key must
never reach it. Refresh, catalog and download are here; refresh and download run as jobs.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Final, Literal

import structlog
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.plugin_packages import WebAdmin
from tiqora.db.tiqora.models import TiqoraPluginRepository
from tiqora.domain.schemas import UtcDateTime
from tiqora.jobs.schemas import JobAccepted
from tiqora.jobs.service import enqueue_job, utc_now
from tiqora.plugins import install_jobs
from tiqora.plugins.repositories import (
    NOT_IN_REPOSITORY,
    YANKED_MESSAGE,
    CatalogOut,
    RepositoryInputError,
    catalog,
    find_index_version,
    sync_env_repositories,
    url_key,
    validate_repository_input,
)
from tiqora.plugins.runtime import runtime_dir_problem

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/plugin-repositories", tags=["admin:plugins"])

NOT_FOUND: Final = "Paketquelle nicht gefunden"
ENV_READ_ONLY: Final = (
    "Diese Paketquelle kommt aus der Server-Konfiguration und ist hier schreibgeschützt"
)
DUPLICATE: Final = "Diese Paketquelle ist bereits eingerichtet"


class RepositoryOut(BaseModel):
    id: int
    name: str
    url: str
    public_key: str | None
    enabled: bool
    origin: Literal["admin", "env"]
    verified: bool
    repository_name: str | None
    plugin_count: int
    last_checked_at: UtcDateTime | None
    last_ok_at: UtcDateTime | None
    last_error: str | None


class RepositoryIn(BaseModel):
    name: str
    url: str
    public_key: str | None = None
    enabled: bool = True


def _index_summary(raw: str | None) -> tuple[str | None, int]:
    """Repository name and plugin count of a stored (already accepted) index."""
    if not raw:
        return None, 0
    try:
        data = json.loads(raw)
        repo = data.get("repository")
        name = repo.get("name") if isinstance(repo, dict) else None
        plugins = data.get("plugins")
        return (name if isinstance(name, str) else None), (
            len(plugins) if isinstance(plugins, list) else 0
        )
    except (ValueError, AttributeError):
        return None, 0


def _out(row: TiqoraPluginRepository) -> RepositoryOut:
    repository_name, plugin_count = _index_summary(row.index_json)
    return RepositoryOut(
        id=row.id,
        name=row.name,
        url=row.url,
        public_key=row.public_key,
        enabled=row.enabled,
        origin=row.origin,  # type: ignore[arg-type]
        verified=row.index_verified,
        repository_name=repository_name,
        plugin_count=plugin_count,
        last_checked_at=row.last_checked_at,
        last_ok_at=row.last_ok_at,
        last_error=row.last_error,
    )


def _validated(body: RepositoryIn) -> tuple[str, str, str | None]:
    try:
        return validate_repository_input(name=body.name, url=body.url, public_key=body.public_key)
    except RepositoryInputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


async def _get(session: AsyncSession, repo_id: int) -> TiqoraPluginRepository:
    row = await session.get(TiqoraPluginRepository, repo_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return row


async def _url_taken(session: AsyncSession, key: str, *, except_id: int | None = None) -> bool:
    found = (
        await session.execute(
            select(TiqoraPluginRepository.id).where(TiqoraPluginRepository.url_key == key)
        )
    ).scalar_one_or_none()
    return found is not None and found != except_id


def _now() -> datetime:
    return utc_now().replace(microsecond=0)  # MariaDB DATETIME has no fraction


@router.get("", response_model=list[RepositoryOut])
async def list_repositories(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> list[RepositoryOut]:
    _ = admin
    await sync_env_repositories(session, settings, _now())
    await session.commit()
    rows = (
        (await session.execute(select(TiqoraPluginRepository).order_by(TiqoraPluginRepository.id)))
        .scalars()
        .all()
    )
    return [_out(r) for r in rows]


# declared before the ``/{repo_id}`` routes
@router.get("/catalog", response_model=CatalogOut)
async def get_catalog(
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    q: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    repository_id: int | None = None,
) -> CatalogOut:
    """What the enabled repositories offer, from their stored indexes (no network access)."""
    _ = admin
    out = await catalog(
        session, settings=settings, q=q, category=category, repository_id=repository_id
    )
    await session.rollback()
    return out


@router.post("", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED)
async def create_repository(
    body: RepositoryIn, admin: WebAdmin, session: DbSession
) -> RepositoryOut:
    name, url, key = _validated(body)
    if await _url_taken(session, url_key(url)):
        raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE)
    now = _now()
    row = TiqoraPluginRepository(
        name=name,
        url=url,
        url_key=url_key(url),
        public_key=key,
        enabled=body.enabled,
        origin="admin",
        index_verified=False,
        created_at=now,
        created_by=admin.id,
        updated_at=now,
    )
    session.add(row)
    await _commit_unique(session)
    log.info("plugin_repository_created", repository=row.id)  # the URL may carry a token
    return _out(row)


async def _commit_unique(session: AsyncSession) -> None:
    """Commit; a concurrent request that stored the same address first is a 409, not a 500."""
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE) from None


@router.put("/{repo_id}", response_model=RepositoryOut)
async def update_repository(
    repo_id: int, body: RepositoryIn, admin: WebAdmin, session: DbSession
) -> RepositoryOut:
    _ = admin
    row = await _get(session, repo_id)
    if row.origin == "env":
        raise HTTPException(status.HTTP_409_CONFLICT, ENV_READ_ONLY)
    name, url, key = _validated(body)
    new_key = url_key(url)
    if new_key != row.url_key:
        if await _url_taken(session, new_key, except_id=row.id):
            raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE)
        # the stored index belongs to the old address
        row.url, row.url_key = url, new_key
        _forget_index(row)
    elif key != row.public_key:
        row.index_verified = False  # verified against the old key
    row.name, row.public_key, row.enabled = name, key, body.enabled
    row.updated_at = _now()
    await _commit_unique(session)
    return _out(row)


def _forget_index(row: TiqoraPluginRepository) -> None:
    fields: dict[str, Any] = {
        "index_json": None,
        "index_sha256": None,
        "index_generated_at": None,
        "index_verified": False,
        "last_checked_at": None,
        "last_ok_at": None,
        "last_error": None,
    }
    for attr, value in fields.items():
        setattr(row, attr, value)


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(repo_id: int, admin: WebAdmin, session: DbSession) -> Response:
    _ = admin
    row = await _get(session, repo_id)
    if row.origin == "env":
        raise HTTPException(status.HTTP_409_CONFLICT, ENV_READ_ONLY)
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{repo_id}/refresh", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def refresh_repository_endpoint(
    repo_id: int, admin: AdminUser, session: DbSession
) -> JobAccepted:
    """Queue a refresh of the repository's index; the UI polls ``GET /jobs/{id}``."""
    await _get(session, repo_id)
    job = await enqueue_job(
        session,
        kind=install_jobs.PLUGIN_REPOSITORY_REFRESH_KIND,
        payload=install_jobs.PluginRepositoryRefreshPayload(repository_id=repo_id),
        created_by=admin.id,
        dedupe_key=install_jobs.refresh_dedupe_key(repo_id),
    )
    await session.commit()
    return JobAccepted(job_id=job.id, status="running" if job.status == "running" else "queued")


class DownloadIn(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=190)
    version: str = Field(min_length=1, max_length=64)


@router.post(
    "/{repo_id}/download", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def download_from_repository(
    repo_id: int, body: DownloadIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> JobAccepted:
    """Queue the download of one release into the package store; the UI polls ``GET /jobs/{id}``.

    The result carries ``package_sha256`` for the install dialog (``GET /plugins/packages/…``).
    """
    row = await _get(session, repo_id)
    entry = find_index_version(row, body.plugin_id, body.version)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_IN_REPOSITORY)
    if entry.yanked:
        raise HTTPException(status.HTTP_409_CONFLICT, YANKED_MESSAGE)
    problem = runtime_dir_problem(settings)
    if problem is not None:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, problem)
    job = await enqueue_job(
        session,
        kind=install_jobs.PLUGIN_FETCH_KIND,
        payload=install_jobs.PluginFetchPayload(
            repository_id=repo_id, plugin_id=body.plugin_id, version=body.version
        ),
        created_by=admin.id,
        dedupe_key=install_jobs.fetch_dedupe_key(repo_id, body.plugin_id, body.version),
    )
    await session.commit()
    return JobAccepted(job_id=job.id, status="running" if job.status == "running" else "queued")
