"""Read-only admin endpoints: generic agent jobs and reference lists.

Ticket ACL CRUD lives in :mod:`tiqora.api.v1.admin.acl`. PostMaster filters
live in :mod:`tiqora.api.v1.admin.postmaster_filters` (full CRUD).
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    FollowUpPossibleOut,
    GenericAgentJobOut,
    StateTypeOut,
)
from tiqora.db.legacy.config import GenericAgentJobs
from tiqora.db.legacy.queue import FollowUpPossible
from tiqora.db.legacy.ticket import TicketStateType

router = APIRouter(tags=["admin:readonly"])

# Znuny ``valid`` list id — 1 == valid (matches agent reference endpoints).
_VALID = 1


@router.get("/state-types", response_model=list[StateTypeOut])
async def list_state_types(admin: AdminUser, session: DbSession) -> list[TicketStateType]:
    """Reference list for resolving a state's ``type_id`` to a name."""
    _ = admin
    result = await session.execute(select(TicketStateType).order_by(TicketStateType.id))
    return list(result.scalars().all())


# system-addresses list/CRUD lives entirely in :mod:`system_addresses`
# (paginated + valid filter). Queue/auto-response pickers use that endpoint
# with ``valid=valid&page_size=500``.


@router.get("/follow-up-possible", response_model=list[FollowUpPossibleOut])
async def list_follow_up_possible(admin: AdminUser, session: DbSession) -> list[FollowUpPossible]:
    """Valid follow-up options for the queue editor (possible / reject / new ticket)."""
    _ = admin
    result = await session.execute(
        select(FollowUpPossible)
        .where(FollowUpPossible.valid_id == _VALID)
        .order_by(FollowUpPossible.id)
    )
    return list(result.scalars().all())


@router.get("/generic-agent-jobs", response_model=list[GenericAgentJobOut])
async def list_generic_agent_jobs(admin: AdminUser, session: DbSession) -> list[GenericAgentJobOut]:
    _ = admin
    result = await session.execute(
        select(GenericAgentJobs).order_by(GenericAgentJobs.job_name, GenericAgentJobs.job_key)
    )
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in result.scalars().all():
        grouped[row.job_name][row.job_key].append(row.job_value or "")
    return [
        GenericAgentJobOut(job_name=name, settings=settings) for name, settings in grouped.items()
    ]


@router.get("/generic-agent-jobs/{job_name}", response_model=GenericAgentJobOut)
async def get_generic_agent_job(
    job_name: str, admin: AdminUser, session: DbSession
) -> GenericAgentJobOut:
    _ = admin
    result = await session.execute(
        select(GenericAgentJobs).where(GenericAgentJobs.job_name == job_name)
    )
    rows = list(result.scalars().all())
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    settings: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        settings[r.job_key].append(r.job_value or "")
    return GenericAgentJobOut(job_name=job_name, settings=dict(settings))
