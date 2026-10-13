"""Agent API: ITSM links (``/links``) and the link types offered for CIs (``/link-types``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from tiqora.api.v1.cmdb.deps import Ctx, cmdb_http_error
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.links import LinkEnd, LinkService, LinkTypeService
from tiqora.cmdb.schemas import CmdbLinkTypeOut, LinkIn, LinkKind, LinkOut

router = APIRouter()


@router.get("/link-types", response_model=list[CmdbLinkTypeOut])
async def list_link_types(ctx: Ctx) -> list[CmdbLinkTypeOut]:
    """Valid link types offered for CI to CI, CI to service or ticket to CI links."""
    rows = await LinkTypeService(ctx.session).list_offered()
    return [
        CmdbLinkTypeOut(
            name=t.name,
            source_label=t.source_label,
            target_label=t.target_label,
            pointed=bool(t.pointed),
            propagation=t.propagation,  # type: ignore[arg-type]
            offer_ci_ci=bool(t.offer_ci_ci),
            offer_ci_service=bool(t.offer_ci_service),
            offer_ticket_ci=bool(t.offer_ticket_ci),
            position=int(t.position),
        )
        for t in rows
    ]


@router.get("/links", response_model=list[LinkOut])
async def list_links(
    ctx: Ctx,
    object_kind: Annotated[LinkKind, Query()],
    object_key: Annotated[str, Query(min_length=1, max_length=100)],
) -> list[LinkOut]:
    try:
        return await LinkService(ctx.session).list_for(ctx.access, object_kind, object_key)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc


@router.post("/links", response_model=LinkOut, status_code=status.HTTP_201_CREATED)
async def create_link(body: LinkIn, ctx: Ctx) -> LinkOut:
    svc = LinkService(ctx.session)
    try:
        rec = await svc.add(
            ctx.access,
            source=LinkEnd(body.source_kind, body.source_key),
            target=LinkEnd(body.target_kind, body.target_key),
            link_type_key=body.link_type,
        )
        out = await svc.get_out(
            ctx.access, rec.id, viewpoint=LinkEnd(body.source_kind, body.source_key)
        )
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return out


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(link_id: int, ctx: Ctx) -> Response:
    try:
        await LinkService(ctx.session).delete(ctx.access, link_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
