"""Logout started by the identity provider; id_token verification; IdP-initiated SAML login.

Wave 1, Task 21. ASGI + DB. Seed band 47120-47149 (agents), customers ``idp.bc.4712x@example.org``,
provider slugs ``idptest-bcl-*``. OIDC uses the fake IdP of ``test_identity_oidc_flow`` with a
real RSA key served as JWKS (generated at test time); SAML runs real round trips against the
in-process pysaml2 IdP and skips (hard failure under ``TIQORA_REQUIRE_SAML_TESTS=1``) without
pysaml2 / xmlsec1.

Every attacker-shaped input is rejected without revoking anything: unsigned / SHA-1 / foreign-key
/ wrong-issuer / stale / replayed SAML LogoutRequests, unsigned or unknown-``InResponseTo``
LogoutResponses, logout tokens with a wrong aud/iss, no events, a nonce, a stale iat, a bad
signature, alg none/HS256, an unknown kid, no selector, a replayed jti; id_tokens with alg
none/HS256, a wrong aud/iss/nonce, an unknown kid, expired, or a userinfo ``sub`` that differs.
"""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
import zlib
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import (
    FakeRedis,
    OidcSigner,
    cleanup_statements,
    make_provider,
    seed_agent,
    seed_customer,
)
from tests._saml_idp import (
    IDP_SLO_URL,
    SIG_RSA_SHA1,
    SamlTestIdp,
    require_saml,
)
from tests.test_identity_oidc_flow import FakeIdp, _resolver
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain import oidc as oidc_mod
from tiqora.domain.auth import SessionStore
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.customer_auth import CustomerSessionStore
from tiqora.domain.oidc import (
    OidcClientConfig,
    OIDCError,
    OIDCService,
    OIDCTokenInvalid,
    clear_discovery_cache,
)
from tiqora.identity import oidc_backchannel, sso_routes
from tiqora.identity.config_models import AttributeMap
from tiqora.identity.oidc_backchannel import BACKCHANNEL_EVENT
from tiqora.identity.sso_sessions import SsoSessionInfo, store_sso, tokens_for
from tiqora.identity.store import ProviderChanges, get_provider_by_slug, update_provider

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-idp-logout"
BAND = (47120, 47149)
SLUG = "idptest-bcl-"
OIDC_SLUG = f"{SLUG}kc"
SAML_SLUG = f"{SLUG}saml"
BASE = "https://helpdesk.example.org"
ISSUER = f"https://{OIDC_SLUG}.example.org/realms/y"
BCL_PATH = f"/api/v1/auth/oidc/{OIDC_SLUG}/backchannel-logout"
SLS_PATH = f"/api/v1/auth/saml/{SAML_SLUG}/sls"
SP_SLS = f"{BASE}{SLS_PATH}"
AGENT_ACS = f"{BASE}/api/v1/auth/saml/{SAML_SLUG}/acs"
SP_ENTITY = f"{BASE}/api/v1/auth/saml/{SAML_SLUG}/metadata"
CUSTOMER_LIKE = "idp.bc.%@example.org"
AGENT_COOKIE = "tiqora_session"
CUSTOMER_COOKIE = "tiqora_customer_session"
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
REJECTED = {"error": "invalid_request"}
_URLS: set[str] = set()


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in cleanup_statements(
                user_ids=BAND, slug_prefix=SLUG, customer_login_like=CUSTOMER_LIKE
            ):
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


@pytest.fixture(scope="module")
def idp(tmp_path_factory: pytest.TempPathFactory) -> SamlTestIdp:
    require_saml()
    return SamlTestIdp(tmp_path_factory.mktemp("idp"))


