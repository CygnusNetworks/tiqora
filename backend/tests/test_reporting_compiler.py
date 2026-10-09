"""Report compiler: golden SQL (pure) and execution on both dialects.

Seed ids: 47710-47739 (names contain ``rep47``). Layout: agent 47710, groups
47711/47713, queues 47711 + 47712 (the viewer's scope) and 47713 (outside it),
tickets/facts 47720 (in scope), 47721 (outside), 47722 (in scope, archived),
47723 (created in 47713, moved to 47711), time_accounting 47730-47732.
Every row this module writes is deleted again.
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import BooleanClauseList, create_engine, insert, text
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import operators

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraReportDailySnapshot,
    TiqoraReportTicketEvent,
    TiqoraReportTicketFact,
)
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.filters import Filter
from tiqora.filters.tickets import TicketQueryScope
from tiqora.reporting.compiler import (
    HIDDEN_QUEUE_ID,
    CompiledReport,
    ReportCompileError,
    RunParams,
    compile_report,
)
from tiqora.reporting.definition import (
    AbsoluteTimeRange,
    DefinitionError,
    ReportDefinition,
    TimeWindow,
    definition_hash,
)
from tiqora.reporting.semantic import REPORT_SOURCES

AGENT, Q_IN, Q_IN2, Q_OUT = 47710, 47711, 47712, 47713
G_IN, G_OUT = 47711, 47713
T_A, T_OUT, T_ARCH, T_MOVED = 47720, 47721, 47722, 47723
LO, HI = 47710, 47739


def sql(stmt: Any, dialect: Any) -> str:
    return str(stmt.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def _registry() -> Registry:
    reg = Registry()
    register_core(reg)
    return reg


SCOPE = TicketQueryScope(user_id=AGENT, queue_ids=frozenset({Q_IN, Q_IN2}))
REG = _registry()
NOW = datetime(2024, 6, 12, 10, 0)


def comp(defn: dict[str, Any], scope: TicketQueryScope = SCOPE, **params: Any) -> CompiledReport:
    params.setdefault("time_zone", "Europe/Berlin")
    return compile_report(
        ReportDefinition.model_validate(defn),
        RunParams(**params),
        scope,
        now=NOW,
        near_miss_percent=10,
        registry=REG,
    )


# ---------------------------------------------------------------- pure: brief


def test_empty_scope_compiles_to_none() -> None:
    c = compile_report(
        ReportDefinition.model_validate({"source": "tickets", "measures": [{"key": "tickets"}]}),
        RunParams(),
        TicketQueryScope(user_id=1, queue_ids=frozenset()),
        now=NOW,
        near_miss_percent=10,
        registry=REG,
    )
    assert c.stmt is None and c.raw_stmt is None
    assert c.window is not None and [m.columns for m in c.measures] == [("m0",)]


def test_empty_scope_still_rejects_bad_parameters() -> None:
    with pytest.raises(ReportCompileError) as exc:
        comp(
            {"source": "tickets", "measures": [{"key": "tickets"}]},
            TicketQueryScope(user_id=1, queue_ids=frozenset()),
            parameters={"nope": [1]},
        )
    assert exc.value.path == "parameters.nope"


def test_scope_and_window_in_sql() -> None:
    s = sql(
        comp(
            {
                "source": "tickets",
                "dimensions": [{"key": "created", "grain": "week"}],
                "measures": [{"key": "tickets"}],
                "time_range": {"kind": "absolute", "start": "2024-06-01", "end": "2024-06-07"},
            }
        ).stmt,
        mysql.dialect(),
    )
    assert f"queue_id IN ({Q_IN}, {Q_IN2})" in s
    assert "created_at >= '2024-05-31 22:00:00'" in s and "created_at < '2024-06-07 22:00:00'" in s
    assert "WEEKDAY(" in s and "GROUP BY d0" in s


def test_parameter_cannot_widen_scope() -> None:
    c = comp(
        {
            "source": "tickets",
            "measures": [{"key": "tickets"}],
            "parameters": [{"key": "queues", "field": "queue", "op": "in"}],
        },
        parameters={"queues": [99999]},
    )
    s = sql(c.stmt, mysql.dialect())
    assert f"queue_id IN ({Q_IN}, {Q_IN2})" in s and "99999" in s  # both ANDed: never widened
    _assert_scope_first(c)


def _assert_scope_first(c: CompiledReport) -> None:
    where = c.stmt.whereclause  # type: ignore[union-attr]
    assert isinstance(where, BooleanClauseList) and where.operator is operators.and_
    first = sql(where.clauses[0], postgresql.dialect())
    assert first == f"f.queue_id IN ({Q_IN}, {Q_IN2})"


def test_inline_any_not_cannot_widen_scope() -> None:
    flt = {
        "version": 1,
        "where": {
            "any": [
                {"field": "queue", "op": "eq", "value": 99999},
                {"not": {"field": "queue", "op": "in", "value": [Q_IN]}},
            ]
        },
    }
    c = comp({"source": "tickets", "measures": [{"key": "tickets"}], "filter": flt})
    s = sql(c.stmt, postgresql.dialect())
    assert s.index(f"queue_id IN ({Q_IN}, {Q_IN2})") < s.index("99999")
    _assert_scope_first(c)  # the OR sits inside the top-level AND, next to the scope


def test_parameter_value_outside_allowed_values_is_rejected() -> None:
    with pytest.raises(ReportCompileError, match="parameters.queues") as exc:
        comp(
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "parameters": [{"key": "queues", "field": "queue", "allowed_values": [Q_IN]}],
            },
            parameters={"queues": [Q_IN2]},
        )
    assert exc.value.path == "parameters.queues"
    assert isinstance(exc.value, DefinitionError)  # API maps it to 422 like definition errors


def test_dimension_choice_and_grain_override() -> None:
    defn = {
        "source": "open_tickets",
        "dimensions": [{"key": "queue", "choices": ["owner"]}],
        "measures": [{"key": "tickets"}],
    }
    c = comp(defn, dimension_choices={0: "owner"})
    assert c.definition.dimensions[0].key == "owner"
    assert "t.user_id AS d0" in sql(c.stmt, mysql.dialect())
    with pytest.raises(ReportCompileError) as exc:
        comp(defn, dimension_choices={0: "state_type"})
    assert exc.value.path == "dimension_choices.0"
    with pytest.raises(ReportCompileError) as exc:
        comp(defn, dimension_choices={3: "owner"})
    assert exc.value.path == "dimension_choices.3"

    g = comp(
        {
            "source": "tickets",
            "dimensions": [{"key": "queue"}, {"key": "created", "grain": "day"}],
            "measures": [{"key": "tickets"}],
        },
        grain="month",
    )
    assert g.definition.dimensions[1].grain == "month"
    assert "DAYOFMONTH(" in sql(g.stmt, mysql.dialect())


def test_choice_that_duplicates_a_dimension_is_rejected() -> None:
    with pytest.raises(ReportCompileError) as exc:
        comp(
            {
                "source": "tickets",
                "dimensions": [{"key": "queue", "choices": ["owner"]}, {"key": "owner"}],
                "measures": [{"key": "tickets"}],
            },
            dimension_choices={0: "owner"},
        )
    assert exc.value.path == "dimensions[1].key"


def test_choice_to_non_date_dimension_drops_the_grain() -> None:
    c = comp(
        {
            "source": "tickets",
            "dimensions": [{"key": "created", "grain": "week", "choices": ["closed", "queue"]}],
            "measures": [{"key": "tickets"}],
        },
        dimension_choices={0: "queue"},
    )
    assert c.definition.dimensions[0].grain is None
    c2 = comp(
        {
            "source": "tickets",
            "dimensions": [{"key": "created", "grain": "week", "choices": ["closed"]}],
            "measures": [{"key": "tickets"}],
        },
        dimension_choices={0: "closed"},
    )
    assert c2.definition.dimensions[0].grain == "week"


def test_rate_and_percentile_plans() -> None:
    c = comp(
        {"source": "tickets", "measures": [{"key": "reopen_rate"}, {"key": "first_response_p50"}]}
    )
    assert [m.columns for m in c.measures] == [("m0_num", "m0_den"), ()]
    assert c.raw_stmt is not None
    s = sql(c.stmt, mysql.dialect())
    # numerator counted inside the denominator population
    assert (
        "sum(CASE WHEN (f.close_count > 0 AND f.reopen_count > 0) THEN 1 ELSE 0 END) AS m0_num" in s
    )
    assert "sum(CASE WHEN (f.close_count > 0) THEN 1 ELSE 0 END) AS m0_den" in s
    assert "first_response_working_minutes" not in s


def test_raw_stmt_keeps_rows_with_any_percentile_value() -> None:
    c = comp(
        {
            "source": "tickets",
            "dimensions": [{"key": "queue"}],
            "measures": [{"key": "first_response_p50"}, {"key": "solution_p90"}],
        }
    )
    raw = sql(c.raw_stmt, mysql.dialect())
    assert "f.first_response_working_minutes AS v_first_response_p50" in raw
    assert "f.solution_working_minutes AS v_solution_p90" in raw
    assert (
        "(f.first_response_working_minutes IS NOT NULL OR f.solution_working_minutes IS NOT NULL)"
        in raw
    )
    assert f"queue_id IN ({Q_IN}, {Q_IN2})" in raw and "GROUP BY" not in raw


def test_stock_dates_for_weekly_snapshot() -> None:
    c = comp(
        {
            "source": "daily_snapshot",
            "dimensions": [{"key": "snapshot", "grain": "week"}],
            "measures": [{"key": "open_tickets"}],
            "time_range": {"kind": "absolute", "start": "2024-06-03", "end": "2024-06-12"},
        }
    )
    assert c.stock_dates == (date(2024, 6, 9), date(2024, 6, 12))
    assert "s.snapshot_date IN ('2024-06-09', '2024-06-12')" in sql(c.stmt, mysql.dialect())


def test_stock_dates_without_dimension_daily_grain_and_last_snapshot() -> None:
    base = {
        "source": "daily_snapshot",
        "measures": [{"key": "open_tickets"}],
        "time_range": {"kind": "absolute", "start": "2024-06-03", "end": "2024-06-12"},
    }
    assert comp(base).stock_dates == (date(2024, 6, 12),)
    assert comp(base, last_snapshot_date=date(2024, 6, 10)).stock_dates == (date(2024, 6, 10),)
    assert comp(base, last_snapshot_date=date(2024, 5, 1)).stock_dates == ()
    daily = comp({**base, "dimensions": [{"key": "snapshot"}]})
    assert daily.stock_dates is None
    s = sql(daily.stmt, mysql.dialect())
    assert "s.snapshot_date >= '2024-06-03'" in s and "s.snapshot_date <= '2024-06-12'" in s
    weekly = {**base, "dimensions": [{"key": "snapshot", "grain": "week"}]}
    clipped = comp(weekly, last_snapshot_date=date(2024, 6, 8))
    assert clipped.stock_dates == (date(2024, 6, 8),)  # second week has no snapshot yet
    # the totals query drops the snapshot dimension -> only the last date
    totals = compile_report(
        ReportDefinition.model_validate(weekly),
        RunParams(time_zone="Europe/Berlin"),
        SCOPE,
        now=NOW,
        near_miss_percent=10,
        registry=REG,
        group_by=[],
    )
    assert totals.stock_dates == (date(2024, 6, 12),) and totals.dims == ()


def test_open_tickets_has_no_window_without_time_range() -> None:
    c = comp({"source": "open_tickets", "measures": [{"key": "tickets"}]})
    assert c.window is None
    assert "create_time >=" not in sql(c.stmt, mysql.dialect())


def test_open_tickets_time_range_uses_created() -> None:
    c = comp(
        {
            "source": "open_tickets",
            "measures": [{"key": "tickets"}],
            "time_range": {"kind": "absolute", "start": "2024-06-01", "end": "2024-06-07"},
        }
    )
    assert c.window is not None
    assert "t.create_time >= '2024-05-31 22:00:00'" in sql(c.stmt, mysql.dialect())


def test_window_override_and_run_time_range() -> None:
    win = TimeWindow(
        start_utc=datetime(2024, 1, 1),
        end_utc=datetime(2024, 1, 2),
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 1),
        time_zone="UTC",
    )
    defn = {"source": "tickets", "measures": [{"key": "tickets"}]}
    s = sql(comp(defn, window_override=win).stmt, mysql.dialect())
    assert (
        "f.created_at >= '2024-01-01 00:00:00'" in s and "f.created_at < '2024-01-02 00:00:00'" in s
    )
    c = comp(defn, time_range=AbsoluteTimeRange(start=date(2024, 2, 1), end=date(2024, 2, 1)))
    assert c.window is not None and c.window.start_date == date(2024, 2, 1)
    assert c.definition.time_range == AbsoluteTimeRange(
        start=date(2024, 2, 1), end=date(2024, 2, 1)
    )
    # default: last 30 days including today (Berlin)
    d = comp(defn)
    assert d.window is not None and (d.window.start_date, d.window.end_date) == (
        date(2024, 5, 14),
        date(2024, 6, 12),
    )


def test_unknown_time_zone_is_rejected() -> None:
    with pytest.raises(ReportCompileError) as exc:
        comp({"source": "tickets", "measures": [{"key": "tickets"}]}, time_zone="Mars/Base")
    assert exc.value.path == "time_zone"


# ---------------------------------------------------------------- pure: parameters


_PARAM_DEFN: dict[str, Any] = {
    "source": "tickets",
    "measures": [{"key": "tickets"}],
    "parameters": [
        {"key": "queues", "field": "queue", "op": "in"},
        {"key": "kind", "field": "state_type", "op": "eq", "default": ["open"]},
        {"key": "archived", "field": "archived", "op": "is_true"},
        {"key": "prio", "field": "priority", "required": True},
    ],
}


def test_parameters_required_default_bool_and_types() -> None:
    with pytest.raises(ReportCompileError) as exc:
        comp(_PARAM_DEFN)
    assert exc.value.path == "parameters.prio"
    c = comp(_PARAM_DEFN, parameters={"prio": [3], "archived": True})
    s = sql(c.stmt, mysql.dialect())
    assert "f.priority_id IN (3)" in s
    assert "f.state_type = 'open'" in s  # default of the optional eq parameter
    assert "f.archived IS true" in s  # bool op switched on by presence
    assert "f.queue_id IN (99" not in s and s.count("f.queue_id IN") == 1  # optional, skipped
    off = sql(comp(_PARAM_DEFN, parameters={"prio": [3], "archived": False}).stmt, mysql.dialect())
    assert "f.archived IS true" not in off

    for bad, path in (
        ({"prio": 3}, "parameters.prio"),  # in needs a list
        ({"prio": [3], "kind": ["open"]}, "parameters.kind"),  # eq needs a scalar
        ({"prio": ["x"]}, "parameters.prio"),  # id field needs ints (wave-0 validation)
        ({"prio": [3], "kind": "nonsense"}, "parameters.kind"),  # enum value
        ({"prio": list(range(1001))}, "parameters.prio"),
        ({"prio": [3], "extra": 1}, "parameters.extra"),
    ):
        with pytest.raises(ReportCompileError) as exc:
            comp(_PARAM_DEFN, parameters=bad)
        assert exc.value.path == path, bad


def test_effective_definition_records_parameter_values() -> None:
    a = comp(_PARAM_DEFN, parameters={"prio": [3]})
    b = comp(_PARAM_DEFN, parameters={"prio": [4]})
    assert definition_hash(a.definition) != definition_hash(b.definition)
    params = {p.key: p for p in a.definition.parameters}
    assert params["prio"].default == [3] and params["prio"].required is False
    assert params["kind"].default == ["open"] and params["queues"].default is None
    assert params["archived"].default is None
    on = comp(_PARAM_DEFN, parameters={"prio": [3], "archived": True})
    assert {p.key: p for p in on.definition.parameters}["archived"].default == []
    assert definition_hash(on.definition) != definition_hash(a.definition)
    # the effective definition re-compiles (no run values) to the same SQL
    for c in (a, on):
        again = compile_report(
            c.definition,
            RunParams(time_zone="Europe/Berlin"),
            SCOPE,
            now=NOW,
            near_miss_percent=10,
            registry=REG,
        )
        assert sql(again.stmt, mysql.dialect()) == sql(c.stmt, mysql.dialect())


# ---------------------------------------------------------------- pure: archive + hidden queues


def test_archived_tickets_follow_the_ticket_list_rule() -> None:
    defn = {"source": "tickets", "measures": [{"key": "tickets"}]}
    default = comp(defn)
    assert "f.archived IS false" in sql(default.stmt, mysql.dialect())
    assert default.include_archived is False
    admin = comp(
        defn, TicketQueryScope(user_id=AGENT, queue_ids=SCOPE.queue_ids, include_archived=True)
    )
    assert "archived" not in sql(admin.stmt, mysql.dialect()) and admin.include_archived
    ev = comp({"source": "ticket_events", "measures": [{"key": "events"}]})
    assert "f.archived IS false" in sql(ev.stmt, postgresql.dialect())
    # a user filter on the flag cannot bring archived tickets back: both are ANDed
    flt = {"version": 1, "where": {"field": "archived", "op": "is_true"}}
    s = sql(comp({**defn, "filter": flt}).stmt, mysql.dialect())
    assert "f.archived IS false" in s and "f.archived IS true" in s
    # accounted_time keeps archived tickets (R-ACCT); open_tickets drops them always
    assert "archive" not in sql(
        comp({"source": "accounted_time", "measures": [{"key": "bookings"}]}).stmt, mysql.dialect()
    )
    open_admin = comp(
        {"source": "open_tickets", "measures": [{"key": "tickets"}]},
        TicketQueryScope(user_id=AGENT, queue_ids=SCOPE.queue_ids, include_archived=True),
    )
    assert "t.archive_flag = 0" in sql(open_admin.stmt, mysql.dialect())
    assert open_admin.include_archived is False


_HIDDEN = (
    f"CASE WHEN (%s IN ({Q_IN}, {Q_IN2})) THEN %s"
    f" WHEN (%s IS NOT NULL) THEN {HIDDEN_QUEUE_ID} ELSE NULL END"
)


@pytest.mark.parametrize(
    "source,key,column,hidden",
    [
        ("tickets", "queue", "f.queue_id", False),
        ("tickets", "queue_at_create", "f.queue_id_at_create", True),
        ("ticket_events", "queue", "e.queue_id", True),
        ("ticket_events", "ticket_queue", "f.queue_id", False),
        ("daily_snapshot", "queue", "s.queue_id", False),
        ("accounted_time", "queue", "t.queue_id", False),
        ("open_tickets", "queue", "t.queue_id", False),
    ],
)
def test_queue_dimensions_outside_scope_are_hidden(
    source: str, key: str, column: str, hidden: bool
) -> None:
    measure = REG.get(REPORT_SOURCES, source).measures[0].key  # type: ignore[union-attr]
    c = comp({"source": source, "dimensions": [{"key": key}], "measures": [{"key": measure}]})
    s = sql(c.stmt, mysql.dialect())
    expected = (_HIDDEN % (column, column, column)) if hidden else column
    assert f"{expected} AS d0" in s


def test_every_queue_labelled_dimension_is_classified() -> None:
    """New queue-valued dimensions are hidden automatically unless they ARE the scope column."""
    hidden = set()
    for src in REG.items(REPORT_SOURCES):
        measure = src.measures[0].key
        for dim in src.dimensions:
            if dim.labels != "queue":
                continue
            c = comp(
                {
                    "source": src.key,
                    "dimensions": [{"key": dim.key}],
                    "measures": [{"key": measure}],
                }
            )
            if f"THEN {HIDDEN_QUEUE_ID} ELSE NULL" in sql(c.stmt, mysql.dialect()):
                hidden.add((src.key, dim.key))
    assert hidden == {
        ("tickets", "queue_at_create"),
        ("ticket_events", "queue"),
        ("ticket_events", "from_queue"),
        ("ticket_events", "to_queue"),
    }


def test_filter_on_hidden_queue_dimension_compares_the_masked_value() -> None:
    flt = {"version": 1, "where": {"field": "from_queue", "op": "eq", "value": Q_OUT}}
    s = sql(
        comp({"source": "ticket_events", "measures": [{"key": "events"}], "filter": flt}).stmt,
        mysql.dialect(),
    )
    assert f"THEN {HIDDEN_QUEUE_ID} ELSE NULL END = {Q_OUT}" in s


def test_extra_filter_errors_and_order() -> None:
    bad = Filter.model_validate({"version": 1, "where": {"field": "nope", "op": "eq", "value": 1}})
    with pytest.raises(ReportCompileError) as exc:
        comp({"source": "tickets", "measures": [{"key": "tickets"}]}, extra_filter=bad)
    assert exc.value.path == "extra_filter.where.field"
    good = Filter.model_validate(
        {"version": 1, "where": {"field": "priority", "op": "eq", "value": 3}}
    )
    c = comp({"source": "tickets", "measures": [{"key": "tickets"}]}, extra_filter=good)
    assert "f.priority_id = 3" in sql(c.stmt, mysql.dialect())
    _assert_scope_first(c)


def test_group_by_keeps_original_dimension_indices() -> None:
    defn = ReportDefinition.model_validate(
        {
            "source": "tickets",
            "dimensions": [{"key": "queue"}, {"key": "state_type"}],
            "measures": [{"key": "tickets"}],
        }
    )
    c = compile_report(
        defn, RunParams(), SCOPE, now=NOW, near_miss_percent=10, registry=REG, group_by=[1]
    )
    assert c.dims == ("d1",)
    s = sql(c.stmt, mysql.dialect())
    assert "f.state_type AS d1" in s and "GROUP BY d1" in s and "AS d0" not in s
    t = compile_report(
        defn, RunParams(), SCOPE, now=NOW, near_miss_percent=10, registry=REG, group_by=[]
    )
    assert t.dims == () and "GROUP BY" not in sql(t.stmt, mysql.dialect())


# ---------------------------------------------------------------- DB seed

_URLS: set[str] = set()
_BAND = {"lo": LO, "hi": HI}
_CLEANUP = (
    "DELETE FROM tiqora_report_daily_snapshot WHERE queue_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM time_accounting WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
#: Berlin: 47720 created 03-31 00:30 CET, 47723 created 04-01 00:30 CEST (DST on 03-31)
T_A_CREATED = datetime(2024, 3, 30, 23, 30)
T_MOVED_CREATED = datetime(2024, 3, 31, 22, 30)
WINDOW = {"kind": "absolute", "start": "2024-03-25", "end": "2024-04-05"}


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt), _BAND)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _fact(tid: int, queue: int, created: datetime, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticket_id": tid,
        "tn": f"rep47{tid}",
        "created_at": created,
        "created_by": AGENT,
        "queue_id": queue,
        "queue_id_at_create": queue,
        "state_id": 1,
        "state_type": "new",
        "priority_id": 3,
        "owner_id": AGENT,
        "customer_id": "rep47-cust",
        "customer_user": "rep47.cust@example.org",
        "channel": "email",
        "last_event_at": created,
        "fact_version": 1,
        "built_at": created,
    }
    row.update(kw)
    return row


def _event(tid: int, kind: str, at: datetime, queue: int, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticket_id": tid,
        "kind": kind,
        "occurred_at": at,
        "queue_id": queue,
        "actor_user_id": AGENT,
        "from_id": None,
        "to_id": None,
        "from_value": None,
        "to_value": None,
        "from_duration_seconds": None,
    }
    row.update(kw)
    return row


def _seed(sync_url: str) -> None:
    _URLS.add(sync_url)
    _clean(sync_url)
    t0 = datetime(2024, 3, 1)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        st_new = conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47.comp', 'x', 'Cora', 'Comp', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": t0},
        )
        for gid in (G_IN, G_OUT):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep47-group-{gid}", "t": t0},
            )
        for qid, gid in ((Q_IN, G_IN), (Q_IN2, G_IN), (Q_OUT, G_OUT)):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": f"rep47-queue-{qid}", "g": gid, "t": t0},
            )
        for tid, queue, archived, created in (
            (T_A, Q_IN, 0, T_A_CREATED),
            (T_OUT, Q_OUT, 0, T_A_CREATED),
            (T_ARCH, Q_IN2, 1, T_A_CREATED),
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'rep47 comp', :q, 1, 1, :u, 1, 3, :s,"
                    " 'rep47-cust', 'rep47.cust@example.org', 0, 0, 0, 0, 0, 0, :a,"
                    " :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"rep47{tid}",
                    "q": queue,
                    "u": AGENT,
                    "s": st_new,
                    "a": archived,
                    "t": created,
                },
            )
        for taid, tid, units, at in (
            (47730, T_A, "1.50", datetime(2024, 4, 2, 10, 0)),
            (47731, T_A, "2.00", datetime(2024, 4, 3, 10, 0)),
            (47732, T_OUT, "5.00", datetime(2024, 4, 3, 10, 0)),
        ):
            conn.execute(
                text(
                    "INSERT INTO time_accounting (id, ticket_id, article_id, time_unit,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, NULL, :u, :t, :by, :t, :by)"
                ),
                {"id": taid, "tid": tid, "u": Decimal(units), "t": at, "by": AGENT},
            )
        for fact in (
            _fact(
                T_A,
                Q_IN,
                T_A_CREATED,
                state_type="closed",
                closed_at=datetime(2024, 4, 2, 10, 0),
                first_response_at=datetime(2024, 3, 31, 8, 0),
                first_response_working_minutes=30,
                first_response_target_minutes=60,
                first_response_breached=False,
                solution_working_minutes=120,
                close_count=1,
                reopen_count=1,
                archived=False,
            ),
            _fact(T_OUT, Q_OUT, T_A_CREATED, first_response_working_minutes=999),
            _fact(T_ARCH, Q_IN2, T_A_CREATED, archived=True),
            _fact(
                T_MOVED,
                Q_IN,
                T_MOVED_CREATED,
                queue_id_at_create=Q_OUT,
                first_response_working_minutes=90,
            ),
        ):  # one statement per row: the rows set different optional columns
            conn.execute(insert(TiqoraReportTicketFact.__table__), fact)
        conn.execute(
            insert(TiqoraReportTicketEvent.__table__),
            [
                _event(T_A, "create", T_A_CREATED, Q_IN, to_id=Q_IN, to_value="new"),
                _event(
                    T_A,
                    "close",
                    datetime(2024, 4, 2, 10, 0),
                    Q_IN,
                    from_id=st_new,
                    to_id=st_new,
                    from_value="new",
                    to_value="closed",
                    from_duration_seconds=7200,
                ),
                _event(T_MOVED, "create", T_MOVED_CREATED, Q_OUT, to_id=Q_OUT, to_value="new"),
                _event(
                    T_MOVED,
                    "queue",
                    datetime(2024, 4, 1, 9, 0),
                    Q_IN,
                    from_id=Q_OUT,
                    to_id=Q_IN,
                    from_duration_seconds=37800,
                ),
                _event(T_OUT, "create", T_A_CREATED, Q_OUT, to_id=Q_OUT, to_value="new"),
                _event(T_ARCH, "create", T_A_CREATED, Q_IN2, to_id=Q_IN2, to_value="new"),
            ],
        )
        conn.execute(
            insert(TiqoraReportDailySnapshot.__table__),
            [
                {
                    "snapshot_date": date(2024, 3, 25 + i) if i < 7 else date(2024, 4, i - 6),
                    "queue_id": queue,
                    "state_type": "new",
                    "priority_id": 3,
                    "open_count": (25 + i if i < 7 else i - 6) * (100 if queue == Q_OUT else 1),
                    "age_0_1d": 1,
                    "age_1_3d": 0,
                    "age_3_7d": 0,
                    "age_7_30d": 0,
                    "age_30d_plus": 0,
                    "built_at": t0,
                }
                for i in range(12)  # 03-25 .. 04-05
                for queue in (Q_IN, Q_OUT)
            ],
        )
    engine.dispose()


async def _run(factory: async_sessionmaker[AsyncSession], stmt: Any) -> list[dict[str, Any]]:
    async with factory() as s:
        return [dict(r._mapping) for r in (await s.execute(stmt)).all()]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _condition(spec: Any) -> dict[str, Any]:
    if spec.type == "id":
        return {"field": spec.key, "op": "not_in", "value": [Q_OUT]}
    if spec.type == "string":
        return {"field": spec.key, "op": "contains", "value": "rep47"}
    if spec.type == "enum":
        return {"field": spec.key, "op": "in", "value": [spec.enum_values[0]]}
    if spec.type == "datetime":
        return {"field": spec.key, "op": "after", "value": "2020-01-01T00:00:00Z"}
    if spec.type == "bool":
        return {"field": spec.key, "op": "is_false"}
    raise AssertionError(spec.type)  # pragma: no cover


def _definitions(src: Any) -> Iterator[dict[str, Any]]:
    dims = _chunks([d.key for d in src.dimensions], 3)
    measures = _chunks([m.key for m in src.measures], 10)
    for i in range(max(len(dims), len(measures))):
        defn: dict[str, Any] = {
            "source": src.key,
            "dimensions": [{"key": k} for k in dims[i % len(dims)]],
            "measures": [{"key": k} for k in measures[i % len(measures)]],
        }
        if src.time_fields:
            defn["time_range"] = WINDOW
        yield defn


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_every_source_executes(url_fixture: str, request: pytest.FixtureRequest) -> None:
    """Every dimension, measure and filterable field of every source runs on both dialects.

    Europe/Berlin over a window across the 2024-03-31 DST change gives multi-segment
    ``to_local`` CASEs in SELECT and GROUP BY (R-GROUPBY); totals use ``group_by=[]``.
    """
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        for src in REG.items(REPORT_SOURCES):
            seen_rows = 0
            for defn in _definitions(src):
                c = comp(defn)
                assert len(c.definition.dimensions) <= 3
                rows = await _run(factory, c.stmt)
                seen_rows += sum(r["n"] for r in rows)
                if c.raw_stmt is not None:
                    await _run(factory, c.raw_stmt)
                totals = compile_report(
                    c.definition,
                    RunParams(time_zone="Europe/Berlin"),
                    SCOPE,
                    now=NOW,
                    near_miss_percent=10,
                    registry=REG,
                    group_by=[],
                )
                assert len(await _run(factory, totals.stmt)) == 1
            assert seen_rows > 0, src.key  # seeded rows reach every source
            keys = [d.key for d in src.dimensions] + [f.key for f in src.filter_fields]
            specs = [s for s in (src.filter_spec(k) for k in keys) if s is not None]
            assert specs, src.key
            for spec in specs:
                defn = {
                    "source": src.key,
                    "measures": [{"key": src.measures[0].key}],
                    "filter": {"version": 1, "where": _condition(spec)},
                }
                if src.time_fields:
                    defn["time_range"] = WINDOW
                await _run(factory, comp(defn).stmt)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_results_respect_scope_archive_and_hidden_queues(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    admin = TicketQueryScope(user_id=AGENT, queue_ids=SCOPE.queue_ids, include_archived=True)
    try:

        async def rows(defn: dict[str, Any], scope: TicketQueryScope = SCOPE, **kw: Any) -> Any:
            c = comp({"time_range": WINDOW, **defn}, scope, **kw)
            return {tuple(r[d] for d in c.dims): r["n"] for r in await _run(factory, c.stmt)}

        tickets = {"source": "tickets", "measures": [{"key": "tickets"}]}
        assert await rows(tickets) == {(): 2}  # T_OUT outside scope, T_ARCH archived
        assert await rows(tickets, admin) == {(): 3}

        # local day buckets across DST: Berlin 03-31 (CET) and 04-01 (CEST); UTC differs
        by_day = {**tickets, "dimensions": [{"key": "created"}]}
        assert await rows(by_day) == {(date(2024, 3, 31),): 1, (date(2024, 4, 1),): 1}
        assert await rows(by_day, time_zone="UTC") == {
            (date(2024, 3, 30),): 1,
            (date(2024, 3, 31),): 1,
        }

        # created outside the scope -> counted, but the queue id/name stays hidden
        at_create = {**tickets, "dimensions": [{"key": "queue_at_create"}]}
        assert await rows(at_create) == {(HIDDEN_QUEUE_ID,): 1, (Q_IN,): 1}

        events = {"source": "ticket_events", "measures": [{"key": "events"}]}
        from_q = await rows({**events, "dimensions": [{"key": "kind"}, {"key": "from_queue"}]})
        assert from_q == {("close", None): 1, ("create", None): 2, ("queue", HIDDEN_QUEUE_ID): 1}
        to_q = await rows({**events, "dimensions": [{"key": "to_queue"}]})
        assert to_q == {(None,): 1, (HIDDEN_QUEUE_ID,): 1, (Q_IN,): 2}
        flt = {"version": 1, "where": {"field": "from_queue", "op": "eq", "value": Q_OUT}}
        assert await rows({**events, "filter": flt}) == {(): 0}

        # rates and percentiles
        c = comp(
            {
                "source": "tickets",
                "time_range": WINDOW,
                "measures": [
                    {"key": "reopen_rate"},
                    {"key": "first_response_p50"},
                    {"key": "solution_p50"},
                ],
            }
        )
        (total,) = await _run(factory, c.stmt)
        assert (int(total["m0_num"]), int(total["m0_den"])) == (1, 1)
        raw = await _run(factory, c.raw_stmt)
        assert sorted((r["v_first_response_p50"], r["v_solution_p50"]) for r in raw) == [
            (30, 120),
            (90, None),
        ]

        # weekly stock: last day of each week inside the window; no dimension -> last day only
        snap = {"source": "daily_snapshot", "measures": [{"key": "open_tickets"}]}
        weekly = await _run(
            factory,
            comp(
                {"time_range": WINDOW, **snap, "dimensions": [{"key": "snapshot", "grain": "week"}]}
            ).stmt,
        )
        assert sorted((r["d0"], int(r["m0"])) for r in weekly) == [
            (date(2024, 3, 25), 31),
            (date(2024, 4, 1), 5),
        ]
        (last,) = await _run(factory, comp({"time_range": WINDOW, **snap}).stmt)
        assert int(last["m0"]) == 5

        acct = comp(
            {
                "source": "accounted_time",
                "time_range": WINDOW,
                "measures": [{"key": "time_units"}, {"key": "bookings"}, {"key": "tickets"}],
            }
        )
        (a,) = await _run(factory, acct.stmt)
        assert (Decimal(a["m0"]), a["m1"], a["m2"]) == (Decimal("3.50"), 2, 1)

        live = comp({"source": "open_tickets", "measures": [{"key": "tickets"}]}, admin)
        (o,) = await _run(factory, live.stmt)
        assert o["m0"] == 1  # T_OUT outside scope, T_ARCH archived (always for live tickets)
    finally:
        await engine.dispose()
