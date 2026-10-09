"""SAML login for agents: start, ACS, SP metadata and replay protection (wave 1, Task 10).

ASGI + DB with an in-process pysaml2 IdP. Seed band 47100-47119 (agents
47101-47111), provider slugs ``idptest-saml-*``.
"""

from __future__ import annotations

import base64
import shutil
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import FakeRedis, cleanup_statements, make_provider, seed_agent
from tests._saml_idp import IDP_SSO_URL, SamlTestIdp, require_saml
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.domain.auth_config import AuthConfigService
from tiqora.identity.config_models import AttributeMap
from tiqora.identity.saml_runtime import SamlAvailability

pytestmark = [pytest.mark.saml, pytest.mark.db]
require_saml()
assert shutil.which("xmlsec1")

KEY = "unit-test-secret-key-for-identity-saml-flow"
BAND = (47100, 47119)
SLUG = "idptest-saml-"
PROVIDER = "idptest-saml-a"
BASE = "https://helpdesk.example.org"
ACS_PATH = f"/api/v1/auth/saml/{PROVIDER}/acs"
LOGIN_PATH = f"/api/v1/auth/sso/{PROVIDER}/login"
FOREIGN = {"Origin": "https://idp.example.org"}
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


@pytest.fixture(scope="module")
def idp(tmp_path_factory: pytest.TempPathFactory) -> SamlTestIdp:
    return SamlTestIdp(tmp_path_factory.mktemp("idp"))


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "test",
        "secret_key": KEY,
        "public_base_url": BASE,
        "session_cookie_secure": True,
    }
    base.update(kw)
    return Settings(**base)


class Env:
    def __init__(self, maker: Any, session: AsyncSession, idp: SamlTestIdp) -> None:
        self.maker = maker
        self.session = session
        self.idp = idp
        self.redis = FakeRedis()
        self.settings = _settings()
        self.client: httpx.AsyncClient = None  # type: ignore[assignment]
        self.spec = "" if False else ""
        self.sp_entity = f"{BASE}/api/v1/auth/saml/{PROVIDER}/metadata"
        self.acs_url = f"{BASE}{ACS_PATH}"

    async def use(self, settings: Settings) -> None:
        """(Re)build the app + client for *settings*; the DB and Redis are kept."""
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

    async def start(
        self, path: str = f"{LOGIN_PATH}?next=/tickets"
    ) -> tuple[str, str, httpx.Response]:
        """SP-initiated start; returns ``(request_id, relay_state, response)``."""
        resp = await self.client.get(path)
        assert resp.status_code == 302, resp.text
        assert resp.headers["location"].startswith(IDP_SSO_URL)
        req_id, acs, relay = self.idp.parse_login_redirect(resp.headers["location"])
        assert acs == self.acs_url
        self.client.cookies.clear()
        return req_id, relay, resp

    def response_for(self, req_id: str | None, **kw: Any) -> str:
        args: dict[str, Any] = {
            "in_response_to": req_id,
            "acs_url": self.acs_url,
            "sp_entity_id": self.sp_entity,
            "name_id": "u-47101",
            "attributes": {"uid": ["idp.saml.47101"]},
        }
        args.update(kw)
        return self.idp.login_response(**args)

    async def acs(
        self, saml_response: str, *, req_id: str | None, relay: str = "", cookies: bool = True
    ) -> httpx.Response:
        jar = "tiqora_session=x; tiqora_customer_session=y"
        if cookies and req_id:
            jar += f"; tiqora_saml_req={req_id}"
        return await self.client.post(
            ACS_PATH,
            content=urlencode({"SAMLResponse": saml_response, "RelayState": relay}),
            headers={
                **FOREIGN,
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": jar,
            },
        )


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest, idp: SamlTestIdp) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        e = Env(maker, session, idp)
        await e.use(e.settings)
        yield e
        await e.client.aclose()
    await engine.dispose()
    _clean(url)


async def _provider(env: Env, *, slug: str = PROVIDER, **cfg: Any) -> Any:
    rec = await make_provider(
        env.session,
        slug=slug,
        kind="saml",
        attribute_map=AttributeMap(login=["uid"]),
        config={
            "idp_metadata_xml": env.idp.metadata_xml(),
            "name_id_format": "persistent",
            **cfg,
        },
        settings=env.settings,
    )
    await env.session.commit()
    meta = await env.client.get(f"/api/v1/auth/saml/{slug}/metadata")
    assert meta.status_code == 200, meta.text
    env.idp.trust_sp(meta.text)
    return rec


