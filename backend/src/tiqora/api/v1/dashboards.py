"""Dashboards API (D3): tabs, whole-dashboard save with optimistic lock, widget settings.

Every route needs a logged-in agent. Visibility is decided by the store: a dashboard the
caller may not see answers exactly like a missing one (404, same body). API keys with scopes
never reach these routes: the prefixes are deliberately not in ``_PATH_PREFIX_TO_AREA``
(``_unknown``: sessions and unrestricted keys only, R-DASH-APIKEY). CSRF protection for
cookie-authenticated writes is the global ``csrf_origin_middleware``.
"""

from __future__ import annotations

from typing import Final, NoReturn

from fastapi import APIRouter, HTTPException, Response, status

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.dashboard import start, store
from tiqora.dashboard.schemas import (
    DashboardCreateIn,
    DashboardOrderIn,
    DashboardOut,
    DashboardSaveIn,
    DashboardTabOut,
    WidgetOut,
    WidgetSettingsPatchIn,
    WidgetTypeOut,
)
from tiqora.dashboard.settings_models import CORE_WIDGET_SETTINGS
from tiqora.permissions.engine import PermissionEngine

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
widget_types_router = APIRouter(prefix="/dashboard-widget-types", tags=["dashboards"])

NOT_FOUND_MESSAGE: Final = "Dashboard nicht gefunden."
CORE_WIDGET_VERSION: Final = 1


def provider_keys() -> frozenset[str]:
    """Widget types with a data provider (Task 5/6 fill ``DASHBOARD_PROVIDERS``)."""
    return frozenset()


def _raise_http(exc: Exception) -> NoReturn:
    """Map a store exception to its HTTP error; anything else propagates."""
    if isinstance(exc, store.DashboardNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND_MESSAGE) from exc
    if isinstance(exc, store.DashboardForbidden):
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.message) from exc
    if isinstance(exc, store.DashboardConflict):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": exc.message, "current_version": exc.current_version},
        ) from exc
    if isinstance(exc, store.DashboardInvalid):
        raise HTTPException(
            422,
            {"message": exc.message, "path": exc.path},
        ) from exc
    raise exc


async def _is_admin(session: DbSession, user: CurrentUser) -> bool:
    return await PermissionEngine(session).is_admin(user.id)


_STORE_ERRORS: Final = (
    store.DashboardNotFound,
    store.DashboardForbidden,
    store.DashboardConflict,
    store.DashboardInvalid,
)


@router.get("", response_model=list[DashboardTabOut])
async def list_dashboards(session: DbSession, user: CurrentUser) -> list[DashboardTabOut]:
    return await store.list_visible(session, user.id, admin=await _is_admin(session, user))


@router.post("/ensure-start", response_model=DashboardOut)
async def ensure_start(session: DbSession, user: CurrentUser, response: Response) -> DashboardOut:
    """The caller's first dashboard; 201 when this call created "Mein Start", else 200."""
    out, created = await start.ensure_start(session, user.id)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return out


@router.put("/order", status_code=status.HTTP_204_NO_CONTENT)
async def put_order(body: DashboardOrderIn, session: DbSession, user: CurrentUser) -> Response:
    try:
        await store.set_order(session, user.id, body.ids)
        await session.commit()
    except _STORE_ERRORS as exc:
        await session.rollback()
        _raise_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("", response_model=DashboardOut, status_code=status.HTTP_201_CREATED)
async def create_dashboard(
    body: DashboardCreateIn, session: DbSession, user: CurrentUser
) -> DashboardOut:
    try:
        out = await store.create_dashboard(
            session, user.id, body, admin=await _is_admin(session, user)
        )
        await session.commit()
    except _STORE_ERRORS as exc:
        await session.rollback()
        _raise_http(exc)
    return out


@router.get("/{dashboard_id}", response_model=DashboardOut)
async def get_dashboard(dashboard_id: int, session: DbSession, user: CurrentUser) -> DashboardOut:
    try:
        return await store.get_dashboard(
            session, dashboard_id, user.id, admin=await _is_admin(session, user)
        )
    except _STORE_ERRORS as exc:
        _raise_http(exc)


@router.put("/{dashboard_id}", response_model=DashboardOut)
async def save_dashboard(
    dashboard_id: int, body: DashboardSaveIn, session: DbSession, user: CurrentUser
) -> DashboardOut:
    try:
        out = await store.save_dashboard(
            session, dashboard_id, user.id, body, admin=await _is_admin(session, user)
        )
        await session.commit()
    except _STORE_ERRORS as exc:
        await session.rollback()
        _raise_http(exc)
    return out


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(dashboard_id: int, session: DbSession, user: CurrentUser) -> Response:
    try:
        await store.delete_dashboard(
            session, dashboard_id, user.id, admin=await _is_admin(session, user)
        )
        await session.commit()
    except _STORE_ERRORS as exc:
        await session.rollback()
        _raise_http(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{dashboard_id}/widgets/{widget_id}/settings", response_model=WidgetOut)
async def patch_widget_settings(
    dashboard_id: int,
    widget_id: str,
    body: WidgetSettingsPatchIn,
    session: DbSession,
    user: CurrentUser,
) -> WidgetOut:
    try:
        out = await store.patch_widget_settings(
            session,
            dashboard_id,
            widget_id,
            user.id,
            body,
            admin=await _is_admin(session, user),
        )
        await session.commit()
    except _STORE_ERRORS as exc:
        await session.rollback()
        _raise_http(exc)
    return out


@widget_types_router.get("", response_model=list[WidgetTypeOut])
async def list_widget_types(_user: CurrentUser) -> list[WidgetTypeOut]:
    """The ten core widget types (plugin widgets have no server side in phase 1)."""
    with_data = provider_keys()
    return [
        WidgetTypeOut(key=key, version=CORE_WIDGET_VERSION, has_data=key in with_data)
        for key in sorted(CORE_WIDGET_SETTINGS)
    ]
