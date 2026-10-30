"""Report source `change_events` (both dialects). Seed ids 972800-972849, names `ist972`.

Queue 972800 (readable by agent 972800 through group 972800) and queue 972801 (foreign group).
Change tickets in queue 972800, each with a change record, a fact row and phase events:
  972810 rejected twice (draft -> submitted -> approval -> rejected, reworked, rejected again)
  972811 rejected -> cancelled (the itsm-change final fix)
  972812 forced emergency path (a ``forced`` row plus ONE phase row draft -> implementing)
  972813 one phase event in August (outside the September window)
Ticket 972820 (queue 972801) has a change, a fact and a rejection: invisible to the agent.
Every row written here is deleted again (events and changes before their tickets).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._approvals_helpers import to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.itsm.change.phases import PHASES
from tiqora.plugins.report_points import source_tables
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import AbsoluteTimeRange, ReportDefinition
from tiqora.reporting.engine import run_report
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.person_dimensions import NON_PERSON_DIMENSIONS, PERSON_DIMENSIONS
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.sources.change_events import change_events_source

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
LO, HI = 972800, 972849
GROUP = QUEUE = AGENT = 972800
FOREIGN_GROUP = FOREIGN_QUEUE = 972801
BUILT = datetime(2026, 10, 1, 12, 0)
NOW = datetime(2026, 10, 2, 12, 0)
CREATED = datetime(2026, 8, 1, 8, 0)
BAND = {"lo": LO, "hi": HI}
SEPTEMBER = AbsoluteTimeRange(start=date(2026, 9, 1), end=date(2026, 9, 30))

TWICE, RETRACTED, FORCED, OLD, FOREIGN = 972810, 972811, 972812, 972813, 972820
TICKETS = [TWICE, RETRACTED, FORCED, OLD, FOREIGN]


def _creation(
    ticket_id: int, at: datetime
) -> list[tuple[int, str, str | None, str | None, datetime]]:
    """What ``create_change`` / ``adopt_ticket`` write: ``created`` plus a ``phase`` row with
    ``from_phase`` NULL -> ``draft``. The latter is no phase change and must not be counted."""
    return [(ticket_id, "created", None, "draft", at), (ticket_id, "phase", None, "draft", at)]


# ticket id, event, from phase, to phase, created_at. Expected figures follow from these rows:
# in September the visible changes have 10 real phase changes (TWICE 7, RETRACTED 2, FORCED 1),
# 3 of them to ``rejected`` on 2 changes (the ruling's 2/1 describes one change rejected twice;
# the extra RETRACTED change rejected once makes 3/2).
EVENTS: list[tuple[int, str, str | None, str | None, datetime]] = [
    *_creation(TWICE, datetime(2026, 9, 1, 8)),
    *_creation(RETRACTED, datetime(2026, 9, 1, 8)),
    *_creation(FORCED, datetime(2026, 9, 1, 8)),
    *_creation(OLD, datetime(2026, 8, 1, 8)),
    *_creation(FOREIGN, datetime(2026, 9, 1, 8)),
    (TWICE, "phase", "draft", "submitted", datetime(2026, 9, 2, 8)),
    (TWICE, "phase", "submitted", "approval", datetime(2026, 9, 2, 9)),
    (TWICE, "phase", "approval", "rejected", datetime(2026, 9, 3, 8)),
    (TWICE, "phase", "rejected", "draft", datetime(2026, 9, 10, 8)),
    (TWICE, "phase", "draft", "submitted", datetime(2026, 9, 10, 9)),
    (TWICE, "phase", "submitted", "approval", datetime(2026, 9, 10, 10)),
    (TWICE, "approval_outcome", None, None, datetime(2026, 9, 11, 7)),
    (TWICE, "phase", "approval", "rejected", datetime(2026, 9, 11, 8)),
    (RETRACTED, "phase", "approval", "rejected", datetime(2026, 9, 12, 8)),
    (RETRACTED, "phase", "rejected", "cancelled", datetime(2026, 9, 13, 8)),
    (FORCED, "forced", None, None, datetime(2026, 9, 14, 8)),
    (FORCED, "phase", "draft", "implementing", datetime(2026, 9, 14, 8)),
    (OLD, "phase", "draft", "submitted", datetime(2026, 8, 5, 8)),
    (FOREIGN, "phase", "approval", "rejected", datetime(2026, 9, 12, 8)),
]

_CLEANUP = (
    "DELETE FROM tiqora_itsm_change_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_itsm_change WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt), BAND)
    finally:
        engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()
    _SEEDED.clear()


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            _seed_rows(conn)
    finally:
        engine.dispose()


def _seed_rows(conn: Any) -> None:
    stamp = {"t": CREATED}
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, 'ist972.chgev', 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
        ),
        {"id": AGENT, **stamp},
    )
    for gid, name in ((GROUP, "ist972-chgev-grp"), (FOREIGN_GROUP, "ist972-chgev-foreign")):
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, 1, :t, 1, :t, 1)"
            ),
            {"id": gid, "n": name, **stamp},
        )
    for qid, gid, name in (
        (QUEUE, GROUP, "ist972-chgev-q"),
        (FOREIGN_QUEUE, FOREIGN_GROUP, "ist972-chgev-foreign-q"),
    ):
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " first_response_time, solution_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, 60, 480, :t, 1, :t, 1)"
            ),
            {"id": qid, "n": name, "g": gid, **stamp},
        )
    conn.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
        ),
        {"u": AGENT, "g": GROUP, **stamp},
    )
    state = int(conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one())
    for tid in TICKETS:
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'ist972 change event', :q, 1, 1, :u, 1, 3, :s,"
                " 'ist972-cust', 'ist972.cust@example.org', 0, 0, 0, 0, 0, 0, 0,"
                " :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"20260801{tid}",
                "q": FOREIGN_QUEUE if tid == FOREIGN else QUEUE,
                "s": state,
                "u": AGENT,
                "t": CREATED,
            },
        )
    for tid in TICKETS:
        conn.execute(
            text(
                "INSERT INTO tiqora_itsm_change (ticket_id, change_type, phase,"
                " approval_round, impact_truncated, portal_visible,"
                " created_at, created_by, updated_at, updated_by)"
                " VALUES (:id, :ct, 'draft', 0, :f, :f, :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "ct": "emergency" if tid == FORCED else "normal",
                "f": False,
                "t": CREATED,
            },
        )
    for tid, event, frm, to, at in EVENTS:
        conn.execute(
            text(
                "INSERT INTO tiqora_itsm_change_event (ticket_id, created_at, created_by,"
                " event, from_phase, to_phase) VALUES (:id, :at, 1, :ev, :f, :to)"
            ),
            {"id": tid, "at": at, "ev": event, "f": frm, "to": to},
        )


async def _factory(
    request: pytest.FixtureRequest, url_fixture: str
) -> async_sessionmaker[AsyncSession]:
    sync_url = request.getfixturevalue(url_fixture)
    engine = create_async_engine(to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    if sync_url not in _SEEDED:
        _URLS.add(sync_url)
        _clean(sync_url)
        _seed(sync_url)
        async with factory() as s:
            await rebuild_ticket_facts(s, TICKETS, now=BUILT)
            await s.commit()
        _SEEDED.add(sync_url)
    return factory


async def _run(
    factory: async_sessionmaker[AsyncSession],
    dims: list[str],
    measures: list[str],
    where: dict[str, Any] | None = None,
    time_range: AbsoluteTimeRange | None = None,
) -> Any:
    defn = ReportDefinition.model_validate(
        {
            "source": "change_events",
            "dimensions": [{"key": d} for d in dims],
            "measures": [{"key": m} for m in measures],
            "filter": {"version": 1, "where": where} if where else None,
            "visualization": {"type": "table"},
        }
    )
    async with factory() as s:
        return await run_report(
            s,
            defn=defn,
            params=RunParams(time_zone="UTC", time_range=time_range),
            user_id=AGENT,
            report_key=None,
            reporting=ReportingSettings(),
            now=NOW,
            use_cache=False,
        )


def _by_key(res: Any) -> dict[Any, list[Any]]:
    return {row[0]: row[1:] for row in res.rows}


def test_phase_enums_match_itsm_change() -> None:
    dims = {d.key: d for d in change_events_source().dimensions}
    assert dims["from_phase"].enum_values == PHASES
    assert dims["to_phase"].enum_values == PHASES
    assert dims["to_phase"].i18n_values == "reports.values.change_phase"


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rejected_twice_counts_two_events_one_change(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    factory = await _factory(request, url_fixture)
    where = {"field": "to_phase", "op": "in", "value": ["rejected"]}
    res = await _run(factory, [], ["phase_changes", "changes"], where, SEPTEMBER)
    # TWICE: two rejections, RETRACTED: one (FOREIGN is invisible)
    assert res.rows == [[3, 2]]


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_every_phase_change_counts_one_row(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """created / forced / approval_outcome rows are no phase changes; the forced emergency
    path and rejected -> cancelled count exactly one phase row each."""
    factory = await _factory(request, url_fixture)
    res = await _run(factory, ["to_phase"], ["phase_changes", "changes"], None, SEPTEMBER)
    assert _by_key(res) == {
        "submitted": [2, 1],
        "approval": [2, 1],
        "rejected": [3, 2],
        "draft": [1, 1],
        "cancelled": [1, 1],
        "implementing": [1, 1],
    }
    cancelled = await _run(
        factory,
        ["from_phase", "to_phase"],
        ["phase_changes"],
        {"field": "to_phase", "op": "in", "value": ["cancelled", "implementing"]},
        SEPTEMBER,
    )
    assert {(r[0], r[1]): r[2] for r in cancelled.rows} == {
        ("rejected", "cancelled"): 1,
        ("draft", "implementing"): 1,
    }


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_change_type_dimension(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    res = await _run(factory, ["change_type"], ["phase_changes"], None, SEPTEMBER)
    assert _by_key(res) == {"normal": [9], "emergency": [1]}


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_foreign_queue_change_invisible(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    factory = await _factory(request, url_fixture)
    res = await _run(factory, ["queue"], ["phase_changes"], None, SEPTEMBER)
    assert _by_key(res) == {QUEUE: [10]}
    hidden = await _run(
        factory,
        [],
        ["phase_changes"],
        {"field": "queue", "op": "in", "value": [FOREIGN_QUEUE]},
        SEPTEMBER,
    )
    assert hidden.rows in ([], [[0]])


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_window_on_occurred(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    august = AbsoluteTimeRange(start=date(2026, 8, 1), end=date(2026, 8, 31))
    res = await _run(factory, ["occurred"], ["phase_changes"], None, august)
    assert [r for r in res.rows if r[1]] == [["2026-08-05", 1]]
    day = AbsoluteTimeRange(start=date(2026, 9, 11), end=date(2026, 9, 11))
    one = await _run(factory, [], ["phase_changes"], None, day)
    assert one.rows == [[1]]  # the approval_outcome row of the same day is not a phase change


def test_source_reads_only_tiqora_tables() -> None:
    tables = source_tables(change_events_source())
    assert tables == {
        "tiqora_itsm_change_event",
        "tiqora_itsm_change",
        "tiqora_report_ticket_fact",
    }


def test_source_shape_and_classification() -> None:
    src = change_events_source()
    assert src.availability == "itsm_tiqora"
    assert src.requires_facts is True
    assert src.time_fields == ("occurred",)
    assert src.default_time_field == "occurred"
    assert src.archive_exclusion is not None
    assert src.common_fields == {"queue": "queue", "priority": "priority", "service": "service"}
    keys = {d.key for d in src.dimensions}
    assert keys == {
        "occurred",
        "from_phase",
        "to_phase",
        "change_type",
        "queue",
        "priority",
        "service",
    }
    assert keys <= PERSON_DIMENSIONS | NON_PERSON_DIMENSIONS
    assert not keys & PERSON_DIMENSIONS
    assert {m.key for m in src.measures} == {"phase_changes", "changes"}


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_real_service_creation_and_transition(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A change made by ``create_change`` and moved by ``transition`` counts exactly the
    transition: the seeds above cannot drift from what production writes."""
    from tests._change_fixtures import (
        APP_SETTINGS,
        Q_A,
        U_FULL,
        change_world,
    )
    from tiqora.itsm.change.service import ChangeCreateIn, create_change
    from tiqora.itsm.change.transitions import TransitionIn, transition
    from tiqora.jobs.service import utc_now
    from tiqora.znuny.sysconfig import SysConfig

    async with change_world(request, url_fixture) as w:
        data = ChangeCreateIn.model_validate(
            {"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A}
        )
        view, _job = await create_change(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            data=data,
            user_id=U_FULL,
            session_factory=w.factory,
        )
        tid = view.record.ticket_id
        await transition(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=tid,
            data=TransitionIn(to="submitted"),
            user_id=U_FULL,
        )
        await rebuild_ticket_facts(w.session, [tid], now=utc_now())
        today = utc_now().date()
        defn = ReportDefinition.model_validate(
            {
                "source": "change_events",
                "dimensions": [{"key": "from_phase"}, {"key": "to_phase"}],
                "measures": [{"key": "phase_changes"}],
                "filter": {"version": 1, "where": {"field": "queue", "op": "in", "value": [Q_A]}},
                "visualization": {"type": "table"},
            }
        )
        res = await run_report(
            w.session,
            defn=defn,
            params=RunParams(
                time_zone="UTC",
                time_range=AbsoluteTimeRange(start=today, end=today),
            ),
            user_id=U_FULL,
            report_key=None,
            reporting=ReportingSettings(),
            now=utc_now(),
            use_cache=False,
        )
        assert [list(r) for r in res.rows] == [["draft", "submitted", 1]]
