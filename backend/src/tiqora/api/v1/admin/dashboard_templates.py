"""Admin API for role dashboard templates (D3, admin block).

Mounted at ``/api/v1/admin/dashboard-templates`` so it inherits ``AdminUser`` and the
``admin`` API-key area (R-DASH-DEVIATIONS). A template's layout is saved with
``PUT /api/v1/dashboards/{id}`` by an admin; this router owns only its properties.
Templates reach role members only once ``locked`` (read-only tab) or as the first-visit
copy source (unlocked).
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Query, Response, status

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.dashboard import store
from tiqora.dashboard.schemas import DashboardTemplateIn, DashboardTemplateOut

router = APIRouter(prefix="/dashboard-templates", tags=["admin:dashboard-templates"])

NOT_FOUND_MESSAGE = "Vorlage nicht gefunden."


async def _fail(session: DbSession, exc: Exception) -> NoReturn:
    await session.rollback()
    if isinstance(exc, store.DashboardNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND_MESSAGE) from exc
    if isinstance(exc, store.DashboardConflict):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": exc.message, "current_version": exc.current_version},
        ) from exc
    if isinstance(exc, store.DashboardInvalid):
        raise HTTPException(422, {"message": exc.message, "path": exc.path}) from exc
    raise exc


@router.get("", response_model=list[DashboardTemplateOut])
async def list_templates(admin: AdminUser, session: DbSession) -> list[DashboardTemplateOut]:
    _ = admin
    return await store.list_templates(session)


@router.post("", response_model=DashboardTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    admin: AdminUser, session: DbSession, body: DashboardTemplateIn
) -> DashboardTemplateOut:
    try:
        out = await store.create_template(session, admin.id, body)
    except store.DashboardInvalid as exc:
        await _fail(session, exc)
    await session.commit()
    return out


@router.put("/{template_id}", response_model=DashboardTemplateOut)
async def update_template(
    admin: AdminUser,
    session: DbSession,
    template_id: int,
    body: DashboardTemplateIn,
    version: int = Query(..., ge=1, description="Optimistic lock: the version last read"),
) -> DashboardTemplateOut:
    _ = admin
    try:
        out = await store.update_template(session, template_id, body, version=version)
    except (store.DashboardInvalid, store.DashboardNotFound, store.DashboardConflict) as exc:
        await _fail(session, exc)
    await session.commit()
    return out


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(admin: AdminUser, session: DbSession, template_id: int) -> Response:
    _ = admin
    try:
        await store.delete_template(session, template_id)
    except store.DashboardNotFound as exc:
        await _fail(session, exc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