async def _agent(env: Env, user_id: int, login: str) -> None:
    await seed_agent(env.session, user_id, login)
    await AuthConfigService(env.session).set(user_id, sso_eligible=True)
    await env.session.commit()


def _error(resp: httpx.Response) -> str:
    assert resp.status_code == 302, resp.text
    return parse_qs(urlparse(resp.headers["location"]).query)["sso_error"][0]


async def test_saml_agent_login_round_trip(env: Env) -> None:
    await _agent(env, 47101, "idp.saml.47101")
    await _provider(env)
    req_id, relay, start = await env.start()
    cookie = start.headers["set-cookie"]
    assert f"tiqora_saml_req={req_id}" in cookie
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=none" in cookie.lower()
    assert relay and relay != "/tickets"  # opaque token, next lives in Redis
    assert f"tiqora:saml:req:{req_id}" in env.redis.store

    resp = await env.acs(env.response_for(req_id), req_id=req_id, relay=relay)
    assert resp.status_code == 200, resp.text
    assert "url=/tickets" in resp.text
    set_cookies = resp.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookies if c.startswith("tiqora_session="))
    token = session_cookie.split(";")[0].split("=", 1)[1]
    assert token and token != "x"
    assert any(c.startswith("tiqora_saml_req=") and "Max-Age=0" in c for c in set_cookies)
    sso = await SessionStore(env.redis, env.settings).get_sso(token)
    assert sso is not None
    assert sso.name_id == "u-47101" and sso.session_index
    assert f"tiqora:saml:req:{req_id}" not in env.redis.store


async def test_saml_replay_rejected(env: Env) -> None:
    await _agent(env, 47102, "idp.saml.47102")
    await _provider(env)
    req_id, relay, _ = await env.start()
    body = env.response_for(req_id, attributes={"uid": ["idp.saml.47102"]})
    ok = await env.acs(body, req_id=req_id, relay=relay)
    assert ok.status_code == 200, ok.text
    again = await env.acs(body, req_id=req_id, relay=relay)
    assert _error(again) == "saml_invalid"
    assert again.headers["location"].startswith("/login?")
    assert not any(c.startswith("tiqora_session=") for c in again.headers.get_list("set-cookie"))
    # the assertion id is remembered for at least a minute
    seen = [k for k in env.redis.store if k.startswith("tiqora:saml:seen:")]
    assert len(seen) == 1 and env.redis.ttls[seen[0]] >= 60


async def test_saml_cookie_binding_enforced_when_secure(env: Env) -> None:
    await _agent(env, 47103, "idp.saml.47103")
    await _provider(env)
    attrs = {"uid": ["idp.saml.47103"]}
    req_id, relay, _ = await env.start()
    resp = await env.acs(env.response_for(req_id, attributes=attrs), req_id=req_id, cookies=False)
    assert _error(resp) == "state"
    # the request survives a rejected binding, so the real browser can still finish
    assert f"tiqora:saml:req:{req_id}" in env.redis.store
    wrong = await env.client.post(
        ACS_PATH,
        content=urlencode({"SAMLResponse": env.response_for(req_id, attributes=attrs)}),
        headers={
            **FOREIGN,
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": "tiqora_saml_req=someone-elses-id",
        },
    )
    assert _error(wrong) == "state"

    # Binding does not depend on session_cookie_secure: plain-HTTP setups are bound too.
    await env.use(_settings(session_cookie_secure=False))
    req_id, relay, start = await env.start()
    assert "tiqora_saml_req" in start.headers["set-cookie"]  # cookie flags follow the URL scheme
    body = env.response_for(req_id, attributes=attrs)
    unbound = await env.acs(body, req_id=req_id, relay=relay, cookies=False)
    assert _error(unbound) == "state"
    assert parse_qs(urlparse(unbound.headers["location"]).query)["next"] == ["/tickets"]
    ok = await env.acs(body, req_id=req_id, relay=relay)
    assert ok.status_code == 200, ok.text


