"""Public chat API ``/api/chat/v1`` against a real DB on both dialects (band 48550-48599)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
import structlog
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from tests._fake_redis import FakeRedis
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tests._webchat_fixtures import (
    NOW,
    ZNUNY_MARK_TABLES,
    cleanup_band,
    enable_webchat,
    insert_config,
    mark_cleanup_first,
    seed_queue_agent,
)
from tiqora.api.app import REQUEST_COUNT, create_app
from tiqora.api.deps import get_redis
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.events.pubsub import TIQORA_EVENTS_CHANNEL
from tiqora.jobs.service import utc_now
from tiqora.webchat import public_api
from tiqora.webchat.availability import mark_agent_online, set_agent_state
from tiqora.webchat.config_cache import clear_config_cache
from tiqora.webchat.models import MIB
from tiqora.webchat.origins import own_origin, reset_public_base_url_warning
from tiqora.webchat.public_api import UPLOAD_BYTES_PER_CONFIG_DAY, UPLOAD_BYTES_PER_IP_DAY

pytestmark = pytest.mark.db

GROUP, CONFIG, AGENT, QUEUE = 48550, 48551, 48552, 48553
CONFIG_IDS = range(48550, 48600)
KEY = "wc_" + "a" * 32
ORIGIN = "https://www.example.org"
API = "/api/chat/v1"
SELF = "http://testserver"
VISITOR = "visitor_wc48_" + "x" * 10
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
CREATE: dict[str, Any] = {
    "key": KEY,
    "origin": ORIGIN,
    "visitor_id": VISITOR,
    "message": "Mein Router blinkt rot",
    "client_id": "c-1",
    "page_url": "https://www.example.org/kontakt",
    "language": "de",
}


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
            conn, config_ids=CONFIG_IDS, queue_ids=[QUEUE], group_ids=[GROUP], user_ids=[AGENT]
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

    def set_rate_limits(self, **limits: int) -> None:
        row = self.sql("SELECT rate_limits FROM tiqora_chat_config WHERE id = :i", i=CONFIG)
        data = json.loads(row[0][0])
        data.update(limits)
        self.sql(
            "UPDATE tiqora_chat_config SET rate_limits = :v WHERE id = :i",
            v=json.dumps(data),
            i=CONFIG,
        )
        clear_config_cache()

    def switch_channel(self, value: str) -> None:
        engine = create_engine(self.url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    update(TiqoraSettings)
                    .where(TiqoraSettings.key == "channel.webchat.enabled")
                    .values(value=value)
                )
        finally:
            engine.dispose()

    def set_upload_max(self, max_bytes: int) -> None:
        row = self.sql("SELECT upload_policy FROM tiqora_chat_config WHERE id = :i", i=CONFIG)
        data = json.loads(row[0][0])
        data["max_bytes"] = max_bytes
        self.sql(
            "UPDATE tiqora_chat_config SET upload_policy = :v WHERE id = :i",
            v=json.dumps(data),
            i=CONFIG,
        )
        clear_config_cache()


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    marks = snapshot_marks(url, ZNUNY_MARK_TABLES)
    engine = create_engine(url)
    with engine.begin() as conn:
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc48-pub")
        insert_config(conn, config_id=CONFIG, queue_id=QUEUE, public_key=KEY, origins=[ORIGIN])
        enable_webchat(conn)
    engine.dispose()
    clear_config_cache()
    aengine = create_async_engine(_to_async_url(url))
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    fake = FakeRedis()
    async with factory() as db:
        await set_agent_state(db, AGENT, available=True, topics=[], max_concurrent=3, now=NOW)
        await db.commit()
    await mark_agent_online(fake, AGENT)
    try:
        yield Env(url, factory, fake)
    finally:
        clear_config_cache()
        await aengine.dispose()
        delete_above_marks(url, marks, first=mark_cleanup_first(marks))
        _cleanup(url)


def _client(env: Env, **settings: Any) -> AsyncClient:
    app = create_app(Settings(environment="test", **settings))
    app.state.session_factory = env.factory
    app.dependency_overrides[get_redis] = lambda: env.redis
    return AsyncClient(transport=ASGITransport(app=app), base_url=SELF, headers={"Origin": SELF})


async def _start(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    res = await client.post(f"{API}/sessions", json={**CREATE, **overrides})
    assert res.status_code == 201, res.text
    return dict(res.json())


def _h(created: dict[str, Any]) -> dict[str, str]:
    return {"X-Chat-Token": created["token"]}


parametrize_db = pytest.mark.parametrize("env", _URLS, indirect=True)


# -- config --------------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_config_ok_and_leaks_nothing(env: Env) -> None:
    async with _client(env) as client:
        res = await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN})
    assert res.status_code == 200
    body = res.json()
    assert body["online"] is True
    assert body["topics"] == [{"key": "support", "label": "Support", "online": True}]
    assert body["max_message_length"] == 2000
    assert body["upload"]["enabled"] is True
    assert {f["key"] for f in body["prechat_fields"]} == {"name", "email"}
    assert all("maps_to" not in f and "dynamic_field" not in f for f in body["prechat_fields"])
    assert "queue_id" not in res.text and str(QUEUE) not in res.text
    assert "agent" not in res.text.lower()
    assert "wc48-config" not in res.text and str(CONFIG) not in res.text


@pytest.mark.asyncio
@parametrize_db
async def test_config_refuses_foreign_origin_param_and_disabled_channel(env: Env) -> None:
    async with _client(env) as client:
        foreign = await client.get(
            f"{API}/config", params={"k": KEY, "origin": "https://evil.example"}
        )
        assert foreign.status_code == 403
        assert foreign.json() == {"detail": "origin"}
        # R-PREVIEW: Tiqora's own origin may read the public config.
        own = await client.get(f"{API}/config", params={"k": KEY, "origin": SELF})
        assert own.status_code == 200
        unknown = await client.get(
            f"{API}/config", params={"k": "wc_" + "0" * 32, "origin": ORIGIN}
        )
        assert unknown.status_code == 404

        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=False, i=CONFIG)
        clear_config_cache()
        invalid = await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN})
        assert invalid.status_code == 404
        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=True, i=CONFIG)

        env.switch_channel("0")
        clear_config_cache()
        off = await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN})
        assert off.status_code == 404
        # R-PREVIEW-DRAFT: the own origin still reads it (admin preview before going live) ...
        own_off = await client.get(f"{API}/config", params={"k": KEY, "origin": SELF})
        assert own_off.status_code == 200
        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=False, i=CONFIG)
        clear_config_cache()
        own_invalid = await client.get(f"{API}/config", params={"k": KEY, "origin": SELF})
        assert own_invalid.status_code == 200
        # ... while foreign origins and sessions stay refused, and unknown keys stay unknown
        foreign = await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN})
        assert foreign.status_code == 404
        unknown_own = await client.get(
            f"{API}/config", params={"k": "wc_" + "0" * 32, "origin": SELF}
        )
        assert unknown_own.status_code == 404
        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=True, i=CONFIG)
        clear_config_cache()
        assert (await client.post(f"{API}/sessions", json=CREATE)).status_code == 404


# -- origin --------------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_cross_origin_post_refused(env: Env) -> None:
    async with _client(env) as client:
        res = await client.post(
            f"{API}/sessions", json=CREATE, headers={"Origin": "https://evil.example"}
        )
        assert res.status_code == 403 and res.json() == {"detail": "cross_origin"}
        assert "access-control-allow-origin" not in {k.lower() for k in res.headers}
        null = await client.post(f"{API}/sessions", json=CREATE, headers={"Origin": "null"})
        assert null.status_code == 403 and null.json() == {"detail": "cross_origin"}
        cross_site = await client.post(
            f"{API}/sessions", json=CREATE, headers={"Sec-Fetch-Site": "cross-site"}
        )
        assert cross_site.status_code == 403
        del client.headers["Origin"]
        missing = await client.post(f"{API}/sessions", json=CREATE)
        assert missing.status_code == 403 and missing.json() == {"detail": "cross_origin"}
        ok = await client.post(f"{API}/sessions", json=CREATE, headers={"Origin": SELF})
        assert ok.status_code == 201, ok.text
        # Safe methods may omit Origin.
        read = await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN})
        assert read.status_code == 200
    assert (
        env.sql("SELECT COUNT(*) FROM tiqora_chat_session WHERE config_id = :c", c=CONFIG)[0][0]
        == 1
    )


@pytest.mark.asyncio
@parametrize_db
async def test_same_origin_check_uses_public_base_url(env: Env) -> None:
    # TLS ends at the proxy: the request is http://testserver, the public origin is https.
    # R-ORIGIN compares host[:port] with the public_base_url host AND the request Host.
    async with _client(env, public_base_url="https://helpdesk.example.org") as client:
        public = await client.post(
            f"{API}/sessions", json=CREATE, headers={"Origin": "https://helpdesk.example.org"}
        )
        assert public.status_code == 201, public.text
        same_host = await client.post(
            f"{API}/sessions",
            json={**CREATE, "visitor_id": "visitor_wc48_" + "h" * 10},
            headers={"Origin": SELF},
        )
        assert same_host.status_code == 201, same_host.text
        other = await client.post(
            f"{API}/sessions", json=CREATE, headers={"Origin": "https://helpdesk.example.net"}
        )
        assert other.status_code == 403 and other.json() == {"detail": "cross_origin"}
        port = await client.post(
            f"{API}/sessions",
            json=CREATE,
            headers={"Origin": "https://helpdesk.example.org:8443"},
        )
        assert port.status_code == 403


def test_production_warns_without_public_base_url() -> None:
    reset_public_base_url_warning()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("helpdesk.example.org", 80),
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", b"helpdesk.example.org")],
        }
    )
    prod = Settings(environment="production", public_base_url="")
    with structlog.testing.capture_logs() as logs:
        assert own_origin(request, prod) == "http://helpdesk.example.org"
        own_origin(request, prod)
        own_origin(request, Settings(environment="test", public_base_url=""))
    events = [e for e in logs if e["event"] == "webchat_public_base_url_missing"]
    assert len(events) == 1
    with structlog.testing.capture_logs() as logs:
        reset_public_base_url_warning()
        own_origin(request, Settings(environment="production", public_base_url="https://h.test"))
    assert not [e for e in logs if e["event"] == "webchat_public_base_url_missing"]


# -- sessions ------------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_session_flow_and_shapes(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        assert set(created) == {"session_id", "token", "status", "message"}
        assert set(created["message"]) == {
            "seq",
            "sender",
            "body",
            "code",
            "params",
            "agent_name",
            "created",
            "attachments",
            "client_id",
        }
        assert created["status"] == "waiting"
        assert created["message"]["seq"] == 1 and created["message"]["client_id"] == "c-1"
        h = _h(created)
        sid = created["session_id"]
        posted = await client.post(
            f"{API}/sessions/{sid}/messages", json={"body": "Noch da?"}, headers=h
        )
        assert posted.status_code == 201 and posted.json()["seq"] == 2
        msgs = (
            await client.get(f"{API}/sessions/{sid}/messages", params={"after": 1}, headers=h)
        ).json()
        assert [m["body"] for m in msgs] == ["Noch da?"]
        all_msgs = (await client.get(f"{API}/sessions/{sid}/messages", headers=h)).json()
        assert [m["seq"] for m in all_msgs] == [1, 2]
        s = (await client.get(f"{API}/sessions/{sid}", headers=h)).json()
        assert s == {
            "session_id": sid,
            "status": "waiting",
            "agent_name": None,
            "topic_label": "Support",
            "can_send": True,
            "needs_contact": False,
            "last_seq": 2,
        }
        # Agent bus: chat_waiting + chat_message with the ticket's queue, ids only.
        events = [json.loads(m) for c, m in env.redis.published if c == TIQORA_EVENTS_CHANNEL]
        assert [e["type"] for e in events] == ["chat_waiting", "chat_message"]
        assert {e["queue_id"] for e in events} == {QUEUE}


@pytest.mark.asyncio
@parametrize_db
async def test_get_session_touches_visitor_last_seen(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        sid = created["session_id"]
        old = utc_now() - timedelta(hours=1)
        env.sql("UPDATE tiqora_chat_session SET visitor_last_seen = :t WHERE id = :s", t=old, s=sid)
        assert (await client.get(f"{API}/sessions/{sid}", headers=_h(created))).status_code == 200
    seen = env.sql("SELECT visitor_last_seen FROM tiqora_chat_session WHERE id = :s", s=sid)
    assert seen[0][0] > old + timedelta(minutes=30)


@pytest.mark.asyncio
@parametrize_db
async def test_wrong_token_and_unknown_session_look_identical(env: Env) -> None:
    async with _client(env) as client:
        a = await _start(client)
        b = await _start(client, visitor_id="visitor_wc48_" + "b" * 10, client_id="c-b")
        sid = a["session_id"]
        url = f"{API}/sessions/{sid}"
        answers = [
            await client.get(url, headers=_h(b)),  # another session's token
            await client.get(url, headers={"X-Chat-Token": sid + ".garbled"}),
            await client.get(url, headers={"X-Chat-Token": sid + "." + "A" * 43}),
            await client.get(url),  # no token
            await client.get(
                f"{API}/sessions/{'0' * 32}", headers={"X-Chat-Token": "0" * 32 + ".x"}
            ),
            await client.get(f"{API}/sessions/{'1' * 32}", headers=_h(a)),
            await client.post(f"{url}/messages", json={"body": "x"}, headers=_h(b)),
        ]
        assert {r.status_code for r in answers} == {404}
        assert {r.text for r in answers} == {'{"detail":"not_found"}'}
        # Purged sessions answer exactly the same.
        env.sql("UPDATE tiqora_chat_session SET purged_at = :t WHERE id = :s", t=NOW, s=sid)
        purged = await client.get(url, headers=_h(a))
        assert purged.status_code == 404 and purged.text == answers[0].text
        assert (
            await client.get(f"{API}/sessions/{b['session_id']}", headers=_h(b))
        ).status_code == 200


@pytest.mark.asyncio
@parametrize_db
async def test_token_failures_spend_auth_fail_bucket(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookups: list[str] = []
    real_lookup = public_api.get_chat_session

    async def counting_lookup(db: AsyncSession, session_id: str, **kw: Any) -> Any:
        lookups.append(session_id)
        return await real_lookup(db, session_id, **kw)

    monkeypatch.setattr(public_api, "get_chat_session", counting_lookup)
    async with _client(env) as client:
        created = await _start(client)
        sid = created["session_id"]
        wrong = {"X-Chat-Token": sid + "." + "W" * 43}
        codes = [
            (await client.get(f"{API}/sessions/{sid}", headers=wrong)).status_code
            for _ in range(60)
        ]
        assert codes == [404] * 60
        assert len(lookups) == 60
        # Bucket full: 429 before any lookup — a guess and the correct token alike.
        guess = await client.get(f"{API}/sessions/{sid}", headers=wrong)
        right = await client.get(f"{API}/sessions/{sid}", headers=_h(created))
        assert guess.status_code == 429 and right.status_code == 429
        assert int(right.headers["Retry-After"]) >= 1
        assert len(lookups) == 60
        # The window passes: the correct token works again.
        env.redis.advance(601)
        assert (await client.get(f"{API}/sessions/{sid}", headers=_h(created))).status_code == 200


@pytest.mark.asyncio
@parametrize_db
async def test_rate_limits(env: Env) -> None:
    env.set_rate_limits(sessions_per_ip_10min=2, messages_per_session_minute=5)
    async with _client(env) as client:
        first = await _start(client)
        await _start(client, client_id="c-2")
        third = await client.post(f"{API}/sessions", json={**CREATE, "client_id": "c-3"})
        assert third.status_code == 429
        assert third.json() == {"detail": "rate_limited"}
        assert int(third.headers["Retry-After"]) >= 1
        sid, h = first["session_id"], _h(first)
        codes = [
            (
                await client.post(
                    f"{API}/sessions/{sid}/messages", json={"body": f"m{i}"}, headers=h
                )
            ).status_code
            for i in range(6)
        ]
        assert codes == [201] * 5 + [429]
        # Closing has its own bucket: a visitor at the message limit can still leave.
        assert (await client.post(f"{API}/sessions/{sid}/end", headers=h)).status_code == 204
    keys = await env.redis.keys("tiqora:chat:rl:*")
    assert any(k.startswith("tiqora:chat:rl:message:") for k in keys)


@pytest.mark.asyncio
@parametrize_db
async def test_per_config_limits(env: Env) -> None:
    env.set_rate_limits(
        sessions_per_ip_10min=100,
        sessions_per_config_minute=5,
        offline_per_ip_hour=50,
        offline_per_config_hour=1,
    )
    async with _client(env) as client:
        codes = [
            (
                await client.post(
                    f"{API}/sessions",
                    json={**CREATE, "visitor_id": f"visitor_wc48_cfg_{i:04d}", "client_id": None},
                )
            ).status_code
            for i in range(6)
        ]
        assert codes == [201] * 5 + [429]
        offline = {**CREATE, "email": "erika@example.org"}
        offline.pop("client_id")
        assert (await client.post(f"{API}/offline", json=offline)).status_code == 201
        assert (await client.post(f"{API}/offline", json=offline)).status_code == 429


@pytest.mark.asyncio
@parametrize_db
async def test_redis_down_is_503(env: Env) -> None:
    async with _client(env) as client:
        env.redis.fail = True
        res = await client.post(f"{API}/sessions", json=CREATE)
        assert res.status_code == 503
        assert res.headers["Retry-After"] == "30"
        assert res.json() == {"detail": "chat_unavailable"}
        cfg = await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN})
        assert cfg.status_code == 503
    assert (
        env.sql("SELECT COUNT(*) FROM tiqora_chat_session WHERE config_id = :c", c=CONFIG)[0][0]
        == 0
    )


@pytest.mark.asyncio
@parametrize_db
async def test_body_limits(env: Env) -> None:
    async with _client(env) as client:
        big = await client.post(f"{API}/sessions", json={**CREATE, "message": "x" * (70 * 1024)})
        assert big.status_code == 413

        async def chunks() -> AsyncIterator[bytes]:
            yield b"--b\r\n"

        created = await _start(client)
        sid = created["session_id"]
        chunked = await client.post(
            f"{API}/sessions/{sid}/attachments",
            content=chunks(),
            headers={**_h(created), "Content-Type": "multipart/form-data; boundary=b"},
        )
        assert chunked.status_code == 413
        assert chunked.json() == {"detail": "upload_rejected", "errors": {"file": "size"}}


# -- typing --------------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_typing_marks_and_notifies_once_per_window(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        sid = created["session_id"]
        before = len(env.redis.published)
        assert (
            await client.post(f"{API}/sessions/{sid}/typing", headers=_h(created))
        ).status_code == 204
        assert (
            await client.post(f"{API}/sessions/{sid}/typing", headers=_h(created))
        ).status_code == 204
    assert await env.redis.get(f"tiqora:chat:vtyping:{sid}") == "1"
    typing = [
        json.loads(m)
        for c, m in env.redis.published[before:]
        if c == TIQORA_EVENTS_CHANNEL and json.loads(m)["type"] == "chat_typing"
    ]
    assert len(typing) == 1 and typing[0]["queue_id"] == QUEUE


# -- uploads -------------------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_upload_svg_rejected_and_png_ok(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        sid, h = created["session_id"], _h(created)
        svg = ("x.png", b"<svg onload='alert(1)'/>", "image/png")
        bad = await client.post(f"{API}/sessions/{sid}/attachments", files={"file": svg}, headers=h)
        assert bad.status_code == 422
        assert bad.json() == {"detail": "upload_rejected", "errors": {"file": "type"}}
        png = ("bild.png", PNG_BYTES, "image/png")
        res = await client.post(
            f"{API}/sessions/{sid}/attachments",
            files={"file": png},
            data={"client_id": "u1"},
            headers=h,
        )
        assert res.status_code == 201, res.text
        out = res.json()
        assert out["attachments"][0]["filename"] == "bild.png"
        assert out["attachments"][0]["content_type"] == "image/png"
        assert out["client_id"] == "u1" and out["body"] == "bild.png"
        two = await client.post(
            f"{API}/sessions/{sid}/attachments",
            files=[("file", png), ("file", png)],
            headers=h,
        )
        assert two.status_code == 422


@pytest.mark.asyncio
@parametrize_db
async def test_upload_too_large_413(env: Env) -> None:
    env.set_upload_max(MIB)
    async with _client(env) as client:
        created = await _start(client)
        pdf = ("gross.pdf", b"%PDF-1.4\n" + b"0" * (2 * MIB), "application/pdf")
        res = await client.post(
            f"{API}/sessions/{created['session_id']}/attachments",
            files={"file": pdf},
            headers=_h(created),
        )
        assert res.status_code == 413
        assert res.json() == {"detail": "upload_rejected", "errors": {"file": "size"}}


@pytest.mark.asyncio
@parametrize_db
async def test_upload_daily_budget_503(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        await env.redis.set(
            f"tiqora:chat:rl:upload_bytes:cfg{CONFIG}", str(UPLOAD_BYTES_PER_CONFIG_DAY), ex=3600
        )
        res = await client.post(
            f"{API}/sessions/{created['session_id']}/attachments",
            files={"file": ("bild.png", PNG_BYTES, "image/png")},
            headers=_h(created),
        )
        assert res.status_code == 503 and res.json() == {"detail": "chat_unavailable"}


@pytest.mark.asyncio
@parametrize_db
async def test_upload_read_limit_413(env: Env) -> None:
    # Content-Length stays under max_bytes + 64 KiB, so the bounded read itself refuses.
    env.set_upload_max(MIB)
    async with _client(env) as client:
        created = await _start(client)
        pdf = ("knapp.pdf", b"%PDF-1.4\n" + b"0" * (MIB + 1024), "application/pdf")
        res = await client.post(
            f"{API}/sessions/{created['session_id']}/attachments",
            files={"file": pdf},
            headers=_h(created),
        )
        assert res.status_code == 413
        assert res.json() == {"detail": "upload_rejected", "errors": {"file": "size"}}


async def _budget(env: Env) -> tuple[int, dict[str, int]]:
    cfg = await env.redis.get(f"tiqora:chat:rl:upload_bytes:cfg{CONFIG}")
    ips = {
        k: int(await env.redis.get(k) or 0)
        for k in await env.redis.keys("tiqora:chat:rl:upload_bytes_ip:*")
    }
    return int(cfg or 0), ips


@pytest.mark.asyncio
@parametrize_db
async def test_upload_budget_charges_only_accepted_files(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        sid, h = created["session_id"], _h(created)
        url = f"{API}/sessions/{sid}/attachments"
        svg = ("x.png", b"<svg onload='alert(1)'/>", "image/png")
        assert (await client.post(url, files={"file": svg}, headers=h)).status_code == 422
        assert await _budget(env) == (0, {})
        png = ("bild.png", PNG_BYTES, "image/png")
        first = await client.post(url, files={"file": png}, data={"client_id": "u1"}, headers=h)
        assert first.status_code == 201
        cfg_total, ips = await _budget(env)
        assert cfg_total == len(PNG_BYTES) and list(ips.values()) == [len(PNG_BYTES)]
        assert not [k for k in ips if "127.0.0.1" in k]
        # An idempotent retry returns the stored line and costs nothing.
        retry = await client.post(url, files={"file": png}, data={"client_id": "u1"}, headers=h)
        assert retry.status_code == 201 and retry.json()["seq"] == first.json()["seq"]
        assert await _budget(env) == (cfg_total, ips)
        # A closed chat refuses the file and costs nothing.
        assert (await client.post(f"{API}/sessions/{sid}/end", headers=h)).status_code == 204
        closed = await client.post(url, files={"file": png}, headers=h)
        assert closed.status_code == 409
        assert await _budget(env) == (cfg_total, ips)


@pytest.mark.asyncio
@parametrize_db
async def test_upload_ip_daily_cap_503(env: Env) -> None:
    assert min(100 * MIB, UPLOAD_BYTES_PER_CONFIG_DAY // 4) == UPLOAD_BYTES_PER_IP_DAY
    async with _client(env) as client:
        created = await _start(client)
        url = f"{API}/sessions/{created['session_id']}/attachments"
        png = ("bild.png", PNG_BYTES, "image/png")
        assert (await client.post(url, files={"file": png}, headers=_h(created))).status_code == 201
        (ip_key,) = await env.redis.keys("tiqora:chat:rl:upload_bytes_ip:*")
        await env.redis.set(ip_key, str(UPLOAD_BYTES_PER_IP_DAY), ex=3600)
        res = await client.post(url, files={"file": png}, headers=_h(created))
        assert res.status_code == 503 and res.json() == {"detail": "chat_unavailable"}
        assert res.headers["Retry-After"] == "30"


def _stored_uploads(env: Env, session_ids: list[str]) -> tuple[int, int]:
    """(transcript lines with attachments, article attachment rows) of these sessions."""
    lines = 0
    ticket_ids: set[int] = set()
    for sid in session_ids:
        rows = env.sql(
            "SELECT attachments, ticket_id FROM tiqora_chat_message WHERE session_id = :s", s=sid
        )
        lines += sum(1 for r in rows if r[0])
        ticket_ids |= {int(r[1]) for r in rows}
    files = 0
    for tid in ticket_ids:
        files += env.sql(
            "SELECT COUNT(*) FROM article_data_mime_attachment WHERE article_id IN"
            " (SELECT id FROM article WHERE ticket_id = :t)",
            t=tid,
        )[0][0]
    return lines, int(files)


@pytest.mark.asyncio
@parametrize_db
@pytest.mark.parametrize("which", ["ip", "config"])
async def test_upload_budget_overshoot_is_refused_and_rolled_back(env: Env, which: str) -> None:
    async with _client(env) as client:
        created = await _start(client)
        sid, h = created["session_id"], _h(created)
        url = f"{API}/sessions/{sid}/attachments"
        png = ("bild.png", PNG_BYTES, "image/png")
        assert (await client.post(url, files={"file": png}, headers=h)).status_code == 201
        (ip_key,) = await env.redis.keys("tiqora:chat:rl:upload_bytes_ip:*")
        cfg_key = f"tiqora:chat:rl:upload_bytes:cfg{CONFIG}"
        # Below the cap, so the cheap pre-read check passes; the charge would overshoot.
        if which == "ip":
            await env.redis.set(ip_key, str(UPLOAD_BYTES_PER_IP_DAY - 1), ex=3600)
        else:
            await env.redis.set(cfg_key, str(UPLOAD_BYTES_PER_CONFIG_DAY - 1), ex=3600)
        before = (await env.redis.get(cfg_key), await env.redis.get(ip_key))
        res = await client.post(url, files={"file": png}, headers=h)
        assert res.status_code == 503 and res.json() == {"detail": "chat_unavailable"}
        assert (await env.redis.get(cfg_key), await env.redis.get(ip_key)) == before
        msgs = (await client.get(f"{API}/sessions/{sid}/messages", headers=h)).json()
        assert [m["seq"] for m in msgs] == [1, 2]
    assert _stored_uploads(env, [sid]) == (1, 1)


@pytest.mark.asyncio
@parametrize_db
async def test_concurrent_uploads_never_exceed_ip_cap(env: Env) -> None:
    fits = 3
    async with _client(env) as client:
        a = await _start(client)
        b = await _start(client, visitor_id="visitor_wc48_" + "c" * 10, client_id="c-c")
        start = UPLOAD_BYTES_PER_IP_DAY - fits * len(PNG_BYTES)

        async def upload(created: dict[str, Any], i: int) -> int:
            res = await client.post(
                f"{API}/sessions/{created['session_id']}/attachments",
                files={"file": (f"bild{i}.png", PNG_BYTES, "image/png")},
                headers=_h(created),
            )
            return res.status_code

        # The IP counter key (a hash_ip digest) appears with the first accepted upload.
        first = await upload(a, 0)
        assert first == 201
        (ip_key,) = await env.redis.keys("tiqora:chat:rl:upload_bytes_ip:*")
        await env.redis.set(ip_key, str(start), ex=3600)
        codes = await asyncio.gather(*(upload(a if i % 2 else b, i) for i in range(1, 9)))
        assert sorted(codes) == [201] * fits + [503] * (8 - fits)
        total = int(await env.redis.get(ip_key) or 0)
        assert total == start + fits * len(PNG_BYTES) <= UPLOAD_BYTES_PER_IP_DAY
    lines, files = _stored_uploads(env, [a["session_id"], b["session_id"]])
    assert (lines, files) == (1 + fits, 1 + fits)


@pytest.mark.asyncio
@parametrize_db
@pytest.mark.parametrize("state", ["channel_off", "config_invalid", "config_deleted"])
async def test_disabled_sessions_keep_reads_and_close(env: Env, state: str) -> None:
    async with _client(env) as client:
        created = await _start(client)
        sid, h = created["session_id"], _h(created)
        up = await client.post(
            f"{API}/sessions/{sid}/attachments",
            files={"file": ("bild.png", PNG_BYTES, "image/png")},
            headers=h,
        )
        assert up.status_code == 201
        env.sql(
            "UPDATE tiqora_chat_session SET assigned_user_id = :u WHERE id = :s", u=AGENT, s=sid
        )
        if state == "channel_off":
            env.switch_channel("0")
        elif state == "config_invalid":
            env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=False, i=CONFIG)
        else:
            env.sql("DELETE FROM tiqora_chat_config WHERE id = :i", i=CONFIG)
        clear_config_cache()

        writes = [
            await client.post(f"{API}/sessions/{sid}/messages", json={"body": "x"}, headers=h),
            await client.post(f"{API}/sessions/{sid}/typing", headers=h),
            await client.post(
                f"{API}/sessions/{sid}/attachments",
                files={"file": ("b.png", PNG_BYTES, "image/png")},
                headers=h,
            ),
            await client.post(
                f"{API}/sessions/{sid}/contact", json={"email": "erika@example.org"}, headers=h
            ),
        ]
        assert [r.status_code for r in writes] == [404] * 4
        assert {r.text for r in writes} == {'{"detail":"disabled"}'}

        state_out = await client.get(f"{API}/sessions/{sid}", headers=h)
        assert state_out.status_code == 200
        expected_label = None if state == "config_deleted" else "Support"
        assert state_out.json()["topic_label"] == expected_label
        # A config we no longer know hides agent names.
        expected_name = None if state == "config_deleted" else "Chat"
        assert state_out.json()["agent_name"] == expected_name
        msgs = await client.get(f"{API}/sessions/{sid}/messages", headers=h)
        assert [m["seq"] for m in msgs.json()] == [1, 2]
        download = await client.get(
            f"{API}/sessions/{sid}/messages/{up.json()['seq']}/attachments/0", headers=h
        )
        assert download.status_code == 200 and download.content == PNG_BYTES
        assert (await client.post(f"{API}/sessions/{sid}/end", headers=h)).status_code == 204
        ended = (await client.get(f"{API}/sessions/{sid}", headers=h)).json()
        assert ended["status"] == "ended" and ended["can_send"] is False
        # Still not an oracle: a wrong token is the usual 404 not_found.
        wrong = await client.get(f"{API}/sessions/{sid}", headers={"X-Chat-Token": sid + ".x"})
        assert wrong.status_code == 404 and wrong.json() == {"detail": "not_found"}


@pytest.mark.asyncio
@parametrize_db
async def test_attachment_download_headers(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        sid, h = created["session_id"], _h(created)
        up = await client.post(
            f"{API}/sessions/{sid}/attachments",
            files={"file": ("n.txt", b"<html><script>x</script></html>", "text/html")},
            headers=h,
        )
        assert up.status_code == 201, up.text
        seq = up.json()["seq"]
        res = await client.get(f"{API}/sessions/{sid}/messages/{seq}/attachments/0", headers=h)
        assert res.status_code == 200
        assert res.content == b"<html><script>x</script></html>"
        assert res.headers["content-type"].split(";")[0] == "text/plain"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["content-security-policy"] == "sandbox"
        assert res.headers["content-disposition"].startswith("attachment")
        png = await client.post(
            f"{API}/sessions/{sid}/attachments",
            files={"file": ("bild.png", PNG_BYTES, "image/png")},
            headers=h,
        )
        img = await client.get(
            f"{API}/sessions/{sid}/messages/{png.json()['seq']}/attachments/0", headers=h
        )
        assert img.status_code == 200 and img.content == PNG_BYTES
        assert img.headers["content-disposition"].startswith("inline")


@pytest.mark.asyncio
@parametrize_db
async def test_attachment_of_other_session_404(env: Env) -> None:
    async with _client(env) as client:
        a = await _start(client)
        b = await _start(client, visitor_id="visitor_wc48_" + "b" * 10, client_id="c-b")
        up = await client.post(
            f"{API}/sessions/{a['session_id']}/attachments",
            files={"file": ("bild.png", PNG_BYTES, "image/png")},
            headers=_h(a),
        )
        seq = up.json()["seq"]
        own = f"{API}/sessions/{a['session_id']}/messages/{seq}/attachments/0"
        assert (await client.get(own, headers=_h(a))).status_code == 200
        answers = [
            # B's token on A's path, and B's path with A's seq/index.
            await client.get(own, headers=_h(b)),
            await client.get(
                f"{API}/sessions/{b['session_id']}/messages/{seq}/attachments/0", headers=_h(b)
            ),
            # seq of a message without attachments, and an index past the end.
            await client.get(
                f"{API}/sessions/{a['session_id']}/messages/1/attachments/0", headers=_h(a)
            ),
            await client.get(
                f"{API}/sessions/{a['session_id']}/messages/{seq}/attachments/1", headers=_h(a)
            ),
        ]
        assert [r.status_code for r in answers] == [404] * 4
        assert {r.json()["detail"] for r in answers} == {"not_found"}


# -- offline, end, contact -----------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_offline_form(env: Env) -> None:
    await env.redis.delete(f"tiqora:agent_online:{AGENT}")
    offline = {k: v for k, v in CREATE.items() if k != "client_id"}
    async with _client(env) as client:
        cfg = (await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN})).json()
        assert cfg["online"] is False and cfg["topics"][0]["online"] is False
        start = await client.post(f"{API}/sessions", json=CREATE)
        assert start.status_code == 409 and start.json() == {"detail": "offline"}
        ok = await client.post(f"{API}/offline", json={**offline, "email": "erika@example.org"})
        assert ok.status_code == 201 and ok.json() == {}
        missing = await client.post(f"{API}/offline", json=offline)
        assert missing.status_code == 422
        assert missing.json() == {"detail": "invalid_prechat", "errors": {"email": "required"}}
        empty = await client.post(f"{API}/offline", json={**offline, "email": ""})
        assert empty.json() == {"detail": "invalid_prechat", "errors": {"email": "required"}}
        fourth = await client.post(f"{API}/offline", json={**offline, "email": "e@example.org"})
        assert fourth.status_code == 429
    rows = env.sql(
        "SELECT status, end_reason, visitor_email FROM tiqora_chat_session WHERE config_id = :c",
        c=CONFIG,
    )
    assert [tuple(r) for r in rows] == [("ended", "offline_form", "erika@example.org")]


@pytest.mark.asyncio
@parametrize_db
async def test_end_and_contact(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        sid, h = created["session_id"], _h(created)
        early = await client.post(
            f"{API}/sessions/{sid}/contact", json={"email": "erika@example.org"}, headers=h
        )
        assert early.status_code == 409 and early.json() == {"detail": "contact_not_allowed"}
        assert (await client.post(f"{API}/sessions/{sid}/end", headers=h)).status_code == 204
        assert (await client.post(f"{API}/sessions/{sid}/end", headers=h)).status_code == 204
        s = (await client.get(f"{API}/sessions/{sid}", headers=h)).json()
        assert s["status"] == "ended" and s["can_send"] is False and s["needs_contact"] is True
        late = await client.post(f"{API}/sessions/{sid}/messages", json={"body": "x"}, headers=h)
        assert late.status_code == 409 and late.json() == {"detail": "session_closed"}
        msgs = (await client.get(f"{API}/sessions/{sid}/messages", headers=h)).json()
        assert msgs[-1]["sender"] == "system" and msgs[-1]["code"] == "visitor_left"
        assert msgs[-1]["body"] is None
        bad = await client.post(
            f"{API}/sessions/{sid}/contact", json={"email": "kein-at"}, headers=h
        )
        assert bad.status_code == 422 and bad.json()["errors"] == {"email": "email"}
        ok = await client.post(
            f"{API}/sessions/{sid}/contact",
            json={"name": "Erika", "email": "erika@example.org"},
            headers=h,
        )
        assert ok.status_code == 204
        s = (await client.get(f"{API}/sessions/{sid}", headers=h)).json()
        assert s["needs_contact"] is False


@pytest.mark.asyncio
@parametrize_db
async def test_response_never_contains_ticket_number(env: Env) -> None:
    texts: list[str] = []
    async with _client(env) as client:
        created = await _start(client)
        sid, h = created["session_id"], _h(created)
        texts.append(json.dumps(created))
        for res in (
            await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN}),
            await client.post(f"{API}/sessions/{sid}/messages", json={"body": "Hallo"}, headers=h),
            await client.post(
                f"{API}/sessions/{sid}/attachments",
                files={"file": ("bild.png", PNG_BYTES, "image/png")},
                headers=h,
            ),
            await client.get(f"{API}/sessions/{sid}/messages", headers=h),
            await client.get(f"{API}/sessions/{sid}", headers=h),
            await client.post(f"{API}/sessions/{sid}/end", headers=h),
            await client.get(f"{API}/sessions/{sid}", headers=h),
            await client.get(f"{API}/sessions/{sid}/messages", headers=h),
        ):
            assert res.status_code < 300, res.text
            texts.append(res.text)
    tid = env.sql("SELECT ticket_id FROM tiqora_chat_session WHERE id = :s", s=sid)[0][0]
    ticket_id = int(tid)
    tn = str(env.sql("SELECT tn FROM ticket WHERE id = :t", t=ticket_id)[0][0])
    article_ids = {
        int(r[0]) for r in env.sql("SELECT id FROM article WHERE ticket_id = :t", t=ticket_id)
    }
    forbidden_keys = {"ticket_id", "tn", "queue_id", "article_id", "user_id", "login", "email"}

    def walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            assert not (forbidden_keys & set(value)), value
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, (int, str)) and not isinstance(value, bool):
            if key in {"seq", "last_seq", "size", "index"}:
                return
            assert str(value) != str(ticket_id) and str(value) != tn
            assert str(value) not in {str(a) for a in article_ids}

    for body in texts:
        assert tn not in body
        if body:
            walk(json.loads(body))


@pytest.mark.asyncio
@parametrize_db
async def test_per_ip_limit_keys_on_client_not_proxy(env: Env) -> None:
    env.set_rate_limits(sessions_per_ip_10min=1)
    async with _client(env, trusted_proxies_raw="127.0.0.1") as client:
        seven = await client.post(
            f"{API}/sessions", json=CREATE, headers={"X-Forwarded-For": "198.51.100.7"}
        )
        eight = await client.post(
            f"{API}/sessions",
            json={**CREATE, "visitor_id": "visitor_wc48_" + "8" * 10},
            headers={"X-Forwarded-For": "198.51.100.8"},
        )
        assert seven.status_code == 201, seven.text
        assert eight.status_code == 201, eight.text
        again = await client.post(
            f"{API}/sessions",
            json={**CREATE, "visitor_id": "visitor_wc48_" + "9" * 10},
            headers={"X-Forwarded-For": "198.51.100.7"},
        )
        assert again.status_code == 429
    keys = await env.redis.keys("*")
    assert keys
    assert not [k for k in keys if "198.51.100" in k or "127.0.0.1" in k]
    ip_hashes = env.sql("SELECT ip_hash FROM tiqora_chat_session WHERE config_id = :c", c=CONFIG)
    assert len({r[0] for r in ip_hashes}) == 2


@pytest.mark.asyncio
@parametrize_db
async def test_metrics_label_collapses_session_ids(env: Env) -> None:
    async with _client(env) as client:
        sids = []
        for i in range(2):
            created = await _start(client, visitor_id=f"visitor_wc48_metric{i}", client_id=f"m-{i}")
            sids.append(created["session_id"])
            await client.get(
                f"{API}/sessions/{created['session_id']}/messages", headers=_h(created)
            )
        await client.get(f"{API}/no-such-{'z' * 20}")
    labels = {
        sample.labels["path"]
        for metric in REQUEST_COUNT.collect()
        for sample in metric.samples
        if "path" in sample.labels
    }
    assert "/api/chat/v1/sessions/{id}/messages" in labels
    assert not [lab for lab in labels if any(s in lab for s in sids)]
    assert not [lab for lab in labels if "no-such" in lab]
