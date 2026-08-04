"""Admin CRUD for services and service↔SLA links (Znuny ``service`` / ``service_sla``)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import SERVICE_CACHE_TYPES, invalidate_znuny_cache_types, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import ServiceCreate, ServiceOut, ServiceUpdate
from tiqora.db.legacy.queue import Service, ServiceSla

router = APIRouter(prefix="/services", tags=["admin:services"])


async def _sla_ids_for(session: DbSession, service_id: int) -> list[int]:
    rows = (
        await session.execute(select(ServiceSla.sla_id).where(ServiceSla.service_id == service_id))
    ).all()
    return [int(r[0]) for r in rows]


async def _set_slas(session: DbSession, service_id: int, sla_ids: list[int]) -> None:
    await session.execute(delete(ServiceSla).where(ServiceSla.service_id == service_id))
    for sla_id in sla_ids:
        session.add(ServiceSla(service_id=service_id, sla_id=int(sla_id)))


async def _to_out(session: DbSession, row: Service) -> ServiceOut:
    return ServiceOut(
        id=row.id,
        name=row.name,
        comments=row.comments,
        valid_id=row.valid_id,
        create_time=row.create_time,
        change_time=row.change_time,
        sla_ids=await _sla_ids_for(session, row.id),
    )


@router.get("", response_model=Page[ServiceOut])
async def list_services(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[ServiceOut]:
    _ = admin
    stmt = apply_valid_filter(select(Service), Service.valid_id, params.valid).order_by(
        Service.name
    )
    page = await paginate(session, ServiceOut, stmt, params)
    # paginate builds from ORM; re-hydrate sla_ids
    items: list[ServiceOut] = []
    for item in page.items:
        row = await session.get(Service, item.id)
        if row is not None:
            items.append(await _to_out(session, row))
    return Page(items=items, total=page.total, page=page.page, page_size=page.page_size)


@router.get("/{service_id}", response_model=ServiceOut)
async def get_service(service_id: int, admin: AdminUser, session: DbSession) -> ServiceOut:
    _ = admin
    row = await session.get(Service, service_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return await _to_out(session, row)


@router.post("", response_model=ServiceOut, status_code=status.HTTP_201_CREATED)
async def create_service(body: ServiceCreate, admin: AdminUser, session: DbSession) -> ServiceOut:
    ts = now()
    data = body.model_dump(exclude={"sla_ids"})
    row = Service(
        **data,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(row)
    await session.flush()
    await _set_slas(session, row.id, body.sla_ids)
    await invalidate_znuny_cache_types(session, SERVICE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return await _to_out(session, row)


@router.patch("/{service_id}", response_model=ServiceOut)
async def update_service(
    service_id: int, body: ServiceUpdate, admin: AdminUser, session: DbSession
) -> ServiceOut:
    row = await session.get(Service, service_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    data = body.model_dump(exclude_unset=True)
    sla_ids = data.pop("sla_ids", None)
    for field, value in data.items():
        setattr(row, field, value)
    if sla_ids is not None:
        await _set_slas(session, service_id, sla_ids)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SERVICE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return await _to_out(session, row)


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_service(service_id: int, admin: AdminUser, session: DbSession) -> None:
    row = await session.get(Service, service_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SERVICE_CACHE_TYPES)
    await session.commit()
