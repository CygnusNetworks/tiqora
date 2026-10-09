"""Customer portal second factors: TOTP and passkeys (Task 22).

ASGI + DB. Seed band 47150-47179 (Ruling T20): customer ids 47150-47159 (explicit
``customer_user.id``), the same numbers double as agent ids for the namespacing test
(``tiqora_user_totp`` has no FK), customer logins ``idp.2fa.*@example.org``,
provider slug ``idptest-2fa-*``. Erasure tests use autoincrement customers of their own.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import NOW, FakeRedis, make_provider, seed_agent
from tests.test_auth_hardening import _MemPipe
from tests.test_identity_oidc_flow import FakeIdp, _resolver
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.domain.customer_auth import CustomerSessionStore
from tiqora.domain.oidc import OidcClientConfig, OIDCService, clear_discovery_cache
from tiqora.domain.passkey import WebAuthnService
from tiqora.domain.totp import TOTPService
from tiqora.domain.two_factor_subject import AGENT_2FA, CUSTOMER_2FA
from tiqora.identity import sso_routes
from tiqora.identity.config_models import AttributeMap
from tiqora.security.ratelimit import AuthRateLimiter
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-customer-two-factor"
BASE = "https://helpdesk.example.org"
SAME = {"Origin": BASE}
COOKIE = "tiqora_customer_session"
PASSWORD = "secret123"
CUST_LO, CUST_HI = 47150, 47159
AGENT_LO, AGENT_HI = 47160, 47169
SLUG = "idptest-2fa-"
_URLS: set[str] = set()


class YieldingRedis(FakeRedis):
    """Hands control to other tasks around every call, so GET-then-SET races become visible."""

    def __init__(self) -> None:
        super().__init__()
        self.zsets: dict[str, dict[str, float]] = {}

    async def get(self, key: str) -> str | None:
        await asyncio.sleep(0)
        return await super().get(key)

    async def set(
        self, key: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        await asyncio.sleep(0)
        return await super().set(key, value, ex=ex, nx=nx)

    async def delete(self, *keys: str) -> int:
        await asyncio.sleep(0)
        return await super().delete(*keys)


class MemRedis(FakeRedis):
    """FakeRedis plus the sorted-set/pipeline subset the auth rate limiter uses."""

    def __init__(self) -> None:
        super().__init__()
        self.zsets: dict[str, dict[str, float]] = {}

    async def zremrangebyscore(self, key: str, lo: float, hi: float) -> int:
        zs = self.zsets.get(key, {})
        drop = [m for m, sc in zs.items() if lo <= sc <= hi]
        for m in drop:
            del zs[m]
        return len(drop)

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.zsets.setdefault(key, {}).update({m: float(s) for m, s in mapping.items()})
        return len(mapping)

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.zsets.pop(key, None)
        return await super().delete(*keys)

    def pipeline(self) -> _MemPipe:
        return _MemPipe(self)  # type: ignore[arg-type]


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            band = {"lo": CUST_LO, "hi": CUST_HI}
            for table in ("tiqora_customer_totp", "tiqora_customer_passkey"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE customer_user_id BETWEEN :lo AND :hi"),  # noqa: S608
                    band,
                )
            # Agent-side rows: the namespacing test reuses customer ids as agent ids.
            for table in ("tiqora_user_totp", "tiqora_user_passkey"):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id BETWEEN :lo AND :hi"),  # noqa: S608
                    {"lo": CUST_LO, "hi": AGENT_HI},
                )
            conn.execute(
                text("DELETE FROM user_preferences WHERE user_id BETWEEN :lo AND :hi"),
                {"lo": AGENT_LO, "hi": AGENT_HI},
            )
            conn.execute(
                text("DELETE FROM users WHERE id BETWEEN :lo AND :hi"),
                {"lo": AGENT_LO, "hi": AGENT_HI},
            )
            conn.execute(
                text(
                    "DELETE FROM tiqora_identity_link WHERE provider_id IN"
                    " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE :p)"
                ),
                {"p": f"{SLUG}%"},
            )
            conn.execute(
                text("DELETE FROM tiqora_identity_provider WHERE slug LIKE :p"), {"p": f"{SLUG}%"}
            )
            conn.execute(text("DELETE FROM customer_user WHERE id BETWEEN :lo AND :hi"), band)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "test",
        "secret_key": KEY,
        "public_base_url": BASE,
        "session_cookie_secure": True,
        "portal_enabled": True,
        "webauthn_rp_id": "helpdesk.example.org",
        "webauthn_origin": BASE,
    }
    base.update(kw)
    return Settings(**base)


class Env:
    def __init__(self, maker: Any, session: AsyncSession) -> None:
        self.maker = maker
        self.session = session
        self.redis = MemRedis()
        self.oidc = FakeIdp()
        self.settings = _settings()
        self.client: httpx.AsyncClient = None  # type: ignore[assignment]

    async def use(self, settings: Settings) -> None:
        if self.client is not None:
            await self.client.aclose()
        self.settings = settings
        app = create_app(settings)
        app.state.redis = self.redis

        async def _get_db() -> AsyncIterator[AsyncSession]:
            async with self.maker() as s:
                yield s

        app.dependency_overrides[get_db] = _get_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)

    def new_client(self) -> httpx.AsyncClient:
        """A second browser against the same app and Redis."""
        return httpx.AsyncClient(transport=self.client._transport, base_url=BASE)  # noqa: SLF001

    @property
    def store(self) -> CustomerSessionStore:
        return CustomerSessionStore(self.redis, self.settings)

    async def customer(self, cid: int, *, password: bool = True) -> str:
        login = f"idp.2fa.{cid}@example.org"
        await self.session.execute(
            text(
                "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name,"
                " pw, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, :login, 'idp-test-co', 'Idp', 'Kunde', :pw, 1, :t, 1, :t, 1)"
            ),
            {
                "id": cid,
                "login": login,
                "pw": hash_password(PASSWORD) if password else None,
                "t": NOW,
            },
        )
        await self.session.commit()
        return login

    def totp(self, subject: Any = CUSTOMER_2FA) -> TOTPService:
        return TOTPService(self.session, self.settings, self.redis, subject=subject)

    async def enroll_totp(self, cid: int, login: str) -> str:
        svc = self.totp()
        secret, _uri = await svc.enroll(cid, login)
        assert await svc.confirm(cid, pyotp.TOTP(secret).now())
        return secret

    async def add_passkey(self, cid: int, cred: str = "Y3JlZC0x") -> None:
        await self.session.execute(
            text(
                "INSERT INTO tiqora_customer_passkey (customer_user_id, credential_id, public_key,"
                " sign_count, name) VALUES (:c, :cred, :pk, 0, 'Key')"
            ),
            {"c": cid, "cred": cred, "pk": b"not-a-real-key"},
        )
        await self.session.commit()

    async def login(
        self, client: httpx.AsyncClient | None = None, login: str | None = None, pw: str = PASSWORD
    ) -> httpx.Response:
        return await (client or self.client).post(
            "/api/portal/auth/login", json={"login": login, "password": pw}, headers=SAME
        )


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    clear_discovery_cache()
    monkeypatch.setattr("tiqora.security.outbound._default_resolve", _resolver)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        e = Env(maker, session)
        transport = httpx.MockTransport(e.oidc.handler)

        def make_service(config: OidcClientConfig) -> OIDCService:
            return OIDCService(config, transport=transport)

        monkeypatch.setattr(sso_routes, "new_oidc_service", make_service)
        await e.use(e.settings)
        yield e
        await e.client.aclose()
    await engine.dispose()
    clear_discovery_cache()
    _clean(url)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# ------------------------------------------------------------------ namespacing / schema


async def test_totp_redis_keys_are_namespaced(env: Env) -> None:
    cid = 47151
    login = await env.customer(cid)
    agent = env.totp(AGENT_2FA)
    cust = env.totp(CUSTOMER_2FA)
    agent_id = cid  # same numeric id on purpose: only the namespace keeps them apart

    a_secret, _ = await agent.enroll(agent_id, "agent.47151")
    c_secret, _ = await cust.enroll(cid, login)
    assert f"tiqora:totp:pending:{cid}" in env.redis.store
    assert f"tiqora:totp:customer:pending:{cid}" in env.redis.store

    # Confirming the customer code must not enable the agent factor (nor vice versa).
    assert await cust.confirm(cid, pyotp.TOTP(c_secret).now())
    assert await cust.is_enabled(cid) is True
    assert await agent.is_enabled(agent_id) is False
    assert f"tiqora:totp:pending:{cid}" in env.redis.store  # agent enrollment untouched

    assert await agent.confirm(agent_id, pyotp.TOTP(a_secret).now())
    # Replay keys are separate: spending the customer's step does not block the agent's.
    assert await cust.verify(cid, pyotp.TOTP(c_secret).now())
    assert f"tiqora:totp:customer:used:{cid}" in env.redis.store
    assert f"tiqora:totp:used:{cid}" not in env.redis.store
    assert await agent.verify(agent_id, pyotp.TOTP(a_secret).now())
    assert f"tiqora:totp:used:{cid}" in env.redis.store
    await env.session.execute(text("DELETE FROM tiqora_user_totp WHERE user_id = :i"), {"i": cid})
    await env.session.commit()


async def test_customer_user_handle_differs_from_agent(env: Env) -> None:
    cid = 47152
    for subject, expected in (
        (CUSTOMER_2FA, b"tiqora-customer:" + cid.to_bytes(8, "big")),
        (AGENT_2FA, cid.to_bytes(8, "big")),
    ):
        svc = WebAuthnService(env.session, env.redis, env.settings, subject=subject)  # type: ignore[arg-type]
        options = await svc.begin_registration(user_id=cid, login="x", session_token="tok")
        assert options["user"]["id"] == _b64url(expected)
    assert "tiqora:webauthn:customer:reg:tok" in env.redis.store
    assert "tiqora:webauthn:reg:tok" in env.redis.store


async def test_customer_totp_row_keyed_by_customer_user_id(env: Env) -> None:
    cid = 47153
    login = await env.customer(cid)
    await env.enroll_totp(cid, login)
    row = (
        await env.session.execute(
            text(
                "SELECT customer_user_id, enabled FROM tiqora_customer_totp"
                " WHERE customer_user_id = :c"
            ),
            {"c": cid},
        )
    ).first()
    assert row is not None and int(row[0]) == cid and bool(row[1]) is True
    cols = {
        str(r[0]).lower()
        for r in await env.session.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'tiqora_customer_totp'"
            )
        )
    }
    assert "customer_user_id" in cols and "user_id" not in cols


# ------------------------------------------------------------------ login / pending


async def test_portal_login_pending_when_totp_enrolled(env: Env) -> None:
    cid = 47154
    login = await env.customer(cid)
    secret = await env.enroll_totp(cid, login)

    resp = await env.login(login=login)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "customer": None,
        "pending_2fa": True,
        "totp_enrolled": True,
        "passkey_enrolled": False,
    }
    pending = env.client.cookies.get(COOKIE)
    assert pending and await env.store.get_pending(pending) == (cid, login)
    assert (await env.client.get("/api/portal/auth/me")).status_code == 401

    bad = await env.client.post(
        "/api/portal/auth/totp/verify", json={"code": "000000"}, headers=SAME
    )
    assert bad.status_code == 401
    assert (await env.client.get("/api/portal/auth/me")).status_code == 401  # still pending

    ok = await env.client.post(
        "/api/portal/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=SAME
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["customer"]["login"] == login and ok.json()["pending_2fa"] is False
    full = env.client.cookies.get(COOKIE)
    assert full and full != pending
    assert await env.store.get_pending(pending) is None  # consumed
    assert await env.store.get(full) == (cid, login)
    assert await env.store.seconds_since_auth(full) is not None
    assert (await env.client.get("/api/portal/auth/me")).status_code == 200


async def test_login_without_factor_is_a_plain_session(env: Env) -> None:
    login = await env.customer(47150)
    resp = await env.login(login=login)
    assert resp.status_code == 200
    assert resp.json()["pending_2fa"] is False and resp.json()["customer"]["login"] == login
    assert (await env.client.get("/api/portal/auth/me")).status_code == 200


async def test_pending_token_reaches_nothing_but_verify_endpoints(env: Env) -> None:
    cid = 47155
    login = await env.customer(cid)
    await env.enroll_totp(cid, login)
    await env.add_passkey(cid)
    assert (await env.login(login=login)).json()["pending_2fa"] is True

    c = env.client
    for method, path, kw in (
        ("GET", "/api/portal/auth/me", {}),
        ("GET", "/api/portal/tickets", {}),
        ("GET", "/api/portal/auth/totp/status", {}),
        ("POST", "/api/portal/auth/totp/enroll", {"json": {}}),
        ("GET", "/api/portal/auth/totp/enroll/qr", {}),
        ("POST", "/api/portal/auth/totp/confirm", {"json": {"code": "123456"}}),
        ("DELETE", "/api/portal/auth/totp", {"json": {"code": "123456"}}),
        ("GET", "/api/portal/auth/passkey", {}),
        ("POST", "/api/portal/auth/passkey/register/begin", {}),
        ("POST", "/api/portal/auth/passkey/register/finish", {"json": {"credential": {}}}),
        ("DELETE", "/api/portal/auth/passkey/1", {}),
    ):
        resp = await c.request(method, path, headers=SAME, **kw)
        assert resp.status_code == 401, (method, path, resp.status_code, resp.text)
    # ... while the verify endpoints do recognise it (wrong input -> their own 401/400).
    begin = await c.post("/api/portal/auth/passkey/authenticate/begin", headers=SAME)
    assert begin.status_code == 200 and "challenge" in begin.json()
    # No cookie at all -> the verify endpoints refuse as well.
    anon = env.new_client()
    for path in ("/totp/verify", "/passkey/authenticate/finish"):
        body = {"code": "123456"} if path == "/totp/verify" else {"credential": {}}
        r = await anon.post(f"/api/portal/auth/{path.lstrip('/')}", json=body, headers=SAME)
        assert r.status_code == 401
    assert (
        await anon.post("/api/portal/auth/passkey/authenticate/begin", headers=SAME)
    ).status_code == 401
    await anon.aclose()


async def test_full_session_cannot_use_verify_endpoints(env: Env) -> None:
    login = await env.customer(47156)
    assert (await env.login(login=login)).json()["customer"] is not None
    r = await env.client.post("/api/portal/auth/totp/verify", json={"code": "123456"}, headers=SAME)
    assert r.status_code == 401
    r = await env.client.post("/api/portal/auth/passkey/authenticate/begin", headers=SAME)
    assert r.status_code == 401


async def test_login_responses_do_not_enumerate(env: Env) -> None:
    cid = 47157
    login = await env.customer(cid)
    await env.enroll_totp(cid, login)
    wrong = await env.login(login=login, pw="not-the-password")
    unknown = await env.login(login="idp.2fa.nobody@example.org", pw="not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert not wrong.headers.get_list("set-cookie") and not unknown.headers.get_list("set-cookie")


# ------------------------------------------------------------------ brute force / replay


async def test_totp_verify_is_rate_limited(env: Env) -> None:
    cid = 47158
    login = await env.customer(cid)
    secret = await env.enroll_totp(cid, login)
    await env.use(_settings(auth_rate_limit_login_max=3, auth_rate_limit_ip_max=100))
    assert (await env.login(login=login)).json()["pending_2fa"] is True
    codes = []
    for _ in range(3):
        r = await env.client.post(
            "/api/portal/auth/totp/verify", json={"code": "000000"}, headers=SAME
        )
        codes.append(r.status_code)
    assert codes == [401, 401, 429]
    # Locked: even the right code is refused until the lockout expires.
    r = await env.client.post(
        "/api/portal/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=SAME
    )
    assert r.status_code == 429 and "retry-after" in r.headers
    assert (await env.client.get("/api/portal/auth/me")).status_code == 401
    # The lock sits on the customer key; the agent counter (`2fa:<login>`) is untouched.
    limiter = AuthRateLimiter(env.redis, env.settings)
    assert (await limiter.check(login=f"2fa:customer:{login}", ip="203.0.113.7")).allowed is False
    assert (await limiter.check(login=f"2fa:{login}", ip="203.0.113.7")).allowed is True


async def test_totp_code_cannot_be_replayed(env: Env) -> None:
    cid = 47159
    login = await env.customer(cid)
    secret = await env.enroll_totp(cid, login)
    code = pyotp.TOTP(secret).now()
    other = env.new_client()
    assert (await env.login(login=login)).json()["pending_2fa"] is True
    assert (await env.login(other, login=login)).json()["pending_2fa"] is True
    first = await env.client.post("/api/portal/auth/totp/verify", json={"code": code}, headers=SAME)
    assert first.status_code == 200
    second = await other.post("/api/portal/auth/totp/verify", json={"code": code}, headers=SAME)
    assert second.status_code == 401
    await other.aclose()


# ------------------------------------------------------------------ enrollment / removal


async def _full_session(env: Env, cid: int, *, with_totp: bool) -> tuple[str, str | None]:
    login = await env.customer(cid)
    secret = await env.enroll_totp(cid, login) if with_totp else None
    resp = await env.login(login=login)
    if with_totp:
        assert secret is not None
        await env.client.post(
            "/api/portal/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}, headers=SAME
        )
    else:
        assert resp.json()["customer"] is not None
    return login, secret


async def test_totp_enroll_confirm_disable_need_a_full_session(env: Env) -> None:
    anon = env.new_client()
    for method, path, kw in (
        ("GET", "/api/portal/auth/totp/status", {}),
        ("POST", "/api/portal/auth/totp/enroll", {"json": {}}),
        ("POST", "/api/portal/auth/totp/confirm", {"json": {"code": "123456"}}),
        ("DELETE", "/api/portal/auth/totp", {"json": {"code": "123456"}}),
    ):
        assert (await anon.request(method, path, headers=SAME, **kw)).status_code == 401
    await anon.aclose()

    await _full_session(env, 47150, with_totp=False)
    c = env.client
    assert (await c.get("/api/portal/auth/totp/status")).json() == {"enabled": False}
    enrolled = await c.post("/api/portal/auth/totp/enroll", json={}, headers=SAME)
    assert enrolled.status_code == 200
    secret = enrolled.json()["secret"]
    qr = await c.get("/api/portal/auth/totp/enroll/qr")
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/svg+xml"
    assert (
        await c.post("/api/portal/auth/totp/confirm", json={"code": "000000"}, headers=SAME)
    ).status_code == 400
    ok = await c.post(
        "/api/portal/auth/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=SAME
    )
    assert ok.status_code == 200 and ok.json() == {"enabled": True}
    assert (await c.get("/api/portal/auth/totp/status")).json() == {"enabled": True}


async def test_replacing_or_removing_a_live_totp_needs_its_code(env: Env) -> None:
    _login, secret = await _full_session(env, 47151, with_totp=True)
    assert secret is not None
    c = env.client
    # Re-enrollment without the current code is refused ...
    r = await c.post("/api/portal/auth/totp/enroll", json={}, headers=SAME)
    assert r.status_code == 403
    # ... and so is removal with a wrong code; the factor stays live.
    r = await c.request("DELETE", "/api/portal/auth/totp", json={"code": "000000"}, headers=SAME)
    assert r.status_code == 400
    assert (await c.get("/api/portal/auth/totp/status")).json() == {"enabled": True}
    good = await c.request(
        "DELETE",
        "/api/portal/auth/totp",
        json={"code": pyotp.TOTP(secret).at(time.time() + 30)},
        headers=SAME,
    )
    assert good.status_code == 200 and good.json() == {"enabled": False}


async def test_passkey_register_requires_recent_auth_when_factor_exists(env: Env) -> None:
    cid = 47152
    login, _secret = await _full_session(env, cid, with_totp=True)
    token = env.client.cookies.get(COOKIE)
    assert token
    c = env.client

    fresh = await c.post("/api/portal/auth/passkey/register/begin", headers=SAME)
    assert fresh.status_code == 200, fresh.text
    options = fresh.json()
    assert options["user"]["id"] == _b64url(b"tiqora-customer:" + cid.to_bytes(8, "big"))

    # Age the authentication event beyond step_up_max_age_seconds.
    env.redis.store[f"tiqora:csession:authat:{token}"] = "1"
    stale = await c.post("/api/portal/auth/passkey/register/begin", headers=SAME)
    assert stale.status_code == 403 and stale.json()["detail"] == "reauthentication_required"
    stale_finish = await c.post(
        "/api/portal/auth/passkey/register/finish", json={"credential": {}}, headers=SAME
    )
    assert stale_finish.status_code == 403
    await env.add_passkey(cid)
    stale_delete = await c.delete("/api/portal/auth/passkey/1", headers=SAME)
    assert stale_delete.status_code == 403
    listed = await c.get("/api/portal/auth/passkey")
    assert listed.status_code == 200 and [p["name"] for p in listed.json()] == ["Key"]

    # Unknown age (key gone, e.g. after an upgrade) counts as stale as well.
    del env.redis.store[f"tiqora:csession:authat:{token}"]
    assert (
        await c.post("/api/portal/auth/passkey/register/begin", headers=SAME)
    ).status_code == 403


async def test_first_passkey_needs_no_recent_auth(env: Env) -> None:
    await _full_session(env, 47153, with_totp=False)
    token = env.client.cookies.get(COOKIE)
    assert token
    del env.redis.store[f"tiqora:csession:authat:{token}"]
    resp = await env.client.post("/api/portal/auth/passkey/register/begin", headers=SAME)
    assert resp.status_code == 200, resp.text


async def test_passkey_delete_is_scoped_to_the_owner(env: Env) -> None:
    await env.customer(47154)
    await env.add_passkey(47154, "b3RoZXI")  # someone else's passkey
    await _full_session(env, 47155, with_totp=False)
    row = (
        await env.session.execute(
            text("SELECT id FROM tiqora_customer_passkey WHERE customer_user_id = 47154")
        )
    ).scalar_one()
    resp = await env.client.delete(f"/api/portal/auth/passkey/{int(row)}", headers=SAME)
    assert resp.status_code == 404
    still = (
        await env.session.execute(
            text("SELECT COUNT(*) FROM tiqora_customer_passkey WHERE customer_user_id = 47154")
        )
    ).scalar_one()
    assert int(still) == 1


# ------------------------------------------------------------------ passkey challenge binding


async def test_passkey_challenge_is_bound_to_the_pending_subject(env: Env) -> None:
    a_id, b_id = 47156, 47157
    a_login = await env.customer(a_id)
    b_login = await env.customer(b_id)
    await env.add_passkey(a_id, "YS1jcmVk")
    await env.add_passkey(b_id, "Yi1jcmVk")

    browser_b = env.new_client()
    assert (await env.login(login=a_login)).json()["pending_2fa"] is True
    assert (await env.login(browser_b, login=b_login)).json()["passkey_enrolled"] is True
    a_pending = env.client.cookies.get(COOKIE)
    b_pending = browser_b.cookies.get(COOKIE)
    assert a_pending and b_pending and a_pending != b_pending

    options_a = (
        await env.client.post("/api/portal/auth/passkey/authenticate/begin", headers=SAME)
    ).json()
    assert [c["id"] for c in options_a["allowCredentials"]] == ["YS1jcmVk"]  # only A's own
    assert f"tiqora:webauthn:customer:auth:{a_pending}" in env.redis.store
    assert f"tiqora:webauthn:customer:auth:{b_pending}" not in env.redis.store

    # B has no challenge of its own: an assertion cannot ride on A's.
    forged = await browser_b.post(
        "/api/portal/auth/passkey/authenticate/finish",
        json={"credential": {"id": "YS1jcmVk", "rawId": "YS1jcmVk", "response": {}}},
        headers=SAME,
    )
    assert forged.status_code == 401
    assert f"tiqora:webauthn:customer:auth:{a_pending}" in env.redis.store  # untouched
    # A garbage assertion by A consumes A's challenge and yields no session.
    bad = await env.client.post(
        "/api/portal/auth/passkey/authenticate/finish",
        json={"credential": {"id": "YS1jcmVk", "rawId": "YS1jcmVk", "response": {}}},
        headers=SAME,
    )
    assert bad.status_code == 401
    assert f"tiqora:webauthn:customer:auth:{a_pending}" not in env.redis.store
    assert (await env.client.get("/api/portal/auth/me")).status_code == 401
    await browser_b.aclose()


# ------------------------------------------------------------------ SSO


async def test_portal_sso_skips_customer_2fa(env: Env) -> None:
    cid = 47151
    login = await env.customer(cid, password=False)
    await env.enroll_totp(cid, login)
    await env.add_passkey(cid)
    await make_provider(
        env.session,
        slug=f"{SLUG}kc",
        audience="customer",
        match_by="email",
        attribute_map=AttributeMap(email=["email"]),
        config={
            "issuer": "https://kc.example.org/realms/y",
            "client_id": "tiqora",
            "client_secret": "kc-secret",
        },
        settings=env.settings,
    )
    await env.session.commit()
    env.oidc.claims["c"] = {"sub": "kunde-2fa", "email": login, "email_verified": True}

    start = await env.client.get(f"/api/portal/auth/sso/{SLUG}kc/login?next=/portal/tickets")
    assert start.status_code == 302, start.text
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    resp = await env.client.get(
        f"/api/portal/auth/oidc/{SLUG}kc/callback", params={"code": "c", "state": state}
    )
    assert resp.status_code == 302 and resp.headers["location"] == "/portal/tickets"
    token = env.client.cookies.get(COOKIE)
    assert token and await env.store.get(token) == (cid, login)  # a FULL session, not pending
    assert await env.store.get_pending(token) is None
    assert (await env.client.get("/api/portal/auth/me")).status_code == 200


# ------------------------------------------------------------------ deletion paths


async def test_agent_delete_never_touches_a_customer_with_the_same_id(env: Env) -> None:
    """``user_delete.OWNED`` is keyed by ``users.id``; customer factors live in another id space."""
    from tiqora.domain.user_delete import OWNED, delete_user_rows

    assert not any(table.startswith("tiqora_customer_") for table, _ in OWNED)
    cid = 47152
    login = await env.customer(cid)
    await env.enroll_totp(cid, login)
    await env.add_passkey(cid)
    await delete_user_rows(env.session, cid)  # an AGENT with the same numeric id
    await env.session.commit()
    for table in ("tiqora_customer_totp", "tiqora_customer_passkey"):
        count = (
            await env.session.execute(
                text(f"SELECT COUNT(*) FROM {table} WHERE customer_user_id = :c"),  # noqa: S608
                {"c": cid},
            )
        ).scalar_one()
        assert int(count) == 1, table


# ------------------------------------------------------------------ review round 1


@pytest.mark.parametrize("subject", [CUSTOMER_2FA, AGENT_2FA], ids=["customer", "agent"])
async def test_totp_code_single_use_under_concurrency(env: Env, subject: Any) -> None:
    uid = 47153
    redis = YieldingRedis()
    svc = TOTPService(env.session, env.settings, redis, subject=subject)
    secret, _ = await svc.enroll(uid, "idp.2fa.concurrent")
    assert await svc.confirm(uid, pyotp.TOTP(secret).now())
    code = pyotp.TOTP(secret).now()
    # The claim itself, raced on pure Redis yields (deterministic interleaving) ...
    step = svc._match_timestep(secret, code)  # noqa: SLF001
    assert step is not None
    claims = await asyncio.gather(*(svc._accept_timestep(uid, step) for _ in range(5)))  # noqa: SLF001
    assert claims.count(True) == 1, claims
    # ... and end to end through verify() with a fresh Redis.
    svc = TOTPService(env.session, env.settings, YieldingRedis(), subject=subject)
    results = await asyncio.gather(*(svc.verify(uid, code) for _ in range(5)))
    assert results.count(True) == 1, results
    await env.session.execute(text("DELETE FROM tiqora_user_totp WHERE user_id = :i"), {"i": uid})
    await env.session.commit()


async def test_pending_promotion_is_single_use_under_concurrency(env: Env) -> None:
    store = CustomerSessionStore(YieldingRedis(), env.settings)  # type: ignore[arg-type]
    pending = await store.create_pending(47154, "idp.2fa.47154@example.org", 300)
    tokens = await asyncio.gather(*(store.promote_pending(pending) for _ in range(5)))
    assert len([t for t in tokens if t]) == 1, tokens


async def test_pending_tokens_are_in_the_session_index_and_revoked(env: Env) -> None:
    cid = 47155
    login = await env.customer(cid)
    await env.enroll_totp(cid, login)
    assert (await env.login(login=login)).json()["pending_2fa"] is True
    pending = env.client.cookies.get(COOKIE)
    assert pending and pending in env.redis.sets[f"tiqora:csession:user:{cid}"]
    assert await env.store.revoke_customer_sessions(cid) == 1
    assert await env.store.get_pending(pending) is None
    r = await env.client.post("/api/portal/auth/totp/verify", json={"code": "123456"}, headers=SAME)
    assert r.status_code == 401


async def test_admin_reset_clears_customer_second_factors(env: Env) -> None:
    from fastapi import HTTPException

    from tiqora.api.v1.admin.customers import reset_customer_user_2fa
    from tiqora.domain.auth import AuthenticatedUser

    cid, other = 47156, 47157
    login = await env.customer(cid)
    other_login = await env.customer(other)
    for uid, lg in ((cid, login), (other, other_login)):
        await env.enroll_totp(uid, lg)
        await env.add_passkey(uid, f"cred-{uid}")
    admin = AuthenticatedUser(
        id=1, login="root@localhost", first_name="A", last_name="B", auth_method="session"
    )
    await reset_customer_user_2fa(cid, admin, env.session, env.settings)
    await reset_customer_user_2fa(cid, admin, env.session, env.settings)  # idempotent
    assert await env.totp().is_enabled(cid) is False
    assert await env.totp().is_enabled(other) is True  # neighbour untouched
    counts = {
        int(r[0]): int(r[1])
        for r in await env.session.execute(
            text(
                "SELECT customer_user_id, COUNT(*) FROM tiqora_customer_passkey"
                " WHERE customer_user_id IN (47156, 47157) GROUP BY customer_user_id"
            )
        )
    }
    assert counts == {other: 1}
    # The customer can sign in with the password alone again.
    assert (await env.login(login=login)).json()["customer"] is not None
    with pytest.raises(HTTPException) as exc:
        await reset_customer_user_2fa(47158, admin, env.session, env.settings)
    assert exc.value.status_code == 404


async def test_customer_adding_totp_to_passkey_only_account_needs_recent_auth(env: Env) -> None:
    cid = 47158
    await _full_session(env, cid, with_totp=False)
    await env.add_passkey(cid)
    token = env.client.cookies.get(COOKIE)
    assert token
    ok = await env.client.post("/api/portal/auth/totp/enroll", json={}, headers=SAME)
    assert ok.status_code == 200, ok.text
    env.redis.store[f"tiqora:csession:authat:{token}"] = "1"
    stale = await env.client.post("/api/portal/auth/totp/enroll", json={}, headers=SAME)
    assert stale.status_code == 403 and stale.json()["detail"] == "reauthentication_required"


async def test_agent_adding_totp_to_passkey_only_account_needs_recent_auth(env: Env) -> None:
    aid, login = 47160, "idp.2fa.agent47160"
    await seed_agent(env.session, aid, login)
    await env.session.execute(
        text(
            "INSERT INTO tiqora_user_passkey (user_id, credential_id, public_key, sign_count,"
            " name) VALUES (:u, 'YWdlbnQ', :pk, 0, 'Key')"
        ),
        {"u": aid, "pk": b"k"},
    )
    await env.session.commit()
    token = await SessionStore(env.redis, env.settings).create(aid, login)  # type: ignore[arg-type]
    cookies = {"tiqora_session": token}
    ok = await env.client.post("/api/v1/auth/totp/enroll", json={}, headers=SAME, cookies=cookies)
    assert ok.status_code == 200, ok.text
    env.redis.store[f"tiqora:session:authat:{token}"] = "1"
    stale = await env.client.post(
        "/api/v1/auth/totp/enroll", json={}, headers=SAME, cookies=cookies
    )
    assert stale.status_code == 403 and stale.json()["detail"] == "reauthentication_required"
