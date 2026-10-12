"""Admin API for wallboard links (E9): create, list, revoke, delete.

Mounted at ``/api/v1/admin/dashboard-kiosk-links`` so it inherits ``AdminUser`` and the ``admin``
API-key area. The secret is returned by ``POST`` only; lists carry the last four characters.
The public, token-authenticated read side lives in ``api/v1/kiosk.py``.
"""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path, Response, status

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.dashboard import kiosk, store
from tiqora.dashboard.schemas import KioskLinkCreatedOut, KioskLinkIn, KioskLinkOut

router = APIRouter(prefix="/dashboard-kiosk-links", tags=["admin:dashboard-kiosk-links"])

DASHBOARD_NOT_FOUND = "Dashboard nicht gefunden."
LinkId = Annotated[int, Path(ge=1, le=2**63 - 1)]


async def _fail(session: DbSession, exc: Exception, message: str) -> NoReturn:
    await session.rollback()
    if isinstance(exc, store.DashboardNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, message) from exc
    if isinstance(exc, store.DashboardInvalid):
        raise HTTPException(422, {"message": exc.message, "path": exc.path}) from exc
    raise exc


@router.get("", response_model=list[KioskLinkOut])
async def list_links(admin: AdminUser, session: DbSession) -> list[KioskLinkOut]:
    _ = admin
    out = await kiosk.list_links(session)
    await session.rollback()  # read-only request
    return out


@router.post("", response_model=KioskLinkCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_link(
    admin: AdminUser, session: DbSession, body: KioskLinkIn
) -> KioskLinkCreatedOut:
    try:
        link, token = await kiosk.create_link(session, admin.id, body)
    except store.DashboardNotFound as exc:
        await _fail(session, exc, DASHBOARD_NOT_FOUND)
    await session.commit()
    return KioskLinkCreatedOut(**link.model_dump(), token=token)


@router.post("/{link_id}/revoke", response_model=KioskLinkOut)
async def revoke_link(admin: AdminUser, session: DbSession, link_id: LinkId) -> KioskLinkOut:
    _ = admin
    try:
        out = await kiosk.revoke_link(session, link_id)
    except store.DashboardNotFound as exc:
        await _fail(session, exc, kiosk.MSG_NOT_FOUND)
    await session.commit()
    return out


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(admin: AdminUser, session: DbSession, link_id: LinkId) -> Response:
    _ = admin
    try:
        await kiosk.delete_link(session, link_id)
    except (store.DashboardNotFound, store.DashboardInvalid) as exc:
        await _fail(session, exc, kiosk.MSG_NOT_FOUND)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
