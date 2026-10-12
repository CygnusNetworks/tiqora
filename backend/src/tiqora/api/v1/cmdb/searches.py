"""Agent API: saved CI searches (own and shared)."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from tiqora.api.v1.cmdb.deps import Ctx, cmdb_http_error
from tiqora.cmdb.errors import CmdbError
from tiqora.cmdb.saved_searches import (
    create_saved,
    delete_saved,
    list_saved,
    saved_to_out,
    update_saved,
)
from tiqora.cmdb.schemas import CmdbSavedSearchIn, CmdbSavedSearchOut

router = APIRouter(prefix="/saved-searches")


@router.get("", response_model=list[CmdbSavedSearchOut])
async def list_searches(ctx: Ctx) -> list[CmdbSavedSearchOut]:
    uid = ctx.user.id
    return [saved_to_out(r, uid) for r in await list_saved(ctx.session, uid)]


@router.post("", response_model=CmdbSavedSearchOut, status_code=status.HTTP_201_CREATED)
async def create_search(body: CmdbSavedSearchIn, ctx: Ctx) -> CmdbSavedSearchOut:
    row = await create_saved(ctx.session, ctx.user.id, body)
    out = saved_to_out(row, ctx.user.id)
    await ctx.session.commit()
    return out


@router.put("/{saved_id}", response_model=CmdbSavedSearchOut)
async def update_search(saved_id: int, body: CmdbSavedSearchIn, ctx: Ctx) -> CmdbSavedSearchOut:
    try:
        row = await update_saved(ctx.session, ctx.user.id, saved_id, body)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    out = saved_to_out(row, ctx.user.id)
    await ctx.session.commit()
    return out


@router.delete("/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_search(saved_id: int, ctx: Ctx) -> Response:
    try:
        await delete_saved(ctx.session, ctx.user.id, saved_id)
    except CmdbError as exc:
        raise cmdb_http_error(exc) from exc
    await ctx.session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
