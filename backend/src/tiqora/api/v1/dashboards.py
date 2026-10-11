"""Dashboards API (D3): tabs, whole-dashboard save with optimistic lock, widget settings.

Every route needs a logged-in agent. Visibility is decided by the store: a dashboard the
caller may not see answers exactly like a missing one (404, same body). API keys with scopes
never reach these routes: the prefixes are deliberately not in ``_PATH_PREFIX_TO_AREA``
(``_unknown``: sessions and unrestricted keys only, R-DASH-APIKEY). CSRF protection for
cookie-authenticated writes is the global ``csrf_origin_middleware``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Final, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.api.deps import (
    CurrentUser,
    DbSession,
    get_app_settings,
    get_redis,
    get_session_factory_dep,
)
from tiqora.config import Settings
from tiqora.dashboard import start, store
from tiqora.dashboard.providers import DASHBOARD_PROVIDERS
from tiqora.dashboard.runner import run_batch, run_widget
from tiqora.dashboard.schemas import (
    MAX_WIDGETS_PER_DASHBOARD,
    DashboardCreateIn,
    DashboardDataOut,
    DashboardFiltersIn,
    DashboardOrderIn,
    DashboardOut,
    DashboardSaveIn,
    DashboardShareIn,
    DashboardTabOut,
    WidgetDataOut,
    WidgetOut,
    WidgetPreviewIn,
    WidgetSettingsPatchIn,
    WidgetTypeOut,
)
from tiqora.dashboard.settings_models import (
    CORE_WIDGET_SETTINGS,
    SettingsError,
    validate_widget_settings,
)
from tiqora.extensions import Registry, get_registry
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.permissions.sharing import ShareGroupError

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
widget_types_router = APIRouter(prefix="/dashboard-widget-types", tags=["dashboards"])

NOT_FOUND_MESSAGE: Final = "Dashboard nicht gefunden."
NO_DATA_MESSAGE: Final = "Dieses Widget hat keine Daten."
CORE_WIDGET_VERSION: Final = 1


def get_dashboard_registry() -> Registry:
    """The registry holding ``DASHBOARD_PROVIDERS`` (tests override it)."""
    return get_registry()


DashboardRegistry = Annotated[Registry, Depends(get_dashboard_registry)]
SessionFactory = Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory_dep)]
#: BIGINT range: a larger id is a 422 here instead of an asyncpg ``DataError`` (500).
DashboardId = Annotated[int, Path(ge=1, le=2**63 - 1)]


def provider_keys(registry: Registry) -> frozenset[str]:
    """Widget types with a data provider."""
    return frozenset(p.key for p in registry.items(DASHBOARD_PROVIDERS))


async def _redis_or_none(request: Request) -> Redis | None:
    """Redis for providers that read presence; ``None`` when it cannot be obtained."""
    try:
        return await get_redis(request)
    except Exception:  # noqa: BLE001 - providers cope without Redis
        return None


@dataclass(frozen=True, slots=True)
class _PreviewWidget:
    id: str
    type_key: str
    type_version: int
    settings: dict[str, Any]
    pinned: bool = False


def _widget_ids(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    ids = {part.strip() for part in raw.split(",") if part.strip()}
    if len(ids) > MAX_WIDGETS_PER_DASHBOARD:
        raise HTTPException(
            422,
            {
                "message": f"Höchstens {MAX_WIDGETS_PER_DASHBOARD} Widgets auf einmal.",
                "path": "widgets",
            },
        )
    return ids


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
    if isinstance(exc, ShareGroupError):
        raise HTTPException(422, {"message": exc.message, "path": exc.path}) from exc
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
    ShareGroupError,
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
async def get_dashboard(
    dashboard_id: DashboardId, session: DbSession, user: CurrentUser
) -> DashboardOut:
    try:
        return await store.get_dashboard(
            session, dashboard_id, user.id, admin=await _is_admin(session, user)
        )
    except _STORE_ERRORS as exc:
        _raise_http(exc)


@router.put("/{dashboard_id}", response_model=DashboardOut)
async def save_dashboard(
    dashboard_id: DashboardId, body: DashboardSaveIn, session: DbSession, user: CurrentUser
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


@router.patch("/{dashboard_id}/filters", response_model=DashboardOut)
async def patch_filters(
    dashboard_id: DashboardId, body: DashboardFiltersIn, session: DbSession, user: CurrentUser
) -> DashboardOut:
    """Set the dashboard-wide filters (same rights and version lock as the save)."""
    try:
        out = await store.set_filters(
            session,
            dashboard_id,
            user.id,
            body.filters,
            body.version,
            admin=await _is_admin(session, user),
        )
        await session.commit()
    except _STORE_ERRORS as exc:
        await session.rollback()
        _raise_http(exc)
    return out


@router.put("/{dashboard_id}/share", response_model=DashboardOut)
async def share_dashboard(
    dashboard_id: DashboardId, body: DashboardShareIn, session: DbSession, user: CurrentUser
) -> DashboardOut:
    """Share with groups (owner only); ``[]`` unshares. Templates are distributed by role."""
    try:
        out = await store.set_share(
            session,
            dashboard_id,
            user.id,
            body.group_ids,
            admin=await _is_admin(session, user),
        )
        await session.commit()
    except _STORE_ERRORS as exc:
        await session.rollback()
        _raise_http(exc)
    return out


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(
    dashboard_id: DashboardId, session: DbSession, user: CurrentUser
) -> Response:
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
    dashboard_id: DashboardId,
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


async def _visible_dashboard(
    session: DbSession, user: CurrentUser, dashboard_id: int
) -> DashboardOut:
    """The dashboard (widgets and filters) the caller may see; frees the request's connection.

    Providers open their own sessions, so the request session must not keep a pooled
    connection while they run.
    """
    try:
        out = await store.get_dashboard(
            session, dashboard_id, user.id, admin=await _is_admin(session, user)
        )
    except _STORE_ERRORS as exc:
        _raise_http(exc)
    finally:
        await session.rollback()
    return out


@router.get("/{dashboard_id}/data", response_model=DashboardDataOut)
async def dashboard_data(
    dashboard_id: DashboardId,
    request: Request,
    session: DbSession,
    user: CurrentUser,
    factory: SessionFactory,
    registry: DashboardRegistry,
    app_settings: Annotated[Settings, Depends(get_app_settings)],
    widgets: Annotated[str | None, Query(max_length=2000)] = None,
) -> DashboardDataOut:
    """Data of the dashboard's widgets (``widgets`` = comma list of ids; default all).

    Providers run as the viewer, at most four at once, five seconds each; a widget that runs
    out of time is ``pending``. Ids that are not on this dashboard are ignored.
    """
    wanted = _widget_ids(widgets)
    board = await _visible_dashboard(session, user, dashboard_id)
    rows = board.widgets
    if wanted is not None:
        rows = [w for w in rows if w.id in wanted]
    results = await run_batch(
        factory,
        registry,
        user.id,
        rows,
        redis=await _redis_or_none(request),
        filters=board.filters,
        app_settings=app_settings,
    )
    return DashboardDataOut(widgets=results, generated_at=utc_now())


@router.get("/{dashboard_id}/widgets/{widget_id}/data", response_model=WidgetDataOut)
async def widget_data(
    dashboard_id: DashboardId,
    widget_id: str,
    request: Request,
    session: DbSession,
    user: CurrentUser,
    factory: SessionFactory,
    registry: DashboardRegistry,
    app_settings: Annotated[Settings, Depends(get_app_settings)],
) -> WidgetDataOut:
    """One widget's data with the longer single-widget budget (25 s)."""
    board = await _visible_dashboard(session, user, dashboard_id)
    widget = next((w for w in board.widgets if w.id == widget_id), None)
    if widget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Widget nicht gefunden.")
    out = await run_widget(
        factory,
        registry,
        user.id,
        widget,
        redis=await _redis_or_none(request),
        filters=board.filters,
        app_settings=app_settings,
    )
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_DATA_MESSAGE)
    return out


