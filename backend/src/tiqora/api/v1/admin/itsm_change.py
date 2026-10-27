"""Change-management administration — ``/api/v1/admin/itsm/change`` (admins only).

Templates here; CABs, risk questions, freeze windows and the legacy archive join this router.
Settings are edited through the generic settings-sections API (section ``itsm_change``).
``ChangeError`` is an ``HTTPException``: the answer is ``{"detail": {"code", ...}}``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.itsm_change_schemas import ChangeTemplateOut, template_out
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
