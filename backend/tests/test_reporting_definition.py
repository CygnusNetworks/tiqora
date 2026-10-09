"""Report definitions, time windows, validation and the five core sources (pure)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects import mysql, postgresql

from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.filters import Filter
from tiqora.reporting.definition import (
    AbsoluteTimeRange,
    DefinitionError,
    RelativeTimeRange,
    ReportDefinition,
    definition_hash,
    resolve_time_window,
    validate_definition,
)
from tiqora.reporting.filter_sql import compile_conditions
from tiqora.reporting.semantic import REPORT_SOURCES, CompileContext, ReportSource
from tiqora.reporting.sources import core_report_sources
from tiqora.reporting.sql import TzSegment

NOW = datetime(2024, 6, 12, 10, 0)  # Wednesday, naive UTC


def _registry() -> Registry:
    reg = Registry()
    register_core(reg)
    return reg


def _ctx() -> CompileContext:
    return CompileContext(
        now=NOW, time_zone="UTC", tz=(TzSegment(None, 0),), near_miss_percent=10, window=None
    )


@pytest.mark.parametrize(
    "tr,start,end",
    [
        (
            RelativeTimeRange(unit="day", count=30, include_current=True),
            date(2024, 5, 14),
            date(2024, 6, 12),
        ),
        (RelativeTimeRange(unit="week", count=12), date(2024, 3, 18), date(2024, 6, 9)),
        (RelativeTimeRange(unit="month", count=1), date(2024, 5, 1), date(2024, 5, 31)),
        (
            RelativeTimeRange(unit="quarter", count=1, include_current=True),
            date(2024, 4, 1),
            date(2024, 6, 30),
        ),
        (RelativeTimeRange(unit="year", count=1), date(2023, 1, 1), date(2023, 12, 31)),
        (
            AbsoluteTimeRange(start=date(2024, 3, 30), end=date(2024, 3, 31)),
            date(2024, 3, 30),
            date(2024, 3, 31),
        ),
    ],
)
def test_resolve_time_window(tr: Any, start: date, end: date) -> None:
    w = resolve_time_window(tr, "Europe/Berlin", NOW)
    assert (w.start_date, w.end_date) == (start, end)
    assert w.start_utc == datetime.combine(start, time.min, ZoneInfo("Europe/Berlin")).astimezone(
        UTC
    ).replace(tzinfo=None)
    nxt = end + timedelta(days=1)
    assert w.end_utc == datetime.combine(nxt, time.min, ZoneInfo("Europe/Berlin")).astimezone(
        UTC
    ).replace(tzinfo=None)
    assert w.time_zone == "Europe/Berlin"


def test_window_end_is_the_next_local_midnight_across_dst() -> None:
    # Berlin switches to summer time on 2024-03-31 (23 h day)
    tr = AbsoluteTimeRange(start=date(2024, 3, 30), end=date(2024, 3, 31))
    w = resolve_time_window(tr, "Europe/Berlin", NOW)
    assert w.start_utc == datetime(2024, 3, 29, 23, 0)
    assert w.end_utc == datetime(2024, 3, 31, 22, 0)


def test_window_uses_the_local_date_of_now() -> None:
    # 23:30 UTC on Tuesday is already Wednesday in Berlin
    late = datetime(2024, 6, 11, 23, 30)
    w = resolve_time_window(
        RelativeTimeRange(unit="day", count=1, include_current=True), "Europe/Berlin", late
    )
    assert (w.start_date, w.end_date) == (date(2024, 6, 12), date(2024, 6, 12))


def test_absolute_range_validation() -> None:
    with pytest.raises(ValidationError):
        AbsoluteTimeRange(start=date(2024, 2, 1), end=date(2024, 1, 1))
    with pytest.raises(ValidationError):
        AbsoluteTimeRange(start=date(2000, 1, 1), end=date(2024, 1, 1))


def test_unrepresentable_relative_range_is_a_definition_error() -> None:
    with pytest.raises(DefinitionError) as exc:
        resolve_time_window(RelativeTimeRange(unit="year", count=3660), "UTC", NOW)
    assert exc.value.path == "time_range"


def test_core_sources_registered_with_expected_keys() -> None:
    reg = _registry()
    keys = ["tickets", "ticket_events", "daily_snapshot", "accounted_time", "open_tickets"]
    assert [s.key for s in reg.items(REPORT_SOURCES)] == keys
    assert [s.key for s in core_report_sources()] == keys


@pytest.mark.parametrize(
    "bad,path",
    [
        ({"source": "nope", "measures": [{"key": "tickets"}]}, "source"),
        ({"source": "tickets", "measures": [{"key": "nope"}]}, "measures[0].key"),
        (
            {
                "source": "tickets",
                "dimensions": [{"key": "queue", "grain": "week"}],
                "measures": [{"key": "tickets"}],
            },
            "dimensions[0].grain",
        ),
        (
            {
                "source": "tickets",
                "dimensions": [{"key": "queue", "choices": ["nope"]}],
                "measures": [{"key": "tickets"}],
            },
            "dimensions[0].choices[0]",
        ),
        (
            {"source": "tickets", "measures": [{"key": "tickets"}], "time_field": "queue"},
            "time_field",
        ),
        (
            {"source": "tickets", "measures": [{"key": "tickets"}], "time_zone": "Mars/Base"},
            "time_zone",
        ),
        (
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "parameters": [{"key": "q", "field": "nope"}],
            },
            "parameters[0].field",
        ),
        (
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "visualization": {"type": "line", "series_dimension": 2},
            },
            "visualization.series_dimension",
        ),
        (
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "filter": {"version": 1, "where": {"field": "nope", "op": "eq", "value": 1}},
            },
            "filter.where",
        ),
        (
            {
                "source": "tickets",
                "dimensions": [{"key": "queue"}, {"key": "queue"}],
                "measures": [{"key": "tickets"}],
            },
            "dimensions[1].key",
        ),
        (
            {"source": "tickets", "measures": [{"key": "tickets"}, {"key": "tickets"}]},
            "measures[1].key",
        ),
        (
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "sort": [{"key": "closed_tickets"}],
            },
            "sort[0].key",
        ),
        (
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "visualization": {"chart_measures": [0, 1]},
            },
            "visualization.chart_measures[1]",
        ),
        (  # a bool field cannot be parametrised with "in"
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "parameters": [{"key": "m", "field": "merged", "op": "in"}],
            },
            "parameters[0].op",
        ),
        (  # an id field takes integers
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "parameters": [{"key": "q", "field": "queue", "allowed_values": ["x"]}],
            },
            "parameters[0].allowed_values[0]",
        ),
        (  # enum values are checked
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "parameters": [{"key": "c", "field": "channel", "default": ["fax"]}],
            },
            "parameters[0].default[0]",
        ),
        (  # not filterable: hour dimensions
            {
                "source": "tickets",
                "measures": [{"key": "tickets"}],
                "filter": {
                    "version": 1,
                    "where": {"field": "created_hour", "op": "eq", "value": 1},
                },
            },
            "filter.where.field",
        ),
    ],
)
def test_validate_definition_errors(bad: dict[str, Any], path: str) -> None:
    with pytest.raises(DefinitionError) as exc:
        validate_definition(ReportDefinition.model_validate(bad), _registry())
    assert exc.value.path.startswith(path)


def test_a_valid_definition_returns_its_source() -> None:
    defn = ReportDefinition.model_validate(
        {
            "source": "tickets",
            "dimensions": [{"key": "created", "grain": "week"}, {"key": "queue", "top_n": 5}],
            "measures": [{"key": "tickets"}, {"key": "first_response_p90"}],
            "filter": {
                "version": 1,
                "where": {"field": "state_type", "op": "in", "value": ["open", "new"]},
            },
            "parameters": [{"key": "queues", "field": "queue", "allowed_values": [1, 2]}],
            "time_field": "closed",
            "time_zone": "Europe/Berlin",
            "sort": [{"key": "tickets"}],
            "visualization": {"type": "line", "series_dimension": 1, "chart_measures": [0]},
        }
    )
    assert validate_definition(defn, _registry()).key == "tickets"


def test_unknown_keys_are_rejected_by_the_schema() -> None:
    with pytest.raises(ValidationError):
        ReportDefinition.model_validate(
            {"source": "tickets", "measures": [{"key": "tickets"}], "bogus": 1}
        )


def test_definition_hash_is_stable_and_independent_of_key_order() -> None:
    a = ReportDefinition.model_validate({"source": "tickets", "measures": [{"key": "tickets"}]})
    b = ReportDefinition.model_validate({"measures": [{"key": "tickets"}], "source": "tickets"})
    assert definition_hash(a) == definition_hash(b) and len(definition_hash(a)) == 64
    c = ReportDefinition.model_validate(
        {"source": "tickets", "measures": [{"key": "tickets"}, {"key": "closed_tickets"}]}
    )
    d = ReportDefinition.model_validate(
        {"source": "tickets", "measures": [{"key": "closed_tickets"}, {"key": "tickets"}]}
    )
    assert definition_hash(c) != definition_hash(d)  # measure order matters
    assert definition_hash(a) != definition_hash(c)


def test_definition_hash_covers_the_filter_with_its_alias() -> None:
    base = {"source": "tickets", "measures": [{"key": "tickets"}]}
    with_not = {
        **base,
        "filter": {"version": 1, "where": {"not": {"field": "queue", "op": "eq", "value": 1}}},
    }
    plain = {**base, "filter": {"version": 1, "where": {"field": "queue", "op": "eq", "value": 1}}}
    h1 = definition_hash(ReportDefinition.model_validate(with_not))
    assert h1 != definition_hash(ReportDefinition.model_validate(plain))


def test_every_source_dimension_and_measure_has_label_key_pattern() -> None:
    for src in core_report_sources():
        assert src.label_key == f"reports.sources.{src.key}"
        for d in src.dimensions:
            assert d.label_key == f"reports.dimensions.{d.key}"
        for m in src.measures:
            assert m.label_key == f"reports.measures.{m.key}"
        for f in src.filter_fields:
            assert f.label_key == f"reports.dimensions.{f.key}"
        keys = [d.key for d in src.dimensions] + [f.key for f in src.filter_fields]
        assert len(keys) == len(set(keys)), src.key
        assert len({m.key for m in src.measures}) == len(src.measures), src.key


def test_source_keys_order_and_flags() -> None:
    by_key = {s.key: s for s in core_report_sources()}
    assert [k for k, s in by_key.items() if not s.requires_facts] == [
        "accounted_time",
        "open_tickets",
    ]
    assert by_key["open_tickets"].default_time_field is None
    assert by_key["open_tickets"].time_fields == ("created",)
    assert by_key["tickets"].default_time_field == "created"
    assert by_key["tickets"].time_fields == ("created", "closed", "first_response")
    assert by_key["ticket_events"].time_fields == ("occurred",)
    assert by_key["daily_snapshot"].time_fields == ("snapshot",)
    assert by_key["accounted_time"].time_fields == ("booked",)
    snap = by_key["daily_snapshot"]
    assert all(m.stock and m.agg == "sum" for m in snap.measures)
    assert snap.dimension("snapshot") is not None and snap.dimension("snapshot").stock  # type: ignore[union-attr]


def test_update_breached_tickets_measure_exists_on_tickets() -> None:
    m = {m.key: m for m in core_report_sources()[0].measures}["update_breached_tickets"]
    assert m.agg == "count_if" and m.condition is not None
    sql = str(m.condition(_ctx()).compile(dialect=postgresql.dialect()))
    assert sql == "f.update_breach_count > %(update_breach_count_1)s"


# R-FILTERTYPE ---------------------------------------------------------------

_ATTRIBUTE_SOURCES = {"queue", "state", "priority", "type", "service", "sla"}


def test_every_id_string_enum_and_date_dimension_is_filterable() -> None:
    for src in core_report_sources():
        for d in src.dimensions:
            if d.kind in ("id", "string", "enum", "date"):
                spec = src.filter_spec(d.key)
                assert spec is not None, (src.key, d.key)
                assert spec.key == d.key and spec.label_key == d.label_key
                assert spec.type == {"date": "datetime"}.get(d.kind, d.kind)
                assert spec.options_source == d.options_source
                if d.labels in _ATTRIBUTE_SOURCES and d.kind == "id":
                    assert d.options_source == d.labels, (src.key, d.key)
            elif d.kind != "local_date":
                assert src.filter_spec(d.key) is None  # hour / weekday
        for f in src.filter_fields:
            spec = src.filter_spec(f.key)
            assert spec is not None and spec.type == "bool"
    assert core_report_sources()[0].filter_spec("nope") is None


@pytest.mark.parametrize(
    "source,fields",
    [
        ("tickets", ["queue", "state", "priority", "type", "customer"]),
        ("open_tickets", ["queue", "state", "priority", "type", "customer"]),
        (
            "ticket_events",
            ["ticket_queue", "ticket_state", "ticket_priority", "ticket_type", "customer", "kind"],
        ),
        ("accounted_time", ["queue"]),
        ("daily_snapshot", ["queue"]),
    ],
)
def test_parameter_and_wrapper_fields_are_filterable(source: str, fields: list[str]) -> None:
    src = {s.key: s for s in core_report_sources()}[source]
    for key in fields:
        assert src.filter_spec(key) is not None, (source, key)
    for key in ("queue", "ticket_queue"):
        spec = src.filter_spec(key)
        if spec is not None:
            assert spec.options_source == "queue"


# R-ENUM ---------------------------------------------------------------------


def test_enum_dimensions_use_the_enum_field_type_with_values() -> None:
    by_key = {s.key: s for s in core_report_sources()}
    tickets, events = by_key["tickets"], by_key["ticket_events"]
    for src, key in [
        (tickets, "state_type"),
        (tickets, "channel"),
        (events, "kind"),
        (events, "from_state_type"),
        (events, "to_state_type"),
        (events, "channel"),
        (by_key["open_tickets"], "state_type"),
        (by_key["daily_snapshot"], "state_type"),
    ]:
        spec = src.filter_spec(key)
        assert spec is not None and spec.type == "enum" and spec.enum_values, (src.key, key)
    assert "closed" in tickets.filter_spec("state_type").enum_values  # type: ignore[union-attr]
    assert "email" in tickets.filter_spec("channel").enum_values  # type: ignore[union-attr]
    assert "reopen" in events.filter_spec("kind").enum_values  # type: ignore[union-attr]
    # a typo is rejected by validation instead of returning an empty report
    bad = ReportDefinition.model_validate(
        {
            "source": "tickets",
            "measures": [{"key": "tickets"}],
            "filter": {
                "version": 1,
                "where": {"field": "state_type", "op": "eq", "value": "clsoed"},
            },
        }
    )
    with pytest.raises(DefinitionError) as exc:
        validate_definition(bad, _registry())
    assert exc.value.path == "filter.where.value"


# SQL of the sources ---------------------------------------------------------

DIALECTS = pytest.mark.parametrize("dialect", [mysql.dialect, postgresql.dialect])


def _compile(stmt: Any, dialect: Any) -> str:
    return str(stmt.compile(dialect=dialect(), compile_kwargs={"literal_binds": True}))


@DIALECTS
def test_every_dimension_measure_and_flag_compiles_against_its_from_clause(dialect: Any) -> None:
    ctx = _ctx()
    for src in core_report_sources():
        cols = [d.expr(ctx) for d in src.dimensions] + [f.expr(ctx) for f in src.filter_fields]
        for m in src.measures:
            if m.expr is not None:
                cols.append(m.expr(ctx))
            if m.condition is not None:
                cols.append(m.condition(ctx))
            if m.denominator is not None:
                cols.append(m.denominator(ctx))
            assert (m.expr is not None) or (m.condition is not None) or m.agg == "count", m.key
        stmt = (
            select(func.count(), src.scope_column(ctx), *cols)
            .select_from(src.from_clause(ctx))
            .where(*src.base_where(ctx))
        )
        # every column belongs to the source's own FROM clause: no implicit cross join
        assert len(stmt.get_final_froms()) == 1, src.key
        assert "FROM" in _compile(stmt, dialect)


@DIALECTS
def test_sources_join_on_integer_ids_only(dialect: Any) -> None:
    ctx = _ctx()
    by_key = {s.key: s for s in core_report_sources()}
    joins = {
        "ticket_events": "JOIN tiqora_report_ticket_fact AS f ON f.ticket_id = e.ticket_id",
        "accounted_time": "JOIN ticket AS t ON t.id = ta.ticket_id",
    }
    for key, expected in joins.items():
        sql = _compile(select(1).select_from(by_key[key].from_clause(ctx)), dialect)
        assert expected in sql, sql
    sql = _compile(select(1).select_from(by_key["open_tickets"].from_clause(ctx)), dialect)
    assert "ST.id" not in sql
    assert "JOIN ticket_state AS st ON st.id = t.ticket_state_id" in sql
    assert "JOIN ticket_state_type AS stt ON stt.id = st.type_id" in sql


@DIALECTS
def test_open_tickets_base_where_and_live_measures(dialect: Any) -> None:
    ctx = _ctx()
    src = {s.key: s for s in core_report_sources()}["open_tickets"]
    where = _compile(select(1).where(*src.base_where(ctx)), dialect)
    assert "stt.name IN ('new', 'open', 'pending auto', 'pending reminder')" in where
    assert "t.archive_flag = 0" in where
    now_epoch = int(NOW.replace(tzinfo=UTC).timestamp())
    esc = _compile(select(src.measure("escalated").condition(ctx)), dialect)  # type: ignore[union-attr]
    assert f"t.escalation_time > 0 AND t.escalation_time < {now_epoch}" in esc
    age = _compile(select(src.measure("age_1_3d").condition(ctx)), dialect)  # type: ignore[union-attr]
    assert "t.create_time > '2024-06-09 10:00:00' AND t.create_time <= '2024-06-11 10:00:00'" in age
    top = _compile(select(src.measure("age_30d_plus").condition(ctx)), dialect)  # type: ignore[union-attr]
    assert top.startswith("SELECT t.create_time <= '2024-05-13 10:00:00' AS anon_1")
    assert "t.create_time >" not in top
    assert _compile(select(src.measure("unassigned").condition(ctx)), dialect).startswith(
        "SELECT t.user_id = 1 AS anon_1"
    )


@DIALECTS
def test_tickets_breach_and_near_miss_conditions(dialect: Any) -> None:
    ctx = _ctx()
    src = core_report_sources()[0]
    breach = _compile(select(src.measure("response_breaches").condition(ctx)), dialect)  # type: ignore[union-attr]
    assert "f.first_response_breached IS true" in breach
    assert (
        "f.first_response_breached IS NULL AND f.first_response_due_at IS NOT NULL "
        "AND f.first_response_due_at < '2024-06-12 10:00:00'"
    ) in breach
    near = _compile(select(src.measure("response_near_misses").condition(ctx)), dialect)  # type: ignore[union-attr]
    assert "f.first_response_breached IS false" in near
    assert "f.first_response_working_minutes * 100 >= f.first_response_target_minutes * 90" in near
    rate = src.measure("response_breach_rate")
    assert rate is not None and rate.agg == "rate" and rate.denominator is not None
    assert "f.first_response_target_minutes > 0" in _compile(select(rate.denominator(ctx)), dialect)
    fcr = src.measure("fcr_rate")
    assert fcr is not None and fcr.condition is not None
    sql = _compile(select(fcr.condition(ctx)), dialect)
    assert "f.close_count > 0 AND f.reopen_count = 0 AND f.article_out_count <= 1" in sql


@DIALECTS
def test_filters_compile_on_every_filterable_source_field(dialect: Any) -> None:
    ctx = _ctx()
    flt = Filter.model_validate(
        {
            "version": 1,
            "where": {
                "all": [
                    {"field": "reopened", "op": "is_true"},
                    {"field": "queue", "op": "in", "value": [1, 2]},
                    {"field": "state_type", "op": "neq", "value": "closed"},
                    {
                        "field": "created",
                        "op": "within_last",
                        "value": {"amount": 7, "unit": "day"},
                    },
                    {"not": {"field": "customer", "op": "contains", "value": "a_b"}},
                ]
            },
        }
    )
    src = core_report_sources()[0]
    expr = compile_conditions(flt, src.filter_resolver(ctx), now=NOW)
    assert expr is not None
    sql = _compile(select(1).select_from(src.from_clause(ctx)).where(expr), dialect)
    assert "f.reopen_count > 0 IS true" in sql or "(f.reopen_count > 0) IS true" in sql
    assert "f.queue_id IN (1, 2)" in sql
    assert "f.created_at >= '2024-06-05 10:00:00'" in sql


def test_filter_resolver_rejects_unknown_and_unfilterable_fields() -> None:
    src: ReportSource = core_report_sources()[0]
    resolve = src.filter_resolver(_ctx())
    assert resolve("nope") is None
    assert resolve("created_hour") is None
    hit = resolve("queue")
    assert hit is not None and hit[0].key == "queue"
