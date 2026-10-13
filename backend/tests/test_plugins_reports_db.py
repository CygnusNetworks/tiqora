"""Report sources and built-in reports from plugins (D15, both dialects).

Seed band 55470-55489: agent 55470, groups/queues 55471 (agent may read) and 55472 (no access),
tickets 55475/55476 (queue 55471) and 55477 (queue 55472), articles 55480-55484 (e-mail channel).
Nothing is committed by the engine runs (the report state row is rolled back with the session).
"""

from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import column, create_engine, table, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import PLUGIN_ID, boot_points_plugin
from tests._plugins_state_helpers import to_async_url
from tests.test_plugins_registrar import _registrar
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.extensions.core import get_registry
from tiqora.plugins.registrar import RegistrationRejected
from tiqora.plugins.state import set_loaded_plugins
from tiqora.reporting.catalogue import (
    BUILTIN_REPORTS,
    BuiltinReport,
    active_builtin,
    active_builtins,
    is_catalogue_key,
)
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import DefinitionError, ReportDefinition, validate_definition
from tiqora.reporting.engine import run_report
from tiqora.reporting.saved import ReportNotFound, resolve_report_ref
from tiqora.reporting.semantic import REPORT_SOURCES, ReportSource, active_source, active_sources
from tiqora.reporting.settings import ReportingSettings

pytestmark = pytest.mark.db

AGENT = 55470
QA, QB = 55471, 55472
TICKETS = (55475, 55476, 55477)
NOW = datetime(2026, 10, 13, 8, 0, 0)
T0 = datetime(2026, 1, 1, 10, 0, 0)
SOURCE_KEY = "plugin.points.articles_per_channel"
REPORT_KEY = "plugin.org.example.points.articles_by_channel"
LO, HI = 55470, 55489
_BAND = {"lo": LO, "hi": HI}
_CLEANUP = (
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_SEEDED: dict[str, int] = {}


def _clean(conn: Any) -> None:
    for stmt in _CLEANUP:
        conn.execute(text(stmt), _BAND)


def _seed(sync_url: str) -> int:
    """Seed once per database; returns the id of the e-mail communication channel."""
    if sync_url in _SEEDED:
        return _SEEDED[sync_url]
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        _clean(conn)
        email = int(
            conn.execute(
                text("SELECT id FROM communication_channel WHERE name = 'Email'")
            ).scalar_one()
        )
        sender = int(
            conn.execute(
                text("SELECT id FROM article_sender_type WHERE name = 'customer'")
            ).scalar_one()
        )
        state = int(
            conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one()
        )
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'pp55.rep', 'x', 'Rita', 'Report', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": T0},
        )
        for gid in (QA, QB):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"pp55-group-{gid}", "t": T0},
            )
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"pp55-queue-{gid}", "g": gid, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": QA, "t": T0},
        )
        for tid, queue in zip(TICKETS, (QA, QA, QB), strict=True):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'pp55 report', :q, 1, 1, 1, 1, 3, :s, '', '',"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"{tid}01", "q": queue, "s": state, "t": T0},
            )
        for aid, tid in zip(range(55480, 55485), (55475, 55475, 55476, 55477, 55477), strict=True):
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :s, :c, 1, :t, 1, :t, 1)"
                ),
                {"id": aid, "tid": tid, "s": sender, "c": email, "t": T0},
            )
    engine.dispose()
    _SEEDED[sync_url] = email
    return email


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in _SEEDED:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _clean(conn)
        engine.dispose()
    _SEEDED.clear()


@pytest.fixture
def booted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    boot_points_plugin(tmp_path, monkeypatch)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def url_fixture(request: pytest.FixtureRequest, booted: None) -> str:
    url: str = request.getfixturevalue(request.param)
    return url


def _fixture() -> Any:
    # the package re-exports the plugin *instance* as ``plugin``; import the module by name
    return importlib.import_module("tq_fixture_points.plugin")


