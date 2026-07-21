"""Admin CRUD for salutations, signatures, standard templates + queue assignment.

Auto-response templates live in :mod:`tiqora.api.v1.admin.auto_responses`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import (
    ATTACHMENT_CACHE_TYPES,
    SALUTATION_CACHE_TYPES,
    SIGNATURE_CACHE_TYPES,
    TEMPLATE_CACHE_TYPES,
    invalidate_znuny_cache_types,
    now,
)
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.pagination import ListParamsDep, Page, apply_valid_filter, paginate
from tiqora.api.v1.admin.schemas import (
    AttachmentRefOut,
    QueueOut,
    QueueTemplateAssignment,
    SalutationOut,
    SalutationUpdate,
    SalutationWrite,
    SignatureOut,
    SignatureUpdate,
    SignatureWrite,
    StandardTemplateCreate,
    StandardTemplateOut,
    StandardTemplateUpdate,
    TemplateAttachmentsReplace,
)
from tiqora.db.legacy.queue import (
    Queue,
    QueueStandardTemplate,
    Salutation,
    Signature,
    StandardAttachment,
    StandardTemplate,
    StandardTemplateAttachment,
)

router = APIRouter(tags=["admin:templates"])


# --- Salutations ------------------------------------------------------------


@router.get("/salutations", response_model=Page[SalutationOut])
async def list_salutations(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[SalutationOut]:
    _ = admin
    stmt = apply_valid_filter(select(Salutation), Salutation.valid_id, params.valid).order_by(
        Salutation.name
    )
    return await paginate(session, SalutationOut, stmt, params)


@router.get("/salutations/{salutation_id}", response_model=SalutationOut)
async def get_salutation(salutation_id: int, admin: AdminUser, session: DbSession) -> Salutation:
    _ = admin
    row = await session.get(Salutation, salutation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salutation not found")
    return row


@router.post("/salutations", response_model=SalutationOut, status_code=status.HTTP_201_CREATED)
async def create_salutation(
    body: SalutationWrite, admin: AdminUser, session: DbSession
) -> Salutation:
    ts = now()
    row = Salutation(
        **body.model_dump(), create_time=ts, create_by=admin.id, change_time=ts, change_by=admin.id
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, SALUTATION_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/salutations/{salutation_id}", response_model=SalutationOut)
async def update_salutation(
    salutation_id: int, body: SalutationUpdate, admin: AdminUser, session: DbSession
) -> Salutation:
    row = await session.get(Salutation, salutation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salutation not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SALUTATION_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/salutations/{salutation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_salutation(salutation_id: int, admin: AdminUser, session: DbSession) -> None:
    row = await session.get(Salutation, salutation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salutation not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SALUTATION_CACHE_TYPES)
    await session.commit()


# --- Signatures --------------------------------------------------------------


@router.get("/signatures", response_model=Page[SignatureOut])
async def list_signatures(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[SignatureOut]:
    _ = admin
    stmt = apply_valid_filter(select(Signature), Signature.valid_id, params.valid).order_by(
        Signature.name
    )
    return await paginate(session, SignatureOut, stmt, params)


@router.get("/signatures/{signature_id}", response_model=SignatureOut)
async def get_signature(signature_id: int, admin: AdminUser, session: DbSession) -> Signature:
    _ = admin
    row = await session.get(Signature, signature_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signature not found")
    return row


@router.post("/signatures", response_model=SignatureOut, status_code=status.HTTP_201_CREATED)
async def create_signature(body: SignatureWrite, admin: AdminUser, session: DbSession) -> Signature:
    ts = now()
    row = Signature(
        **body.model_dump(), create_time=ts, create_by=admin.id, change_time=ts, change_by=admin.id
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, SIGNATURE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/signatures/{signature_id}", response_model=SignatureOut)
async def update_signature(
    signature_id: int, body: SignatureUpdate, admin: AdminUser, session: DbSession
) -> Signature:
    row = await session.get(Signature, signature_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signature not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SIGNATURE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/signatures/{signature_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_signature(signature_id: int, admin: AdminUser, session: DbSession) -> None:
    row = await session.get(Signature, signature_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Signature not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, SIGNATURE_CACHE_TYPES)
    await session.commit()


# --- Standard templates + queue assignment -----------------------------------


@router.get("/templates", response_model=Page[StandardTemplateOut])
async def list_templates(
    admin: AdminUser, session: DbSession, params: ListParamsDep
) -> Page[StandardTemplateOut]:
    _ = admin
    stmt = apply_valid_filter(
        select(StandardTemplate), StandardTemplate.valid_id, params.valid
    ).order_by(StandardTemplate.name)
    return await paginate(session, StandardTemplateOut, stmt, params)


@router.get("/templates/{template_id}", response_model=StandardTemplateOut)
async def get_template(template_id: int, admin: AdminUser, session: DbSession) -> StandardTemplate:
    _ = admin
    row = await session.get(StandardTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return row


@router.post("/templates", response_model=StandardTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: StandardTemplateCreate, admin: AdminUser, session: DbSession
) -> StandardTemplate:
    ts = now()
    row = StandardTemplate(
        **body.model_dump(), create_time=ts, create_by=admin.id, change_time=ts, change_by=admin.id
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, TEMPLATE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/templates/{template_id}", response_model=StandardTemplateOut)
async def update_template(
    template_id: int, body: StandardTemplateUpdate, admin: AdminUser, session: DbSession
) -> StandardTemplate:
    row = await session.get(StandardTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, TEMPLATE_CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_template(template_id: int, admin: AdminUser, session: DbSession) -> None:
    row = await session.get(StandardTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    row.valid_id = 2
    row.change_time = now()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, TEMPLATE_CACHE_TYPES)
    await session.commit()


@router.get("/queues/{queue_id}/templates", response_model=list[StandardTemplateOut])
async def get_queue_templates(
    queue_id: int, admin: AdminUser, session: DbSession
) -> list[StandardTemplate]:
    """Templates currently assigned to *queue_id* (read side for the editor)."""
    _ = admin
    queue = await session.get(Queue, queue_id)
    if queue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue not found")
    result = await session.execute(
        select(StandardTemplate)
        .join(
            QueueStandardTemplate,
            QueueStandardTemplate.standard_template_id == StandardTemplate.id,
        )
        .where(QueueStandardTemplate.queue_id == queue_id)
        .order_by(StandardTemplate.name)
    )
    return list(result.scalars().all())


@router.get("/templates/{template_id}/queues", response_model=list[QueueOut])
async def get_template_queues(
    template_id: int, admin: AdminUser, session: DbSession
) -> list[Queue]:
    """Queues this template is assigned to (reverse read side)."""
    _ = admin
    tmpl = await session.get(StandardTemplate, template_id)
    if tmpl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    result = await session.execute(
        select(Queue)
        .join(QueueStandardTemplate, QueueStandardTemplate.queue_id == Queue.id)
        .where(QueueStandardTemplate.standard_template_id == template_id)
        .order_by(Queue.name)
    )
    return list(result.scalars().all())


@router.put("/queues/{queue_id}/templates", status_code=status.HTTP_204_NO_CONTENT)
async def assign_queue_template(
    queue_id: int, body: QueueTemplateAssignment, admin: AdminUser, session: DbSession
) -> None:
    existing = await session.get(QueueStandardTemplate, (queue_id, body.standard_template_id))
    ts = now()
    if existing is None:
        session.add(
            QueueStandardTemplate(
                queue_id=queue_id,
                standard_template_id=body.standard_template_id,
                create_time=ts,
                create_by=admin.id,
                change_time=ts,
                change_by=admin.id,
            )
        )
        await invalidate_znuny_cache_types(session, TEMPLATE_CACHE_TYPES)
        await session.commit()


@router.delete(
    "/queues/{queue_id}/templates/{standard_template_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_queue_template(
    queue_id: int, standard_template_id: int, admin: AdminUser, session: DbSession
) -> None:
    _ = admin
    existing = await session.get(QueueStandardTemplate, (queue_id, standard_template_id))
    if existing is not None:
        await session.delete(existing)
        await invalidate_znuny_cache_types(session, TEMPLATE_CACHE_TYPES)
        await session.commit()


# --- Template ↔ Attachment assignment (standard_template_attachment) ---------


@router.get("/templates/{template_id}/attachments", response_model=list[AttachmentRefOut])
async def get_template_attachments(
    template_id: int, admin: AdminUser, session: DbSession
) -> list[StandardAttachment]:
    """Attachments currently linked to *template_id* (ids/names for the editor)."""
    _ = admin
    tmpl = await session.get(StandardTemplate, template_id)
    if tmpl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    result = await session.execute(
        select(StandardAttachment)
        .join(
            StandardTemplateAttachment,
            StandardTemplateAttachment.standard_attachment_id == StandardAttachment.id,
        )
        .where(StandardTemplateAttachment.standard_template_id == template_id)
        .order_by(StandardAttachment.name)
    )
    return list(result.scalars().all())


@router.put("/templates/{template_id}/attachments", status_code=status.HTTP_204_NO_CONTENT)
async def replace_template_attachments(
    template_id: int,
    body: TemplateAttachmentsReplace,
    admin: AdminUser,
    session: DbSession,
) -> None:
    """Replace the full set of attachments linked to *template_id*.

    Deletes every ``standard_template_attachment`` row for the template, then
    inserts one row per id in ``attachment_ids`` (deduplicated, order-
    preserving). Mirrors the multi-select replace semantics the template
    attachment editor needs — single-add PUT alone cannot clear unchecked
    rows the way a replace body can.
    """
    tmpl = await session.get(StandardTemplate, template_id)
    if tmpl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    # Validate every attachment id exists before mutating the link table.
    wanted: list[int] = []
    seen: set[int] = set()
    for attachment_id in body.attachment_ids:
        if attachment_id in seen:
            continue
        seen.add(attachment_id)
        att = await session.get(StandardAttachment, attachment_id)
        if att is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Attachment {attachment_id} not found",
            )
        wanted.append(attachment_id)

    existing = await session.execute(
        select(StandardTemplateAttachment).where(
            StandardTemplateAttachment.standard_template_id == template_id
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)
    # Flush deletes before inserts so unique/FK constraints stay happy.
    await session.flush()

    ts = now()
    for attachment_id in wanted:
        session.add(
            StandardTemplateAttachment(
                standard_attachment_id=attachment_id,
                standard_template_id=template_id,
                create_time=ts,
                create_by=admin.id,
                change_time=ts,
                change_by=admin.id,
            )
        )
    # Template↔attachment links are read via StdAttachment list caches and
    # Queue template membership (StandardTemplate clears Queue on change).
    await invalidate_znuny_cache_types(session, (*TEMPLATE_CACHE_TYPES, *ATTACHMENT_CACHE_TYPES))
    await session.commit()
