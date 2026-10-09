"""Customer SSO in the portal: OIDC, SAML, logout, JIT and agent/customer separation (Task 20).

ASGI + DB. Seed band 47260-47289: customers ``idp.kunde.4726x@example.org``, agents 47270-47279,
provider slugs ``idptest-portal-*``. OIDC tests use the fake IdP of ``test_identity_oidc_flow``;
SAML tests run a real ACS round trip against the in-process pysaml2 IdP and skip (hard failure
under ``TIQORA_REQUIRE_SAML_TESTS=1``) when pysaml2 / xmlsec1 are missing.
"""

from __future__ import annotations

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
    cleanup_statements,
    make_provider,
    seed_agent,
    seed_customer,
)
from tests._saml_idp import IDP_SLO_URL, IDP_SSO_URL, SamlTestIdp, require_saml
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
from tiqora.identity import sso_routes
from tiqora.identity.config_models import AttributeMap, JitDefaults
from tiqora.identity.store import ProviderChanges, update_provider

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-portal-sso"
BAND = (47260, 47289)
SLUG = "idptest-portal-"
BASE = "https://helpdesk.example.org"
SAME = {"Origin": BASE}
CUSTOMER_COOKIE = "tiqora_customer_session"
AGENT_COOKIE = "tiqora_session"
CUSTOMER_LIKE = "idp.kunde.4726%@example.org"
_URLS: set[str] = set()


_INVAL_MARK: dict[str, int] = {}  # url -> MAX(id) of tiqora_cache_invalidation at module start


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            if url not in _INVAL_MARK:
                row = conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
                )
                _INVAL_MARK[url] = int(row.scalar_one())
            conn.execute(text("DELETE FROM customer_user WHERE login LIKE 'idp.agent.4727%'"))
            conn.execute(
                text("DELETE FROM tiqora_cache_invalidation WHERE id > :mx"),
                {"mx": _INVAL_MARK[url]},
            )
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


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "test",
        "secret_key": KEY,
        "public_base_url": BASE,
        "session_cookie_secure": True,
        "portal_enabled": True,
    }
    base.update(kw)
    return Settings(**base)


class Env:
    def __init__(self, maker: Any, session: AsyncSession) -> None:
        self.maker = maker
        self.session = session
        self.redis = FakeRedis()
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

    @property
    def customer_store(self) -> CustomerSessionStore:
        return CustomerSessionStore(self.redis, self.settings)

    @property
    def agent_store(self) -> SessionStore:
        return SessionStore(self.redis, self.settings)

    async def agent(self, user_id: int, login: str) -> None:
        await seed_agent(self.session, user_id, login)
        await AuthConfigService(self.session).set(user_id, sso_eligible=True)
        await self.session.commit()

    async def start_oidc(self, path: str) -> dict[str, list[str]]:
        resp = await self.client.get(path)
        assert resp.status_code == 302, resp.text
        return parse_qs(urlparse(resp.headers["location"]).query)


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


async def _oidc_provider(
    env: Env,
    slug: str,
    *,
    audience: Any = "customer",
    jit_defaults: JitDefaults | None = None,
    **kw: Any,
) -> Any:
    rec = await make_provider(
        env.session,
        slug=f"{SLUG}{slug}",
        audience=audience,
        attribute_map=kw.pop("attribute_map", AttributeMap(login=["preferred_username"])),
        config={
            "issuer": f"https://{slug}.example.org/realms/y",
            "client_id": "tiqora",
            "client_secret": "kc-secret",
        },
        settings=env.settings,
        **kw,
    )
    if jit_defaults is not None:
        rec = await update_provider(
            env.session,
            rec.id,
            ProviderChanges(jit_defaults=jit_defaults),
            secret_key=KEY,
            actor_id=1,
        )
    await env.session.commit()
    return rec


def _error(resp: httpx.Response) -> tuple[str, str]:
    """``(path, sso_error)`` of a 302 to a login page."""
    assert resp.status_code == 302, resp.text
    parts = urlparse(resp.headers["location"])
    return parts.path, parse_qs(parts.query)["sso_error"][0]


def _set_cookie_names(resp: httpx.Response) -> set[str]:
    """Names of cookies the response SETS (deletions via Max-Age=0 do not count)."""
    return {
        c.split("=", 1)[0]
        for c in resp.headers.get_list("set-cookie")
        if "max-age=0" not in c.lower()
    }


# ------------------------------------------------------------------------- discovery / gate


