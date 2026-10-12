"""Agent API: configuration items - search, CRUD, versions, diff, history."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status

from tiqora.api.v1.cmdb.classes import catalog_out
from tiqora.api.v1.cmdb.deps import Ctx, cmdb_http_error
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.diff import diff_versions
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.impact import affected, impact_graph, tickets_for_ci
from tiqora.cmdb.links import LinkService
from tiqora.cmdb.render import RenderedAttr, render_values
from tiqora.cmdb.schemas import (
    AffectedOut,
    CiClassRefOut,
    CiCreateIn,
    CiDiffOut,
    CiHistoryPage,
    CiOut,
    CiPage,
    CiRefOut,
    CiSaveOut,
    CiSearchIn,
    CiStatesOut,
    CiTicketOut,
    CiUpdateIn,
    CiVersionOut,
    CiVersionPage,
    ImpactGraphOut,
    LinkOut,
    RenderedAttrOut,
)
from tiqora.cmdb.search import lookup_cis, search_cis
from tiqora.cmdb.service import CiRecord, CiService, VersionRecord
from tiqora.cmdb.settings import load_cmdb_settings
from tiqora.filters import FilterError

router = APIRouter()


def _rendered(nodes: list[RenderedAttr]) -> list[RenderedAttrOut]:
    return [RenderedAttrOut.model_validate(n, from_attributes=True) for n in nodes]


async def version_out(ctx: Ctx, rec: VersionRecord) -> CiVersionOut:
    nodes = await render_values(
        ctx.session, rec.definition_attrs, rec.attributes, access=ctx.access
    )
    return CiVersionOut(
        id=rec.id,
        ci_id=rec.ci_id,
        definition_id=rec.definition_id,
        definition_version=rec.definition_version,
        name=rec.name,
        deployment_state_id=rec.deployment_state_id,
        incident_state_id=rec.incident_state_id,
        attributes=rec.attributes,
        rendered=_rendered(nodes),
        change_note=rec.change_note,
        created_by=rec.created_by,
        created_at=rec.created_at,
        archived=rec.archived,
    )


async def ci_out(ctx: Ctx, rec: CiRecord) -> CiOut:
    """The full CI answer; the rendered tree uses the definition of the current version."""
    classes = ClassService(ctx.session)
    cls = await classes.get(rec.class_id)
    definition = await classes.current_definition(rec.class_id)
    known = {a.key for a in definition.attributes}
    current: CiVersionOut | None = None
    if rec.current_version_id is not None:
        version = await CiService(ctx.session).get_version(
            ctx.access, rec.id, rec.current_version_id
        )
        current = await version_out(ctx, version)
    catalog = CatalogService(ctx.session)
    items = {
        i: catalog_out(await catalog.get(i))
        for i in {rec.deployment_state_id, rec.incident_state_id, rec.cur_incident_state_id}
    }
    return CiOut(
        id=rec.id,
        number=rec.number,
        class_=CiClassRefOut(
            id=cls.id,
            class_key=cls.class_key,
            name=cls.name,
            icon=cls.icon,
            group_id=cls.group_id,
        ),
        name=rec.name,
        deployment_state_id=rec.deployment_state_id,
        incident_state_id=rec.incident_state_id,
        states=CiStatesOut(
            deployment=items[rec.deployment_state_id],
            incident_own=items[rec.incident_state_id],
            incident_current=items[rec.cur_incident_state_id],
        ),
        version_count=rec.version_count,
        lock_version=rec.lock_version,
        current_version=current,
        rendered=[] if current is None else current.rendered,
        unknown_keys=sorted(k for k in rec.attributes if k not in known),
        can_write=ctx.access.can_write_group(cls.group_id),
        created_by=rec.created_by,
        created_at=rec.created_at,
        changed_by=rec.changed_by,
        changed_at=rec.changed_at,
    )


@router.post("/cis/search", response_model=CiPage)
async def search(body: CiSearchIn, ctx: Ctx) -> CiPage:
    try:
        return await search_cis(ctx.session, ctx.access, body)
    except (CmdbError, FilterError) as exc:
        raise cmdb_http_error(exc) from exc


@router.get("/cis/lookup", response_model=list[CiRefOut])
async def lookup(
    ctx: Ctx,
    q: Annotated[str, Query(max_length=200)] = "",
    class_keys: Annotated[list[str] | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[CiRefOut]:
    return await lookup_cis(ctx.session, ctx.access, q, class_keys=class_keys or [], limit=limit)


@router.post("/cis", response_model=CiOut, status_code=status.HTTP_201_CREATED)
async def create_ci(body: CiCreateIn, ctx: Ctx) -> CiOut:
    try:
        rec = await CiService(ctx.session).create(
            ctx.access,
            class_id=body.class_id,
            name=body.name,
            deployment_state_id=body.deployment_state_id,
            incident_state_id=body.incident_state_id,
            attributes=body.attributes,
            change_note=body.change_note,
            attachment_ids=body.attachment_ids,
        )
        out = await ci_out(ctx, rec)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return out


@router.get("/cis/{ci_id}", response_model=CiOut)
async def get_ci(ci_id: int, ctx: Ctx) -> CiOut:
    try:
        return await ci_out(ctx, await CiService(ctx.session).get(ctx.access, ci_id))
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc


@router.put("/cis/{ci_id}", response_model=CiSaveOut)
async def update_ci(ci_id: int, body: CiUpdateIn, ctx: Ctx) -> CiSaveOut:
    try:
        result = await CiService(ctx.session).update(
            ctx.access,
            ci_id,
            expected_lock_version=body.expected_lock_version,
            name=body.name,
            deployment_state_id=body.deployment_state_id,
            incident_state_id=body.incident_state_id,
            attributes=body.attributes,
            change_note=body.change_note,
            drop_unknown=body.drop_unknown,
        )
        out = CiSaveOut(ci=await ci_out(ctx, result.ci), changed=result.changed)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return out


@router.delete("/cis/{ci_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ci(ci_id: int, ctx: Ctx) -> Response:
    try:
        await CiService(ctx.session).delete(ctx.access, ci_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/cis/{ci_id}/versions", response_model=CiVersionPage)
async def list_versions(
    ci_id: int,
    ctx: Ctx,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    include_archived: bool = False,
) -> CiVersionPage:
    try:
        page = await CiService(ctx.session).list_versions(
            ctx.access, ci_id, before_id=before_id, limit=limit, include_archived=include_archived
        )
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    return CiVersionPage.model_validate(page, from_attributes=True)


@router.get("/cis/{ci_id}/versions/{version_id}", response_model=CiVersionOut)
async def get_version(ci_id: int, version_id: int, ctx: Ctx) -> CiVersionOut:
    try:
        rec = await CiService(ctx.session).get_version(ctx.access, ci_id, version_id)
        return await version_out(ctx, rec)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc


@router.get("/cis/{ci_id}/diff", response_model=CiDiffOut)
async def diff(
    ci_id: int,
    ctx: Ctx,
    from_version: Annotated[int, Query(ge=1)],
    to_version: Annotated[int, Query(ge=1)],
) -> CiDiffOut:
    try:
        rows = await diff_versions(ctx.session, ctx.access, ci_id, from_version, to_version)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    return CiDiffOut.model_validate(
        {"from_version_id": from_version, "to_version_id": to_version, "rows": rows},
        from_attributes=True,
    )


@router.get("/cis/{ci_id}/history", response_model=CiHistoryPage)
async def history(
    ci_id: int,
    ctx: Ctx,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CiHistoryPage:
    try:
        page = await CiService(ctx.session).list_history(
            ctx.access, ci_id, before_id=before_id, limit=limit
        )
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    return CiHistoryPage.model_validate(page, from_attributes=True)


@router.get("/cis/{ci_id}/impact", response_model=ImpactGraphOut)
async def ci_impact(
    ci_id: int,
    ctx: Ctx,
    depth: Annotated[int, Query(ge=1, le=5)] = 2,
    direction: Literal["both", "up", "down"] = "both",
) -> ImpactGraphOut:
    try:
        return await impact_graph(
            ctx.session,
            ctx.access,
            ci_id,
            depth=depth,
            direction=direction,
            settings=await load_cmdb_settings(ctx.session),
        )
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc


@router.get("/cis/{ci_id}/affected", response_model=AffectedOut)
async def ci_affected(ci_id: int, ctx: Ctx) -> AffectedOut:
    try:
        return await affected(
            ctx.session, ctx.access, ci_id, settings=await load_cmdb_settings(ctx.session)
        )
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc


@router.get("/cis/{ci_id}/tickets", response_model=list[CiTicketOut])
async def ci_tickets(
    ci_id: int, ctx: Ctx, open_only: Annotated[bool, Query()] = False
) -> list[CiTicketOut]:
    try:
        return await tickets_for_ci(ctx.session, ctx.access, ci_id, open_only=open_only)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc


@router.get("/cis/{ci_id}/links", response_model=list[LinkOut])
async def ci_links(ci_id: int, ctx: Ctx) -> list[LinkOut]:
    try:
        return await LinkService(ctx.session).list_for(ctx.access, "ci", str(ci_id))
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc


__all__ = ["ci_out", "router", "version_out"]