class Env:
    def __init__(self, maker: Any, session: AsyncSession) -> None:
        self.maker = maker
        self.session = session
        self.redis = FakeRedis()
        self.oidc = FakeIdp()
        self.settings = Settings(
            environment="test",
            secret_key=KEY,
            public_base_url=BASE,
            session_cookie_secure=True,
            portal_enabled=True,
        )
        app = create_app(self.settings)
        app.state.redis = self.redis

        async def _get_db() -> AsyncIterator[AsyncSession]:
            async with self.maker() as s:
                yield s

        app.dependency_overrides[get_db] = _get_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
        self._codes = 0

    @property
    def agents(self) -> SessionStore:
        return SessionStore(self.redis, self.settings)

    @property
    def customers(self) -> CustomerSessionStore:
        return CustomerSessionStore(self.redis, self.settings)

    async def agent(self, user_id: int, login: str) -> None:
        await seed_agent(self.session, user_id, login)
        await AuthConfigService(self.session).set(user_id, sso_eligible=True)
        await self.session.commit()

    def code(self) -> str:
        self._codes += 1
        return f"code-{self._codes}"

    async def start(self, path: str) -> dict[str, list[str]]:
        resp = await self.client.get(path)
        assert resp.status_code == 302, resp.text
        return parse_qs(urlparse(resp.headers["location"]).query)

    async def oidc_login(
        self,
        *,
        login: str,
        sub: str,
        sid: str | None,
        customer: bool = False,
        id_token: Any = None,
    ) -> httpx.Response:
        """One OIDC login through the real routes. ``id_token``: None = a valid signed token
        (with *sid*), a callable ``(nonce) -> str | None`` = custom token / no token."""
        prefix = "/api/portal/auth" if customer else "/api/v1/auth"
        q = await self.start(f"{prefix}/sso/{OIDC_SLUG}/login")
        code = self.code()
        self.oidc.claims[code] = {"sub": sub, "preferred_username": login}
        nonce = q["nonce"][0]
        if id_token is None:
            extra = {"sid": sid} if sid is not None else {}
            token: str | None = self.oidc.signer.id_token(
                iss=ISSUER, aud="tiqora", sub=sub, nonce=nonce, **extra
            )
        else:
            token = id_token(nonce)
        if token is not None:
            self.oidc.id_tokens[code] = token
        resp = await self.client.get(
            f"{prefix}/oidc/{OIDC_SLUG}/callback", params={"code": code, "state": q["state"][0]}
        )
        return resp

    async def session_token(self, **kw: Any) -> str:
        customer = kw.get("customer", False)
        resp = await self.oidc_login(**kw)
        assert resp.status_code == 302, resp.text
        assert "sso_error" not in resp.headers["location"], resp.headers["location"]
        name = CUSTOMER_COOKIE if customer else AGENT_COOKIE
        token = next(
            c.split(";")[0].split("=", 1)[1]
            for c in resp.headers.get_list("set-cookie")
            if c.startswith(f"{name}=")
        )
        self.client.cookies.clear()
        return token

    async def bcl(self, token: str | None, *, path: str = BCL_PATH, **kw: Any) -> httpx.Response:
        body = urlencode({"logout_token": token}) if token is not None else ""
        headers = {**FORM, **kw.pop("headers", {})}
        return await self.client.post(path, content=body, headers=headers, **kw)


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
        yield e
        await e.client.aclose()
    await engine.dispose()
    clear_discovery_cache()
    _clean(url)


async def _oidc_provider(env: Env, **config: Any) -> int:
    rec = await make_provider(
        env.session,
        slug=OIDC_SLUG,
        audience="both",
        attribute_map=AttributeMap(login=["preferred_username"]),
        config={"issuer": ISSUER, "client_id": "tiqora", "client_secret": "kc-secret", **config},
        settings=env.settings,
    )
    await env.session.commit()
    return rec.id


def _logout_token(env: Env, *, drop: tuple[str, ...] = (), **claims: Any) -> str:
    now = int(time.time())
    body: dict[str, Any] = {
        "iss": ISSUER,
        "aud": "tiqora",
        "iat": now,
        "exp": now + 120,
        "jti": uuid.uuid4().hex,
        "events": {BACKCHANNEL_EVENT: {}},
    }
    body.update(claims)
    for name in drop:
        body.pop(name, None)
    return env.oidc.signer.sign(body)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unsigned(header: dict[str, Any], claims: dict[str, Any], sig: bytes = b"") -> str:
    return ".".join(
        [_b64(json.dumps(header).encode()), _b64(json.dumps(claims).encode()), _b64(sig)]
    )


