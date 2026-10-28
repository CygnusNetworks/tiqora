"""Legacy (Znuny ITSMChangeManagement) change reader and the pure mapping rules (Task 18).

Pure tests replay the Task 1 vectors (``behaviour/change-itsm-*.json``). DB tests run on both
dialects x both profiles (``itsm_db_url``) in a session that is rolled back, so the strict leak
check has nothing to find. Seed band 946060-946098: users 94606x, groups 946063-946065, queues
946066/946067, tickets 946068/946069, changes 946070-946072, work orders 946073-946076, CAB rows
94608x, history rows 946083-946087, dynamic fields 946088-946089/946097, field values 94609x,
service 946098; logins, titles and numbers contain chg94.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._itsm_adapter_helpers import itsm_session, seed_service, seed_user
from tests._itsm_db import ITSM_FIXTURES, ITSM_PARAMS
from tests._itsm_fixtures import (
    CHANGE_LINK_OBJECT,
    CHANGE_TIME_SENTINEL,
    CLASS_CATALOG,
    WORKORDER_LINK_OBJECT,
    catalog_id,
    seed_change,
    seed_change_cab,
    seed_change_history,
    seed_change_link,
    seed_ci,
    seed_ci_link,
    seed_workorder,
    znuny_change_times,
)
from tiqora.itsm.change.legacy import reader
from tiqora.itsm.change.legacy.mapping import (
    SENTINEL,
    change_window,
    dynamic_field_text,
    plain_text,
    znuny_time,
)
from tiqora.itsm.change.legacy.model import LegacyLink

PROFILES = ("itsm-6", "itsm-7")
NOW = datetime(2026, 10, 1, 8, 0, 0)  # noqa: DTZ001 - Znuny stores naive UTC

MANAGER, AGENT, MEMBER = 946060, 946061, 946062
GROUP_A, GROUP_B, SECRET_GROUP = 946063, 946064, 946065
QUEUE_A, QUEUE_B = 946066, 946067
TICKET_A, TICKET_B = 946068, 946069
MAIL, FIRMWARE, UPTIME = 946070, 946071, 946072
WO_INSTALL, WO_BACKOUT, WO_PIR, WO_UPTIME = 946073, 946074, 946075, 946076
DF_CHANGE, DF_WORKORDER, DF_INVALID = 946088, 946089, 946097
SERVICE = 946098
CUSTOMER = "chg94-cust@example.org"


def _vectors(profile: str) -> dict[str, Any]:
    path = ITSM_FIXTURES / "behaviour" / f"change-{profile}.json"
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _when(raw: str) -> datetime | None:
    return None if not raw else datetime.fromisoformat(raw)


WINDOW_KEYS = ("planned_start", "planned_end", "actual_start", "actual_end")


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, 0)  # noqa: DTZ001 - Znuny stores naive UTC


# --- pure -----------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", PROFILES)
def test_mapping_sentinels_and_plain_text(profile: str) -> None:
    assert znuny_time(None) is None
    assert znuny_time(SENTINEL) is None
    assert SENTINEL == CHANGE_TIME_SENTINEL
    assert znuny_time(SENTINEL.replace(microsecond=5)) is None
    assert znuny_time(_at(2, 8, 5)) == _at(2, 8, 5)

    # survey Q6: the *_plain column is ToAscii of the text; the lines differ in their newlines
    cases = _vectors(profile)["questions"]["6"]["expected"]["cases"]
    assert cases
    for case in cases:
        assert plain_text(case["plain"], case["html"]) == case["plain"].strip()
    assert plain_text("\ndone\n\n") == "done"  # 6.5
    assert plain_text("done\n") == "done"  # 7.3
    # only an empty plain column falls back to the text, tags dropped, never rendered
    assert plain_text(None, "<p>Tom &amp; <b>Jerry</b></p><p>next</p>") == "Tom & Jerry\nnext"
    assert plain_text("   ", "<script>x</script>") == "x"
    assert plain_text("", None) is None and plain_text(None) is None

    assert dynamic_field_text("chg94", None, None) == "chg94"
    assert dynamic_field_text(None, datetime(2026, 11, 2, 8, 0, 0), None) == "2026-11-02 08:00:00"
    assert dynamic_field_text(None, None, 1) == "1"
    assert dynamic_field_text(None, None, None) is None


@pytest.mark.parametrize("profile", PROFILES)
def test_planned_window_derived_from_workorders(profile: str) -> None:
    """Task 1 vector 2: the change window from the peer's work orders equals Znuny's."""
    doc = _vectors(profile)
    peer = doc["peer"]["results"]["probe"]["times"]
    expected = {c["change"]: c for c in doc["questions"]["2"]["expected"]["cases"]}
    assert set(expected) == {"mail", "firmware", "database"}
    for name, case in expected.items():
        orders = peer[name]["workorders"]
        times = [
            (
                _when(o["planned_start"]),
                _when(o["planned_end"]),
                _when(o["actual_start"]),
                _when(o["actual_end"]),
            )
            for o in orders
        ]
        got = change_window(times)
        assert got == tuple(_when(case[k]) for k in WINDOW_KEYS), name
        reference = znuny_change_times(
            [
                dict(
                    zip(
                        ("planned_start", "planned_end", "actual_start", "actual_end"),
                        t,
                        strict=True,
                    )
                )
                for t in times
            ]
        )
        assert got == tuple(reference.values()), name
        # the sentinel itself is accepted for "not set" as well
        raw = [tuple(SENTINEL if v is None else v for v in t) for t in times]
        assert change_window(raw) == got, name  # type: ignore[arg-type]
    # mail: one work order without a planned end blanks the change's planned end (S2)
    assert expected["mail"]["planned_end"] == "" and expected["mail"]["actual_start"]
    assert change_window([]) == (None, None, None, None)


# --- seed -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LegacyWorld:
    ci_visible: int
    ci_hidden: int
    ci_group: int  # itsm-configitem


async def _sql(session: AsyncSession, sql: str, **params: Any) -> None:
    await session.execute(text(sql), params)


async def _group(session: AsyncSession, gid: int, name: str) -> None:
    await _sql(
        session,
        "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
        " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)",
        id=gid,
        n=name,
        t=NOW,
    )


async def grant(session: AsyncSession, user_id: int, group_id: int, key: str = "ro") -> None:
    await _sql(
        session,
        "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
        " change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)",
        u=user_id,
        g=group_id,
        k=key,
        t=NOW,
    )


async def group_id(session: AsyncSession, name: str) -> int:
    return int(
        (
            await session.execute(
                text("SELECT id FROM permission_groups WHERE name = :n"), {"n": name}
            )
        ).scalar_one()
    )


async def _dynamic_field(
    session: AsyncSession, field_id: int, name: str, object_type: str, *, valid: bool = True
) -> None:
    await _sql(
        session,
        "INSERT INTO dynamic_field (id, internal_field, name, label, field_order, field_type,"
        " object_type, config, valid_id, create_time, create_by, change_time, change_by)"
        " VALUES (:id, 0, :n, :n, 1, 'Text', :ot, '---', :v, :t, 1, :t, 1)",
        id=field_id,
        n=name,
        ot=object_type,
        v=1 if valid else 2,
        t=NOW,
    )


async def _df_value(
    session: AsyncSession, value_id: int, field_id: int, object_id: int, value: str
) -> None:
    await _sql(
        session,
        "INSERT INTO dynamic_field_value (id, field_id, object_id, value_text)"
        " VALUES (:id, :f, :o, :v)",
        id=value_id,
        f=field_id,
        o=object_id,
        v=value,
    )


async def seed_legacy_world(session: AsyncSession, profile: str) -> LegacyWorld:
    """Three changes the way ITSMChangeManagement stores them, two queues with one ticket each,
    two CIs (one in a class only the secret group reads) and a service.

    * MAIL (in progress): 3 work orders (install: done, CI + service links; backout: planned;
      pir: no times), CAB of two agents and a customer, history, change and work-order DFs,
      a link to TICKET_A (change is source) and one from TICKET_B (change is target).
    * FIRMWARE (requested): no work orders, nothing linked.
    * UPTIME ("100% uptime"): one work order linked to TICKET_B.
    """
    for uid, login in (
        (MANAGER, "chg94.lmanager"),
        (AGENT, "chg94.lagent"),
        (MEMBER, "chg94.lmember"),
    ):
        await seed_user(session, uid, login, "Erika", "Beispiel")
    for gid, name in ((GROUP_A, "chg94-qa"), (GROUP_B, "chg94-qb"), (SECRET_GROUP, "chg94-sec")):
        await _group(session, gid, name)
    for qid, gid in ((QUEUE_A, GROUP_A), (QUEUE_B, GROUP_B)):
        await _sql(
            session,
            "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
            " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)",
            id=qid,
            n=f"chg94-q{qid}",
            g=gid,
            t=NOW,
        )
    for tid, qid in ((TICKET_A, QUEUE_A), (TICKET_B, QUEUE_B)):
        await _sql(
            session,
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) VALUES (:id, :tn, :title, :q, 1, 1, 1, 1, 3, 1, 0, 0, 0, 0,"
            " 0, 0, 0, :t, 1, :t, 1)",
            id=tid,
            tn=f"2026100194{tid}",
            title=f"chg94 ticket {tid}",
            q=qid,
            t=NOW,
        )
    # Hardware is readable only through the secret group
    hardware = await catalog_id(session, CLASS_CATALOG, "Hardware")
    await _sql(
        session,
        "DELETE FROM general_catalog_preferences WHERE general_catalog_id = :c"
        " AND pref_key = 'Permission'",
        c=hardware,
    )
    await _sql(
        session,
        "INSERT INTO general_catalog_preferences (general_catalog_id, pref_key, pref_value)"
        " VALUES (:c, 'Permission', :g)",
        c=hardware,
        g=str(SECRET_GROUP),
    )
    ci_visible = await seed_ci(
        session, profile, number="94607001", class_name="Computer", name="chg94-srv-01"
    )
    ci_hidden = await seed_ci(
        session, profile, number="94607002", class_name="Hardware", name="chg94-secret-01"
    )
    await seed_service(session, SERVICE, "chg94 Mail")

    await seed_change(
        session,
        change_id=MAIL,
        number="2026110194000070",
        title="chg94 Mail relay replacement",
        state="in progress",
        impact="4 high",
        priority="4 high",
        manager_id=MANAGER,
        builder_id=AGENT,
        created_at=_at(1, 8),
        description="<p>Replace the <b>relay</b> mail01.example.org</p>",
        justification="End of support",
        requested_time=_at(1, 12),
        profile=profile,
    )
    await seed_workorder(
        session,
        change_id=MAIL,
        workorder_id=WO_INSTALL,
        number=1,
        title="chg94 install",
        wo_type="workorder",
        state="closed",
        agent_id=AGENT,
        planned=(_at(2, 8), _at(2, 12)),
        actual=(_at(2, 8, 5), _at(2, 12, 30)),
        instruction="<p>Install the relay</p>",
        report="<p>done</p>",
        planned_effort=4,
        accounted_time=4.5,
        profile=profile,
    )
    await seed_workorder(
        session,
        change_id=MAIL,
        workorder_id=WO_BACKOUT,
        number=2,
        title="chg94 backout",
        wo_type="backout",
        state="created",
        planned=(_at(2, 12), _at(2, 14)),
        profile=profile,
    )
    await seed_workorder(
        session,
        change_id=MAIL,
        workorder_id=WO_PIR,
        number=3,
        title="chg94 review",
        wo_type="pir",
        state="created",
        profile=profile,
    )
    await seed_change_cab(session, change_id=MAIL, cab_id=946080, user_id=AGENT)
    await seed_change_cab(session, change_id=MAIL, cab_id=946081, customer_user_id=CUSTOMER)
    await seed_change_cab(session, change_id=MAIL, cab_id=946082, user_id=MANAGER)
    await seed_change_history(
        session,
        history_id=946083,
        change_id=MAIL,
        history_type="ChangeAdd",
        created_at=_at(1, 8),
        content_new=str(MAIL),
        user_id=AGENT,
    )
    await seed_change_history(
        session,
        history_id=946084,
        change_id=MAIL,
        history_type="ChangeUpdate",
        created_at=_at(1, 9),
        fieldname="ChangeTitle",
        content_old="chg94 Mail relay",
        content_new="chg94 Mail relay replacement",
        user_id=MANAGER,
    )
    await seed_change_history(
        session,
        history_id=946085,
        change_id=MAIL,
        workorder_id=WO_INSTALL,
        history_type="WorkOrderAdd",
        created_at=_at(1, 10),
        content_new=str(WO_INSTALL),
        user_id=AGENT,
    )
    await seed_change_history(
        session,
        history_id=946086,
        change_id=MAIL,
        workorder_id=WO_INSTALL,
        history_type="WorkOrderUpdate",
        created_at=_at(1, 11),
        fieldname="NoNumberCalc",
        content_new="1",
    )
    await seed_change_link(
        session,
        source_object=CHANGE_LINK_OBJECT,
        source_key=MAIL,
        target_object="Ticket",
        target_key=TICKET_A,
        link_type="Normal",
    )
    # the core accepts the pair in both directions (survey Q1)
    await seed_ci_link(
        session,
        source_object="Ticket",
        source_key=TICKET_B,
        target_object=CHANGE_LINK_OBJECT,
        target_key=MAIL,
        link_type="Normal",
    )
    for ci in (ci_visible, ci_hidden):
        await seed_change_link(
            session,
            source_object=WORKORDER_LINK_OBJECT,
            source_key=WO_INSTALL,
            target_object="ITSMConfigItem",
            target_key=ci,
            link_type="DependsOn",
        )
    await seed_change_link(
        session,
        source_object=WORKORDER_LINK_OBJECT,
        source_key=WO_BACKOUT,
        target_object="Service",
        target_key=SERVICE,
        link_type="Normal",
    )
    # a link that is no longer valid does not count
    await seed_ci_link(
        session,
        source_object=CHANGE_LINK_OBJECT,
        source_key=MAIL,
        target_object="Ticket",
        target_key=946099,
        link_type="Normal",
        state="Temporary",
    )
    await _dynamic_field(session, DF_CHANGE, "chg94LChangeRef", CHANGE_LINK_OBJECT)
    await _dynamic_field(session, DF_WORKORDER, "chg94LWorkOrderRef", WORKORDER_LINK_OBJECT)
    await _dynamic_field(session, DF_INVALID, "chg94LGone", CHANGE_LINK_OBJECT, valid=False)
    await _df_value(session, 946090, DF_CHANGE, MAIL, "CHG-94-MAIL")
    await _df_value(session, 946091, DF_CHANGE, MAIL, "CHG-94-MAIL-2")
    await _df_value(session, 946092, DF_WORKORDER, WO_INSTALL, "WO-94-1")
    await _df_value(session, 946093, DF_INVALID, MAIL, "never shown")
    # same object id, other object type: a work order DF never lands on the change with that id
    await _df_value(session, 946094, DF_WORKORDER, MAIL, "not the change")

    await seed_change(
        session,
        change_id=FIRMWARE,
        number="2026110194000071",
        title="chg94 Firmware update",
        state="requested",
        builder_id=AGENT,
        created_at=_at(1, 9),
        description="Plain text description",
        profile=profile,
    )
    await seed_change(
        session,
        change_id=UPTIME,
        number="2026110194000072",
        title="chg94 100% uptime",
        state="requested",
        builder_id=AGENT,
        created_at=_at(1, 10),
        profile=profile,
    )
    await seed_workorder(
        session,
        change_id=UPTIME,
        workorder_id=WO_UPTIME,
        number=1,
        title="chg94 check",
        wo_type="workorder",
        state="created",
        planned=(_at(9, 18), _at(9, 20)),
        profile=profile,
    )
    await seed_change_link(
        session,
        source_object=WORKORDER_LINK_OBJECT,
        source_key=WO_UPTIME,
        target_object="Ticket",
        target_key=TICKET_B,
        link_type="Normal",
    )
    # change_time decides the order of the list: UPTIME newest, then FIRMWARE, then MAIL
    for change_id, changed in ((MAIL, _at(3, 8)), (FIRMWARE, _at(4, 8)), (UPTIME, _at(5, 8))):
        await _sql(
            session,
            "UPDATE change_item SET change_time = :t WHERE id = :id",
            t=changed,
            id=change_id,
        )
    await session.flush()
    return LegacyWorld(
        ci_visible=ci_visible,
        ci_hidden=ci_hidden,
        ci_group=await group_id(session, "itsm-configitem"),
    )


# --- DB ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_read_changes_full(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        world = await seed_legacy_world(session, profile)
        assert await reader.change_tables_present(session) is True

        got = await reader.read_changes(session, [FIRMWARE, 946099, MAIL])
        assert [c.znuny_change_id for c in got] == [
            FIRMWARE,
            MAIL,
        ]  # order of ids, missing left out
        firmware, mail = got

        assert (mail.number, mail.title) == ("2026110194000070", "chg94 Mail relay replacement")
        assert (mail.state, mail.category, mail.impact, mail.priority) == (
            "in progress",
            "3 normal",
            "4 high",
            "4 high",
        )
        assert (mail.manager_login, mail.builder_login) == ("chg94.lmanager", "chg94.lagent")
        assert mail.requested_time == _at(1, 12)
        assert (mail.created_at, mail.changed_at) == (_at(1, 8), _at(3, 8))
        # S3: the plain column, stripped on both lines; never the HTML
        assert mail.description == "Replace the relay mail01.example.org"
        assert mail.justification == "End of support"

        # S2: the pir has no times -> no planned start/end; the backout is unfinished -> no
        # actual end; the install started -> actual start
        assert (mail.planned_start, mail.planned_end) == (None, None)
        assert (mail.actual_start, mail.actual_end) == (_at(2, 8, 5), None)

        install, backout, pir = mail.workorders
        assert (install.number, install.title, install.work_order_type, install.state) == (
            1,
            "chg94 install",
            "workorder",
            "closed",
        )
        assert install.agent_login == "chg94.lagent"
        assert (install.planned_start, install.planned_end) == (_at(2, 8), _at(2, 12))
        assert (install.actual_start, install.actual_end) == (_at(2, 8, 5), _at(2, 12, 30))
        assert (install.planned_effort, install.accounted_time) == (4.0, 4.5)
        assert (install.instruction, install.report) == ("Install the relay", "done")
        assert install.dynamic_fields == {"chg94LWorkOrderRef": ("WO-94-1",)}
        assert (backout.work_order_type, backout.agent_login, backout.actual_start) == (
            "backout",
            None,
            None,
        )
        assert backout.dynamic_fields == {}
        assert (pir.work_order_type, pir.planned_start, pir.planned_end) == ("pir", None, None)

        # CAB: agents (user id order) then the customer user
        assert mail.cab == (
            ("user", "chg94.lmanager"),
            ("user", "chg94.lagent"),
            ("customer_user", CUSTOMER),
        )
        assert [
            (h.history_type, h.field, h.old, h.new, h.workorder_number) for h in mail.history
        ] == [
            ("ChangeAdd", None, None, str(MAIL), 0),
            ("ChangeUpdate", "ChangeTitle", "chg94 Mail relay", "chg94 Mail relay replacement", 0),
            ("WorkOrderAdd", None, None, str(WO_INSTALL), 1),
            ("WorkOrderUpdate", "NoNumberCalc", None, "1", 1),
        ]
        assert [h.user_login for h in mail.history][:3] == [
            "chg94.lagent",
            "chg94.lmanager",
            "chg94.lagent",
        ]
        assert mail.history[0].created_at == _at(1, 8)
        # only valid fields of the change's own object type, all values in row order
        assert mail.dynamic_fields == {"chg94LChangeRef": ("CHG-94-MAIL", "CHG-94-MAIL-2")}
        # S1: CIs and the service hang off work orders; the temporary link is not read
        expected_links = [
            LegacyLink("Ticket", str(TICKET_A), "Normal", "source", 0),
            LegacyLink("Ticket", str(TICKET_B), "Normal", "target", 0),
            LegacyLink("ITSMConfigItem", str(world.ci_visible), "DependsOn", "source", 1),
            LegacyLink("ITSMConfigItem", str(world.ci_hidden), "DependsOn", "source", 1),
            LegacyLink("Service", str(SERVICE), "Normal", "source", 2),
        ]
        assert sorted(mail.links, key=repr) == sorted(expected_links, key=repr)
        # stable order: work order number, object, key
        assert [li.workorder_number for li in mail.links] == [0, 0, 1, 1, 2]

        assert (firmware.state, firmware.manager_login, firmware.description) == (
            "requested",
            None,
            "Plain text description",
        )
        assert (firmware.workorders, firmware.cab, firmware.history, firmware.links) == (
            (),
            (),
            (),
            (),
        )
        assert firmware.dynamic_fields == {}
        assert (firmware.planned_start, firmware.actual_start) == (None, None)

        assert await reader.read_changes(session, []) == []
        with pytest.raises(ValueError):
            await reader.read_changes(session, list(range(1, reader.MAX_READ + 2)))
        assert await reader.iter_change_ids(session, after_id=MAIL - 1, limit=2) == [MAIL, FIRMWARE]
        assert await reader.iter_change_ids(session, after_id=FIRMWARE, limit=5) == [UPTIME]


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_search_by_text_ticket_ci(
    request: pytest.FixtureRequest, dialect: str, profile: str
) -> None:
    async with itsm_session(request, dialect, profile) as (session, _reader, _engine):
        world = await seed_legacy_world(session, profile)

        async def ids(**kw: Any) -> tuple[list[int], int]:
            params: dict[str, Any] = {"text": None, "ticket_id": None, "ci_znuny_id": None}
            params.update(kw)
            items, total = await reader.search(
                session, limit=params.pop("limit", 25), offset=params.pop("offset", 0), **params
            )
            return [i.znuny_change_id for i in items], total

        # newest change first
        assert await ids() == ([UPTIME, FIRMWARE, MAIL], 3)
        assert await ids(limit=1, offset=1) == ([FIRMWARE], 3)
        # number, title, description, justification; case-insensitive on both dialects
        assert await ids(text="MAIL RELAY") == ([MAIL], 1)
        assert await ids(text="2026110194000071") == ([FIRMWARE], 1)
        assert await ids(text="plain text DESCRIPTION") == ([FIRMWARE], 1)
        assert await ids(text="end of support") == ([MAIL], 1)
        # % and _ are literal (F36)
        assert await ids(text="100%") == ([UPTIME], 1)
        assert await ids(text="chg94_") == ([], 0)
        assert await ids(text="%") == ([UPTIME], 1)
        # a ticket: linked to the change (either direction) or to one of its work orders
        assert await ids(ticket_id=TICKET_A) == ([MAIL], 1)
        assert await ids(ticket_id=TICKET_B) == ([UPTIME, MAIL], 2)
        assert await ids(ticket_id=TICKET_B, text="uptime") == ([UPTIME], 1)
        assert await ids(ticket_id=TICKET_B, limit=1, offset=1) == ([MAIL], 2)
        assert await ids(ticket_id=946099) == ([], 0)  # the temporary link does not count
        # a CI: through the work orders (S1)
        assert await ids(ci_znuny_id=world.ci_hidden) == ([MAIL], 1)
        assert await ids(ci_znuny_id=world.ci_visible, ticket_id=TICKET_A) == ([MAIL], 1)
        # both filters: the intersection (UPTIME is linked to TICKET_B but to no CI)
        assert await ids(ci_znuny_id=world.ci_visible, ticket_id=TICKET_B) == ([MAIL], 1)
        assert await ids(ci_znuny_id=946099) == ([], 0)

        items, _ = await reader.search(
            session, text="relay", ticket_id=None, ci_znuny_id=None, limit=10, offset=0
        )
        (summary,) = items
        assert (summary.number, summary.title, summary.state) == (
            "2026110194000070",
            "chg94 Mail relay replacement",
            "in progress",
        )
        assert (summary.planned_start, summary.planned_end, summary.workorder_count) == (
            None,
            None,
            3,
        )
        assert summary.changed_at == _at(3, 8)
        (uptime,), _ = await reader.search(
            session, text="uptime", ticket_id=None, ci_znuny_id=None, limit=10, offset=0
        )
        assert (uptime.planned_start, uptime.planned_end) == (_at(9, 18), _at(9, 20))
        # the page size is capped
        _, total = await reader.search(
            session, text=None, ticket_id=None, ci_znuny_id=None, limit=1000, offset=0
        )
        assert total == 3
