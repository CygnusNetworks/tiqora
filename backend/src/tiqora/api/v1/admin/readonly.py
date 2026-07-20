"""Read-only admin endpoints: postmaster filters, ACLs, generic agent jobs.

ACL editing is explicitly deferred (config_match/config_change YAML shape is
complex and Znuny's ACL cache invalidation semantics need dedicated design
work) — only list/detail are implemented here.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.schemas import (
    AclOut,
    GenericAgentJobOut,
    PostmasterFilterOut,
    PostmasterFilterRuleOut,
    StateTypeOut,
)
from tiqora.db.legacy.config import Acl, GenericAgentJobs, PostmasterFilter
from tiqora.db.legacy.ticket import TicketStateType

router = APIRouter(tags=["admin:readonly"])


@router.get("/state-types", response_model=list[StateTypeOut])
async def list_state_types(admin: AdminUser, session: DbSession) -> list[TicketStateType]:
    """Reference list for resolving a state's ``type_id`` to a name."""
    _ = admin
    result = await session.execute(select(TicketStateType).order_by(TicketStateType.id))
    return list(result.scalars().all())


@router.get("/postmaster-filters", response_model=list[PostmasterFilterOut])
async def list_postmaster_filters(
    admin: AdminUser, session: DbSession
) -> list[PostmasterFilterOut]:
    _ = admin
    result = await session.execute(select(PostmasterFilter).order_by(PostmasterFilter.f_name))
    grouped: dict[str, list[PostmasterFilterRuleOut]] = defaultdict(list)
    for row in result.scalars().all():
        grouped[row.f_name].append(
            PostmasterFilterRuleOut(
                f_name=row.f_name,
                f_stop=row.f_stop,
                f_type=row.f_type,
                f_key=row.f_key,
                f_value=row.f_value,
                f_not=row.f_not,
            )
        )
    return [PostmasterFilterOut(name=name, rules=rules) for name, rules in grouped.items()]


@router.get("/postmaster-filters/{filter_name}", response_model=PostmasterFilterOut)
async def get_postmaster_filter(
    filter_name: str, admin: AdminUser, session: DbSession
) -> PostmasterFilterOut:
    _ = admin
    result = await session.execute(
        select(PostmasterFilter).where(PostmasterFilter.f_name == filter_name)
    )
    rows = list(result.scalars().all())
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filter not found")
    rules = [
        PostmasterFilterRuleOut(
            f_name=r.f_name,
            f_stop=r.f_stop,
            f_type=r.f_type,
            f_key=r.f_key,
            f_value=r.f_value,
            f_not=r.f_not,
        )
        for r in rows
    ]
    return PostmasterFilterOut(name=filter_name, rules=rules)


@router.get("/acl", response_model=list[AclOut])
async def list_acls(admin: AdminUser, session: DbSession) -> list[Acl]:
    _ = admin
    result = await session.execute(select(Acl).order_by(Acl.name))
    return list(result.scalars().all())


@router.get("/acl/{acl_id}", response_model=AclOut)
async def get_acl(acl_id: int, admin: AdminUser, session: DbSession) -> Acl:
    _ = admin
    row = await session.get(Acl, acl_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ACL not found")
    return row


@router.get("/generic-agent-jobs", response_model=list[GenericAgentJobOut])
async def list_generic_agent_jobs(admin: AdminUser, session: DbSession) -> list[GenericAgentJobOut]:
    _ = admin
    result = await session.execute(
        select(GenericAgentJobs).order_by(GenericAgentJobs.job_name, GenericAgentJobs.job_key)
    )
    grouped: dict[str, dict[str, str | None]] = defaultdict(dict)
    for row in result.scalars().all():
        grouped[row.job_name][row.job_key] = row.job_value
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
    settings = {r.job_key: r.job_value for r in rows}
    return GenericAgentJobOut(job_name=job_name, settings=settings)