async def test_portal_methods(env: Env) -> None:
    await _oidc_provider(env, "cust", audience="customer")
    await _oidc_provider(env, "both", audience="both")
    await _oidc_provider(env, "agent", audience="agent")
    await _oidc_provider(env, "off", audience="customer", enabled=False)

    resp = await env.client.get("/api/portal/auth/methods")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["password"] is True
    slugs = [p["slug"] for p in body["providers"]]
    assert set(slugs) == {f"{SLUG}cust", f"{SLUG}both"}  # agent-only and disabled are absent
    assert {p["kind"] for p in body["providers"]} == {"oidc"}

    agent_slugs = {
        p["slug"] for p in (await env.client.get("/api/v1/auth/methods")).json()["providers"]
    }
    assert agent_slugs == {f"{SLUG}agent", f"{SLUG}both"}  # and the reverse for agents


async def test_portal_disabled_answers_404_on_every_new_route(env: Env) -> None:
    await _oidc_provider(env, "gate", audience="customer")
    await env.use(_settings(portal_enabled=False))
    slug = f"{SLUG}gate"
    form = {
        "content": urlencode({"SAMLResponse": "x"}),
        "headers": {**SAME, "Content-Type": "application/x-www-form-urlencoded"},
    }
    results = {
        "methods": await env.client.get("/api/portal/auth/methods"),
        "login": await env.client.get(f"/api/portal/auth/sso/{slug}/login"),
        "callback": await env.client.get(
            f"/api/portal/auth/oidc/{slug}/callback", params={"code": "c", "state": "s"}
        ),
        "acs": await env.client.post(f"/api/portal/auth/saml/{slug}/acs", **form),
        "logout": await env.client.post("/api/portal/auth/sso/logout", headers=SAME),
    }
    assert {k: r.status_code for k, r in results.items()} == dict.fromkeys(results, 404)
    assert not any(r.headers.get_list("set-cookie") for r in results.values())


# ------------------------------------------------------------------------------------- OIDC


async def test_portal_oidc_login(env: Env) -> None:
    await seed_customer(env.session, "idp.kunde.47261@example.org")
    await _oidc_provider(env, "kc", match_by="email", attribute_map=AttributeMap(email=["email"]))
    env.oidc.claims["c"] = {
        "sub": "kunde-1",
        "email": "idp.kunde.47261@example.org",
        "email_verified": True,
    }

    q = await env.start_oidc(f"/api/portal/auth/sso/{SLUG}kc/login?next=/portal/tickets")
    assert q["redirect_uri"] == [f"{BASE}/api/portal/auth/oidc/{SLUG}kc/callback"]
    resp = await env.client.get(
        f"/api/portal/auth/oidc/{SLUG}kc/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"] == "/portal/tickets"
    assert _set_cookie_names(resp) == {CUSTOMER_COOKIE}  # never an agent cookie
    token = env.client.cookies.get(CUSTOMER_COOKIE)
    assert token and env.client.cookies.get(AGENT_COOKIE) is None
    info = await env.customer_store.get_sso(token)
    assert info is not None and (info.slug, info.kind) == (f"{SLUG}kc", "oidc")
    me = await env.client.get("/api/portal/auth/me")
    assert me.status_code == 200 and me.json()["login"] == "idp.kunde.47261@example.org"
    assert me.json()["sso_logout"] is False  # no id_token_hint, no IdP logout