async def test_saml_invalid_response_lands_on_login(env: Env) -> None:
    await _agent(env, 47104, "idp.saml.47104")
    await _provider(env)
    req_id, relay, _ = await env.start()
    unsigned = env.response_for(req_id, sign_assertion=False)
    resp = await env.acs(unsigned, req_id=req_id, relay=relay)
    assert _error(resp) == "saml_invalid"
    q = parse_qs(urlparse(resp.headers["location"]).query)
    assert q["next"] == ["/tickets"]
    assert not any(c.startswith("tiqora_session=") for c in resp.headers.get_list("set-cookie"))
    # a forged response must not burn the genuine request
    assert f"tiqora:saml:req:{req_id}" in env.redis.store

    garbage = await env.acs(base64.b64encode(b"<nope").decode(), req_id=req_id)
    assert _error(garbage) == "saml_invalid"
    no_field = await env.client.post(ACS_PATH, content=b"", headers={**FOREIGN})
    assert _error(no_field) == "saml_invalid"


async def test_saml_unknown_request_rejected_even_with_idp_initiated(env: Env) -> None:
    await _agent(env, 47105, "idp.saml.47105")
    await _provider(env, allow_idp_initiated=True)
    body = env.response_for("id-never-issued", attributes={"uid": ["idp.saml.47105"]})
    resp = await env.acs(body, req_id="id-never-issued")
    assert _error(resp) == "saml_invalid"


async def test_saml_idp_initiated_when_allowed(env: Env) -> None:
    await _agent(env, 47106, "idp.saml.47106")
    await _agent(env, 47107, "idp.saml.47107")
    await _provider(env, allow_idp_initiated=True)
    ok = await env.acs(
        env.response_for(None, attributes={"uid": ["idp.saml.47106"]}),
        req_id=None,
        relay="/tickets?q=1",
    )
    assert ok.status_code == 200 and "url=/tickets?q=1" in ok.text
    evil = await env.acs(
        env.response_for(None, attributes={"uid": ["idp.saml.47107"]}),
        req_id=None,
        relay="//evil.example.net",
    )
    assert evil.status_code == 200 and 'url=/"' in evil.text


async def test_saml_idp_initiated_refused_by_default(env: Env) -> None:
    await _agent(env, 47108, "idp.saml.47108")
    await _provider(env)
    resp = await env.acs(
        env.response_for(None, attributes={"uid": ["idp.saml.47108"]}), req_id=None
    )
    assert _error(resp) == "saml_invalid"


