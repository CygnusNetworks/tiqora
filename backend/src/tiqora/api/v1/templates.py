"""Agent-facing Standard Template editing.

Unlike the admin CRUD in :mod:`tiqora.api.v1.admin.templates` (admin-only),
these endpoints let a *non-admin* agent edit the templates they've been granted
via the per-template ACL (see :class:`TemplatePermissionService`). Managing the
ACL itself (who may edit which template) stays an admin action.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.api.v1.admin.common import (
    TEMPLATE_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.pagination import (
    ListParamsDep,
    Page,
    apply_valid_filter,
    window,
)
from tiqora.api.v1.admin.schemas import StandardTemplateOut, StandardTemplateUpdate
from tiqora.db.legacy.queue import StandardTemplate
from tiqora.domain.template_permission import TemplatePermissionService

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=Page[StandardTemplateOut])
async def list_editable_templates(
    user: CurrentUser, session: DbSession, params: ListParamsDep
) -> Page[StandardTemplateOut]:
    """Templates the calling agent is allowed to edit."""
    svc = TemplatePermissionService(session)
    editable = await svc.editable_template_ids(user.id)  # None = admin (all)
    if editable is not None and not editable:
        return Page(items=[], total=0, page=params.page, page_size=params.page_size)
    stmt = apply_valid_filter(
        select(StandardTemplate), StandardTemplate.valid_id, params.valid
    ).order_by(StandardTemplate.name)
    if editable is not None:
        stmt = stmt.where(StandardTemplate.id.in_(editable))
    rows, total = await window(session, stmt, params)
    items = [StandardTemplateOut.model_validate(r) for r in rows]
    return Page(items=items, total=total, page=params.page, page_size=params.page_size)


async def _load_editable(template_id: int, user_id: int, session: DbSession) -> StandardTemplate:
    svc = TemplatePermissionService(session)
    if not await svc.may_edit(user_id, template_id):
        # 404 (not 403) so agents can't probe which template ids exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    row = await session.get(StandardTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return row


@router.get("/{template_id}", response_model=StandardTemplateOut)
async def get_template(template_id: int, user: CurrentUser, session: DbSession) -> StandardTemplate:
    return await _load_editable(template_id, user.id, session)


@router.patch("/{template_id}", response_model=StandardTemplateOut)
async def update_template(
    template_id: int,
    body: StandardTemplateUpdate,
    user: CurrentUser,
    session: DbSession,
) -> StandardTemplate:
    row = await _load_editable(template_id, user.id, session)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = user.id
    await invalidate_znuny_cache_types(session, TEMPLATE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row
