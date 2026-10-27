"""Change-management administration — ``/api/v1/admin/itsm/change`` (admins only).

Templates and freeze windows here; CABs, risk questions and the legacy archive join this router.
Settings are edited through the generic settings-sections API (section ``itsm_change``).
``ChangeError`` is an ``HTTPException``: the answer is ``{"detail": {"code", ...}}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.itsm_change_schemas import (
    ChangeTemplateOut,
    FreezeWindowOut,
    freeze_out,
    template_out,
)
from tiqora.itsm.change.freeze import (
    FreezeIn,
    create_freeze,
    delete_freeze,
    get_freeze,
    list_freezes,
    update_freeze,
)
from tiqora.itsm.change.templates import (
    TemplateIn,
    create_template,
    delete_template,
    get_template,
    list_templates,
    update_template,
)

router = APIRouter(prefix="/itsm/change", tags=["admin:itsm-change"])


@router.get("/templates", response_model=list[ChangeTemplateOut])
async def get_templates(admin: AdminUser, session: DbSession) -> list[ChangeTemplateOut]:
    """All templates, valid or not."""
    _ = admin
    return [template_out(v) for v in await list_templates(session, include_invalid=True)]


@router.post("/templates", response_model=ChangeTemplateOut, status_code=status.HTTP_201_CREATED)
async def post_template(
    body: TemplateIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> ChangeTemplateOut:
    out = template_out(await create_template(session, settings, data=body, user_id=admin.id))
    await session.commit()
    return out


@router.get("/templates/{template_id}", response_model=ChangeTemplateOut)
async def get_template_detail(
    template_id: int, admin: AdminUser, session: DbSession
) -> ChangeTemplateOut:
    _ = admin
    return template_out(await get_template(session, template_id))


@router.put("/templates/{template_id}", response_model=ChangeTemplateOut)
async def put_template(
    template_id: int,
    body: TemplateIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> ChangeTemplateOut:
    """Replace the template. Changes created from it earlier keep what they got."""
    out = template_out(
        await update_template(
            session, settings, template_id=template_id, data=body, user_id=admin.id
        )
    )
    await session.commit()
    return out


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template_route(template_id: int, admin: AdminUser, session: DbSession) -> Response:
    """Delete the template; changes created from it stay (their ``template_id`` becomes empty)."""
    _ = admin
    await delete_template(session, template_id=template_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/freeze-windows", response_model=list[FreezeWindowOut])
async def get_freeze_windows(admin: AdminUser, session: DbSession) -> list[FreezeWindowOut]:
    """All freeze windows (Sperrzeiträume), valid or not; times are UTC."""
    _ = admin
    return [freeze_out(v) for v in await list_freezes(session)]


@router.post("/freeze-windows", response_model=FreezeWindowOut, status_code=status.HTTP_201_CREATED)
async def post_freeze_window(
    body: FreezeIn, admin: AdminUser, session: DbSession
) -> FreezeWindowOut:
    out = freeze_out(await create_freeze(session, data=body, user_id=admin.id))
    await session.commit()
    return out


@router.get("/freeze-windows/{freeze_id}", response_model=FreezeWindowOut)
async def get_freeze_window(
    freeze_id: int, admin: AdminUser, session: DbSession
) -> FreezeWindowOut:
    _ = admin
    return freeze_out(await get_freeze(session, freeze_id))


@router.put("/freeze-windows/{freeze_id}", response_model=FreezeWindowOut)
async def put_freeze_window(
    freeze_id: int, body: FreezeIn, admin: AdminUser, session: DbSession
) -> FreezeWindowOut:
    """Replace the window."""
    out = freeze_out(await update_freeze(session, freeze_id=freeze_id, data=body, user_id=admin.id))
    await session.commit()
    return out


@router.delete("/freeze-windows/{freeze_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_freeze_window(freeze_id: int, admin: AdminUser, session: DbSession) -> Response:
    _ = admin
    await delete_freeze(session, freeze_id=freeze_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
