"""Admin CRUD for Znuny ``acl_ticket_attribute_relations``."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.common import invalidate_znuny_cache_types
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.db.legacy.config import AclTicketAttributeRelations
from tiqora.domain.ticket_attribute_relations import parse_attribute_relations_csv

router = APIRouter(prefix="/ticket-attribute-relations", tags=["admin:ticket-attribute-relations"])

_CACHE_TYPES = ("TicketAttributeRelations", "ACLEditor_ACL")


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TicketAttributeRelationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    attribute_1: str
    attribute_2: str
    acl_data: str
    priority: int
    create_time: datetime | None = None
    change_time: datetime | None = None


class TicketAttributeRelationCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    acl_data: str = Field(min_length=1, description="CSV text: header Attr1;Attr2 then value rows")
    priority: int = Field(default=1, ge=1)


class TicketAttributeRelationUpdate(BaseModel):
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    acl_data: str | None = Field(default=None, min_length=1)
    priority: int | None = Field(default=None, ge=1)


@router.get("", response_model=list[TicketAttributeRelationOut])
async def list_ticket_attribute_relations(
    admin: AdminUser, session: DbSession
) -> list[AclTicketAttributeRelations]:
    _ = admin
    result = await session.execute(
        select(AclTicketAttributeRelations).order_by(
            AclTicketAttributeRelations.priority, AclTicketAttributeRelations.id
        )
    )
    return list(result.scalars().all())


@router.get("/{relation_id}", response_model=TicketAttributeRelationOut)
async def get_ticket_attribute_relation(
    relation_id: int, admin: AdminUser, session: DbSession
) -> AclTicketAttributeRelations:
    _ = admin
    row = await session.get(AclTicketAttributeRelations, relation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return row


@router.post("", response_model=TicketAttributeRelationOut, status_code=status.HTTP_201_CREATED)
async def create_ticket_attribute_relation(
    body: TicketAttributeRelationCreate, admin: AdminUser, session: DbSession
) -> AclTicketAttributeRelations:
    try:
        parsed = parse_attribute_relations_csv(body.acl_data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # Unique filename (Znuny UNIQUE INDEX acl_tar_filename).
    existing = (
        await session.execute(
            select(AclTicketAttributeRelations.id).where(
                AclTicketAttributeRelations.filename == body.filename.strip()
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="filename already exists")

    now = _utcnow_naive()
    uid = admin.id
    row = AclTicketAttributeRelations(
        filename=body.filename.strip(),
        attribute_1=parsed.attribute_1,
        attribute_2=parsed.attribute_2,
        acl_data=body.acl_data,
        priority=body.priority,
        create_time=now,
        create_by=uid,
        change_time=now,
        change_by=uid,
    )
    session.add(row)
    await invalidate_znuny_cache_types(session, _CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.patch("/{relation_id}", response_model=TicketAttributeRelationOut)
async def update_ticket_attribute_relation(
    relation_id: int,
    body: TicketAttributeRelationUpdate,
    admin: AdminUser,
    session: DbSession,
) -> AclTicketAttributeRelations:
    row = await session.get(AclTicketAttributeRelations, relation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.filename is not None:
        row.filename = body.filename.strip()
    if body.acl_data is not None:
        try:
            parsed = parse_attribute_relations_csv(body.acl_data)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        row.acl_data = body.acl_data
        row.attribute_1 = parsed.attribute_1
        row.attribute_2 = parsed.attribute_2
    if body.priority is not None:
        row.priority = body.priority
    row.change_time = _utcnow_naive()
    row.change_by = admin.id
    await invalidate_znuny_cache_types(session, _CACHE_TYPES)
    await session.commit()
    await session.refresh(row)
    return row


@router.delete("/{relation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket_attribute_relation(
    relation_id: int, admin: AdminUser, session: DbSession
) -> None:
    _ = admin
    row = await session.get(AclTicketAttributeRelations, relation_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(row)
    await invalidate_znuny_cache_types(session, _CACHE_TYPES)
    await session.commit()
