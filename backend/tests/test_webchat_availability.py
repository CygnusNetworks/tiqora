"""Agent chat availability, online markers, business hours (band 48450-48489, both dialects)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._fake_redis import FakeRedis
from tests._webchat_fixtures import NOW, cleanup_band, insert_config, seed_queue_agent
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraChatSession
from tiqora.webchat import availability
from tiqora.webchat.availability import (
    AGENT_ONLINE_PREFIX,
    AgentChatState,
    active_count,
    config_availability,
    eligible_agent_ids,
    get_agent_state,
    mark_agent_online,
    set_agent_state,
    topic_ref,
    within_business_hours,
)
from tiqora.webchat.models import ChatTopic
from tiqora.webchat.store import config_values, load_config
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

GROUP, QUEUE = 48450, 48452
CONFIG, CONFIG_CACHE = 48460, 48461
CONFIG_IDS = range(48460, 48470)
A_OK, A_OFFLINE, A_RO, A_BILLING, A_FULL, A_NEW = 48453, 48454, 48455, 48456, 48457, 48451
USERS = [A_NEW, A_OK, A_OFFLINE, A_RO, A_BILLING, A_FULL]
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
TOPIC = ChatTopic(key="support", label="S", queue_id=QUEUE)


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        cleanup_band(
            conn, config_ids=CONFIG_IDS, queue_ids=[QUEUE], group_ids=[GROUP], user_ids=USERS
        )
    engine.dispose()


def _add_agent(conn: Any, user_id: int, *, rw: bool) -> None:
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:uid, :login, 'x', 'Chat', 'Agent', 1, :t, 1, :t, 1)"
        ),
        {"uid": user_id, "login": f"wc48-avail-{user_id}", "t": NOW},
    )
    for key in ("ro", "rw") if rw else ("ro",):
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
            ),
            {"uid": user_id, "gid": GROUP, "k": key, "t": NOW},
        )


@pytest_asyncio.fixture
async def factory(
    request: pytest.FixtureRequest,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        seed_queue_agent(
            conn, queue_id=QUEUE, group_id=GROUP, user_id=A_NEW, login="wc48-avail-new"
        )
        for uid, rw in (
            (A_OK, True),
            (A_OFFLINE, True),
            (A_RO, False),
            (A_BILLING, True),
            (A_FULL, True),
        ):
            _add_agent(conn, uid, rw=rw)
        insert_config(
            conn, config_id=CONFIG, queue_id=QUEUE, public_key="wc_" + "c" * 32, origins=[]
        )
        insert_config(
            conn, config_id=CONFIG_CACHE, queue_id=QUEUE, public_key="wc_" + "d" * 32, origins=[]
        )
    engine.dispose()
    aengine = create_async_engine(_to_async_url(url))
    try:
        yield async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    finally:
        # inside the loop: pooled aiomysql connections must not be closed by GC later
        await aengine.dispose()
        _cleanup(url)


def _session_row(sid: str, user_id: int) -> dict[str, Any]:
    return {
        "id": sid,
        "config_id": CONFIG,
        "topic_key": "support",
        "queue_id": QUEUE,
        "ticket_id": 48499,
        "status": "active",
        "token_hash": "0" * 64,
        "visitor_id": "v" * 16,
        "assigned_user_id": user_id,
        "visitor_last_seen": NOW,
        "created": NOW,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _URLS, indirect=True)
async def test_agent_state_defaults_and_upsert(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as db:
        assert await get_agent_state(db, A_NEW) == AgentChatState(A_NEW, False, (), 3)
        st = await set_agent_state(
            db, A_NEW, available=True, topics=["48460:support"], max_concurrent=2, now=NOW
        )
        await db.commit()
        assert st == AgentChatState(A_NEW, True, ("48460:support",), 2)
    async with factory() as db:
        got = await get_agent_state(db, A_NEW)
        assert got.topics == ("48460:support",)
        assert got.available is True and got.max_concurrent == 2
        # second upsert updates the same row
        await set_agent_state(db, A_NEW, available=False, topics=[], max_concurrent=20, now=NOW)
        await db.commit()
    async with factory() as db:
        assert await get_agent_state(db, A_NEW) == AgentChatState(A_NEW, False, (), 20)
        rows = (
            await db.execute(
                text("SELECT COUNT(*) FROM tiqora_chat_agent_state WHERE user_id = :u"),
                {"u": A_NEW},
            )
        ).scalar_one()
        assert rows == 1
        for bad in (0, 21):
            with pytest.raises(ValueError):
                await set_agent_state(
                    db, A_NEW, available=True, topics=[], max_concurrent=bad, now=NOW
                )


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _URLS, indirect=True)
async def test_eligible_agents_need_available_online_rw_capacity_topic(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as db:
        for uid in (A_OK, A_OFFLINE, A_RO, A_FULL):
            await set_agent_state(db, uid, available=True, topics=[], max_concurrent=3, now=NOW)
        await set_agent_state(
            db, A_BILLING, available=True, topics=["48460:billing"], max_concurrent=3, now=NOW
        )
        await set_agent_state(db, A_FULL, available=True, topics=[], max_concurrent=1, now=NOW)
        # an agent who is online but not available must not count either
        await set_agent_state(db, A_NEW, available=False, topics=[], max_concurrent=3, now=NOW)
        await db.execute(insert(TiqoraChatSession).values(**_session_row("a" * 32, A_FULL)))
        await db.commit()
    r = FakeRedis()
    for uid in (A_OK, A_RO, A_BILLING, A_FULL, A_NEW):
        await mark_agent_online(r, uid)
    async with factory() as db:
        assert await active_count(db, A_FULL) == 1
        assert await active_count(db, A_OK) == 0
        ids = await eligible_agent_ids(db, r, config_id=CONFIG, topic=TOPIC)
        assert ids == [A_OK]
        # the topic filter admits the billing agent for "billing" only
        billing = ChatTopic(key="billing", label="B", queue_id=QUEUE)
        assert await eligible_agent_ids(db, r, config_id=CONFIG, topic=billing) == [
            A_OK,
            A_BILLING,
        ]
        # a redis outage means nobody is online (fail closed)
        r.fail = True
        assert await eligible_agent_ids(db, r, config_id=CONFIG, topic=TOPIC) == []


@pytest.mark.asyncio
async def test_mark_agent_online_sets_ttl_and_never_raises() -> None:
    r = FakeRedis()
    await mark_agent_online(r, 7)
    assert await r.get(f"{AGENT_ONLINE_PREFIX}7") == "1"
    assert await r.ttl(f"{AGENT_ONLINE_PREFIX}7") == availability.AGENT_ONLINE_TTL_SECONDS
    r.advance(61)
    assert await r.get(f"{AGENT_ONLINE_PREFIX}7") is None
    r.fail = True
    await mark_agent_online(r, 7)  # swallowed
    assert topic_ref(1, "x") == "1:x"


def _fake_calendar(
    monkeypatch: pytest.MonkeyPatch, hours: dict[str, list[int]], seen: list[str | None]
) -> None:
    async def fake(sysconfig: Any, calendar: str | None) -> Any:
        seen.append(calendar)
        return hours, {}, {}, "Europe/Berlin"

    monkeypatch.setattr(availability, "calendar_config", fake)


@pytest.mark.asyncio
async def test_business_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    weekdays = {d: list(range(8, 17)) for d in ("Mon", "Tue", "Wed", "Thu", "Fri")}
    seen: list[str | None] = []
    _fake_calendar(monkeypatch, {**weekdays, "Sat": [], "Sun": []}, seen)
    sysconfig = SysConfig(fetch=_unused_fetch)
    monday_10 = datetime(2026, 10, 12, 8, 0)  # 10:00 Europe/Berlin (CEST)
    monday_17 = datetime(2026, 10, 12, 15, 0)  # 17:00 local: closed
    monday_0759 = datetime(2026, 10, 12, 5, 59)  # 07:59 local: closed
    sunday_10 = datetime(2026, 10, 11, 8, 0)
    assert await within_business_hours(sysconfig, "", monday_10) is True
    assert seen == [None]  # "" selects the default calendar
    assert await within_business_hours(sysconfig, "", sunday_10) is False
    assert await within_business_hours(sysconfig, "", monday_17) is False
    assert await within_business_hours(sysconfig, "", monday_0759) is False
    seen.clear()
    assert await within_business_hours(sysconfig, "2", monday_10) is True
    assert seen == ["2"]
    # no calendar at all = always open (and calendar_config is not even consulted)
    seen.clear()
    assert await within_business_hours(sysconfig, None, sunday_10) is True
    assert seen == []


@pytest.mark.asyncio
async def test_business_hours_calendar_without_hours_is_always_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_calendar(monkeypatch, {}, [])
    sysconfig = SysConfig(fetch=_unused_fetch)
    assert await within_business_hours(sysconfig, "", datetime(2026, 10, 11, 8, 0)) is True
    _fake_calendar(monkeypatch, {"Mon": [], "Sun": []}, [])
    assert await within_business_hours(sysconfig, "", datetime(2026, 10, 11, 8, 0)) is True


async def _unused_fetch(name: str) -> Any:
    raise AssertionError(f"sysconfig must not be read: {name}")


@pytest.mark.asyncio
@pytest.mark.parametrize("factory", _URLS, indirect=True)
async def test_config_availability_is_cached(
    factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    r = FakeRedis()
    await mark_agent_online(r, A_OK)
    async with factory() as db:
        await set_agent_state(db, A_OK, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    sysconfig = SysConfig(fetch=_unused_fetch)  # calendar None: never read
    async with factory() as db:
        config = await load_config(db, CONFIG_CACHE)
        assert config is not None
        values = config_values(config)
        assert values.business_calendar is None
        got = await config_availability(
            db, r, sysconfig, config=config, values=values, now=NOW, settings=get_settings()
        )
        assert got == {"support": True}
        await set_agent_state(db, A_OK, available=False, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
        again = await config_availability(
            db, r, sysconfig, config=config, values=values, now=NOW, settings=get_settings()
        )
        assert again == {"support": True}  # served from the 10 s cache
        r.advance(11)
        fresh = await config_availability(
            db, r, sysconfig, config=config, values=values, now=NOW, settings=get_settings()
        )
        assert fresh == {"support": False}
        # outside business hours every topic is offline even with an agent ready
        await set_agent_state(db, A_OK, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
        r.advance(11)
        _fake_calendar(monkeypatch, {"Mon": [8, 9]}, [])
        closed = values.model_copy(update={"business_calendar": ""})
        sunday = datetime(2026, 10, 11, 8, 0)
        assert await config_availability(
            db,
            r,
            sysconfig,
            config=config,
            values=closed,
            now=sunday,
            settings=get_settings(),
        ) == {"support": False}
        # a redis outage computes uncached instead of failing
        r.fail = True
        assert await config_availability(
            db,
            r,
            sysconfig,
            config=config,
            values=values,
            now=NOW,
            settings=get_settings(),
        ) == {"support": False}
