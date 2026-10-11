"""Identity endpoints of the public chat API against a real DB (band 54460-54499, logins ``wc546``).

Website sign-in tokens on ``POST /sessions`` and ``POST /sessions/{id}/identify``, the portal
assertion on ``POST /identity/portal`` and ``GET /api/portal/webchat``.
"""

from __future__ import annotations

import calendar
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
from tiqora.api.app import create_app
from tiqora.api.deps import get_redis
from tiqora.config import Settings
from tiqora.crypto.secret import encrypt_secret
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.customer_auth import CustomerSessionStore
from tiqora.jobs.service import utc_now
from tiqora.webchat.availability import mark_agent_online, set_agent_state
from tiqora.webchat.config_cache import clear_config_cache
from tiqora.webchat.identity import sign_identity_token
from tiqora.webchat.store import config_values, load_config
from tiqora.webchat.visitor_identity import verify_and_resolve

pytestmark = pytest.mark.db

GROUP, CONFIG, AGENT, QUEUE = 54460, 54461, 54462, 54463
CONFIG_IDS = range(54460, 54500)
KEY = "wc_" + "j" * 32
ORIGIN = "https://www.example.org"
API = "/api/chat/v1"
SELF = "http://testserver"
SECRET_KEY = "unit-test-secret-key"
WEBSITE_SECRET = "wc546-website-secret"
VISITOR = "visitor_wc546_" + "x" * 8
COOKIE = "tiqora_customer_session"
_URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
CREATE: dict[str, Any] = {
    "key": KEY,
    "origin": ORIGIN,
    "visitor_id": VISITOR,
    "message": "Mein Router blinkt rot",
    "client_id": "c-1",
}
parametrize_db = pytest.mark.parametrize("env", _URLS, indirect=True)


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
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE 'wc546-%'"))
    engine.dispose()


def _seed_customer(conn: Any, *, login: str, first: str, last: str) -> None:
    conn.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, pw, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:l, :e, 'wc546-co', NULL, :f, :s, 1, :t, 1, :t, 1)"
        ),
        {"l": login, "e": f"{login}@example.org", "f": first, "s": last, "t": NOW},
    )


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

    def set_portal(self, value: bool) -> None:
        self.sql(
            "UPDATE tiqora_chat_config SET show_on_portal = :v WHERE id = :i", v=value, i=CONFIG
        )
        clear_config_cache()

    def set_channel(self, value: str) -> None:
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
        clear_config_cache()

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

    def customer_id(self, login: str) -> int:
        return int(self.sql("SELECT id FROM customer_user WHERE login = :l", l=login)[0][0])

    def chat_count(self) -> int:
        return int(
            self.sql("SELECT COUNT(*) FROM tiqora_chat_session WHERE config_id = :c", c=CONFIG)[0][
                0
            ]
        )

    def ticket_count(self) -> int:
        return int(self.sql("SELECT COUNT(*) FROM ticket WHERE queue_id = :q", q=QUEUE)[0][0])

    def fail_bucket(self) -> int:
        keys = [k for k in self.redis._data if "identity_fail" in k]
        return sum(int(self.redis._data[k]) for k in keys)


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
        seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=AGENT, login="wc546-agent")
        insert_config(conn, config_id=CONFIG, queue_id=QUEUE, public_key=KEY, origins=[ORIGIN])
        conn.execute(
            text("UPDATE tiqora_chat_config SET identity_secret = :s WHERE id = :i"),
            {"s": encrypt_secret(SECRET_KEY, WEBSITE_SECRET), "i": CONFIG},
        )
        _seed_customer(conn, login="wc546-erika", first="Erika", last="Muster")
        _seed_customer(conn, login="wc546-hans", first="Hans", last="Beispiel")
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


def _settings(**extra: Any) -> Settings:
    return Settings(environment="test", secret_key=SECRET_KEY, portal_enabled=True, **extra)


def _client(env: Env, **settings: Any) -> AsyncClient:
    app = create_app(_settings(**settings))
    app.state.session_factory = env.factory
    app.dependency_overrides[get_redis] = lambda: env.redis
    return AsyncClient(transport=ASGITransport(app=app), base_url=SELF, headers={"Origin": SELF})


def _now() -> int:
    return calendar.timegm(utc_now().utctimetuple())


def _website_token(sub: str = "wc546-erika", *, secret: str = WEBSITE_SECRET, **claims: Any) -> str:
    payload = {"sub": sub, "exp": _now() + 3600, "aud": KEY, **claims}
    return sign_identity_token(payload, secret.encode())


