"""The legacy change world of the Task 18/19 tests: Znuny changes the way ITSMChangeManagement
stores them, with queues, tickets, CIs and a service to link to.

Seed band 946060-946098 (Znuny rows, flushed into a session the test rolls back): users
94606x, groups 946063-946065, queues 946066/946067, tickets 946068/946069, changes
946070-946072, work orders 946073-946076, CAB rows 946080-946082, history rows 946083-946089 and
946095, dynamic fields 946088/946089/946097 (another table), field values 946090-946094, service
946098; logins, titles and numbers contain chg94.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._itsm_adapter_helpers import seed_service, seed_user
from tests._itsm_fixtures import (
    CHANGE_LINK_OBJECT,
    CHANGE_STATE_CATALOG,
    CLASS_CATALOG,
    WORKORDER_LINK_OBJECT,
    WORKORDER_STATE_CATALOG,
    catalog_id,
    seed_change,
    seed_change_cab,
    seed_change_history,
    seed_change_link,
    seed_ci,
    seed_ci_link,
    seed_workorder,
)

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
#: a CI id no configitem row has (linked, but deleted in Znuny)
GONE_CI = 946099
#: a change state id no catalog entry has (its history value stays raw)
UNKNOWN_STATE_ID = 946096


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, 0)  # noqa: DTZ001 - Znuny stores naive UTC


@dataclass(frozen=True, slots=True)
class LegacyWorld:
    ci_visible: int
    ci_hidden: int
    ci_group: int  # itsm-configitem


async def sql(session: AsyncSession, sql: str, **params: Any) -> None:
    await session.execute(text(sql), params)


async def _group(session: AsyncSession, gid: int, name: str) -> None:
    await sql(
        session,
        "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
        " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)",
        id=gid,
        n=name,
        t=NOW,
    )


async def grant(session: AsyncSession, user_id: int, group_id: int, key: str = "ro") -> None:
    await sql(
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
    await sql(
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
    await sql(
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

    * MAIL (in progress): 3 work orders (install: done, links to both CIs; backout: planned,
      links to the service and TICKET_B; pir: no times; install and backout also link GONE_CI),
      CAB of two agents and a customer, history, change and work-order DFs, a link to TICKET_A
      (change is source) and one from TICKET_B (change is target).
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
        await sql(
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
        await sql(
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
    await sql(
        session,
        "DELETE FROM general_catalog_preferences WHERE general_catalog_id = :c"
        " AND pref_key = 'Permission'",
        c=hardware,
    )
    await sql(
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
        created_at=at(1, 8),
        description="<p>Replace the <b>relay</b> mail01.example.org</p>",
        justification="End of support",
        requested_time=at(1, 12),
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
        planned=(at(2, 8), at(2, 12)),
        actual=(at(2, 8, 5), at(2, 12, 30)),
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
        planned=(at(2, 12), at(2, 14)),
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
        created_at=at(1, 8),
        content_new=str(MAIL),
        user_id=AGENT,
    )
    await seed_change_history(
        session,
        history_id=946084,
        change_id=MAIL,
        history_type="ChangeUpdate",
        created_at=at(1, 9),
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
        created_at=at(1, 10),
        content_new=str(WO_INSTALL),
        user_id=AGENT,
    )
    # update rows as HistoryAdd writes them: raw ids, "" for "was unset", %%-joined CAB lists
    created = await catalog_id(session, WORKORDER_STATE_CATALOG, "created")
    closed = await catalog_id(session, WORKORDER_STATE_CATALOG, "closed")
    requested = await catalog_id(session, CHANGE_STATE_CATALOG, "requested")
    updates: list[tuple[int, str, int | None, str, str, str]] = [
        (946086, "WorkOrderUpdate", WO_INSTALL, "WorkOrderStateID", str(created), str(closed)),
        (946087, "ChangeUpdate", None, "ChangeManagerID", str(MEMBER), str(MANAGER)),
        (946088, "ChangeCABUpdate", None, "CABAgents", "", f"{AGENT}%%{MANAGER}"),
        (946089, "ChangeCABUpdate", None, "CABCustomers", "", CUSTOMER),
        (946095, "ChangeUpdate", None, "ChangeStateID", str(requested), str(UNKNOWN_STATE_ID)),
    ]
    for minute, (hid, kind, workorder, field, old, new) in enumerate(updates):
        await seed_change_history(
            session,
            history_id=hid,
            change_id=MAIL,
            workorder_id=workorder,
            history_type=kind,
            created_at=at(1, 11, minute),
            fieldname=field,
            content_old=old,
            content_new=new,
            user_id=MANAGER,
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
    # duplicates for the hidden count: TICKET_B also through the backout, and a CI that no longer
    # exists through two work orders
    await seed_change_link(
        session,
        source_object=WORKORDER_LINK_OBJECT,
        source_key=WO_BACKOUT,
        target_object="Ticket",
        target_key=TICKET_B,
        link_type="Normal",
    )
    for wo in (WO_INSTALL, WO_BACKOUT):
        await seed_change_link(
            session,
            source_object=WORKORDER_LINK_OBJECT,
            source_key=wo,
            target_object="ITSMConfigItem",
            target_key=GONE_CI,
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
        created_at=at(1, 9),
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
        created_at=at(1, 10),
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
        planned=(at(9, 18), at(9, 20)),
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
    for change_id, changed in ((MAIL, at(3, 8)), (FIRMWARE, at(4, 8)), (UPTIME, at(5, 8))):
        await sql(
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