def _hs256(claims: dict[str, Any], secret: bytes) -> str:
    import hashlib  # noqa: PLC0415
    import hmac  # noqa: PLC0415

    head = _b64(json.dumps({"alg": "HS256", "kid": "k1"}).encode())
    body = _b64(json.dumps(claims).encode())
    mac = hmac.new(secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(mac)}"


async def _alive(env: Env, token: str, *, customer: bool = False) -> bool:
    store = env.customers if customer else env.agents
    return await store.get(token) is not None


# ------------------------------------------------------------------ OIDC back-channel logout


async def test_backchannel_logout_by_sid(env: Env) -> None:
    await _oidc_provider(env)
    await env.agent(47121, "idp.bc.47121")
    await env.agent(47122, "idp.bc.47122")
    await seed_customer(env.session, "idp.bc.47123@example.org")

    a1 = await env.session_token(login="idp.bc.47121", sub="s-47121", sid="sid-1")
    a1_other = await env.session_token(login="idp.bc.47121", sub="s-47121", sid="sid-2")
    a2 = await env.session_token(login="idp.bc.47122", sub="s-47122", sid="sid-3")
    c1 = await env.session_token(
        login="idp.bc.47123@example.org", sub="s-c-47123", sid="sid-1", customer=True
    )
    info = await env.agents.get_sso(a1)
    assert info is not None and info.sid == "sid-1"  # stored from the VERIFIED id_token
    cinfo = await env.customers.get_sso(c1)
    assert cinfo is not None and cinfo.sid == "sid-1"

    # sub AND sid named: the sid alone selects (the subject's other IdP session survives)
    resp = await env.bcl(_logout_token(env, sid="sid-1", sub="s-47121"))
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert not await _alive(env, a1)
    assert not await _alive(env, c1, customer=True)  # both audiences
    assert await _alive(env, a1_other)
    assert await _alive(env, a2)


async def test_backchannel_logout_by_sub(env: Env) -> None:
    await _oidc_provider(env)
    await env.agent(47124, "idp.bc.47124")
    await env.agent(47125, "idp.bc.47125")
    s1 = await env.session_token(login="idp.bc.47124", sub="s-47124", sid="sid-a")
    s2 = await env.session_token(login="idp.bc.47124", sub="s-47124", sid=None)
    other = await env.session_token(login="idp.bc.47125", sub="s-47125", sid="sid-b")

    resp = await env.bcl(_logout_token(env, sub="s-47124"))
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert not await _alive(env, s1)
    assert not await _alive(env, s2)
    assert await _alive(env, other)


