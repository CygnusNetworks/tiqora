"""Admin CRUD for standard_attachment (Znuny Attachments master data)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import ATTACHMENT_CACHE_TYPES, invalidate_znuny_cache_types, now
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import (
    StandardAttachmentCreate,
    StandardAttachmentOut,
    StandardAttachmentUpdate,
    StandardTemplateOut,
)
from tiqora.db.legacy.queue import StandardAttachment, StandardTemplate, StandardTemplateAttachment

router = APIRouter(prefix="/attachments", tags=["admin:attachments"])


@router.get("", response_model=Page[StandardAttachmentOut])
async def list_attachments(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[StandardAttachmentOut]:
    _ = admin
    stmt = apply_valid_filter(
        select(StandardAttachment), StandardAttachment.valid_id, params.valid
    ).order_by(StandardAttachment.name)
    return await paginate(session, StandardAttachmentOut, stmt, params)


@router.get("/{attachment_id}", response_model=StandardAttachmentOut)
async def get_attachment(
    attachment_id: int, admin: AdminUser, session: DbSession
) -> StandardAttachment:
    _ = admin
    row = await session.get(StandardAttachment, attachment_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    return row


@router.post("", response_model=StandardAttachmentOut, status_code=status.HTTP_201_CREATED)
async def create_attachment(
    body: StandardAttachmentCreate, admin: AdminUser, session: DbSession
) -> StandardAttachment:
    ts = now()
    try:
        content = body.content_bytes()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="content must be valid base64",
        ) from exc
    row = StandardAttachment(
        name=body.name,
        content_type=body.content_type,
        content=content,
        filename=body.filename,
        comments=body.comments,
        valid_id=body.valid_id,
        create_time=ts,
        create_by=admin.id,
        change_time=ts,
        change_by=admin.id,
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, ATTACHMENT_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{attachment_id}", response_model=StandardAttachmentOut)
async def update_attachment(
    attachment_id: int,
    body: StandardAttachmentUpdate,
    admin: AdminUser,
    session: DbSession,
) -> StandardAttachment:
    row = await session.get(StandardAttachment, attachment_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    data = body.model_dump(exclude_unset=True)
    if "content" in data:
        try:
            content = body.content_bytes()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="content must be valid base64",
            ) from exc
        if content is not None:
            row.content = content
        del data["content"]
    for field, value in data.items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, ATTACHMENT_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_attachment(attachment_id: int, admin: AdminUser, session: DbSession) -> None:
    """Soft-invalidate (``valid_id = 2``) — Znuny never hard-deletes attachments."""
    row = await session.get(StandardAttachment, attachment_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, ATTACHMENT_CACHE_TYPES)
    await session.commit()


@router.get("/{attachment_id}/templates", response_model=list[StandardTemplateOut])
async def get_attachment_templates(
    attachment_id: int, admin: AdminUser, session: DbSession
) -> list[StandardTemplate]:
    """Templates currently using *attachment_id* — reverse of template↔attachments."""
    _ = admin
    att = await session.get(StandardAttachment, attachment_id)
    if att is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    result = await session.execute(
        select(StandardTemplate)
        .join(
            StandardTemplateAttachment,
            StandardTemplateAttachment.standard_template_id == StandardTemplate.id,
        )
        .where(StandardTemplateAttachment.standard_attachment_id == attachment_id)
        .order_by(StandardTemplate.name)
    )
    return list(result.scalars().all())
