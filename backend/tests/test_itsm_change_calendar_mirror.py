"""Znuny calendar mirror of scheduled changes (I15).

Seed band 947400-947499: the Znuny calendar 947401 (rows live in the rolled-back world of
``tests._change_fixtures``; nothing is committed). Change tickets are created by the services.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select, text

from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    Q_A,
    U_FULL,
    URL_FIXTURES,
    ChangeWorld,
    FakeApprovalPort,
    change_world,
)
from tests._itsm_mode_helpers import store  # noqa: F401  (fixture helpers share the module)
from tiqora.db.legacy.calendar import CalendarAppointment
from tiqora.db.legacy.ticket import Ticket
from tiqora.itsm.change.approval_port import set_approval_port_for_tests
from tiqora.itsm.change.calendar_mirror import sync_appointment
from tiqora.itsm.change.models import TiqoraItsmChange
from tiqora.itsm.change.service import ChangeCreateIn, ChangeUpdateIn, create_change, update_change
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION, load_change_settings
from tiqora.itsm.change.transitions import TransitionIn, transition
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

CAL = 947401
CAL_MISSING = 947499
WINDOW = (datetime(2026, 11, 2, 8, 0, 0), datetime(2026, 11, 2, 10, 0, 0))
WINDOW_B = (datetime(2026, 11, 3, 8, 0, 0), datetime(2026, 11, 3, 9, 0, 0))
_T = datetime(2026, 10, 1)


@pytest.fixture
def port() -> Iterator[FakeApprovalPort]:
    fake = FakeApprovalPort()
    set_approval_port_for_tests(fake)
    try:
        yield fake
    finally:
        set_approval_port_for_tests(None)


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        await world.session.execute(
            text(
                "INSERT INTO calendar (id, group_id, name, salt_string, color, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :g, 'chg94-cal', 'salt', '#3b82f6', 1, :t, 1, :t, 1)"
            ),
            {"id": CAL, "g": G_A, "t": _T},
        )
        yield world


async def _cfg(w: ChangeWorld, calendar_id: int) -> Any:
    await save_section_values(
        w.session,
        ITSM_CHANGE_SECTION,
        {"calendar_id": calendar_id},
        secret_key=APP_SETTINGS.secret_key,
    )
    return await load_change_settings(w.session, APP_SETTINGS)


async def _change(w: ChangeWorld, phase: str, window: Any = WINDOW) -> int:
    view, _ = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=ChangeCreateIn.model_validate(
            {"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A}
        ),
        user_id=U_FULL,
        session_factory=w.factory,
    )
    tid = view.record.ticket_id
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.phase = phase
    row.planned_start, row.planned_end = window if window else (None, None)
    await w.session.flush()
    return tid


async def _row(w: ChangeWorld, tid: int) -> TiqoraItsmChange:
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    return row


async def _appointments(w: ChangeWorld) -> list[CalendarAppointment]:
    stmt = select(CalendarAppointment).where(CalendarAppointment.calendar_id == CAL)
    return list((await w.session.execute(stmt.order_by(CalendarAppointment.id))).scalars())


async def _sync(w: ChangeWorld, cfg: Any, tid: int, base_url: str = "") -> None:
    await sync_appointment(w.session, cfg, ticket_id=tid, user_id=U_FULL, base_url=base_url)


async def test_mirror_off_by_default(w: ChangeWorld) -> None:
    cfg = await load_change_settings(w.session, APP_SETTINGS)
    assert cfg.calendar_id == 0
    tid = await _change(w, "scheduled")
    await _sync(w, cfg, tid)
    assert await _appointments(w) == []
    assert (await _row(w, tid)).calendar_appointment_id is None


async def test_scheduled_creates_appointment_like_znuny(w: ChangeWorld) -> None:
    cfg = await _cfg(w, CAL)
    tid = await _change(w, "scheduled")
    await _sync(w, cfg, tid, base_url="https://tiqora.example.org/")
    (appt,) = await _appointments(w)
    ticket = await w.session.get(Ticket, tid)
    assert ticket is not None
    assert (await _row(w, tid)).calendar_appointment_id == appt.id
    assert appt.title == f"Change {ticket.tn}: Switch-Tausch"
    assert appt.description == f"Tiqora: https://tiqora.example.org/tickets/{tid}"
    assert (appt.start_time, appt.end_time) == WINDOW
    # the fields Znuny's AppointmentCreate stores for a plain appointment
    assert (appt.all_day, appt.recur_interval, appt.recur_type) == (0, 1, None)
    assert appt.notify_template == "" and appt.notify_custom_unit_count == 0
    assert appt.unique_id.endswith("@tiqora") and appt.create_by == U_FULL
    # nothing changed: a second sync keeps the very same row
    await _sync(w, cfg, tid, base_url="https://tiqora.example.org/")
    assert [a.id for a in await _appointments(w)] == [appt.id]


async def test_window_change_recreates(w: ChangeWorld) -> None:
    cfg = await _cfg(w, CAL)
    tid = await _change(w, "scheduled")
    await _sync(w, cfg, tid)
    (old,) = await _appointments(w)
    await update_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=tid,
        data=ChangeUpdateIn.model_validate(
            {"planned_start": WINDOW_B[0], "planned_end": WINDOW_B[1]}
        ),
        user_id=U_FULL,
    )
    (new,) = await _appointments(w)
    assert new.id != old.id
    assert (new.start_time, new.end_time) == WINDOW_B
    assert (await _row(w, tid)).calendar_appointment_id == new.id


async def test_cancel_deletes(w: ChangeWorld, port: FakeApprovalPort) -> None:
    _ = port
    cfg = await _cfg(w, CAL)
    tid = await _change(w, "scheduled")
    await _sync(w, cfg, tid)
    assert len(await _appointments(w)) == 1
    await transition(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=tid,
        data=TransitionIn.model_validate({"to": "cancelled"}),
        user_id=U_FULL,
        session_factory=w.factory,
    )
    assert await _appointments(w) == []
    assert (await _row(w, tid)).calendar_appointment_id is None


async def test_agent_transition_creates(w: ChangeWorld, port: FakeApprovalPort) -> None:
    """The agent path mirrors too: ``scheduled`` -> ``implementing`` creates the appointment."""
    _ = port
    await _cfg(w, CAL)
    tid = await _change(w, "scheduled")
    await transition(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=tid,
        data=TransitionIn.model_validate({"to": "implementing"}),
        user_id=U_FULL,
        session_factory=w.factory,
    )
    (appt,) = await _appointments(w)
    assert (await _row(w, tid)).calendar_appointment_id == appt.id


async def test_missing_calendar_logged_not_failed(
    w: ChangeWorld, caplog: pytest.LogCaptureFixture
) -> None:
    cfg = await _cfg(w, CAL_MISSING)
    tid = await _change(w, "scheduled")
    with caplog.at_level(logging.WARNING):
        await _sync(w, cfg, tid)
    assert any(r.message == "change_calendar_missing" for r in caplog.records)
    assert (await _row(w, tid)).calendar_appointment_id is None
    # a vanished appointment is not an error either: it is recreated
    cfg = await _cfg(w, CAL)
    await _sync(w, cfg, tid)
    (appt,) = await _appointments(w)
    old_id = appt.id
    await w.session.execute(text("DELETE FROM calendar_appointment WHERE id = :i"), {"i": old_id})
    w.session.expire_all()
    await _sync(w, cfg, tid)
    (again,) = await _appointments(w)
    assert again.id != old_id


async def test_no_session_begin_used(w: ChangeWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = await _cfg(w, CAL)
    tid = await _change(w, "scheduled")
    await w.session.execute(text("SELECT 1"))  # the request session is already in autobegin

    def _forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("session.begin() after autobegin")

    monkeypatch.setattr(w.session, "begin", _forbidden)
    await _sync(w, cfg, tid)
    assert len(await _appointments(w)) == 1
