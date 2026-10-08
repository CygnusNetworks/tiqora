"""SP-initiated single logout, SAML and OIDC (wave 1, Task 11).

ASGI + DB. Seed band 47450-47479, provider slugs ``idptest-lo-*``. The SAML tests run a real
login through the ACS against the in-process pysaml2 IdP of ``tests/_saml_idp.py``; the OIDC
tests use an ``httpx.MockTransport`` IdP whose discovery document can advertise
``end_session_endpoint``. SAML tests skip (hard failure under ``TIQORA_REQUIRE_SAML_TESTS=1``)
when pysaml2 / xmlsec1 are missing; everything else runs without them.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import FakeRedis, cleanup_statements, make_provider, seed_agent
from tests._saml_idp import IDP_SLO_URL, SamlTestIdp, require_saml
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.oidc import OIDCService, clear_discovery_cache
from tiqora.identity import flow_state, sso_routes
from tiqora.identity.config_models import AttributeMap
from tiqora.identity.flow_state import LogoutState

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-logout"
BAND = (47450, 47479)
SLUG = "idptest-lo-"
SAML_SLUG = "idptest-lo-saml"
OIDC_SLUG = "idptest-lo-kc"
BASE = "https://helpdesk.example.org"
SAME = {"Origin": BASE}
SP_SLS = f"{BASE}/api/v1/auth/saml/{SAML_SLUG}/sls"
SLS_PATH = f"/api/v1/auth/saml/{SAML_SLUG}/sls"
LOGGED_OUT = "/login?logged_out=1"
END_SESSION = "https://kc.example.org/realms/y/protocol/openid-connect/logout"
_URLS: set[str] = set()


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in cleanup_statements(user_ids=BAND, slug_prefix=SLUG):
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _jwt(payload: dict[str, Any]) -> str:
    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return ".".join([b64(b'{"alg":"none"}'), b64(json.dumps(payload).encode()), b64(b"sig")])


class FakeOidcIdp:
    def __init__(self) -> None:
        self.end_session: str | None = None
        self.claims: dict[str, dict[str, Any]] = {}
        self.id_tokens: dict[str, str] = {}
        self.discovery_down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.headers["host"], request.url.path
        suffix = "/.well-known/openid-configuration"
        if path.endswith(suffix):
            if self.discovery_down:
                return httpx.Response(503)
            base = f"https://{host}{path[: -len(suffix)]}"
            doc: dict[str, Any] = {
                "issuer": base,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "userinfo_endpoint": f"{base}/userinfo",
            }
            if self.end_session:
                doc["end_session_endpoint"] = self.end_session
            return httpx.Response(200, json=doc)
        if path.endswith("/token"):
            code = parse_qs(request.content.decode())["code"][0]
            body: dict[str, Any] = {"access_token": code, "token_type": "Bearer"}
            if code in self.id_tokens:
                body["id_token"] = self.id_tokens[code]
            return httpx.Response(200, json=body)
        if path.endswith("/userinfo"):
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            if bearer in self.claims:
                return httpx.Response(200, json=self.claims[bearer])
            return httpx.Response(401)
        return httpx.Response(404)


class Env:
    def __init__(self, maker: Any, session: AsyncSession) -> None:
        self.maker = maker
        self.session = session
        self.redis = FakeRedis()
        self.settings = Settings(
            environment="test",
            secret_key=KEY,
            public_base_url=BASE,
            session_cookie_secure=True,
        )
        self.oidc = FakeOidcIdp()
        app = create_app(self.settings)
        app.state.redis = self.redis

        async def _get_db() -> AsyncIterator[AsyncSession]:
            async with self.maker() as s:
                yield s

        app.dependency_overrides[get_db] = _get_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)

    @property
    def store(self) -> SessionStore:
        return SessionStore(self.redis, self.settings)

    async def agent(self, user_id: int, login: str) -> None:
        await seed_agent(self.session, user_id, login)
        await AuthConfigService(self.session).set(user_id, sso_eligible=True)
        await self.session.commit()

    async def password_session(self, user_id: int, login: str) -> str:
        await seed_agent(self.session, user_id, login)
        token = await self.store.create(user_id, login)
        self.client.cookies.set("tiqora_session", token)
        return token

    async def logout(self) -> httpx.Response:
        return await self.client.post("/api/v1/auth/sso/logout", headers=SAME)

    def pending_logouts(self) -> list[str]:
        return [k for k in self.redis.store if k.startswith("tiqora:saml:logout:")]


@pytest.fixture(scope="module")
def idp(tmp_path_factory: pytest.TempPathFactory) -> SamlTestIdp:
    require_saml()
    return SamlTestIdp(tmp_path_factory.mktemp("idp"))


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    clear_discovery_cache()
    monkeypatch.setattr(
        "tiqora.security.outbound._default_resolve", lambda _h, _p: ["93.184.216.34"]
    )
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        e = Env(maker, session)
        transport = httpx.MockTransport(e.oidc.handler)
        monkeypatch.setattr(
            sso_routes, "new_oidc_service", lambda config: OIDCService(config, transport=transport)
        )
        yield e
        await e.client.aclose()
    await engine.dispose()
    clear_discovery_cache()
    _clean(url)


# ------------------------------------------------------------------------ password / basics


async def test_logout_without_sso_session_goes_to_login(env: Env) -> None:
    token = await env.password_session(47451, "idp.lo.47451")
    resp = await env.logout()
    assert resp.status_code == 303
    assert resp.headers["location"] == LOGGED_OUT
    assert await env.store.get(token) is None
    assert any(
        c.startswith("tiqora_session=") and "Max-Age=0" in c
        for c in resp.headers.get_list("set-cookie")
    )


async def test_logout_without_any_cookie_still_redirects(env: Env) -> None:
    resp = await env.logout()
    assert (resp.status_code, resp.headers["location"]) == (303, LOGGED_OUT)


async def test_existing_logout_endpoint_unchanged(env: Env) -> None:
    token = await env.password_session(47452, "idp.lo.47452")
    resp = await env.client.post("/api/v1/auth/logout", headers=SAME)
    assert resp.status_code == 204
    assert await env.store.get(token) is None


async def test_me_reports_sso_logout_false_for_password_session(env: Env) -> None:
    await env.password_session(47453, "idp.lo.47453")
    me = await env.client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["sso_logout"] is False


async def test_logout_is_post_only_and_csrf_checked(env: Env) -> None:
    token = await env.password_session(47454, "idp.lo.47454")
    assert (await env.client.get("/api/v1/auth/sso/logout")).status_code == 405
    foreign = await env.client.post(
        "/api/v1/auth/sso/logout", headers={"Origin": "https://evil.example.org"}
    )
    assert foreign.status_code == 403
    assert await env.store.get(token) is not None  # cross-site POST cannot log the user out


# ------------------------------------------------------------------------ flow state (pure)


async def test_logout_request_state_is_bound_to_provider_and_one_shot() -> None:
    redis = FakeRedis()
    state = LogoutState(provider_id=7, user_kind="agent")
    await flow_state.put_logout_request(redis, "id-1", state)
    assert redis.ttls["tiqora:saml:logout:id-1"] == flow_state.LOGOUT_REQUEST_TTL
    # a response arriving at another provider's SLS neither matches nor consumes it
    assert await flow_state.pop_logout_request(redis, "id-1", provider_id=8) is None
    assert "tiqora:saml:logout:id-1" in redis.store
    assert await flow_state.pop_logout_request(redis, "id-1", provider_id=7) == state
    # replay
    assert await flow_state.pop_logout_request(redis, "id-1", provider_id=7) is None
    assert await flow_state.pop_logout_request(redis, "nope", provider_id=7) is None


async def test_logout_state_garbage_is_ignored() -> None:
    redis = FakeRedis()
    redis.store["tiqora:saml:logout:x"] = "not json"
    assert await flow_state.pop_logout_request(redis, "x", provider_id=1) is None
    redis.store["tiqora:saml:logout:y"] = json.dumps({"p": 1, "a": "root"})
    assert await flow_state.pop_logout_request(redis, "y", provider_id=1) is None


# ------------------------------------------------------------------------ OIDC


async def _kc(env: Env) -> None:
    await make_provider(
        env.session,
        slug=OIDC_SLUG,
        attribute_map=AttributeMap(login=["preferred_username"]),
        config={
            "issuer": "https://kc.example.org/realms/y",
            "client_id": "tiqora",
            "client_secret": "kc-secret",
        },
        settings=env.settings,
    )
    await env.session.commit()


async def _oidc_login(env: Env, user_id: int) -> str:
    await env.agent(user_id, f"idp.lo.{user_id}")
    env.oidc.claims["c"] = {"sub": f"kc-{user_id}", "preferred_username": f"idp.lo.{user_id}"}
    env.oidc.id_tokens["c"] = _jwt({"sub": f"kc-{user_id}", "iss": "x"})
    start = await env.client.get(f"/api/v1/auth/sso/{OIDC_SLUG}/login")
    assert start.status_code == 302, start.text
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    cb = await env.client.get(
        f"/api/v1/auth/oidc/{OIDC_SLUG}/callback", params={"code": "c", "state": state}
    )
    assert cb.status_code == 302, cb.text
    token = env.client.cookies.get("tiqora_session")
    assert token
    return token


async def test_oidc_end_session(env: Env) -> None:
    env.oidc.end_session = END_SESSION
    await _kc(env)
    token = await _oidc_login(env, 47461)
    me = await env.client.get("/api/v1/auth/me")
    assert me.json()["sso_logout"] is True

    resp = await env.logout()
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith(END_SESSION + "?")
    assert (
        "post_logout_redirect_uri=https%3A%2F%2Fhelpdesk.example.org%2Flogin%3Flogged_out%3D1"
        in loc
    )
    q = parse_qs(urlparse(loc).query)
    assert q["id_token_hint"] == [_jwt({"sub": "kc-47461", "iss": "x"})]
    assert q["client_id"] == ["tiqora"]
    assert await env.store.get(token) is None
    assert await env.store.get_sso(token) is None


async def test_oidc_without_end_session_endpoint_goes_to_login(env: Env) -> None:
    await _kc(env)
    token = await _oidc_login(env, 47462)
    resp = await env.logout()
    assert (resp.status_code, resp.headers["location"]) == (303, LOGGED_OUT)
    assert await env.store.get(token) is None


async def test_oidc_local_logout_succeeds_when_idp_is_down(env: Env) -> None:
    env.oidc.end_session = END_SESSION
    await _kc(env)
    token = await _oidc_login(env, 47463)
    clear_discovery_cache()
    env.oidc.discovery_down = True
    resp = await env.logout()
    assert (resp.status_code, resp.headers["location"]) == (303, LOGGED_OUT)
    assert await env.store.get(token) is None


async def test_oidc_unsafe_end_session_scheme_is_not_followed(env: Env) -> None:
    env.oidc.end_session = "javascript:alert(1)"
    await _kc(env)
    await _oidc_login(env, 47464)
    resp = await env.logout()
    assert (resp.status_code, resp.headers["location"]) == (303, LOGGED_OUT)


# ------------------------------------------------------------------------ SAML


async def _saml_provider(env: Env, idp: SamlTestIdp, **cfg: Any) -> None:
    await make_provider(
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


async def _saml_login(env: Env, idp: SamlTestIdp, user_id: int) -> str:
    await env.agent(user_id, f"idp.lo.{user_id}")
    start = await env.client.get(f"/api/v1/auth/sso/{SAML_SLUG}/login?next=/tickets")
    assert start.status_code == 302, start.text
    req_id, _acs, relay = idp.parse_login_redirect(start.headers["location"])
    body = idp.login_response(
        in_response_to=req_id,
        acs_url=f"{BASE}/api/v1/auth/saml/{SAML_SLUG}/acs",
        sp_entity_id=f"{BASE}/api/v1/auth/saml/{SAML_SLUG}/metadata",
        name_id=f"u-{user_id}",
        attributes={"uid": [f"idp.lo.{user_id}"]},
    )
    acs = await env.client.post(
        f"/api/v1/auth/saml/{SAML_SLUG}/acs",
        content=urlencode({"SAMLResponse": body, "RelayState": relay}),
        headers={
            "Origin": "https://idp.example.org",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"tiqora_saml_req={req_id}",
        },
    )
    assert acs.status_code == 200, acs.text
    token = next(
        c.split(";")[0].split("=", 1)[1]
        for c in acs.headers.get_list("set-cookie")
        if c.startswith("tiqora_session=")
    )
    env.client.cookies.clear()
    env.client.cookies.set("tiqora_session", token)
    return token


@pytest.mark.saml
async def test_saml_sp_initiated_logout(env: Env, idp: SamlTestIdp) -> None:
    await _saml_provider(env, idp)
    token = await _saml_login(env, idp, 47471)
    stored = await env.store.get_sso(token)
    assert stored is not None and stored.session_index

    me = await env.client.get("/api/v1/auth/me")
    assert me.json()["sso_logout"] is True

    resp = await env.logout()
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith(IDP_SLO_URL + "?")
    assert await env.store.get(token) is None  # local session is gone BEFORE the IdP round trip
    assert any(
        c.startswith("tiqora_session=") and "Max-Age=0" in c
        for c in resp.headers.get_list("set-cookie")
    )
    parsed = idp.parse_logout_request(loc)  # also asserts the redirect is signed
    assert parsed.name_id == "u-47471"
    assert parsed.session_index == stored.session_index
    assert env.pending_logouts() == [f"tiqora:saml:logout:{parsed.request_id}"]

    back = idp.logout_response_redirect(sp_slo_url=SP_SLS, in_response_to=parsed.request_id)
    sls = await env.client.get(f"{SLS_PATH}?{urlparse(back).query}")
    assert sls.status_code == 303
    assert sls.headers["location"] == LOGGED_OUT
    assert env.pending_logouts() == []

    # the same response again (replay) is harmless and lands on the login page too
    again = await env.client.get(f"{SLS_PATH}?{urlparse(back).query}")
    assert (again.status_code, again.headers["location"]) == (303, LOGGED_OUT)


@pytest.mark.saml
async def test_saml_logout_response_must_match_a_stored_request(env: Env, idp: SamlTestIdp) -> None:
    await _saml_provider(env, idp)
    await _saml_login(env, idp, 47472)
    resp = await env.logout()
    parsed = idp.parse_logout_request(resp.headers["location"])
    stored_key = f"tiqora:saml:logout:{parsed.request_id}"

    forged = idp.logout_response_redirect(sp_slo_url=SP_SLS, in_response_to="id-never-issued")
    r = await env.client.get(f"{SLS_PATH}?{urlparse(forged).query}")
    assert (r.status_code, r.headers["location"]) == (303, LOGGED_OUT)
    assert stored_key in env.redis.store  # the unrelated response did not consume it

    garbage = await env.client.get(f"{SLS_PATH}?SAMLResponse=AAAA")
    assert (garbage.status_code, garbage.headers["location"]) == (303, LOGGED_OUT)
    assert stored_key in env.redis.store

    empty = await env.client.get(SLS_PATH)
    assert (empty.status_code, empty.headers["location"]) == (303, LOGGED_OUT)


@pytest.mark.saml
async def test_saml_idp_initiated_logout_request_not_supported_yet(
    env: Env, idp: SamlTestIdp
) -> None:
    await _saml_provider(env, idp)
    url = idp.logout_request_redirect(sp_slo_url=SP_SLS, name_id="u-1", session_index="s1")
    r = await env.client.get(f"{SLS_PATH}?{urlparse(url).query}")
    assert r.status_code == 400
    assert "IdP-initiated logout not supported yet" in r.text


@pytest.mark.saml
async def test_saml_local_logout_succeeds_when_sp_cannot_build_request(
    env: Env, idp: SamlTestIdp, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _saml_provider(env, idp)
    token = await _saml_login(env, idp, 47473)

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("xmlsec exploded")

    monkeypatch.setattr(sso_routes.SamlServiceProvider, "logout_redirect", boom)
    resp = await env.logout()
    assert (resp.status_code, resp.headers["location"]) == (303, LOGGED_OUT)
    assert await env.store.get(token) is None
    assert env.pending_logouts() == []


@pytest.mark.saml
async def test_saml_idp_without_slo_goes_to_login(env: Env, idp: SamlTestIdp) -> None:
    no_slo = re.sub(r"<(\w+:)?SingleLogoutService\b[^>]*/>", "", idp.metadata_xml(), flags=re.S)
    assert "SingleLogoutService" not in no_slo
    await _saml_provider(env, idp, idp_metadata_xml=no_slo)
    token = await _saml_login(env, idp, 47474)
    me = await env.client.get("/api/v1/auth/me")
    assert me.json()["sso_logout"] is False
    resp = await env.logout()
    assert (resp.status_code, resp.headers["location"]) == (303, LOGGED_OUT)
    assert await env.store.get(token) is None
