"""The change management seed helpers write the rows ITSMChangeManagement leaves behind.

Both dialects x both profiles (``itsm_db_url``). Nothing is committed: the session of
``itsm_session`` is rolled back, so the strict leak check has nothing to find.
Seed band 946000-946099: users 94600x, changes 94601x, work orders 94602x, CAB rows 94603x,
history rows 94604x, tickets 94605x; logins and titles contain chg94.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text

from tests._itsm_adapter_helpers import itsm_session, seed_user
from tests._itsm_db import ITSM_PARAMS
from tests._itsm_fixtures import (
    CHANGE_LINK_OBJECT,
    CHANGE_PRIORITY_CATALOG,
    CHANGE_STATE_CATALOG,
    CHANGE_TIME_SENTINEL,
    WORKORDER_LINK_OBJECT,
    catalog_id,
    seed_change,
    seed_change_cab,
    seed_change_history,
    seed_change_link,
    seed_workorder,
    znuny_change_times,
)

pytestmark = pytest.mark.db

MANAGER = 946001
AGENT = 946002
MAIL, FIRMWARE = 946010, 946011
MAIL_WOS = (946020, 946021, 946022)
CREATED = datetime(2026, 10, 1, 8, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC

# The SQL of WorkOrderChangeTimeGet (ITSMChangeManagement ITSMWorkOrder.pm).
_MIN_MAX = (
    "SELECT MIN(planned_start_time), MAX(planned_end_time), MIN(actual_start_time),"
    " MAX(actual_end_time) FROM change_workorder WHERE change_id = :c"
)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, 0)  # noqa: DTZ001 - Znuny stores naive UTC


async def _seed_mail_and_firmware(session) -> None:
    await seed_user(session, MANAGER, "chg94.manager", "Erika", "Beispiel")
    await seed_user(session, AGENT, "chg94.agent", "Max", "Mustermann")
    await seed_change(
        session,
        change_id=MAIL,
        number="2026110110000015",
        title="chg94 Mail server replacement",
        state="in progress",
        impact="4 high",
        manager_id=MANAGER,
        builder_id=1,
        created_at=CREATED,
        description="<p>Replace the mail server</p>",
        requested_time=_at(1, 0),
    )
    await seed_workorder(
        session,
        change_id=MAIL,
        workorder_id=MAIL_WOS[0],
        number=1,
        title="chg94 install",
        wo_type="workorder",
        state="closed",
        agent_id=AGENT,
        planned=(_at(2, 8), _at(2, 12)),
        actual=(_at(2, 8, 5), _at(2, 12, 30)),
        planned_effort=4,
        accounted_time=4.5,
    )
    await seed_workorder(
        session,
        change_id=MAIL,
        workorder_id=MAIL_WOS[1],
        number=2,
        title="chg94 backout",
        wo_type="backout",
        state="created",
        planned=(_at(2, 12), _at(2, 14)),
    )
    await seed_workorder(
        session,
        change_id=MAIL,
        workorder_id=MAIL_WOS[2],
        number=3,
        title="chg94 review",
        wo_type="pir",
        state="created",
    )
    await seed_change(
        session,
        change_id=FIRMWARE,
        number="2026110110000024",
        title="chg94 Switch firmware rollout",
        state="requested",
        builder_id=1,
        created_at=CREATED,
    )
    await seed_workorder(
        session,
        change_id=FIRMWARE,
        workorder_id=946023,
        number=1,
        title="chg94 flash",
        wo_type="workorder",
        state="ready",
        planned=(_at(9, 18), _at(9, 20)),
    )
    await seed_workorder(
        session,
        change_id=FIRMWARE,
        workorder_id=946024,
        number=2,
        title="chg94 backout",
        wo_type="backout",
        state="created",
        planned=(_at(9, 20), _at(9, 21)),
    )


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_seed_change_round_trip(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        await _seed_mail_and_firmware(session)

        row = (
            await session.execute(
                text(
                    "SELECT c.change_number, c.title, c.description, c.description_plain,"
                    " c.justification, c.change_manager_id, c.change_builder_id, c.requested_time,"
                    " c.create_time, c.change_state_id, c.category_id, c.impact_id, c.priority_id,"
                    " s.name, cat.name, imp.name, prio.name FROM change_item c"
                    " JOIN general_catalog s ON s.id = c.change_state_id"
                    " JOIN general_catalog cat ON cat.id = c.category_id"
                    " JOIN general_catalog imp ON imp.id = c.impact_id"
                    " JOIN general_catalog prio ON prio.id = c.priority_id WHERE c.id = :c"
                ),
                {"c": MAIL},
            )
        ).one()
        assert row[0] == "2026110110000015" and row[1] == "chg94 Mail server replacement"
        assert row[2] == row[3] == "<p>Replace the mail server</p>"  # plain = text as given
        assert row[4] is None and row[5] == MANAGER and row[6] == 1
        assert row[7] == _at(1, 0) and row[8] == CREATED
        assert row[9] == await catalog_id(session, CHANGE_STATE_CATALOG, "in progress")
        assert row[12] == await catalog_id(session, CHANGE_PRIORITY_CATALOG, "3 normal")
        assert row[13:] == ("in progress", "3 normal", "4 high", "3 normal")

        # the change has no time columns of its own: requested_time is NULL, not the sentinel
        bare = (
            await session.execute(
                text("SELECT requested_time FROM change_item WHERE id = :c"), {"c": FIRMWARE}
            )
        ).scalar_one()
        assert bare is None


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_seeded_workorders_store_the_sentinel_and_derive_the_change_times(
    dialect, profile, request
) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        await _seed_mail_and_firmware(session)
        orders = (
            await session.execute(
                text(
                    "SELECT w.workorder_number, t.name, s.name, w.workorder_agent_id,"
                    " w.planned_start_time, w.planned_end_time, w.actual_start_time,"
                    " w.actual_end_time, w.planned_effort, w.accounted_time FROM change_workorder w"
                    " JOIN general_catalog t ON t.id = w.workorder_type_id"
                    " JOIN general_catalog s ON s.id = w.workorder_state_id"
                    " WHERE w.change_id = :c ORDER BY w.workorder_number"
                ),
                {"c": MAIL},
            )
        ).all()
        assert [(o[0], o[1], o[2]) for o in orders] == [
            (1, "workorder", "closed"),
            (2, "backout", "created"),
            (3, "pir", "created"),
        ]
        assert orders[0][3] == AGENT and orders[1][3] is None
        assert orders[0][4:8] == (_at(2, 8), _at(2, 12), _at(2, 8, 5), _at(2, 12, 30))
        assert float(orders[0][8]) == 4.0 and float(orders[0][9]) == 4.5
        # no times at all: Znuny's sentinel in all four columns, never NULL
        assert orders[2][4:8] == (CHANGE_TIME_SENTINEL,) * 4
        assert orders[1][6:8] == (CHANGE_TIME_SENTINEL,) * 2
        assert float(orders[2][8]) == 0.0

        # Znuny's own MIN/MAX query and the reference agree (the sentinel dominates the MAX columns)
        derived = {}
        for change in (MAIL, FIRMWARE):
            mins_maxs = (await session.execute(text(_MIN_MAX), {"c": change})).one()
            members = [
                {
                    "planned_start": o[0],
                    "planned_end": o[1],
                    "actual_start": o[2],
                    "actual_end": o[3],
                }
                for o in (
                    await session.execute(
                        text(
                            "SELECT planned_start_time, planned_end_time, actual_start_time,"
                            " actual_end_time FROM change_workorder WHERE change_id = :c"
                        ),
                        {"c": change},
                    )
                ).all()
            ]
            derived[change] = znuny_change_times(members)
            assert tuple(mins_maxs)[0] == min(m["planned_start"] for m in members)
            assert tuple(mins_maxs)[1] == max(m["planned_end"] for m in members)
        assert derived[MAIL] == {
            "planned_start": None,  # one work order has no planned start
            "planned_end": None,  # sentinel is the maximum
            "actual_start": _at(2, 8, 5),
            "actual_end": None,
        }
        assert derived[FIRMWARE] == {
            "planned_start": _at(9, 18),
            "planned_end": _at(9, 21),
            "actual_start": None,
            "actual_end": None,
        }
        assert znuny_change_times([]) == dict.fromkeys(
            ("planned_start", "planned_end", "actual_start", "actual_end")
        )


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_seeded_cab_links_and_history_read_back(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        await _seed_mail_and_firmware(session)
        await seed_change_cab(session, change_id=MAIL, cab_id=946030, user_id=AGENT)
        await seed_change_cab(
            session, change_id=MAIL, cab_id=946031, customer_user_id="chg94-cust@example.org"
        )
        with pytest.raises(ValueError, match="exactly one"):
            await seed_change_cab(session, change_id=MAIL, cab_id=946032)
        with pytest.raises(ValueError, match="exactly one"):
            await seed_change_cab(
                session, change_id=MAIL, cab_id=946032, user_id=AGENT, customer_user_id="x"
            )
        cab = (
            await session.execute(
                text(
                    "SELECT user_id, customer_user_id FROM change_cab WHERE change_id = :c"
                    " ORDER BY id"
                ),
                {"c": MAIL},
            )
        ).all()
        assert [tuple(r) for r in cab] == [(AGENT, None), (None, "chg94-cust@example.org")]

        ticket = 946050
        await seed_change_link(
            session,
            source_object=CHANGE_LINK_OBJECT,
            source_key=MAIL,
            target_object="Ticket",
            target_key=ticket,
            link_type="Normal",
        )
        await seed_change_link(
            session,
            source_object=WORKORDER_LINK_OBJECT,
            source_key=MAIL_WOS[0],
            target_object="ITSMConfigItem",
            target_key=946051,
            link_type="DependsOn",
        )
        links = (
            await session.execute(
                text(
                    "SELECT so.name, r.source_key, tobj.name, r.target_key, t.name, s.name"
                    " FROM link_relation r JOIN link_object so ON so.id = r.source_object_id"
                    " JOIN link_object tobj ON tobj.id = r.target_object_id"
                    " JOIN link_type t ON t.id = r.type_id JOIN link_state s ON s.id = r.state_id"
                    " WHERE so.name IN ('ITSMChange', 'ITSMWorkOrder') ORDER BY so.name"
                )
            )
        ).all()
        assert [tuple(r) for r in links] == [
            ("ITSMChange", str(MAIL), "Ticket", str(ticket), "Normal", "Valid"),
            ("ITSMWorkOrder", str(MAIL_WOS[0]), "ITSMConfigItem", "946051", "DependsOn", "Valid"),
        ]

        await seed_change_history(
            session, history_id=946040, change_id=MAIL, history_type="ChangeAdd",
            created_at=CREATED, content_new=str(MAIL),
        )  # fmt: skip
        await seed_change_history(
            session,
            history_id=946041,
            change_id=MAIL,
            history_type="WorkOrderUpdate",
            created_at=CREATED,
            workorder_id=MAIL_WOS[0],
            fieldname="WorkOrderStateID",
            content_old="124",
            content_new="128",
        )
        with pytest.raises(LookupError, match="NoSuchType"):
            await seed_change_history(
                session, history_id=946042, change_id=MAIL, history_type="NoSuchType",
                created_at=CREATED,
            )  # fmt: skip
        history = (
            await session.execute(
                text(
                    "SELECT t.name, h.workorder_id, h.fieldname, h.content_old, h.content_new"
                    " FROM change_history h JOIN change_history_type t ON t.id = h.type_id"
                    " WHERE h.change_id = :c ORDER BY h.id"
                ),
                {"c": MAIL},
            )
        ).all()
        assert [tuple(r) for r in history] == [
            ("ChangeAdd", None, None, None, str(MAIL)),
            ("WorkOrderUpdate", MAIL_WOS[0], "WorkOrderStateID", "124", "128"),
        ]


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_seed_change_refuses_an_unknown_catalog_name(dialect, profile, request) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        with pytest.raises(LookupError, match="no entry 'finished'"):
            await seed_change(
                session,
                change_id=946012,
                number="2026110110000033",
                title="chg94 bad state",
                state="finished",
                builder_id=1,
                created_at=CREATED,
            )
        with pytest.raises(LookupError, match="no entry 'urgent'"):
            await seed_workorder(
                session,
                change_id=MAIL,
                workorder_id=946025,
                number=1,
                title="chg94 bad type",
                wo_type="urgent",
                state="created",
            )
