"""Admin API of the CMDB (D10): catalog, classes, definitions, templates, seeding.

Mounted at ``/api/v1/admin/cmdb`` (API-key area ``admin``). Every route requires an admin and the
CMDB mode gate except ``GET /status``. Link types are edited on the ITSM link-type page (cross
ruling R2), so there is no link-type route here.
"""

from __future__ import annotations

import json
from typing import Annotated, NoReturn

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.cmdb.classes import (
    build_class_out,
    catalog_out,
    definition_out,
    group_names,
)
from tiqora.api.v1.cmdb.deps import cmdb_gate, cmdb_http_error
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.classes import ClassData, ClassService, DefinitionNotFound
from tiqora.cmdb.definition import (
    AttributeDef,
    DefinitionChange,
    DefinitionIssue,
    definition_changes,
    iter_attribute_paths,
    parse_attrs,
)
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.gate import current_itsm_mode
from tiqora.cmdb.propagation import enqueue_full_recompute
from tiqora.cmdb.schemas import (
    CatalogItemIn,
    CatalogItemOut,
    ClassTemplateApplyIn,
    ClassTemplateOut,
    CmdbAdminStatusOut,
    CmdbClassIn,
    CmdbClassOut,
    CmdbClassUpdateIn,
    CmdbDefinitionIn,
    CmdbDefinitionOut,
    DefinitionChangeOut,
    DefinitionImportIn,
    DefinitionImportOut,
    DefinitionIssueOut,
    DefinitionValidateIn,
)
from tiqora.cmdb.seed import DEFAULT_CATALOG_ROWS, seed_defaults
from tiqora.cmdb.templates import CLASS_TEMPLATES
from tiqora.cmdb.views import create_cmdb_views, views_present
from tiqora.cmdb.znuny_definition import DefinitionParseError, parse_znuny_definition
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbCiVersionArchive,
    TiqoraCmdbClass,
)
from tiqora.jobs.schemas import JobAccepted

logger = structlog.get_logger(__name__)

#: ungated like the agent status route, but admin only
status_router = APIRouter(prefix="/cmdb", tags=["admin:cmdb"])
router = APIRouter(prefix="/cmdb", tags=["admin:cmdb"], dependencies=[Depends(cmdb_gate)])


def _raise(exc: Exception) -> NoReturn:
    raise cmdb_http_error(exc) from exc


def _issue_out(issues: list[DefinitionIssue]) -> list[DefinitionIssueOut]:
    return [DefinitionIssueOut(path=i.path, code=i.code, message=i.message) for i in issues]


def _change_out(changes: list[DefinitionChange]) -> list[DefinitionChangeOut]:
    return [DefinitionChangeOut(path=c.path, kind=c.kind, detail=c.detail) for c in changes]


async def _require_group(session: AsyncSession, group_id: int) -> str:
    names = await group_names(session, {group_id})
    if group_id not in names:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "unknown_group", "message": "Diese Gruppe gibt es nicht."},
        )
    return names[group_id]


async def _count(session: AsyncSession, model: type[TiqoraBase]) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def _admin_status(session: AsyncSession, *, views: bool | None = None) -> CmdbAdminStatusOut:
    existing = {
        (c, k)
        for c, k in (
            await session.execute(
                select(TiqoraCmdbCatalog.catalog_class, TiqoraCmdbCatalog.item_key)
            )
        ).all()
    }
    seeded = all((r.catalog_class, r.item_key) in existing for r in DEFAULT_CATALOG_ROWS)
    return CmdbAdminStatusOut(
        mode=await current_itsm_mode(session),
        views_present=await views_present(session) if views is None else views,
        catalog_seeded=seeded,
        class_count=await _count(session, TiqoraCmdbClass),
        ci_count=await _count(session, TiqoraCmdbCi),
        archived_version_count=await _count(session, TiqoraCmdbCiVersionArchive),
    )


@status_router.get("/status", response_model=CmdbAdminStatusOut)
async def admin_status(admin: AdminUser, session: DbSession) -> CmdbAdminStatusOut:
    _ = admin
    return await _admin_status(session)


@router.post("/seed-defaults", response_model=CmdbAdminStatusOut)
async def seed(admin: AdminUser, session: DbSession) -> CmdbAdminStatusOut:
    """Idempotent: missing catalog rows and link types, then (re)create the two add-on views.

    A missing ``CREATE VIEW`` privilege is not an error: the answer says ``views_present: false``.
    """
    _ = admin
    await seed_defaults(session)
    await session.commit()
    conn = await session.connection()
    created = await conn.run_sync(create_cmdb_views)
    await session.commit()
    if not created:
        logger.warning("cmdb_seed_views_missing", message="CREATE VIEW-Recht fehlt")
    return await _admin_status(session, views=created)


