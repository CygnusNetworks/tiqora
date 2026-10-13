"""Znuny parameter parity of ``LinkAdd``, ``TicketWatchSet`` and ``ArticleSend``.

Both dialects. Seed band 899000-899999 (spare band of the process plan): users 899010-899014,
tickets 899040 (watchers) / 899041 + 899042 (links) / 899043 (ArticleSend), one system address
and one queue created without a fixed id (those ids are SMALLINT) and removed by name.
The configs are the examples in the POD of ``LinkAdd.pm``, ``TicketWatchSet.pm`` and
``ArticleSend.pm`` (Znuny 6.5.22) with the example values replaced by seed data.
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

from tests._sysconfig_rows import set_sysconfig_rows
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.deployed import clear_revision_cache
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action
from tiqora.process.exceptions import RequiredFieldMissing
from tiqora.znuny.sysconfig import SysConfig

T0 = datetime(2026, 3, 4, 5, 6, 7)
PREFIX = "Kernel::System::ProcessManagement::TransitionAction::"
ACTOR, WATCH_A, WATCH_B, WATCH_OFF, OVERRIDE = 899010, 899011, 899012, 899013, 899014
T_WATCH, T_SRC, T_DST, T_MAIL = 899040, 899041, 899042, 899043
SA_ADDR, SA_NAME = "wf3-support@example.org", "Wf3 Support"
OTHER_ADDR, OTHER_NAME = "wf3-other@example.org", "Wf3 Other"
OTHER_QUEUE = "wf3-other-queue"
OBJECT = "Wf3Object"

_CLEANUP = (
    "DELETE FROM link_relation WHERE source_key IN ('899041', '899042', '899043')"
    " OR target_key IN ('899041', '899042', '899043')",
    f"DELETE FROM link_object WHERE name IN ('{OBJECT}', 'Ticket')"
    " AND id NOT IN (SELECT source_object_id FROM link_relation)"
    " AND id NOT IN (SELECT target_object_id FROM link_relation)",
    "DELETE FROM tiqora_mail_log WHERE ticket_id BETWEEN 899000 AND 899999",
    "DELETE FROM ticket_watcher WHERE ticket_id BETWEEN 899000 AND 899999",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN 899000 AND 899999",
    "DELETE FROM article_data_mime WHERE article_id IN"
    " (SELECT id FROM article WHERE ticket_id BETWEEN 899000 AND 899999)",
    "DELETE FROM article WHERE ticket_id BETWEEN 899000 AND 899999",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 899000 AND 899999",
    "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 899000 AND 899999",
    # LinkAdd drops Znuny's whole LinkObject cache type (a signal without a ticket).
    "DELETE FROM tiqora_cache_invalidation WHERE ticket_id IS NULL AND cache_type = 'LinkObject'",
    "DELETE FROM ticket WHERE id BETWEEN 899000 AND 899999",
    f"DELETE FROM queue WHERE name = '{OTHER_QUEUE}'",
    f"DELETE FROM system_address WHERE value0 IN ('{SA_ADDR}', '{OTHER_ADDR}')",
    "DELETE FROM sysconfig_modified WHERE id BETWEEN 899050 AND 899059",
    "DELETE FROM sysconfig_default WHERE id BETWEEN 899050 AND 899059",
    "DELETE FROM user_preferences WHERE user_id BETWEEN 899010 AND 899019",
    "DELETE FROM users WHERE id BETWEEN 899010 AND 899019",
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


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        # LinkObject.pm::LinkAdd refuses a type that is not a PossibleLink of the two objects, so
        # the fictitious object of the POD example (FAQ there) needs its pair configured.
        set_sysconfig_rows(
            conn,
            899050,
            {
                "LinkObject::Type###Normal": {"SourceName": "Normal", "TargetName": "Normal"},
                "LinkObject::Type###ParentChild": {
                    "SourceName": "Parent",
                    "TargetName": "Child",
                },
                "LinkObject::PossibleLink###0100": {
                    "Object1": "Ticket",
                    "Object2": "Ticket",
                    "Type": "Normal",
                },
                "LinkObject::PossibleLink###0200": {
                    "Object1": "Ticket",
                    "Object2": "Ticket",
                    "Type": "ParentChild",
                },
                "LinkObject::PossibleLink###0201": {
                    "Object1": "Ticket",
                    "Object2": OBJECT,
                    "Type": "ParentChild",
                },
            },
        )
        for uid, login, valid, email in (
            (ACTOR, "wf3.par.actor", 1, None),
            (WATCH_A, "wf3.par.a", 1, "wf3.a@example.org"),
            (WATCH_B, "wf3.par.b", 1, "wf3.b@example.org"),
            (WATCH_OFF, "wf3.par.off", 2, "wf3.off@example.org"),
            (OVERRIDE, "wf3.par.override", 1, None),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', 'Wf3', :l, :v, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "v": valid, "t": T0},
            )
            if email:
                conn.execute(
                    text(
                        "INSERT INTO user_preferences (user_id, preferences_key,"
                        " preferences_value) VALUES (:u, 'UserEmail', :v)"
                    ),
                    {"u": uid, "v": email},
                )
        for addr, name in ((SA_ADDR, SA_NAME), (OTHER_ADDR, OTHER_NAME)):
            conn.execute(
                text(
                    "INSERT INTO system_address (value0, value1, comments, valid_id, queue_id,"
                    " create_by, create_time, change_by, change_time)"
                    " VALUES (:a, :n, 'wf3', 1, 1, 1, :t, 1, :t)"
                ),
                {"a": addr, "n": name, "t": T0},
            )
        other_sa = conn.execute(
            text("SELECT id FROM system_address WHERE value0 = :a"), {"a": OTHER_ADDR}
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO queue (name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:n, 1, :sa, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"n": OTHER_QUEUE, "sa": other_sa, "t": T0},
        )
        for tid in (T_WATCH, T_SRC, T_DST, T_MAIL):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'wf3 parity', 1, 1, 1, 1, 1, 3, 4, 0, 0, 0, 0, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2026110{tid}", "t": T0},
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
    sysconfig: SysConfig

    async def run(
        self, module: str, config: dict[str, Any], *, ticket: int, user: int = ACTOR
    ) -> None:
        async with self.factory() as session, session.begin():
            await execute_transition_action(
                session,
                action=TransitionActionConfig(module=PREFIX + module, config=config),
                ticket_id=ticket,
                process_entity_id="P",
                activity_entity_id="A",
                transition_entity_id="T",
                user_id=user,
                sysconfig=self.sysconfig,
            )

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.factory() as session:
            return list((await session.execute(text(sql), params)).all())

    async def watchers(self) -> dict[int, int]:
        """``{watcher user id: create_by}`` of the watcher ticket."""
        rows = await self.rows(
            "SELECT user_id, create_by FROM ticket_watcher WHERE ticket_id = :t", t=T_WATCH
        )
        return {int(r[0]): int(r[1]) for r in rows}

    async def links(self) -> list[tuple[str, str, str, str, str, str, int]]:
        rows = await self.rows(
            "SELECT so.name, lr.source_key, tobj.name, lr.target_key, lt.name, ls.name,"
            " lr.create_by FROM link_relation lr"
            " JOIN link_object so ON so.id = lr.source_object_id"
            " JOIN link_object tobj ON tobj.id = lr.target_object_id"
            " JOIN link_type lt ON lt.id = lr.type_id"
            " JOIN link_state ls ON ls.id = lr.state_id"
            " WHERE lr.source_key IN ('899041', '899042') OR lr.target_key IN ('899041', '899042')"
            " ORDER BY lr.source_key, lr.target_key, lt.name"
        )
        return [(r[0], r[1], r[2], r[3], r[4], r[5], int(r[6])) for r in rows]

    async def mails(self) -> list[dict[str, Any]]:
        rows = await self.rows(
            "SELECT m.a_from, m.a_to, m.a_cc, m.a_bcc, m.a_subject FROM article a"
            " JOIN article_data_mime m ON m.article_id = a.id"
            " WHERE a.ticket_id = :t ORDER BY a.id",
            t=T_MAIL,
        )
        return [{"from": r[0], "to": r[1], "cc": r[2], "bcc": r[3], "subject": r[4]} for r in rows]

    async def queue_default_from(self) -> str:
        row = (
            await self.rows(
                "SELECT sa.value0, sa.value1 FROM queue q"
                " JOIN system_address sa ON sa.id = q.system_address_id WHERE q.id = 1"
            )
        )[0]
        return f"{row[1]} <{row[0]}>" if row[1] else str(row[0])


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    clear_revision_cache()
    _run(sync_url, _CLEANUP)
    _seed(sync_url)

    async def _fetch(name: str) -> Any:
        return None

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield Env(factory, SysConfig(fetch=_fetch))
    finally:
        await engine.dispose()
        _run(sync_url, _CLEANUP)
        clear_revision_cache()


# ---------------------------------------------------------------------------
# LinkAdd (LinkAdd.pm POD)
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_link_add_pod_example_links_ticket_to_another_object(env: Env) -> None:
    # POD: SourceObject Ticket / SourceKey 321 -> TargetObject FAQ / TargetKey 5, ParentChild,
    # Valid, UserID override.
    await env.run(
        "LinkAdd",
        {
            "SourceObject": "Ticket",
            "SourceKey": str(T_SRC),
            "TargetObject": OBJECT,
            "TargetKey": "5",
            "Type": "ParentChild",
            "State": "Valid",
            "UserID": OVERRIDE,
        },
        ticket=T_WATCH,
    )
    assert await env.links() == [
        ("Ticket", str(T_SRC), OBJECT, "5", "ParentChild", "Valid", OVERRIDE)
    ]


@pytest.mark.db
async def test_link_add_keeps_the_direction_and_the_state_it_was_given(env: Env) -> None:
    # The linked tickets are not the ticket the process runs on, and source is not the target.
    await env.run(
        "LinkAdd",
        {
            "SourceObject": "Ticket",
            "SourceKey": str(T_DST),
            "TargetObject": "Ticket",
            "TargetKey": str(T_SRC),
            "Type": "ParentChild",
            "State": "Temporary",
        },
        ticket=T_WATCH,
    )
    assert await env.links() == [
        ("Ticket", str(T_DST), "Ticket", str(T_SRC), "ParentChild", "Temporary", ACTOR)
    ]


@pytest.mark.db
async def test_link_add_dynamic_field_user_id_overrides_the_acting_user(env: Env) -> None:
    await env.run(
        "LinkAdd",
        {
            "SourceObject": "Ticket",
            "SourceKey": str(T_SRC),
            "TargetObject": "Ticket",
            "TargetKey": str(T_DST),
            "Type": "Normal",
            "State": "Valid",
            "DynamicField_UserID": str(OVERRIDE),
        },
        ticket=T_SRC,
    )
    (link,) = await env.links()
    assert link[6] == OVERRIDE


@pytest.mark.db
async def test_link_add_is_idempotent_and_never_links_an_object_with_itself(env: Env) -> None:
    config = {
        "SourceObject": "Ticket",
        "SourceKey": str(T_SRC),
        "TargetObject": "Ticket",
        "TargetKey": str(T_DST),
        "Type": "Normal",
        "State": "Valid",
    }
    await env.run("LinkAdd", config, ticket=T_SRC)
    await env.run("LinkAdd", config, ticket=T_SRC)
    await env.run("LinkAdd", {**config, "TargetKey": str(T_SRC)}, ticket=T_SRC)
    assert len(await env.links()) == 1


@pytest.mark.db
async def test_link_add_old_keys_still_link_the_current_ticket(env: Env) -> None:
    await env.run("LinkAdd", {"TargetTicketID": T_DST, "LinkType": "ParentChild"}, ticket=T_SRC)
    await env.run("LinkAdd", {"TicketID": str(T_WATCH), "Type": "Normal"}, ticket=T_SRC)
    assert await env.links() == [
        ("Ticket", str(T_SRC), "Ticket", str(T_WATCH), "Normal", "Valid", ACTOR),
        ("Ticket", str(T_SRC), "Ticket", str(T_DST), "ParentChild", "Valid", ACTOR),
    ]


@pytest.mark.db
async def test_link_add_rejects_what_cannot_be_linked(env: Env) -> None:
    with pytest.raises(RequiredFieldMissing):
        await env.run("LinkAdd", {"SourceObject": "Ticket"}, ticket=T_SRC)
    with pytest.raises(RequiredFieldMissing):
        await env.run("LinkAdd", {"TargetObject": "Ticket", "TargetKey": "not-an-id"}, ticket=T_SRC)
    # An impossible type is not an error: LinkAdd.pm ignores LinkObject->LinkAdd's refusal.
    await env.run("LinkAdd", {"TargetKey": str(T_DST), "Type": "NoSuchLinkType"}, ticket=T_SRC)
    assert await env.links() == []


# ---------------------------------------------------------------------------
# TicketWatchSet (TicketWatchSet.pm POD)
# ---------------------------------------------------------------------------


@pytest.mark.db
async def test_watch_set_pod_user_login_list(env: Env) -> None:
    await env.run(
        "TicketWatchSet",
        {"Action": "Subscribe", "UserLogin": "wf3.par.a, wf3.par.b"},
        ticket=T_WATCH,
    )
    assert await env.watchers() == {WATCH_A: ACTOR, WATCH_B: ACTOR}


@pytest.mark.db
async def test_watch_set_pod_post_master_search_and_user_id_override(env: Env) -> None:
    await env.run(
        "TicketWatchSet",
        {
            "Action": "Subscribe",
            "PostMasterSearch": "wf3.a@example.org, WF3.B@EXAMPLE.ORG, wf3.off@example.org",
            "UserID": OVERRIDE,
        },
        ticket=T_WATCH,
    )
    # the invalid agent is not found, the acting user is the override
    assert await env.watchers() == {WATCH_A: OVERRIDE, WATCH_B: OVERRIDE}


@pytest.mark.db
async def test_watch_set_pod_user_ids_skips_unknown_and_invalid_users(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="tiqora.process.engine"):
        await env.run(
            "TicketWatchSet",
            {"Action": "Subscribe", "UserIDs": f"{WATCH_A}, {WATCH_OFF}, 899999, x"},
            ticket=T_WATCH,
        )
    assert await env.watchers() == {WATCH_A: ACTOR}
    assert caplog.text.count("cannot find user") == 3


@pytest.mark.db
async def test_watch_set_unsubscribe_removes_the_listed_watchers_only(env: Env) -> None:
    await env.run(
        "TicketWatchSet", {"UserLogin": "wf3.par.a, wf3.par.b"}, ticket=T_WATCH
    )  # Action defaults to Subscribe
    await env.run(
        "TicketWatchSet", {"Action": "Unsubscribe", "UserIDs": str(WATCH_A)}, ticket=T_WATCH
    )
    assert set(await env.watchers()) == {WATCH_B}
    history = await env.rows(
        "SELECT h.name FROM ticket_history h JOIN ticket_history_type ht"
        " ON ht.id = h.history_type_id WHERE h.ticket_id = :t AND ht.name = 'Unsubscribe'",
        t=T_WATCH,
    )
    assert len(history) == 1


@pytest.mark.db
async def test_watch_set_pod_unsubscribe_all(env: Env) -> None:
    await env.run(
        "TicketWatchSet", {"Action": "Subscribe", "UserIDs": f"{WATCH_A},{WATCH_B}"}, ticket=T_WATCH
    )
    await env.run(
        "TicketWatchSet", {"Action": "UnsubscribeAll", "UserID": OVERRIDE}, ticket=T_WATCH
    )
    assert await env.watchers() == {}


@pytest.mark.db
async def test_watch_set_old_keys_still_name_the_one_watcher(env: Env) -> None:
    await env.run("TicketWatchSet", {"User": "wf3.par.a"}, ticket=T_WATCH)
    await env.run("TicketWatchSet", {"UserID": WATCH_B}, ticket=T_WATCH)
    await env.run("TicketWatchSet", {}, ticket=T_WATCH, user=OVERRIDE)  # the acting user
    assert await env.watchers() == {WATCH_A: ACTOR, WATCH_B: ACTOR, OVERRIDE: OVERRIDE}
    await env.run("TicketWatchSet", {"Action": "Unsubscribe", "User": "wf3.par.a"}, ticket=T_WATCH)
    assert set(await env.watchers()) == {WATCH_B, OVERRIDE}


# ---------------------------------------------------------------------------
# ArticleSend (ArticleSend.pm POD)
# ---------------------------------------------------------------------------

_MAIL = {
    "SenderType": "agent",
    "IsVisibleForCustomer": 1,
    "To": "to@example.org",
    "Subject": "Wf3 parity",
    "Body": "Hello",
}


@pytest.mark.db
async def test_article_send_pod_from_cc_bcc_are_honoured(env: Env) -> None:
    await env.run(
        "ArticleSend",
        {
            **_MAIL,
            "From": SA_ADDR,
            "Cc": "cc1@example.org, cc2@example.org",
            "Bcc": "bcc@example.org",
        },
        ticket=T_MAIL,
    )
    (mail,) = await env.mails()
    assert mail["from"] == f"{SA_NAME} <{SA_ADDR}>"
    assert mail["to"] == "to@example.org"
    assert mail["cc"] == "cc1@example.org, cc2@example.org"
    assert mail["bcc"] == "bcc@example.org"


@pytest.mark.db
async def test_article_send_from_that_is_no_system_address_gets_the_queue_default(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="tiqora.process.engine"):
        await env.run("ArticleSend", {**_MAIL, "From": "boss@example.org"}, ticket=T_MAIL)
    (mail,) = await env.mails()
    assert mail["from"] == await env.queue_default_from()
    assert "not a system address" in caplog.text


@pytest.mark.db
async def test_article_send_without_from_uses_the_ticket_queue_and_the_queue_key(
    env: Env,
) -> None:
    await env.run("ArticleSend", _MAIL, ticket=T_MAIL)
    await env.run("ArticleSend", {**_MAIL, "Queue": OTHER_QUEUE}, ticket=T_MAIL)
    first, second = await env.mails()
    assert first["from"] == await env.queue_default_from()
    assert second["from"] == f"{OTHER_NAME} <{OTHER_ADDR}>"
    assert first["cc"] in (None, "")
    assert first["bcc"] in (None, "")


@pytest.mark.db
async def test_article_send_old_customer_key_and_list_values(env: Env) -> None:
    await env.run(
        "ArticleSend",
        {
            "Subject": "Wf3 old",
            "Body": "Hello",
            "Customer": "customer@example.org",
            "Cc": ["a@example.org", "b@example.org"],
        },
        ticket=T_MAIL,
    )
    (mail,) = await env.mails()
    assert mail["to"] == "customer@example.org"
    assert mail["cc"] == "a@example.org, b@example.org"