@widget_types_router.get("", response_model=list[WidgetTypeOut])
async def list_widget_types(_user: CurrentUser, registry: DashboardRegistry) -> list[WidgetTypeOut]:
    """The ten core widget types (plugin widgets have no server side in phase 1)."""
    with_data = provider_keys(registry)
    return [
        WidgetTypeOut(key=key, version=CORE_WIDGET_VERSION, has_data=key in with_data)
        for key in sorted(CORE_WIDGET_SETTINGS)
    ]


@widget_types_router.post("/{key}/data", response_model=WidgetDataOut)
async def preview_widget_data(
    key: str,
    body: WidgetPreviewIn,
    request: Request,
    user: CurrentUser,
    factory: SessionFactory,
    registry: DashboardRegistry,
) -> WidgetDataOut:
    """Data for unsaved settings (settings drawer); runs as the viewer, writes no history."""
    provider = registry.get(DASHBOARD_PROVIDERS, key)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_DATA_MESSAGE)
    try:
        settings = validate_widget_settings(key, body.settings)
    except SettingsError as exc:
        raise HTTPException(422, {"message": exc.message, "path": exc.path}) from exc
    widget = _PreviewWidget(
        id="preview", type_key=key, type_version=provider.version, settings=settings
    )
    out = await run_widget(
        factory, registry, user.id, widget, redis=await _redis_or_none(request), preview=True
    )
    assert out is not None  # the provider exists
    return out