async def test_portal_error_redirects_stay_on_portal_login(env: Env) -> None:
    await _oidc_provider(env, "err", match_by="email", attribute_map=AttributeMap(email=["email"]))
    env.oidc.claims["c"] = {
        "sub": "nobody",
        "email": "idp.kunde.47269@example.org",
        "email_verified": True,
    }
    q = await env.start_oidc(f"/api/portal/auth/sso/{SLUG}err/login")
    resp = await env.client.get(
        f"/api/portal/auth/oidc/{SLUG}err/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert _error(resp) == ("/portal/login", "no_match")
    assert not any(
        c.split("=", 1)[0] == CUSTOMER_COOKIE for c in resp.headers.get_list("set-cookie")
    )

    unknown = await env.client.get("/api/portal/auth/sso/idptest-portal-nope/login")
    assert _error(unknown) == ("/portal/login", "unknown_provider")


async def test_agent_state_cannot_finish_as_customer(env: Env) -> None:
    """State minted by one login surface is useless at the other one (both directions)."""
    await env.agent(47270, "idp.agent.47270")
    await seed_customer(env.session, "idp.agent.47270@example.org")
    await _oidc_provider(env, "both", audience="both")
    env.oidc.claims["c"] = {"sub": "s-both", "preferred_username": "idp.agent.47270"}
    slug = f"{SLUG}both"

    q = await env.start_oidc(f"/api/v1/auth/sso/{slug}/login")
    resp = await env.client.get(
        f"/api/portal/auth/oidc/{slug}/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert _error(resp) == ("/portal/login", "state")
    assert env.client.cookies.get(CUSTOMER_COOKIE) is None

    q = await env.start_oidc(f"/api/portal/auth/sso/{slug}/login")
    resp = await env.client.get(
        f"/api/v1/auth/oidc/{slug}/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert _error(resp) == ("/login", "state")
    assert env.client.cookies.get(AGENT_COOKIE) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/admin", "/portal"),
        ("//evil.example.net", "/portal"),
        ("/\\evil.example.net", "/portal"),
        ("/%09/evil.example.net", "/portal"),
        ("/\t/evil.example.net", "/portal"),
        ("/portal/../admin", "/portal/../admin"),  # still a same-site path under /portal
        ("/portal/tickets?x=1", "/portal/tickets?x=1"),
        ("https://evil.example.net/portal", "/portal"),
    ],
)
async def test_portal_next_must_stay_in_portal(env: Env, raw: str, expected: str) -> None:
    await seed_customer(env.session, "idp.kunde.47262@example.org")
    await _oidc_provider(env, "nx", match_by="email", attribute_map=AttributeMap(email=["email"]))
    env.oidc.claims["c"] = {
        "sub": "kunde-nx",
        "email": "idp.kunde.47262@example.org",
        "email_verified": True,
    }
    start = await env.client.get(f"/api/portal/auth/sso/{SLUG}nx/login", params={"next": raw})
    assert start.status_code == 302, start.text
    q = parse_qs(urlparse(start.headers["location"]).query)
    resp = await env.client.get(
        f"/api/portal/auth/oidc/{SLUG}nx/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == expected


# --------------------------------------------------------------- audience / session separation


async def test_provider_audience_is_enforced_per_login_surface(env: Env) -> None:
    await _oidc_provider(env, "ag", audience="agent")
    await _oidc_provider(env, "cu", audience="customer")
    for_customer = await env.client.get(f"/api/portal/auth/sso/{SLUG}ag/login")
    assert _error(for_customer) == ("/portal/login", "unknown_provider")
    for_agent = await env.client.get(f"/api/v1/auth/sso/{SLUG}cu/login")
    assert _error(for_agent) == ("/login", "unknown_provider")
    # a customer-only provider has no agent callback either
    cb = await env.client.get(
        f"/api/v1/auth/oidc/{SLUG}cu/callback", params={"code": "c", "state": "nope"}
    )
    assert _error(cb)[1] in {"state", "unknown_provider"}
    assert env.client.cookies.get(AGENT_COOKIE) is None


async def test_customer_idp_response_never_logs_in_or_creates_an_agent(env: Env) -> None:
    await env.agent(47271, "idp.agent.47271")
    await _oidc_provider(
        env,
        "cu2",
        audience="customer",
        match_by="login",
        jit_enabled=True,
        attribute_map=AttributeMap(login=["preferred_username"], email=["email"]),
        jit_defaults=JitDefaults(customer_id="idp-jit-co"),
    )
    # The IdP asserts the login of an existing AGENT; there is no such customer.
    env.oidc.claims["c"] = {
        "sub": "s-agent-lookalike",
        "preferred_username": "idp.agent.47271",
        "email": "idp.agent.47271@example.test",
        "email_verified": True,
    }
    q = await env.start_oidc(f"/api/portal/auth/sso/{SLUG}cu2/login")
    resp = await env.client.get(
        f"/api/portal/auth/oidc/{SLUG}cu2/callback", params={"code": "c", "state": q["state"][0]}
    )
    # JIT creates a *customer* named like the agent, never touches the agent account.
    assert resp.status_code == 302, resp.text
    assert _set_cookie_names(resp) == {CUSTOMER_COOKIE}
    assert env.client.cookies.get(AGENT_COOKIE) is None
    agents = (
        await env.session.execute(
            text("SELECT COUNT(*) FROM users WHERE login LIKE 'idp.agent.47271%'")
        )
    ).scalar_one()
    assert agents == 1  # the existing agent only; nothing was created
    agent_links = (
        await env.session.execute(
            text(
                "SELECT COUNT(*) FROM tiqora_identity_link WHERE user_kind = 'agent'"
                " AND provider_id IN (SELECT id FROM tiqora_identity_provider WHERE slug = :s)"
            ),
            {"s": f"{SLUG}cu2"},
        )
    ).scalar_one()
    assert agent_links == 0
    token = env.client.cookies.get(CUSTOMER_COOKIE)
    assert token and await env.agent_store.get(token) is None  # not an agent session


async def test_agent_idp_response_never_logs_in_or_creates_a_customer(env: Env) -> None:
    await seed_customer(env.session, "idp.kunde.47263@example.org")
    await _oidc_provider(
        env,
        "ag2",
        audience="agent",
        jit_enabled=True,
        attribute_map=AttributeMap(login=["preferred_username"], email=["email"]),
    )
    env.oidc.claims["c"] = {
        "sub": "s-customer-lookalike",
        "preferred_username": "idp.kunde.47263@example.org",
    }
    q = await env.start_oidc(f"/api/v1/auth/sso/{SLUG}ag2/login")
    resp = await env.client.get(
        f"/api/v1/auth/oidc/{SLUG}ag2/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?")  # no_match / jit_missing_fields: rejected
    assert not (_set_cookie_names(resp) & {AGENT_COOKIE, CUSTOMER_COOKIE})
    # and the customer portal does not accept that provider at all
    at_portal = await env.client.get(f"/api/portal/auth/sso/{SLUG}ag2/login")
    assert _error(at_portal) == ("/portal/login", "unknown_provider")


async def test_sessions_use_separate_cookies_and_stores(env: Env) -> None:
    await seed_customer(env.session, "idp.kunde.47264@example.org")
    await seed_agent(env.session, 47272, "idp.agent.47272")
    ctoken = await env.customer_store.create(
        (
            await env.session.execute(
                text("SELECT id FROM customer_user WHERE login = :l"),
                {"l": "idp.kunde.47264@example.org"},
            )
        ).scalar_one(),
        "idp.kunde.47264@example.org",
    )
    atoken = await env.agent_store.create(47272, "idp.agent.47272")
    # each token only exists in its own namespace
    assert await env.agent_store.get(ctoken) is None
    assert await env.customer_store.get(atoken) is None
    assert not any(k.startswith("tiqora:session:") and ctoken in k for k in env.redis.store)
    assert any(k.startswith("tiqora:csession:") and ctoken in k for k in env.redis.store)

    # a customer token presented as the agent cookie (and the reverse) authenticates nothing
    env.client.cookies.set(AGENT_COOKIE, ctoken)
    assert (await env.client.get("/api/v1/auth/me")).status_code == 401
    env.client.cookies.clear()
    env.client.cookies.set(CUSTOMER_COOKIE, atoken)
    assert (await env.client.get("/api/portal/auth/me")).status_code == 401
    env.client.cookies.clear()
    env.client.cookies.set(CUSTOMER_COOKIE, ctoken)
    assert (await env.client.get("/api/portal/auth/me")).status_code == 200


# -------------------------------------------------------------------------------------- JIT


async def test_customer_jit_creates_a_customer_user_row(env: Env) -> None:
    await _oidc_provider(
        env,
        "jit",
        jit_enabled=True,
        jit_defaults=JitDefaults(customer_id="idp-jit-co"),
        attribute_map=AttributeMap(
            login=["preferred_username"],
            email=["email"],
            first_name=["given_name"],
            last_name=["family_name"],
        ),
    )
    env.oidc.claims["c"] = {
        "sub": "s-jit",
        "preferred_username": "idp.kunde.47265@example.org",
        "email": "idp.kunde.47265@example.test",
        "email_verified": True,
        "given_name": "Jana",
        "family_name": "Kunde",
    }
    q = await env.start_oidc(f"/api/portal/auth/sso/{SLUG}jit/login")
    resp = await env.client.get(
        f"/api/portal/auth/oidc/{SLUG}jit/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert resp.status_code == 302 and resp.headers["location"] == "/portal", resp.text
    row = (
        await env.session.execute(
            text("SELECT email, customer_id, first_name FROM customer_user WHERE login = :l"),
            {"l": "idp.kunde.47265@example.org"},
        )
    ).first()
    assert row is not None
    assert tuple(row) == ("idp.kunde.47265@example.test", "idp-jit-co", "Jana")
    me = await env.client.get("/api/portal/auth/me")
    assert me.status_code == 200 and me.json()["customer_id"] == "idp-jit-co"
    # JIT customers are never agents and get no role/group assignments
    assert (
        await env.session.execute(
            text("SELECT COUNT(*) FROM users WHERE login = :l"),
            {"l": "idp.kunde.47265@example.org"},
        )
    ).scalar_one() == 0
    assert (
        await env.session.execute(text("SELECT COUNT(*) FROM tiqora_identity_managed_assignment"))
    ).scalar_one() == 0


# ----------------------------------------------------------------------------------- SAML


async def _saml_provider(env: Env, idp: SamlTestIdp, slug: str, audience: Any = "both") -> str:
    full = f"{SLUG}{slug}"
    await make_provider(
        env.session,
        slug=full,
        kind="saml",
        audience=audience,
        attribute_map=AttributeMap(login=["uid"]),
        config={"idp_metadata_xml": idp.metadata_xml(), "name_id_format": "persistent"},
        settings=env.settings,
    )
    await env.session.commit()
    meta = await env.client.get(f"/api/v1/auth/saml/{full}/metadata")
    assert meta.status_code == 200, meta.text
    idp.trust_sp(meta.text)
    return full


def _acs_request(
    env: Env, path: str, body: str, *, relay: str = "", req_id: str | None = None
) -> Any:
    jar = f"tiqora_saml_req={req_id}" if req_id else ""
    return env.client.post(
        path,
        content=urlencode({"SAMLResponse": body, "RelayState": relay}),
        headers={
            "Origin": "https://idp.example.org",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": jar,
        },
    )


def _saml_response(
    idp: SamlTestIdp, slug: str, req_id: str, acs_path: str, uid: str, **kw: Any
) -> str:
    return idp.login_response(
        in_response_to=req_id,
        acs_url=f"{BASE}{acs_path}",
        sp_entity_id=f"{BASE}/api/v1/auth/saml/{slug}/metadata",
        name_id=f"u-{uid}",
        attributes={"uid": [uid]},
        **kw,
    )


@pytest.mark.saml
async def test_portal_saml_login(env: Env, idp: SamlTestIdp) -> None:
    slug = await _saml_provider(env, idp, "saml")
    await seed_customer(env.session, "idp.kunde.47266@example.org")
    login = f"/api/portal/auth/sso/{slug}/login?next=/portal/tickets"
    acs_path = f"/api/portal/auth/saml/{slug}/acs"
    agent_acs_path = f"/api/v1/auth/saml/{slug}/acs"

    start = await env.client.get(login)
    assert start.status_code == 302 and start.headers["location"].startswith(IDP_SSO_URL)
    req_id, acs, relay = idp.parse_login_redirect(start.headers["location"])
    assert acs == f"{BASE}{acs_path}"  # the customer ACS, not the agent one
    env.client.cookies.clear()

    ok = await _acs_request(
        env,
        acs_path,
        _saml_response(idp, slug, req_id, acs_path, "idp.kunde.47266@example.org"),
        relay=relay,
        req_id=req_id,
    )
    assert ok.status_code == 200, ok.text
    assert "url=/portal/tickets" in ok.text
    assert _set_cookie_names(ok) >= {CUSTOMER_COOKIE}
    assert AGENT_COOKIE not in _set_cookie_names(ok)
    token = next(
        c.split(";")[0].split("=", 1)[1]
        for c in ok.headers.get_list("set-cookie")
        if c.startswith(f"{CUSTOMER_COOKIE}=")
    )
    sso = await env.customer_store.get_sso(token)
    assert sso is not None and sso.kind == "saml" and sso.session_index
    assert await env.agent_store.get_sso(token) is None

    # A response addressed to the AGENT ACS, posted to the portal ACS, is rejected.
    env.client.cookies.clear()
    start = await env.client.get(login)
    req_id, _acs, relay = idp.parse_login_redirect(start.headers["location"])
    env.client.cookies.clear()
    misrouted = _saml_response(idp, slug, req_id, agent_acs_path, "idp.kunde.47266@example.org")
    bad = await _acs_request(env, acs_path, misrouted, relay=relay, req_id=req_id)
    assert _error(bad) == ("/portal/login", "saml_invalid")
    assert not _set_cookie_names(bad) & {CUSTOMER_COOKIE, AGENT_COOKIE}
    # the genuine request survives the forged one
    good = await _acs_request(
        env,
        acs_path,
        _saml_response(idp, slug, req_id, acs_path, "idp.kunde.47266@example.org"),
        relay=relay,
        req_id=req_id,
    )
    assert good.status_code == 200, good.text


@pytest.mark.saml
async def test_portal_saml_request_cannot_finish_at_agent_acs(env: Env, idp: SamlTestIdp) -> None:
    slug = await _saml_provider(env, idp, "saml2")
    await env.agent(47273, "idp.kunde.47273")
    await seed_customer(env.session, "idp.kunde.47267@example.org")
    start = await env.client.get(f"/api/portal/auth/sso/{slug}/login")
    req_id, _acs, relay = idp.parse_login_redirect(start.headers["location"])
    env.client.cookies.clear()
    portal_acs = f"/api/portal/auth/saml/{slug}/acs"
    agent_acs = f"/api/v1/auth/saml/{slug}/acs"
    # request id minted by the portal start, response aimed at the agent ACS
    resp = await _acs_request(
        env,
        agent_acs,
        _saml_response(idp, slug, req_id, agent_acs, "idp.kunde.47273"),
        relay=relay,
        req_id=req_id,
    )
    assert resp.status_code == 302 and resp.headers["location"].startswith("/login?")
    assert not _set_cookie_names(resp) & {CUSTOMER_COOKIE, AGENT_COOKIE}
    # and the request is still usable at the portal ACS
    ok = await _acs_request(
        env,
        portal_acs,
        _saml_response(idp, slug, req_id, portal_acs, "idp.kunde.47267@example.org"),
        relay=relay,
        req_id=req_id,
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.saml
async def test_portal_sso_logout(env: Env, idp: SamlTestIdp) -> None:
    # Without an SSO session: local logout only.
    env.client.cookies.clear()
    plain = await env.client.post("/api/portal/auth/sso/logout", headers=SAME)
    assert (plain.status_code, plain.headers["location"]) == (303, "/portal/login?logged_out=1")

    slug = await _saml_provider(env, idp, "lo", audience="customer")
    await seed_customer(env.session, "idp.kunde.47268@example.org")
    acs_path = f"/api/portal/auth/saml/{slug}/acs"
    start = await env.client.get(f"/api/portal/auth/sso/{slug}/login")
    req_id, _acs, relay = idp.parse_login_redirect(start.headers["location"])
    env.client.cookies.clear()
    ok = await _acs_request(
        env,
        acs_path,
        _saml_response(idp, slug, req_id, acs_path, "idp.kunde.47268@example.org"),
        relay=relay,
        req_id=req_id,
    )
    assert ok.status_code == 200, ok.text
    token = next(
        c.split(";")[0].split("=", 1)[1]
        for c in ok.headers.get_list("set-cookie")
        if c.startswith(f"{CUSTOMER_COOKIE}=")
    )
    env.client.cookies.clear()
    env.client.cookies.set(CUSTOMER_COOKIE, token)
    me = await env.client.get("/api/portal/auth/me")
    assert me.json()["sso_logout"] is True

    resp = await env.client.post("/api/portal/auth/sso/logout", headers=SAME)
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith(IDP_SLO_URL + "?")
    assert await env.customer_store.get(token) is None  # local session gone before the round trip
    assert any(
        c.startswith(f"{CUSTOMER_COOKIE}=") and "Max-Age=0" in c
        for c in resp.headers.get_list("set-cookie")
    )
    parsed = idp.parse_logout_request(loc)
    pending = [k for k in env.redis.store if k.startswith("tiqora:saml:logout:")]
    assert pending == [f"tiqora:saml:logout:{parsed.request_id}"]

    # LogoutResponse at the shared SLS: audience comes from the stored logout request.
    back = idp.logout_response_redirect(
        sp_slo_url=f"{BASE}/api/v1/auth/saml/{slug}/sls", in_response_to=parsed.request_id
    )
    sls = await env.client.get(f"/api/v1/auth/saml/{slug}/sls?{urlparse(back).query}")
    assert sls.status_code == 303
    assert sls.headers["location"] == "/portal/login?logged_out=1"


async def test_portal_logout_is_post_only_and_csrf_checked(env: Env) -> None:
    assert (await env.client.get("/api/portal/auth/sso/logout")).status_code == 405
    env.client.cookies.set(CUSTOMER_COOKIE, "whatever")
    forged = await env.client.post(
        "/api/portal/auth/sso/logout", headers={"Origin": "https://evil.example.org"}
    )
    assert forged.status_code == 403