async def test_backchannel_rejects(env: Env) -> None:
    await _oidc_provider(env)
    await env.agent(47126, "idp.bc.47126")
    victim = await env.session_token(login="idp.bc.47126", sub="s-47126", sid="sid-v")
    sel = {"sub": "s-47126", "sid": "sid-v"}
    now = int(time.time())
    impostor = OidcSigner()  # same kid "k1", key not in the JWKS
    good_claims = {
        "iss": ISSUER,
        "aud": "tiqora",
        "iat": now,
        "jti": uuid.uuid4().hex,
        "events": {BACKCHANNEL_EVENT: {}},
        **sel,
    }
    jwk_n = env.oidc.signer.jwks()["keys"][0]["n"].encode()
    cases: dict[str, str] = {
        "wrong_aud": _logout_token(env, aud="other-client", **sel),
        "wrong_iss": _logout_token(env, iss="https://evil.example.org/realms/y", **sel),
        "no_events": _logout_token(env, drop=("events",), **sel),
        "events_without_bcl": _logout_token(env, events={"urn:other": {}}, **sel),
        "events_not_object": _logout_token(env, events={BACKCHANNEL_EVENT: "x"}, **sel),
        "nonce_present": _logout_token(env, nonce="n-1", **sel),
        "iat_too_old": _logout_token(env, iat=now - 3600, **sel),
        "iat_in_future": _logout_token(env, iat=now + 3600, **sel),
        "no_iat": _logout_token(env, drop=("iat",), **sel),
        "expired": _logout_token(env, exp=now - 3600, **sel),
        "no_jti": _logout_token(env, drop=("jti",), **sel),
        "no_selector": _logout_token(env),
        "bad_signature": impostor.sign(good_claims),
        "unknown_kid": env.oidc.signer.sign(good_claims, key=OidcSigner("k-x").key),
        "alg_none": _unsigned({"alg": "none", "kid": "k1"}, good_claims),
        "alg_hs256_with_public_key": _hs256(good_claims, jwk_n),
        "garbage": "not-a-jwt",
        "empty": "",
    }
    for name, token in cases.items():
        resp = await env.bcl(token)
        assert resp.status_code == 400, (name, resp.status_code, resp.text)
        assert resp.json() == REJECTED, name  # never which check failed
        assert resp.headers["cache-control"] == "no-store", name
        assert await _alive(env, victim), name

    # missing parameter / wrong content type
    resp = await env.client.post(BCL_PATH, content="other=1", headers=FORM)
    assert resp.status_code == 400 and resp.json() == REJECTED
    resp = await env.client.post(
        BCL_PATH,
        content=json.dumps({"logout_token": _logout_token(env, **sel)}),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400 and resp.json() == REJECTED
    assert await _alive(env, victim)


async def test_backchannel_replayed_jti_rejected(env: Env) -> None:
    await _oidc_provider(env)
    await env.agent(47127, "idp.bc.47127")
    first = await env.session_token(login="idp.bc.47127", sub="s-47127", sid="sid-r1")
    token = _logout_token(env, sid="sid-r1")
    assert (await env.bcl(token)).status_code == 200
    assert not await _alive(env, first)

    # a new IdP session that happens to reuse the sid: the replayed token must not end it
    second = await env.session_token(login="idp.bc.47127", sub="s-47127", sid="sid-r1")
    again = await env.bcl(token)
    assert again.status_code == 400 and again.json() == REJECTED
    assert await _alive(env, second)


async def test_backchannel_not_found_cases(env: Env) -> None:
    pid = await _oidc_provider(env, backchannel_logout=False)
    await env.agent(47128, "idp.bc.47128")
    token = await env.session_token(login="idp.bc.47128", sub="s-47128", sid="sid-n")
    resp = await env.bcl(_logout_token(env, sid="sid-n"))
    assert resp.status_code == 404
    assert await _alive(env, token)

    unknown = await env.bcl(
        _logout_token(env, sid="sid-n"),
        path="/api/v1/auth/oidc/idptest-bcl-none/backchannel-logout",
    )
    assert unknown.status_code == 404

    await update_provider(
        env.session,
        pid,
        ProviderChanges(config={"backchannel_logout": True}),
        secret_key=KEY,
        actor_id=1,
    )
    await env.session.commit()
    assert (await env.bcl(_logout_token(env, sid="sid-n"))).status_code == 200
    assert not await _alive(env, token)


async def test_backchannel_rate_limited(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oidc_backchannel, "RATE_LIMIT_MAX", 3)
    await _oidc_provider(env)
    for _ in range(3):
        assert (await env.bcl("not-a-jwt")).status_code == 400
    limited = await env.bcl(_logout_token(env, sub="s-x"))
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.headers["cache-control"] == "no-store"


async def test_backchannel_is_csrf_exempt_but_still_needs_a_valid_token(env: Env) -> None:
    await _oidc_provider(env)
    await env.agent(47129, "idp.bc.47129")
    token = await env.session_token(login="idp.bc.47129", sub="s-47129", sid="sid-c")
    stray = {"Origin": "https://idp.example.org", "Cookie": f"{AGENT_COOKIE}=whatever"}
    forged = await env.bcl("not-a-jwt", headers=stray)
    assert forged.status_code == 400  # not the CSRF 403: the token is the only authenticator
    assert await _alive(env, token)
    ok = await env.bcl(_logout_token(env, sid="sid-c"), headers=stray)
    assert ok.status_code == 200
    assert not await _alive(env, token)
    # the exemption is this exact route only
    other = await env.client.post(f"{BCL_PATH}/x", content="", headers={**FORM, **stray})
    assert other.status_code == 403


# ------------------------------------------------------------------ id_token verification


async def test_id_token_rejects(env: Env) -> None:
    await _oidc_provider(env)
    await env.agent(47130, "idp.bc.47130")
    login, sub = "idp.bc.47130", "s-47130"
    signer = env.oidc.signer
    jwk_n = signer.jwks()["keys"][0]["n"].encode()
    now = int(time.time())

    def claims(nonce: str, **over: Any) -> dict[str, Any]:
        body = {"iss": ISSUER, "aud": "tiqora", "sub": sub, "iat": now, "exp": now + 300}
        body["nonce"] = nonce
        body.update(over)
        return body

    cases: dict[str, Any] = {
        "alg_none": lambda n: _unsigned({"alg": "none"}, claims(n)),
        "alg_hs256": lambda n: _hs256(claims(n), jwk_n),
        "wrong_aud": lambda n: signer.sign(claims(n, aud="other-client")),
        "multi_aud_without_azp": lambda n: signer.sign(claims(n, aud=["tiqora", "other"])),
        "wrong_azp": lambda n: signer.sign(claims(n, azp="other-client")),
        "wrong_iss": lambda n: signer.sign(claims(n, iss="https://evil.example.org/realms/y")),
        "wrong_nonce": lambda n: signer.sign(claims("attacker-nonce")),
        "missing_nonce": lambda n: signer.sign(
            {k: v for k, v in claims(n).items() if k != "nonce"}
        ),
        "expired": lambda n: signer.sign(claims(n, exp=now - 3600)),
        "unknown_kid": lambda n: signer.sign(claims(n), key=OidcSigner("k-zz").key),
        "bad_signature": lambda n: OidcSigner().sign(claims(n)),
        "userinfo_sub_differs": lambda n: signer.sign(claims(n, sub="someone-else")),
    }
    for name, make in cases.items():
        resp = await env.oidc_login(login=login, sub=sub, sid=None, id_token=make)
        assert resp.status_code == 302, (name, resp.text)
        query = parse_qs(urlparse(resp.headers["location"]).query)
        assert query.get("sso_error") == ["oidc_failed"], (name, resp.headers["location"])
        assert not any(
            c.startswith(f"{AGENT_COOKIE}=") and "Max-Age=0" not in c
            for c in resp.headers.get_list("set-cookie")
        ), name
        env.client.cookies.clear()

    # a valid token logs in and its sid ends up in the session
    token = await env.session_token(login=login, sub=sub, sid="sid-ok")
    info = await env.agents.get_sso(token)
    assert info is not None and info.sid == "sid-ok"


async def test_provider_without_id_token_keeps_working(env: Env) -> None:
    await _oidc_provider(env)
    await env.agent(47131, "idp.bc.47131")
    token = await env.session_token(
        login="idp.bc.47131", sub="s-47131", sid=None, id_token=lambda _n: None
    )
    info = await env.agents.get_sso(token)
    assert info is not None and info.sid is None and info.id_token_hint is None


# ------------------------------------------------------------------ JWKS cache (pure)


class _JwksIdp:
    def __init__(self) -> None:
        self.signer = OidcSigner()
        self.jwks_hits = 0
        self.timeouts: list[dict[str, Any]] = []
        self.jwks_down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": "https://jwks.example.org",
                    "jwks_uri": "https://jwks.example.org/jwks",
                },
            )
        if request.url.path == "/jwks":
            self.jwks_hits += 1
            self.timeouts.append(dict(request.extensions.get("timeout", {})))
            if self.jwks_down:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, json=self.signer.jwks())
        return httpx.Response(404)


