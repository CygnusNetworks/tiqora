"""Read-only SQL on Znuny's ITSMChangeManagement tables (the only module with ``change_*`` SQL).

Every statement is ``text()`` with bound parameters; table and column names are constants of this
module, never input. Joins stay inside Znuny's own tables (``change_history`` with
``change_history_type``, ``link_relation`` with ``link_object``/``link_type``/``link_state``,
``dynamic_field_value`` with ``dynamic_field``); nothing here touches a ``tiqora_*`` table. IN
lists are chunked (:data:`~tiqora.itsm.znuny_adapter.catalog.CHUNK`). The reader never writes
and never commits. The schema is identical in both Znuny lines (survey Q9).

Text search (like the CI search of ``znuny_adapter.reader``): number and title match every needle,
description and justification from three characters on; a search with text is bounded by
``statement_timeout`` (a hit is :class:`LegacySearchTooBroad`).

Links (survey Q1, ruling S1): a change links only to tickets; CIs and services hang off its work
orders, so the links of both are read and the work-order ones carry its number. Only ``Valid``
relations count, in either direction.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, Final

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.change.legacy.mapping import (
    change_window,
    dynamic_field_text,
    history_display,
    history_ids,
    plain_text,
    znuny_time,
)
from tiqora.itsm.change.legacy.model import (
    CabKind,
    LegacyChange,
    LegacyChangeSummary,
    LegacyHistoryEntry,
    LegacyLink,
    LegacyWorkOrder,
)
from tiqora.itsm.znuny_adapter.catalog import chunks
from tiqora.itsm.znuny_adapter.detect import detect_znuny_itsm
from tiqora.itsm.znuny_adapter.reader import (
    ATTRIBUTE_SEARCH_MIN_CHARS,
    SEARCH_TIMEOUT_SECONDS,
    like_contains,
)
from tiqora.reporting.sql import ReportQueryTimeout, statement_timeout

#: most changes one :func:`read_changes` call reads
MAX_READ: Final = 100
#: largest page of :func:`search`
MAX_PAGE: Final = 100

CHANGE_OBJECT: Final = "ITSMChange"
WORKORDER_OBJECT: Final = "ITSMWorkOrder"
TICKET_OBJECT: Final = "Ticket"
CI_OBJECT: Final = "ITSMConfigItem"
SERVICE_OBJECT: Final = "Service"

#: the 15 ``change_*`` and 5 ``condition_*`` tables of the package (survey Q9)
CHANGE_TABLES: Final[tuple[str, ...]] = (
    "change_cab",
    "change_cip_allocate",
    "change_condition",
    "change_history",
    "change_history_type",
    "change_item",
    "change_notification",
    "change_notification_grps",
    "change_notification_message",
    "change_notification_rec",
    "change_number_counter",
    "change_state_machine",
    "change_template",
    "change_template_type",
    "change_workorder",
    "condition_action",
    "condition_attribute",
    "condition_expression",
    "condition_object",
    "condition_operator",
)

_CHANGE_COLUMNS: Final = (
    "SELECT id, change_number, title, description, description_plain, justification,"
    " justification_plain, change_state_id, change_manager_id, change_builder_id, category_id,"
    " impact_id, priority_id, requested_time, create_time, change_time FROM change_item"
)
_WORKORDER_COLUMNS: Final = (
    "SELECT id, change_id, workorder_number, title, instruction, instruction_plain, report,"
    " report_plain, workorder_state_id, workorder_type_id, workorder_agent_id,"
    " planned_start_time, planned_end_time, actual_start_time, actual_end_time, planned_effort,"
    " accounted_time FROM change_workorder"
)
_LINKS_SQL: Final = (
    "SELECT so.name, lr.source_key, tob.name, lr.target_key, lt.name FROM link_relation lr"
    " JOIN link_object so ON so.id = lr.source_object_id"
    " JOIN link_object tob ON tob.id = lr.target_object_id"
    " JOIN link_type lt ON lt.id = lr.type_id"
    " JOIN link_state ls ON ls.id = lr.state_id"
    " WHERE ls.name = 'Valid' AND ((so.name = :obj AND lr.source_key IN :keys)"
    " OR (tob.name = :obj AND lr.target_key IN :keys))"
)
#: number and title: every needle
_SHORT_TEXT_MATCH: Final = (
    "lower(change_number) LIKE lower(:pat) ESCAPE '!' OR lower(title) LIKE lower(:pat) ESCAPE '!'"
)
#: the long texts only from :data:`ATTRIBUTE_SEARCH_MIN_CHARS` characters on (like the CI search)
_LONG_TEXT_MATCH: Final = (
    " OR lower(description_plain) LIKE lower(:pat) ESCAPE '!'"
    " OR lower(justification_plain) LIKE lower(:pat) ESCAPE '!'"
)


def _in(sql: str, *names: str) -> Any:
    """``text(sql)`` with ``names`` as expanding IN parameters (also usable where a parameter
    called ``text`` shadows ``sqlalchemy.text``)."""
    return text(sql).bindparams(*(bindparam(n, expanding=True) for n in names))


def _str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _dt(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"expected a datetime from the database, got {type(value).__name__}")
    return value.replace(tzinfo=None, microsecond=0)


class LegacySearchTooBroad(Exception):
    """The text search ran into its statement timeout (the route answers 409)."""


async def change_tables_present(session: AsyncSession) -> bool:
    """``change_item`` and ``change_workorder`` exist (the ITSM detection, dialect-safe and in
    the current schema only; cached like the mode detection)."""
    return (await detect_znuny_itsm(session)).change_installed


#: what the archive leaves in Znuny and only counts (templates are opaque ``Data::Dumper`` text,
#: ruling S4; conditions and notification rules have no Tiqora counterpart, plan D6/D10)
NOT_ARCHIVED_TABLES: Final[dict[str, str]] = {
    "templates": "change_template",
    "conditions": "change_condition",
    "notifications": "change_notification",
}


async def count_changes(session: AsyncSession) -> int:
    """Number of changes (progress of the archive)."""
    return int((await session.execute(text("SELECT COUNT(*) FROM change_item"))).scalar_one())


async def not_archived_counts(session: AsyncSession) -> dict[str, int]:
    """Rows per :data:`NOT_ARCHIVED_TABLES` kind (the content is never read)."""
    out: dict[str, int] = {}
    for kind, table in NOT_ARCHIVED_TABLES.items():  # constants, never input
        count = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
        out[kind] = int(count.scalar_one())
    return out


async def iter_change_ids(session: AsyncSession, *, after_id: int, limit: int) -> list[int]:
    """Change ids above ``after_id`` in id order, at most ``limit`` (keyset paging)."""
    rows = await session.execute(
        text("SELECT id FROM change_item WHERE id > :a ORDER BY id LIMIT :l"),
        {"a": after_id, "l": max(1, limit)},
    )
    return [int(r[0]) for r in rows]


async def _names(session: AsyncSession, sql: str, ids: Collection[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    stmt = _in(sql, "ids")
    for part in chunks(sorted(ids)):
        for key, name in (await session.execute(stmt, {"ids": list(part)})).all():
            out[int(key)] = str(name)
    return out


async def _catalog_names(session: AsyncSession, ids: Collection[int]) -> dict[int, str]:
    return await _names(session, "SELECT id, name FROM general_catalog WHERE id IN :ids", ids)


async def _user_logins(session: AsyncSession, ids: Collection[int]) -> dict[int, str]:
    return await _names(session, "SELECT id, login FROM users WHERE id IN :ids", ids)


async def service_names(session: AsyncSession, ids: Collection[int]) -> dict[int, str]:
    """Znuny service names (``service.name`` is the full ``Parent::Child`` name)."""
    return await _names(session, "SELECT id, name FROM service WHERE id IN :ids", ids)


async def _links(session: AsyncSession, obj: str, keys: Sequence[int]) -> list[tuple[Any, ...]]:
    """``(own key, other object, other key, link type, direction)`` of every valid relation."""
    out: list[tuple[Any, ...]] = []
    stmt = _in(_LINKS_SQL, "keys")
    for part in chunks(sorted(set(keys))):
        params = {"obj": obj, "keys": [str(k) for k in part]}
        for s_obj, s_key, t_obj, t_key, link_type in (await session.execute(stmt, params)).all():
            if str(s_obj) == obj:
                out.append((int(s_key), str(t_obj), str(t_key), str(link_type), "source"))
            if str(t_obj) == obj:
                out.append((int(t_key), str(s_obj), str(s_key), str(link_type), "target"))
    return out


async def _dynamic_fields(
    session: AsyncSession, object_type: str, ids: Sequence[int]
) -> dict[int, dict[str, tuple[str, ...]]]:
    """Valid dynamic fields of ``object_type`` per object id (``DynamicFieldListGet`` default)."""
    found: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    stmt = _in(
        "SELECT v.object_id, f.name, v.value_text, v.value_date, v.value_int"
        " FROM dynamic_field_value v JOIN dynamic_field f ON f.id = v.field_id"
        " WHERE f.object_type = :ot AND f.valid_id = 1 AND v.object_id IN :ids"
        " ORDER BY v.object_id, f.field_order, f.name, v.id",
        "ids",
    )
    for part in chunks(sorted(set(ids))):
        rows = await session.execute(stmt, {"ot": object_type, "ids": list(part)})
        for object_id, name, v_text, v_date, v_int in rows.all():
            value = dynamic_field_text(_str(v_text), v_date, _int(v_int))
            if value is not None:
                found[int(object_id)][str(name)].append(value)
    return {oid: {n: tuple(v) for n, v in fields.items()} for oid, fields in found.items()}


async def read_changes(session: AsyncSession, ids: Sequence[int]) -> list[LegacyChange]:
    """The changes among ``ids`` (at most :data:`MAX_READ`), in the order of ``ids``; missing
    ids are left out. Catalog values and users (also those the history refers to) are resolved
    by IN lists."""
    wanted = list(dict.fromkeys(int(i) for i in ids))
    if len(wanted) > MAX_READ:
        raise ValueError(f"read_changes reads at most {MAX_READ} changes per call")
    if not wanted:
        return []
    stmt = _in(f"{_CHANGE_COLUMNS} WHERE id IN :ids", "ids")
    changes = {
        int(r["id"]): r for r in (await session.execute(stmt, {"ids": wanted})).mappings().all()
    }
    if not changes:
        return []
    change_ids = sorted(changes)

    orders: dict[int, list[Any]] = defaultdict(list)
    wo_number: dict[int, int] = {}
    wo_change: dict[int, int] = {}
    stmt = _in(f"{_WORKORDER_COLUMNS} WHERE change_id IN :ids ORDER BY workorder_number, id", "ids")
    for wo in (await session.execute(stmt, {"ids": change_ids})).mappings().all():
        orders[int(wo["change_id"])].append(wo)
        wo_number[int(wo["id"])] = int(wo["workorder_number"])
        wo_change[int(wo["id"])] = int(wo["change_id"])

    cab: dict[int, list[Any]] = defaultdict(list)
    stmt = _in(
        "SELECT change_id, user_id, customer_user_id FROM change_cab WHERE change_id IN :ids"
        " ORDER BY change_id, id",
        "ids",
    )
    for member in (await session.execute(stmt, {"ids": change_ids})).mappings().all():
        cab[int(member["change_id"])].append(member)

    history: dict[int, list[Any]] = defaultdict(list)
    stmt = _in(
        "SELECT h.change_id, h.workorder_id, t.name AS history_type, h.fieldname,"
        " h.content_old, h.content_new, h.create_time, h.create_by FROM change_history h"
        " LEFT JOIN change_history_type t ON t.id = h.type_id"
        " WHERE h.change_id IN :ids ORDER BY h.create_time, h.id",
        "ids",
    )
    for entry in (await session.execute(stmt, {"ids": change_ids})).mappings().all():
        history[int(entry["change_id"])].append(entry)

    links: dict[int, list[LegacyLink]] = defaultdict(list)
    for own, obj, key, link_type, direction in await _links(session, CHANGE_OBJECT, change_ids):
        links[own].append(LegacyLink(obj, key, link_type, direction, 0))
    for own, obj, key, link_type, direction in await _links(
        session, WORKORDER_OBJECT, sorted(wo_number)
    ):
        if own in wo_change:
            links[wo_change[own]].append(LegacyLink(obj, key, link_type, direction, wo_number[own]))

    change_dfs = await _dynamic_fields(session, CHANGE_OBJECT, change_ids)
    wo_dfs = await _dynamic_fields(session, WORKORDER_OBJECT, sorted(wo_number))

    catalog_ids: set[int] = set()
    user_ids: set[int] = set()
    for c in changes.values():
        catalog_ids.update(
            int(c[k]) for k in ("change_state_id", "category_id", "impact_id", "priority_id")
        )
        user_ids.update(
            int(c[k]) for k in ("change_manager_id", "change_builder_id") if c[k] is not None
        )
    for wos in orders.values():
        for w in wos:
            catalog_ids.update((int(w["workorder_state_id"]), int(w["workorder_type_id"])))
            if w["workorder_agent_id"] is not None:
                user_ids.add(int(w["workorder_agent_id"]))
    for members in cab.values():
        user_ids.update(int(m["user_id"]) for m in members if m["user_id"] is not None)
    for entries in history.values():
        for h in entries:
            if h["create_by"] is not None:
                user_ids.add(int(h["create_by"]))
            for raw in (h["content_old"], h["content_new"]):
                more_catalog, more_users = history_ids(_str(h["fieldname"]), _str(raw))
                catalog_ids |= more_catalog
                user_ids |= more_users
    catalog = await _catalog_names(session, catalog_ids)
    logins = await _user_logins(session, user_ids)

    def login(user_id: object) -> str | None:
        return None if user_id is None else logins.get(int(str(user_id)))

    def work_order(w: Any) -> LegacyWorkOrder:
        return LegacyWorkOrder(
            number=int(w["workorder_number"]),
            title=_str(w["title"]),
            work_order_type=catalog.get(int(w["workorder_type_id"])),
            state=catalog.get(int(w["workorder_state_id"])),
            agent_login=login(w["workorder_agent_id"]),
            planned_start=znuny_time(w["planned_start_time"]),
            planned_end=znuny_time(w["planned_end_time"]),
            actual_start=znuny_time(w["actual_start_time"]),
            actual_end=znuny_time(w["actual_end_time"]),
            planned_effort=float(w["planned_effort"] or 0),
            accounted_time=float(w["accounted_time"] or 0),
            instruction=plain_text(_str(w["instruction_plain"]), _str(w["instruction"])),
            report=plain_text(_str(w["report_plain"]), _str(w["report"])),
            dynamic_fields=wo_dfs.get(int(w["id"]), {}),
        )

    def history_entry(h: Any) -> LegacyHistoryEntry:
        field = _str(h["fieldname"]) or None
        old, new = _str(h["content_old"]), _str(h["content_new"])
        workorder_id = h["workorder_id"]
        return LegacyHistoryEntry(
            created_at=_dt(h["create_time"]),
            user_login=login(h["create_by"]),
            history_type=str(h["history_type"] or ""),
            field=field,
            old=old,
            new=new,
            workorder_number=0 if workorder_id is None else wo_number.get(int(workorder_id), 0),
            old_display=history_display(field, old, catalog, logins),
            new_display=history_display(field, new, catalog, logins),
        )

    def cab_members(members: list[Any]) -> tuple[tuple[CabKind, str], ...]:
        """Agents by login in user id order, then customer users by login (like ChangeGet)."""
        out: list[tuple[CabKind, str]] = []
        for user_id in sorted({int(m["user_id"]) for m in members if m["user_id"] is not None}):
            if (name := login(user_id)) is not None:
                out.append(("user", name))
        customers = {
            str(m["customer_user_id"])
            for m in members
            if m["user_id"] is None and m["customer_user_id"] is not None
        }
        out.extend(("customer_user", c) for c in sorted(customers))
        return tuple(out)

    out: list[LegacyChange] = []
    for change_id in wanted:
        row = changes.get(change_id)
        if row is None:
            continue
        own_orders = orders.get(change_id, [])
        planned_start, planned_end, actual_start, actual_end = change_window(
            [
                (
                    w["planned_start_time"],
                    w["planned_end_time"],
                    w["actual_start_time"],
                    w["actual_end_time"],
                )
                for w in own_orders
            ]
        )
        out.append(
            LegacyChange(
                znuny_change_id=change_id,
                number=str(row["change_number"]),
                title=_str(row["title"]),
                state=catalog.get(int(row["change_state_id"])),
                category=catalog.get(int(row["category_id"])),
                impact=catalog.get(int(row["impact_id"])),
                priority=catalog.get(int(row["priority_id"])),
                manager_login=login(row["change_manager_id"]),
                builder_login=login(row["change_builder_id"]),
                requested_time=znuny_time(row["requested_time"]),
                planned_start=planned_start,
                planned_end=planned_end,
                actual_start=actual_start,
                actual_end=actual_end,
                created_at=_dt(row["create_time"]),
                changed_at=_dt(row["change_time"]),
                description=plain_text(_str(row["description_plain"]), _str(row["description"])),
                justification=plain_text(
                    _str(row["justification_plain"]), _str(row["justification"])
                ),
                cab=cab_members(cab.get(change_id, [])),
                workorders=tuple(work_order(w) for w in own_orders),
                history=tuple(history_entry(h) for h in history.get(change_id, [])),
                dynamic_fields=change_dfs.get(change_id, {}),
                links=tuple(
                    sorted(
                        links.get(change_id, []),
                        key=lambda li: (li.workorder_number, li.object, li.key, li.link_type),
                    )
                ),
            )
        )
    return out


async def _linked_change_ids(session: AsyncSession, obj: str, key: int) -> set[int]:
    """Changes linked to ``obj:key`` directly or through one of their work orders."""
    stmt = text(
        "SELECT so.name, lr.source_key, tob.name, lr.target_key FROM link_relation lr"
        " JOIN link_object so ON so.id = lr.source_object_id"
        " JOIN link_object tob ON tob.id = lr.target_object_id"
        " JOIN link_state ls ON ls.id = lr.state_id"
        " WHERE ls.name = 'Valid' AND ((so.name = :obj AND lr.source_key = :key"
        " AND tob.name IN (:change, :wo)) OR (tob.name = :obj AND lr.target_key = :key"
        " AND so.name IN (:change, :wo)))"
    )
    params = {"obj": obj, "key": str(key), "change": CHANGE_OBJECT, "wo": WORKORDER_OBJECT}
    change_ids: set[int] = set()
    wo_ids: set[int] = set()
    for s_obj, s_key, t_obj, t_key in (await session.execute(stmt, params)).all():
        own_obj, own_key = (t_obj, t_key) if str(s_obj) == obj else (s_obj, s_key)
        if not str(own_key).isdigit():
            continue
        (change_ids if str(own_obj) == CHANGE_OBJECT else wo_ids).add(int(own_key))
    stmt = _in("SELECT DISTINCT change_id FROM change_workorder WHERE id IN :ids", "ids")
    for part in chunks(sorted(wo_ids)):
        change_ids.update(int(r[0]) for r in await session.execute(stmt, {"ids": list(part)}))
    return change_ids


async def _summaries(session: AsyncSession, ids: Sequence[int]) -> list[LegacyChangeSummary]:
    if not ids:
        return []
    stmt = _in(
        "SELECT id, change_number, title, change_state_id, change_time"
        " FROM change_item WHERE id IN :ids",
        "ids",
    )
    rows = {
        int(r["id"]): r for r in (await session.execute(stmt, {"ids": list(ids)})).mappings().all()
    }
    times: dict[int, list[tuple[Any, Any, Any, Any]]] = defaultdict(list)
    stmt = _in(
        "SELECT change_id, planned_start_time, planned_end_time, actual_start_time,"
        " actual_end_time FROM change_workorder WHERE change_id IN :ids",
        "ids",
    )
    for w in (await session.execute(stmt, {"ids": list(ids)})).mappings().all():
        times[int(w["change_id"])].append(
            (
                w["planned_start_time"],
                w["planned_end_time"],
                w["actual_start_time"],
                w["actual_end_time"],
            )
        )
    states = await _catalog_names(session, {int(r["change_state_id"]) for r in rows.values()})
    out: list[LegacyChangeSummary] = []
    for change_id in ids:
        row = rows.get(change_id)
        if row is None:
            continue
        planned_start, planned_end, _, _ = change_window(times.get(change_id, []))
        out.append(
            LegacyChangeSummary(
                znuny_change_id=change_id,
                number=str(row["change_number"]),
                title=_str(row["title"]),
                state=states.get(int(row["change_state_id"])),
                planned_start=planned_start,
                planned_end=planned_end,
                workorder_count=len(times.get(change_id, [])),
                changed_at=_dt(row["change_time"]),
            )
        )
    return out


async def search(
    session: AsyncSession,
    *,
    text: str | None,
    ticket_id: int | None,
    ci_znuny_id: int | None,
    limit: int,
    offset: int,
    timeout_seconds: int = SEARCH_TIMEOUT_SECONDS,
) -> tuple[list[LegacyChangeSummary], int]:
    """Changes, newest change first, and the number of matches.

    ``text`` matches number and title (case-insensitive contains, ``%``/``_`` literal), from
    :data:`ATTRIBUTE_SEARCH_MIN_CHARS` characters on also description and justification;
    ``ticket_id`` / ``ci_znuny_id`` keep the changes linked to that ticket (directly or through a
    work order) / to that CI (through a work order). The caller checks that it may read the
    ticket or CI. A text search over ``timeout_seconds`` raises :class:`LegacySearchTooBroad`.
    """
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    needle = (text or "").strip()
    where = "1 = 1"
    params: dict[str, Any] = {}
    if needle:
        long_match = _LONG_TEXT_MATCH if len(needle) >= ATTRIBUTE_SEARCH_MIN_CHARS else ""
        where = f"({_SHORT_TEXT_MATCH}{long_match})"
        params["pat"] = like_contains(needle)

    candidates: set[int] | None = None
    if ticket_id is not None:
        candidates = await _linked_change_ids(session, TICKET_OBJECT, ticket_id)
    if ci_znuny_id is not None:
        by_ci = await _linked_change_ids(session, CI_OBJECT, ci_znuny_id)
        candidates = by_ci if candidates is None else candidates & by_ci

    try:
        if needle:
            async with statement_timeout(session, timeout_seconds):
                page, total = await _matching(session, where, params, candidates, limit, offset)
        else:
            page, total = await _matching(session, where, params, candidates, limit, offset)
    except ReportQueryTimeout as exc:
        raise LegacySearchTooBroad("legacy change search exceeded its time limit") from exc
    if needle and session.get_bind().dialect.name == "postgresql":
        # SET LOCAL lasts until the transaction ends (MariaDB's variant is reset by the helper)
        await session.execute(_in("RESET statement_timeout"))
    return await _summaries(session, page), total


async def _matching(
    session: AsyncSession,
    where: str,
    params: dict[str, Any],
    candidates: set[int] | None,
    limit: int,
    offset: int,
) -> tuple[list[int], int]:
    """The ids of one page and the number of matches (``where`` is built from constants)."""
    if candidates is None:
        count = _in(f"SELECT COUNT(*) FROM change_item WHERE {where}")
        total = int((await session.execute(count, params)).scalar_one())
        rows = await session.execute(
            _in(
                f"SELECT id FROM change_item WHERE {where}"
                " ORDER BY change_time DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
        return [int(r[0]) for r in rows], total
    found: list[tuple[datetime, int]] = []
    stmt = _in(f"SELECT id, change_time FROM change_item WHERE id IN :ids AND {where}", "ids")
    for part in chunks(sorted(candidates)):
        rows = await session.execute(stmt, {**params, "ids": list(part)})
        for change_id, changed in rows.all():
            found.append((_dt(changed), int(change_id)))
    found.sort(reverse=True)
    return [change_id for _, change_id in found[offset : offset + limit]], len(found)