@router.post(
    "/propagation/recompute", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def recompute_propagation(admin: AdminUser, session: DbSession) -> JobAccepted:
    """Queue the full recompute of derived incident states (one job at a time, dedupe ``all``)."""
    job_id = await enqueue_full_recompute(session, user_id=admin.id)
    await session.commit()
    return JobAccepted(job_id=job_id, status="queued")


# --- catalog ----------------------------------------------------------------------------


@router.get("/catalog-classes", response_model=list[str])
async def catalog_classes(admin: AdminUser, session: DbSession) -> list[str]:
    _ = admin
    return await CatalogService(session).list_classes()


@router.get("/catalog", response_model=list[CatalogItemOut])
async def list_catalog(
    admin: AdminUser,
    session: DbSession,
    catalog_class: Annotated[str, Query(min_length=1, max_length=100)],
) -> list[CatalogItemOut]:
    _ = admin
    items = await CatalogService(session).items(catalog_class, include_invalid=True)
    return [catalog_out(i) for i in items]


@router.post("/catalog", response_model=CatalogItemOut, status_code=status.HTTP_201_CREATED)
async def create_catalog_item(
    body: CatalogItemIn, admin: AdminUser, session: DbSession
) -> CatalogItemOut:
    _ = admin
    try:
        item = await CatalogService(session).create(
            body.catalog_class,
            body.item_key,
            body.name,
            functionality=body.functionality,
            position=body.position,
            valid=body.valid,
        )
    except CmdbError as exc:
        _raise(exc)
    await session.commit()
    return catalog_out(item)


@router.put("/catalog/{item_id}", response_model=CatalogItemOut)
async def update_catalog_item(
    item_id: int, body: CatalogItemIn, admin: AdminUser, session: DbSession
) -> CatalogItemOut:
    _ = admin
    svc = CatalogService(session)
    try:
        current = await svc.get(item_id)
        if (body.catalog_class, body.item_key) != (current.catalog_class, current.item_key):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                {
                    "code": "immutable_key",
                    "message": "Klasse und Schlüssel eines Eintrags lassen sich nicht ändern.",
                },
            )
        item = await svc.update(
            item_id,
            name=body.name,
            functionality=body.functionality,
            position=body.position,
            valid=body.valid,
        )
    except CmdbError as exc:
        _raise(exc)
    await session.commit()
    return catalog_out(item)


# --- classes ----------------------------------------------------------------------------


@router.get("/classes", response_model=list[CmdbClassOut])
async def list_classes(admin: AdminUser, session: DbSession) -> list[CmdbClassOut]:
    _ = admin
    svc = ClassService(session)
    records = await svc.list(include_invalid=True)
    names = await group_names(session, {r.group_id for r in records})
    return [
        await build_class_out(
            svc, r, group_name=names.get(r.group_id), can_write=True, with_definition=False
        )
        for r in records
    ]


def _class_data(body: CmdbClassIn) -> ClassData:
    return ClassData(
        class_key=body.class_key,
        name=body.name,
        group_id=body.group_id,
        icon=body.icon,
        number_prefix=body.number_prefix,
        portal_visible=body.portal_visible,
        valid=body.valid,
        position=body.position,
    )


@router.post("/classes", response_model=CmdbClassOut, status_code=status.HTTP_201_CREATED)
async def create_class(body: CmdbClassIn, admin: AdminUser, session: DbSession) -> CmdbClassOut:
    group_name = await _require_group(session, body.group_id)
    svc = ClassService(session)
    try:
        rec = await svc.create(_class_data(body), user_id=admin.id)
    except CmdbError as exc:
        _raise(exc)
    await session.commit()
    return await build_class_out(
        svc, rec, group_name=group_name, can_write=True, with_definition=True
    )


@router.get("/classes/{class_id}", response_model=CmdbClassOut)
async def get_class(class_id: int, admin: AdminUser, session: DbSession) -> CmdbClassOut:
    _ = admin
    svc = ClassService(session)
    try:
        rec = await svc.get(class_id)
    except CmdbError as exc:
        _raise(exc)
    names = await group_names(session, {rec.group_id})
    return await build_class_out(
        svc, rec, group_name=names.get(rec.group_id), can_write=True, with_definition=True
    )


@router.put("/classes/{class_id}", response_model=CmdbClassOut)
async def update_class(
    class_id: int, body: CmdbClassUpdateIn, admin: AdminUser, session: DbSession
) -> CmdbClassOut:
    group_name = await _require_group(session, body.group_id)
    svc = ClassService(session)
    try:
        rec = await svc.update(
            class_id,
            _class_data(body),
            expected_lock_version=body.expected_lock_version,
            user_id=admin.id,
        )
    except CmdbError as exc:
        _raise(exc)
    await session.commit()
    return await build_class_out(
        svc, rec, group_name=group_name, can_write=True, with_definition=True
    )