async def test_saml_unavailable(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    await _provider(env)
    missing = SamlAvailability(False, "xmlsec1", None)
    monkeypatch.setattr("tiqora.identity.sso_routes.saml_availability", lambda: missing)
    monkeypatch.setattr("tiqora.identity.login_providers.saml_availability", lambda: missing)
    monkeypatch.setattr("tiqora.api.v1.auth.saml_availability", lambda: missing)
    resp = await env.client.get(LOGIN_PATH)
    assert _error(resp) == "saml_unavailable"
    methods = (await env.client.get("/api/v1/auth/methods")).json()
    assert methods["saml"] is False
    assert all(p["kind"] != "saml" for p in methods["providers"])


async def test_sp_metadata_endpoint(env: Env) -> None:
    rec = await _provider(env)
    resp = await env.client.get(f"/api/v1/auth/saml/{PROVIDER}/metadata")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/samlmetadata+xml")
    assert f"{BASE}/api/v1/auth/saml/{PROVIDER}/acs" in resp.text
    assert f"{BASE}/api/portal/auth/saml/{PROVIDER}/acs" in resp.text
    cert_body = "".join(rec.config.sp_certificate.splitlines()[1:-1])
    assert cert_body in resp.text.replace("\n", "")
    assert "PRIVATE KEY" not in resp.text
    assert (await env.client.get("/api/v1/auth/saml/idptest-saml-nope/metadata")).status_code == 404
    await make_provider(
        env.session,
        slug="idptest-saml-ldap",
        kind="ldap",
        settings=env.settings,
    )
    await env.session.commit()
    assert (await env.client.get("/api/v1/auth/saml/idptest-saml-ldap/metadata")).status_code == 404


async def test_base_url_missing(env: Env) -> None:
    await _provider(env)
    await env.use(_settings(public_base_url="", cors_origins=""))
    resp = await env.client.get(LOGIN_PATH)
    assert _error(resp) == "config"


@pytest.mark.parametrize("nxt", ["/%09/evil.example.net", "/%0a/evil.example.net", "/%5Cevil"])
async def test_next_control_chars_and_backslash_never_redirect_off_site(env: Env, nxt: str) -> None:
    await _agent(env, 47109, "idp.saml.47109")
    await _provider(env, allow_idp_initiated=True)
    req_id, relay, _ = await env.start(f"{LOGIN_PATH}?next={nxt}")
    ok = await env.acs(
        env.response_for(req_id, attributes={"uid": ["idp.saml.47109"]}), req_id=req_id
    )
    assert ok.status_code == 200 and 'url=/"' in ok.text
    # IdP-initiated RelayState goes through the same filter
    decoded = unquote(nxt)
    idp = await env.acs(
        env.response_for(None, attributes={"uid": ["idp.saml.47109"]}),
        req_id=None,
        relay=decoded,
    )
    assert idp.status_code == 200 and 'url=/"' in idp.text


async def test_assertion_without_expiry_is_rejected(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _agent(env, 47110, "idp.saml.47110")
    await _provider(env)
    req_id, _relay, _ = await env.start()
    body = env.response_for(req_id, attributes={"uid": ["idp.saml.47110"]})
    # a signed expiry cannot be stripped from a real response, so make the SP see none
    monkeypatch.setattr("tiqora.identity.saml_sp._instant", lambda _v: None)
    resp = await env.acs(body, req_id=req_id)
    assert _error(resp) == "saml_invalid"
    assert not any(k.startswith("tiqora:saml:seen:") for k in env.redis.store)


async def test_replay_ttl_is_bounded_by_assertion_expiry(env: Env) -> None:
    await _agent(env, 47111, "idp.saml.47111")
    await _provider(env, clock_skew_seconds=120)
    req_id, relay, _ = await env.start()
    ok = await env.acs(
        env.response_for(req_id, attributes={"uid": ["idp.saml.47111"]}), req_id=req_id
    )
    assert ok.status_code == 200
    (key,) = [k for k in env.redis.store if k.startswith("tiqora:saml:seen:")]
    assert 60 <= env.redis.ttls[key] <= 15 * 60 + 120 + 5


async def test_saml_admin_test_run_on_disabled_provider_applies_draft_and_writes_nothing(
    env: Env,
) -> None:
    from tiqora.identity import flow_state
    from tiqora.identity.store import ProviderChanges, update_provider

    admin_id = 47110
    await _agent(env, admin_id, "idp.saml.47110")
    rec = await _provider(env)
    await update_provider(
        env.session,
        rec.id,
        ProviderChanges(enabled=False),
        secret_key=KEY,
        actor_id=1,
    )
    await env.session.commit()
    token = await SessionStore(env.redis, env.settings).create(admin_id, "idp.saml.47110")
    run = flow_state.TestRun(
        provider_id=rec.id,
        admin_id=admin_id,
        audience="agent",
        # The IdP sends the login in "alt"; only the unsaved draft knows that.
        draft=ProviderChanges(attribute_map=AttributeMap(login=["alt"])),
    )
    await flow_state.put_test(env.redis, "tid-47110", run, secret_key=KEY)

    # No admin cookie, or a disabled provider without a test: refused.
    anon = await env.client.get(f"{LOGIN_PATH}?test=tid-47110")
    assert _error(anon) == "provider_disabled"
    plain = await env.client.get(LOGIN_PATH)
    assert _error(plain) == "provider_disabled"

    resp = await env.client.get(
        f"{LOGIN_PATH}?test=tid-47110", headers={"Cookie": f"tiqora_session={token}"}
    )
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith(IDP_SSO_URL)
    req_id, _acs, relay = env.idp.parse_login_redirect(resp.headers["location"])
    env.client.cookies.clear()

    body = env.response_for(req_id, attributes={"uid": ["nobody.47199"], "alt": ["idp.saml.47110"]})
    done = await env.acs(body, req_id=req_id, relay=relay)
    assert done.status_code == 302, done.text
    assert done.headers["location"] == f"/admin/identity-providers/{rec.id}?test=tid-47110"
    assert not any(c.startswith("tiqora_session=") for c in done.headers.get_list("set-cookie"))

    stored = await flow_state.get_test(env.redis, "tid-47110", secret_key=KEY)
    assert stored is not None and stored.status == "done" and stored.result is not None
    assert stored.result.plan["decision"] == "login"
    assert stored.result.plan["user_id"] == admin_id
    links = (
        await env.session.execute(
            text("SELECT COUNT(*) FROM tiqora_identity_link WHERE provider_id = :p"),
            {"p": rec.id},
        )
    ).scalar_one()
    assert links == 0
