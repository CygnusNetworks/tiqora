"""``link_objects`` / ``unlink_objects`` parity with ``Kernel/System/LinkObject.pm``.

Port targets (Znuny 6.5.22): ``LinkObject.pm`` ``LinkAdd`` (possible types, existing link in
either direction, type groups), ``LinkDelete`` (either direction, any state, missing link is no
error), ``PossibleType``; ``LinkObject/Ticket.pm`` ``LinkAddPost`` / ``LinkDeletePost``
(``TicketLinkAdd`` / ``TicketLinkDelete`` history on every Ticket end whose other end is a
Ticket, none for ``Temporary``).

Both dialects. Seed band 940500-940549 (the API tests use 940550-940599); queue and group rows
carry fixed ids, the autoincrement rows the services write (tickets from the split, history,
outbox, cache signals) are removed above their pre-test ids.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._sysconfig_rows import set_sysconfig_rows
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_write_service import (
    LinkConflict,
    TicketWriteService,
    link_objects,
    unlink_objects,
)
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action
from tiqora.znuny.sysconfig import SysConfig, yaml_encode_effective

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 1, 8, 0, 0)
AGENT = 940501
GROUP = 940510
QUEUE = 940520
T_A, T_B, T_C = 940530, 940531, 940532
TICKETS = (T_A, T_B, T_C)
TN = {T_A: "2026100194053001", T_B: "2026100194053101", T_C: "2026100194053201"}
ARTICLE = 940540
SYSCONFIG_BASE = 940545  # ids 940545..940554
_SC_LO, _SC_HI = SYSCONFIG_BASE, SYSCONFIG_BASE + 9
CI_OBJECT = "ITSMConfigItem"
PREFIX = "Kernel::System::ProcessManagement::TransitionAction::"

# Children first: ticket_history / article rows reference tickets, outbox and cache signals do not.
_MARK_TABLES = (
    "tiqora_event_outbox",
    "tiqora_cache_invalidation",
    "ticket_history",
    "article_data_mime_attachment",
    "article_data_mime_plain",
    "article_data_mime",
    "article",
    "ticket_number_counter",
    "ticket",  # the split ticket; the seeded ones are below the mark
)

_TYPE_NORMAL = {"SourceName": "Normal", "TargetName": "Normal"}
_TYPE_PARENT_CHILD = {"SourceName": "Parent", "TargetName": "Child"}
_TYPE_DEPENDS_ON = {"SourceName": "Depends on", "TargetName": "Required for"}


def _possible(object1: str, object2: str, link_type: str) -> dict[str, str]:
    return {"Object1": object1, "Object2": object2, "Type": link_type}


# Znuny's shipped link configuration plus DependsOn between a ticket and a CI.
CONFIG: dict[str, Any] = {
    "LinkObject::Type###Normal": _TYPE_NORMAL,
    "LinkObject::Type###ParentChild": _TYPE_PARENT_CHILD,
    "LinkObject::Type###DependsOn": _TYPE_DEPENDS_ON,
    "LinkObject::PossibleLink###0200": _possible("Ticket", "Ticket", "Normal"),
    "LinkObject::PossibleLink###0201": _possible("Ticket", "Ticket", "ParentChild"),
    "LinkObject::PossibleLink###3220": _possible("Ticket", CI_OBJECT, "DependsOn"),
    "LinkObject::TypeGroup###0001": ["Normal", "ParentChild"],
}


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _clean_base(conn: Any) -> None:
    keys = ", ".join(f"'{t}'" for t in TICKETS)
    for sql in (
        f"DELETE FROM link_relation WHERE source_key IN ({keys}) OR target_key IN ({keys})",
        "DELETE FROM link_type WHERE name = 'DependsOn' AND id NOT IN"
        " (SELECT type_id FROM link_relation)",
        f"DELETE FROM link_object WHERE name IN ('{CI_OBJECT}', 'Ticket') AND id NOT IN"
        " (SELECT source_object_id FROM link_relation)"
        " AND id NOT IN (SELECT target_object_id FROM link_relation)",
        f"DELETE FROM sysconfig_modified WHERE id BETWEEN {_SC_LO} AND {_SC_HI}",
        f"DELETE FROM sysconfig_default WHERE id BETWEEN {_SC_LO} AND {_SC_HI}",
        f"DELETE FROM article_data_mime WHERE article_id = {ARTICLE}",
        f"DELETE FROM article WHERE id = {ARTICLE}",
        "DELETE FROM ticket_history WHERE ticket_id IN (" + ", ".join(map(str, TICKETS)) + ")",
        "DELETE FROM ticket WHERE id IN (" + ", ".join(map(str, TICKETS)) + ")",
        f"DELETE FROM queue WHERE id = {QUEUE}",
        f"DELETE FROM group_user WHERE group_id = {GROUP}",
        f"DELETE FROM permission_groups WHERE id = {GROUP}",
        f"DELETE FROM users WHERE id = {AGENT}",
    ):
        conn.execute(text(sql))


def _clean_ticket(conn: Any, ticket_id: int) -> None:
    arts = f"(SELECT id FROM article WHERE ticket_id = {ticket_id})"
    for sql in (
        f"DELETE FROM tiqora_event_outbox WHERE ticket_id = {ticket_id}",
        f"DELETE FROM tiqora_cache_invalidation WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket_history WHERE ticket_id = {ticket_id}",
        f"DELETE FROM article_data_mime_attachment WHERE article_id IN {arts}",
        f"DELETE FROM article_data_mime_plain WHERE article_id IN {arts}",
        f"DELETE FROM article_data_mime WHERE article_id IN {arts}",
        f"DELETE FROM article_search_index WHERE ticket_id = {ticket_id}",
        f"DELETE FROM article WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket_flag WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket_index WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket_lock_index WHERE ticket_id = {ticket_id}",
        f"DELETE FROM ticket WHERE id = {ticket_id}",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _clean_base(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'linkpar.agent', 'x', 'Link', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'linkpar-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": NOW},
        )
        for key in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": AGENT, "g": GROUP, "k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'LinkParQueue', :g, 1, 1, 1, 1, 0, 1,"
                " :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "g": GROUP, "t": NOW},
        )
        for tid in TICKETS:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'link parity', :q, 1, 1, :u, 1, 3, 4, 0, 0, 0, 0, 0, 0,"
                    " 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": TN[tid], "q": QUEUE, "u": AGENT, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :tid, 3, 1, 1, :t, 1, :t, 1)"
            ),
            {"id": ARTICLE, "tid": T_A, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO article_data_mime (article_id, a_from, a_to, a_subject, a_body,"
                " a_content_type, incoming_time, create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'c@example.org', 'support@example.org', 'Split me', 'body',"
                " 'text/plain; charset=utf-8', 0, :t, 1, :t, 1)"
            ),
            {"id": ARTICLE, "t": NOW},
        )
    engine.dispose()


@dataclass
class Env:
    sync_url: str
    factory: async_sessionmaker[AsyncSession]
    marks: dict[str, int] = field(default_factory=dict)
    extra_tickets: list[int] = field(default_factory=list)  # created by a service under test

    def configure(self, settings: dict[str, Any]) -> None:
        """Replace the link configuration in sysconfig (``{}`` = no rows at all)."""
        engine = create_engine(self.sync_url)
        with engine.begin() as conn:
            set_sysconfig_rows(conn, SYSCONFIG_BASE, settings)
        engine.dispose()

    async def link(
        self,
        source: int | str,
        target: int | str,
        link_type: str = "Normal",
        *,
        state: str = "Valid",
        target_object: str = "Ticket",
    ) -> bool:
        async with self.factory() as session, session.begin():
            return await link_objects(
                session,
                source_object="Ticket",
                source_key=str(source),
                target_object=target_object,
                target_key=str(target),
                link_type=link_type,
                user_id=AGENT,
                state=state,
            )

    async def unlink(
        self,
        key1: int | str,
        key2: int | str,
        link_type: str = "Normal",
        *,
        object2: str = "Ticket",
    ) -> int:
        async with self.factory() as session, session.begin():
            return await unlink_objects(
                session,
                object1="Ticket",
                key1=str(key1),
                object2=object2,
                key2=str(key2),
                link_type=link_type,
                user_id=AGENT,
            )

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.factory() as session:
            return list((await session.execute(text(sql), params)).all())

    async def relations(self) -> list[tuple[str, str, str, str, str]]:
        """``(source key, target key, type, state, target object)`` of the seeded tickets."""
        rows = await self.rows(
            "SELECT lr.source_key, lr.target_key, lt.name, ls.name, tobj.name"
            " FROM link_relation lr JOIN link_type lt ON lt.id = lr.type_id"
            " JOIN link_state ls ON ls.id = lr.state_id"
            " JOIN link_object tobj ON tobj.id = lr.target_object_id"
            " WHERE lr.source_key IN ('940530', '940531', '940532')"
            " OR lr.target_key IN ('940530', '940531', '940532')"
            " ORDER BY lr.source_key, lr.target_key, lt.name"
        )
        return [(str(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in rows]

    async def history(self, ticket_id: int, history_type: str) -> list[str]:
        rows = await self.rows(
            "SELECT h.name FROM ticket_history h JOIN ticket_history_type ht"
            " ON ht.id = h.history_type_id WHERE h.ticket_id = :t AND ht.name = :n"
            " ORDER BY h.id",
            t=ticket_id,
            n=history_type,
        )
        return [str(r[0]) for r in rows]

    async def cache_signals(self) -> list[tuple[int | None, str | None]]:
        rows = await self.rows(
            "SELECT ticket_id, cache_type FROM tiqora_cache_invalidation WHERE id > :m ORDER BY id",
            m=self.marks["tiqora_cache_invalidation"],
        )
        return [(None if r[0] is None else int(r[0]), r[1]) for r in rows]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    marks = snapshot_marks(sync_url, _MARK_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    e = Env(sync_url, factory, marks)
    e.configure(CONFIG)
    try:
        yield e
    finally:
        await engine.dispose()
        cleanup = create_engine(sync_url)
        with cleanup.begin() as conn:
            for tid in e.extra_tickets:
                conn.execute(
                    text("DELETE FROM link_relation WHERE target_key = :k"), {"k": str(tid)}
                )
                _clean_ticket(conn, tid)
        delete_above_marks(sync_url, marks)
        with cleanup.begin() as conn:
            _clean_base(conn)
        cleanup.dispose()


# ---------------------------------------------------------------------------
# LinkAdd
# ---------------------------------------------------------------------------


async def test_unpointed_reverse_duplicate_not_inserted(env: Env) -> None:
    assert await env.link(T_A, T_B, "Normal") is True
    # Normal has equal source/target names: B -> A is the same link.
    assert await env.link(T_B, T_A, "Normal") is False
    assert await env.link(T_A, T_B, "Normal") is False
    assert await env.relations() == [(str(T_A), str(T_B), "Normal", "Valid", "Ticket")]


async def test_pointed_opposite_direction_conflict(env: Env) -> None:
    assert await env.link(T_A, T_B, "ParentChild") is True
    assert await env.link(T_A, T_B, "ParentChild") is False  # same direction: idempotent
    with pytest.raises(LinkConflict) as err:
        await env.link(T_B, T_A, "ParentChild")  # the parent cannot also be the child
    assert err.value.reason == "opposite_direction"
    assert await env.relations() == [(str(T_A), str(T_B), "ParentChild", "Valid", "Ticket")]


async def test_existing_other_state_conflict(env: Env) -> None:
    assert await env.link(T_A, T_B, "Normal", state="Valid") is True
    with pytest.raises(LinkConflict) as err:
        await env.link(T_A, T_B, "Normal", state="Temporary")
    assert err.value.reason == "state"
    # A second pair is unaffected by the first one's state.
    assert await env.link(T_A, T_C, "Normal", state="Temporary") is True
    assert [r[1:4] for r in await env.relations()] == [
        (str(T_B), "Normal", "Valid"),
        (str(T_C), "Normal", "Temporary"),
    ]


async def test_type_group_conflict(env: Env) -> None:
    assert await env.link(T_A, T_B, "Normal") is True
    # LinkObject::TypeGroup###0001 = [Normal, ParentChild]: the pair cannot have both.
    with pytest.raises(LinkConflict) as err:
        await env.link(T_A, T_B, "ParentChild")
    assert err.value.reason == "type_group"
    assert await env.link(T_A, T_C, "ParentChild") is True  # another pair is free
    # Without the group the same second type is allowed.
    env.configure({k: v for k, v in CONFIG.items() if "TypeGroup" not in k})
    assert await env.link(T_A, T_B, "ParentChild") is True
    assert sorted(r[2] for r in await env.relations() if r[1] == str(T_B)) == [
        "Normal",
        "ParentChild",
    ]


async def test_type_not_possible_for_the_object_pair(env: Env) -> None:
    # PossibleLink only offers DependsOn between a ticket and a CI.
    with pytest.raises(LinkConflict) as err:
        await env.link(T_A, T_B, "DependsOn")
    assert err.value.reason == "not_possible"
    with pytest.raises(LinkConflict):
        await env.link(T_A, T_B, "NoSuchLinkType")
    assert await env.relations() == []


async def test_default_link_configuration_without_sysconfig_rows(env: Env) -> None:
    # A database that never had its configuration rebuilt links like a stock Znuny.
    env.configure({})
    assert await env.link(T_A, T_B, "Normal") is True
    with pytest.raises(LinkConflict) as err:
        await env.link(T_A, T_B, "ParentChild")
    assert err.value.reason == "type_group"
    assert await env.link(T_A, T_C, "ParentChild") is True


async def test_ticket_link_add_history_both_sides(env: Env) -> None:
    assert await env.link(T_A, T_B, "Normal") is True
    # Ticket.pm BackendLinkAddPost: "%%<TN of the other ticket>%%<other id>%%<this id>".
    assert await env.history(T_A, "TicketLinkAdd") == [f"%%{TN[T_B]}%%{T_B}%%{T_A}"]
    assert await env.history(T_B, "TicketLinkAdd") == [f"%%{TN[T_A]}%%{T_A}%%{T_B}"]
    # A refused or repeated link writes no second history row.
    assert await env.link(T_B, T_A, "Normal") is False
    assert len(await env.history(T_A, "TicketLinkAdd")) == 1
    # Temporary links are skipped by the backend.
    assert await env.link(T_A, T_C, "Normal", state="Temporary") is True
    assert await env.history(T_C, "TicketLinkAdd") == []


async def test_no_ticket_history_for_ticket_ci_link(env: Env) -> None:
    assert await env.link(T_A, 77, "DependsOn", target_object=CI_OBJECT) is True
    assert await env.history(T_A, "TicketLinkAdd") == []
    assert await env.relations() == [(str(T_A), "77", "DependsOn", "Valid", CI_OBJECT)]
    # The link list of the ticket is still refreshed in Znuny.
    assert (T_A, None) in await env.cache_signals()
    # The row of the type was created on first use, like TypeLookup does.
    assert await env.rows("SELECT 1 FROM link_type WHERE name = 'DependsOn'")


async def test_link_cache_invalidated(env: Env) -> None:
    assert await env.link(T_A, T_B, "Normal") is True
    signals = await env.cache_signals()
    assert (None, "LinkObject") in signals
    assert (T_A, None) in signals
    assert (T_B, None) in signals
    before = len(signals)
    assert await env.unlink(T_B, T_A, "Normal") == 1
    after = await env.cache_signals()
    assert len(after) > before
    assert after.count((None, "LinkObject")) == 2
    # A refused link changes nothing, so it must not signal either.
    quiet = len(after)
    with pytest.raises(LinkConflict):
        await env.link(T_A, T_B, "DependsOn")
    assert len(await env.cache_signals()) == quiet


# ---------------------------------------------------------------------------
# LinkDelete
# ---------------------------------------------------------------------------


async def test_unlink_removes_either_direction_and_writes_history(env: Env) -> None:
    assert await env.link(T_A, T_B, "ParentChild") is True
    # Asked in the reverse order of how it is stored.
    assert await env.unlink(T_B, T_A, "ParentChild") == 1
    assert await env.relations() == []
    assert await env.history(T_A, "TicketLinkDelete") == [f"%%{TN[T_B]}%%{T_B}%%{T_A}"]
    assert await env.history(T_B, "TicketLinkDelete") == [f"%%{TN[T_A]}%%{T_A}%%{T_B}"]
    # Same order as stored, and a different type of the same pair stays.
    assert await env.link(T_B, T_C, "Normal") is True
    assert await env.link(T_B, 5, "DependsOn", target_object=CI_OBJECT) is True
    assert await env.unlink(T_B, T_C, "Normal") == 1
    assert await env.relations() == [(str(T_B), "5", "DependsOn", "Valid", CI_OBJECT)]
    # A Ticket-CI link is removed without ticket history.
    assert await env.unlink(T_B, 5, "DependsOn", object2=CI_OBJECT) == 1
    assert await env.history(T_B, "TicketLinkDelete") == [
        f"%%{TN[T_A]}%%{T_A}%%{T_B}",
        f"%%{TN[T_C]}%%{T_C}%%{T_B}",
    ]


async def test_unlink_ignores_the_link_state(env: Env) -> None:
    assert await env.link(T_A, T_B, "Normal", state="Temporary") is True
    assert await env.unlink(T_A, T_B, "Normal") == 1  # LinkDelete has no state filter
    assert await env.relations() == []
    assert await env.history(T_A, "TicketLinkDelete") == []  # Temporary: no history


async def test_unlink_returns_zero_when_absent(env: Env) -> None:
    assert await env.unlink(T_A, T_B, "Normal") == 0
    assert await env.unlink(T_A, T_B, "NoSuchLinkType") == 0
    assert await env.link(T_A, T_B, "Normal") is True
    assert await env.unlink(T_A, T_C, "Normal") == 0  # another pair
    assert await env.unlink(T_A, T_B, "ParentChild") == 0  # another type
    assert len(await env.relations()) == 1
    assert await env.history(T_A, "TicketLinkDelete") == []


# ---------------------------------------------------------------------------
# Callers that keep working
# ---------------------------------------------------------------------------


async def test_process_linkadd_conflict_logged_not_raised(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    async def _fetch(name: str) -> Any:
        return None

    async def run(config: dict[str, Any]) -> None:
        async with env.factory() as session, session.begin():
            await execute_transition_action(
                session,
                action=TransitionActionConfig(module=PREFIX + "LinkAdd", config=config),
                ticket_id=T_A,
                process_entity_id="P",
                activity_entity_id="A",
                transition_entity_id="T",
                user_id=AGENT,
                sysconfig=SysConfig(fetch=_fetch),
            )

    base = {"SourceObject": "Ticket", "TargetObject": "Ticket", "Type": "ParentChild"}
    await run({**base, "SourceKey": str(T_A), "TargetKey": str(T_B)})
    with caplog.at_level(logging.WARNING, logger="tiqora.process.engine"):
        # The opposite direction is refused by LinkObject->LinkAdd; the process goes on.
        await run({**base, "SourceKey": str(T_B), "TargetKey": str(T_A)})
        await run({**base, "SourceKey": str(T_A), "TargetKey": str(T_C), "Type": "DependsOn"})
    assert await env.relations() == [(str(T_A), str(T_B), "ParentChild", "Valid", "Ticket")]
    messages = [r.getMessage() for r in caplog.records if r.name == "tiqora.process.engine"]
    assert any("skipped (opposite_direction)" in m for m in messages)
    assert any("skipped (not_possible)" in m for m in messages)


async def test_split_article_still_links(env: Env) -> None:
    async with env.factory() as session, session.begin():
        svc = TicketWriteService(session, env.factory, SysConfig(session))
        new_id = await svc.split_article(AGENT, T_A, ARTICLE, queue_id=QUEUE, title="Split off")
    env.extra_tickets.append(new_id)
    assert new_id not in TICKETS
    assert await env.rows(
        "SELECT 1 FROM link_relation lr JOIN link_type lt ON lt.id = lr.type_id"
        " WHERE lr.source_key = :a AND lr.target_key = :b AND lt.name = 'ParentChild'",
        a=str(T_A),
        b=str(new_id),
    )
    new_tn = await _tn(env, new_id)
    assert await env.history(T_A, "TicketLinkAdd") == [f"%%{new_tn}%%{new_id}%%{T_A}"]
    assert await env.history(new_id, "TicketLinkAdd") == [f"%%{TN[T_A]}%%{T_A}%%{new_id}"]


async def _tn(env: Env, ticket_id: int) -> str:
    return str((await env.rows("SELECT tn FROM ticket WHERE id = :t", t=ticket_id))[0][0])


# ---------------------------------------------------------------------------
# SysConfig.get_prefix (backs the link configuration)
# ---------------------------------------------------------------------------


async def test_sysconfig_get_prefix_honours_modified_and_disabled(env: Env) -> None:
    env.configure(
        {
            "LinkObject::Type###Normal": _TYPE_NORMAL,
            "LinkObject::Type###Off": {"SourceName": "Off", "TargetName": "Off"},
            "LinkObject::Type###Changed": {"SourceName": "Old", "TargetName": "Old"},
            "LinkObjectOther::Type###Nope": {"SourceName": "x", "TargetName": "x"},
        }
    )
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        # An admin disabled one type and edited another one in Znuny's SysConfig.
        conn.execute(
            text("UPDATE sysconfig_modified SET is_valid = 0 WHERE name = 'LinkObject::Type###Off'")
        )
        enc: bytes | str = yaml_encode_effective({"SourceName": "New", "TargetName": "Newer"})
        if conn.dialect.name == "postgresql":
            enc = enc.decode("utf-8")
        conn.execute(
            text(
                "UPDATE sysconfig_modified SET effective_value = :v"
                " WHERE name = 'LinkObject::Type###Changed'"
            ),
            {"v": enc},
        )
    engine.dispose()
    async with env.factory() as session:
        found = await SysConfig(session).get_prefix("LinkObject::Type###")
    assert found == {
        "LinkObject::Type###Normal": _TYPE_NORMAL,
        "LinkObject::Type###Changed": {"SourceName": "New", "TargetName": "Newer"},
    }
