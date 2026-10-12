"""Process path endpoint (plan Task 7, D8). Both dialects.

Seed band 893500-893999: users 893510 (ro on the group), 893511 (no rights), 893512 (mover),
group 893520, queue 893530, tickets 893540-893544, processes 893600+, activities 893700+.
Znuny reference: ``DynamicField/ObjectType/Ticket.pm`` writes the ``TicketDynamicFieldUpdate``
history rows the path is rebuilt from.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1 import process as agent_process
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.deployed import clear_revision_cache
from tiqora.domain.auth import AuthenticatedUser
from tiqora.process.path import load_process_path
from tiqora.znuny.history import add_dynamic_field_update

T0 = datetime(2026, 3, 4, 5, 6, 7)
READER, STRANGER, MOVER = 893510, 893511, 893512
GROUP, QUEUE = 893520, 893530
T_PATH, T_RESET, T_GONE, T_LIMIT, T_PLAIN = 893540, 893541, 893542, 893543, 893544
PROC = "Process-wf3-893600"
OTHER_PROC = "Process-wf3-893601"
ACT_A, ACT_B, ACT_C = "Activity-wf3-893700", "Activity-wf3-893701", "Activity-wf3-893702"
ACT_GONE = "Activity-wf3-893799"

_CLEANUP = (
    "DELETE FROM dynamic_field_value WHERE object_id BETWEEN 893540 AND 893549",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN 893540 AND 893549",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 893540 AND 893549",
    "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 893540 AND 893549",
    "DELETE FROM ticket WHERE id BETWEEN 893540 AND 893549",
    "DELETE FROM queue WHERE id = 893530",
    "DELETE FROM group_user WHERE group_id = 893520",
    "DELETE FROM permission_groups WHERE id = 893520",
    "DELETE FROM users WHERE id BETWEEN 893510 AND 893519",
    "DELETE FROM pm_process WHERE id BETWEEN 893600 AND 893609",
    "DELETE FROM pm_activity WHERE id BETWEEN 893700 AND 893709",
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
    pm = (
        "INSERT INTO {table} (id, entity_id, name, config, create_time, create_by, change_time,"
        " change_by) VALUES (:id, :eid, :name, 'ActivityDialog: {{}}', current_timestamp, 1,"
        " current_timestamp, 1)"
    )
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for uid, login, first in (
            (READER, "wf3.path.reader", "Rita"),
            (STRANGER, "wf3.path.stranger", "Sven"),
            (MOVER, "wf3.path.mover", "Mona"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', :f, 'Path', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": login, "f": first, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'wf3-path', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": READER, "g": GROUP, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES (:id, 'Wf3Path-Q', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE, "g": GROUP, "t": T0},
        )
        for tid in (T_PATH, T_RESET, T_GONE, T_LIMIT, T_PLAIN):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'wf3 path', :q, 1, 1, :u, :u, 3, 4,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2026110{tid}", "q": QUEUE, "u": MOVER, "t": T0},
            )
        conn.execute(
            text(
                "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
                " create_time, create_by, change_time, change_by) VALUES (893600, :eid,"
                " 'Wf3 Path Process', 'S1', '{}', :cfg, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"eid": PROC, "cfg": f"StartActivity: {ACT_A}\nPath:\n  {ACT_A}: {{}}\n"},
        )
        for n, eid, name in ((0, ACT_A, "Eingang"), (1, ACT_B, "Prüfung"), (2, ACT_C, "Abschluss")):
            conn.execute(
                text(pm.format(table="pm_activity")),
                {"id": 893700 + n, "eid": eid, "name": name},
            )
    engine.dispose()
    clear_revision_cache()


@pytest.fixture(scope="module", autouse=True)
def _delete_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run(url, _CLEANUP)
    _URLS.clear()
    clear_revision_cache()


READER_USER = AuthenticatedUser(
    id=READER, login="wf3.path.reader", first_name="Rita", last_name="Path", auth_method="session"
)
STRANGER_USER = AuthenticatedUser(
    id=STRANGER,
    login="wf3.path.stranger",
    first_name="Sven",
    last_name="Path",
    auth_method="session",
)


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]

    async def set_df(self, ticket_id: int, name: str, value: str) -> None:
        async with self.factory() as session, session.begin():
            fid = (
                await session.execute(
                    text("SELECT id FROM dynamic_field WHERE name = :n"), {"n": name}
                )
            ).scalar_one()
            n = (
                await session.execute(
                    text(
                        "UPDATE dynamic_field_value SET value_text = :v"
                        " WHERE field_id = :f AND object_id = :o"
                    ),
                    {"v": value, "f": fid, "o": ticket_id},
                )
            ).rowcount
            if not n:
                await session.execute(
                    text(
                        "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                        " VALUES (:f, :o, :v)"
                    ),
                    {"v": value, "f": fid, "o": ticket_id},
                )

    async def move(
        self, ticket_id: int, activity: str, *, by: int = MOVER, process: str | None = None
    ) -> None:
        """What the engine does on a transition: DF history rows plus the new DF value."""
        async with self.factory() as session, session.begin():
            if process is not None:
                await add_dynamic_field_update(
                    session,
                    ticket_id=ticket_id,
                    field_name="ProcessManagementProcessID",
                    value=process,
                    old_value="",
                    user_id=by,
                )
            await add_dynamic_field_update(
                session,
                ticket_id=ticket_id,
                field_name="ProcessManagementActivityID",
                value=activity,
                old_value="",
                user_id=by,
            )
        if process is not None:
            await self.set_df(ticket_id, "ProcessManagementProcessID", process)
        await self.set_df(ticket_id, "ProcessManagementActivityID", activity)

    async def path(self, ticket_id: int):  # type: ignore[no-untyped-def]
        async with self.factory() as session:
            return await load_process_path(session, ticket_id)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    clear_revision_cache()
    _run(sync_url, _CLEANUP)
    _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield Env(factory)
    finally:
        await engine.dispose()
        _run(sync_url, _CLEANUP)
        clear_revision_cache()


@pytest.mark.db
async def test_path_from_history_oldest_first_with_current_flag(env: Env) -> None:
    await env.move(T_PATH, ACT_A, process=PROC)
    await env.move(T_PATH, ACT_B)
    await env.move(T_PATH, ACT_C, by=READER)

    out = await env.path(T_PATH)

    assert out.process_entity_id == PROC
    assert [s.activity_entity_id for s in out.steps] == [ACT_A, ACT_B, ACT_C]
    assert [s.activity_name for s in out.steps] == ["Eingang", "Prüfung", "Abschluss"]
    assert [s.current for s in out.steps] == [False, False, True]
    assert [(s.entered_by, s.entered_by_name) for s in out.steps] == [
        (MOVER, "Mona Path"),
        (MOVER, "Mona Path"),
        (READER, "Rita Path"),
    ]
    assert all(isinstance(s.entered_at, datetime) for s in out.steps)


@pytest.mark.db
async def test_only_rows_after_newest_process_start(env: Env) -> None:
    # First enrolment: A -> B. Then the process DF was reset and the ticket re-enrolled.
    await env.move(T_RESET, ACT_A, process=PROC)
    await env.move(T_RESET, ACT_B)
    await env.move(T_RESET, ACT_A, process=OTHER_PROC)
    await env.move(T_RESET, ACT_C)
    await env.move(T_RESET, ACT_C, process=PROC)  # re-enrolled in PROC at C

    out = await env.path(T_RESET)

    assert out.process_entity_id == PROC
    assert [s.activity_entity_id for s in out.steps] == [ACT_C]
    assert out.steps[0].current is True


@pytest.mark.db
async def test_deleted_activity_has_empty_name(env: Env) -> None:
    await env.move(T_GONE, ACT_A, process=PROC)
    await env.move(T_GONE, ACT_GONE)

    out = await env.path(T_GONE)

    assert [(s.activity_entity_id, s.activity_name) for s in out.steps] == [
        (ACT_A, "Eingang"),
        (ACT_GONE, ""),
    ]
    assert out.steps[-1].current is True


@pytest.mark.db
async def test_no_access_is_404_like_state(env: Env) -> None:
    await env.move(T_PATH, ACT_A, process=PROC)
    async with env.factory() as session:
        ok = await agent_process.get_ticket_path(T_PATH, READER_USER, session)
        assert [s.activity_entity_id for s in ok.steps] == [ACT_A]
        for ticket_id, user, code in (
            (T_PATH, STRANGER_USER, 403),
            (893549, READER_USER, 404),
        ):
            with pytest.raises(HTTPException) as path_exc:
                await agent_process.get_ticket_path(ticket_id, user, session)
            with pytest.raises(HTTPException) as state_exc:
                await agent_process.get_ticket_state(ticket_id, user, session)
            assert path_exc.value.status_code == state_exc.value.status_code == code
        # A ticket outside any process is a normal empty answer, not an error.
        plain = await agent_process.get_ticket_path(T_PLAIN, READER_USER, session)
        assert plain.process_entity_id is None
        assert plain.steps == []


@pytest.mark.db
async def test_limit_200(env: Env) -> None:
    await env.move(T_LIMIT, ACT_A, process=PROC)
    async with env.factory() as session, session.begin():
        for i in range(204):
            await add_dynamic_field_update(
                session,
                ticket_id=T_LIMIT,
                field_name="ProcessManagementActivityID",
                value=ACT_B if i % 2 == 0 else ACT_A,
                old_value="",
                user_id=MOVER,
            )
    await env.set_df(T_LIMIT, "ProcessManagementActivityID", ACT_A)

    out = await env.path(T_LIMIT)

    assert len(out.steps) == 200
    # The newest 200 survive: the very first step (and its neighbours) were cut off.
    assert out.steps[-1].activity_entity_id == ACT_A
    assert out.steps[-1].current is True
    assert sum(s.current for s in out.steps) == 1
