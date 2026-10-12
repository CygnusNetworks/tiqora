"""Several OIDC providers, generic SSO routes and env aliases (wave 1, Task 8).

ASGI + DB. Seed band 47250-47259 (47500-47999 belongs to reporting), provider slugs use
the ``idptest-`` prefix.
A fake IdP (``httpx.MockTransport``) serves discovery, token and userinfo; the
authorization code doubles as the access token so each test picks its claims.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import (
    FakeRedis,
    OidcSigner,
    cleanup_statements,
    make_provider,
    seed_agent,
)
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.domain.auth_config import AuthConfigService
from tiqora.domain.oidc import OidcClientConfig, OIDCService
from tiqora.identity import sso_routes
from tiqora.identity.config_models import AttributeMap, MfaPolicy
from tiqora.identity.saml_runtime import saml_availability
from tiqora.identity.store import ProviderChanges, update_provider

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-oidc-flow"
BAND = (47250, 47259)
SLUG = "idptest-"
_URLS: set[str] = set()
ENV_ISSUER_HOST = "idp.example.org"
ENV_REDIRECT = "https://helpdesk.example.org/api/v1/auth/oidc/callback"
JSON = {"Accept": "application/json"}
HTML = {"Accept": "text/html"}


def _stmts() -> tuple[tuple[str, dict[str, object]], ...]:
    env_rows = (
        (
            "DELETE FROM tiqora_identity_link WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'env-%')",
            {},
        ),
        ("DELETE FROM tiqora_identity_provider WHERE slug LIKE 'env-%'", {}),
    )
    return env_rows + cleanup_statements(user_ids=BAND, slug_prefix=SLUG)


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


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "test",
        "secret_key": KEY,
        "public_base_url": "https://helpdesk.example.org",
        "oidc_enabled": True,
        "oidc_issuer": f"https://{ENV_ISSUER_HOST}/realms/x",
        "oidc_client_id": "tiqora",
        "oidc_client_secret": "s3cr3t",
        "oidc_claim": "preferred_username",
        "oidc_redirect_uri": ENV_REDIRECT,
    }
    base.update(kw)
    return Settings(**base)


class FakeIdp:
    """Discovery + token + userinfo for any host; the code is the access token."""

    def __init__(self) -> None:
        self.claims: dict[str, dict[str, Any]] = {}
        self.id_tokens: dict[str, str] = {}
        self.token_forms: list[dict[str, list[str]]] = []
        self.signer = OidcSigner()
        self.jwks_requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        path = request.url.path
        suffix = "/.well-known/openid-configuration"
        if path.endswith(suffix):
            prefix = path[: -len(suffix)]
            base = f"https://{host}{prefix}"
            return httpx.Response(
                200,
                json={
                    "issuer": base,
                    "authorization_endpoint": f"{base}/authorize",
                    "token_endpoint": f"{base}/token",
                    "userinfo_endpoint": f"{base}/userinfo",
                    "jwks_uri": f"{base}/jwks",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if path.endswith("/jwks"):
            self.jwks_requests += 1
            return httpx.Response(200, json=self.signer.jwks())
        if path.endswith("/token"):
            form = parse_qs(request.content.decode())
            self.token_forms.append(form)
            code = form["code"][0]
            body: dict[str, Any] = {"access_token": code, "token_type": "Bearer"}
            if code in self.id_tokens:
                body["id_token"] = self.id_tokens[code]
            return httpx.Response(200, json=body)
        if path.endswith("/userinfo"):
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            if bearer in self.claims:
                return httpx.Response(200, json=self.claims[bearer])
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(404)


class Env:
    def __init__(
        self,
        app: FastAPI,
        client: httpx.AsyncClient,
        session: AsyncSession,
        redis: FakeRedis,
        idp: FakeIdp,
        settings: Settings,
    ) -> None:
        self.app = app
        self.client = client
        self.session = session
        self.redis = redis
        self.idp = idp
        self.settings = settings

    async def start(self, path: str, headers: dict[str, str] | None = None) -> dict[str, list[str]]:
        resp = await self.client.get(path, headers=headers)
        assert resp.status_code == 302, resp.text
        assert "tiqora_oidc_state" in resp.headers.get("set-cookie", "")
        return parse_qs(urlparse(resp.headers["location"]).query)


def _resolver(host: str, _port: int) -> list[str]:
    return ["10.0.0.5"] if host == "private.example.org" else ["93.184.216.34"]


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    monkeypatch.setattr("tiqora.security.outbound._default_resolve", _resolver)
    idp = FakeIdp()
    transport = httpx.MockTransport(idp.handler)

    def make_service(config: OidcClientConfig) -> OIDCService:
        return OIDCService(config, transport=transport)

    monkeypatch.setattr(sso_routes, "new_oidc_service", make_service)

    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    settings = _settings()
    app = create_app(settings)
    redis = FakeRedis()
    app.state.redis = redis

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    async with (
        maker() as session,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://helpdesk.example.org"
        ) as client,
    ):
        yield Env(app, client, session, redis, idp, settings)
    await engine.dispose()
    _clean(url)


async def _agent(env: Env, user_id: int, login: str, *, sso: bool = True) -> None:
    await seed_agent(env.session, user_id, login)
    if sso:
        await AuthConfigService(env.session).set(user_id, sso_eligible=True)
        await env.session.commit()


async def _kc(env: Env, slug: str = "kc", *, host: str = "kc.example.org", **kw: Any) -> Any:
    rec = await make_provider(
        env.session,
        slug=f"{SLUG}{slug}",
        attribute_map=AttributeMap(login=["preferred_username"]),
        config={
            "issuer": f"https://{host}/realms/y",
            "client_id": "tiqora",
            "client_secret": "kc-secret",
        },
        settings=env.settings,
        **kw,
    )
    await env.session.commit()
    return rec


async def test_env_oidc_unchanged_login_via_alias(env: Env) -> None:
    await _agent(env, 47250, "idp.env.47250")
    env.idp.claims["c"] = {"sub": "sub-47250", "preferred_username": "idp.env.47250"}
    q = await env.start("/api/v1/auth/oidc/login")
    assert q["redirect_uri"] == [ENV_REDIRECT]
    assert q["code_challenge_method"] == ["S256"]
    state = q["state"][0]

    ok = await env.client.get(
        "/api/v1/auth/oidc/callback", params={"code": "c", "state": state}, headers=JSON
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["login"] == "idp.env.47250"
    assert env.client.cookies.get("tiqora_session")
    # PKCE verifier from the state reached the token endpoint
    assert env.idp.token_forms[-1]["code_verifier"][0]

    q2 = await env.start("/api/v1/auth/oidc/login")
    env.client.cookies.delete("tiqora_session")
    html = await env.client.get(
        "/api/v1/auth/oidc/callback", params={"code": "c", "state": q2["state"][0]}, headers=HTML
    )
    assert html.status_code == 302
    assert html.headers["location"] == "/"
    assert env.client.cookies.get("tiqora_session")


async def test_env_oidc_unchanged_rejections(env: Env) -> None:
    await _agent(env, 47251, "idp.env.47251", sso=False)
    env.idp.claims["nomatch"] = {"sub": "x1", "preferred_username": "nobody.oidcflow"}
    env.idp.claims["noopt"] = {"sub": "x2", "preferred_username": "idp.env.47251"}

    async def callback(code: str, headers: dict[str, str]) -> httpx.Response:
        q = await env.start("/api/v1/auth/oidc/login")
        return await env.client.get(
            "/api/v1/auth/oidc/callback",
            params={"code": code, "state": q["state"][0]},
            headers=headers,
        )

    r = await callback("nomatch", JSON)
    assert r.status_code == 403
    assert "No local user matches SSO identity" in r.json()["detail"]
    r = await callback("noopt", JSON)
    assert (r.status_code, r.json()["detail"]) == (403, "SSO not enabled for this account")

    r = await callback("nomatch", HTML)
    assert r.status_code == 302
    assert parse_qs(urlparse(r.headers["location"]).query)["sso_error"] == ["no_match"]
    assert r.headers["location"].startswith("/login?")
    r = await callback("noopt", HTML)
    assert parse_qs(urlparse(r.headers["location"]).query)["sso_error"] == ["sso_not_enabled"]
    assert env.client.cookies.get("tiqora_session") is None


@pytest.mark.parametrize("stored", ["1", "legacy-verifier-value"])
async def test_callback_accepts_legacy_state_value(env: Env, stored: str) -> None:
    await _agent(env, 47252, "idp.env.47252")
    env.idp.claims["c"] = {"sub": "s47903", "preferred_username": "idp.env.47252"}
    # env-oidc row is created by the first request to any SSO route
    await env.client.get("/api/v1/auth/methods")
    await env.redis.set(f"tiqora:oidc:state:legacy{stored[:1]}", stored, ex=300)
    env.client.cookies.set("tiqora_oidc_state", f"legacy{stored[:1]}")
    r = await env.client.get(
        "/api/v1/auth/oidc/callback",
        params={"code": "c", "state": f"legacy{stored[:1]}"},
        headers=JSON,
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["login"] == "idp.env.47252"
    form = env.idp.token_forms[-1]
    if stored == "1":
        assert "code_verifier" not in form
    else:
        assert form["code_verifier"] == [stored]


async def test_db_oidc_provider_login_redirects_to_next(env: Env) -> None:
    await _agent(env, 47253, "idp.kc.47253")
    await _kc(env)
    env.idp.claims["c"] = {"sub": "kc-sub-1", "preferred_username": "idp.kc.47253"}
    q = await env.start("/api/v1/auth/sso/idptest-kc/login?next=/tickets/5")
    assert q["redirect_uri"] == [
        "https://helpdesk.example.org/api/v1/auth/oidc/idptest-kc/callback"
    ]
    resp = await env.client.get(
        "/api/v1/auth/oidc/idptest-kc/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/tickets/5"
    token = env.client.cookies.get("tiqora_session")
    assert token
    info = await SessionStore(env.redis, env.settings).get_sso(token)  # type: ignore[arg-type]
    assert info is not None
    assert (info.slug, info.kind) == ("idptest-kc", "oidc")


async def test_state_bound_to_provider(env: Env) -> None:
    await _agent(env, 47254, "idp.kc.47254")
    await _kc(env, "a")
    await _kc(env, "b")
    env.idp.claims["c"] = {"sub": "s", "preferred_username": "idp.kc.47254"}
    q = await env.start("/api/v1/auth/sso/idptest-a/login")
    resp = await env.client.get(
        "/api/v1/auth/oidc/idptest-b/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert resp.status_code == 302
    assert parse_qs(urlparse(resp.headers["location"]).query)["sso_error"] == ["state"]
    assert resp.headers["location"].startswith("/login?")
    assert env.client.cookies.get("tiqora_session") is None


async def test_next_is_sanitised(env: Env) -> None:
    await _agent(env, 47255, "idp.kc.47255")
    await _kc(env)
    env.idp.claims["c"] = {"sub": "s", "preferred_username": "idp.kc.47255"}
    q = await env.start("/api/v1/auth/sso/idptest-kc/login?next=//evil.example.com")
    resp = await env.client.get(
        "/api/v1/auth/oidc/idptest-kc/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


async def test_disabled_provider_404_without_test(env: Env) -> None:
    await _kc(env, enabled=False)
    resp = await env.client.get("/api/v1/auth/sso/idptest-kc/login")
    assert resp.status_code == 302
    assert parse_qs(urlparse(resp.headers["location"]).query)["sso_error"] == ["provider_disabled"]
    assert "tiqora_oidc_state" not in resp.headers.get("set-cookie", "")


async def test_unknown_and_customer_only_provider_for_agent(env: Env) -> None:
    await make_provider(env.session, slug=f"{SLUG}cust", audience="customer", settings=env.settings)
    await env.session.commit()
    for slug in ("idptest-nope", "idptest-cust"):
        resp = await env.client.get(f"/api/v1/auth/sso/{slug}/login")
        assert resp.status_code == 302
        assert parse_qs(urlparse(resp.headers["location"]).query)["sso_error"] == [
            "unknown_provider"
        ]


async def test_methods_lists_enabled_agent_sso_providers(env: Env) -> None:
    await _kc(env)
    await make_provider(env.session, slug=f"{SLUG}cust", audience="customer", settings=env.settings)
    await env.session.commit()
    await _kc(env, "off", enabled=False)
    resp = await env.client.get("/api/v1/auth/methods")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["slug"] for p in body["providers"]] == ["env-oidc", "idptest-kc"]
    env_row = body["providers"][0]
    assert (env_row["label"], env_row["kind"]) == ("", "oidc")
    assert body["oidc"] is True
    assert body["saml"] is saml_availability().available


async def test_methods_synthesises_env_oidc_without_db() -> None:
    from tiqora.api.v1.auth import auth_methods

    out = await auth_methods(_settings(portal_enabled=False), None)  # type: ignore[arg-type]
    assert [(p.slug, p.kind, p.label) for p in out.providers] == [("env-oidc", "oidc", "")]
    off = await auth_methods(_settings(portal_enabled=False, oidc_enabled=False), None)  # type: ignore[arg-type]
    assert off.providers == []


async def test_private_network_issuer_blocked_unless_allowed(env: Env) -> None:
    rec = await _kc(env, host="private.example.org")
    resp = await env.client.get("/api/v1/auth/sso/idptest-kc/login")
    assert resp.status_code == 302
    assert parse_qs(urlparse(resp.headers["location"]).query)["sso_error"] == ["idp_error"]
    await update_provider(
        env.session,
        rec.id,
        ProviderChanges(config={"allow_private_network": True}),
        secret_key=KEY,
        actor_id=1,
    )
    await env.session.commit()
    resp = await env.client.get("/api/v1/auth/sso/idptest-kc/login")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://private.example.org/realms/y/authorize")


async def test_avatar_picture_kept(env: Env) -> None:
    await _agent(env, 47256, "idp.kc.47256")
    await _kc(env)
    env.idp.claims["c"] = {
        "sub": "s",
        "preferred_username": "idp.kc.47256",
        "picture": "https://cdn.example.org/a.png",
    }
    q = await env.start("/api/v1/auth/sso/idptest-kc/login")
    cb = await env.client.get(
        "/api/v1/auth/oidc/idptest-kc/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert cb.status_code == 302 and cb.headers["location"] == "/"
    token = env.client.cookies.get("tiqora_session")
    assert token
    avatar = await SessionStore(env.redis, env.settings).get_avatar_url(token)  # type: ignore[arg-type]
    assert avatar == "https://cdn.example.org/a.png"


def _env_id_token(env: Env, q: dict[str, list[str]], sub: str) -> str:
    return env.idp.signer.id_token(
        iss=f"https://{ENV_ISSUER_HOST}/realms/x", aud="tiqora", sub=sub, nonce=q["nonce"][0]
    )


async def test_userinfo_sub_must_match_id_token_sub(env: Env) -> None:
    await _agent(env, 47257, "idp.env.47257")
    env.idp.claims["c"] = {"sub": "real-sub", "preferred_username": "idp.env.47257"}
    q = await env.start("/api/v1/auth/oidc/login")
    env.idp.id_tokens["c"] = _env_id_token(env, q, "other-sub")
    resp = await env.client.get(
        "/api/v1/auth/oidc/callback",
        params={"code": "c", "state": q["state"][0]},
        headers=HTML,
    )
    assert resp.status_code == 302
    assert parse_qs(urlparse(resp.headers["location"]).query)["sso_error"] == ["oidc_failed"]
    assert env.client.cookies.get("tiqora_session") is None

    # a matching sub (verified id_token) still logs in
    q = await env.start("/api/v1/auth/oidc/login")
    env.idp.id_tokens["c"] = _env_id_token(env, q, "real-sub")
    ok = await env.client.get(
        "/api/v1/auth/oidc/callback", params={"code": "c", "state": q["state"][0]}, headers=JSON
    )
    assert ok.status_code == 200, ok.text


async def _env_login(env: Env, headers: dict[str, str] = JSON) -> httpx.Response:
    q = await env.start("/api/v1/auth/oidc/login")
    return await env.client.get(
        "/api/v1/auth/oidc/callback", params={"code": "c", "state": q["state"][0]}, headers=headers
    )


async def test_env_oidc_survives_missing_row_without_methods(env: Env) -> None:
    await _agent(env, 47258, "idp.env.47258")
    env.idp.claims["c"] = {"sub": "s47909", "preferred_username": "idp.env.47258"}
    # warm the once-per-process sync flag, then lose the row behind its back
    await env.client.get("/api/v1/auth/methods")
    for stmt, params in _stmts()[:2]:
        await env.session.execute(text(stmt), params)
    await env.session.commit()
    resp = await _env_login(env)
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["login"] == "idp.env.47258"


async def test_env_oidc_survives_stale_disabled_row(env: Env) -> None:
    await _agent(env, 47259, "idp.env.47259")
    env.idp.claims["c"] = {"sub": "s47910", "preferred_username": "idp.env.47259"}
    await env.client.get("/api/v1/auth/methods")
    await env.session.execute(
        text("UPDATE tiqora_identity_provider SET enabled = :f WHERE slug = 'env-oidc'"),
        {"f": False},
    )
    await env.session.commit()
    resp = await _env_login(env)
    assert resp.status_code == 200, resp.text


async def test_methods_hides_only_the_unreadable_provider(env: Env) -> None:
    await _kc(env, "good")
    bad = await _kc(env, "bad")
    await env.session.execute(
        text("UPDATE tiqora_identity_provider SET config = 'not json' WHERE id = :i"),
        {"i": bad.id},
    )
    await env.session.commit()
    body = (await env.client.get("/api/v1/auth/methods")).json()
    assert [p["slug"] for p in body["providers"]] == ["env-oidc", "idptest-good"]


def test_sso_error_codes_cover_reject_codes() -> None:
    from typing import get_args

    from tiqora.identity.http import SSO_ERROR_CODES
    from tiqora.identity.login import RejectCode

    assert set(get_args(RejectCode)) <= set(SSO_ERROR_CODES)
    assert {"state", "saml_invalid", "oidc_failed", "idp_error", "config"} <= set(SSO_ERROR_CODES)


# --------------------------------------------------------------------------- MFA evidence


def _kc_id_token(env: Env, q: dict[str, list[str]], **extra: Any) -> str:
    return env.idp.signer.id_token(
        iss="https://kc.example.org/realms/y",
        aud="tiqora",
        sub="kc-sub",
        nonce=q["nonce"][0],
        **extra,
    )


async def _capture_identity(
    env: Env, monkeypatch: pytest.MonkeyPatch, *, id_token_extra: dict[str, Any] | None
) -> Any:
    """Run an OIDC callback; return the ExternalIdentity handed to complete_login."""
    from fastapi.responses import PlainTextResponse  # noqa: PLC0415

    seen: list[Any] = []

    async def fake_complete_login(*_a: Any, **kw: Any) -> PlainTextResponse:
        seen.append(kw["identity"])
        return PlainTextResponse("captured")

    monkeypatch.setattr(sso_routes, "complete_login", fake_complete_login)
    q = await env.start("/api/v1/auth/sso/idptest-kc/login")
    if id_token_extra is not None:
        env.idp.id_tokens["c"] = _kc_id_token(env, q, **id_token_extra)
    resp = await env.client.get(
        "/api/v1/auth/oidc/idptest-kc/callback", params={"code": "c", "state": q["state"][0]}
    )
    assert resp.text == "captured"
    assert len(seen) == 1
    return seen[0]


async def test_amr_from_userinfo_is_ignored(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    await _kc(env)
    env.idp.claims["c"] = {
        "sub": "kc-sub",
        "preferred_username": "x",
        "amr": ["mfa"],
        "acr": "gold",
        "oidc:amr": ["mfa"],
        "saml:authn_context": ["x"],
    }
    identity = await _capture_identity(env, monkeypatch, id_token_extra={})
    assert "amr" not in identity.attributes and "acr" not in identity.attributes
    assert identity.attributes.get("oidc:amr", ()) == ()
    assert identity.attributes.get("oidc:acr", ()) == ()
    assert "saml:authn_context" not in identity.attributes
    assert identity.attributes["preferred_username"] == ("x",)


async def test_no_id_token_means_no_mfa_proof(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    await _kc(env)
    env.idp.claims["c"] = {"sub": "kc-sub", "preferred_username": "x", "amr": ["mfa"]}
    identity = await _capture_identity(env, monkeypatch, id_token_extra=None)
    assert identity.attributes.get("oidc:amr", ()) == ()
    assert "amr" not in identity.attributes


async def test_amr_from_verified_id_token(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    await _kc(env)
    env.idp.claims["c"] = {"sub": "kc-sub", "preferred_username": "x", "amr": ["spoof"]}
    identity = await _capture_identity(
        env, monkeypatch, id_token_extra={"amr": ["pwd", "mfa"], "acr": "gold"}
    )
    assert identity.attributes["oidc:amr"] == ("pwd", "mfa")
    assert identity.attributes["oidc:acr"] == ("gold",)
    assert "amr" not in identity.attributes


async def test_authorize_url_sends_acr_values(env: Env) -> None:
    rec = await _kc(env)
    plain = await env.start("/api/v1/auth/sso/idptest-kc/login")
    assert "acr_values" not in plain
    await update_provider(
        env.session,
        rec.id,
        ProviderChanges(
            require_mfa_claim=True, mfa_policy=MfaPolicy(oidc_request_acr_values="gold silver")
        ),
        secret_key=env.settings.secret_key,
        actor_id=1,
        settings=env.settings,
    )
    await env.session.commit()
    q = await env.start("/api/v1/auth/sso/idptest-kc/login")
    assert q["acr_values"] == ["gold silver"]
