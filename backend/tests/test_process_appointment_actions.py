"""Process transition actions ``AppointmentCreate`` / ``AppointmentUpdate`` / ``AppointmentRemove``.

Both dialects. Seed band 891000-891999: group 891001, users 891010 (rw) / 891011 (no permission)
/ 891012 (create only), calendar 891020 (group 891001), DF 891030 ``wf3AppointmentID`` (Text),
tickets 891040 (direct action calls) and 891041 (transition), process 891100.
Znuny references: ``AppointmentCreate.pm``, ``AppointmentUpdate.pm``, ``AppointmentRemove.pm``,
``Calendar/Appointment.pm:1548-1560`` (delete needs create or rw).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.deployed import clear_revision_cache
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action, run_transitions
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2026, 3, 4, 5, 6, 7)
MODULE_PREFIX = "Kernel::System::ProcessManagement::TransitionAction::"
GROUP = 891001
RW_USER, NO_USER, CREATE_USER = 891010, 891011, 891012
CALENDAR = 891020
CAL_NAME = "wf3 Calendar"
DF_ID = 891030
DF_NAME = "wf3AppointmentID"
TICKET, TICKET_TRANSITION = 891040, 891041
PROCESS_ROW, PROCESS_EID = 891100, "Process-wf3891100"
ACT_A, ACT_B = "Activity-wf3891101", "Activity-wf3891102"
TRANS, TA_FAIL, TA_STATE = "Transition-wf3891103", "TA-wf3891104", "TA-wf3891105"

_CLEANUP = (
    "DELETE FROM calendar_appointment_ticket WHERE ticket_id BETWEEN 891000 AND 891999",
    "DELETE FROM calendar_appointment WHERE calendar_id = 891020",
    "DELETE FROM calendar WHERE id = 891020",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 891000 AND 891999",
    "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 891000 AND 891999",
    "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 891000 AND 891999",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN 891000 AND 891999",
    "DELETE FROM ticket WHERE id BETWEEN 891000 AND 891999",
    "DELETE FROM dynamic_field WHERE id = 891030",
    "DELETE FROM pm_process WHERE id BETWEEN 891100 AND 891199",
    "DELETE FROM pm_activity WHERE id BETWEEN 891100 AND 891199",
    "DELETE FROM pm_transition WHERE id BETWEEN 891100 AND 891199",
    "DELETE FROM pm_transition_action WHERE id BETWEEN 891100 AND 891199",
    "DELETE FROM group_user WHERE group_id = 891001",
    "DELETE FROM permission_groups WHERE id = 891001",
    "DELETE FROM users WHERE id BETWEEN 891010 AND 891019",
)
_URLS: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _run(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    engine.dispose()


_PM = (
    "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
    " change_by) VALUES (:id, :eid, :name, :cfg, current_timestamp, 1, current_timestamp, 1)"
)


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for uid, login in (
            (RW_USER, "wf3.cal.rw"),
            (NO_USER, "wf3.cal.none"),
            (CREATE_USER, "wf3.cal.create"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', 'Wf3', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'wf3-cal-group', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": T0},
        )
        for uid, key in ((RW_USER, "rw"), (CREATE_USER, "create")):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": GROUP, "k": key, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO calendar (id, group_id, name, salt_string, color, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :g, :n, 'salt-wf3', '#3b82f6', 1, :t, 1, :t, 1)"
            ),
            {"id": CALENDAR, "g": GROUP, "n": CAL_NAME, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:id, 0, :n, :n, :id, 'Text', 'Ticket', '---', 1, :t, 1, :t, 1)"
            ),
            {"id": DF_ID, "n": DF_NAME, "t": T0},
        )
        for tid, title in ((TICKET, "wf3 direct"), (TICKET_TRANSITION, "wf3 transition")):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, 1, 1, 1, 1, 1, 3, 4, 0, 0, 0, 0, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2026110{tid}", "title": title, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
                " create_time, create_by, change_time, change_by) VALUES (:id, :eid, 'wf3',"
                " 'S1', '{}', :cfg, current_timestamp, 1, current_timestamp, 1)"
            ),
            {
                "id": PROCESS_ROW,
                "eid": PROCESS_EID,
                "cfg": (
                    f"StartActivity: {ACT_A}\nStartActivityDialog: ''\nPath:\n  {ACT_A}:\n"
                    f"    {TRANS}:\n      ActivityEntityID: {ACT_B}\n      TransitionAction:\n"
                    f"      - {TA_FAIL}\n      - {TA_STATE}\n  {ACT_B}: {{}}\n"
                ),
            },
        )
        for row, eid in ((1, ACT_A), (2, ACT_B)):
            conn.execute(
                text(_PM.format(table="pm_activity")),
                {"id": PROCESS_ROW + row, "eid": eid, "name": eid, "cfg": "ActivityDialog: {}\n"},
            )
        conn.execute(
            text(_PM.format(table="pm_transition")),
            {
                "id": PROCESS_ROW + 3,
                "eid": TRANS,
                "name": "wf3 T",
                "cfg": (
                    "Condition:\n  '1':\n    Fields:\n      Title:\n"
                    "        Match: wf3 transition\n        Type: String\n    Type: and\n"
                    "  Type: and\nConditionLinking: and\n"
                ),
            },
        )
        for row, eid, module, cfg in (
            (
                4,
                TA_FAIL,
                "AppointmentCreate",
                "Config:\n  CalendarName: No such calendar wf3\n  Title: T\n"
                "  StartTime: '2026-11-02 09:00:00'\n  EndTime: '2026-11-02 10:00:00'\n",
            ),
            (5, TA_STATE, "TicketStateSet", "Config:\n  State: closed successful\n"),
        ):
            conn.execute(
                text(_PM.format(table="pm_transition_action")),
                {
                    "id": PROCESS_ROW + row,
                    "eid": eid,
                    "name": module,
                    "cfg": f"{cfg}Module: {MODULE_PREFIX}{module}\n",
                },
            )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run(url, _CLEANUP)
    _URLS.clear()
    clear_revision_cache()


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]
    sync_url: str
    sysconfig: SysConfig

    async def run(
        self, module: str, config: dict[str, Any], *, user: int = RW_USER, ticket: int = TICKET
    ) -> str | None:
        async with self.factory() as session, session.begin():
            return await execute_transition_action(
                session,
                action=TransitionActionConfig(module=MODULE_PREFIX + module, config=config),
                ticket_id=ticket,
                process_entity_id="P",
                activity_entity_id="A",
                transition_entity_id="T",
                user_id=user,
                sysconfig=self.sysconfig,
            )

    async def appointments(self) -> list[Any]:
        async with self.factory() as session:
            rows = await session.execute(
                text(
                    "SELECT id, title, description, location, start_time, end_time, all_day,"
                    " team_id, resource_id, recurring, recur_type, create_by"
                    " FROM calendar_appointment WHERE calendar_id = :c ORDER BY id"
                ),
                {"c": CALENDAR},
            )
            return list(rows.mappings().all())

    async def df_value(self, ticket: int = TICKET) -> str | None:
        async with self.factory() as session:
            return (
                await session.execute(
                    text(
                        "SELECT value_text FROM dynamic_field_value"
                        " WHERE field_id = :f AND object_id = :o"
                    ),
                    {"f": DF_ID, "o": ticket},
                )
            ).scalar_one_or_none()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    clear_revision_cache()
    _run(sync_url, _CLEANUP)
    _seed(sync_url)

    async def _fetch(name: str) -> Any:
        return "Europe/Berlin" if name == "OTRSTimeZone" else None

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield Env(factory, sync_url, SysConfig(fetch=_fetch))
    finally:
        await engine.dispose()
        _run(sync_url, _CLEANUP)
        clear_revision_cache()


_CREATE = {
    "CalendarName": CAL_NAME,
    "Title": "Webinar",
    "StartTime": "2026-11-02 09:00:00",
    "EndTime": "2026-11-02 10:30",
    "DynamicField_AppointmentID": DF_NAME,
}


@pytest.mark.db
async def test_create_with_calendar_name_times_in_system_zone_and_df(env: Env) -> None:
    # OTRSTimeZone Europe/Berlin: 09:00 CET on 2026-11-02 is 08:00 UTC.
    assert await env.run("AppointmentCreate", {**_CREATE, "Location": "Berlin"}) is None
    rows = await env.appointments()
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Webinar"
    assert row["location"] == "Berlin"
    assert row["start_time"] == datetime(2026, 11, 2, 8, 0, 0)
    assert row["end_time"] == datetime(2026, 11, 2, 9, 30, 0)
    assert row["create_by"] == RW_USER
    assert await env.df_value() == str(row["id"])


@pytest.mark.db
async def test_create_needs_no_permission_like_znuny(env: Env) -> None:
    await env.run("AppointmentCreate", {**_CREATE, "DynamicField_AppointmentID": ""}, user=NO_USER)
    rows = await env.appointments()
    assert len(rows) == 1
    assert rows[0]["create_by"] == NO_USER
    assert await env.df_value() is None


@pytest.mark.db
async def test_update_requires_rw_and_keeps_unset_fields(env: Env) -> None:
    await env.run("AppointmentCreate", {**_CREATE, "Location": "Berlin", "Description": "D"})
    appointment_id = (await env.appointments())[0]["id"]

    await env.run(
        "AppointmentUpdate", {"AppointmentID": appointment_id, "Title": "Hacked"}, user=NO_USER
    )
    assert (await env.appointments())[0]["title"] == "Webinar"

    await env.run(
        "AppointmentUpdate",
        {"AppointmentID": str(appointment_id), "Title": "Renamed", "EndTime": "2026-11-02 11:00"},
        user=RW_USER,
    )
    row = (await env.appointments())[0]
    assert row["title"] == "Renamed"
    assert row["end_time"] == datetime(2026, 11, 2, 10, 0, 0)
    # keys that were not configured are untouched
    assert row["start_time"] == datetime(2026, 11, 2, 8, 0, 0)
    assert row["location"] == "Berlin"
    assert row["description"] == "D"


@pytest.mark.db
async def test_remove_requires_create_or_rw(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    await env.run("AppointmentCreate", {**_CREATE, "DynamicField_AppointmentID": ""})
    await env.run("AppointmentCreate", {**_CREATE, "DynamicField_AppointmentID": ""})
    first, second = [r["id"] for r in await env.appointments()]

    with caplog.at_level(logging.WARNING):
        await env.run("AppointmentRemove", {"AppointmentID": first}, user=NO_USER)
    assert len(await env.appointments()) == 2
    assert "process_action_appointment_skipped" in caplog.text
    assert "AppointmentRemove" in caplog.text

    # create alone is enough for AppointmentDelete (Appointment.pm:1548-1560) ...
    await env.run("AppointmentRemove", {"AppointmentID": first}, user=CREATE_USER)
    assert [r["id"] for r in await env.appointments()] == [second]
    # ... and so is rw
    await env.run("AppointmentRemove", {"AppointmentID": second}, user=RW_USER)
    assert await env.appointments() == []


@pytest.mark.db
async def test_team_and_resource_comma_lists(env: Env) -> None:
    await env.run(
        "AppointmentCreate",
        {**_CREATE, "TeamID": "1 , 2", "ResourceID": "3,4", "AllDay": "1"},
    )
    row = (await env.appointments())[0]
    assert row["team_id"] == "1,2"
    assert row["resource_id"] == "3,4"
    assert row["all_day"] == 1

    await env.run(
        "AppointmentUpdate",
        {"AppointmentID": row["id"], "TeamID": "5, 6", "AllDay": "0"},
    )
    updated = (await env.appointments())[0]
    assert updated["team_id"] == "5,6"
    assert updated["resource_id"] == "3,4"
    assert updated["all_day"] == 0


@pytest.mark.db
async def test_supported_recurrence_is_stored(env: Env) -> None:
    await env.run(
        "AppointmentCreate",
        {
            **_CREATE,
            "Recurring": "1",
            "RecurrenceType": "Weekly",
            "RecurrenceInterval": "2",
            "RecurrenceCount": "5",
        },
    )
    row = (await env.appointments())[0]
    assert row["recurring"] == 1
    assert row["recur_type"] == "Weekly"


@pytest.mark.db
async def test_unsupported_recurrence_keys_are_ignored_with_warning(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        await env.run(
            "AppointmentCreate",
            {
                **_CREATE,
                "Recurring": "1",
                "RecurrenceType": "CustomWeekly",
                "RecurrenceFrequency": "1,3",
                "NotificationTemplate": "Start",
            },
        )
    rows = await env.appointments()
    assert len(rows) == 1  # the appointment itself is still created
    assert not rows[0]["recurring"]
    assert rows[0]["recur_type"] is None
    assert "CustomWeekly" in caplog.text
    assert "RecurrenceFrequency" in caplog.text
    assert "NotificationTemplate" in caplog.text


@pytest.mark.db
async def test_create_skips_with_reason_on_bad_input(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    bad = (
        {**_CREATE, "CalendarName": "No such calendar wf3"},
        {k: v for k, v in _CREATE.items() if k != "Title"},
        {**_CREATE, "StartTime": "next tuesday"},
    )
    for config in bad:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert await env.run("AppointmentCreate", config) is None
        assert "process_action_appointment_skipped" in caplog.text
    assert await env.appointments() == []
    assert await env.df_value() is None


@pytest.mark.db
async def test_failing_appointment_action_does_not_stop_transition(env: Env) -> None:
    async with env.factory() as session, session.begin():
        for name, value in (
            ("ProcessManagementProcessID", PROCESS_EID),
            ("ProcessManagementActivityID", ACT_A),
        ):
            await session.execute(
                text(
                    "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                    " SELECT id, :o, :v FROM dynamic_field WHERE name = :n"
                ),
                {"o": TICKET_TRANSITION, "v": value, "n": name},
            )
    async with env.factory() as session, session.begin():
        outcome = await run_transitions(
            session, ticket_id=TICKET_TRANSITION, user_id=RW_USER, sysconfig=env.sysconfig
        )
    assert outcome.activity_changed is True
    assert outcome.new_activity_entity_id == ACT_B
    assert outcome.unsupported_actions == []
    async with env.factory() as session:
        state = (
            await session.execute(
                text(
                    "SELECT s.name FROM ticket t JOIN ticket_state s ON s.id = t.ticket_state_id"
                    " WHERE t.id = :t"
                ),
                {"t": TICKET_TRANSITION},
            )
        ).scalar_one()
    assert state == "closed successful"
    assert await env.appointments() == []


@pytest.mark.db
async def test_update_via_smart_tag_from_df(env: Env) -> None:
    await env.run("AppointmentCreate", _CREATE)
    created = (await env.appointments())[0]["id"]
    assert await env.df_value() == str(created)

    await env.run(
        "AppointmentUpdate",
        {
            "AppointmentID": f"<OTRS_TICKET_DynamicField_{DF_NAME}>",
            "Title": "Updated by tag",
        },
    )
    rows = await env.appointments()
    assert [(r["id"], r["title"]) for r in rows] == [(created, "Updated by tag")]
