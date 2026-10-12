"""GenericInterface event follower (G12, both dialects): takeover flag, cursor, matching, exactly
once. Webservices and ``ticket_history`` rows are seeded directly; the tick runs against the real
tables and enqueues real ``core.gi_invoke*`` jobs, which are never executed here.

Seed band 51310-51369: webservices 51310-51312, tickets 51310-51349 (``gi51follow``), queues
51310/51311, group 51310. Jobs, history and other autoincrement rows are removed with
``snapshot_marks`` / ``delete_above_marks``.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session

from tests._gi_fixtures import (
    GI_MARK_TABLES,
    cleanup_webservices,
    insert_webservice,
    requester_config,
)
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests.test_gi_jobs import _async_url
from tiqora.api.v1.admin import daemons as admin_daemons
from tiqora.api.v1.admin.schemas import DaemonUpdate
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.settings_store import (
    KEY_GI_REQUESTER_CURSOR,
    KEY_GI_REQUESTER_ENABLED,
    KEY_GI_REQUESTER_LAST_ENABLED_TICK,
)
from tiqora.extensions import get_registry
from tiqora.gi import follower
from tiqora.gi.follower import run_gi_requester_tick
from tiqora.gi.jobs import GI_INVOKE_KIND, GI_INVOKE_SYNC_KIND
from tiqora.gi.loader import GiWebservices
from tiqora.jobs.registry import JOB_HANDLERS
from tiqora.jobs.service import utc_now
from tiqora.plugins.events import GapTracker

pytestmark = pytest.mark.db

WS, WS_BROKEN, WS_OTHER = 51310, 51311, 51312
QUEUE_A, QUEUE_B = 51310, 51311
FIRST_TICKET = 51310
_SEEDED: dict[str, dict[str, int]] = {}
_SETTING_KEYS = (
    KEY_GI_REQUESTER_ENABLED,
    KEY_GI_REQUESTER_CURSOR,
    KEY_GI_REQUESTER_LAST_ENABLED_TICK,
)

CREATE = {"Type": "Ticket::Generic", "Events": [{"Event": "TicketCreate", "Asynchronous": "1"}]}


def _clean(conn: Any) -> None:
    cleanup_webservices(conn, [WS, WS_BROKEN, WS_OTHER])
    conn.execute(text("DELETE FROM tiqora_job WHERE kind LIKE 'core.gi_invoke%'"))
    conn.execute(text("DELETE FROM ticket_history WHERE ticket_id BETWEEN 51310 AND 51349"))
    conn.execute(text("DELETE FROM ticket WHERE id BETWEEN 51310 AND 51349"))
    conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(_SETTING_KEYS)))
    conn.execute(
        text("DELETE FROM sysconfig_modified WHERE id BETWEEN 51310 AND 51319"),
    )
    conn.execute(text("DELETE FROM sysconfig_default WHERE id BETWEEN 51310 AND 51319"))


def _seed(sync_url: str) -> None:
    if sync_url in _SEEDED:
        return
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    _SEEDED[sync_url] = snapshot_marks(sync_url, GI_MARK_TABLES)
    with engine.begin() as conn:
        _clean(conn)
        conn.execute(text("DELETE FROM queue WHERE id IN (51310, 51311)"))
        conn.execute(text("DELETE FROM permission_groups WHERE id = 51310"))
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (51310, 'gi51follow-group', 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            )
        )
        for qid, name in ((QUEUE_A, "GI51FollowQ"), (QUEUE_B, "GI51FollowOther")):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id, comments,"
                    " calendar_name, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 51310, 1, 1, 1, 1, 0, 1, NULL, NULL,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"id": qid, "n": name},
            )
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url, marks in _SEEDED.items():
        delete_above_marks(sync_url, marks)
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            _clean(conn)
            conn.execute(text("DELETE FROM queue WHERE id IN (51310, 51311)"))
            conn.execute(text("DELETE FROM permission_groups WHERE id = 51310"))
        engine.dispose()
    _SEEDED.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def url_fixture(request: pytest.FixtureRequest) -> Generator[str, None, None]:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        _clean(conn)
    engine.dispose()
    yield url
    engine = create_engine(url)
    with engine.begin() as conn:
        _clean(conn)
    engine.dispose()


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


# -------------------------------------------------------------------------------------- seeding


def _install(
    sync_url: str, invokers: dict[str, Any], ws_id: int = WS, name: str = "GI51Follow"
) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        cleanup_webservices(conn, [ws_id])
        insert_webservice(conn, ws_id=ws_id, name=name, config=requester_config(invokers=invokers))
    engine.dispose()


def _install_broken(sync_url: str) -> None:
    """A valid-flagged webservice whose YAML does not parse."""
    engine = create_engine(sync_url)
    stored: bytes | str = "foo: [unclosed"
    if engine.dialect.name != "postgresql":
        stored = stored.encode()
    with engine.begin() as conn:
        cleanup_webservices(conn, [WS_BROKEN])
        conn.execute(
            text(
                "INSERT INTO gi_webservice_config (id, name, config, valid_id, create_time,"
                " create_by, change_time, change_by) VALUES (:id, 'GI51Broken', :c, 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            ),
            {"id": WS_BROKEN, "c": stored},
        )
    engine.dispose()


def _ticket(sync_url: str, number: int, queue_id: int = QUEUE_A) -> int:
    tid = FIRST_TICKET + number
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ticket WHERE id = :i"), {"i": tid})
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, 'gi51follow', :q, 1, 1, 1, 1, 3, 1, 'gi51-cust',"
                " 'gi51.cust@example.org', 0, 0, 0, 0, 0, 0, 0, current_timestamp, 1,"
                " current_timestamp, 1)"
            ),
            {"id": tid, "tn": f"gi51follow{tid}", "q": queue_id},
        )
    engine.dispose()
    return tid


def _history(
    sync_url: str, ticket_id: int, htype: str = "NewTicket", name: str = "%%gi51follow"
) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        type_id = conn.execute(
            text("SELECT id FROM ticket_history_type WHERE name = :n"), {"n": htype}
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO ticket_history (name, history_type_id, ticket_id, article_id,"
                " type_id, queue_id, owner_id, priority_id, state_id, create_time, create_by,"
                " change_time, change_by) VALUES (:name, :ht, :tid, NULL, 1, 1, 1, 3, 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            ),
            {"name": name, "ht": type_id, "tid": ticket_id},
        )
        new_id = conn.execute(
            text("SELECT MAX(id) FROM ticket_history WHERE ticket_id = :t"), {"t": ticket_id}
        ).scalar_one()
    engine.dispose()
    return int(new_id)


def _new_ticket(sync_url: str, number: int, queue_id: int = QUEUE_A) -> tuple[int, int]:
    """A ticket and its ``NewTicket`` history row: ``(ticket_id, history_id)``."""
    tid = _ticket(sync_url, number, queue_id)
    return tid, _history(sync_url, tid)


def _set_setting(sync_url: str, key: str, value: str | None) -> None:
    engine = create_engine(sync_url)
    with Session(engine) as session:
        session.execute(delete(TiqoraSettings).where(TiqoraSettings.key == key))
        if value is not None:
            session.add(TiqoraSettings(key=key, value=value))
        session.commit()
    engine.dispose()


def _get_setting(sync_url: str, key: str) -> str | None:
    engine = create_engine(sync_url)
    with Session(engine) as session:
        value = session.execute(
            select(TiqoraSettings.value).where(TiqoraSettings.key == key)
        ).scalar_one_or_none()
    engine.dispose()
    return value


def _head(sync_url: str) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        value = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM ticket_history")).scalar_one()
    engine.dispose()
    return int(value)


def _jobs(sync_url: str) -> list[dict[str, Any]]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, kind, payload, dedupe_key FROM tiqora_job"
                " WHERE kind LIKE 'core.gi_invoke%' ORDER BY id"
            )
        ).all()
    engine.dispose()
    return [
        {"id": int(r[0]), "kind": str(r[1]), "payload": json.loads(r[2]), "dedupe_key": r[3]}
        for r in rows
    ]


class Follower:
    """One worker process: the same webservice snapshot and tracker on every tick."""

    def __init__(self, sync_url: str, *, hold_seconds: float = 0.0) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.url = sync_url
        self.engine = create_async_engine(_async_url(sync_url))
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.webservices = GiWebservices()
        self.tracker = GapTracker(hold_seconds=hold_seconds, step=1, offset=None)

    async def tick(self, now: float = 1000.0) -> dict[str, int]:
        return await run_gi_requester_tick(
            settings=get_settings(),
            session_factory=self.factory,
            webservices=self.webservices,
            tracker=self.tracker,
            now=now,
        )

    async def start(self) -> dict[str, int]:
        """Enable the flag and run the tick that starts at the head of the history."""
        _set_setting(self.url, KEY_GI_REQUESTER_ENABLED, "1")
        return await self.tick()

    async def close(self) -> None:
        await self.engine.dispose()


@pytest.fixture
async def follower_(url_fixture: str) -> Any:
    f = Follower(url_fixture)
    try:
        yield f
    finally:
        await f.close()


# ------------------------------------------------------------------------------------------ tests


async def test_flag_off_moves_cursor_without_sending(url_fixture: str, follower_: Follower) -> None:
    _install(url_fixture, {"Send": CREATE})
    _new_ticket(url_fixture, 0)
    result = await follower_.tick()
    assert result == {"enabled": 0}
    assert _jobs(url_fixture) == []
    assert _get_setting(url_fixture, KEY_GI_REQUESTER_CURSOR) == str(_head(url_fixture))
    assert _get_setting(url_fixture, KEY_GI_REQUESTER_LAST_ENABLED_TICK) is None
    # more history while off: still no job, cursor follows
    _new_ticket(url_fixture, 1)
    await follower_.tick()
    assert _jobs(url_fixture) == []
    assert _get_setting(url_fixture, KEY_GI_REQUESTER_CURSOR) == str(_head(url_fixture))


async def test_first_enabled_tick_starts_at_head(url_fixture: str, follower_: Follower) -> None:
    _install(url_fixture, {"Send": CREATE})
    _new_ticket(url_fixture, 0)  # written before the flag was ever on
    first = await follower_.start()
    assert first["enabled"] == 1 and first["enqueued"] == 0
    assert _get_setting(url_fixture, KEY_GI_REQUESTER_CURSOR) == str(_head(url_fixture))
    assert _get_setting(url_fixture, KEY_GI_REQUESTER_LAST_ENABLED_TICK) is not None
    assert _jobs(url_fixture) == []
    tid, hid = _new_ticket(url_fixture, 1)
    second = await follower_.tick()
    assert second["enqueued"] == 1
    jobs = _jobs(url_fixture)
    assert [j["payload"]["data"]["TicketID"] for j in jobs] == [tid]
    assert jobs[0]["payload"]["history_id"] == hid


async def test_reenable_after_gap_does_not_replay(url_fixture: str, follower_: Follower) -> None:
    _install(url_fixture, {"Send": CREATE})
    await follower_.start()  # marker written
    # disabled without any tick in between (worker down or flag flipped between two ticks)
    _set_setting(url_fixture, KEY_GI_REQUESTER_ENABLED, "0")
    _new_ticket(url_fixture, 0)
    _new_ticket(url_fixture, 1)
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_async_url(url_fixture))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            out = await admin_daemons.update_daemon(
                "gi_requester", DaemonUpdate(enabled=True), _admin(), session
            )
        assert out.enabled is True
    finally:
        await engine.dispose()
    assert _get_setting(url_fixture, KEY_GI_REQUESTER_CURSOR) == str(_head(url_fixture))
    result = await follower_.tick()
    assert result["enqueued"] == 0 and _jobs(url_fixture) == []

    # same without the API: the marker is older than the stale threshold
    _set_setting(
        url_fixture,
        KEY_GI_REQUESTER_LAST_ENABLED_TICK,
        (utc_now() - timedelta(hours=1)).isoformat(),
    )
    _new_ticket(url_fixture, 2)
    result = await follower_.tick()
    assert result["enqueued"] == 0 and _jobs(url_fixture) == []
    assert _get_setting(url_fixture, KEY_GI_REQUESTER_CURSOR) == str(_head(url_fixture))
    # and the next ticket after that is delivered
    tid, _ = _new_ticket(url_fixture, 3)
    result = await follower_.tick()
    assert [j["payload"]["data"]["TicketID"] for j in _jobs(url_fixture)] == [tid]


async def test_long_tick_marker_still_delivers(url_fixture: str, follower_: Follower) -> None:
    _install(url_fixture, {"Send": CREATE})
    await follower_.start()
    # the previous tick ran long (budget stop) or the worker restarted briefly
    _set_setting(
        url_fixture,
        KEY_GI_REQUESTER_LAST_ENABLED_TICK,
        (utc_now() - timedelta(seconds=60)).isoformat(),
    )
    tid, _ = _new_ticket(url_fixture, 0)
    result = await follower_.tick()
    assert result["enqueued"] == 1
    assert [j["payload"]["data"]["TicketID"] for j in _jobs(url_fixture)] == [tid]


def test_stale_threshold_covers_budget_and_floor() -> None:
    assert follower.stale_marker_seconds(5) >= 120
    assert follower.stale_marker_seconds(5) > follower.TICK_BUDGET_SECONDS + 15
    assert follower.stale_marker_seconds(600) >= follower.TICK_BUDGET_SECONDS + 3 * 600


async def test_stale_reset_logs_warning_with_skipped(url_fixture: str, follower_: Follower) -> None:
    import structlog.testing

    _install(url_fixture, {"Send": CREATE})
    await follower_.start()
    _set_setting(
        url_fixture,
        KEY_GI_REQUESTER_LAST_ENABLED_TICK,
        (utc_now() - timedelta(hours=1)).isoformat(),
    )
    _new_ticket(url_fixture, 0)
    with structlog.testing.capture_logs() as logs:
        await follower_.tick()
    (entry,) = [e for e in logs if e["event"] == "gi_requester_cursor_reset"]
    assert entry["log_level"] == "warning" and entry["skipped"] >= 1


async def test_event_fires_matching_invokers_once(url_fixture: str, follower_: Follower) -> None:
    _install(url_fixture, {"Send": CREATE})
    await follower_.start()
    tid, hid = _new_ticket(url_fixture, 0)
    result = await follower_.tick()
    assert (result["events"], result["enqueued"]) == (1, 1)
    (job,) = _jobs(url_fixture)
    assert job["kind"] == GI_INVOKE_KIND
    assert job["payload"] == {
        "webservice_id": WS,
        "invoker": "Send",
        "data": {"TicketID": tid, "Event": "TicketCreate"},
        "asynchronous": True,
        "origin": "event",
        "history_id": hid,
        "past_execution": None,
        "dry_run": False,
    }
    assert job["dedupe_key"] == f"gi:{hid}:{WS}:Send"
    again = await follower_.tick()
    assert again["enqueued"] == 0 and len(_jobs(url_fixture)) == 1


async def test_sync_invoker_goes_to_sync_lane_in_order(
    url_fixture: str, follower_: Follower
) -> None:
    sync_ev = {
        "Type": "Ticket::Generic",
        "Events": [{"Event": "TicketCreate", "Asynchronous": "0"}],
    }
    _install(url_fixture, {"Async": CREATE, "Sync": sync_ev})
    await follower_.start()
    first, _ = _new_ticket(url_fixture, 0)
    second, _ = _new_ticket(url_fixture, 1)
    result = await follower_.tick()
    assert result["enqueued"] == 4
    jobs = _jobs(url_fixture)
    sync_jobs = [j for j in jobs if j["kind"] == GI_INVOKE_SYNC_KIND]
    assert [j["payload"]["data"]["TicketID"] for j in sync_jobs] == [first, second]
    assert sync_jobs[0]["id"] < sync_jobs[1]["id"]
    assert {j["payload"]["invoker"] for j in sync_jobs} == {"Sync"}
    assert {j["payload"]["invoker"] for j in jobs if j["kind"] == GI_INVOKE_KIND} == {"Async"}
    registry = get_registry()
    sync_handler = registry.get(JOB_HANDLERS, GI_INVOKE_SYNC_KIND)
    async_handler = registry.get(JOB_HANDLERS, GI_INVOKE_KIND)
    assert sync_handler is not None and async_handler is not None
    assert (sync_handler.lane, async_handler.lane) == ("gi_sync", "gi")


async def test_condition_filters(url_fixture: str, follower_: Follower) -> None:
    conditional = {
        "Type": "Ticket::Generic",
        "Events": [
            {
                "Event": "TicketCreate",
                "Asynchronous": "1",
                "Condition": {
                    "ConditionLinking": "and",
                    "Condition": {
                        "Cond1": {
                            "Type": "and",
                            "Fields": {"Queue": {"Type": "String", "Match": "GI51FollowQ"}},
                        }
                    },
                },
            }
        ],
    }
    _install(url_fixture, {"Send": conditional})
    await follower_.start()
    _new_ticket(url_fixture, 0, QUEUE_B)
    result = await follower_.tick()
    assert result["events"] == 1 and result["enqueued"] == 0
    assert _jobs(url_fixture) == []
    tid, _ = _new_ticket(url_fixture, 1, QUEUE_A)
    result = await follower_.tick()
    assert result["enqueued"] == 1
    assert [j["payload"]["data"]["TicketID"] for j in _jobs(url_fixture)] == [tid]


async def test_enqueue_and_cursor_commit_atomically(
    url_fixture: str, follower_: Follower, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(url_fixture, {"Send": CREATE})
    await follower_.start()
    before = _get_setting(url_fixture, KEY_GI_REQUESTER_CURSOR)
    tid, _ = _new_ticket(url_fixture, 0)

    async def boom(session: Any, cursor: int) -> None:
        raise RuntimeError("cursor write failed")

    monkeypatch.setattr(follower, "_write_cursor", boom)
    with pytest.raises(RuntimeError, match="cursor write failed"):
        await follower_.tick()
    assert _jobs(url_fixture) == []
    assert _get_setting(url_fixture, KEY_GI_REQUESTER_CURSOR) == before
    monkeypatch.undo()
    result = await follower_.tick()
    assert result["enqueued"] == 1
    assert [j["payload"]["data"]["TicketID"] for j in _jobs(url_fixture)] == [tid]
    assert (await follower_.tick())["enqueued"] == 0
    assert len(_jobs(url_fixture)) == 1


async def test_gap_is_held_then_delivered(url_fixture: str, follower_: Follower) -> None:
    _install(url_fixture, {"Send": CREATE})
    await follower_.start()
    # Ids left over from earlier deletes are a hole in front of the cursor; a primer row that is
    # delivered without a hold makes the ids after it contiguous, then the real tracker takes over.
    _new_ticket(url_fixture, 5)
    await follower_.tick()
    follower_.tracker = GapTracker(hold_seconds=30, step=1, offset=None)
    jobs_before = len(_jobs(url_fixture))
    t_a, h_a = _new_ticket(url_fixture, 0)
    _t_b, h_b = _new_ticket(url_fixture, 1)
    t_c, h_c = _new_ticket(url_fixture, 2)
    assert (h_b, h_c) == (h_a + 1, h_a + 2)
    engine = create_engine(url_fixture)
    with engine.begin() as conn:  # a transaction that took an id and rolled back
        conn.execute(text("DELETE FROM ticket_history WHERE id = :i"), {"i": h_b})
    engine.dispose()

    first = await follower_.tick(now=1000.0)
    assert first["enqueued"] == 1 and first["cursor"] == h_a
    held = await follower_.tick(now=1010.0)
    assert held["enqueued"] == 0 and held["cursor"] == h_a
    released = await follower_.tick(now=1031.0)
    assert released["enqueued"] == 1 and released["cursor"] == h_c
    delivered = [j["payload"]["data"]["TicketID"] for j in _jobs(url_fixture)[jobs_before:]]
    assert delivered == [t_a, t_c]


async def test_broken_webservice_does_not_stop_others(
    url_fixture: str, follower_: Follower, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(url_fixture, {"Send": CREATE})
    _install(url_fixture, {"Other": CREATE}, ws_id=WS_OTHER, name="GI51Other")
    _install_broken(url_fixture)
    await follower_.start()
    _new_ticket(url_fixture, 0)
    result = await follower_.tick()
    assert result["enqueued"] == 2  # the unparsable webservice is simply absent
    assert {(j["payload"]["webservice_id"]) for j in _jobs(url_fixture)} == {WS, WS_OTHER}

    # a webservice that raises while matching must not take the others down
    real = follower.match_invokers

    async def flaky(ws_list: Any, event: Any, loader: Any, **kwargs: Any) -> Any:
        if ws_list[0].id == WS:
            raise ValueError("boom")
        return await real(ws_list, event, loader, **kwargs)

    monkeypatch.setattr(follower, "match_invokers", flaky)
    tid, _ = _new_ticket(url_fixture, 1)
    result = await follower_.tick()
    assert result["enqueued"] == 1
    newest = _jobs(url_fixture)[-1]
    assert newest["payload"]["webservice_id"] == WS_OTHER
    assert newest["payload"]["data"]["TicketID"] == tid


async def test_dynamic_field_event_name(url_fixture: str, follower_: Follower) -> None:
    listening = {
        "Type": "Ticket::Generic",
        "Events": [{"Event": "TicketDynamicFieldUpdate_GiTag", "Asynchronous": "1"}],
    }
    _install(url_fixture, {"Send": listening})
    await follower_.start()
    tid = _ticket(url_fixture, 0)
    _history(url_fixture, tid, "NewTicket")  # no invoker listens to this one
    _history(
        url_fixture,
        tid,
        "TicketDynamicFieldUpdate",
        "%%FieldName%%GiTag%%Value%%a%%OldValue%%b",
    )
    _history(url_fixture, tid, "TicketDynamicFieldUpdate", "%%FieldName%%Other%%Value%%x")
    result = await follower_.tick()
    assert (result["events"], result["enqueued"]) == (3, 1)
    (job,) = _jobs(url_fixture)
    assert job["payload"]["data"] == {
        "TicketID": tid,
        "FieldName": "GiTag",
        "Value": "a",
        "OldValue": "b",
        "UserID": 1,
        "Event": "TicketDynamicFieldUpdate_GiTag",
    }


async def test_unsupported_and_mapping_error_invokers_skipped(
    url_fixture: str, follower_: Follower
) -> None:
    _install(
        url_fixture,
        {
            "Good": CREATE,
            "Other": {"Type": "Test::Test", "Events": [{"Event": "TicketCreate"}]},
            "BadMap": {**CREATE, "MappingOutbound": {"Type": "Nope"}},
        },
    )
    await follower_.start()
    _new_ticket(url_fixture, 0)
    result = await follower_.tick()
    assert result["enqueued"] == 1
    assert [j["payload"]["invoker"] for j in _jobs(url_fixture)] == ["Good"]


async def test_condition_with_catastrophic_regex_is_a_non_match(
    url_fixture: str, follower_: Follower
) -> None:
    evil = {
        "Type": "Ticket::Generic",
        "Events": [
            {
                "Event": "TicketCreate",
                "Asynchronous": "1",
                "Condition": {
                    "Condition": {
                        "Cond1": {
                            "Type": "and",
                            "Fields": {"Title": {"Type": "Regexp", "Match": "^(a|aa)+$"}},
                        }
                    }
                },
            }
        ],
    }
    _install(url_fixture, {"Send": evil})
    await follower_.start()
    tid = _ticket(url_fixture, 0)
    engine = create_engine(url_fixture)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ticket SET title = :t WHERE id = :i"), {"t": "a" * 60 + "!", "i": tid}
        )
    engine.dispose()
    _history(url_fixture, tid)
    import structlog.testing

    with structlog.testing.capture_logs() as logs:
        result = await follower_.tick()
    assert result["events"] == 1 and result["enqueued"] == 0
    assert any(e["event"] == "gi_requester_condition_warning" for e in logs)
