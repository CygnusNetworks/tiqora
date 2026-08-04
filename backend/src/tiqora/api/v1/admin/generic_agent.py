"""Admin write API for GenericAgent jobs (Znuny ``generic_agent_jobs`` key/value).

List/detail remain available under the readonly routes for backward compatibility;
this module adds create / replace / delete.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import GENERIC_AGENT_CACHE_TYPES, invalidate_znuny_cache_types
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    GenericAgentJobOut,
    GenericAgentJobUpdate,
    GenericAgentJobWrite,
)
from tiqora.db.legacy.config import GenericAgentJobs

router = APIRouter(prefix="/generic-agent-jobs", tags=["admin:generic-agent"])


async def _load_job(session: DbSession, job_name: str) -> GenericAgentJobOut | None:
    result = await session.execute(
        select(GenericAgentJobs).where(GenericAgentJobs.job_name == job_name)
    )
    rows = list(result.scalars().all())
    if not rows:
        return None
    settings: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        settings[r.job_key].append(r.job_value or "")
    return GenericAgentJobOut(job_name=job_name, settings=dict(settings))


async def _write_settings(
    session: DbSession, job_name: str, settings: dict[str, list[str]]
) -> None:
    await session.execute(delete(GenericAgentJobs).where(GenericAgentJobs.job_name == job_name))
    for key, values in settings.items():
        for value in values:
            session.add(
                GenericAgentJobs(job_name=job_name, job_key=key, job_value=value)
            )


@router.put("", response_model=GenericAgentJobOut, status_code=status.HTTP_201_CREATED)
async def upsert_generic_agent_job(
    body: GenericAgentJobWrite, admin: AdminUser, session: DbSession
) -> GenericAgentJobOut:
    """Create or fully replace a job's settings under ``job_name``."""
    _ = admin
    await _write_settings(session, body.job_name, body.settings)
    await invalidate_znuny_cache_types(session, GENERIC_AGENT_CACHE_TYPES)
    await session.commit()
    loaded = await _load_job(session, body.job_name)
    assert loaded is not None
    return loaded


@router.patch("/{job_name}", response_model=GenericAgentJobOut)
async def update_generic_agent_job(
    job_name: str, body: GenericAgentJobUpdate, admin: AdminUser, session: DbSession
) -> GenericAgentJobOut:
    _ = admin
    existing = await _load_job(session, job_name)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    new_name = body.job_name or job_name
    settings = body.settings if body.settings is not None else existing.settings
    if new_name != job_name:
        await session.execute(
            delete(GenericAgentJobs).where(GenericAgentJobs.job_name == job_name)
        )
    await _write_settings(session, new_name, settings)
    await invalidate_znuny_cache_types(session, GENERIC_AGENT_CACHE_TYPES)
    await session.commit()
    loaded = await _load_job(session, new_name)
    assert loaded is not None
    return loaded


@router.delete("/{job_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_generic_agent_job(
    job_name: str, admin: AdminUser, session: DbSession
) -> None:
    _ = admin
    existing = await _load_job(session, job_name)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    await session.execute(delete(GenericAgentJobs).where(GenericAgentJobs.job_name == job_name))
    await invalidate_znuny_cache_types(session, GENERIC_AGENT_CACHE_TYPES)
    await session.commit()
