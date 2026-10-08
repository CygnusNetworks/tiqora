"""Deploy watcher: one test per row of the D4 table, plus the tick result.

Seed band 882000-882499 (pm_* ids and entity ids). Revisions and sync rows are
cleared around every test; the pristine snapshot holds none of them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Generator
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraAclRevision, TiqoraProcessRevision, TiqoraSettings
from tiqora.deployed import clear_revision_cache, latest_revision_info, record_revision
from tiqora.deployed import watcher as watcher_module
from tiqora.deployed.sync_tables import pending_sync_count
from tiqora.deployed.watcher import (
    check_deploy_state,
    revision_write_lock,
    run_deploy_watch_tick,
)
from tiqora.domain import ticket_acl
from tiqora.process.dump import build_process_dump

pytestmark = pytest.mark.db

T0 = datetime(2026, 3, 4, 5, 6, 7)
PROCESS_ID = 882001
TRANSITION_ID = 882001
TRANSITION_EID = "Transition-882001"
_URLS: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


_CLEANUP = (
    "DELETE FROM tiqora_process_revision",
    "DELETE FROM tiqora_acl_revision",
    "DELETE FROM pm_entity_sync",
    "DELETE FROM acl_sync",
    "DELETE FROM pm_transition WHERE id BETWEEN 882000 AND 882499",
    "DELETE FROM pm_process WHERE id BETWEEN 882000 AND 882499",
    "DELETE FROM acl WHERE id BETWEEN 882000 AND 882499",
)


def _reset(sync_url: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(
        engine,
        tables=[  # type: ignore[list-item]
            TiqoraProcessRevision.__table__,
            TiqoraAclRevision.__table__,
            TiqoraSettings.__table__,
        ],
        checkfirst=True,
    )
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
        conn.execute(
            text(
                "INSERT INTO pm_process (id, entity_id, name, state_entity_id, layout, config,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'Process-882001', 'Watch me', 'S1', '{}', 'Description: x\n',"
                " :t, 1, :t, 1)"
            ),
            {"id": PROCESS_ID, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO acl (id, name, valid_id, stop_after_match, config_match,"
                " config_change, create_time, create_by, change_time, change_by)"
                " VALUES (882001, 'watch-acl', 1, 0, 'Properties: {}\n', 'Possible: {}\n',"
                " :t, 1, :t, 1)"
            ),
            {"t": T0},
        )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _final_cleanup() -> Generator[None, None, None]:
    yield
    for sync_url in _URLS:
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt))
        engine.dispose()
    clear_revision_cache()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def session_factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    _reset(sync_url)
    clear_revision_cache()
    ticket_acl._warned_live = False
    watcher_module._candidate.clear()
    engine = create_async_engine(_to_async_url(sync_url))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()
    clear_revision_cache()


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


async def _mark_process_draft(session: AsyncSession, *, name: str = "Edited") -> None:
    """What the Znuny designer does: edit a row and register it as unsynced."""
    await session.execute(
        text("UPDATE pm_process SET name = :n WHERE id = :id"), {"n": name, "id": PROCESS_ID}
    )
    await session.execute(
        text(
            "INSERT INTO pm_entity_sync (entity_type, entity_id, sync_state, create_time,"
            " change_time) VALUES ('Process', 'Process-882001', 'not_sync', :t, :t)"
        ),
        {"t": T0},
    )
    await session.commit()


async def _znuny_deploy(session: AsyncSession) -> None:
    await session.execute(text("DELETE FROM pm_entity_sync"))
    await session.commit()


async def test_draft_edit_is_not_picked_up_until_sync_table_empties(session: AsyncSession) -> None:
    out1 = await check_deploy_state(session, "process", ownership_active=False)
    assert out1.action == "snapshot" and out1.revision_id is not None
    first = await latest_revision_info(session, "process")
    assert first is not None and first.source == "initial"

    await _mark_process_draft(session)
    out2 = await check_deploy_state(session, "process", ownership_active=False)
    assert out2.action == "waiting_for_deploy"
    assert out2.pending_changes == 1
    assert out2.revision_id == out1.revision_id

    await _znuny_deploy(session)
    # First tick after the deploy only notes the new checksum (draft-edit race guard).
    pre = await check_deploy_state(session, "process", ownership_active=False)
    assert pre.action == "unchanged" and pre.revision_id == out1.revision_id
    out3 = await check_deploy_state(session, "process", ownership_active=False)
    assert out3.action == "snapshot" and out3.revision_id != out1.revision_id
    info = await latest_revision_info(session, "process")
    assert info is not None and info.source == "znuny" and info.id == out3.revision_id


async def test_running_live_when_no_revision_and_drafts_exist(session: AsyncSession) -> None:
    await _mark_process_draft(session)
    out = await check_deploy_state(session, "process", ownership_active=False)
    assert out.action == "running_live"
    assert out.revision_id is None and out.pending_changes == 1
    assert await latest_revision_info(session, "process") is None


async def test_unchanged_dump_records_nothing(session: AsyncSession) -> None:
    first = await check_deploy_state(session, "acl", ownership_active=False)
    second = await check_deploy_state(session, "acl", ownership_active=False)
    assert first.action == "snapshot"
    assert second.action == "unchanged" and second.revision_id == first.revision_id
    count = (await session.execute(text("SELECT COUNT(*) FROM tiqora_acl_revision"))).scalar_one()
    assert count == 1


async def test_acl_changes_wait_for_acl_sync_to_empty(session: AsyncSession) -> None:
    first = await check_deploy_state(session, "acl", ownership_active=False)
    await session.execute(text("UPDATE acl SET name = 'watch-acl-2' WHERE id = 882001"))
    await session.execute(
        text(
            "INSERT INTO acl_sync (acl_id, sync_state, create_time, change_time)"
            " VALUES ('882001', 'not_sync', :t, :t)"
        ),
        {"t": T0},
    )
    await session.commit()
    waiting = await check_deploy_state(session, "acl", ownership_active=False)
    assert waiting.action == "waiting_for_deploy" and waiting.revision_id == first.revision_id
    await session.execute(text("DELETE FROM acl_sync"))
    await session.commit()
    await check_deploy_state(session, "acl", ownership_active=False)  # confirmation tick
    done = await check_deploy_state(session, "acl", ownership_active=False)
    assert done.action == "snapshot" and done.revision_id != first.revision_id


async def test_ownership_mode_snapshots_and_clears_sync_tables(session: AsyncSession) -> None:
    await _mark_process_draft(session)
    out = await check_deploy_state(session, "process", ownership_active=True)
    assert out.action == "snapshot" and out.pending_changes == 0
    info = await latest_revision_info(session, "process")
    assert info is not None and info.source == "tiqora" and info.id == out.revision_id
    assert await pending_sync_count(session, "process") == 0

    # Same dump again, but with a leftover sync row: only the table is cleared.
    await session.execute(
        text(
            "INSERT INTO pm_entity_sync (entity_type, entity_id, sync_state, create_time,"
            " change_time) VALUES ('Process', 'Process-882001', 'not_sync', :t, :t)"
        ),
        {"t": T0},
    )
    await session.commit()
    again = await check_deploy_state(session, "process", ownership_active=True)
    assert again.action == "unchanged" and again.revision_id == out.revision_id
    assert await pending_sync_count(session, "process") == 0


async def test_tick_result_reports_live_mode(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        await _mark_process_draft(s)
    result = await run_deploy_watch_tick(
        settings=Settings(environment="test"), session_factory=session_factory
    )
    assert result["mode"] == "live"
    assert result["process"]["action"] == "running_live"
    assert result["process"]["pending_changes"] == 1
    assert result["acl"]["action"] == "snapshot"

    async with session_factory() as s:
        await _znuny_deploy(s)
    result2 = await run_deploy_watch_tick(
        settings=Settings(environment="test"), session_factory=session_factory
    )
    assert result2["mode"] == "deployed"
    assert result2["process"]["action"] == "snapshot"  # no revision yet: initial, no wait
    assert result2["acl"]["action"] == "unchanged"


async def test_acl_live_fallback_warns_once(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="tiqora.domain.ticket_acl"):
        await ticket_acl.load_valid_acls(session)
        await ticket_acl.load_valid_acls(session)
    warnings = [r for r in caplog.records if r.getMessage() == "acl_definitions_running_live"]
    assert len(warnings) == 1


async def test_changing_dump_between_ticks_is_not_snapshotted(session: AsyncSession) -> None:
    first = await check_deploy_state(session, "process", ownership_active=False)
    await session.execute(
        text("UPDATE pm_process SET name = 'v2' WHERE id = :i"), {"i": PROCESS_ID}
    )
    await session.commit()
    seen_v2 = await check_deploy_state(session, "process", ownership_active=False)
    # A designer keeps editing before the next tick: checksum differs again.
    await session.execute(
        text("UPDATE pm_process SET name = 'v3' WHERE id = :i"), {"i": PROCESS_ID}
    )
    await session.commit()
    seen_v3 = await check_deploy_state(session, "process", ownership_active=False)
    assert seen_v2.action == seen_v3.action == "unchanged"
    assert seen_v3.revision_id == first.revision_id
    stable = await check_deploy_state(session, "process", ownership_active=False)
    assert stable.action == "snapshot" and stable.revision_id != first.revision_id
    count = (
        await session.execute(text("SELECT COUNT(*) FROM tiqora_process_revision"))
    ).scalar_one()
    assert count == 2


async def test_concurrent_writer_is_deduplicated_by_in_lock_recheck(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The watcher waits on the lock with a stale view; a rival commits the same dump."""
    async with (
        session_factory() as holder,
        session_factory() as rival,
        session_factory() as watcher_session,
    ):
        async with revision_write_lock(holder, "process"):
            task = asyncio.create_task(
                check_deploy_state(watcher_session, "process", ownership_active=False)
            )
            await asyncio.sleep(1.0)
            assert not task.done()  # blocked on the lock, latest was None when it looked
            dump = await build_process_dump(rival)
            info = await record_revision(
                rival, "process", dump=dump, source="znuny", deployed_by=None
            )
            await rival.commit()
        out = await asyncio.wait_for(task, timeout=15)
    assert out.action == "unchanged" and out.revision_id == info.id
    async with session_factory() as s:
        count = (await s.execute(text("SELECT COUNT(*) FROM tiqora_process_revision"))).scalar_one()
    assert count == 1


async def test_lock_is_released_after_exception(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watcher_module, "_LOCK_TIMEOUT_SECONDS", 2)
    async with session_factory() as a, session_factory() as b:
        with pytest.raises(RuntimeError, match="boom"):
            async with revision_write_lock(a, "acl"):
                raise RuntimeError("boom")
        async with revision_write_lock(b, "acl"):
            pass  # acquirable again: the failed holder released it


async def test_lock_times_out_instead_of_blocking_forever(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watcher_module, "_LOCK_TIMEOUT_SECONDS", 1)
    async with session_factory() as a, session_factory() as b, revision_write_lock(a, "acl"):
        with pytest.raises(TimeoutError):
            async with revision_write_lock(b, "acl"):
                pass


async def test_check_deploy_state_rolls_back_on_error(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("dump failed")

    monkeypatch.setattr(watcher_module, "_build_dump", boom)
    with pytest.raises(RuntimeError, match="dump failed"):
        await check_deploy_state(session, "process", ownership_active=False)
    assert not session.in_transaction()
