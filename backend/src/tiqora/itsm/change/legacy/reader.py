"""Read-only SQL on Znuny's ITSMChangeManagement tables (the only module with ``change_*`` SQL).

Every statement is ``text()`` with bound parameters; table and column names are constants of this
module, never input. Joins stay inside Znuny's own tables (``change_history`` with
``change_history_type``, ``link_relation`` with ``link_object``/``link_type``/``link_state``,
``dynamic_field_value`` with ``dynamic_field``); nothing here touches a ``tiqora_*`` table. IN
lists are chunked (:data:`~tiqora.itsm.znuny_adapter.catalog.CHUNK`). The reader never writes
and never commits. The schema is identical in both Znuny lines (survey Q9).

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
from tiqora.itsm.znuny_adapter.reader import like_contains

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
_TEXT_MATCH: Final = (
    "(lower(change_number) LIKE lower(:pat) ESCAPE '!' OR lower(title) LIKE lower(:pat) ESCAPE '!'"
    " OR lower(description_plain) LIKE lower(:pat) ESCAPE '!'"
    " OR lower(justification_plain) LIKE lower(:pat) ESCAPE '!')"
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
    assert isinstance(value, datetime)
    return value.replace(tzinfo=None, microsecond=0)


async def change_tables_present(session: AsyncSession) -> bool:
    """``change_item`` and ``change_workorder`` exist (the ITSM detection, dialect-safe and in
    the current schema only; cached like the mode detection)."""
    return (await detect_znuny_itsm(session)).change_installed


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
    ids are left out. Catalog values and users are resolved by IN lists."""
    wanted = list(dict.fromkeys(int(i) for i in ids))
    if len(wanted) > MAX_READ:
        raise ValueError(f"read_changes reads at most {MAX_READ} changes per call")
    if not wanted:
        return []
    changes: dict[int, tuple[Any, ...]] = {
        int(r[0]): tuple(r)
        for r in (
            await session.execute(
                _in(f"{_CHANGE_COLUMNS} WHERE id IN :ids", "ids"), {"ids": wanted}
            )
        ).all()
    }
    if not changes:
        return []
    change_ids = sorted(changes)

    orders: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    wo_number: dict[int, int] = {}
    wo_change: dict[int, int] = {}
    stmt = _in(f"{_WORKORDER_COLUMNS} WHERE change_id IN :ids ORDER BY workorder_number, id", "ids")
    for wo in (await session.execute(stmt, {"ids": change_ids})).all():
        orders[int(wo[1])].append(tuple(wo))
        wo_number[int(wo[0])] = int(wo[2])
        wo_change[int(wo[0])] = int(wo[1])

    cab: dict[int, list[tuple[CabKind, int | str]]] = defaultdict(list)
    stmt = _in(
        "SELECT change_id, user_id, customer_user_id FROM change_cab WHERE change_id IN :ids"
        " ORDER BY change_id, id",
        "ids",
    )
    for change_id, user_id, customer in (await session.execute(stmt, {"ids": change_ids})).all():
        if user_id is not None:
            cab[int(change_id)].append(("user", int(user_id)))
        elif customer is not None:
            cab[int(change_id)].append(("customer_user", str(customer)))

    history: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    stmt = _in(
        "SELECT h.change_id, h.workorder_id, t.name, h.fieldname, h.content_old, h.content_new,"
        " h.create_time, h.create_by FROM change_history h"
        " LEFT JOIN change_history_type t ON t.id = h.type_id"
        " WHERE h.change_id IN :ids ORDER BY h.create_time, h.id",
        "ids",
    )
    for entry in (await session.execute(stmt, {"ids": change_ids})).all():
        history[int(entry[0])].append(tuple(entry))

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
        catalog_ids.update(int(c[i]) for i in (7, 10, 11, 12))
        user_ids.update(int(c[i]) for i in (8, 9) if c[i] is not None)
    for wos in orders.values():
        for w in wos:
            catalog_ids.update((int(w[8]), int(w[9])))
            if w[10] is not None:
                user_ids.add(int(w[10]))
    for cab_rows in cab.values():
        user_ids.update(int(ref) for kind, ref in cab_rows if kind == "user")
    for entries in history.values():
        user_ids.update(int(h[7]) for h in entries if h[7] is not None)
    catalog = await _catalog_names(session, catalog_ids)
    logins = await _user_logins(session, user_ids)

    def login(user_id: object) -> str | None:
        return None if user_id is None else logins.get(int(str(user_id)))

    out: list[LegacyChange] = []
    for change_id in wanted:
        row = changes.get(change_id)
        if row is None:
            continue
        work_orders = tuple(
            LegacyWorkOrder(
                number=int(w[2]),
                title=_str(w[3]),
                work_order_type=catalog.get(int(w[9])),
                state=catalog.get(int(w[8])),
                agent_login=login(w[10]),
                planned_start=znuny_time(w[11]),
                planned_end=znuny_time(w[12]),
                actual_start=znuny_time(w[13]),
                actual_end=znuny_time(w[14]),
                planned_effort=float(w[15] or 0),
                accounted_time=float(w[16] or 0),
                instruction=plain_text(_str(w[5]), _str(w[4])),
                report=plain_text(_str(w[7]), _str(w[6])),
                dynamic_fields=wo_dfs.get(int(w[0]), {}),
            )
            for w in orders.get(change_id, [])
        )
        planned_start, planned_end, actual_start, actual_end = change_window(
            [(w[11], w[12], w[13], w[14]) for w in orders.get(change_id, [])]
        )
        own_cab = cab.get(change_id, [])
        members: list[tuple[CabKind, str]] = []
        for user_id in sorted({int(r) for k, r in own_cab if k == "user"}):
            if (name := login(user_id)) is not None:
                members.append(("user", name))
        for customer in sorted({str(r) for k, r in own_cab if k == "customer_user"}):
            members.append(("customer_user", customer))
        own_history = tuple(
            LegacyHistoryEntry(
                created_at=_dt(h[6]),
                user_login=login(h[7]),
                history_type=str(h[2] or ""),
                field=_str(h[3]) or None,
                old=_str(h[4]),
                new=_str(h[5]),
                workorder_number=0 if h[1] is None else wo_number.get(int(h[1]), 0),
            )
            for h in history.get(change_id, [])
        )
        out.append(
            LegacyChange(
                znuny_change_id=change_id,
                number=str(row[1]),
                title=_str(row[2]),
                state=catalog.get(int(row[7])),
                category=catalog.get(int(row[10])),
                impact=catalog.get(int(row[11])),
                priority=catalog.get(int(row[12])),
                manager_login=login(row[8]),
                builder_login=login(row[9]),
                requested_time=znuny_time(row[13]),
                planned_start=planned_start,
                planned_end=planned_end,
                actual_start=actual_start,
                actual_end=actual_end,
                created_at=_dt(row[14]),
                changed_at=_dt(row[15]),
                description=plain_text(_str(row[4]), _str(row[3])),
                justification=plain_text(_str(row[6]), _str(row[5])),
                cab=tuple(members),
                workorders=work_orders,
                history=own_history,
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
    rows = {
        int(r[0]): tuple(r)
        for r in (
            await session.execute(
                _in(
                    "SELECT id, change_number, title, change_state_id, change_time"
                    " FROM change_item WHERE id IN :ids",
                    "ids",
                ),
                {"ids": list(ids)},
            )
        ).all()
    }
    times: dict[int, list[tuple[Any, Any, Any, Any]]] = defaultdict(list)
    stmt = _in(
        "SELECT change_id, planned_start_time, planned_end_time, actual_start_time,"
        " actual_end_time FROM change_workorder WHERE change_id IN :ids",
        "ids",
    )
    for change_id, ps, pe, a_s, a_e in (await session.execute(stmt, {"ids": list(ids)})).all():
        times[int(change_id)].append((ps, pe, a_s, a_e))
    states = await _catalog_names(session, {int(r[3]) for r in rows.values()})
    out: list[LegacyChangeSummary] = []
    for change_id in ids:
        row = rows.get(change_id)
        if row is None:
            continue
        planned_start, planned_end, _, _ = change_window(times.get(change_id, []))
        out.append(
            LegacyChangeSummary(
                znuny_change_id=change_id,
                number=str(row[1]),
                title=_str(row[2]),
                state=states.get(int(row[3])),
                planned_start=planned_start,
                planned_end=planned_end,
                workorder_count=len(times.get(change_id, [])),
                changed_at=_dt(row[4]),
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
) -> tuple[list[LegacyChangeSummary], int]:
    """Changes, newest change first, and the number of matches.

    ``text`` matches number, title, description and justification (case-insensitive contains,
    ``%``/``_`` literal); ``ticket_id`` / ``ci_znuny_id`` keep the changes linked to that ticket
    (directly or through a work order) / to that CI (through a work order). The caller checks
    that it may read the ticket or CI.
    """
    limit = max(1, min(limit, MAX_PAGE))
    offset = max(0, offset)
    needle = (text or "").strip()
    where = _TEXT_MATCH if needle else "1 = 1"
    params: dict[str, Any] = {"pat": like_contains(needle)} if needle else {}

    candidates: set[int] | None = None
    if ticket_id is not None:
        candidates = await _linked_change_ids(session, TICKET_OBJECT, ticket_id)
    if ci_znuny_id is not None:
        by_ci = await _linked_change_ids(session, CI_OBJECT, ci_znuny_id)
        candidates = by_ci if candidates is None else candidates & by_ci

    if candidates is None:
        total = int(
            (
                await session.execute(
                    _in(f"SELECT COUNT(*) FROM change_item WHERE {where}"), params
                )
            ).scalar_one()
        )
        rows = await session.execute(
            _in(
                f"SELECT id FROM change_item WHERE {where}"
                " ORDER BY change_time DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
        return await _summaries(session, [int(r[0]) for r in rows]), total

    found: list[tuple[datetime, int]] = []
    stmt = _in(f"SELECT id, change_time FROM change_item WHERE id IN :ids AND {where}", "ids")
    for part in chunks(sorted(candidates)):
        for change_id, changed in (
            await session.execute(stmt, {**params, "ids": list(part)})
        ).all():
            found.append((_dt(changed), int(change_id)))
    found.sort(reverse=True)
    page = [change_id for _, change_id in found[offset : offset + limit]]
    return await _summaries(session, page), len(found)
