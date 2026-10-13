"""Customer portal: the configuration items linked to the customer (Mode B only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from tiqora.api.deps import DbSession
from tiqora.api.portal.deps import CurrentCustomer
from tiqora.cmdb.gate import current_itsm_mode
from tiqora.cmdb.portal import PortalCiNotFound, PortalCmdbService
from tiqora.cmdb.schemas import PortalCiDetailOut, PortalCiPage


async def require_cmdb_mode(session: DbSession) -> None:
    """404 unless the Tiqora CMDB is the active ITSM mode (a portal never advertises it)."""
    if await current_itsm_mode(session) != "tiqora":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(
    prefix="/config-items",
    tags=["portal-cmdb"],
    dependencies=[Depends(require_cmdb_mode)],
)


async def _service(session: DbSession) -> PortalCmdbService:
    return PortalCmdbService(session)


PortalCmdb = Annotated[PortalCmdbService, Depends(_service)]


@router.get("", response_model=PortalCiPage)
async def list_config_items(
    customer: CurrentCustomer,
    svc: PortalCmdb,
    q: str | None = Query(default=None, max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> PortalCiPage:
    return await svc.list(customer, q=q, offset=offset, limit=limit)


@router.get("/{ci_id}", response_model=PortalCiDetailOut)
async def get_config_item(
    ci_id: int, customer: CurrentCustomer, svc: PortalCmdb
) -> PortalCiDetailOut:
    try:
        return await svc.get(customer, ci_id)
    except PortalCiNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