async def _portal_cookie(env: Env, login: str = "wc546-erika", *, pending: bool = False) -> str:
    store = CustomerSessionStore(env.redis, _settings())  # type: ignore[arg-type]
    cid = env.customer_id(login)
    token = (
        await store.create_pending(cid, login, 300) if pending else await store.create(cid, login)
    )
    return f"{COOKIE}={token}"


def _h(created: dict[str, Any]) -> dict[str, str]:
    return {"X-Chat-Token": created["token"]}


async def _start(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    res = await client.post(f"{API}/sessions", json={**CREATE, **overrides})
    assert res.status_code == 201, res.text
    return dict(res.json())


# -- POST /sessions --------------------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_create_with_valid_identity_201_and_identified_as(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client, identity=_website_token())
        res = await client.get(f"{API}/sessions/{created['session_id']}", headers=_h(created))
    assert res.status_code == 200
    assert res.json()["identified_as"] == "Erika Muster"
    for leaked in ("wc546-erika", "example.org"):
        assert leaked not in res.text and leaked not in json.dumps(created)
    row = env.sql(
        "SELECT identity_source, customer_user_login, visitor_name FROM tiqora_chat_session"
        " WHERE id = :i",
        i=created["session_id"],
    )[0]
    assert tuple(row) == ("website", "wc546-erika", "Erika Muster")
    ticket_id = env.sql(
        "SELECT ticket_id FROM tiqora_chat_session WHERE id = :i", i=created["session_id"]
    )[0][0]
    ticket = env.sql("SELECT customer_user_id FROM ticket WHERE id = :t", t=ticket_id)
    assert ticket[0][0] == "wc546-erika"


@pytest.mark.asyncio
@parametrize_db
async def test_create_without_identity_is_anonymous(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        res = await client.get(f"{API}/sessions/{created['session_id']}", headers=_h(created))
    assert res.json()["identified_as"] is None
    row = env.sql(
        "SELECT identity_source FROM tiqora_chat_session WHERE id = :i", i=created["session_id"]
    )
    assert row[0][0] is None


@pytest.mark.asyncio
@parametrize_db
async def test_create_with_invalid_identity_422_nothing_written(env: Env) -> None:
    tampered = _website_token(secret="not-the-secret")
    chats, tickets = env.chat_count(), env.ticket_count()
    assert env.fail_bucket() == 0
    async with _client(env) as client:
        res = await client.post(f"{API}/sessions", json={**CREATE, "identity": tampered})
        garbage = await client.post(f"{API}/sessions", json={**CREATE, "identity": "a.b.c"})
        foreign = await client.post(
            f"{API}/sessions",
            json={**CREATE, "identity": _website_token(), "origin": "https://evil.example"},
        )
    assert res.status_code == 422 and res.json() == {"detail": "invalid_identity"}
    assert garbage.status_code == 422 and garbage.json() == {"detail": "invalid_identity"}
    # a foreign website is refused before any signature work: no failure is counted for it
    assert foreign.status_code == 403 and foreign.json() == {"detail": "origin"}
    assert (env.chat_count(), env.ticket_count()) == (chats, tickets)
    assert env.fail_bucket() == 2


@pytest.mark.asyncio
@parametrize_db
async def test_identity_fail_bucket_limits(env: Env) -> None:
    env.set_rate_limits(sessions_per_ip_10min=100, sessions_per_config_minute=300)
    tampered = _website_token(secret="not-the-secret")
    async with _client(env) as client:
        for i in range(30):
            res = await client.post(
                f"{API}/sessions",
                json={**CREATE, "visitor_id": f"visitor_wc546_{i:010d}", "identity": tampered},
            )
            assert res.status_code == 422, (i, res.text)
        # the 31st failure is refused BEFORE verification: a correct token gets 429 too
        blocked = await client.post(
            f"{API}/sessions",
            json={**CREATE, "visitor_id": "visitor_wc546_0000000099", "identity": _website_token()},
        )
    assert blocked.status_code == 429 and "retry-after" in blocked.headers
    assert blocked.json() == {"detail": "rate_limited"}
    assert env.chat_count() == 0


# -- POST /sessions/{id}/identify ------------------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_identify_endpoint(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        url = f"{API}/sessions/{created['session_id']}/identify"
        ok = await client.post(url, json={"identity": _website_token()}, headers=_h(created))
        assert ok.status_code == 204
        state = await client.get(f"{API}/sessions/{created['session_id']}", headers=_h(created))
        assert state.json()["identified_as"] == "Erika Muster"
        same = await client.post(url, json={"identity": _website_token()}, headers=_h(created))
        assert same.status_code == 204
        other = await client.post(
            url, json={"identity": _website_token("wc546-hans")}, headers=_h(created)
        )
        assert other.status_code == 409 and other.json() == {"detail": "identity_conflict"}
        bad = await client.post(
            url, json={"identity": _website_token(secret="nope")}, headers=_h(created)
        )
        assert bad.status_code == 422 and bad.json() == {"detail": "invalid_identity"}
    row = env.sql(
        "SELECT customer_user_login, identity_source FROM tiqora_chat_session WHERE id = :i",
        i=created["session_id"],
    )[0]
    assert tuple(row) == ("wc546-erika", "website")
    assert env.fail_bucket() == 1


@pytest.mark.asyncio
@parametrize_db
async def test_identify_requires_token_header(env: Env) -> None:
    async with _client(env) as client:
        created = await _start(client)
        url = f"{API}/sessions/{created['session_id']}/identify"
        body = {"identity": _website_token()}
        wrong = await client.post(url, json=body, headers={"X-Chat-Token": "x" * 40})
        missing = await client.post(url, json=body)
    assert wrong.status_code == 404 and wrong.json() == {"detail": "not_found"}
    assert missing.status_code == 404 and missing.json() == {"detail": "not_found"}
    row = env.sql(
        "SELECT identity_source FROM tiqora_chat_session WHERE id = :i", i=created["session_id"]
    )
    assert row[0][0] is None


@pytest.mark.asyncio
@parametrize_db
async def test_identify_full_post_real_session(env: Env) -> None:
    """R-TX: the whole POST on a real DB session (no InvalidRequestError) changes the ticket."""
    async with _client(env) as client:
        created = await _start(client)
        res = await client.post(
            f"{API}/sessions/{created['session_id']}/identify",
            json={"identity": _website_token()},
            headers=_h(created),
        )
    assert res.status_code == 204, res.text
    sess = env.sql(
        "SELECT ticket_id, visitor_email FROM tiqora_chat_session WHERE id = :i",
        i=created["session_id"],
    )[0]
    tick = env.sql("SELECT customer_user_id, customer_id FROM ticket WHERE id = :t", t=sess[0])[0]
    assert (tick[0], tick[1], sess[1]) == ("wc546-erika", "wc546-co", "wc546-erika@example.org")


# -- POST /identity/portal -------------------------------------------------------------------

PORTAL = {"key": KEY, "origin": SELF, "visitor_id": VISITOR}


@pytest.mark.asyncio
@parametrize_db
async def test_portal_identity_requires_own_origin_and_portal_flag(env: Env) -> None:
    cookie = {"Cookie": await _portal_cookie(env)}
    async with _client(env) as client:
        # the chat is not shown on the portal yet
        off = await client.post(f"{API}/identity/portal", json=PORTAL, headers=cookie)
        assert off.status_code == 404 and off.json() == {"detail": "not_found"}

        env.set_portal(True)
        foreign = await client.post(
            f"{API}/identity/portal", json={**PORTAL, "origin": ORIGIN}, headers=cookie
        )
        assert foreign.status_code == 404 and foreign.json() == {"detail": "not_found"}
        unknown = await client.post(
            f"{API}/identity/portal", json={**PORTAL, "key": "wc_" + "0" * 32}, headers=cookie
        )
        assert unknown.status_code == 404
        no_cookie = await client.post(f"{API}/identity/portal", json=PORTAL)
        assert no_cookie.status_code == 404 and no_cookie.json() == {"detail": "not_found"}
        pending = {"Cookie": await _portal_cookie(env, pending=True)}
        not_yet = await client.post(f"{API}/identity/portal", json=PORTAL, headers=pending)
        assert not_yet.status_code == 404 and not_yet.json() == {"detail": "not_found"}
        junk = await client.post(
            f"{API}/identity/portal", json=PORTAL, headers={"Cookie": f"{COOKIE}=nope"}
        )
        assert junk.status_code == 404

        ok = await client.post(f"{API}/identity/portal", json=PORTAL, headers=cookie)
        assert ok.status_code == 200, ok.text
        body = ok.json()
    assert body["name"] == "Erika Muster"
    async with env.factory() as db:
        row = await load_config(db, CONFIG)
        assert row is not None
        resolved = await verify_and_resolve(
            db,
            _settings(),
            config=row,
            values=config_values(row),
            token=body["identity"],
            visitor_id=VISITOR,
            now=utc_now(),
        )
    assert (resolved.source, resolved.customer_user_login, resolved.name) == (
        "portal",
        "wc546-erika",
        "Erika Muster",
    )


@pytest.mark.asyncio
@parametrize_db
async def test_portal_identity_off_when_portal_disabled(env: Env) -> None:
    env.set_portal(True)
    cookie = {"Cookie": await _portal_cookie(env)}
    app = create_app(Settings(environment="test", secret_key=SECRET_KEY, portal_enabled=False))
    app.state.session_factory = env.factory
    app.dependency_overrides[get_redis] = lambda: env.redis
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=SELF, headers={"Origin": SELF}
    ) as client:
        res = await client.post(f"{API}/identity/portal", json=PORTAL, headers=cookie)
    assert res.status_code == 404 and res.json() == {"detail": "not_found"}


@pytest.mark.asyncio
@parametrize_db
async def test_portal_assertion_creates_identified_chat(env: Env) -> None:
    env.set_portal(True)
    cookie = {"Cookie": await _portal_cookie(env)}
    async with _client(env) as client:
        asserted = (await client.post(f"{API}/identity/portal", json=PORTAL, headers=cookie)).json()
        created = await _start(client, origin=SELF, identity=asserted["identity"])
        mismatch = await client.post(
            f"{API}/sessions",
            json={
                **CREATE,
                "origin": SELF,
                "visitor_id": "visitor_wc546_other_vis",
                "identity": asserted["identity"],
            },
        )
    assert mismatch.status_code == 422 and mismatch.json() == {"detail": "invalid_identity"}
    row = env.sql(
        "SELECT identity_source, customer_user_login FROM tiqora_chat_session WHERE id = :i",
        i=created["session_id"],
    )[0]
    assert tuple(row) == ("portal", "wc546-erika")


@pytest.mark.asyncio
@parametrize_db
async def test_other_chat_endpoints_ignore_portal_cookie(env: Env) -> None:
    env.set_portal(True)
    cookie = {"Cookie": await _portal_cookie(env)}
    async with _client(env) as client:
        res = await client.post(f"{API}/sessions", json={**CREATE, "origin": SELF}, headers=cookie)
        assert res.status_code == 201, res.text
        created = res.json()
        state = await client.get(
            f"{API}/sessions/{created['session_id']}", headers={**_h(created), **cookie}
        )
    assert state.json()["identified_as"] is None
    row = env.sql(
        "SELECT identity_source, customer_user_login FROM tiqora_chat_session WHERE id = :i",
        i=created["session_id"],
    )[0]
    assert row[0] is None and row[1] != "wc546-erika"


# -- GET /config and GET /api/portal/webchat -------------------------------------------------


@pytest.mark.asyncio
@parametrize_db
async def test_config_portal_identity_flag(env: Env) -> None:
    async with _client(env) as client:
        params_own = {"k": KEY, "origin": SELF}
        before = await client.get(f"{API}/config", params=params_own)
        assert before.status_code == 200 and before.json()["portal_identity"] is False
        env.set_portal(True)
        own = await client.get(f"{API}/config", params=params_own)
        assert own.json()["portal_identity"] is True
        # a customer website never gets the portal path, whatever the flag says
        site = await client.get(f"{API}/config", params={"k": KEY, "origin": ORIGIN})
        assert site.status_code == 200 and site.json()["portal_identity"] is False
        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=False, i=CONFIG)
        clear_config_cache()
        draft = await client.get(f"{API}/config", params=params_own)
        assert draft.status_code == 200 and draft.json()["portal_identity"] is False


@pytest.mark.asyncio
@parametrize_db
async def test_portal_webchat_endpoint(env: Env) -> None:
    cookie = {"Cookie": await _portal_cookie(env)}
    async with _client(env) as client:
        anonymous = await client.get("/api/portal/webchat")
        assert anonymous.status_code == 401
        none = await client.get("/api/portal/webchat", headers=cookie)
        assert none.status_code == 404
        env.set_portal(True)
        ok = await client.get("/api/portal/webchat", headers=cookie)
        assert ok.status_code == 200 and ok.json() == {"public_key": KEY}
        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=False, i=CONFIG)
        invalid = await client.get("/api/portal/webchat", headers=cookie)
        assert invalid.status_code == 404
        env.sql("UPDATE tiqora_chat_config SET valid = :v WHERE id = :i", v=True, i=CONFIG)
        env.set_channel("false")
        off = await client.get("/api/portal/webchat", headers=cookie)
        assert off.status_code == 404
