"""Semantic layer: scope kinds, extra scopes, availability defaults, type roles, cache key.

Pure apart from ``test_load_type_roles`` (both dialects, names ``ist972``; ``ticket_type.id`` is a
SMALLINT on MariaDB, so its rows take autoincrement ids and are found by name; it deletes its own
rows). The tests on the CMDB world are in
``test_reporting_scope_kinds_cmdb.py``: ``_cmdb_rows_reset`` is autouse and would put every test
of a module on the database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, get_args

import pytest
from sqlalchemy import Integer, column, create_engine, delete, insert, table, text
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.extensions.registry import Registry
from tiqora.filters.tickets import TicketQueryScope
from tiqora.itsm.summary import Role
from tiqora.plugins.report_points import source_tables
from tiqora.reporting.cache import ReportCache
from tiqora.reporting.compiler import (
    HIDDEN_QUEUE_ID,
    CompiledReport,
    ReportCompileError,
    RunParams,
    compile_report,
)
from tiqora.reporting.definition import ReportDefinition
from tiqora.reporting.itsm_roles import (
    INCIDENT_ROLES,
    ITSM_ROLES,
    itsm_role_dim,
    load_type_roles,
    role_type_ids,
)
from tiqora.reporting.semantic import REPORT_SOURCES, CompileContext, ReportSource
from tiqora.reporting.sources import core_report_sources
from tiqora.reporting.sources._common import col, dim, measure

NOW = datetime(2026, 10, 15, 8, 0)
QUEUES = frozenset({9711, 9712})
CLASSES = frozenset({8801, 8802})
SCOPE = TicketQueryScope(user_id=1, queue_ids=QUEUES)
NO_QUEUES = TicketQueryScope(user_id=1, queue_ids=frozenset())

CI = table(
    "ist972_ci",
    column("id", Integer),
    column("class_id", Integer),
    column("queue_id", Integer),
    column("type_id", Integer),
)
OTHER = table("ist972_other", column("class_id", Integer))


def _source(**over: Any) -> ReportSource:
    """Class-scoped test source with a queue-labelled, a class and a type-role dimension."""
    args: dict[str, Any] = {
        "key": "ist972_cis",
        "label_key": "reports.sources.ist972_cis",
        "from_clause": lambda ctx: CI,
        "scope_column": col(CI, "class_id"),
        "scope_kind": "cmdb_class",
        "dimensions": (
            dim("queue", "id", col(CI, "queue_id"), labels="queue"),
            dim("class", "id", col(CI, "class_id")),
            itsm_role_dim("itsm_role", col(CI, "type_id")),
        ),
        "measures": (measure("cis", "count"),),
        "requires_facts": False,
    }
    args.update(over)
    return ReportSource(**args)


def _queue_source(**over: Any) -> ReportSource:
    """The same table scoped by its queue column, optionally with an extra class scope."""
    return _source(scope_column=col(CI, "queue_id"), scope_kind="queue", **over)


def _compile(
    src: ReportSource,
    scope: TicketQueryScope = SCOPE,
    *,
    dims: tuple[str, ...] = (),
    flt: dict[str, Any] | None = None,
    **kwargs: Any,
) -> CompiledReport:
    reg = Registry()
    reg.add(REPORT_SOURCES, src)
    body: dict[str, Any] = {
        "source": src.key,
        "dimensions": [{"key": k} for k in dims],
        "measures": [{"key": "cis"}],
    }
    if flt is not None:
        body["filter"] = {"version": 1, "where": flt}
    return compile_report(
        ReportDefinition.model_validate(body),
        RunParams(),
        scope,
        now=NOW,
        near_miss_percent=10,
        registry=reg,
        **kwargs,
    )


def _sql(stmt: Any, dialect: Any = None) -> str:
    return str(
        stmt.compile(
            dialect=dialect if dialect is not None else postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _ctx(type_roles: dict[int, str] | None = None) -> CompileContext:
    return CompileContext(
        now=NOW,
        time_zone="UTC",
        tz=(),
        near_miss_percent=10,
        window=None,
        type_roles=type_roles or {},
    )


def _key(**over: Any) -> str:
    args: dict[str, Any] = {
        "definition_hash": "h",
        "queue_ids": [1, 2],
        "window": None,
        "time_zone": "UTC",
        "row_cap": 10,
        "compare_previous": False,
        "include_archived": False,
        "extra_filter": None,
        "last_snapshot_date": None,
        "near_miss_percent": 10,
    }
    args.update(over)
    return ReportCache.key_for(**args)


# ---------------------------------------------------------------- source defaults


def test_source_defaults_keep_queue_scope() -> None:
    by_key = {s.key: s for s in core_report_sources()}
    for key in ("tickets", "ticket_events", "daily_snapshot", "accounted_time", "open_tickets"):
        src = by_key[key]
        assert src.scope_kind == "queue"
        assert src.extra_scopes == ()
        assert src.availability == "always"
        assert src.scope_kinds() == frozenset({"queue"})


def test_scope_kinds_lists_primary_and_extra_kinds() -> None:
    src = _queue_source(extra_scopes=(("cmdb_class", col(CI, "class_id")),))
    assert src.scope_kinds() == frozenset({"queue", "cmdb_class"})
    assert _source().scope_kinds() == frozenset({"cmdb_class"})


# ---------------------------------------------------------------- compiler scope


def test_cmdb_scope_without_classes_compiles_to_nothing() -> None:
    c = _compile(_source(), cmdb_class_ids=frozenset())
    assert c.stmt is None and c.raw_stmt is None


def test_cmdb_scope_missing_ids_is_compile_error() -> None:
    for scope in (SCOPE, NO_QUEUES):
        with pytest.raises(ReportCompileError) as exc:
            _compile(_source(), scope, cmdb_class_ids=None)
        assert exc.value.path == "source"
        assert exc.value.message == "Die CMDB-Berechtigungen fehlen."


def test_cmdb_extra_scope_missing_ids_is_compile_error() -> None:
    src = _queue_source(extra_scopes=(("cmdb_class", col(CI, "class_id")),))
    with pytest.raises(ReportCompileError) as exc:
        _compile(src, cmdb_class_ids=None)
    assert exc.value.path == "source"


def test_extra_scope_is_anded() -> None:
    src = _queue_source(extra_scopes=(("cmdb_class", col(CI, "class_id")),))
    where = _sql(_compile(src, cmdb_class_ids=CLASSES).stmt).split("WHERE", 1)[1]
    queue_at = where.index("ist972_ci.queue_id IN (9711, 9712)")
    class_at = where.index("ist972_ci.class_id IN (8801, 8802)")
    assert queue_at < class_at
    assert " AND " in where[queue_at:class_at]
    assert " OR " not in where


def test_empty_extra_scope_or_primary_scope_compiles_to_nothing() -> None:
    src = _queue_source(extra_scopes=(("cmdb_class", col(CI, "class_id")),))
    assert _compile(src, cmdb_class_ids=frozenset()).stmt is None
    assert _compile(src, NO_QUEUES, cmdb_class_ids=CLASSES).stmt is None


def test_hidden_queue_uses_queue_ids_on_class_scoped_source() -> None:
    c = _compile(_source(), dims=("queue",), cmdb_class_ids=CLASSES)
    s = _sql(c.stmt)
    case_part = s.split("FROM", 1)[0]
    assert "CASE WHEN (ist972_ci.queue_id IN (9711, 9712))" in case_part
    assert str(HIDDEN_QUEUE_ID) in case_part
    assert "8801" not in case_part
    assert "ist972_ci.class_id IN (8801, 8802)" in s.split("WHERE", 1)[1]


def test_class_scoped_source_ignores_empty_queue_scope() -> None:
    """F7: a viewer with class rights and no queue ``ro`` still gets the class-scoped report."""
    c = _compile(_source(), NO_QUEUES, dims=("queue",), cmdb_class_ids=CLASSES)
    assert c.stmt is not None
    for dialect in (mysql.dialect(), postgresql.dialect()):
        s = _sql(c.stmt, dialect)
        assert "ist972_ci.class_id IN (8801, 8802)" in s.split("WHERE", 1)[1]
        assert "9711" not in s


def test_queue_source_ignores_class_ids() -> None:
    """A purely queue-scoped source neither needs class ids nor reacts to them."""
    assert _compile(_queue_source()).stmt is not None
    c = _compile(_queue_source(), cmdb_class_ids=frozenset())
    assert c.stmt is not None
    assert "8801" not in _sql(c.stmt)


def test_probe_includes_extra_scopes() -> None:
    src = _queue_source(extra_scopes=(("cmdb_class", col(OTHER, "class_id")),))
    assert "ist972_other" in source_tables(src)
    assert "ist972_other" not in source_tables(_queue_source())


# ---------------------------------------------------------------- cache key


def test_cache_key_includes_class_ids() -> None:
    assert _key(cmdb_class_ids={1}) != _key(cmdb_class_ids={1, 2})
    assert _key(cmdb_class_ids={1, 2}) == _key(cmdb_class_ids=[2, 1])
    assert _key(cmdb_class_ids=None) == _key()
    assert _key(cmdb_class_ids=frozenset()) != _key()


def test_cache_key_includes_type_roles() -> None:
    assert _key(type_roles={}) == _key()
    assert _key(type_roles=None) == _key()
    assert _key(type_roles={2: "incident"}) != _key()
    assert _key(type_roles={2: "incident"}) != _key(type_roles={2: "problem"})
    assert _key(type_roles={2: "incident", 3: "problem"}) == _key(
        type_roles={3: "problem", 2: "incident"}
    )


# ---------------------------------------------------------------- type roles


def test_itsm_role_dim_case_and_empty() -> None:
    roles = {2: "incident", 3: "major_incident"}
    for dialect in (mysql.dialect(), postgresql.dialect()):
        s = _sql(
            _compile(_source(), dims=("itsm_role",), cmdb_class_ids=CLASSES, type_roles=roles).stmt,
            dialect,
        )
        assert "CASE ist972_ci.type_id WHEN 2 THEN 'incident' WHEN 3 THEN 'major_incident'" in s
        empty = _sql(_compile(_source(), dims=("itsm_role",), cmdb_class_ids=CLASSES).stmt, dialect)
        assert "CASE" not in empty
        assert "CAST(NULL AS" in empty
    d = itsm_role_dim("itsm_role", col(CI, "type_id"))
    assert d.kind == "enum" and d.filter_type == "enum"
    assert d.enum_values == ITSM_ROLES
    assert d.i18n_values == "reports.values.itsm_role"
    assert d.label_key == "reports.dimensions.itsm_role"


def test_itsm_role_filter_compares_the_case() -> None:
    c = _compile(
        _source(),
        flt={"field": "itsm_role", "op": "in", "value": ["incident"]},
        cmdb_class_ids=CLASSES,
        type_roles={2: "incident"},
    )
    where = _sql(c.stmt).split("WHERE", 1)[1]
    assert "CASE ist972_ci.type_id WHEN 2 THEN 'incident'" in where


def test_role_type_ids() -> None:
    ctx = _ctx({3: "major_incident", 2: "incident", 4: "service_request"})
    assert role_type_ids(ctx, INCIDENT_ROLES) == [2, 3]
    assert role_type_ids(ctx, ()) == []
    assert role_type_ids(_ctx(), INCIDENT_ROLES) == []


def test_itsm_roles_match_summary_role() -> None:
    assert get_args(Role) == ITSM_ROLES


# ---------------------------------------------------------------- load_type_roles (DB)

_TYPE_NAMES = {
    "itsm.incident_type": "ist972 Incident",
    "itsm.problem_type": "ist972 Problem",
    # configured but absent from ticket_type: skipped (also keeps the test independent of
    # whatever the default names find in the dump)
    "itsm.major_incident_type": "ist972 no major",
    "itsm.service_request_type": "ist972 no request",
    "itsm.change_type": "ist972 no change",
    "itsm.change_task_type": "ist972 no task",
}


def _clean_type_roles(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(list(_TYPE_NAMES))))
            conn.execute(text("DELETE FROM ticket_type WHERE name LIKE 'ist972 %'"))
    finally:
        engine.dispose()


@pytest.mark.db
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_load_type_roles(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    engine.dispose()
    _clean_type_roles(sync_url)
    async_engine = create_async_engine(to_async_url(sync_url))
    try:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for name in ("ist972 Incident", "ist972 Problem", "ist972 Sonstiges"):
                conn.execute(
                    text(
                        "INSERT INTO ticket_type (name, valid_id, create_time, create_by,"
                        " change_time, change_by) VALUES (:n, 1, :t, 1, :t, 1)"
                    ),
                    {"n": name, "t": NOW},
                )
            ids = dict(
                conn.execute(
                    text("SELECT name, id FROM ticket_type WHERE name LIKE 'ist972 %'")
                ).all()
            )
            conn.execute(
                insert(TiqoraSettings),
                [{"key": k, "value": v} for k, v in _TYPE_NAMES.items()],
            )
        engine.dispose()
        factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            assert await load_type_roles(session) == {
                ids["ist972 Incident"]: "incident",
                ids["ist972 Problem"]: "problem",
            }
    finally:
        await async_engine.dispose()
        _clean_type_roles(sync_url)