def test_source_tables_checked(booted: None) -> None:
    fixture = _fixture()
    reg = _registrar(
        short_id="points",
        plugin_id=PLUGIN_ID,
        points=["core.reporting.sources", "core.reporting.builtin_reports"],
    )
    good = fixture.articles_per_channel_source()
    reg.report_source(good)
    assert (REPORT_SOURCES, good) in reg.staged

    def rejected(source: ReportSource, fragment: str) -> None:
        with pytest.raises(RegistrationRejected) as info:
            _registrar(
                short_id="points",
                plugin_id=PLUGIN_ID,
                points=["core.reporting.sources", "core.reporting.builtin_reports"],
            ).report_source(source)
        assert fragment in str(info.value)

    mixed = "Berichtsquelle darf nur Znuny-Tabellen oder eigene Tabellen (tqp_<kurzname>_) lesen"
    rejected(fixture.tiqora_source(), mixed)  # tiqora_report_fact
    rejected(fixture.mixed_source(), mixed)  # article + tqp_points_x
    rejected(fixture.other_plugin_source(), mixed)  # tqp_other_x
    own = fixture.own_source()  # tqp_points_x only: the table need not exist
    _registrar(
        short_id="points",
        plugin_id=PLUGIN_ID,
        points=["core.reporting.sources", "core.reporting.builtin_reports"],
    ).report_source(own)
    rejected(dataclasses.replace(good, key="articles"), "plugin.points.")
    rejected(dataclasses.replace(good, key="plugin.points.Bad Name"), "Ungültiger Name")
    rejected(dataclasses.replace(good, label_key="reports.x"), "plugin.org.example.points:")
    rejected(dataclasses.replace(good, requires_facts=True), "requires_facts")
    bad_dim = dataclasses.replace(good.dimensions[0], label_key="reports.dimensions.channel")
    rejected(dataclasses.replace(good, dimensions=(bad_dim,)), "plugin.org.example.points:")
    bad_measure = dataclasses.replace(good.measures[0], label_key="reports.measures.count")
    rejected(dataclasses.replace(good, measures=(bad_measure,)), "plugin.org.example.points:")


def test_source_table_check_walks_expressions_not_only_from_clause(booted: None) -> None:
    """F12: a ``tiqora_*`` table hidden in a measure condition or the base filter is found."""
    fixture = _fixture()
    good = fixture.articles_per_channel_source()
    hidden = table("tiqora_secret", column("id"))
    leaky_measure = dataclasses.replace(
        good.measures[0],
        agg="count_if",
        condition=lambda ctx: hidden.c.id > 0,
    )
    leaky_where = lambda ctx: [hidden.c.id > 0]  # noqa: E731
    for variant in (
        dataclasses.replace(good, measures=(leaky_measure,)),
        dataclasses.replace(good, base_where=leaky_where),
    ):
        with pytest.raises(RegistrationRejected):
            _registrar(
                short_id="points", plugin_id=PLUGIN_ID, points=["core.reporting.sources"]
            ).report_source(variant)


def test_builtin_report_registration_checks(booted: None) -> None:
    fixture = _fixture()
    points = ["core.reporting.sources", "core.reporting.builtin_reports"]
    definition = ReportDefinition.model_validate(
        {
            "source": SOURCE_KEY,
            "dimensions": [{"key": "channel"}],
            "measures": [{"key": "count"}],
        }
    )

    def reg() -> Any:
        r = _registrar(short_id="points", plugin_id=PLUGIN_ID, points=points)
        r.report_source(fixture.articles_per_channel_source())
        return r

    r = reg()
    r.builtin_report("articles_by_channel", category="volume", definition=definition)
    ((_, staged),) = [(p, i) for p, i in r.staged if p is BUILTIN_REPORTS]
    assert staged.key == REPORT_KEY and staged.plugin_id == PLUGIN_ID
    with pytest.raises(RegistrationRejected):
        reg().builtin_report("Bad Name", category="volume", definition=definition)
    with pytest.raises(RegistrationRejected):
        reg().builtin_report("x_report", category="nonsense", definition=definition)  # type: ignore[arg-type]
    other = definition.model_copy(update={"source": "plugin.other.things"})
    with pytest.raises(RegistrationRejected):
        reg().builtin_report("x_report", category="volume", definition=other)
    unknown_dim = definition.model_copy(
        update={"dimensions": [definition.dimensions[0].model_copy(update={"key": "nope"})]}
    )
    with pytest.raises(RegistrationRejected):
        reg().builtin_report("x_report", category="volume", definition=unknown_dim)
    with pytest.raises(RegistrationRejected):  # source of a plugin that is not this one's
        _registrar(short_id="points", plugin_id=PLUGIN_ID, points=points).builtin_report(
            "x_report", category="volume", definition=definition
        )


