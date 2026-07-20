"""DB integration tests for CalendarService: permission-filtered calendars,
appointment CRUD, recurrence expansion, ticket linking, and ICS export.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.calendar.ics import build_ical
from tiqora.calendar.schemas import AppointmentIn, AppointmentUpdateIn, RecurrenceIn
from tiqora.calendar.service import AppointmentNotFound, CalendarForbidden, CalendarService

NOW = datetime(2026, 1, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _seed(sync_url: str) -> dict[str, Any]:
    ns = uuid.uuid4().hex[:8]
    base = int(ns, 16) % 1_000_000 * 1000

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        # Two agents: one with rw on the allowed group, one with nothing.
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :login, 'x', 'Cal', 'Agent', 1, :t, 1, :t, 1),"
                " (:uid2, :login2, 'x', 'No', 'Access', 1, :t, 1, :t, 1)"
            ),
            {
                "uid": base + 1,
                "login": f"cal.agent.{ns}",
                "uid2": base + 2,
                "login2": f"cal.denied.{ns}",
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:g1, :n1, 1, :t, 1, :t, 1), (:g2, :n2, 1, :t, 1, :t, 1)"
            ),
            {
                "g1": base + 10,
                "n1": f"cal-allowed-{ns}",
                "g2": base + 11,
                "n2": f"cal-denied-{ns}",
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'rw', :t, 1, :t, 1)"
            ),
            {"uid": base + 1, "gid": base + 10, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO calendar (id, group_id, name, salt_string, color, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:c1, :g1, :n1, 'salt-abc', '#3b82f6', 1, :t, 1, :t, 1),"
                " (:c2, :g2, :n2, 'salt-xyz', '#ef4444', 1, :t, 1, :t, 1)"
            ),
            {
                "c1": base + 20,
                "g1": base + 10,
                "n1": f"Allowed Calendar {ns}",
                "c2": base + 21,
                "g2": base + 11,
                "n2": f"Denied Calendar {ns}",
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q1, :qn1, :g1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q1": base + 30, "qn1": f"CalQueue-{ns}", "g1": base + 10, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " timeout, until_time, escalation_time, escalation_update_time,"
                " escalation_response_time, escalation_solution_time, archive_flag,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :tn, 'Calendar test ticket', :qid, 1, 1, 1, 1, 3, 4,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"tid": base + 40, "tn": f"{base + 40}", "qid": base + 30, "t": NOW},
        )
    engine.dispose()
    return {
        "agent_id": base + 1,
        "denied_agent_id": base + 2,
        "agent_login": f"cal.agent.{ns}",
        "allowed_calendar_id": base + 20,
        "denied_calendar_id": base + 21,
        "ticket_id": base + 40,
    }


async def _session_factory(mariadb_znuny_url: str) -> Any:
    async_url = _mysql_async(mariadb_znuny_url)
    engine = create_async_engine(async_url)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.db
@pytest.mark.asyncio
async def test_list_calendars_permission_filtered(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)
    async with factory() as session:
        svc = CalendarService(session)
        cals = await svc.list_calendars(ids["agent_id"])
    await engine.dispose()

    cal_ids = {c.id for c in cals}
    assert ids["allowed_calendar_id"] in cal_ids
    assert ids["denied_calendar_id"] not in cal_ids


@pytest.mark.db
@pytest.mark.asyncio
async def test_appointment_crud_and_forbidden(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)

    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            appt = await svc.create_appointment(
                ids["agent_id"],
                AppointmentIn(
                    calendar_id=ids["allowed_calendar_id"],
                    title="Kickoff",
                    description="Project kickoff",
                    location="HQ",
                    start_time=NOW,
                    end_time=NOW + timedelta(hours=1),
                ),
            )
        appt_id = appt.id
        assert appt.unique_id

    async with factory() as session:
        svc = CalendarService(session)
        got = await svc.get_appointment(appt_id, ids["agent_id"])
        assert got.title == "Kickoff"

        with pytest.raises(CalendarForbidden):
            await svc.get_appointment(appt_id, ids["denied_agent_id"])

    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            updated = await svc.update_appointment(
                ids["agent_id"], appt_id, AppointmentUpdateIn(title="Kickoff (rescheduled)")
            )
        assert updated.title == "Kickoff (rescheduled)"

        with pytest.raises(CalendarForbidden):
            async with session.begin():
                await svc.update_appointment(
                    ids["denied_agent_id"], appt_id, AppointmentUpdateIn(title="Hijack")
                )

    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            await svc.delete_appointment(ids["agent_id"], appt_id)
        with pytest.raises(AppointmentNotFound):
            await svc.get_appointment(appt_id, ids["agent_id"])

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_recurring_appointment_expansion_and_occurrence_delete(
    mariadb_znuny_url: str,
) -> None:
    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)

    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            appt = await svc.create_appointment(
                ids["agent_id"],
                AppointmentIn(
                    calendar_id=ids["allowed_calendar_id"],
                    title="Daily standup",
                    start_time=NOW,
                    end_time=NOW + timedelta(minutes=15),
                    recurrence=RecurrenceIn(type="Daily", interval=1, count=5),
                ),
            )
        appt_id = appt.id

    async with factory() as session:
        svc = CalendarService(session)
        occs = await svc.list_occurrences(
            ids["agent_id"], range_start=NOW, range_end=NOW + timedelta(days=10)
        )
        assert len(occs) == 5

    # Delete a single occurrence (the third day).
    third_day = NOW + timedelta(days=2)
    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            await svc.delete_appointment(ids["agent_id"], appt_id, occurrence=third_day)

    async with factory() as session:
        svc = CalendarService(session)
        occs = await svc.list_occurrences(
            ids["agent_id"], range_start=NOW, range_end=NOW + timedelta(days=10)
        )
        assert len(occs) == 4
        assert all(o.start_time.day != third_day.day for o in occs)

        # Permission filtering also applies to occurrence expansion.
        occs_denied = await svc.list_occurrences(
            ids["denied_agent_id"], range_start=NOW, range_end=NOW + timedelta(days=10)
        )
        assert occs_denied == []

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_ticket_linking(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)

    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            appt = await svc.create_appointment(
                ids["agent_id"],
                AppointmentIn(
                    calendar_id=ids["allowed_calendar_id"],
                    title="Escalation review",
                    start_time=NOW,
                    end_time=NOW + timedelta(hours=1),
                ),
            )
        appt_id = appt.id

    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            await svc.link_ticket(ids["agent_id"], appt_id, ids["ticket_id"])

    async with factory() as session:
        svc = CalendarService(session)
        links = await svc.list_ticket_links(appt_id)
        assert len(links) == 1
        assert links[0].ticket_id == ids["ticket_id"]
        assert links[0].rule_id == "manual"

    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            await svc.unlink_ticket(ids["agent_id"], appt_id, ids["ticket_id"])

    async with factory() as session:
        svc = CalendarService(session)
        links = await svc.list_ticket_links(appt_id)
        assert links == []

    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_ics_export_and_feed_token(mariadb_znuny_url: str) -> None:
    ids = _seed(mariadb_znuny_url)
    engine, factory = await _session_factory(mariadb_znuny_url)

    async with factory() as session:
        svc = CalendarService(session)
        async with session.begin():
            await svc.create_appointment(
                ids["agent_id"],
                AppointmentIn(
                    calendar_id=ids["allowed_calendar_id"],
                    title="Board meeting",
                    start_time=NOW,
                    end_time=NOW + timedelta(hours=2),
                    recurrence=RecurrenceIn(
                        type="Weekly", interval=1, until=NOW + timedelta(days=60)
                    ),
                ),
            )

    async with factory() as session:
        svc = CalendarService(session)
        cal, appts = await svc.export_appointments(ids["allowed_calendar_id"])
        ical = build_ical(cal, appts)
        assert "BEGIN:VCALENDAR" in ical
        assert "SUMMARY:Board meeting" in ical
        assert "RRULE:FREQ=WEEKLY" in ical

        token = await svc.feed_token(ids["allowed_calendar_id"], ids["agent_id"])
        assert await svc.verify_feed_token(ids["allowed_calendar_id"], ids["agent_login"], token)
        assert not await svc.verify_feed_token(
            ids["allowed_calendar_id"], ids["agent_login"], "wrong"
        )

    await engine.dispose()
