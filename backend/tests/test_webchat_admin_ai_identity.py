"""Admin API: chat assistant settings, website sign-in secret, portal switch
(band 54710-54739, both dialects, real HTTP routes)."""

from __future__ import annotations

import calendar
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_bot_fixtures import cleanup_bot_queue, seed_bot_queue
from tests._webchat_fixtures import (
    NOW,
    ZNUNY_MARK_TABLES,
    cleanup_band,
    enable_webchat,
    insert_config,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_redis
from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.jobs.service import utc_now
from tiqora.webchat.availability import mark_agent_online, set_agent_state
from tiqora.webchat.bot import BOT_ALIVE_KEY
from tiqora.webchat.config_cache import clear_config_cache, public_config_cache
from tiqora.webchat.identity import sign_identity_token
from tiqora.webchat.models import ChatTopic

pytestmark = pytest.mark.db

GROUP, QUEUE, QUEUE2, GROUP2 = 54710, 54711, 54712, 54713
SERVICE, SERVICE2, ADMIN_ID, PLAIN_ID, AGENT = 54714, 54715, 54716, 54717, 54718
STATUS_CONFIG = 54720
CONFIG_IDS = range(54710, 54740)
STATUS_KEY = "wc_" + "s" * 32
SECRET_KEY = "unit-test-secret-key"
ORIGIN = "https://www.example.org"
SELF = "http://testserver"
BASE = "/api/v1/admin/chat-configs"
API = "/api/chat/v1"
TOPIC = {"key": "support", "label": "Support", "queue_id": QUEUE}
FIRST_LINE = {**TOPIC, "ai_mode": "first_line"}
URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
parametrize_db = pytest.mark.parametrize("env", URLS, indirect=True)


def _async_url(url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if url.startswith(old):
            return url.replace(old, new, 1)
    return url


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        like = {"p": "%wc547%"}
        conn.execute(
            text(
                "DELETE FROM tiqora_chat_session WHERE config_id IN"
                " (SELECT id FROM tiqora_chat_config WHERE name LIKE :p)"
            ),
            like,
        )
        conn.execute(text("DELETE FROM tiqora_chat_config WHERE name LIKE :p"), like)
        cleanup_bot_queue(conn, queue_ids=[QUEUE, QUEUE2])
        cleanup_band(
            conn,
            config_ids=CONFIG_IDS,
            queue_ids=[QUEUE, QUEUE2],
            group_ids=[GROUP, GROUP2],
            user_ids=[SERVICE, SERVICE2, ADMIN_ID, PLAIN_ID, AGENT],
        )
    engine.dispose()


@dataclass
class Env:
    url: str
    factory: async_sessionmaker[AsyncSession]
    redis: FakeRedis

    def sql(self, statement: str, **params: Any) -> list[Any]:
        engine = create_engine(self.url)
        try:
            with engine.begin() as conn:
                result = conn.execute(text(statement), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            engine.dispose()


def _insert_user(conn: Any, user_id: int, login: str) -> None:
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :l, 'x', 'Chat', 'Agent', 1, :t, 1, :t, 1)"
        ),
        {"u": user_id, "l": login, "t": NOW},
    )