def test_builtin_report_keys(booted: None) -> None:
    registry = get_registry()
    report = registry.get(BUILTIN_REPORTS, REPORT_KEY)
    assert report is not None
    assert report.name == "articles_by_channel"
    assert report.title_key == "plugin.org.example.points:reports.articles_by_channel.title"
    assert (
        report.description_key
        == "plugin.org.example.points:reports.articles_by_channel.description"
    )
    assert report.plugin_id == PLUGIN_ID and report.definition.source == SOURCE_KEY
    assert REPORT_KEY in [r.key for r in active_builtins(registry)]
    assert is_catalogue_key(REPORT_KEY) and is_catalogue_key("builtin.volume")
    assert not is_catalogue_key("42")
    # core built-ins are unchanged
    core = registry.get(BUILTIN_REPORTS, "builtin.volume")
    assert core is not None and core.plugin_id is None
    assert core.name == "volume" and core.title_key == "reports.builtin.volume.title"
    assert core.description_key == "reports.builtin.volume.description"
    source = registry.get(REPORT_SOURCES, SOURCE_KEY)
    assert source is not None and source.label_key.startswith(f"plugin.{PLUGIN_ID}:")
    assert [s.key for s in active_sources(registry)].count(SOURCE_KEY) == 1


def test_catalogue_survives_plugin_overview_builtin(booted: None) -> None:
    """F20: the catalogue/stats code looks parameters up with ``.get``; no 500 for a plugin."""
    from tiqora.api.v1.reports import _builtin_out
    from tiqora.reporting.catalogue import QUEUE_PARAM_FIELD

    registry = get_registry()
    report = registry.get(BUILTIN_REPORTS, REPORT_KEY)
    assert report is not None
    overview = dataclasses.replace(report, overview=True)
    out = _builtin_out(overview)
    assert out.overview is True and out.key == REPORT_KEY
    assert out.title_key == overview.title_key
    assert QUEUE_PARAM_FIELD.get(SOURCE_KEY) is None


@pytest.mark.asyncio
async def test_source_scope_enforced(url_fixture: str) -> None:
    email = _seed(url_fixture)
    report = get_registry().get(BUILTIN_REPORTS, REPORT_KEY)
    assert isinstance(report, BuiltinReport)
    engine = create_async_engine(to_async_url(url_fixture))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await run_report(
                session,
                defn=report.definition,
                params=RunParams(time_zone="UTC"),
                user_id=AGENT,
                report_key=REPORT_KEY,
                reporting=ReportingSettings(),
                now=NOW,
            )
            await session.rollback()
    finally:
        await engine.dispose()
    # queue A: 3 articles; the 2 articles of queue B (no access) are not counted
    assert result.rows == [[email, 3]]
    assert result.report_key == REPORT_KEY


@pytest.mark.asyncio
async def test_disabled_plugin_reports_hidden(url_fixture: str) -> None:
    _seed(url_fixture)
    registry = get_registry()
    saved = ReportDefinition.model_validate(
        {
            "source": SOURCE_KEY,
            "dimensions": [{"key": "channel"}],
            "measures": [{"key": "count"}],
        }
    )
    engine = create_async_engine(to_async_url(url_fixture))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            resolved = await resolve_report_ref(session, REPORT_KEY, AGENT, admin=False)
            assert resolved.builtin is not None and resolved.key == REPORT_KEY
            assert active_source(registry, SOURCE_KEY) is not None
            assert validate_definition(saved, registry).key == SOURCE_KEY

            set_loaded_plugins([])  # the plugin is switched off in this process

            assert active_source(registry, SOURCE_KEY) is None
            assert SOURCE_KEY not in [s.key for s in active_sources(registry)]
            assert active_builtin(registry, REPORT_KEY) is None
            assert REPORT_KEY not in [r.key for r in active_builtins(registry)]
            # core items stay
            assert active_builtin(registry, "builtin.volume") is not None
            assert active_source(registry, "tickets") is not None
            with pytest.raises(ReportNotFound):
                await resolve_report_ref(session, REPORT_KEY, AGENT, admin=False)
            # a saved report on the plugin source answers with the existing "unknown source" error
            with pytest.raises(DefinitionError) as info:
                await run_report(
                    session,
                    defn=saved,
                    params=RunParams(time_zone="UTC"),
                    user_id=AGENT,
                    report_key="1",
                    reporting=ReportingSettings(),
                    now=NOW,
                )
            assert info.value.path == "source" and "Unbekannte" in info.value.message
            await session.rollback()
    finally:
        await engine.dispose()


def test_source_tables_resolve_aliases_to_real_tables() -> None:
    from tiqora.plugins.report_points import source_tables
    from tiqora.reporting.sources.open_tickets import open_tickets_source
    from tiqora.reporting.sources.tickets import tickets_source

    assert source_tables(open_tickets_source()) == {"ticket", "ticket_state", "ticket_state_type"}
    assert any(name.startswith("tiqora_") for name in source_tables(tickets_source()))
