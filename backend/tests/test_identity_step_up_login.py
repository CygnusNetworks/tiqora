"""Step-up MFA in the SSO login: pending / enroll sessions, refusals, pending endpoints (Task 16).

ASGI + DB, both dialects. Seed band 63360-63379 (agents 63360-63369, customers
``sc63.kunde.<n>@example.org``); provider slugs ``idptest-su-*``. The fake IdP of
``test_identity_oidc_flow`` serves discovery/token/userinfo; the id_token carries ``amr``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pyotp
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import (
    FakeRedis,
    cleanup_statements,
    make_provider,
    seed_agent,
    seed_customer,
)
from tests.test_identity_oidc_flow import FakeIdp, _resolver
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.customer_auth import CustomerSessionStore
from tiqora.domain.oidc import OidcClientConfig, OIDCService, clear_discovery_cache
from tiqora.domain.totp import TOTPService
from tiqora.domain.two_factor_subject import CUSTOMER_2FA
from tiqora.identity import flow_state, sso_routes
from tiqora.identity.config_models import AttributeMap, MfaPolicy
from tiqora.identity.flow_state import TestRun
from tiqora.identity.http import SSO_ERROR_CODES
from tiqora.identity.sso_sessions import revoke_sso_sessions, store_sso
from tiqora.identity.store import ProviderChanges, update_provider

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-step-up-login"
BAND = (63360, 63379)
SLUG = "idptest-su-"
BASE = "https://helpdesk.example.org"
SAME = {"Origin": BASE}
JSON = {"Accept": "application/json"}
AGENT_COOKIE = "tiqora_session"
CUSTOMER_COOKIE = "tiqora_customer_session"
CUSTOMER_LIKE = "sc63.kunde.%@example.org"
ADMIN = 63369
_URLS: set[str] = set()


def _stmts() -> tuple[tuple[str, dict[str, object]], ...]:
    return (
        (
            "DELETE FROM tiqora_customer_totp WHERE customer_user_id IN"
            " (SELECT id FROM customer_user WHERE login LIKE :c)",
            {"c": CUSTOMER_LIKE},
        ),
        (
            "DELETE FROM tiqora_user_totp WHERE user_id BETWEEN :lo AND :hi",
            {"lo": 63360, "hi": 63379},
        ),
        ("DELETE FROM tiqora_identity_provider WHERE slug LIKE 'env-%'", {}),
        ("DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi", {"lo": 63360, "hi": 63379}),
        *cleanup_statements(user_ids=BAND, slug_prefix=SLUG, customer_login_like=CUSTOMER_LIKE),
    )


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in _stmts():
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _settings() -> Settings:
    return Settings(
        environment="test",
        secret_key=KEY,
        public_base_url=BASE,
        session_cookie_secure=True,
        portal_enabled=True,
        oidc_enabled=True,
        oidc_issuer="https://idp.example.org/realms/x",
        oidc_client_id="tiqora",
        oidc_client_secret="s3cr3t",
        oidc_claim="preferred_username",
        oidc_redirect_uri=f"{BASE}/api/v1/auth/oidc/callback",
    )


class Env:
    def __init__(self, maker: Any, session: AsyncSession, client: httpx.AsyncClient) -> None:
        self.maker = maker
        self.session = session
        self.client = client
        self.redis = FakeRedis()
        self.idp = FakeIdp()
        self.settings = _settings()

    @property
    def agent_store(self) -> SessionStore:
        return SessionStore(self.redis, self.settings)  # type: ignore[arg-type]

    @property
    def customer_store(self) -> CustomerSessionStore:
        return CustomerSessionStore(self.redis, self.settings)  # type: ignore[arg-type]

    async def agent(self, user_id: int, *, totp: bool = False) -> str:
        login = f"sc63.agent.{user_id}"
        await seed_agent(self.session, user_id, login)
        await AuthConfigService(self.session).set(user_id, sso_eligible=True)
        await self.session.commit()
        if totp:
            await self.enroll_totp(user_id, login)
        return login

    async def customer(self, n: int, *, totp: bool = False) -> str:
        login = f"sc63.kunde.{n}@example.org"
        cid = await seed_customer(self.session, login)
        if totp:
            await self.enroll_totp(cid, login, subject=CUSTOMER_2FA)
        return login

    async def enroll_totp(self, uid: int, login: str, **kw: Any) -> str:
        svc = TOTPService(self.session, self.settings, self.redis, **kw)
        secret, _uri = await svc.enroll(uid, login)
        assert await svc.confirm(uid, pyotp.TOTP(secret).now())
        return secret

    async def provider(
        self,
        slug: str,
        *,
        audience: Any = "agent",
        mfa: bool = True,
        fallback: str = "local_second_factor",
    ) -> Any:
        rec = await make_provider(
            self.session,
            slug=f"{SLUG}{slug}",
            audience=audience,
            match_by="login" if audience == "agent" else "email",
            attribute_map=(
                AttributeMap(login=["preferred_username"])
                if audience == "agent"
                else AttributeMap(email=["email"])
            ),
            config={
                "issuer": f"https://{slug}.example.org/realms/y",
                "client_id": "tiqora",
                "client_secret": "kc-secret",
            },
            settings=self.settings,
        )
        if mfa:
            rec = await update_provider(
                self.session,
                rec.id,
                ProviderChanges(
                    require_mfa_claim=True,
                    mfa_policy=MfaPolicy(fallback=fallback),  # type: ignore[arg-type]
                ),
                secret_key=KEY,
                actor_id=1,
                settings=self.settings,
            )
        await self.session.commit()
        return rec

    async def login(
        self,
        slug: str,
        claims: dict[str, Any],
        *,
        amr: list[str] | None = None,
        audience: str = "agent",
        next_path: str = "/tickets/5",
    ) -> httpx.Response:
        """Full OIDC round trip against provider *slug*; returns the callback response."""
        prefix = "/api/v1/auth" if audience == "agent" else "/api/portal/auth"
        start = await self.client.get(f"{prefix}/sso/{SLUG}{slug}/login?next={next_path}")
        assert start.status_code == 302, start.text
        q = parse_qs(urlparse(start.headers["location"]).query)
        self.idp.claims["c"] = claims
        if amr is not None:
            self.idp.id_tokens["c"] = self.idp.signer.id_token(
                iss=f"https://{slug}.example.org/realms/y",
                aud="tiqora",
                sub=claims["sub"],
                nonce=q["nonce"][0],
                amr=amr,
            )
        else:
            self.idp.id_tokens.pop("c", None)
        return await self.client.get(
            f"{prefix}/oidc/{SLUG}{slug}/callback", params={"code": "c", "state": q["state"][0]}
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
    settings = _settings()
    app = create_app(settings)

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    async with (
        maker() as session,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE, headers=SAME
        ) as client,
    ):
        e = Env(maker, session, client)
        app.state.redis = e.redis
        transport = httpx.MockTransport(e.idp.handler)

        def make_service(config: OidcClientConfig) -> OIDCService:
            return OIDCService(config, transport=transport)

        monkeypatch.setattr(sso_routes, "new_oidc_service", make_service)
        yield e
    await engine.dispose()
    clear_discovery_cache()
    _clean(url)


def _location(resp: httpx.Response) -> tuple[str, dict[str, list[str]]]:
    assert resp.status_code == 302, resp.text
    parts = urlparse(resp.headers["location"])
    return parts.path, parse_qs(parts.query)


async def _count(env: Env, table: str) -> int:
    row = await env.session.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
    return int(row.scalar_one())


# ------------------------------------------------------------------------------- agents


async def test_mfa_satisfied_gives_full_session(env: Env) -> None:
    login = await env.agent(63360, totp=True)
    await env.provider("full")
    resp = await env.login(
        "full", {"sub": "s-63360", "preferred_username": login}, amr=["pwd", "mfa"]
    )
    assert resp.status_code == 302 and resp.headers["location"] == "/tickets/5"
    token = env.client.cookies.get(AGENT_COOKIE)
    assert token
    assert await env.agent_store.get(token) == (63360, login)
    assert await env.agent_store.get_pending(token) is None
    info = await env.agent_store.get_sso(token)
    assert info is not None and info.slug == f"{SLUG}full"
    me = await env.client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["login"] == login


async def test_provider_without_mfa_requirement_is_unchanged(env: Env) -> None:
    login = await env.agent(63361, totp=True)
    await env.provider("plain", mfa=False)
    resp = await env.login("plain", {"sub": "s-63361", "preferred_username": login})
    assert resp.headers["location"] == "/tickets/5"
    token = env.client.cookies.get(AGENT_COOKIE)
    assert token and await env.agent_store.get(token) == (63361, login)


async def test_agent_with_totp_gets_pending_and_redirect(env: Env) -> None:
    login = await env.agent(63362, totp=True)
    await env.provider("pend")
    resp = await env.login("pend", {"sub": "s-63362", "preferred_username": login}, amr=["pwd"])
    assert resp.status_code == 302
    assert resp.headers["location"] == f"/login?sso_step_up=1&next={quote('/tickets/5', safe='')}"
    token = env.client.cookies.get(AGENT_COOKIE)
    assert token
    assert await env.agent_store.get(token) is None
    assert await env.agent_store.get_pending(token) == (63362, login)
    pending = await env.client.get("/api/v1/auth/pending")
    assert pending.status_code == 200
    assert pending.json() == {
        "state": "pending_2fa",
        "totp_enrolled": True,
        "passkey_enrolled": False,
    }


async def test_pending_endpoint_never_extends_a_session(env: Env) -> None:
    login = await env.agent(63363, totp=True)
    await env.provider("ttl")
    await env.login("ttl", {"sub": "s-63363", "preferred_username": login}, amr=["pwd"])
    token = env.client.cookies.get(AGENT_COOKIE)
    assert token
    before = dict(env.redis.ttls)
    resp = await env.client.get("/api/v1/auth/pending")
    assert resp.status_code == 200
    assert resp.json()["state"] == "pending_2fa"
    assert env.redis.ttls == before


async def test_step_up_pending_cookie_is_not_a_session(env: Env) -> None:
    login = await env.agent(63364, totp=True)
    await env.provider("nose")
    await env.login("nose", {"sub": "s-63364", "preferred_username": login}, amr=["pwd"])
    assert env.client.cookies.get(AGENT_COOKIE)
    assert (await env.client.get("/api/v1/auth/me")).status_code == 401


async def test_agent_without_factor_is_refused_and_nothing_is_written(env: Env) -> None:
    login = await env.agent(63365)
    await env.provider("nofac")
    resp = await env.login("nofac", {"sub": "s-63365", "preferred_username": login}, amr=["pwd"])
    path, q = _location(resp)
    assert (path, q["sso_error"]) == ("/login", ["mfa_required"])
    assert env.client.cookies.get(AGENT_COOKIE) is None
    assert await _count(env, "tiqora_identity_link") == 0  # F8: a refused login must not write
    assert not [k for k in env.redis.store if k.startswith("tiqora:session:")]


async def test_agent_without_factor_must_enroll_only_as_explicit_opt_in(env: Env) -> None:
    login = await env.agent(63366)
    await env.provider("enr", fallback="local_second_factor_or_enroll")
    resp = await env.login("enr", {"sub": "s-63366", "preferred_username": login}, amr=["pwd"])
    assert resp.headers["location"].startswith("/login?sso_step_up=1")
    token = env.client.cookies.get(AGENT_COOKIE)
    assert token
    assert await env.agent_store.get(token) is None
    assert await env.agent_store.get_enroll(token) == (63366, login)
    state = await env.client.get("/api/v1/auth/pending")
    assert state.json() == {
        "state": "must_enroll_2fa",
        "totp_enrolled": False,
        "passkey_enrolled": False,
    }


async def test_fallback_reject_refuses_even_agents_with_totp(env: Env) -> None:
    login = await env.agent(63367, totp=True)
    await env.provider("rej", fallback="reject")
    resp = await env.login("rej", {"sub": "s-63367", "preferred_username": login}, amr=["pwd"])
    path, q = _location(resp)
    assert (path, q["sso_error"]) == ("/login", ["mfa_required"])
    assert env.client.cookies.get(AGENT_COOKIE) is None
    assert await _count(env, "tiqora_identity_link") == 0


async def test_promoted_session_keeps_sso(env: Env) -> None:
    login = await env.agent(63368)
    secret = await env.enroll_totp(63368, login)
    await env.provider("prom")
    await env.login("prom", {"sub": "s-63368", "preferred_username": login}, amr=["pwd"])
    pending_token = env.client.cookies.get(AGENT_COOKIE)
    assert pending_token
    pending_info = await env.agent_store.get_sso(pending_token)
    assert pending_info is not None and pending_info.slug == f"{SLUG}prom"

    verify = await env.client.post(
        "/api/v1/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()}
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["user"]["login"] == login
    full = env.client.cookies.get(AGENT_COOKIE)
    assert full and full != pending_token
    assert await env.agent_store.get(full) == (63368, login)
    info = await env.agent_store.get_sso(full)
    assert info is not None
    assert (info.provider_id, info.slug) == (pending_info.provider_id, f"{SLUG}prom")
    assert info.subject_hash == pending_info.subject_hash
    assert await env.agent_store.get_pending(pending_token) is None
    assert await env.agent_store.get_sso(pending_token) is None


async def test_idp_logout_kills_pending_token(env: Env) -> None:
    login = await env.agent(63360, totp=True)
    rec = await env.provider("slo")
    await env.login("slo", {"sub": "s-63360", "preferred_username": login}, amr=["pwd"])
    token = env.client.cookies.get(AGENT_COOKIE)
    assert token and await env.agent_store.get_pending(token) == (63360, login)
    info = await env.agent_store.get_sso(token)
    assert info is not None
    removed = await revoke_sso_sessions(
        env.redis, env.settings, provider_id=rec.id, subject_hash=info.subject_hash
    )
    assert removed == 1
    assert await env.agent_store.get_pending(token) is None
    assert (await env.client.get("/api/v1/auth/pending")).json()["state"] == "none"


async def test_pending_endpoints_without_cookie_say_none(env: Env) -> None:
    for path in ("/api/v1/auth/pending", "/api/portal/auth/pending"):
        resp = await env.client.get(path)
        assert resp.status_code == 200, path
        assert resp.json() == {"state": "none", "totp_enrolled": False, "passkey_enrolled": False}


async def test_full_session_cookie_reports_none(env: Env) -> None:
    token = await env.agent_store.create(63361, "sc63.agent.63361")
    env.client.cookies.set(AGENT_COOKIE, token)
    assert (await env.client.get("/api/v1/auth/pending")).json()["state"] == "none"


async def test_env_provider_never_steps_up(env: Env) -> None:
    login = await env.agent(63362, totp=True)
    env.idp.claims["c"] = {"sub": "env-63362", "preferred_username": login}
    start = await env.client.get("/api/v1/auth/oidc/login")
    q = parse_qs(urlparse(start.headers["location"]).query)
    resp = await env.client.get(
        "/api/v1/auth/oidc/callback", params={"code": "c", "state": q["state"][0]}, headers=JSON
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["login"] == login
    assert body["pending_2fa"] is False and body["must_enroll_2fa"] is False
    token = env.client.cookies.get(AGENT_COOKIE)
    assert token and await env.agent_store.get(token) == (63362, login)


# ----------------------------------------------------------------------------- customers


async def test_customer_without_factor_rejected(env: Env) -> None:
    login = await env.customer(63360)
    await env.provider("cnf", audience="customer")
    resp = await env.login(
        "cnf",
        {"sub": "c-63360", "email": login, "email_verified": True},
        amr=["pwd"],
        audience="customer",
        next_path="/portal/tickets",
    )
    path, q = _location(resp)
    assert (path, q["sso_error"]) == ("/portal/login", ["mfa_required"])
    assert env.client.cookies.get(CUSTOMER_COOKIE) is None
    assert await _count(env, "tiqora_identity_link") == 0


async def test_customer_with_totp_pending(env: Env) -> None:
    login = await env.customer(63361, totp=True)
    await env.provider("cpe", audience="customer")
    resp = await env.login(
        "cpe",
        {"sub": "c-63361", "email": login, "email_verified": True},
        amr=["pwd"],
        audience="customer",
        next_path="/portal/tickets",
    )
    path, q = _location(resp)
    assert path == "/portal/login"
    assert q["sso_step_up"] == ["1"] and q["next"] == ["/portal/tickets"]
    token = env.client.cookies.get(CUSTOMER_COOKIE)
    assert token
    assert await env.customer_store.get(token) is None
    pending = await env.customer_store.get_pending(token)
    assert pending is not None and pending[1] == login
    assert (await env.client.get("/api/portal/auth/me")).status_code == 401
    state = await env.client.get("/api/portal/auth/pending")
    assert state.json() == {
        "state": "pending_2fa",
        "totp_enrolled": True,
        "passkey_enrolled": False,
    }
    info = await env.customer_store.get_sso(token)
    assert info is not None and info.slug == f"{SLUG}cpe"


async def test_customer_mfa_satisfied_gives_full_session(env: Env) -> None:
    login = await env.customer(63362)
    await env.provider("cok", audience="customer")
    resp = await env.login(
        "cok",
        {"sub": "c-63362", "email": login, "email_verified": True},
        amr=["pwd", "mfa"],
        audience="customer",
        next_path="/portal/tickets",
    )
    assert resp.headers["location"] == "/portal/tickets"
    token = env.client.cookies.get(CUSTOMER_COOKIE)
    assert token and await env.customer_store.get(token) is not None


# ------------------------------------------------------------------------------- admin test


async def test_admin_test_login_reports_mfa(env: Env) -> None:
    login = await env.agent(63364, totp=True)
    await seed_agent(env.session, ADMIN, f"sc63.admin.{ADMIN}")
    await env.session.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) SELECT :u, id, 'rw', :t, 1, :t, 1"
            " FROM permission_groups WHERE name = 'admin'"
        ),
        {"u": ADMIN, "t": datetime(2026, 1, 1)},
    )
    await env.session.commit()
    rec = await env.provider("adm")
    await flow_state.put_test(
        env.redis,
        "tid-su",
        TestRun(provider_id=rec.id, admin_id=ADMIN, audience="agent"),
        secret_key=KEY,
    )
    admin_token = await env.agent_store.create(ADMIN, f"sc63.admin.{ADMIN}")
    env.client.cookies.set(AGENT_COOKIE, admin_token)

    start = await env.client.get(f"/api/v1/auth/sso/{SLUG}adm/login", params={"test": "tid-su"})
    q = parse_qs(urlparse(start.headers["location"]).query)
    env.idp.claims["c"] = {"sub": "s-63364", "preferred_username": login}
    env.idp.id_tokens["c"] = env.idp.signer.id_token(
        iss="https://adm.example.org/realms/y",
        aud="tiqora",
        sub="s-63364",
        nonce=q["nonce"][0],
        amr=["pwd"],
    )
    done = await env.client.get(
        f"/api/v1/auth/oidc/{SLUG}adm/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert done.status_code == 302
    assert done.headers["location"] == f"/admin/identity-providers/{rec.id}?test=tid-su"
    assert "tiqora_session=" not in done.headers.get("set-cookie", "")
    assert env.client.cookies.get(AGENT_COOKIE) == admin_token  # no session was swapped in
    assert await _count(env, "tiqora_identity_link") == 0

    result = await env.client.get(f"/api/v1/admin/identity-providers/{rec.id}/tests/tid-su")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "done"
    assert result.json()["mfa"] == {"required": True, "satisfied": False, "evidence": ["pwd"]}


# ------------------------------------------------------------------------------ unit level


def test_sso_error_codes_include_mfa_required() -> None:
    assert "mfa_required" in SSO_ERROR_CODES


async def test_short_lived_token_does_not_shorten_the_shared_index() -> None:
    """F7: a pending token (300 s) must not cut the index of a full session (8 h)."""
    from tiqora.identity.sso_sessions import SsoSessionInfo, tokens_for

    redis = FakeRedis()
    info = SsoSessionInfo(provider_id=7, slug="x", kind="oidc", subject_hash="h" * 64, sid="sid-1")
    await store_sso(redis, "a", "full-token", info, 28800)
    await store_sso(redis, "a", "pending-token", info, 300)
    index = "tiqora:sso:idx:a:7:sub:" + "h" * 64
    assert redis.ttls[index] == 28800
    assert await tokens_for(redis, "a", 7, subject_hash="h" * 64) == {
        "full-token",
        "pending-token",
    }
