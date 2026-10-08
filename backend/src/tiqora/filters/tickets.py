"""Permission-scoped ticket filter compiler.

A :class:`~tiqora.filters.model.Filter` is compiled into a ``select(Ticket)``.
The agent's scope (readable queues, archive rule) is ANDed *outside* the
user's expression, so no ``any`` / ``not`` branch can widen it.

Every condition is two-valued (never SQL ``NULL``), so ``not`` over a nullable
column behaves like "not that value" and includes the NULL rows.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, exists, false, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import TiqoraAiTicketState
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketLockType,
    TicketState,
    TicketStateType,
    TicketWatcher,
)
from tiqora.domain.ticket_service import (
    LIST_CHANNEL_KEYS,
    LOCKED_LOCK_TYPE_NAMES,
    UNASSIGNED_OWNER_ID,
    UNLOCK_TYPE_NAME,
    _channel_condition,
    _escalated_condition,
    _escalation_due_before,
)
from tiqora.filters.catalog import (
    FilterError,
    FilterFieldSpec,
    parse_datetime_value,
    relative_delta,
    validate_filter,
)
from tiqora.filters.model import AllOf, AnyOf, Condition, Filter, FilterNode, Not
from tiqora.permissions.engine import PermissionEngine

DF_FIELD_PREFIX = "df."

_LABEL_KEY = "ticketFilter.fields."


def _spec(key: str, type_: Any, **kw: Any) -> FilterFieldSpec:
    return FilterFieldSpec(key=key, type=type_, label_key=f"{_LABEL_KEY}{key}", **kw)


#: Static ticket fields (see Design D4). Dynamic fields are added per database.
TICKET_FILTER_FIELDS: dict[str, FilterFieldSpec] = {
    s.key: s
    for s in (
        _spec("ticket_id", "id"),
        _spec("tn", "string"),
        _spec("title", "string"),
        _spec("queue_id", "id", options_source="queue"),
        _spec("state_id", "id", options_source="state"),
        _spec(
            "state_type",
            "string",
            ops=frozenset({"eq", "neq", "in", "not_in"}),
        ),
        _spec("priority_id", "id", options_source="priority"),
        _spec("type_id", "id", options_source="type"),
        _spec("service_id", "id", options_source="service"),
        _spec("sla_id", "id", options_source="sla"),
        _spec("owner_id", "id"),
        _spec("responsible_id", "id"),
        _spec("customer_id", "string"),
        _spec("customer_user_id", "string"),
        _spec("locked", "bool"),
        _spec("watched_by", "id", ops=frozenset({"eq", "in"})),
        _spec("unassigned", "bool"),
        _spec("escalated", "bool"),
        _spec("ai_escalated", "bool"),
        _spec("escalation_due_within", "int", ops=frozenset({"lte"})),
        _spec(
            "channel",
            "enum",
            ops=frozenset({"eq", "in"}),
            enum_values=LIST_CHANNEL_KEYS,
        ),
        _spec("archived", "bool"),
        _spec("created", "datetime"),
        _spec("changed", "datetime"),
        _spec("pending_until", "datetime"),
    )
}

#: Ticket columns behind the plain column fields.
_COLUMNS: dict[str, Any] = {
    "ticket_id": Ticket.id,
    "tn": Ticket.tn,
    "title": Ticket.title,
    "queue_id": Ticket.queue_id,
    "state_id": Ticket.ticket_state_id,
    "priority_id": Ticket.ticket_priority_id,
    "type_id": Ticket.type_id,
    "service_id": Ticket.service_id,
    "sla_id": Ticket.sla_id,
    "owner_id": Ticket.user_id,
    "responsible_id": Ticket.responsible_user_id,
    "customer_id": Ticket.customer_id,
    "customer_user_id": Ticket.customer_user_id,
    "created": Ticket.create_time,
    "changed": Ticket.change_time,
    "pending_until": Ticket.until_time,  # epoch seconds, 0 = none
}

#: Znuny dynamic field type -> (filter type, value column on dynamic_field_value).
_DF_TYPES: dict[str, tuple[str, Any]] = {
    "Text": ("string", DynamicFieldValue.value_text),
    "TextArea": ("string", DynamicFieldValue.value_text),
    "Dropdown": ("string", DynamicFieldValue.value_text),
    "Multiselect": ("string", DynamicFieldValue.value_text),
    "Checkbox": ("bool", DynamicFieldValue.value_int),
    "Date": ("datetime", DynamicFieldValue.value_date),
    "DateTime": ("datetime", DynamicFieldValue.value_date),
}
_DF_OPTION_TYPES = frozenset({"Dropdown", "Multiselect"})


@dataclass(frozen=True, slots=True)
class TicketQueryScope:
    user_id: int
    queue_ids: frozenset[int]  # valid queues the user holds `permission` on
    permission: str = "ro"
    include_archived: bool = False


async def ticket_query_scope(
    session: AsyncSession,
    user_id: int,
    *,
    permission: str = "ro",
    include_archived: bool = False,
) -> TicketQueryScope:
    groups = await PermissionEngine(session).groups_for_permission(user_id, permission)
    queue_ids: set[int] = set()
    if groups:
        rows = await session.execute(
            select(Queue.id).where(Queue.group_id.in_(groups), Queue.valid_id == 1)
        )
        queue_ids = set(rows.scalars().all())
    return TicketQueryScope(
        user_id=user_id,
        queue_ids=frozenset(queue_ids),
        permission=permission,
        include_archived=include_archived,
    )


# --------------------------------------------------------------------------
# Field catalog
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _DynamicFieldInfo:
    id: int
    name: str
    label: str
    field_type: str

    @property
    def filter_type(self) -> str:
        return _DF_TYPES[self.field_type][0]

    @property
    def value_column(self) -> Any:
        return _DF_TYPES[self.field_type][1]


async def _load_dynamic_fields(session: AsyncSession) -> dict[str, _DynamicFieldInfo]:
    rows = await session.execute(
        select(DynamicField.id, DynamicField.name, DynamicField.label, DynamicField.field_type)
        .where(
            DynamicField.object_type == "Ticket",
            DynamicField.valid_id == 1,
            DynamicField.field_type.in_(list(_DF_TYPES)),
        )
        .order_by(DynamicField.field_order, DynamicField.id)
    )
    return {name: _DynamicFieldInfo(fid, name, label, ft) for fid, name, label, ft in rows.all()}


def _df_spec(info: _DynamicFieldInfo) -> FilterFieldSpec:
    return FilterFieldSpec(
        key=f"{DF_FIELD_PREFIX}{info.name}",
        type=info.filter_type,  # type: ignore[arg-type]
        label=info.label,
        options_source=(
            f"DynamicField_{info.name}" if info.field_type in _DF_OPTION_TYPES else None
        ),
    )


async def ticket_filter_fields(session: AsyncSession) -> list[FilterFieldSpec]:
    """Static fields followed by one field per valid ticket dynamic field."""
    dfs = await _load_dynamic_fields(session)
    return [*TICKET_FILTER_FIELDS.values(), *(_df_spec(i) for i in dfs.values())]


# --------------------------------------------------------------------------
# Compilation
# --------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _epoch(dt: datetime) -> int:
    return calendar.timegm(dt.timetuple())


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_nullable(col: Any) -> bool:
    return bool(getattr(col.property.columns[0], "nullable", True))


def _datetime_bound(value: Any, path: str) -> datetime:
    return parse_datetime_value(value, path=path)


def _datetime_predicate(
    col: Any, cond: Condition, path: str, now: datetime, *, epoch: bool
) -> ColumnElement[bool]:
    """Positive datetime comparison. ``epoch`` columns hold seconds, 0 = unset."""

    def bound(dt: datetime) -> Any:
        return _epoch(dt) if epoch else dt

    op, value = cond.op, cond.value
    if op == "before":
        pred = col < bound(_datetime_bound(value, path))
    elif op == "after":
        pred = col > bound(_datetime_bound(value, path))
    elif op == "between":
        pred = and_(
            col >= bound(_datetime_bound(value["from"], f"{path}.from")),
            col < bound(_datetime_bound(value["to"], f"{path}.to")),
        )
    elif op == "within_last":
        pred = col >= bound(now - relative_delta(value, path=path))
    elif op == "older_than":
        pred = col < bound(now - relative_delta(value, path=path))
    else:  # pragma: no cover - validate_filter limits the ops
        raise FilterError(f"„{op}“ ist für Datumsfelder nicht möglich.", path=path)
    return and_(col > 0, pred) if epoch else pred


def _positive_predicate(
    col: Any, spec_type: str, cond: Condition, path: str, now: datetime, *, epoch: bool = False
) -> ColumnElement[bool]:
    """Condition on a value column, for ops that mean "a value that ...".

    ``neq`` / ``not_in`` / ``is_empty`` are handled by the callers (they differ
    between ticket columns and EXISTS-based dynamic fields).
    """
    op, value = cond.op, cond.value
    if spec_type == "datetime":
        if op == "is_not_empty":
            return (col > 0) if epoch else col.is_not(None)  # type: ignore[no-any-return]
        return _datetime_predicate(col, cond, path, now, epoch=epoch)
    if op == "eq":
        return col == value  # type: ignore[no-any-return]
    if op == "in":
        return col.in_(value)  # type: ignore[no-any-return]
    if op == "contains":
        return col.ilike(f"%{_like(value)}%", escape="\\")  # type: ignore[no-any-return]
    if op == "starts_with":
        return col.ilike(f"{_like(value)}%", escape="\\")  # type: ignore[no-any-return]
    if op == "is_not_empty":
        return and_(col.is_not(None), col != "")
    if op == "lt":
        return col < value  # type: ignore[no-any-return]
    if op == "lte":
        return col <= value  # type: ignore[no-any-return]
    if op == "gt":
        return col > value  # type: ignore[no-any-return]
    if op == "gte":
        return col >= value  # type: ignore[no-any-return]
    if op == "between":
        return and_(col >= value["from"], col <= value["to"])
    raise FilterError(f"„{op}“ ist hier nicht möglich.", path=path)  # pragma: no cover


_NEGATED_OPS = {"neq": "eq", "not_in": "in"}


class _Compiler:
    def __init__(self, session: AsyncSession, now: datetime) -> None:
        self._session = session
        self._now = now
        self._dfs: dict[str, _DynamicFieldInfo] = {}
        self._state_ids: dict[str, set[int]] | None = None
        self._lock_ids: dict[str, int] | None = None
        self._ai_ids: set[int] | None = None

    async def load_dynamic_fields(self) -> None:
        self._dfs = await _load_dynamic_fields(self._session)

    def resolve(self, key: str) -> FilterFieldSpec | None:
        if key in TICKET_FILTER_FIELDS:
            return TICKET_FILTER_FIELDS[key]
        if key.startswith(DF_FIELD_PREFIX):
            info = self._dfs.get(key[len(DF_FIELD_PREFIX) :])
            return _df_spec(info) if info else None
        return None

    # -- lazily preloaded lookups -------------------------------------------

    async def _state_ids_by_type(self, type_name: str) -> set[int]:
        if self._state_ids is None:
            rows = await self._session.execute(
                select(TicketState.id, TicketStateType.name).join(
                    TicketStateType, TicketStateType.id == TicketState.type_id
                )
            )
            self._state_ids = {}
            for sid, name in rows.all():
                self._state_ids.setdefault(name, set()).add(sid)
        return self._state_ids.get(type_name, set())

    async def _lock_type_ids(self) -> dict[str, int]:
        if self._lock_ids is None:
            rows = await self._session.execute(select(TicketLockType.id, TicketLockType.name))
            self._lock_ids = {name: lid for lid, name in rows.all()}
        return self._lock_ids

    async def _ai_escalated_ids(self) -> set[int]:
        if self._ai_ids is None:
            rows = await self._session.execute(
                select(TiqoraAiTicketState.ticket_id).where(
                    TiqoraAiTicketState.ai_escalated_at.is_not(None)
                )
            )
            self._ai_ids = set(rows.scalars().all())
        return self._ai_ids

    # -- tree ---------------------------------------------------------------

    async def node(self, node: FilterNode, path: str) -> ColumnElement[bool]:
        if isinstance(node, Condition):
            return await self.condition(node, path)
        if isinstance(node, AllOf):
            return and_(*[await self.node(c, f"{path}.all[{i}]") for i, c in enumerate(node.all)])
        if isinstance(node, AnyOf):
            return or_(*[await self.node(c, f"{path}.any[{i}]") for i, c in enumerate(node.any)])
        assert isinstance(node, Not)
        return not_(await self.node(node.not_, f"{path}.not"))

    async def condition(self, cond: Condition, path: str) -> ColumnElement[bool]:
        key = cond.field
        vpath = f"{path}.value"
        if key.startswith(DF_FIELD_PREFIX):
            return self._dynamic_field(self._dfs[key[len(DF_FIELD_PREFIX) :]], cond, vpath)
        if key in _COLUMNS:
            return self._column(key, cond, vpath)
        return await self._special(key, cond)

    def _column(self, key: str, cond: Condition, vpath: str) -> ColumnElement[bool]:
        col = _COLUMNS[key]
        spec = TICKET_FILTER_FIELDS[key]
        epoch = key == "pending_until"
        nullable = epoch is False and _is_nullable(col)
        op = cond.op
        if op == "is_empty":
            if spec.type == "datetime":
                return col == 0 if epoch else col.is_(None)  # type: ignore[no-any-return]
            return or_(col.is_(None), col == "")
        if op in _NEGATED_OPS:
            positive = _positive_predicate(
                col, spec.type, cond.model_copy(update={"op": _NEGATED_OPS[op]}), vpath, self._now
            )
            return or_(col.is_(None), not_(positive)) if nullable else not_(positive)
        positive = _positive_predicate(col, spec.type, cond, vpath, self._now, epoch=epoch)
        return and_(col.is_not(None), positive) if nullable else positive

    def _dynamic_field(
        self, info: _DynamicFieldInfo, cond: Condition, vpath: str
    ) -> ColumnElement[bool]:
        col = info.value_column
        ftype = info.filter_type
        op = cond.op

        def has(pred: ColumnElement[bool]) -> ColumnElement[bool]:
            return exists().where(
                DynamicFieldValue.object_id == Ticket.id,
                DynamicFieldValue.field_id == info.id,
                pred,
            )

        if ftype == "bool":
            checked = has(col == 1)
            return checked if op == "is_true" else not_(checked)
        if op == "is_empty":
            return not_(has(col.is_not(None) if ftype == "datetime" else _non_empty(col)))
        if op in _NEGATED_OPS:
            positive = _positive_predicate(
                col, ftype, cond.model_copy(update={"op": _NEGATED_OPS[op]}), vpath, self._now
            )
            return not_(has(positive))
        return has(_positive_predicate(col, ftype, cond, vpath, self._now))

    async def _special(self, key: str, cond: Condition) -> ColumnElement[bool]:
        op, value = cond.op, cond.value
        negative = op in ("neq", "not_in", "is_false")
        if key == "state_type":
            names = value if isinstance(value, list) else [value]
            ids: set[int] = set()
            for name in names:
                ids |= await self._state_ids_by_type(name)
            pred: ColumnElement[bool] = Ticket.ticket_state_id.in_(ids) if ids else false()
            return not_(pred) if negative else pred
        if key == "locked":
            locks = await self._lock_type_ids()
            if op == "is_true":
                locked = {lid for name, lid in locks.items() if name in LOCKED_LOCK_TYPE_NAMES}
                return Ticket.ticket_lock_id.in_(locked) if locked else false()
            unlock = locks.get(UNLOCK_TYPE_NAME)
            return Ticket.ticket_lock_id == unlock if unlock is not None else false()
        if key == "watched_by":
            users = value if isinstance(value, list) else [value]
            rows = await self._session.execute(
                select(TicketWatcher.ticket_id).where(TicketWatcher.user_id.in_(users))
            )
            watched = set(rows.scalars().all())
            return Ticket.id.in_(watched) if watched else false()
        if key == "unassigned":
            return (
                Ticket.user_id == UNASSIGNED_OWNER_ID
                if op == "is_true"
                else Ticket.user_id != UNASSIGNED_OWNER_ID
            )
        if key == "escalated":
            pred = _escalated_condition(
                Ticket.id, await self._ai_escalated_ids(), now=_epoch(self._now)
            )
            return not_(pred) if negative else pred
        if key == "ai_escalated":
            ai_ids = await self._ai_escalated_ids()
            pred = Ticket.id.in_(ai_ids) if ai_ids else false()
            return not_(pred) if negative else pred
        if key == "escalation_due_within":
            return _escalation_due_before(_epoch(self._now) + int(value))
        if key == "channel":
            channels = value if isinstance(value, list) else [value]
            return or_(*(_channel_condition(Ticket.id, c) for c in channels))
        if key == "archived":
            return Ticket.archive_flag == (1 if op == "is_true" else 0)
        raise FilterError(f"Unbekanntes Feld „{key}“.", path="where.field")  # pragma: no cover


def _non_empty(col: Any) -> ColumnElement[bool]:
    return and_(col.is_not(None), col != "")


async def compile_ticket_filter(
    session: AsyncSession,
    flt: Filter,
    scope: TicketQueryScope,
    *,
    now: datetime | None = None,
) -> Select[tuple[Ticket]] | None:
    """Compile ``flt`` inside ``scope``; ``None`` means guaranteed empty.

    Raises :class:`FilterError` (callers answer 422) for an invalid filter,
    also when the scope is empty.
    """
    compiler = _Compiler(session, now if now is not None else _utc_now())
    await compiler.load_dynamic_fields()
    validate_filter(flt, compiler.resolve)
    if not scope.queue_ids:
        return None
    stmt = select(Ticket).where(Ticket.queue_id.in_(scope.queue_ids))
    if not scope.include_archived:
        stmt = stmt.where(Ticket.archive_flag == 0)
    if flt.where is not None:
        stmt = stmt.where(await compiler.node(flt.where, "where"))
    return stmt


async def count_tickets(
    session: AsyncSession,
    flt: Filter,
    scope: TicketQueryScope,
    *,
    now: datetime | None = None,
) -> int:
    stmt = await compile_ticket_filter(session, flt, scope, now=now)
    if stmt is None:
        return 0
    total = await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    return int(total.scalar_one())


__all__ = [
    "DF_FIELD_PREFIX",
    "TICKET_FILTER_FIELDS",
    "TicketQueryScope",
    "compile_ticket_filter",
    "count_tickets",
    "ticket_filter_fields",
    "ticket_query_scope",
]
