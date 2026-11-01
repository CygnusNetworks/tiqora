"""Report source ``changes`` (both dialects). Seed ids 972200-972299, names contain ``ist972``.

Queue 972200 (readable by agent 972200 through group 972200) and queue 972201 (foreign group).
Tickets 972210-972217 in queue 972200 carry a change record and a fact row:
  972210 closed successful (emergency not set, risk low overridden to high, in time)
  972211 closed failed (actual_end after planned_end -> late)
  972212 closed backed_out
  972213 rejected
  972214 cancelled
  972215 implementing (emergency)
  972216 approval
  972217 draft
Ticket 972220 (queue 972201) has a change and a fact; change 972221 has no ticket and no fact.
Every row written here is deleted again (changes before their tickets).
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any, get_args

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._approvals_helpers import to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.itsm.change.phases import (
    CHANGE_TYPES as ITSM_CHANGE_TYPES,
)
from tiqora.itsm.change.phases import (
    FINAL_PHASES,
    OUTCOMES,
    PHASES,
    Phase,
)
from tiqora.itsm.change.risk import RISK_LEVELS as ITSM_RISK_LEVELS
from tiqora.plugins.report_points import source_tables
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import ReportDefinition
from tiqora.reporting.engine import run_report
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.person_dimensions import NON_PERSON_DIMENSIONS, PERSON_DIMENSIONS
from tiqora.reporting.settings import ReportingSettings
from tiqora.reporting.sources.changes import (
    CHANGE_OUTCOMES,
    CHANGE_STATUSES,
    CHANGE_TYPES,
    OPEN_EXCLUDED_PHASES,
    RISK_LEVELS,
    changes_source,
)

URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
LO, HI = 972200, 972299
GROUP = QUEUE = AGENT = 972200
FOREIGN_GROUP = FOREIGN_QUEUE = 972201
BUILT = datetime(2026, 10, 1, 12, 0)
NOW = datetime(2026, 10, 2, 12, 0)
CREATED = datetime(2026, 9, 20, 8, 0)
PLAN_START = datetime(2026, 9, 25, 8, 0)
PLAN_END = datetime(2026, 9, 25, 10, 0)
BAND = {"lo": LO, "hi": HI}

# ticket id, phase, outcome, type, risk, risk override, planned_end, actual_end
CHANGES: list[tuple[int, str, str | None, str, str, str | None, datetime, datetime | None]] = [
    (972210, "closed", "successful", "normal", "low", "high", PLAN_END, datetime(2026, 9, 25, 9)),
    (972211, "closed", "failed", "normal", "medium", None, PLAN_END, datetime(2026, 9, 25, 12)),
    (972212, "closed", "backed_out", "standard", "medium", None, PLAN_END, None),
    (972213, "rejected", None, "normal", "medium", None, PLAN_END, None),
    (972214, "cancelled", None, "normal", "medium", None, PLAN_END, None),
    (972215, "implementing", None, "emergency", "medium", None, PLAN_END, None),
    (972216, "approval", None, "normal", "medium", None, PLAN_END, None),
    (972217, "draft", None, "normal", "medium", None, PLAN_END, None),
]
TICKETS = [c[0] for c in CHANGES]
FOREIGN_TICKET, NO_FACT_CHANGE = 972220, 972221
ALL_FACT_TICKETS = [*TICKETS, FOREIGN_TICKET]

_CLEANUP = (
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
            " VALUES (:id, 'ist972.chg', 'x', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
        ),
        {"id": AGENT, **stamp},
    )
    for gid, name in ((GROUP, "ist972-chg-grp"), (FOREIGN_GROUP, "ist972-chg-foreign")):
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, 1, :t, 1, :t, 1)"
            ),
            {"id": gid, "n": name, **stamp},
        )
    for qid, gid, name in (
        (QUEUE, GROUP, "ist972-chg-q"),
        (FOREIGN_QUEUE, FOREIGN_GROUP, "ist972-chg-foreign-q"),
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
    for tid in ALL_FACT_TICKETS:
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time,"
                " escalation_solution_time, archive_flag,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'ist972 change', :q, 1, 1, :u, 1, 3, :s,"
                " 'ist972-cust', 'ist972.cust@example.org', 0, 0, 0, 0, 0, 0, 0,"
                " :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "tn": f"20260920{tid}",
                "q": FOREIGN_QUEUE if tid == FOREIGN_TICKET else QUEUE,
                "s": state,
                "u": AGENT,
                "t": CREATED,
            },
        )
    rows = [
        *CHANGES,
        (FOREIGN_TICKET, "closed", "successful", "normal", "low", None, PLAN_END, PLAN_END),
        (NO_FACT_CHANGE, "closed", "successful", "normal", "low", None, PLAN_END, PLAN_END),
    ]
    for tid, phase, outcome, ctype, risk, override, planned_end, actual_end in rows:
        conn.execute(
            text(
                "INSERT INTO tiqora_itsm_change (ticket_id, change_type, phase, outcome,"
                " planned_start, planned_end, actual_end, risk_level, risk_override_level,"
                " approval_round, impact_truncated, portal_visible,"
                " created_at, created_by, updated_at, updated_by)"
                " VALUES (:id, :ct, :ph, :oc, :ps, :pe, :ae, :rl, :ro, 0, :f, :f,"
                " :t, 1, :t, 1)"
            ),
            {
                "id": tid,
                "ct": ctype,
                "ph": phase,
                "oc": outcome,
                "ps": PLAN_START,
                "pe": planned_end,
                "ae": actual_end,
                "rl": risk,
                "ro": override,
                "f": False,
                "t": CREATED,
            },
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
            await rebuild_ticket_facts(s, ALL_FACT_TICKETS, now=BUILT)
            await s.commit()
        _SEEDED.add(sync_url)
    return factory


async def _run(
    factory: async_sessionmaker[AsyncSession],
    dims: list[str],
    measures: list[str],
    where: dict[str, Any] | None = None,
) -> Any:
    defn = ReportDefinition.model_validate(
        {
            "source": "changes",
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
            params=RunParams(time_zone="UTC"),
            user_id=AGENT,
            report_key=None,
            reporting=ReportingSettings(),
            now=NOW,
            use_cache=False,
        )


def _by_key(res: Any, ndims: int = 1) -> dict[Any, list[Any]]:
    return {(row[0] if ndims == 1 else tuple(row[:ndims])): row[ndims:] for row in res.rows}


def test_phase_enum_matches_itsm_change() -> None:
    dims = {d.key: d for d in changes_source().dimensions}
    assert dims["phase"].enum_values == PHASES == get_args(Phase)
    assert dims["outcome"].enum_values == CHANGE_OUTCOMES == OUTCOMES
    assert dims["change_type"].enum_values == CHANGE_TYPES == ITSM_CHANGE_TYPES
    assert dims["risk_level"].enum_values == RISK_LEVELS == ITSM_RISK_LEVELS
    assert dims["change_status"].enum_values == CHANGE_STATUSES
    assert (
        *PHASES,
        "closed_successful",
        "closed_failed",
        "closed_backed_out",
    ) == CHANGE_STATUSES
    assert FINAL_PHASES == OPEN_EXCLUDED_PHASES == {"closed", "cancelled"}


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_counts_by_status(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    res = await _run(factory, ["change_status"], ["changes"])
    assert _by_key(res) == {
        "closed_successful": [1],
        "closed_failed": [1],
        "closed_backed_out": [1],
        "rejected": [1],
        "cancelled": [1],
        "implementing": [1],
        "approval": [1],
        "draft": [1],
    }


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_outcome_measures(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    measures = [
        "changes",
        "successful_changes",
        "failed_changes",
        "change_success_rate",
        "rejected_changes",
        "open_changes",
        "emergency_changes",
    ]
    res = await _run(factory, [], measures)
    assert len(res.rows) == 1
    got = dict(zip(measures, res.rows[0], strict=True))
    assert got["changes"] == 8
    assert got["successful_changes"] == 1
    assert got["failed_changes"] == 2
    assert float(got["change_success_rate"]) == pytest.approx(1 / 3, abs=0.001)
    assert got["rejected_changes"] == 1
    # rejected is reopenable, so it counts as open (F11): rejected, implementing, approval, draft
    assert got["open_changes"] == 4
    assert got["emergency_changes"] == 1


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_late_changes(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    res = await _run(factory, [], ["late_changes"])
    assert res.rows == [[1]]


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_risk_level_uses_override(url_fixture: str, request: pytest.FixtureRequest) -> None:
    factory = await _factory(request, url_fixture)
    res = await _run(factory, ["risk_level"], ["changes"])
    assert _by_key(res) == {"high": [1], "medium": [7]}


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_foreign_queue_change_invisible(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    factory = await _factory(request, url_fixture)
    res = await _run(factory, ["queue"], ["changes"])
    assert _by_key(res) == {QUEUE: [8]}
    hidden = await _run(
        factory,
        [],
        ["changes"],
        {"field": "queue", "op": "in", "value": [FOREIGN_QUEUE]},
    )
    assert hidden.rows in ([], [[0]])


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_change_without_fact_not_counted(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A change whose ticket has no fact row is not counted (the inner join; documented)."""
    factory = await _factory(request, url_fixture)
    res = await _run(factory, [], ["changes"])
    assert res.rows == [[8]]  # 972221 would make it 9 (successful) if it were counted
    ok = await _run(factory, [], ["successful_changes"])
    assert ok.rows == [[1]]


def test_source_reads_only_tiqora_tables() -> None:
    tables = source_tables(changes_source())
    assert tables == {"tiqora_itsm_change", "tiqora_report_ticket_fact"}


def test_source_shape_and_classification() -> None:
    src = changes_source()
    assert src.availability == "itsm_change"
    assert src.requires_facts is True
    assert src.default_time_field == "created"
    assert src.time_fields == ("created", "planned_start", "actual_end", "closed")
    assert src.archive_exclusion is not None
    keys = {d.key for d in src.dimensions}
    assert {"phase", "outcome", "change_status", "change_type", "risk_level"} <= keys
    classified = PERSON_DIMENSIONS | NON_PERSON_DIMENSIONS
    assert keys <= classified
    assert not keys & (PERSON_DIMENSIONS & NON_PERSON_DIMENSIONS)
    assert {"owner", "created_by", "customer", "customer_user"} <= PERSON_DIMENSIONS
