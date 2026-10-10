"""Saved ticket searches: list, create, edit, share, per-viewer sidebar settings, counts.

A search stores a wave-0 filter; whoever opens it (owner or colleague in a shared group) runs
it with their own queue permissions, so a shared search never shows more than the viewer may
see. Invisible searches are indistinguishable from unknown ones (404). ``GET /counts`` is part
of every page (the sidebar): it runs under one shared deadline, caches per viewer for 30 s and
answers ``null`` for a search that cannot be counted.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, NoReturn, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import CurrentUser, DbSession, get_redis
from tiqora.domain.auth import AuthenticatedUser
from tiqora.filters import FilterError
from tiqora.filters.ticket_params import filter_from_list_params
from tiqora.filters.tickets import count_tickets, ticket_query_scope
from tiqora.permissions.engine import PermissionEngine
from tiqora.permissions.sharing import ShareGroupError
from tiqora.reporting.cache import CacheBackend
from tiqora.reporting.labels import resolve_labels
from tiqora.reporting.schemas import ReportOwnerOut
from tiqora.reporting.sql import ReportQueryTimeout, is_timeout_error, statement_timeout
from tiqora.saved_searches import store
from tiqora.saved_searches.schemas import (
    SavedSearchCountsOut,
    SavedSearchIn,
    SavedSearchOut,
    SavedSearchPrefsIn,
    SavedSearchUpdateIn,
)
from tiqora.saved_searches.store import (
    SavedSearch,
    SavedSearchConflict,
    SavedSearchData,
    SavedSearchForbidden,
    SavedSearchInvalid,
    SavedSearchNotFound,
    SavedSearchPrefs,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/saved-searches", tags=["saved-searches"])

NOT_FOUND_MESSAGE = "Gespeicherte Suche nicht gefunden."
CONFLICT_MESSAGE = "Die Suche wurde inzwischen geändert. Bitte neu laden."

#: saved-search id in a path: bounded to the database's BIGINT so huge ids are a 422, not a 500
SearchId = Annotated[int, Path(ge=1, le=2**63 - 1)]


async def get_count_cache(redis_client: Annotated[Any, Depends(get_redis)]) -> CacheBackend:
    """Backend of the sidebar-count cache (Redis); tests override it with an in-memory one."""
    return cast(CacheBackend, redis_client)


CountCache = Annotated[CacheBackend, Depends(get_count_cache)]


def _unprocessable(message: str, path: str) -> NoReturn:
    raise HTTPException(status_code=422, detail={"message": message, "path": path})


def _store_error(exc: Exception) -> NoReturn:
    if isinstance(exc, SavedSearchNotFound):
        raise HTTPException(status_code=404, detail={"message": NOT_FOUND_MESSAGE}) from None
    if isinstance(exc, SavedSearchForbidden):
        raise HTTPException(status_code=403, detail={"message": exc.message}) from None
    if isinstance(exc, SavedSearchConflict):
        raise HTTPException(
            status_code=409,
            detail={"message": CONFLICT_MESSAGE, "current_version": exc.current_version},
        ) from None
    if isinstance(exc, ShareGroupError | SavedSearchInvalid | FilterError):
        _unprocessable(exc.message, exc.path)
    raise exc


_STORE_ERRORS = (
    SavedSearchNotFound,
    SavedSearchForbidden,
    SavedSearchConflict,
    SavedSearchInvalid,
    ShareGroupError,
)


async def _owner_names(session: AsyncSession, searches: list[SavedSearch]) -> dict[str, str]:
    ids = sorted({s.owner_user_id for s in searches})
    return await resolve_labels(session, "user", ids) if ids else {}


def _out(
    search: SavedSearch,
    prefs: SavedSearchPrefs,
    user_id: int,
    admin: bool,
    owner_names: dict[str, str],
) -> SavedSearchOut:
    is_owner = search.owner_user_id == user_id
    return SavedSearchOut(
        id=search.id,
        name=search.name,
        criteria=search.criteria,
        criteria_error=search.criteria_error,
        include_archived=search.include_archived,
        sort_by=search.sort_by,  # type: ignore[arg-type]
        sort_order=search.sort_order,  # type: ignore[arg-type]
        owner=ReportOwnerOut(
            user_id=search.owner_user_id,
            name=owner_names.get(str(search.owner_user_id), str(search.owner_user_id)),
        ),
        is_owner=is_owner,
        can_edit=is_owner or admin,
        group_ids=list(search.group_ids),
        show_in_sidebar=prefs.show_in_sidebar,
        show_count=prefs.show_count,
        position=prefs.position,
        source=search.source,
        source_name=search.source_name,
        version=search.version,
        changed_at=search.changed_at,
    )


async def _data_from(session: AsyncSession, body: SavedSearchIn) -> SavedSearchData:
    """Resolve ``criteria`` / ``list_params`` to a validated filter."""
    include_archived = body.include_archived
    if body.list_params is not None:
        criteria = filter_from_list_params(body.list_params)
        include_archived = include_archived or body.list_params.include_archived
        prefix = "list_params"
    else:
        assert body.criteria is not None  # the schema demands exactly one
        criteria = body.criteria
        prefix = "criteria"
    try:
        await store.validate_criteria(session, criteria)
    except FilterError as exc:
        _unprocessable(exc.message, f"{prefix}.{exc.path}" if exc.path else prefix)
    return SavedSearchData(
        name=body.name,
        criteria=criteria,
        include_archived=include_archived,
        sort_by=body.sort_by,
        sort_order=body.sort_order,
        group_ids=tuple(body.group_ids),
    )


@router.get("", response_model=list[SavedSearchOut])
async def list_saved_searches(user: CurrentUser, session: DbSession) -> list[SavedSearchOut]:
    """Own searches and those shared with a group the caller holds ``ro`` on."""
    admin = await PermissionEngine(session).is_admin(user.id)
    entries = await store.list_visible(session, user.id)
    names = await _owner_names(session, [s for s, _p in entries])
    return [_out(s, p, user.id, admin, names) for s, p in entries]


@router.post("", response_model=SavedSearchOut, status_code=status.HTTP_201_CREATED)
async def create_saved_search(
    body: SavedSearchIn, user: CurrentUser, session: DbSession
) -> SavedSearchOut:
    """Save a search from ``criteria`` or from the queue list's ``list_params``.

    ``group_ids`` shares it with groups the caller holds ``ro`` on (admins: any valid group).
    """
    admin = await PermissionEngine(session).is_admin(user.id)
    data = await _data_from(session, body)
    try:
        created = await store.create_search(session, user.id, data)
        position = await store.next_position(session, user.id)
        prefs = SavedSearchPrefs(body.show_in_sidebar, body.show_count, position)
        await store.set_prefs(session, created.id, user.id, prefs)
    except (*_STORE_ERRORS, FilterError) as exc:
        _store_error(exc)
    out = _out(created, prefs, user.id, admin, await _owner_names(session, [created]))
    await session.commit()
    return out


async def _sidebar_counts(
    session: AsyncSession,
    user: AuthenticatedUser,
    admin: bool,
    entries: list[tuple[SavedSearch, SavedSearchPrefs]],
    cache: CacheBackend,
) -> dict[str, int | None]:
    """Count each search in the viewer's scope; ``None`` = not countable.

    One deadline (``COUNTS_DEADLINE_SECONDS``) for the whole loop, at most
    ``COUNT_TIMEOUT_SECONDS`` per count; the rest of the time budget goes to the next one.
    """
    deadline = time.monotonic() + store.COUNTS_DEADLINE_SECONDS
    counts: dict[str, int | None] = {}
    for search, _prefs in entries:
        key = f"saved-search-count:{user.id}:{search.id}:{search.version}"
        counts[str(search.id)] = None
        if search.criteria is None:
            continue
        try:
            cached = await cache.get(key)
            if cached is not None:
                counts[str(search.id)] = int(cached)
                continue
        except Exception as exc:  # noqa: BLE001 - a broken cache is a miss
            logger.warning("saved_search_count_cache_failed", error=str(exc))
        remaining = deadline - time.monotonic()
        if remaining < 1:
            continue  # out of time: this and every later search stays null
        seconds = max(1, min(store.COUNT_TIMEOUT_SECONDS, int(remaining)))
        try:
            async with statement_timeout(session, seconds):
                scope = await ticket_query_scope(
                    session, user.id, include_archived=admin and search.include_archived
                )
                counts[str(search.id)] = await count_tickets(session, search.criteria, scope)
        except (FilterError, ValidationError, ReportQueryTimeout):
            counts[str(search.id)] = None
        except Exception as exc:  # noqa: BLE001
            if not is_timeout_error(exc):
                raise
            counts[str(search.id)] = None
        finally:
            await session.rollback()
        value = counts[str(search.id)]
        if value is not None:
            try:
                await cache.set(key, str(value), ex=store.COUNTS_CACHE_SECONDS)
            except Exception as exc:  # noqa: BLE001
                logger.warning("saved_search_count_cache_failed", error=str(exc))
    return counts


@router.get("/counts", response_model=SavedSearchCountsOut)
async def saved_search_counts(
    user: CurrentUser, session: DbSession, cache: CountCache
) -> SavedSearchCountsOut:
    """Ticket counts for the caller's sidebar entries that show a count (viewer's scope).

    Only entries with both settings on are counted, then the first
    ``MAX_SIDEBAR_COUNTS`` by position. Cached 30 s per viewer; clients should not poll
    faster than every 60 s.
    """
    admin = await PermissionEngine(session).is_admin(user.id)
    entries = [
        (s, p)
        for s, p in await store.list_visible(session, user.id)
        if p.show_in_sidebar and p.show_count
    ][: store.MAX_SIDEBAR_COUNTS]
    return SavedSearchCountsOut(counts=await _sidebar_counts(session, user, admin, entries, cache))


@router.get("/{search_id}", response_model=SavedSearchOut)
async def get_saved_search(
    search_id: SearchId, user: CurrentUser, session: DbSession
) -> SavedSearchOut:
    admin = await PermissionEngine(session).is_admin(user.id)
    try:
        search, prefs = await store.get_visible(session, search_id, user.id, admin=admin)
    except _STORE_ERRORS as exc:
        _store_error(exc)
    return _out(search, prefs, user.id, admin, await _owner_names(session, [search]))


@router.put("/{search_id}", response_model=SavedSearchOut)
async def update_saved_search(
    search_id: SearchId, body: SavedSearchUpdateIn, user: CurrentUser, session: DbSession
) -> SavedSearchOut:
    """Replace a search completely (owner or admin). ``version`` is the optimistic lock (409).

    Sharing groups already stored may stay without being checked again; added ones must be
    groups the caller may share with. The caller's sidebar settings are not part of this call.
    """
    admin = await PermissionEngine(session).is_admin(user.id)
    data = await _data_from(session, body)
    try:
        updated = await store.update_search(
            session, search_id, user.id, data, version=body.version, admin=admin
        )
        _s, prefs = await store.get_visible(session, search_id, user.id, admin=admin)
    except _STORE_ERRORS as exc:
        _store_error(exc)
    out = _out(updated, prefs, user.id, admin, await _owner_names(session, [updated]))
    await session.commit()
    return out


@router.put("/{search_id}/prefs", response_model=SavedSearchOut)
async def set_saved_search_prefs(
    search_id: SearchId, body: SavedSearchPrefsIn, user: CurrentUser, session: DbSession
) -> SavedSearchOut:
    """The caller's own sidebar entry, count badge and position for a search they can see."""
    admin = await PermissionEngine(session).is_admin(user.id)
    try:
        search, _old = await store.get_visible(session, search_id, user.id, admin=admin)
        position = await store.resolve_position(session, search_id, user.id, body.position)
        prefs = SavedSearchPrefs(body.show_in_sidebar, body.show_count, position)
        await store.set_prefs(session, search_id, user.id, prefs)
    except _STORE_ERRORS as exc:
        _store_error(exc)
    out = _out(search, prefs, user.id, admin, await _owner_names(session, [search]))
    await session.commit()
    return out


@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_search(
    search_id: SearchId, user: CurrentUser, session: DbSession
) -> Response:
    admin = await PermissionEngine(session).is_admin(user.id)
    try:
        await store.delete_search(session, search_id, user.id, admin=admin)
    except _STORE_ERRORS as exc:
        _store_error(exc)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
