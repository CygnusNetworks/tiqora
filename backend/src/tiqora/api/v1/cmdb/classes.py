"""Agent API: CMDB status, readable classes with their current definition, catalog items."""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.api.v1.cmdb.deps import Ctx, cmdb_http_error
from tiqora.cmdb.access import (
    load_access,
    readable_class_ids,
    require_class_access,
    writable_class_ids,
)
from tiqora.cmdb.catalog import CatalogItem, CatalogService
from tiqora.cmdb.classes import ClassRecord, ClassService, DefinitionNotFound, DefinitionRecord
from tiqora.cmdb.definition import iter_attribute_paths
from tiqora.cmdb.errors import CmdbError, CmdbForbidden
from tiqora.cmdb.gate import current_itsm_mode
from tiqora.cmdb.schemas import (
    CatalogItemOut,
    CmdbClassOut,
    CmdbDefinitionOut,
    CmdbFilterFieldOut,
    CmdbFormOptionsOut,
    CmdbNamedRefOut,
    CmdbStatusOut,
)
from tiqora.cmdb.search import cmdb_filter_fields
from tiqora.cmdb.views import views_present
from tiqora.db.legacy.customer import CustomerCompany
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import PermissionGroups, Users
from tiqora.domain.field_options import FieldOptionOut, sorted_options
from tiqora.filters.predicates import like_escape

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


#: attribute type -> (``CmdbFormOptionsOut`` field, Znuny table with ``id``, ``name``, ``valid_id``)
_OPTION_TABLES: Final[dict[str, tuple[str, Any]]] = {
    "service": ("service", Service),
    "sla": ("sla", Sla),
    "queue": ("queue", Queue),
    "priority": ("priority", TicketPriority),
    "ticket_type": ("ticket_type", TicketType),
    "state": ("state", TicketState),
}


@router.get("/form-options", response_model=CmdbFormOptionsOut, response_model_exclude_none=True)
async def form_options(ctx: Ctx, class_id: Annotated[int, Query(ge=1)]) -> CmdbFormOptionsOut:
    """Options for the reference attributes of the class's current definition (only the types
    the definition uses; valid rows only, no ticket ACLs: a CI may reference any queue)."""
    try:
        await require_class_access(ctx.session, ctx.access, class_id, "ro")
        definition = await ClassService(ctx.session).current_definition(class_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    attrs = [a for _, a in iter_attribute_paths(definition.attributes)]
    types = {a.type for a in attrs}
    out: dict[str, Any] = {}
    for type_, (field, model) in _OPTION_TABLES.items():
        if type_ not in types:
            continue
        rows = await ctx.session.execute(
            select(model.id, model.name).where(model.valid_id == 1).order_by(model.id)
        )
        out[field] = sorted_options({str(i): str(n) for i, n in rows.all()})
    if "agent" in types:
        agents = await ctx.session.execute(
            select(Users.id, Users.first_name, Users.last_name, Users.login).where(
                Users.valid_id == 1
            )
        )
        out["agent"] = sorted_options(
            {str(i): (f"{f} {ln}".strip() or str(login)) for i, f, ln, login in agents.all()}
        )
    classes = {
        a.options.catalog_class for a in attrs if a.type == "catalog" and a.options.catalog_class
    }
    if classes:
        catalog = CatalogService(ctx.session)
        out["catalog"] = {
            name: [
                FieldOptionOut(value=str(i.id), label=i.name)
                for i in await catalog.items(name)
                if i.valid
            ]
            for name in sorted(classes)
        }
    return CmdbFormOptionsOut.model_validate(out)


@router.get("/filter-fields", response_model=list[CmdbFilterFieldOut])
async def filter_fields(
    ctx: Ctx, class_id: Annotated[int | None, Query(ge=1)] = None
) -> list[CmdbFilterFieldOut]:
    try:
        if class_id is not None:
            await require_class_access(ctx.session, ctx.access, class_id, "ro")
        readable = await readable_class_ids(ctx.session, ctx.access)
        specs = await cmdb_filter_fields(ctx.session, class_id, readable_class_ids=readable)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    return [
        CmdbFilterFieldOut(
            key=f.key,
            type=f.type,
            ops=sorted(f.allowed_ops()),
            label_key=f.label_key,
            label=f.label,
            enum_values=list(f.enum_values),
            options_source=f.options_source,
        )
        for f in specs
    ]


@router.get("/companies/lookup", response_model=list[CmdbNamedRefOut])
async def lookup_companies(
    ctx: Ctx,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[CmdbNamedRefOut]:
    """Valid customer companies by name (contains) or customer id (prefix).

    It feeds the ``customer_company`` attribute input, so it needs ``rw`` on at least one
    class (this lookup does not go through the customer directory grant, D10)."""
    if not await writable_class_ids(ctx.session, ctx.access):
        raise cmdb_http_error(CmdbForbidden("no writable cmdb class"))
    needle = like_escape(q.strip().lower())
    rows = await ctx.session.execute(
        select(CustomerCompany.customer_id, CustomerCompany.name)
        .where(
            CustomerCompany.valid_id == 1,
            or_(
                func.lower(CustomerCompany.name).like(f"%{needle}%", escape="\\"),
                func.lower(CustomerCompany.customer_id).like(f"{needle}%", escape="\\"),
            ),
        )
        .order_by(CustomerCompany.name, CustomerCompany.customer_id)
        .limit(limit)
    )
    return [
        CmdbNamedRefOut(id=str(cid), label=f"{name} ({cid})" if name != cid else str(cid))
        for cid, name in rows.all()
    ]


__all__ = [
    "build_class_out",
    "catalog_out",
    "definition_out",
    "group_names",
    "router",
    "status_router",
]