def _grant(conn: Any, user_id: int, group_id: int) -> None:
    conn.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
        ),
        {"u": user_id, "g": group_id, "t": NOW},
    )


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    clear_config_cache()
    marks = snapshot_marks(url, ZNUNY_MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=SERVICE, login="wc547-bot")
        seed_queue_agent(
            conn, queue_id=QUEUE2, group_id=GROUP2, user_id=SERVICE2, login="wc547-bot2"
        )
        for uid, login in ((PLAIN_ID, "wc547-plain"), (ADMIN_ID, "wc547-admin")):
            _insert_user(conn, uid, login)
        _insert_user(conn, AGENT, "wc547-agent")
        _grant(conn, AGENT, GROUP)
        admin_gid = conn.execute(
            text("SELECT id FROM permission_groups WHERE name = 'admin'")
        ).scalar_one()
        _grant(conn, ADMIN_ID, int(admin_gid))
        enable_webchat(conn)
    engine.dispose()
    aengine = create_async_engine(_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    redis = FakeRedis()
    async with factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    await mark_agent_online(redis, AGENT)
    try:
        yield Env(url, factory, redis)
    finally:
        clear_config_cache()
        await aengine.dispose()
        delete_above_marks(url, marks, first=mark_cleanup_first(marks))
        _cleanup(url)


@pytest.fixture(autouse=True)
def llm_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tiqora.webchat.bot.build_task_llm", AsyncMock(return_value=object()))


def _client(env: Env, user_id: int = ADMIN_ID) -> AsyncClient:
    app = create_app(Settings(environment="test", secret_key=SECRET_KEY))
    app.state.session_factory = env.factory
    user = AuthenticatedUser(
        id=user_id, login=f"wc547-{user_id}", first_name="W", last_name="C", auth_method="session"
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_redis] = lambda: env.redis
    return AsyncClient(transport=ASGITransport(app=app), base_url=SELF, headers={"Origin": SELF})


def _body(name: str = "Website wc547", *, valid: bool = True, **values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {"allowed_origins": [ORIGIN], "topics": [TOPIC]}
    merged.update(values)
    return {"name": name, "valid": valid, "values": merged}


async def _create(client: AsyncClient, name: str = "Website wc547", **kw: Any) -> dict[str, Any]:
    res = await client.post(BASE, json=_body(name, **kw))
    assert res.status_code == 201, res.text
    return dict(res.json())


def _token(secret: str, key: str, sub: str = "wc547-nobody@example.org") -> str:
    payload = {
        "sub": sub,
        "exp": calendar.timegm(utc_now().utctimetuple()) + 3600,
        "aud": key,
    }
    return sign_identity_token(payload, secret.encode())


# -- identity secret -------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_create_identity_secret_shown_once(env: Env) -> None:
    async with _client(env) as client:
        out = await _create(client)
        assert out["identity_secret_set"] is False
        res = await client.post(f"{BASE}/{out['id']}/identity-secret")
        assert res.status_code == 201, res.text
        secret = res.json()["secret"]
        assert len(secret) >= 32
        got = await client.get(f"{BASE}/{out['id']}")
        listed = await client.get(BASE)
    assert got.json()["identity_secret_set"] is True
    assert secret not in got.text and secret not in listed.text
    stored = env.sql("SELECT identity_secret FROM tiqora_chat_config WHERE id = :i", i=out["id"])
    assert stored[0][0] and stored[0][0] != secret
    assert decrypt_secret(SECRET_KEY, stored[0][0]) == secret


@pytest.mark.asyncio
@parametrize_db
async def test_rotate_invalidates_old_tokens(env: Env) -> None:
    async with _client(env) as client:
        out = await _create(client)
        key = out["public_key"]
        old = (await client.post(f"{BASE}/{out['id']}/identity-secret")).json()["secret"]
        create = {
            "key": key,
            "origin": ORIGIN,
            "message": "Mein Router blinkt rot",
            "client_id": "c-1",
        }
        ok = await client.post(
            f"{API}/sessions",
            json={**create, "visitor_id": "visitor_wc547_0000000001", "identity": _token(old, key)},
        )
        assert ok.status_code == 201, ok.text
        new = (await client.post(f"{BASE}/{out['id']}/identity-secret")).json()["secret"]
        assert new != old
        stale = await client.post(
            f"{API}/sessions",
            json={**create, "visitor_id": "visitor_wc547_0000000002", "identity": _token(old, key)},
        )
        fresh = await client.post(
            f"{API}/sessions",
            json={**create, "visitor_id": "visitor_wc547_0000000003", "identity": _token(new, key)},
        )
    assert stale.status_code == 422 and stale.json() == {"detail": "invalid_identity"}
    assert fresh.status_code == 201, fresh.text


@pytest.mark.asyncio
@parametrize_db
async def test_delete_identity_secret(env: Env) -> None:
    async with _client(env) as client:
        out = await _create(client)
        await client.post(f"{BASE}/{out['id']}/identity-secret")
        res = await client.delete(f"{BASE}/{out['id']}/identity-secret")
        assert res.status_code == 204
        got = (await client.get(f"{BASE}/{out['id']}")).json()
        missing = await client.delete(f"{BASE}/999999/identity-secret")
    assert got["identity_secret_set"] is False
    stored = env.sql("SELECT identity_secret FROM tiqora_chat_config WHERE id = :i", i=out["id"])
    assert stored[0][0] is None
    assert missing.status_code == 404


@pytest.mark.asyncio
@parametrize_db
async def test_config_cache_cleared_on_secret_change(env: Env) -> None:
    async with _client(env) as client:
        out = await _create(client)
        key = out["public_key"]
        async with env.factory() as db:
            before = await public_config_cache.by_key(db, key)
        assert before is not None and before.config.identity_secret is None
        await client.post(f"{BASE}/{out['id']}/identity-secret")
        async with env.factory() as db:
            after = await public_config_cache.by_key(db, key)
        assert after is not None and after.config.identity_secret
        await client.delete(f"{BASE}/{out['id']}/identity-secret")
        async with env.factory() as db:
            gone = await public_config_cache.by_key(db, key)
    assert gone is not None and gone.config.identity_secret is None


# -- save-time checks ------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_only_one_portal_chat(env: Env) -> None:
    message = "Es kann nur ein Chat im Kundenportal angezeigt werden."
    async with _client(env) as client:
        first = await _create(client, "Portal wc547 a", show_on_portal=True)
        res = await client.post(BASE, json=_body("Portal wc547 b", show_on_portal=True))
        assert res.status_code == 422
        assert res.json() == {"detail": {"errors": {"show_on_portal": message}}}
        # an invalid duplicate is fine; switching it valid is not
        second = await _create(client, "Portal wc547 c", show_on_portal=True, valid=False)
        again = await client.patch(f"{BASE}/{second['id']}", json={"valid": True})
        assert again.status_code == 422
        assert again.json() == {"detail": {"errors": {"show_on_portal": message}}}
        # the portal chat itself may be saved again
        same = await client.patch(
            f"{BASE}/{first['id']}", json={"values": {**first["values"], "greeting": "Hallo"}}
        )
        assert same.status_code == 200, same.text
    rows = env.sql(
        "SELECT id FROM tiqora_chat_config WHERE show_on_portal = :t AND valid = :t"
        " AND name LIKE '%wc547%'",
        t=True,
    )
    assert [r[0] for r in rows] == [first["id"]]


@pytest.mark.asyncio
@parametrize_db
async def test_offline_ai_requires_first_line_topic(env: Env) -> None:
    async with _client(env) as client:
        res = await client.post(BASE, json=_body(offline_mode="ai"))
        assert res.status_code == 422
        errors = res.json()["detail"]["errors"]
        assert list(errors) == ["offline_mode"]
        assert "KI antwortet zuerst" in errors["offline_mode"]
        out = await _create(client, offline_mode="ai", topics=[FIRST_LINE])
        assert out["values"]["offline_mode"] == "ai"
        # taking the first-line topic away again is refused
        bad = await client.patch(
            f"{BASE}/{out['id']}", json={"values": {**out["values"], "topics": [TOPIC]}}
        )
    assert bad.status_code == 422 and list(bad.json()["detail"]["errors"]) == ["offline_mode"]
    stored = env.sql("SELECT offline_mode FROM tiqora_chat_config WHERE id = :i", i=out["id"])
    assert stored[0][0] == "ai"


@pytest.mark.asyncio
@parametrize_db
async def test_new_value_fields_roundtrip(env: Env) -> None:
    async with _client(env) as client:
        out = await _create(client)
        values = {
            **out["values"],
            "topics": [FIRST_LINE],
            "bot_name": "Robin",
            "bot_tone_prompt": "Freundlich und knapp.",
            "show_on_portal": True,
            "identity_create_customers": True,
            "offline_mode": "ai",
        }
        res = await client.patch(f"{BASE}/{out['id']}", json={"values": values})
        assert res.status_code == 200, res.text
        got = (await client.get(f"{BASE}/{out['id']}")).json()["values"]
    assert got["bot_name"] == "Robin"
    assert got["bot_tone_prompt"] == "Freundlich und knapp."
    assert got["show_on_portal"] is True
    assert got["identity_create_customers"] is True
    assert got["offline_mode"] == "ai"
    assert got["topics"][0]["ai_mode"] == "first_line"


# -- AI status -------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_ai_status_reports_reasons(env: Env) -> None:
    engine = create_engine(env.url)
    with engine.begin() as conn:
        seed_bot_queue(conn, queue_id=QUEUE, service_user_id=SERVICE)
        seed_bot_queue(conn, queue_id=QUEUE2, service_user_id=SERVICE2, auto_reply=False)
        insert_config(
            conn,
            config_id=STATUS_CONFIG,
            queue_id=QUEUE,
            public_key=STATUS_KEY,
            origins=[ORIGIN],
            topics=[
                ChatTopic(key="ok", label="Ok", queue_id=QUEUE, ai_mode="first_line"),
                ChatTopic(key="off", label="Aus", queue_id=QUEUE2, ai_mode="first_line"),
                ChatTopic(key="human", label="Mensch", queue_id=QUEUE, ai_mode="off"),
            ],
        )
    engine.dispose()
    await env.redis.set(BOT_ALIVE_KEY, "1", ex=30)
    async with _client(env) as client:
        res = await client.get(f"{BASE}/{STATUS_CONFIG}/ai-status")
        assert res.status_code == 200, res.text
        body = res.json()
        assert isinstance(body, list)
        assert {t["topic_key"]: t["queue_name"] for t in body} == {
            "ok": f"wc48-queue-{QUEUE}",
            "off": f"wc48-queue-{QUEUE2}",
            "human": f"wc48-queue-{QUEUE}",
        }
        alive = {t["topic_key"]: (t["ok"], t["reason"]) for t in body}
        await env.redis.delete(BOT_ALIVE_KEY)
        down = {
            t["topic_key"]: (t["ok"], t["reason"])
            for t in (await client.get(f"{BASE}/{STATUS_CONFIG}/ai-status")).json()
        }
        missing = await client.get(f"{BASE}/999999/ai-status")
    assert alive == {
        "ok": (True, None),
        "off": (False, "auto_reply_off"),
        "human": (False, "ai_mode_off"),
    }
    assert down["ok"] == (False, "worker_down")
    assert down["off"] == (False, "auto_reply_off")
    assert missing.status_code == 404


# -- access ----------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_admin_only(env: Env) -> None:
    async with _client(env) as admin:
        out = await _create(admin)
    calls = []
    # AGENT holds rw on the queue group: queue rights must not grant admin.
    for uid in (PLAIN_ID, AGENT):
        async with _client(env, uid) as plain:
            calls += [
                await plain.post(f"{BASE}/{out['id']}/identity-secret"),
                await plain.delete(f"{BASE}/{out['id']}/identity-secret"),
                await plain.get(f"{BASE}/{out['id']}/ai-status"),
            ]
    assert [c.status_code for c in calls] == [403] * 6
    stored = env.sql("SELECT identity_secret FROM tiqora_chat_config WHERE id = :i", i=out["id"])
    assert stored[0][0] is None
