"""Admin CRUD for SLAs and SLA↔service links (Znuny ``sla`` / ``service_sla``)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import SLA_CACHE_TYPES, invalidate_znuny_cache_types, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import SlaCreate, SlaOut, SlaUpdate
from tiqora.db.legacy.queue import ServiceSla, Sla

router = APIRouter(prefix="/slas", tags=["admin:slas"])


async def _service_ids_for(session: DbSession, sla_id: int) -> list[int]:
    rows = (
        await session.execute(select(ServiceSla.service_id).where(ServiceSla.sla_id == sla_id))
    ).all()
    return [int(r[0]) for r in rows]


async def _set_services(session: DbSession, sla_id: int, service_ids: list[int]) -> None:
    await session.execute(delete(ServiceSla).where(ServiceSla.sla_id == sla_id))
    for service_id in service_ids:
        session.add(ServiceSla(service_id=int(service_id), sla_id=sla_id))


async def _to_out(session: DbSession, row: Sla) -> SlaOut:
    return SlaOut(
        id=row.id,
        name=row.name,
        calendar_name=row.calendar_name,
        first_response_time=row.first_response_time,
        first_response_notify=row.first_response_notify,
        update_time=row.update_time,
        update_notify=row.update_notify,
        solution_time=row.solution_time,
        solution_notify=row.solution_notify,
        comments=row.comments,
        valid_id=row.valid_id,
        create_time=row.create_time,
        change_time=row.change_time,
        service_ids=await _service_ids_for(session, row.id),
    )


@router.get("", response_model=Page[SlaOut])
async def list_slas(admin: AdminUser, session: DbSession, params: ListParamsDep) -> Page[SlaOut]:
    _ = admin
    stmt = apply_valid_filter(select(Sla), Sla.valid_id, params.valid).order_by(Sla.name)
    page = await paginate(session, SlaOut, stmt, params)
    items: list[SlaOut] = []
    for item in page.items:
        row = await session.get(Sla, item.id)
        if row is not None:
            items.append(await _to_out(session, row))
    return Page(items=items, total=page.total, page=page.page, page_size=page.page_size)


@router.get("/{sla_id}", response_model=SlaOut)
async def get_sla(sla_id: int, admin: AdminUser, session: DbSession) -> SlaOut:
    _ = admin
    row = await session.get(Sla, sla_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SLA not found")
    return await _to_out(session, row)


@router.post("", response_model=SlaOut, status_code=status.HTTP_201_CREATED)
async def create_sla(body: SlaCreate, admin: AdminUser, session: DbSession) -> SlaOut:
    ts = now()
    data = body.model_dump(exclude={"service_ids"})
    row = Sla(
        **data,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(row)
    await session.flush()
    await _set_services(session, row.id, body.service_ids)
    await invalidate_znuny_cache_types(session, SLA_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return await _to_out(session, row)


@router.patch("/{sla_id}", response_model=SlaOut)
async def update_sla(
    sla_id: int, body: SlaUpdate, admin: AdminUser, session: DbSession
) -> SlaOut:
    row = await session.get(Sla, sla_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SLA not found")
    data = body.model_dump(exclude_unset=True)
    service_ids = data.pop("service_ids", None)
    for field, value in data.items():
        setattr(row, field, value)
    if service_ids is not None:
        await _set_services(session, sla_id, service_ids)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SLA_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return await _to_out(session, row)


@router.delete("/{sla_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_sla(sla_id: int, admin: AdminUser, session: DbSession) -> None:
    row = await session.get(Sla, sla_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SLA not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SLA_CACHE_TYPES)
    await session.commit()
