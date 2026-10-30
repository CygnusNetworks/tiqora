"""The 16 ITSM built-in reports on the shared CMDB world: exact values, scope, ``queues``.

Both dialects, driven by ``cmdb_db`` (module scoped, re-exported below). Seed ids 972500-972699,
names contain ``ist972``. Everything is Znuny rows plus ``tiqora_*`` rows, deleted again.

Viewer ``USER_A`` reads QUEUE_RW and QUEUE_Q2 (group ``ro``) and the CI classes ``ist972srv``
(GROUP_RW) and ``ist972net`` (GROUP_RO); ``USER_B`` has only ``ro`` on GROUP_RO: QUEUE_Q2 and
class net. ``ist972sec`` (GROUP_HIDDEN) and QUEUE_HIDDEN are foreign to both.

Tickets (ids 972510-972520), created in June 2024 unless noted, all with priority ``3 normal``:
  QUEUE_RW  972510 incident 06-03, first level, closed after 120 min
            972511 request 06-03, first level, closed after 60 min
            972512 incident 06-04, customer/agent/customer, closed after 240 min
            972513 incident 06-10, closed after 60, reopened after 120, closed after 150
                   (open spans 60 + 30 = 90), customer answered after the reopen: not first level
            972514 incident 06-17, never closed
            972515 change C1 06-05 (normal, closed successful, one rejection on 06-08 reworked)
            972516 change C2 06-12 (emergency, closed backed_out, finished late)
            972517 change C3 06-20 (normal, currently rejected, risk overridden to very_high)
  QUEUE_Q2  972520 incident 05-20 (May), open: the only ticket outside the June window
  QUEUE_HIDDEN 972518 incident 06-03 closed after 30 min, 972519 change (rejected, reworked,
            cancelled): foreign, must be invisible everywhere.
Change rows and their events are what ``create_change`` and the transitions write: the creation
pair ``created`` plus ``phase`` NULL -> draft (excluded from ``change_events``), one ``phase`` row
per transition, ``approval_outcome`` rows in addition.

CIs (created 2024-06 through SQL, the service stamps the real clock): ``srv-db-01`` and
``srv-web-01`` (srv), ``sw-core-01`` (net, planned), ``vault-01`` (sec). Links as in the plan:
see ``LINKS``. Reports run with ``time_range`` June 2024 where they have one, ``NOW`` is
2024-07-01 10:00.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter
from sqlalchemy import delete

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_RO,
    GROUP_RW,
    QUEUE_HIDDEN,
    QUEUE_RW,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    make_ci,
    make_class,
    set_mode,
)
from tiqora.cmdb.gate import reset_mode_cache
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.reporting.catalogue import BuiltinReport
from tiqora.reporting.catalogue_itsm import ITSM_EXPECTED, itsm_builtin_reports
from tiqora.reporting.compiler import ReportCompileError, RunParams
from tiqora.reporting.definition import TimeRange
from tiqora.reporting.engine import run_report
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.schemas import ReportResultOut
from tiqora.reporting.settings import ReportingSettings

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

LO, HI = 972500, 972699
QUEUE_Q2 = 972501
(
    T_INC_FLS,
    T_REQ_FLS,
    T_INC_LONG,
    T_INC_REOPEN,
    T_INC_OPEN,
    C1,
    C2,
    C3,
    T_HID_INC,
    C_HID,
    T_Q2,
) = range(972510, 972521)
TICKETS = tuple(range(972510, 972521))
BUILT = datetime(2024, 7, 1, 9, 0)
NOW = datetime(2024, 7, 1, 10, 0)
STAMP = datetime(2024, 6, 1, 8, 0)
DB, WEB, CORE, SEC = (
    "srv-db-01.example.org",
    "srv-web-01.example.org",
    "sw-core-01.example.org",
    "vault-01.example.org",
)
INC, REQ, CHG = "ist972 Incident", "ist972 Request", "ist972 Change"
PRIO = "3 normal"
SRV, NET, SECRET = "ist972srv", "ist972net", "ist972sec"
SETTINGS = {
    "itsm.incident_type": INC,
    "itsm.service_request_type": REQ,
    "itsm.change_type": CHG,
    # configured but absent from ticket_type: no other type gets a role
    "itsm.major_incident_type": "ist972 no major",
    "itsm.problem_type": "ist972 no problem",
    "itsm.change_task_type": "ist972 no task",
}


def _range(start: str, end: str) -> TimeRange:
    return TypeAdapter(TimeRange).validate_python({"kind": "absolute", "start": start, "end": end})


JUNE = _range("2024-06-01", "2024-06-30")
MAY_JUNE = _range("2024-05-01", "2024-06-30")


def _at(day: int, hour: int = 8, minute: int = 0) -> datetime:
    return datetime(2024, 6, day, hour, minute)


# ticket id, type, queue, created, customer user, articles (sender, minutes, visible),
# history (state, minutes after creation)
_TICKET_SPEC: list[tuple[Any, ...]] = [
    (
        T_INC_FLS, INC, QUEUE_RW, _at(3), "ist972.cust@example.org",
        [("customer", 0, True), ("agent", 30, True)], [("closed", 120)],
    ),
    (
        T_REQ_FLS, REQ, QUEUE_RW, _at(3, 9), "ist972.cust@example.org",
        [("customer", 0, True), ("agent", 30, True)], [("closed", 60)],
    ),
    (
        T_INC_LONG, INC, QUEUE_RW, _at(4), "ist972.cust@example.org",
        [("customer", 0, True), ("agent", 30, True), ("customer", 60, True)], [("closed", 240)],
    ),
    (
        T_INC_REOPEN, INC, QUEUE_RW, _at(10), "ist972.cust@example.org",
        [("customer", 0, True), ("agent", 30, True), ("customer", 125, True)],
        [("closed", 60), ("open", 120), ("closed", 150)],
    ),
    (
        T_INC_OPEN, INC, QUEUE_RW, _at(17), "ist972.cust@example.org",
        [("customer", 0, True), ("agent", 30, True)], [],
    ),
    (
        C1, CHG, QUEUE_RW, _at(5), "ist972.cust@example.org",
        [("agent", 0, False)], [("closed", 14 * 1440 + 60)],
    ),
    (
        C2, CHG, QUEUE_RW, _at(12), "ist972.cust@example.org",
        [("agent", 0, False)], [("failed", 14 * 1440 + 240)],
    ),
    (C3, CHG, QUEUE_RW, _at(20), None, [("agent", 0, False)], [("open", 60)]),
    (
        T_HID_INC, INC, QUEUE_HIDDEN, _at(3), "ist972.cust@example.org",
        [("customer", 0, True), ("agent", 30, True)], [("closed", 30)],
    ),
    (C_HID, CHG, QUEUE_HIDDEN, _at(6), None, [("agent", 0, False)], [("open", 60)]),
    (
        T_Q2, INC, QUEUE_Q2, datetime(2024, 5, 20, 8, 0), "ist972.cust@example.org",
        [("customer", 0, True)], [],
    ),
]  # fmt: skip

# ticket id, change type, phase, outcome, risk level, risk override, planned_end, actual_start,
# actual_end
_CHANGE_SPEC: list[tuple[Any, ...]] = [
    (C1, "normal", "closed", "successful", "medium", None, _at(20, 10), _at(17, 9), _at(19, 8)),
    (C2, "emergency", "closed", "backed_out", "high", None, _at(25, 10), _at(13, 8), _at(26, 8)),
    (C3, "normal", "rejected", None, "low", "very_high", _at(30, 10), None, None),
    (C_HID, "normal", "cancelled", None, "low", None, None, None, None),
]  # fmt: skip


def _creation(tid: int, at: datetime) -> list[tuple[Any, ...]]:
    """What ``create_change`` writes: ``created`` plus a ``phase`` row NULL -> draft."""
    return [(tid, "created", None, "draft", at), (tid, "phase", None, "draft", at)]


# ticket id, event, from phase, to phase, created_at
_EVENTS: list[tuple[Any, ...]] = [
    *_creation(C1, _at(5)),
    (C1, "phase", "draft", "submitted", _at(5, 9)),
    (C1, "phase", "submitted", "assessment", _at(5, 10)),
    (C1, "phase", "assessment", "approval", _at(6, 9)),
    (C1, "approval_outcome", None, None, _at(8, 8, 55)),
    (C1, "phase", "approval", "rejected", _at(8, 9)),
    (C1, "phase", "rejected", "draft", _at(9, 9)),
    (C1, "phase", "draft", "submitted", _at(9, 10)),
    (C1, "phase", "submitted", "assessment", _at(9, 11)),
    (C1, "phase", "assessment", "approval", _at(10, 9)),
    (C1, "approval_outcome", None, None, _at(11, 8, 55)),
    (C1, "phase", "approval", "scheduled", _at(11, 9)),
    (C1, "phase", "scheduled", "implementing", _at(17, 9)),
    (C1, "phase", "implementing", "review", _at(19, 8)),
    (C1, "phase", "review", "closed", _at(19, 9)),
    *_creation(C2, _at(12)),
    (C2, "forced", None, None, _at(12, 9)),
    (C2, "phase", "draft", "approval", _at(12, 9)),
    (C2, "approval_outcome", None, None, _at(12, 9, 55)),
    (C2, "phase", "approval", "scheduled", _at(12, 10)),
    (C2, "phase", "scheduled", "implementing", _at(13, 8)),
    (C2, "phase", "implementing", "review", _at(26, 8)),
    (C2, "phase", "review", "closed", _at(26, 12)),
    *_creation(C3, _at(20)),
    (C3, "phase", "draft", "submitted", _at(20, 9)),
    (C3, "phase", "submitted", "assessment", _at(20, 10)),
    (C3, "phase", "assessment", "approval", _at(21, 9)),
    (C3, "approval_outcome", None, None, _at(21, 15, 55)),
    (C3, "phase", "approval", "rejected", _at(21, 16)),
    # foreign: rejected on the same day as C1, so a leak would show as a count of 2
    *_creation(C_HID, _at(6)),
    (C_HID, "phase", "draft", "submitted", _at(6, 9)),
    (C_HID, "phase", "submitted", "assessment", _at(6, 10)),
    (C_HID, "phase", "assessment", "approval", _at(7, 9)),
    (C_HID, "phase", "approval", "rejected", _at(8, 10)),
    (C_HID, "phase", "rejected", "draft", _at(8, 11)),
    (C_HID, "phase", "draft", "cancelled", _at(9, 9)),
]  # fmt: skip

# source kind, source key, target ticket, link type; CIs are named, resolved to ids per test
_LINKS: list[tuple[str, str, int, str]] = [
    ("ci", DB, T_INC_FLS, "RelevantTo"),
    ("ci", DB, T_INC_LONG, "RelevantTo"),
    ("ci", DB, T_INC_OPEN, "DependsOn"),
    ("ci", WEB, T_INC_OPEN, "RelevantTo"),
    ("ci", WEB, T_INC_REOPEN, "RelevantTo"),
    ("ci", CORE, T_REQ_FLS, "RelevantTo"),
    # the same pair through two link types and in both directions counts once
    ("ci", DB, C1, "RelevantTo"),
    ("ticket", DB, C1, "DependsOn"),
    ("ci", WEB, C1, "RelevantTo"),
    ("ci", DB, C3, "RelevantTo"),
    ("ci", CORE, C2, "RelevantTo"),
    ("ci", CORE, T_Q2, "RelevantTo"),
    # a CI of a class nobody here reads, linked to readable tickets
    ("ci", SEC, T_INC_OPEN, "RelevantTo"),
    ("ci", SEC, C2, "RelevantTo"),
    # readable CIs linked to tickets in the foreign queue
    ("ci", DB, T_HID_INC, "RelevantTo"),
    ("ci", DB, C_HID, "RelevantTo"),
]

_TICKET_INSERT = (
    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
    " escalation_update_time, escalation_response_time,"
    " escalation_solution_time, archive_flag,"
    " create_time, create_by, change_time, change_by)"
    " VALUES (:id, :tn, 'ist972 itsm ticket', :q, 1, :ty, :u, 1, 3, :s,"
    " NULL, :cu, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
)
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
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_type WHERE name LIKE 'ist972 %'",
)


def _clean(world: CmdbWorld) -> None:
    for stmt in _CLEANUP:
        world.sql(stmt, lo=LO, hi=HI)


def _scalar(world: CmdbWorld, sql: str, **params: Any) -> int:
    return int(world.sql(sql, **params)[0][0])


def _seed(world: CmdbWorld) -> None:
    sql = world.sql
    h_new = _scalar(world, "SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
    h_state = _scalar(world, "SELECT id FROM ticket_history_type WHERE name = 'StateUpdate'")
    sender = {
        n: _scalar(world, "SELECT id FROM article_sender_type WHERE name = :n", n=n)
        for n in ("customer", "agent")
    }
    channel = _scalar(world, "SELECT id FROM communication_channel WHERE name = 'Email'")
    state = {
        key: _scalar(world, "SELECT id FROM ticket_state WHERE name = :n", n=name)
        for key, name in (
            ("new", "new"),
            ("open", "open"),
            ("closed", "closed successful"),
            ("failed", "closed unsuccessful"),
        )
    }
    for name in (INC, REQ, CHG):
        sql(
            "INSERT INTO ticket_type (name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:n, 1, :t, 1, :t, 1)",
            n=name,
            t=STAMP,
        )
    types = {
        name: _scalar(world, "SELECT id FROM ticket_type WHERE name = :n", n=name)
        for name in (INC, REQ, CHG)
    }
    sql(
        "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
        " signature_id, follow_up_id, follow_up_lock, valid_id,"
        " create_time, create_by, change_time, change_by)"
        " VALUES (:id, 'ist972-q2', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)",
        id=QUEUE_Q2,
        g=GROUP_RO,
        t=STAMP,
    )
    for tid, type_name, queue, created, customer, articles, history in _TICKET_SPEC:
        final = history[-1][0] if history else "new"
        sql(
            _TICKET_INSERT,
            id=tid,
            tn=f"20240603{tid}",
            q=queue,
            ty=types[type_name],
            u=USER_A,
            s=state[final],
            cu=customer,
            t=created,
        )
        for k, (kind, minutes, visible) in enumerate(articles):
            sql(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tid, :s, :c, :v, :t, :by, :t, :by)",
                id=tid * 10 + k,
                tid=tid,
                s=sender[kind],
                c=channel,
                v=1 if visible else 0,
                t=created + timedelta(minutes=minutes),
                by=USER_A if kind == "agent" else 1,
            )
        previous = "new"
        events = [(h_new, "new", 0), *((h_state, s, m) for s, m in history)]
        for k, (htype, st, minutes) in enumerate(events):
            sql(
                "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :n, :ht, :tid, NULL, :ty, :q, :o, 3, :s, :t, :o, :t, :o)",
                id=tid * 10 + k,
                n=f"%%{tid}%%ist972-q%%3 normal%%new%%{tid}" if k == 0 else f"%%{previous}%%{st}",
                ht=htype,
                tid=tid,
                ty=types[type_name],
                q=queue,
                o=USER_A,
                s=state[st],
                t=created + timedelta(minutes=minutes),
            )
            previous = st
    for tid, ctype, phase, outcome, risk, override, planned_end, a_start, a_end in _CHANGE_SPEC:
        created = next(spec[3] for spec in _TICKET_SPEC if spec[0] == tid)
        sql(
            "INSERT INTO tiqora_itsm_change (ticket_id, change_type, phase, outcome,"
            " risk_level, risk_override_level, planned_end, actual_start, actual_end,"
            " approval_round, impact_truncated, portal_visible,"
            " created_at, created_by, updated_at, updated_by)"
            " VALUES (:id, :ct, :ph, :oc, :rl, :ro, :pe, :as, :ae, 1, :f, :f, :t, 1, :t, 1)",
            id=tid,
            ct=ctype,
            ph=phase,
            oc=outcome,
            rl=risk,
            ro=override,
            pe=planned_end,
            **{"as": a_start},
            ae=a_end,
            f=False,
            t=created,
        )
    for tid, event, frm, to, at in _EVENTS:
        sql(
            "INSERT INTO tiqora_itsm_change_event (ticket_id, created_at, created_by,"
            " event, from_phase, to_phase) VALUES (:id, :at, 1, :ev, :f, :to)",
            id=tid,
            at=at,
            ev=event,
            f=frm,
            to=to,
        )


async def _seed_async(world: CmdbWorld) -> None:
    async with world.factory() as session:
        for key, value in SETTINGS.items():
            session.add(TiqoraSettings(key=key, value=value))
        await session.commit()
    async with world.factory() as session:
        await rebuild_ticket_facts(session, list(TICKETS), now=BUILT)
        await session.commit()


async def _unseed_async(world: CmdbWorld) -> None:
    async with world.factory() as session:
        await session.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(list(SETTINGS))))
        await session.commit()


@pytest.fixture(scope="module")
def tickets(cmdb_db: CmdbWorld) -> Iterator[None]:
    _clean(cmdb_db)
    _seed(cmdb_db)
    asyncio.run(_seed_async(cmdb_db))
    yield
    asyncio.run(_unseed_async(cmdb_db))
    _clean(cmdb_db)


@pytest.fixture
async def world(cmdb_db: CmdbWorld, tickets: None) -> Any:
    """Classes, CIs and links: ``_cmdb_rows_reset`` empties them before every test (F8)."""
    set_mode(cmdb_db, "tiqora")
    reset_mode_cache()
    async with cmdb_db.factory() as session:
        srv = await make_class(session, key=SRV, group_id=GROUP_RW)
        net = await make_class(session, key=NET, group_id=GROUP_RO)
        sec = await make_class(session, key=SECRET, group_id=_cmdb_helpers.GROUP_HIDDEN)
        ids = {
            DB: await make_ci(session, srv, DB, deployment="production", incident="operational"),
            WEB: await make_ci(session, srv, WEB, deployment="production", incident="incident"),
            CORE: await make_ci(session, net, CORE, deployment="planned", incident="operational"),
            SEC: await make_ci(session, sec, SEC, deployment="production", incident="operational"),
        }
    for name, day in ((DB, 3), (WEB, 10), (CORE, 10), (SEC, 10)):
        cmdb_db.sql(
            "UPDATE tiqora_cmdb_ci SET created_at = :t WHERE id = :i", t=_at(day), i=ids[name]
        )
    for kind, ci, ticket, link_type in _LINKS:
        if kind == "ci":
            ends = ("ci", str(ids[ci]), "ticket", str(ticket))
        else:
            ends = ("ticket", str(ticket), "ci", str(ids[ci]))
        cmdb_db.sql(
            "INSERT INTO tiqora_itsm_link (source_kind, source_key, target_kind, target_key,"
            " link_type, created_by, created_at) VALUES (:sk, :sv, :tk, :tv, :lt, :u, :t)",
            sk=ends[0],
            sv=ends[1],
            tk=ends[2],
            tv=ends[3],
            lt=link_type,
            u=USER_ADMIN,
            t=STAMP,
        )
    yield cmdb_db
    reset_mode_cache()


# --- running ------------------------------------------------------------------------------


async def _run(
    world: CmdbWorld,
    report: BuiltinReport,
    user: int = USER_A,
    *,
    time_range: TimeRange | None = JUNE,
    **params: Any,
) -> ReportResultOut:
    """One built-in; the June window replaces the relative one where the report has one.

    Parameters go to the reports that declare any (the CI reports have none)."""
    run_params = RunParams(
        time_zone="UTC",
        time_range=time_range if report.definition.time_range is not None else None,
        **(params if report.definition.parameters else {}),
    )
    async with world.factory() as session:
        return await run_report(
            session,
            defn=report.definition,
            params=run_params,
            user_id=user,
            report_key=report.key,
            reporting=ReportingSettings(),
            now=NOW,
            use_cache=False,
        )


async def _run_all(
    world: CmdbWorld, user: int = USER_A, **kwargs: Any
) -> dict[str, ReportResultOut]:
    return {r.name: await _run(world, r, user, **kwargs) for r in itsm_builtin_reports()}


#: rate trends: an empty bucket is ``None``, a real 0 % is kept (``0.0``)
_ZERO_IS_DATA = {"itsm_first_level_trend"}


def _data_rows(res: ReportResultOut, width: int, *, zero_is_data: bool = False) -> list[list[Any]]:
    """Rows with data (zero-filled buckets dropped), dimension ids replaced by their labels."""
    keys = [c.key for c in res.columns[:width]]
    empty: tuple[Any, ...] = (None,) if zero_is_data else (0, None)
    rows = []
    for row in res.rows:
        if all(c is None or (c in empty and not isinstance(c, bool)) for c in row[width:]):
            continue
        shown = [
            res.labels[k][str(v)] if k in res.labels and v is not None else v
            for k, v in zip(keys, row[:width], strict=True)
        ]
        rows.append([*shown, *row[width:]])
    return sorted(rows, key=lambda r: [str(c) for c in r])


def _sorted(rows: list[list[Any]]) -> list[list[Any]]:
    return sorted(rows, key=lambda r: [str(c) for c in r])


def _width(report: BuiltinReport) -> int:
    return len(report.definition.dimensions)


def _shown(report: BuiltinReport, res: ReportResultOut) -> list[list[Any]]:
    return _data_rows(res, _width(report), zero_is_data=report.name in _ZERO_IS_DATA)


I_, R_, C_ = INC, REQ, CHG
DB_CI = "IST972SRV-000001 " + DB
WEB_CI = "IST972SRV-000002 " + WEB
CORE_CI = "IST972NET-000001 " + CORE
#: ticket 740300 of the shared world: open, default type, QUEUE_RW; it has no fact row, but
#: ``open_tickets`` reads the live ticket
WORLD_OPEN = ["Unclassified", PRIO, 1]

#: one exact row list per ITSM built-in, computed by hand from the seeds above (labels, not ids)
EXPECTED_ROWS: dict[str, list[list[Any]]] = {
    # June: incidents 510, 512, 513, 514; request 511; changes 515, 516, 517
    "itsm_tickets_by_type": [[I_, PRIO, 4], [R_, PRIO, 1], [C_, PRIO, 3]],
    "itsm_created_by_type": [
        ["2024-06-03", I_, 1],
        ["2024-06-03", R_, 1],
        ["2024-06-04", I_, 1],
        ["2024-06-05", C_, 1],
        ["2024-06-10", I_, 1],
        ["2024-06-12", C_, 1],
        ["2024-06-17", I_, 1],
        ["2024-06-20", C_, 1],
    ],
    # open now: incidents 514 and 520 (May, queue Q2), change 517, the world's ticket 740300
    "itsm_open_by_type": [WORLD_OPEN, [I_, PRIO, 2], [C_, PRIO, 1]],
    # closed incidents / requests created in June: 510 (first level), 511 (first level), 512, 513
    "itsm_first_level_solution": [[I_, 3, 1, 1 / 3], [R_, 1, 1, 1.0]],
    "itsm_first_level_trend": [
        ["2024-06-03", I_, 1.0],
        ["2024-06-03", R_, 1.0],
        ["2024-06-04", I_, 0.0],
        ["2024-06-10", I_, 0.0],
    ],
    # minutes: incidents 120, 240, 90 (avg 150, median 120); request 60; changes 20220 and 20400
    "itsm_solution_time": [
        [I_, 3, 150.0, 120.0],
        [R_, 1, 60.0, 60.0],
        [C_, 2, 20310.0, 20310.0],
    ],
    "itsm_solution_time_trend": [
        ["2024-06-03", I_, 120.0],
        ["2024-06-03", R_, 60.0],
        ["2024-06-04", I_, 240.0],
        ["2024-06-05", C_, 20220.0],
        ["2024-06-10", I_, 90.0],
        ["2024-06-12", C_, 20400.0],
    ],
    "itsm_tickets_by_role": [
        ["2024-06-03", "incident", 1],
        ["2024-06-03", "service_request", 1],
        ["2024-06-04", "incident", 1],
        ["2024-06-05", "change", 1],
        ["2024-06-10", "incident", 1],
        ["2024-06-12", "change", 1],
        ["2024-06-17", "incident", 1],
        ["2024-06-20", "change", 1],
    ],
    # CIs: two in srv (production), one in net (planned); the sec CI is not readable
    "cmdb_cis_by_class": [[SRV, "Production", 2], [NET, "Planned", 1]],
    "cmdb_cis_created": [
        ["2024-06-03", SRV, 1],
        ["2024-06-10", SRV, 1],
        ["2024-06-10", NET, 1],
    ],
    "itsm_changes_by_status": [
        ["2024-06-05", "closed_successful", 1],
        ["2024-06-12", "closed_backed_out", 1],
        ["2024-06-20", "rejected", 1],
    ],
    # normal: C1 (successful, on time) and C3 (rejected, no outcome yet): rate 1 of 1 decided
    # emergency: C2 backed out (failed), finished a day late
    "itsm_change_outcomes": [
        ["normal", 2, 1, 0, 1.0, 1, 0],
        ["emergency", 1, 0, 1, 0.0, 0, 1],
    ],
    # C1 on 06-08 and C3 on 06-21; the foreign change was rejected on 06-08 too
    "itsm_change_transitions": [
        ["2024-06-08", "rejected", 1],
        ["2024-06-21", "rejected", 1],
    ],
    "itsm_change_requesters": [["ist972.cust@example.org", 2], [None, 1]],
    # srv: C1 (db, web) and C3 (db); net: C2 (core); the sec CI and the foreign change are out
    "itsm_changes_per_ci_class": [[SRV, 2, 2], [NET, 1, 1]],
    # db: incidents 510, 512, 514 (open) + changes 515, 517; web: incidents 513, 514 (open)
    # + change 515; core: request 511 + change 516 (the May incident 520 is outside June)
    "itsm_incident_prone_cis": [
        [DB_CI, 3, 1, 5],
        [WEB_CI, 2, 1, 3],
        [CORE_CI, 0, 0, 2],
    ],
}


async def test_every_itsm_builtin_runs_and_has_exact_values(world: CmdbWorld) -> None:
    """All 16 compile and run on this dialect; every one has exact rows."""
    assert list(EXPECTED_ROWS) == ITSM_EXPECTED
    results = await _run_all(world)
    for r in itsm_builtin_reports():
        res = results[r.name]
        expected_columns = [d.key for d in r.definition.dimensions] + [
            m.key for m in r.definition.measures
        ]
        assert [c.key for c in res.columns] == expected_columns, r.key
        assert [c.role for c in res.columns] == ["dimension"] * _width(r) + ["measure"] * len(
            r.definition.measures
        )
        assert all(len(row) == len(expected_columns) for row in res.rows), r.key
        assert res.report_key == r.key
        got = _shown(r, res)
        want = _sorted(EXPECTED_ROWS[r.name])
        assert len(got) == len(want), (r.key, got)
        for g, w in zip(got, want, strict=True):
            assert g == [pytest.approx(c) if isinstance(c, float) else c for c in w], (r.key, g)


#: what the foreign queue / classes would leak: their names, the secret CI and its class
_FOREIGN_MARKERS = (
    SECRET,
    SEC,
    "vault-01",
    f"cmdb4-queue-{QUEUE_HIDDEN}",
    str(T_HID_INC),
    str(C_HID),
)


async def test_itsm_builtins_hide_foreign_queue_and_class(world: CmdbWorld) -> None:
    """A: no foreign ticket / change / CI anywhere (the exact values already pin the counts).

    B reads only QUEUE_Q2 and class net: no QUEUE_RW ticket, no CI of ist972srv, in any of the
    16 results. A wide window (May and June) makes B's one ticket (created in May) visible.
    """
    for result in (await _run_all(world, USER_A)).values():
        dumped = result.model_dump_json()
        for marker in _FOREIGN_MARKERS:
            assert marker not in dumped, marker
    results = await _run_all(world, USER_B, time_range=MAY_JUNE)
    by_name = {r.name: r for r in itsm_builtin_reports()}
    for name, res in results.items():
        dumped = res.model_dump_json()
        for marker in (*_FOREIGN_MARKERS, SRV, DB, WEB, f"cmdb4-queue-{QUEUE_RW}"):
            assert marker not in dumped, (name, marker)
    expected_b: dict[str, list[list[Any]]] = {
        "itsm_tickets_by_type": [[I_, PRIO, 1]],
        "itsm_created_by_type": [["2024-05-20", I_, 1]],
        "itsm_open_by_type": [[I_, PRIO, 1]],
        "itsm_first_level_solution": [],
        "itsm_first_level_trend": [],
        "itsm_solution_time": [],
        "itsm_solution_time_trend": [],
        "itsm_tickets_by_role": [["2024-05-20", "incident", 1]],
        "cmdb_cis_by_class": [[NET, "Planned", 1]],
        "cmdb_cis_created": [["2024-06-10", NET, 1]],
        "itsm_changes_by_status": [],
        "itsm_change_outcomes": [],
        "itsm_change_transitions": [],
        "itsm_change_requesters": [],
        "itsm_changes_per_ci_class": [],
        # the one May incident is linked to the core switch of class net
        "itsm_incident_prone_cis": [[CORE_CI, 1, 1, 1]],
    }
    assert list(expected_b) == ITSM_EXPECTED
    for name, rows in expected_b.items():
        got = _shown(by_name[name], results[name])
        assert got == _sorted(rows), name


def _narrowed(rows: dict[str, list[list[Any]]], **overrides: list[list[Any]]) -> dict[str, Any]:
    return {**rows, **overrides}


async def _assert_exact(
    world: CmdbWorld, expected: dict[str, list[list[Any]]], **kwargs: Any
) -> None:
    results = await _run_all(world, **kwargs)
    for r in itsm_builtin_reports():
        got = _shown(r, results[r.name])
        want = _sorted(expected[r.name])
        assert len(got) == len(want), (r.key, kwargs, got)
        for g, w in zip(got, want, strict=True):
            assert g == [pytest.approx(c) if isinstance(c, float) else c for c in w], (r.key, g)


async def test_itsm_builtins_with_queues_parameter(world: CmdbWorld) -> None:
    """``queues`` narrows the queue-scoped built-ins; the two ``config_items`` ones ignore it.

    May and June are in the window, so QUEUE_Q2's May incident (972520) is in scope of A.
    """
    cmdb_only = {n for n, rows in EXPECTED_ROWS.items() if n.startswith("cmdb_")}
    every = {r.name: r for r in itsm_builtin_reports()}
    assert {n for n, r in every.items() if r.definition.source == "config_items"} == cmdb_only
    assert all(not every[n].definition.parameters for n in cmdb_only)
    cmdb_rows = {n: EXPECTED_ROWS[n] for n in cmdb_only}
    for name in cmdb_only:  # a parameter they do not declare is refused, never silently applied
        with pytest.raises(ReportCompileError, match="Unbekannter Parameter"):
            async with world.factory() as session:
                await run_report(
                    session,
                    defn=every[name].definition,
                    params=RunParams(time_zone="UTC", parameters={"queues": [QUEUE_RW]}),
                    user_id=USER_A,
                    report_key=None,
                    reporting=ReportingSettings(),
                    now=NOW,
                    use_cache=False,
                )
    # without a parameter the May incident adds to the open and created figures
    wide = await _run_all(world, time_range=MAY_JUNE)
    assert _shown(every["itsm_tickets_by_type"], wide["itsm_tickets_by_type"]) == _sorted(
        [[I_, PRIO, 5], [R_, PRIO, 1], [C_, PRIO, 3]]
    )
    # QUEUE_RW only: the June figures, the May incident is gone
    await _assert_exact(
        world,
        _narrowed(
            EXPECTED_ROWS,
            itsm_open_by_type=[WORLD_OPEN, [I_, PRIO, 1], [C_, PRIO, 1]],
        ),
        time_range=MAY_JUNE,
        parameters={"queues": [QUEUE_RW]},
    )
    # QUEUE_Q2 only: just the May incident and its CI link, nothing of QUEUE_RW
    only_q2: dict[str, list[list[Any]]] = {
        "itsm_tickets_by_type": [[I_, PRIO, 1]],
        "itsm_created_by_type": [["2024-05-20", I_, 1]],
        "itsm_open_by_type": [[I_, PRIO, 1]],
        "itsm_first_level_solution": [],
        "itsm_first_level_trend": [],
        "itsm_solution_time": [],
        "itsm_solution_time_trend": [],
        "itsm_tickets_by_role": [["2024-05-20", "incident", 1]],
        "itsm_changes_by_status": [],
        "itsm_change_outcomes": [],
        "itsm_change_transitions": [],
        "itsm_change_requesters": [],
        "itsm_changes_per_ci_class": [],
        "itsm_incident_prone_cis": [[CORE_CI, 1, 1, 1]],
        **cmdb_rows,
    }
    await _assert_exact(world, only_q2, time_range=MAY_JUNE, parameters={"queues": [QUEUE_Q2]})
    # the foreign queue is no queue of A: nothing, and no leak
    nothing = {n: ([] if n not in cmdb_only else cmdb_rows[n]) for n in EXPECTED_ROWS}
    await _assert_exact(world, nothing, time_range=MAY_JUNE, parameters={"queues": [QUEUE_HIDDEN]})
