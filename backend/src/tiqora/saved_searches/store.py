"""Saved-search store: visibility, create / update / delete, per-viewer prefs, Znuny re-import.

Visibility: the owner, or a search shared with a group the viewer holds ``ro`` on; admins may
open any search **by id** (their default list still shows only own and shared ones). An
invisible search is indistinguishable from an unknown one (:class:`SavedSearchNotFound`).
Sharing never widens what a viewer sees: counts and lists always run in the viewer's own queue
scope (``tiqora.filters.tickets.ticket_query_scope``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import (
    TiqoraSavedSearch,
    TiqoraSavedSearchGroup,
    TiqoraSavedSearchPref,
)
from tiqora.filters import Filter, validate_filter
from tiqora.filters.tickets import ticket_filter_fields
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.permissions.sharing import check_share_groups

MAX_SAVED_SEARCHES_PER_USER: Final = 200
MAX_SIDEBAR_COUNTS: Final = 15
COUNT_TIMEOUT_SECONDS: Final = 5
#: one shared deadline for all counts of a request (nginx cuts /api/ at 90 s); R-COUNTBUDGET
COUNTS_DEADLINE_SECONDS: Final = 20
COUNTS_CACHE_SECONDS: Final = 30
BROKEN_CRITERIA_MESSAGE: Final = "Die gespeicherte Suche kann nicht mehr gelesen werden."
LIMIT_MESSAGE: Final = (
    f"Es sind höchstens {MAX_SAVED_SEARCHES_PER_USER} gespeicherte Suchen pro Person möglich."
)

Source = Literal["tiqora", "znuny"]


@dataclass(frozen=True, slots=True)
class SavedSearch:
    id: int
    owner_user_id: int
    name: str
    criteria: Filter | None
    criteria_error: str | None  # set when the stored JSON no longer parses / validates
    include_archived: bool
    sort_by: str
    sort_order: str
    source: Source
    source_name: str | None
    group_ids: tuple[int, ...]
    version: int
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class SavedSearchPrefs:
    show_in_sidebar: bool
    show_count: bool
    position: int


@dataclass(frozen=True, slots=True)
class SavedSearchData:
    name: str
    criteria: Filter
    include_archived: bool
    sort_by: str
    sort_order: str
    group_ids: tuple[int, ...]


class SavedSearchNotFound(LookupError):
    """Unknown id, or not visible to the viewer."""


class SavedSearchForbidden(PermissionError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SavedSearchConflict(Exception):  # noqa: N818
    def __init__(self, current_version: int) -> None:
        super().__init__(current_version)
        self.current_version = current_version


class SavedSearchInvalid(ValueError):
    """A request value the store rejects (German message, API path)."""

    def __init__(self, message: str, path: str = "name") -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def dump_criteria(flt: Filter) -> str:
    return json.dumps(flt.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True)


def load_criteria(raw: str) -> tuple[Filter | None, str | None]:
    try:
        return Filter.model_validate_json(raw), None
    except ValidationError:
        return None, BROKEN_CRITERIA_MESSAGE


def default_prefs(*, owner: bool) -> SavedSearchPrefs:
    return SavedSearchPrefs(show_in_sidebar=owner, show_count=False, position=0)


def _to_search(row: TiqoraSavedSearch, group_ids: tuple[int, ...]) -> SavedSearch:
    criteria, error = load_criteria(row.criteria)
    return SavedSearch(
        id=int(row.id),
        owner_user_id=int(row.owner_user_id),
        name=row.name,
        criteria=criteria,
        criteria_error=error,
        include_archived=bool(row.include_archived),
        sort_by=row.sort_by,
        sort_order=row.sort_order,
        source="znuny" if row.source == "znuny" else "tiqora",
        source_name=row.source_name,
        group_ids=group_ids,
        version=int(row.version),
        changed_at=row.changed_at,
    )


async def validate_criteria(session: AsyncSession, flt: Filter) -> None:
    """Raise :class:`tiqora.filters.FilterError` when ``flt`` is not valid for tickets."""
    fields = {f.key: f for f in await ticket_filter_fields(session)}
    validate_filter(flt, fields.get)


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise SavedSearchInvalid("Bitte einen Namen angeben.", path="name")
    return cleaned


async def _groups_of(session: AsyncSession, search_ids: list[int]) -> dict[int, tuple[int, ...]]:
    if not search_ids:
        return {}
    rows = await session.execute(
        select(TiqoraSavedSearchGroup.saved_search_id, TiqoraSavedSearchGroup.group_id).where(
            TiqoraSavedSearchGroup.saved_search_id.in_(search_ids)
        )
    )
    out: dict[int, list[int]] = {}
    for sid, gid in rows.all():
        out.setdefault(int(sid), []).append(int(gid))
    return {sid: tuple(sorted(g)) for sid, g in out.items()}


async def _prefs_of(
    session: AsyncSession, search_ids: list[int], user_id: int
) -> dict[int, SavedSearchPrefs]:
    if not search_ids:
        return {}
    rows = await session.execute(
        select(TiqoraSavedSearchPref).where(
            TiqoraSavedSearchPref.user_id == user_id,
            TiqoraSavedSearchPref.saved_search_id.in_(search_ids),
        )
    )
    return {
        int(p.saved_search_id): SavedSearchPrefs(
            bool(p.show_in_sidebar), bool(p.show_count), int(p.position)
        )
        for p in rows.scalars().all()
    }


async def _load(session: AsyncSession, search_id: int) -> SavedSearch | None:
    row = (
        await session.execute(
            select(TiqoraSavedSearch)
            .where(TiqoraSavedSearch.id == search_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    groups = await _groups_of(session, [search_id])
    return _to_search(row, groups.get(search_id, ()))


async def list_visible(
    session: AsyncSession, user_id: int
) -> list[tuple[SavedSearch, SavedSearchPrefs]]:
    """Own and group-shared searches, ordered by the viewer's position, then name."""
    member = await PermissionEngine(session).groups_for_permission(user_id, "ro")
    conditions = [TiqoraSavedSearch.owner_user_id == user_id]
    if member:
        shared = select(TiqoraSavedSearchGroup.saved_search_id).where(
            TiqoraSavedSearchGroup.group_id.in_(member)
        )
        conditions.append(TiqoraSavedSearch.id.in_(shared))
    rows = (
        (
            await session.execute(
                select(TiqoraSavedSearch)
                .where(or_(*conditions))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    ids = [int(r.id) for r in rows]
    groups = await _groups_of(session, ids)
    prefs = await _prefs_of(session, ids, user_id)
    out = [
        (
            _to_search(r, groups.get(int(r.id), ())),
            prefs.get(int(r.id)) or default_prefs(owner=int(r.owner_user_id) == user_id),
        )
        for r in rows
    ]
    out.sort(key=lambda sp: (sp[1].position, sp[0].name.casefold(), sp[0].id))
    return out


async def _is_visible(
    session: AsyncSession, search: SavedSearch, user_id: int, *, admin: bool
) -> bool:
    if admin or search.owner_user_id == user_id:
        return True
    if not search.group_ids:
        return False
    member = await PermissionEngine(session).groups_for_permission(user_id, "ro")
    return bool(member & set(search.group_ids))


async def get_visible(
    session: AsyncSession, search_id: int, user_id: int, *, admin: bool
) -> tuple[SavedSearch, SavedSearchPrefs]:
    search = await _load(session, search_id)
    if search is None or not await _is_visible(session, search, user_id, admin=admin):
        raise SavedSearchNotFound(str(search_id))
    prefs = (await _prefs_of(session, [search_id], user_id)).get(search_id)
    return search, prefs or default_prefs(owner=search.owner_user_id == user_id)


async def _replace_groups(session: AsyncSession, search_id: int, group_ids: Iterable[int]) -> None:
    await session.execute(
        delete(TiqoraSavedSearchGroup).where(TiqoraSavedSearchGroup.saved_search_id == search_id)
    )
    for gid in group_ids:
        session.add(TiqoraSavedSearchGroup(saved_search_id=search_id, group_id=gid))
    await session.flush()


async def _check_limit(session: AsyncSession, user_id: int) -> None:
    owned = (
        await session.execute(
            select(func.count())
            .select_from(TiqoraSavedSearch)
            .where(TiqoraSavedSearch.owner_user_id == user_id)
        )
    ).scalar_one()
    if int(owned) >= MAX_SAVED_SEARCHES_PER_USER:
        raise SavedSearchInvalid(LIMIT_MESSAGE, path="name")


async def create_search(session: AsyncSession, user_id: int, data: SavedSearchData) -> SavedSearch:
    name = _clean_name(data.name)
    groups = await check_share_groups(session, user_id, data.group_ids) if data.group_ids else ()
    await _check_limit(session, user_id)
    now = utc_now()
    row = TiqoraSavedSearch(
        owner_user_id=user_id,
        name=name,
        criteria=dump_criteria(data.criteria),
        include_archived=data.include_archived,
        sort_by=data.sort_by,
        sort_order=data.sort_order,
        source="tiqora",
        source_name=None,
        version=1,
        created_at=now,
        changed_at=now,
    )
    session.add(row)
    await session.flush()
    await _replace_groups(session, int(row.id), groups)
    return _to_search(row, groups)


async def update_search(
    session: AsyncSession,
    search_id: int,
    user_id: int,
    data: SavedSearchData,
    *,
    version: int,
    admin: bool,
) -> SavedSearch:
    """Full replace by the owner or an admin. Invisible -> NotFound; visible, not editable ->
    Forbidden; stale ``version`` -> Conflict (nothing is written, group rows stay)."""
    current, _prefs = await get_visible(session, search_id, user_id, admin=admin)
    if current.owner_user_id != user_id and not admin:
        raise SavedSearchForbidden("Diese Suche darf nur von ihrem Besitzer geändert werden.")
    if version != current.version:
        raise SavedSearchConflict(current.version)
    name = _clean_name(data.name)
    # R-SHAREKEEP: groups already stored are kept without re-validation; only added ones count
    wanted = tuple(sorted(set(data.group_ids)))
    added = [g for g in wanted if g not in current.group_ids]
    if added:
        await check_share_groups(session, user_id, added)
    result = await session.execute(
        update(TiqoraSavedSearch)
        .where(TiqoraSavedSearch.id == search_id, TiqoraSavedSearch.version == version)
        .values(
            name=name,
            criteria=dump_criteria(data.criteria),
            include_archived=data.include_archived,
            sort_by=data.sort_by,
            sort_order=data.sort_order,
            version=TiqoraSavedSearch.version + 1,
            changed_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]  # two concurrent saves / a delete
        latest = await _load(session, search_id)
        if latest is None:
            raise SavedSearchNotFound(str(search_id))
        raise SavedSearchConflict(latest.version)
    await _replace_groups(session, search_id, wanted)
    fresh = await _load(session, search_id)
    assert fresh is not None
    return fresh


async def next_position(session: AsyncSession, user_id: int) -> int:
    top = (
        await session.execute(
            select(func.max(TiqoraSavedSearchPref.position)).where(
                TiqoraSavedSearchPref.user_id == user_id
            )
        )
    ).scalar_one()
    return 0 if top is None else int(top) + 1


async def set_prefs(
    session: AsyncSession, search_id: int, user_id: int, prefs: SavedSearchPrefs
) -> None:
    """Upsert the viewer's display settings. The caller checked visibility first (R-PREFS)."""
    values = {
        "show_in_sidebar": prefs.show_in_sidebar,
        "show_count": prefs.show_count,
        "position": prefs.position,
    }

    async def _update() -> int:
        res = await session.execute(
            update(TiqoraSavedSearchPref)
            .where(
                TiqoraSavedSearchPref.saved_search_id == search_id,
                TiqoraSavedSearchPref.user_id == user_id,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return int(res.rowcount)  # type: ignore[attr-defined]

    if await _update():
        return
    try:
        async with session.begin_nested():
            session.add(TiqoraSavedSearchPref(saved_search_id=search_id, user_id=user_id, **values))
            await session.flush()
    except IntegrityError:  # a parallel request inserted the row first
        await _update()


async def resolve_position(
    session: AsyncSession, search_id: int, user_id: int, requested: int | None
) -> int:
    """``requested``, else the viewer's stored position, else the end of their list."""
    if requested is not None:
        return requested
    stored = (
        await session.execute(
            select(TiqoraSavedSearchPref.position).where(
                TiqoraSavedSearchPref.saved_search_id == search_id,
                TiqoraSavedSearchPref.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    return int(stored) if stored is not None else await next_position(session, user_id)


async def delete_search(
    session: AsyncSession, search_id: int, user_id: int, *, admin: bool
) -> None:
    """Hard delete (group and pref rows go with it). Owner or admin only."""
    current, _prefs = await get_visible(session, search_id, user_id, admin=admin)
    if current.owner_user_id != user_id and not admin:
        raise SavedSearchForbidden("Diese Suche darf nur von ihrem Besitzer gelöscht werden.")
    await session.execute(
        delete(TiqoraSavedSearchGroup).where(TiqoraSavedSearchGroup.saved_search_id == search_id)
    )
    await session.execute(
        delete(TiqoraSavedSearchPref).where(TiqoraSavedSearchPref.saved_search_id == search_id)
    )
    await session.execute(delete(TiqoraSavedSearch).where(TiqoraSavedSearch.id == search_id))
    await session.flush()


async def _find_imported(
    session: AsyncSession, user_id: int, source_name: str, *, lock: bool = False
) -> TiqoraSavedSearch | None:
    """``lock``: a locking read, which sees the latest committed row even on MariaDB's
    REPEATABLE READ (a plain SELECT would keep reading the transaction's old snapshot)."""
    stmt = select(TiqoraSavedSearch).where(
        TiqoraSavedSearch.owner_user_id == user_id,
        TiqoraSavedSearch.source == "znuny",
        TiqoraSavedSearch.source_name == source_name,
    )
    if lock:
        stmt = stmt.with_for_update()
    return (
        await session.execute(stmt.execution_options(populate_existing=True))
    ).scalar_one_or_none()


async def _refresh_imported(
    session: AsyncSession, row_id: int, criteria: Filter, include_archived: bool
) -> None:
    await session.execute(
        update(TiqoraSavedSearch)
        .where(TiqoraSavedSearch.id == row_id)
        .values(
            criteria=dump_criteria(criteria),
            include_archived=include_archived,
            version=TiqoraSavedSearch.version + 1,
            changed_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )


async def upsert_imported(
    session: AsyncSession,
    user_id: int,
    *,
    source_name: str,
    criteria: Filter,
    include_archived: bool,
) -> tuple[int, Literal["created", "updated"]]:
    """Create or refresh the search imported from Znuny profile ``source_name`` (per owner).

    An update replaces criteria and archive flag and bumps ``version``; name, sharing and
    sorting stay. Two tabs applying the same import race on ``uq_tiqora_saved_search_source``;
    the loser falls back to the update path (R-UPSERTRACE).
    """
    existing = await _find_imported(session, user_id, source_name)
    if existing is not None:
        await _refresh_imported(session, int(existing.id), criteria, include_archived)
        return int(existing.id), "updated"
    await _check_limit(session, user_id)
    now = utc_now()
    row = TiqoraSavedSearch(
        owner_user_id=user_id,
        name=source_name,
        criteria=dump_criteria(criteria),
        include_archived=include_archived,
        sort_by="age",
        sort_order="desc",
        source="znuny",
        source_name=source_name,
        version=1,
        created_at=now,
        changed_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        raced = await _find_imported(session, user_id, source_name, lock=True)
        if raced is None:
            raise
        await _refresh_imported(session, int(raced.id), criteria, include_archived)
        return int(raced.id), "updated"
    return int(row.id), "created"