def _jwks_service(fake: _JwksIdp) -> OIDCService:
    cfg = OidcClientConfig(
        issuer="https://jwks.example.org",
        client_id="tiqora",
        client_secret="x",
        scopes="openid",
        redirect_uri="",
    )
    return OIDCService(cfg, transport=httpx.MockTransport(fake.handler))


@pytest.fixture
def _pure(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    clear_discovery_cache()
    monkeypatch.setattr("tiqora.security.outbound._default_resolve", _resolver)
    yield
    clear_discovery_cache()


@pytest.mark.usefixtures("_pure")
async def test_jwks_cached_rotation_refetches_once_and_refetch_is_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _JwksIdp()
    service = _jwks_service(fake)
    claims = {"sub": "a"}
    assert (await service.verify_jwt(fake.signer.sign(claims)))["sub"] == "a"
    assert (await service.verify_jwt(fake.signer.sign(claims)))["sub"] == "a"
    assert fake.jwks_hits == 1  # cached
    assert fake.timeouts[0]["read"] == 5.0  # every fetch is bounded

    # key rotation: a token with a new kid triggers exactly one refetch and verifies
    new_key = fake.signer.add_key("k2")
    assert (await service.verify_jwt(fake.signer.sign(claims, key=new_key)))["sub"] == "a"
    assert fake.jwks_hits == 2

    # invented kids right after: no further fetch (rate-limited), rejected
    for i in range(5):
        with pytest.raises(OIDCTokenInvalid) as err:
            await service.verify_jwt(fake.signer.sign(claims, key=OidcSigner(f"x{i}").key))
        assert err.value.reason == "unknown_kid"
    assert fake.jwks_hits == 2

    # after the minimum interval one more refetch is allowed
    monkeypatch.setattr(oidc_mod, "_JWKS_REFRESH_MIN_INTERVAL", 0.0)
    with pytest.raises(OIDCTokenInvalid):
        await service.verify_jwt(fake.signer.sign(claims, key=OidcSigner("x9").key))
    assert fake.jwks_hits == 3


@pytest.mark.usefixtures("_pure")
async def test_jwks_fetch_failure_is_an_oidc_error() -> None:
    fake = _JwksIdp()
    fake.jwks_down = True
    with pytest.raises(OIDCError):
        await _jwks_service(fake).verify_jwt(fake.signer.sign({"sub": "a"}))


@pytest.mark.usefixtures("_pure")
async def test_ambiguous_key_without_kid_rejected() -> None:
    fake = _JwksIdp()
    fake.signer.add_key("k2")
    token = _unsigned({"alg": "RS256"}, {"sub": "a"}, b"x" * 256)
    with pytest.raises(OIDCTokenInvalid) as err:
        await _jwks_service(fake).verify_jwt(token)
    assert err.value.reason == "unknown_kid"


# ------------------------------------------------------------------ SSO index (pure)


async def test_tokens_for_prunes_dead_tokens() -> None:
    redis = FakeRedis()
    info = SsoSessionInfo(provider_id=9, slug="x", kind="oidc", subject_hash="h", sid="sid-9")
    await store_sso(redis, "a", "live", info, 600)
    await store_sso(redis, "a", "dead", info, 600)
    del redis.store["tiqora:session:sso:dead"]  # the session expired, the index did not
    assert await tokens_for(redis, "a", 9, subject_hash="h") == {"live"}
    assert await tokens_for(redis, "a", 9, sid="sid-9") == {"live"}
    assert all("dead" not in members for members in redis.sets.values())


# ------------------------------------------------------------------ SAML


async def _saml_provider(env: Env, idp: SamlTestIdp, **cfg: Any) -> int:
    rec = await make_provider(
        env.session,
        slug=SAML_SLUG,
        kind="saml",
        attribute_map=AttributeMap(login=["uid"]),
        config={"idp_metadata_xml": idp.metadata_xml(), "name_id_format": "persistent", **cfg},
        settings=env.settings,
    )
    await env.session.commit()
    meta = await env.client.get(f"/api/v1/auth/saml/{SAML_SLUG}/metadata")
    assert meta.status_code == 200, meta.text
    idp.trust_sp(meta.text)
    return rec.id


def _session_cookie(resp: httpx.Response) -> str | None:
    return next(
        (
            c.split(";")[0].split("=", 1)[1]
            for c in resp.headers.get_list("set-cookie")
            if c.startswith(f"{AGENT_COOKIE}=") and "Max-Age=0" not in c
        ),
        None,
    )


async def _acs(
    env: Env, body: str, *, relay: str = "", cookie: str | None = None
) -> httpx.Response:
    headers = {"Origin": "https://idp.example.org", **FORM}
    if cookie:
        headers["Cookie"] = f"tiqora_saml_req={cookie}"
    resp = await env.client.post(
        f"/api/v1/auth/saml/{SAML_SLUG}/acs",
        content=urlencode({"SAMLResponse": body, "RelayState": relay}),
        headers=headers,
    )
    env.client.cookies.clear()
    return resp


async def _saml_login(env: Env, idp: SamlTestIdp, user_id: int) -> str:
    start = await env.client.get(f"/api/v1/auth/sso/{SAML_SLUG}/login?next=/tickets")
    assert start.status_code == 302, start.text
    req_id, _acs_url, relay = idp.parse_login_redirect(start.headers["location"])
    body = idp.login_response(
        in_response_to=req_id,
        acs_url=AGENT_ACS,
        sp_entity_id=SP_ENTITY,
        name_id=f"u-{user_id}",
        attributes={"uid": [f"idp.bc.{user_id}"]},
    )
    resp = await _acs(env, body, relay=relay, cookie=req_id)
    assert resp.status_code == 200, resp.text
    token = _session_cookie(resp)
    assert token
    return token


def _request_id(url: str) -> str:
    raw = parse_qs(urlparse(url).query)["SAMLRequest"][0]
    xml = zlib.decompress(base64.b64decode(raw), -15).decode()
    found = re.search(r'\bID="([^"]+)"', xml)
    assert found is not None, xml
    return found.group(1)


async def _sls(env: Env, url: str) -> httpx.Response:
    return await env.client.get(f"{SLS_PATH}?{urlparse(url).query}")


@pytest.mark.saml
async def test_idp_initiated_logout_request(env: Env, idp: SamlTestIdp) -> None:
    await _saml_provider(env, idp)
    await env.agent(47140, "idp.bc.47140")
    first = await _saml_login(env, idp, 47140)
    second = await _saml_login(env, idp, 47140)  # another IdP session of the same subject
    info = await env.agents.get_sso(first)
    assert info is not None and info.session_index

    url = idp.logout_request_redirect(
        sp_slo_url=SP_SLS, name_id="u-47140", session_index=info.session_index
    )
    resp = await _sls(env, url)
    assert resp.status_code == 302, resp.text
    loc = resp.headers["location"]
    assert loc.startswith(IDP_SLO_URL + "?")
    parsed = idp.parse_logout_response(loc)  # asserts a SHA-256 signed redirect
    assert parsed.status == "urn:oasis:names:tc:SAML:2.0:status:Success"
    assert parsed.in_response_to == _request_id(url)
    assert parsed.destination == IDP_SLO_URL
    assert not await _alive(env, first)
    assert await _alive(env, second)  # subject AND session index


@pytest.mark.saml
async def test_idp_logout_request_rejects(env: Env, idp: SamlTestIdp, tmp_path: Any) -> None:
    await _saml_provider(env, idp)
    await env.agent(47141, "idp.bc.47141")
    victim = await _saml_login(env, idp, 47141)
    info = await env.agents.get_sso(victim)
    assert info is not None and info.session_index
    si = info.session_index

    def req(**kw: Any) -> str:
        return idp.logout_request_redirect(
            sp_slo_url=SP_SLS, name_id="u-47141", session_index=si, **kw
        )

    valid = req()
    q = urlparse(valid).query
    unsigned = "&".join(p for p in q.split("&") if not p.startswith(("Signature=", "SigAlg=")))
    impostor = SamlTestIdp(tmp_path)  # same entity id, key not in the metadata
    foreign = impostor.logout_request_redirect(
        sp_slo_url=SP_SLS, name_id="u-47141", session_index=si
    )
    cases = {
        "unsigned": f"{SLS_PATH}?{unsigned}",
        "sha1": f"{SLS_PATH}?{urlparse(req(sigalg=SIG_RSA_SHA1)).query}",
        "foreign_key": f"{SLS_PATH}?{urlparse(foreign).query}",
        "wrong_issuer": f"{SLS_PATH}?{urlparse(req(issuer='https://other.example.org/saml')).query}",
        "stale": f"{SLS_PATH}?{urlparse(req(issue_instant='2026-01-01T00:00:00Z')).query}",
        "tampered_signature": f"{SLS_PATH}?{q[:-6]}AAAAAA",
        "unknown_provider": f"/api/v1/auth/saml/idptest-bcl-none/sls?{q}",
    }
    for name, path in cases.items():
        resp = await env.client.get(path)
        assert resp.status_code == 400, (name, resp.status_code, resp.text)
        assert resp.json() == {"detail": "Invalid logout request"}, name
        assert await _alive(env, victim), name

    # POST binding is not offered for LogoutRequests
    form = {k: v[0] for k, v in parse_qs(q).items()}
    post = await env.client.post(SLS_PATH, content=urlencode(form), headers=FORM)
    assert post.status_code == 400
    assert await _alive(env, victim)

    ok = await env.client.get(f"{SLS_PATH}?{q}")
    assert ok.status_code == 302
    assert not await _alive(env, victim)

    # replay of the answered request: refused, and it cannot touch a later session
    later = await _saml_login(env, idp, 47141)
    replay = await env.client.get(f"{SLS_PATH}?{q}")
    assert replay.status_code == 400
    assert await _alive(env, later)


@pytest.mark.saml
async def test_logout_response_unsigned_or_unknown_in_response_to_ignored(
    env: Env, idp: SamlTestIdp
) -> None:
    await _saml_provider(env, idp)
    await env.agent(47142, "idp.bc.47142")
    token = await _saml_login(env, idp, 47142)
    env.client.cookies.set(AGENT_COOKIE, token)
    out = await env.client.post("/api/v1/auth/sso/logout", headers={"Origin": BASE})
    env.client.cookies.clear()
    parsed = idp.parse_logout_request(out.headers["location"])
    stored = f"tiqora:saml:logout:{parsed.request_id}"
    assert stored in env.redis.store

    back = idp.logout_response_redirect(sp_slo_url=SP_SLS, in_response_to=parsed.request_id)
    q = urlparse(back).query
    unsigned = "&".join(p for p in q.split("&") if not p.startswith(("Signature=", "SigAlg=")))
    r = await env.client.get(f"{SLS_PATH}?{unsigned}")
    assert (r.status_code, r.headers["location"]) == (303, "/login?logged_out=1")
    assert stored in env.redis.store  # unsigned: not consumed

    unknown = idp.logout_response_redirect(sp_slo_url=SP_SLS, in_response_to="id-never-issued")
    r = await _sls(env, unknown)
    assert r.status_code == 303 and stored in env.redis.store

    form = {k: v[0] for k, v in parse_qs(q).items()}
    r = await env.client.post(SLS_PATH, content=urlencode(form), headers=FORM)
    assert r.status_code == 303 and stored in env.redis.store  # POST binding: not offered

    r = await env.client.get(f"{SLS_PATH}?{q}")
    assert r.status_code == 303
    assert stored not in env.redis.store  # the signed, matching response consumes it


@pytest.mark.saml
async def test_idp_initiated_login_off_by_default(env: Env, idp: SamlTestIdp) -> None:
    pid = await _saml_provider(env, idp)
    await env.agent(47143, "idp.bc.47143")

    def unsolicited() -> str:
        return idp.login_response(
            in_response_to=None,
            acs_url=AGENT_ACS,
            sp_entity_id=SP_ENTITY,
            name_id="u-47143",
            attributes={"uid": ["idp.bc.47143"]},
        )

    off = await _acs(env, unsolicited(), relay="/tickets/7")
    assert off.status_code == 302
    assert parse_qs(urlparse(off.headers["location"]).query)["sso_error"] == ["saml_invalid"]
    assert _session_cookie(off) is None

    await update_provider(
        env.session,
        pid,
        ProviderChanges(config={"allow_idp_initiated": True}),
        secret_key=KEY,
        actor_id=1,
    )
    await env.session.commit()
    record = await get_provider_by_slug(env.session, SAML_SLUG, secret_key=KEY)
    assert record is not None and record.config.allow_idp_initiated  # type: ignore[union-attr]

    body = unsolicited()
    on = await _acs(env, body, relay="/tickets/7")
    assert on.status_code == 200, on.text
    assert 'url=/tickets/7"' in on.text
    token = _session_cookie(on)
    assert token and await _alive(env, token)

    evil = await _acs(env, unsolicited(), relay="https://evil.example.com")
    assert evil.status_code == 200
    assert 'url=/"' in evil.text and "evil.example.com" not in evil.text
    assert _session_cookie(evil)

    replay = await _acs(env, body, relay="/tickets/7")
    assert replay.status_code == 302
    assert parse_qs(urlparse(replay.headers["location"]).query)["sso_error"] == ["saml_invalid"]
    assert _session_cookie(replay) is None
