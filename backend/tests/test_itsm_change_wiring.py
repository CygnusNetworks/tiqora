"""Wiring of the change module into the rest of Tiqora (Task 21).

Seed band 947800-947899: only the Znuny calendar 947801. The rolled-back ``tests._change_fixtures``
world creates every other row through the services (ids come from the database).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text, update

from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    Q_A,
    SERVICE,
    SERVICE_B,
    U_FULL,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tiqora.api.app import create_app
from tiqora.db.legacy.calendar import CalendarAppointment
from tiqora.db.tiqora.models import TiqoraJob
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.ticket_write_service import change_service, change_title
from tiqora.extensions import Registry
from tiqora.extensions.core import register_core
from tiqora.itsm.change.impact import ITSM_CHANGE_IMPACT_KIND
from tiqora.itsm.change.legacy.archive import ARCHIVE_JOB_KIND
from tiqora.itsm.change.models import TiqoraItsmChange
from tiqora.itsm.change.risk import CHANGE_RISK_CHECKS
from tiqora.itsm.change.service import CHANGE_LISTENERS, ChangeCreateIn, create_change
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION
from tiqora.itsm.change.tasks import ITSM_CHANGE_TASKS_KIND
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.settings_sections.section import SETTINGS_SECTIONS
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

CAL = 947801


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        yield world


async def _change(w: ChangeWorld) -> int:
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
    return view.record.ticket_id


async def _impact_jobs(w: ChangeWorld, ticket_id: int) -> list[TiqoraJob]:
    got = await w.session.execute(
        select(TiqoraJob)
        .where(
            TiqoraJob.kind == ITSM_CHANGE_IMPACT_KIND, TiqoraJob.dedupe_key == f"change:{ticket_id}"
        )
        .order_by(TiqoraJob.id)
    )
    return list(got.scalars())


async def _finish_jobs(w: ChangeWorld, ticket_id: int) -> None:
    await w.session.execute(
        update(TiqoraJob)
        .where(TiqoraJob.dedupe_key == f"change:{ticket_id}")
        .values(status="succeeded")
    )


async def test_service_change_requeues_impact(w: ChangeWorld) -> None:
    tid = await _change(w)
    await _finish_jobs(w, tid)
    before = len(await _impact_jobs(w, tid))
    await change_service(
        w.session,
        ticket_id=tid,
        new_service_id=SERVICE,
        user_id=U_FULL,
        sysconfig=SysConfig(w.session),
    )
    assert len(await _impact_jobs(w, tid)) == before + 1
    # the same service again is a no-op: no further job
    await _finish_jobs(w, tid)
    await change_service(
        w.session,
        ticket_id=tid,
        new_service_id=SERVICE,
        user_id=U_FULL,
        sysconfig=SysConfig(w.session),
    )
    assert len(await _impact_jobs(w, tid)) == before + 1
    await change_service(
        w.session,
        ticket_id=tid,
        new_service_id=SERVICE_B,
        user_id=U_FULL,
        sysconfig=SysConfig(w.session),
    )
    assert len(await _impact_jobs(w, tid)) == before + 2


async def test_service_change_on_plain_ticket_queues_nothing(w: ChangeWorld) -> None:
    tid = await _change(w)
    await w.session.execute(text("DELETE FROM tiqora_itsm_change WHERE ticket_id = :t"), {"t": tid})
    before = len(await _impact_jobs(w, tid))  # the job of the create
    await change_service(
        w.session,
        ticket_id=tid,
        new_service_id=SERVICE,
        user_id=U_FULL,
        sysconfig=SysConfig(w.session),
    )
    assert await w.session.get(TiqoraItsmChange, tid) is None
    assert len(await _impact_jobs(w, tid)) == before


async def test_title_edit_resyncs_calendar_mirror(w: ChangeWorld) -> None:
    from datetime import datetime

    await w.session.execute(
        text(
            "INSERT INTO calendar (id, group_id, name, salt_string, color, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:id, :g, 'chg94-cal', 'salt', '#3b82f6', 1, :t, 1, :t, 1)"
        ),
        {"id": CAL, "g": G_A, "t": datetime(2026, 10, 1)},
    )
    await save_section_values(
        w.session,
        ITSM_CHANGE_SECTION,
        {"calendar_id": CAL},
        secret_key=APP_SETTINGS.secret_key,
    )
    tid = await _change(w)
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.phase = "scheduled"  # fixture shortcut: the mirror is what is tested, not the phase walk
    row.planned_start, row.planned_end = datetime(2026, 11, 2, 8), datetime(2026, 11, 2, 10)
    await w.session.flush()
    from tiqora.itsm.change.calendar_mirror import sync_appointment
    from tiqora.itsm.change.settings import load_change_settings

    cfg = await load_change_settings(w.session, APP_SETTINGS)
    await sync_appointment(w.session, cfg, ticket_id=tid, user_id=U_FULL, base_url="")
    (appt,) = (
        await w.session.execute(
            select(CalendarAppointment).where(CalendarAppointment.calendar_id == CAL)
        )
    ).scalars()
    assert "Switch-Tausch" in appt.title

    await change_title(w.session, ticket_id=tid, new_title="Core-Switch-Tausch", user_id=U_FULL)
    (appt,) = (
        await w.session.execute(
            select(CalendarAppointment).where(CalendarAppointment.calendar_id == CAL)
        )
    ).scalars()
    assert "Core-Switch-Tausch" in appt.title
    assert row.calendar_appointment_id == appt.id


async def test_title_edit_survives_a_failing_resync(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    tid = await _change(w)
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.calendar_appointment_id = 1  # "mirrored", so the resync really runs
    await w.session.flush()

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr("tiqora.itsm.change.calendar_mirror.load_change_settings", boom)
    await change_title(w.session, ticket_id=tid, new_title="Trotzdem gespeichert", user_id=U_FULL)
    got = await w.session.execute(text("SELECT title FROM ticket WHERE id = :t"), {"t": tid})
    assert got.scalar_one() == "Trotzdem gespeichert"
    assert any(r.message == "change_calendar_mirror_failed" for r in caplog.records)
    # the transaction is still usable (PostgreSQL would refuse otherwise)
    assert (await w.session.execute(text("SELECT 1"))).scalar_one() == 1


async def test_title_edit_without_mirror_does_nothing(w: ChangeWorld) -> None:
    tid = await _change(w)
    await change_title(w.session, ticket_id=tid, new_title="Neuer Titel", user_id=U_FULL)
    got = await w.session.execute(text("SELECT title FROM ticket WHERE id = :t"), {"t": tid})
    assert got.scalar_one() == "Neuer Titel"
    assert (await w.session.execute(select(CalendarAppointment.id))).first() is None


def _core() -> Registry:
    registry = Registry()
    register_core(registry)
    return registry


def test_registry_contains_change_items() -> None:
    registry = _core()
    sections = {s.key for s in registry.items(SETTINGS_SECTIONS)}
    assert ITSM_CHANGE_SECTION.key in sections
    kinds = {h.kind for h in registry.items(JOB_HANDLERS)}
    assert {ITSM_CHANGE_TASKS_KIND, ITSM_CHANGE_IMPACT_KIND, ARCHIVE_JOB_KIND} <= kinds
    assert [c.key for c in registry.items(CHANGE_RISK_CHECKS)] == [
        "core.service_criticality",
        "core.ci_incident",
        "core.open_incidents",
        "core.overlap",
        "core.freeze",
        "core.failed_history",
        "core.emergency",
        "core.missing_backout",
    ]
    # the listener point is generic: core registers none (itsm-beyond does later)
    assert CHANGE_LISTENERS.key == "itsm.change_listeners"
    assert registry.items(CHANGE_LISTENERS) == []


@pytest.mark.skip(
    reason="Task 17 (approvals G1): the itsm.change subject type and itsm.service_owner resolver "
    "are registered once the approvals engine is merged; enable there"
)
def test_registry_contains_change_subject_type() -> None:
    pytest.fail("enabled with Task 17")


_CHANGE_PREFIXES = (
    "changes",
    "change-tasks",
    "change-calendar",
    "change-templates",
    "cabs",
    "cab-meetings",
    "freeze-windows",
    "legacy-changes",
)


def test_every_change_route_has_area() -> None:
    for prefix in _CHANGE_PREFIXES:
        assert path_to_area(f"/api/v1/itsm/{prefix}") == "tickets", prefix
        assert path_to_area(f"/api/v1/itsm/{prefix}/1") == "tickets", prefix
    assert path_to_area("/api/v1/tickets/1/change/create-from") == "tickets"
    assert path_to_area("/api/v1/admin/itsm/change/settings") == "admin"
    assert path_to_area("/api/v1/itsm/status") == "cmdb"  # the generic ITSM stays cmdb
    assert path_to_area("/api/portal/change-schedule") is None  # portal is session-based, not keyed


def test_all_registered_change_paths_map_to_an_area() -> None:
    from tiqora.config import Settings

    paths = create_app(Settings(environment="test")).openapi()["paths"]
    seen = 0
    for path in paths:
        if any(f"/api/v1/itsm/{p}" in path for p in _CHANGE_PREFIXES) or (
            "/change/create-from" in path
        ):
            seen += 1
            assert path_to_area(path) == "tickets", path
        elif path.startswith("/api/v1/admin/itsm/change"):
            seen += 1
            assert path_to_area(path) == "admin", path
    assert seen > 20