# --- definitions ------------------------------------------------------------------------


@router.get("/classes/{class_id}/definitions", response_model=list[CmdbDefinitionOut])
async def list_definitions(
    class_id: int, admin: AdminUser, session: DbSession
) -> list[CmdbDefinitionOut]:
    _ = admin
    svc = ClassService(session)
    try:
        await svc.get(class_id)
    except CmdbError as exc:
        _raise(exc)
    return [definition_out(d) for d in await svc.definitions(class_id)]


@router.post(
    "/classes/{class_id}/definitions",
    response_model=CmdbDefinitionOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_definition(
    class_id: int, body: CmdbDefinitionIn, admin: AdminUser, session: DbSession
) -> CmdbDefinitionOut:
    try:
        rec = await ClassService(session).add_definition(
            class_id, body.attributes, comment=body.comment, user_id=admin.id
        )
    except CmdbError as exc:
        _raise(exc)
    await session.commit()
    return definition_out(rec)


@router.get("/classes/{class_id}/definitions/diff", response_model=list[DefinitionChangeOut])
async def definition_diff(
    class_id: int,
    admin: AdminUser,
    session: DbSession,
    from_version: Annotated[int, Query(ge=1)],
    to_version: Annotated[int, Query(ge=1)],
) -> list[DefinitionChangeOut]:
    _ = admin
    svc = ClassService(session)
    try:
        await svc.get(class_id)
        by_version = {d.version: d for d in await svc.definitions(class_id)}
        old, new = by_version.get(from_version), by_version.get(to_version)
        if old is None or new is None:
            raise DefinitionNotFound(f"class {class_id}")
    except CmdbError as exc:
        _raise(exc)
    return _change_out(definition_changes(old.attributes, new.attributes))


@router.post("/definitions/validate", response_model=list[DefinitionIssueOut])
async def validate_definition_route(
    body: DefinitionValidateIn, admin: AdminUser, session: DbSession
) -> list[DefinitionIssueOut]:
    _ = admin
    svc = ClassService(session)
    try:
        if body.class_id is not None:
            await svc.get(body.class_id)
    except CmdbError as exc:
        _raise(exc)
    return _issue_out(await svc.check(body.attributes))


def _parse_tiqora_json(text: str) -> list[AttributeDef]:
    try:
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("Die Definition muss eine Liste von Attributen sein.")
        return parse_attrs(data)
    except (ValueError, TypeError, ValidationError) as exc:
        raise DefinitionParseError(f"JSON-Definition nicht lesbar: {exc}") from exc


@router.post("/definition-import", response_model=DefinitionImportOut)
async def definition_import(
    body: DefinitionImportIn, admin: AdminUser, session: DbSession
) -> DefinitionImportOut:
    """Preview only: parses a Znuny (YAML/Perl) or Tiqora JSON definition, writes nothing."""
    _ = admin
    warnings: list[str] = []
    try:
        if body.format == "tiqora_json":
            attrs = _parse_tiqora_json(body.text)
            used = {
                a.options.catalog_class
                for _path, a in iter_attribute_paths(attrs)
                if a.type == "catalog" and a.options.catalog_class
            }
        else:
            res = parse_znuny_definition(
                body.text, "yaml" if body.format == "znuny_yaml" else "perl"
            )
            attrs, warnings, used = res.attributes, res.warnings, res.catalog_classes_used
    except DefinitionParseError as exc:
        _raise(exc)
    known = set(await CatalogService(session).list_classes())
    return DefinitionImportOut(
        attributes=attrs, warnings=warnings, missing_catalog_classes=sorted(used - known)
    )


# --- templates --------------------------------------------------------------------------


@router.get("/class-templates", response_model=list[ClassTemplateOut])
async def list_templates(admin: AdminUser) -> list[ClassTemplateOut]:
    _ = admin
    return [
        ClassTemplateOut(
            key=t.key,
            name=t.name,
            icon=t.icon,
            attribute_count=sum(1 for _ in iter_attribute_paths(t.attributes)),
        )
        for t in CLASS_TEMPLATES.values()
    ]


@router.post(
    "/class-templates/{key}/apply",
    response_model=CmdbClassOut,
    status_code=status.HTTP_201_CREATED,
)
async def apply_template(
    key: str, body: ClassTemplateApplyIn, admin: AdminUser, session: DbSession
) -> CmdbClassOut:
    group_name = await _require_group(session, body.group_id)
    svc = ClassService(session)
    try:
        rec = await svc.apply_template(key, group_id=body.group_id, user_id=admin.id)
    except CmdbError as exc:
        _raise(exc)
    await session.commit()
    return await build_class_out(
        svc, rec, group_name=group_name, can_write=True, with_definition=True
    )


__all__ = ["router", "status_router"]
