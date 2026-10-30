"""Read-only access to Znuny statistics in ``xml_storage`` (no JOIN with tiqora_* tables)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Row, bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketStateType
from tiqora.db.legacy.user import PermissionGroups, Users
from tiqora.db.tiqora.models import TiqoraCmdbClass, TiqoraItsmImportMap
from tiqora.itsm.znuny_adapter.detect import detect_znuny_itsm
from tiqora.reporting.engine import CLIENT_GRACE_SECONDS, QueryBudget
from tiqora.reporting.sql import ReportQueryTimeout, invalidate_quietly, statement_timeout
from tiqora.reporting.znuny_stats.mapping_common import StatLookups
from tiqora.reporting.znuny_stats.model import ZnunyStat, stat_from_xml_hash
from tiqora.reporting.znuny_stats.xml_hash import (
    MAX_STAT_ROWS,
    XmlHashError,
    build_xml_hash,
)
from tiqora.saved_searches.znuny_reader import load_lookups

MAX_PREVIEW_STATS: Final = 500
_CHUNK: Final = 200
MAX_TOTAL_ROWS: Final = 200_000  # leaves held in memory across one load_stats call
_PERMISSION_LIKE: Final = "%{'Permission'}%"
_CHANGE_STATE_CLASS: Final = "ITSM::ChangeManagement::Change::State"


async def _query(
    session: AsyncSession,
    stmt: Any,
    params: dict[str, Any] | None = None,
    *,
    budget: QueryBudget | None = None,
) -> list[Row[Any]]:
    """Run one xml_storage statement; with a budget under the dialect statement timeout.

    Raises ``ReportQueryTimeout`` when the statement or the shared inline deadline is exceeded.
    """
    if budget is None:
        return list((await session.execute(stmt, params)).all())
    seconds = budget.statement_seconds()
    try:
        async with statement_timeout(session, seconds):
            try:
                result = await asyncio.wait_for(
                    session.execute(stmt, params), seconds + CLIENT_GRACE_SECONDS
                )
            except (TimeoutError, asyncio.CancelledError):
                await invalidate_quietly(session)
                raise
            return list(result.all())
    except TimeoutError as exc:
        raise ReportQueryTimeout(f"statement exceeded {seconds}s (client side)") from exc


@dataclass(frozen=True, slots=True)
class StatLoadError:
    stat_id: int
    message: str  # German


def _numeric_key(key: object) -> int | None:
    text_key = str(key)
    return int(text_key) if text_key.isascii() and text_key.isdigit() else None


async def list_stat_ids(
    session: AsyncSession,
    *,
    permission_group_ids: Collection[int] | None = None,
    budget: QueryBudget | None = None,
) -> list[int]:
    ids, _more = await list_stat_ids_page(
        session, permission_group_ids=permission_group_ids, budget=budget
    )
    return ids


async def list_stat_ids_page(
    session: AsyncSession,
    *,
    permission_group_ids: Collection[int] | None = None,
    budget: QueryBudget | None = None,
) -> tuple[list[int], bool]:
    """Numeric ``xml_key`` values of stored statistics, sorted, capped at MAX_PREVIEW_STATS.

    The second value says that more stats than MAX_PREVIEW_STATS qualify.
    ``permission_group_ids=None`` lists everything (admins). Otherwise only stats with a
    ``Permission`` leaf naming one of these group ids qualify; the filter runs BEFORE the cap
    and reads only the cheap Permission leaves, so a large site cannot hide visible stats.
    """
    if permission_group_ids is None:
        rows = await _query(
            session,
            text("SELECT DISTINCT xml_key FROM xml_storage WHERE xml_type = 'Stats'"),
            budget=budget,
        )
        keys = [k for (k,) in rows]
    else:
        wanted = {str(g) for g in permission_group_ids}
        if not wanted:
            return [], False
        rows = await _query(
            session,
            text(
                "SELECT xml_key, xml_content_value FROM xml_storage "
                "WHERE xml_type = 'Stats' AND xml_content_key LIKE :pat"
            ),
            {"pat": _PERMISSION_LIKE},
            budget=budget,
        )
        keys = [k for (k, value) in rows if value is not None and str(value) in wanted]
    ids = sorted({n for n in map(_numeric_key, keys) if n is not None})
    return ids[:MAX_PREVIEW_STATS], len(ids) > MAX_PREVIEW_STATS


async def load_stats(
    session: AsyncSession,
    stat_ids: Sequence[int],
    *,
    row_budget: int = MAX_TOTAL_ROWS,
    budget: QueryBudget | None = None,
) -> tuple[list[ZnunyStat], list[StatLoadError], bool]:
    """Load and parse the given statistics with bounded memory.

    Leaves are counted per chunk first; a stat above MAX_STAT_ROWS becomes a StatLoadError
    without its rows being fetched, and loading stops once ``row_budget`` leaves are held.
    The third value is ``truncated``: True when stats were left unread because of the budget.
    """
    ids = sorted(set(stat_ids))
    count_stmt = text(
        "SELECT xml_key, COUNT(*) FROM xml_storage "
        "WHERE xml_type = 'Stats' AND xml_key IN :keys GROUP BY xml_key"
    ).bindparams(bindparam("keys", expanding=True))
    stmt = text(
        "SELECT xml_key, xml_content_key, xml_content_value FROM xml_storage "
        "WHERE xml_type = 'Stats' AND xml_key IN :keys ORDER BY xml_key, xml_content_key"
    ).bindparams(bindparam("keys", expanding=True))
    stats: list[ZnunyStat] = []
    errors: list[StatLoadError] = []
    used = 0
    truncated = False
    for start in range(0, len(ids), _CHUNK):
        chunk_ids = ids[start : start + _CHUNK]
        counts: dict[int, int] = {}
        for key, n in await _query(
            session, count_stmt, {"keys": [str(i) for i in chunk_ids]}, budget=budget
        ):
            num = _numeric_key(key)
            if num is not None:
                counts[num] = int(n)
        take: list[int] = []
        for stat_id in chunk_ids:
            n = counts.get(stat_id, 0)
            if n == 0:
                errors.append(StatLoadError(stat_id, "Die Statistik wurde nicht gefunden."))
            elif n > MAX_STAT_ROWS:
                errors.append(StatLoadError(stat_id, "Die Statistik ist zu groß, um sie zu lesen."))
            elif used + n > row_budget:
                truncated = True
            else:
                used += n
                take.append(stat_id)
        if not take:
            continue
        rows_by_id: dict[int, list[tuple[str, str | None]]] = defaultdict(list)
        for key, content_key, value in await _query(
            session, stmt, {"keys": [str(i) for i in take]}, budget=budget
        ):
            num = _numeric_key(key)
            if num is not None:
                rows_by_id[num].append((content_key, value))
        for stat_id in take:
            try:
                xml_hash = build_xml_hash(rows_by_id.get(stat_id, []))
                stats.append(stat_from_xml_hash(xml_hash, origin="db", stat_id=stat_id))
            except XmlHashError as exc:
                errors.append(StatLoadError(stat_id, exc.message))
    return stats, errors, truncated


async def _load_itsm_lookups(session: AsyncSession) -> dict[str, Any]:
    """ITSM mode, import maps, CI class names and Znuny change states (D10); no JOIN."""
    from tiqora.cmdb.gate import current_itsm_mode  # noqa: PLC0415 - import cycle

    maps: dict[str, dict[int, int]] = {"class": {}, "catalog": {}}
    map_rows = await session.execute(
        select(
            TiqoraItsmImportMap.source_kind,
            TiqoraItsmImportMap.source_id,
            TiqoraItsmImportMap.target_id,
        ).where(TiqoraItsmImportMap.source_kind.in_(("class", "catalog")))
    )
    for kind, source_id, target_id in map_rows.all():
        znuny_id = _numeric_key(source_id)
        if znuny_id is not None:
            maps[str(kind)][znuny_id] = int(target_id)
    class_ids: dict[str, int] = {}
    for class_id, name, class_key in (
        await session.execute(
            select(TiqoraCmdbClass.id, TiqoraCmdbClass.name, TiqoraCmdbClass.class_key)
        )
    ).all():
        class_ids[str(name).casefold()] = int(class_id)
        class_ids.setdefault(str(class_key).casefold(), int(class_id))
    change_states: dict[int, str] = {}
    # general_catalog is only queried when Znuny's change tables exist: a failed statement
    # would abort the PostgreSQL transaction
    if (await detect_znuny_itsm(session)).change_installed:
        rows = await session.execute(
            text("SELECT id, name FROM general_catalog WHERE general_catalog_class = :c"),
            {"c": _CHANGE_STATE_CLASS},
        )
        change_states = {int(i): str(n) for i, n in rows.all()}
    return {
        "itsm_mode": str(await current_itsm_mode(session)),
        "cmdb_class_by_znuny_id": maps["class"],
        "cmdb_catalog_by_znuny_id": maps["catalog"],
        "cmdb_class_ids_by_name": class_ids,
        "change_state_names": change_states,
    }


async def load_stat_lookups(session: AsyncSession, user_id: int) -> StatLookups:
    """Name -> id maps for file imports, state types and the agent's zone (one query per table).

    Queue names are the full names as Znuny stores them (``Parent::Child``), casefolded.
    Only valid queues, users and groups qualify; states and priorities are all listed because a
    stat may name an invalid one. No JOIN with ``tiqora_*`` tables.
    """

    async def names(model_id: object, model_name: object, *where: object) -> dict[str, int]:
        stmt = select(model_id, model_name).where(*where)  # type: ignore[call-overload]
        return {str(n).casefold(): int(i) for i, n in (await session.execute(stmt)).all()}

    state_types = {
        int(i): str(n)
        for i, n in (await session.execute(select(TicketStateType.id, TicketStateType.name))).all()
    }
    search = await load_lookups(session, user_id)
    itsm = await _load_itsm_lookups(session)
    return StatLookups(
        state_type_names=state_types,
        queue_ids_by_name=await names(Queue.id, Queue.name, Queue.valid_id == 1),
        state_ids_by_name=await names(TicketState.id, TicketState.name),
        priority_ids_by_name=await names(TicketPriority.id, TicketPriority.name),
        user_ids_by_login=await names(Users.id, Users.login, Users.valid_id == 1),
        group_ids_by_name=await names(
            PermissionGroups.id, PermissionGroups.name, PermissionGroups.valid_id == 1
        ),
        default_time_zone=search.time_zone,
        search=search,
        **itsm,
    )
