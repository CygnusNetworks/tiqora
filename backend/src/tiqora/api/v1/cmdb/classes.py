"""Agent API: CMDB status, readable classes with their current definition, catalog items."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.api.v1.cmdb.deps import Ctx, cmdb_http_error
from tiqora.cmdb.access import load_access, readable_class_ids, require_class_access
from tiqora.cmdb.catalog import CatalogItem, CatalogService
from tiqora.cmdb.classes import ClassRecord, ClassService, DefinitionNotFound, DefinitionRecord
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.gate import current_itsm_mode
from tiqora.cmdb.schemas import (
    CatalogItemOut,
    CmdbClassOut,
    CmdbDefinitionOut,
    CmdbStatusOut,
)
from tiqora.cmdb.views import views_present
from tiqora.db.legacy.user import PermissionGroups

#: ungated: the frontend asks it to decide whether to show the CMDB at all
status_router = APIRouter()
router = APIRouter()


def definition_out(rec: DefinitionRecord) -> CmdbDefinitionOut:
    return CmdbDefinitionOut(
        id=rec.id,
        class_id=rec.class_id,
        version=rec.version,
        attributes=rec.attributes,
        comment=rec.comment,
        created_by=rec.created_by,
        created_at=rec.created_at,
    )


def catalog_out(item: CatalogItem) -> CatalogItemOut:
    return CatalogItemOut(
        id=item.id,
        catalog_class=item.catalog_class,
        item_key=item.item_key,
        name=item.name,
        functionality=item.functionality,
        position=item.position,
        valid=item.valid,
    )


async def group_names(session: AsyncSession, group_ids: set[int]) -> dict[int, str]:
    """Znuny group names by id (separate IN query: never JOINed with ``tiqora_*``)."""
    if not group_ids:
        return {}
    rows = await session.execute(
        select(PermissionGroups.id, PermissionGroups.name).where(
            PermissionGroups.id.in_(sorted(group_ids))
        )
    )
    return {int(i): str(n) for i, n in rows.all()}


async def build_class_out(
    svc: ClassService,
    rec: ClassRecord,
    *,
    group_name: str | None,
    can_write: bool,
    with_definition: bool,
) -> CmdbClassOut:
    definition: DefinitionRecord | None = None
    if rec.current_definition_id is not None:
        definition = await svc.definition(rec.current_definition_id)
    return CmdbClassOut(
        id=rec.id,
        class_key=rec.class_key,
        name=rec.name,
        icon=rec.icon,
        number_prefix=rec.number_prefix,
        group_id=rec.group_id,
        group_name=group_name,
        portal_visible=rec.portal_visible,
        valid=rec.valid,
        position=rec.position,
        lock_version=rec.lock_version,
        current_definition_version=None if definition is None else definition.version,
        definition=definition_out(definition) if (with_definition and definition) else None,
        can_write=can_write,
    )


@status_router.get("/status", response_model=CmdbStatusOut)
async def cmdb_status(session: DbSession, user: CurrentUser) -> CmdbStatusOut:
    mode = await current_itsm_mode(session)
    access = await load_access(session, user.id)
    count = len(await readable_class_ids(session, access)) if mode == "tiqora" else 0
    return CmdbStatusOut(
        mode=mode,
        readable_class_count=count,
        can_admin=access.is_admin,
        views_present=await views_present(session),
    )


@router.get("/classes", response_model=list[CmdbClassOut])
async def list_classes(ctx: Ctx) -> list[CmdbClassOut]:
    svc = ClassService(ctx.session)
    readable = set(await readable_class_ids(ctx.session, ctx.access))
    records = [r for r in await svc.list() if r.id in readable]
    names = await group_names(ctx.session, {r.group_id for r in records})
    return [
        await build_class_out(
            svc,
            r,
            group_name=names.get(r.group_id),
            can_write=ctx.access.can_write_group(r.group_id),
            with_definition=True,
        )
        for r in records
    ]


@router.get("/classes/{class_id}/definitions/{version}", response_model=CmdbDefinitionOut)
async def get_definition(class_id: int, version: int, ctx: Ctx) -> CmdbDefinitionOut:
    try:
        await require_class_access(ctx.session, ctx.access, class_id, "ro")
        for rec in await ClassService(ctx.session).definitions(class_id):
            if rec.version == version:
                return definition_out(rec)
        raise DefinitionNotFound(f"class {class_id} version {version}")
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc


@router.get("/catalog", response_model=list[CatalogItemOut])
async def list_catalog(
    ctx: Ctx, catalog_class: Annotated[str, Query(min_length=1, max_length=100)]
) -> list[CatalogItemOut]:
    items = await CatalogService(ctx.session).items(catalog_class)
    return [catalog_out(i) for i in items]


__all__ = [
    "build_class_out",
    "catalog_out",
    "definition_out",
    "group_names",
    "router",
    "status_router",
]
