"""Admin API for identity providers incl. test login and preview (wave 1, Task 13).

ASGI + DB. Seed band 47200-47209, provider slugs use the ``idpadm-`` prefix. Agents log in
with real sessions (FakeRedis) so the test-login path sees the admin's agent cookie.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import (
    NOW,
    FakeRedis,
    cleanup_statements,
    make_provider,
    seed_agent,
)
from tests._saml_idp import IDP_ENTITY_ID, IDP_SLO_URL, IDP_SSO_URL, make_test_cert
from tests.test_identity_oidc_flow import FakeIdp
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin import identity_providers as admin_api
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.domain.oidc import OIDCService
from tiqora.identity import saml_runtime, sso_routes
from tiqora.identity.links import upsert_link
from tiqora.identity.saml_runtime import SamlAvailability
from tiqora.identity.store import get_provider_by_slug

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-admin-api"
BAND = (47200, 47209)
SLUG = "idpadm-"
BASE = "/api/v1/admin/identity-providers"
ADMIN1, ADMIN2, PLAIN, LINKED = 47201, 47202, 47203, 47204
ROLE_ID = 47200
PUBLIC = "93.184.216.34"
_URLS: set[str] = set()


def _stmts() -> tuple[tuple[str, dict[str, object]], ...]:
    extra = (
        (
            "DELETE FROM tiqora_identity_link WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'env-%')",
            {},
        ),
        ("DELETE FROM tiqora_identity_provider WHERE slug LIKE 'env-%'", {}),
        (
            "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
            {"lo": BAND[0], "hi": BAND[1]},
        ),
        ("DELETE FROM roles WHERE id = :r", {"r": ROLE_ID}),
    )
    return extra + cleanup_statements(user_ids=BAND, slug_prefix=SLUG)


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
        public_base_url="https://helpdesk.example.org",
        oidc_enabled=True,
        oidc_issuer="https://idp.example.org/realms/x",
        oidc_client_id="tiqora",
        oidc_client_secret="env-s3cr3t",
        oidc_claim="preferred_username",
        oidc_redirect_uri="https://helpdesk.example.org/api/v1/auth/oidc/callback",
    )


class Env:
    def __init__(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        redis: FakeRedis,
        idp: FakeIdp,
        settings: Settings,
        tokens: dict[int, str],
    ) -> None:
        self.client = client
        self.session = session
        self.redis = redis
        self.idp = idp
        self.settings = settings
        self.tokens = tokens

    def login_as(self, user_id: int | None) -> None:
        self.client.cookies.delete("tiqora_session")
        if user_id is not None:
            self.client.cookies.set("tiqora_session", self.tokens[user_id])

    async def create(self, slug: str, preset: str = "generic_oidc", **kw: Any) -> dict[str, Any]:
        resp = await self.client.post(
            BASE, json={"preset": preset, "name": f"Test {slug}", "slug": f"{SLUG}{slug}", **kw}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def patch(self, provider_id: int, body: dict[str, Any]) -> httpx.Response:
        return await self.client.patch(f"{BASE}/{provider_id}", json=body)

    async def oidc_provider(self, slug: str, **changes: Any) -> dict[str, Any]:
        created = await self.create(slug)
        resp = await self.patch(
            created["id"],
            {
                "config": {
                    "issuer": "https://kc.example.org/realms/y",
                    "client_id": "tiqora",
                    "client_secret": "kc-secret",
                },
                **changes,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    monkeypatch.setattr("tiqora.security.outbound._default_resolve", lambda _h, _p: [PUBLIC])
    idp = FakeIdp()
    transport = httpx.MockTransport(idp.handler)
    monkeypatch.setattr(
        sso_routes,
        "new_oidc_service",
        lambda config: OIDCService(config, transport=transport),
    )

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
            transport=httpx.ASGITransport(app=app),
            base_url="https://helpdesk.example.org",
            headers={"Origin": "https://helpdesk.example.org"},
        ) as client,
    ):
        for uid, login in (
            (ADMIN1, f"idp.adm.{ADMIN1}"),
            (ADMIN2, f"idp.adm.{ADMIN2}"),
            (PLAIN, f"idp.plain.{PLAIN}"),
            (LINKED, f"idp.link.{LINKED}"),
        ):
            await seed_agent(session, uid, login)
        for uid in (ADMIN1, ADMIN2):
            await session.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) SELECT :u, id, 'rw', :t, 1, :t, 1"
                    " FROM permission_groups WHERE name = 'admin'"
                ),
                {"u": uid, "t": NOW},
            )
        await session.execute(
            text(
                "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:r, 'idp-role-47200', 1, :t, 1, :t, 1)"
            ),
            {"r": ROLE_ID, "t": NOW},
        )
        await session.commit()
        store = SessionStore(redis, settings)  # type: ignore[arg-type]
        tokens = {
            uid: await store.create(uid, login)
            for uid, login in (
                (ADMIN1, f"idp.adm.{ADMIN1}"),
                (ADMIN2, f"idp.adm.{ADMIN2}"),
                (PLAIN, f"idp.plain.{PLAIN}"),
                (LINKED, f"idp.link.{LINKED}"),
            )
        }
        e = Env(client, session, redis, idp, settings, tokens)
        e.login_as(ADMIN1)
        yield e
    await engine.dispose()
    _clean(url)


# --------------------------------------------------------------------------- access


async def test_non_admin_gets_403_on_every_route_and_anonymous_401(env: Env) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", BASE, None),
        ("GET", f"{BASE}/kinds", None),
        ("GET", f"{BASE}/presets", None),
        ("GET", f"{BASE}/capabilities", None),
        ("POST", BASE, {"preset": "generic_oidc", "name": "x", "slug": "idpadm-denied"}),
        ("GET", f"{BASE}/1", None),
        ("PATCH", f"{BASE}/1", {"name": "x"}),
        ("DELETE", f"{BASE}/1", None),
        ("POST", f"{BASE}/1/refresh-metadata", None),
        ("POST", f"{BASE}/1/tests", {"audience": "agent"}),
        ("GET", f"{BASE}/1/tests/abc", None),
        ("POST", f"{BASE}/1/preview", {"attributes": {}}),
        ("GET", f"{BASE}/1/links", None),
        ("DELETE", f"{BASE}/1/links/1", None),
        ("GET", f"{BASE}/1/managed-assignments", None),
    ]
    for user, expected in ((PLAIN, 403), (None, 401)):
        env.login_as(user)
        for method, path, body in calls:
            resp = await env.client.request(method, path, json=body)
            assert resp.status_code == expected, (method, path, resp.status_code, resp.text)
    rows = await get_provider_by_slug(env.session, "idpadm-denied", secret_key=KEY)
    assert rows is None


# --------------------------------------------------------------------------- create / read


async def test_create_oidc_from_preset(env: Env) -> None:
    out = await env.create("oidc1")
    assert out["enabled"] is False
    assert out["kind"] == "oidc"
    assert out["source"] == "db"
    assert out["read_only"] is False
    assert out["link_count"] == 0
    assert out["secrets_set"] == []
    assert out["config"]["client_secret"] is None
    assert out["endpoints"]["agent_redirect_uri"] == (
        f"https://helpdesk.example.org/api/v1/auth/oidc/{SLUG}oidc1/callback"
    )
    assert out["endpoints"]["base_url_missing"] is False
    assert out["attribute_map"]["login"] == ["preferred_username"]


async def test_create_saml_masks_private_key(env: Env) -> None:
    out = await env.create("saml1", preset="generic_saml")
    cfg = out["config"]
    assert cfg["sp_certificate"].startswith("-----BEGIN CERTIFICATE-----")
    assert cfg["sp_private_key"] == "********"
    assert out["secrets_set"] == ["sp_private_key"]
    again = (await env.client.get(f"{BASE}/{out['id']}")).json()
    assert again["config"]["sp_private_key"] == "********"
    assert "PRIVATE KEY" not in str(again)
    assert again["endpoints"]["agent_acs_url"] == (
        f"https://helpdesk.example.org/api/v1/auth/saml/{SLUG}saml1/acs"
    )
    assert again["endpoints"]["sp_metadata_url"].endswith(f"/saml/{SLUG}saml1/metadata")


async def test_create_validation_errors(env: Env) -> None:
    resp = await env.client.post(BASE, json={"preset": "nope", "name": "x", "slug": f"{SLUG}p"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"] == {"preset": "Unbekannte Vorlage."}
    await env.create("dup")
    resp = await env.client.post(
        BASE, json={"preset": "generic_oidc", "name": "x", "slug": f"{SLUG}dup"}
    )
    assert resp.status_code == 422
    assert "slug" in resp.json()["detail"]["errors"]
    resp = await env.client.post(
        BASE, json={"preset": "generic_oidc", "name": "x", "slug": "env-oidc"}
    )
    assert resp.status_code == 422


async def test_kinds_presets_capabilities(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    kinds = (await env.client.get(f"{BASE}/kinds")).json()
    assert [k["kind"] for k in kinds] == ["oidc", "saml", "ldap"]
    assert all("properties" in k["json_schema"] for k in kinds)
    saml = next(k for k in kinds if k["kind"] == "saml")
    assert saml["read_only_fields"] == ["sp_certificate", "sp_private_key"]
    assert saml["hidden_fields"] == ["sp_private_key"]
    assert saml["i18n_prefix"] == "admin.identity.config.saml"
    assert next(k for k in kinds if k["kind"] == "oidc")["hidden_fields"] == []

    presets = (await env.client.get(f"{BASE}/presets")).json()
    assert {p["key"] for p in presets} >= {"entra_id", "generic_saml", "active_directory"}

    monkeypatch.setattr(
        saml_runtime, "saml_availability", lambda: SamlAvailability(False, "xmlsec1", None)
    )
    caps = (await env.client.get(f"{BASE}/capabilities")).json()
    assert caps["saml_available"] is False
    assert caps["saml_missing"] == "xmlsec1"
    assert caps["public_base_url_set"] is True
    assert caps["webauthn"] is False
    monkeypatch.setattr(
        saml_runtime, "saml_availability", lambda: SamlAvailability(True, None, "/usr/bin/xmlsec1")
    )
    caps = (await env.client.get(f"{BASE}/capabilities")).json()
    assert (caps["saml_available"], caps["saml_missing"]) == (True, None)


# --------------------------------------------------------------------------- update / delete


async def test_patch_keeps_masked_secret_and_never_returns_it(env: Env) -> None:
    created = await env.create("sec")
    resp = await env.patch(created["id"], {"config": {"client_id": "c1", "client_secret": "s3"}})
    assert resp.status_code == 200
    assert resp.json()["config"]["client_secret"] == "********"
    assert resp.json()["secrets_set"] == ["client_secret"]
    assert "s3" not in resp.text.replace("s3cr3t", "")

    resp = await env.patch(
        created["id"], {"config": {"client_secret": "********", "scopes": "openid"}}
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["scopes"] == "openid"
    stored = await get_provider_by_slug(env.session, f"{SLUG}sec", secret_key=KEY)
    assert stored is not None
    assert stored.config.client_secret.get_secret_value() == "s3"  # type: ignore[attr-defined]

    resp = await env.patch(created["id"], {"config": {"client_secret": ""}})
    assert resp.json()["secrets_set"] == []
    assert resp.json()["config"]["client_secret"] is None


async def test_patch_unknown_role_is_422_with_field_error(env: Env) -> None:
    created = await env.create("rules")
    resp = await env.patch(
        created["id"], {"role_rules": [{"attribute": "groups", "value": "x", "role_ids": [999999]}]}
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "message": "Bitte die markierten Felder prüfen.",
        "errors": {"role_rules": "Unbekannte Rolle: 999999"},
    }
    assert (await env.client.get(f"{BASE}/{created['id']}")).json()["role_rules"] == []
    missing = await env.patch(created["id"] + 100_000, {"name": "x"})
    assert missing.status_code == 404


async def test_env_row_listed_read_only_and_immutable(env: Env) -> None:
    rows = (await env.client.get(BASE)).json()
    env_row = next(r for r in rows if r["slug"] == "env-oidc")
    assert env_row["read_only"] is True
    assert env_row["source"] == "env"
    assert env_row["link_count"] == 0
    await upsert_link(
        env.session,
        provider_id=env_row["id"],
        user_kind="agent",
        subject="env-sub",
        user_id=ADMIN1,
        user_login="idp.adm.47201",
    )
    await env.session.commit()
    rows = (await env.client.get(BASE)).json()
    assert next(r for r in rows if r["slug"] == "env-oidc")["link_count"] == 1

    detail = (await env.client.get(f"{BASE}/{env_row['id']}")).json()
    assert detail["config"]["client_id"] == "tiqora"
    assert detail["config"]["client_secret"] == "********"
    assert "env-s3cr3t" not in str(detail)
    assert detail["endpoints"]["agent_redirect_uri"] == (
        "https://helpdesk.example.org/api/v1/auth/oidc/callback"
    )

    msg = (
        "Dieser Anmeldedienst kommt aus den Umgebungsvariablen und kann hier nicht geändert werden."
    )
    patched = await env.patch(env_row["id"], {"name": "x"})
    assert patched.status_code == 409
    assert patched.json()["detail"] == {"message": msg}
    deleted = await env.client.delete(f"{BASE}/{env_row['id']}")
    assert deleted.status_code == 409
    assert deleted.json()["detail"] == {"message": msg}
    bad_test = await env.client.post(f"{BASE}/{env_row['id']}/tests", json={"draft": {"name": "x"}})
    assert bad_test.status_code == 409


async def test_delete_provider_removes_links(env: Env) -> None:
    created = await env.create("del")
    await upsert_link(
        env.session,
        provider_id=created["id"],
        user_kind="agent",
        subject="s",
        user_id=ADMIN1,
        user_login="idp.adm.47201",
    )
    await env.session.commit()
    assert (await env.client.delete(f"{BASE}/{created['id']}")).status_code == 204
    assert (await env.client.get(f"{BASE}/{created['id']}")).status_code == 404
    left = (
        await env.session.execute(
            text("SELECT COUNT(*) FROM tiqora_identity_link WHERE provider_id = :p"),
            {"p": created["id"]},
        )
    ).scalar_one()
    assert left == 0


# --------------------------------------------------------------------------- SAML metadata


def _metadata_xml(tmp_path_factory: pytest.TempPathFactory) -> str:
    tmp = tmp_path_factory.mktemp("admmd")
    pem = make_test_cert("idp.example.org", tmp / "k.pem", tmp / "c.pem")
    cert = "".join(line for line in pem.splitlines() if "CERTIFICATE" not in line)
    return f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{IDP_ENTITY_ID}">
 <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
  <md:KeyDescriptor use="signing"><ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
   <ds:X509Data><ds:X509Certificate>{cert}</ds:X509Certificate></ds:X509Data></ds:KeyInfo>
  </md:KeyDescriptor>
  <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
    Location="{IDP_SLO_URL}"/>
  <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
    Location="{IDP_SSO_URL}"/>
 </md:IDPSSODescriptor>
</md:EntityDescriptor>"""


async def test_refresh_metadata_failure_is_422_with_german_message(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await env.create("md-fail", preset="generic_saml")
    # Unreachable at save time: the save is refused and nothing is stored.
    monkeypatch.setattr(
        admin_api, "_metadata_transport", httpx.MockTransport(lambda _r: httpx.Response(404))
    )
    saved = await env.patch(
        created["id"], {"config": {"idp_metadata_url": "https://idp.example.org/md"}}
    )
    assert saved.status_code == 422
    errors = saved.json()["detail"]["errors"]
    assert "config.idp_metadata_url" in errors
    reloaded = (await env.client.get(f"{BASE}/{created['id']}")).json()
    assert reloaded["config"]["idp_metadata_url"] == ""

    # A stored URL whose refresh fails: 422 with the message, error kept for the UI.
    stored = await make_provider(
        env.session,
        slug=f"{SLUG}md-stored",
        kind="saml",
        enabled=False,
        config={"idp_metadata_url": "https://idp.example.org/md"},
        settings=env.settings,
    )
    await env.session.commit()
    created = {"id": stored.id}
    resp = await env.client.post(f"{BASE}/{created['id']}/refresh-metadata")
    assert resp.status_code == 422
    message = resp.json()["detail"]["message"]
    assert message and "Metadaten" in message
    summary = next(r for r in (await env.client.get(BASE)).json() if r["id"] == created["id"])
    assert summary["metadata_error"] == message


async def test_save_fetches_metadata_and_refresh_endpoint_succeeds(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    xml = _metadata_xml(tmp_path_factory)
    monkeypatch.setattr(
        admin_api,
        "_metadata_transport",
        httpx.MockTransport(lambda _r: httpx.Response(200, text=xml)),
    )
    created = await env.create("md-ok", preset="generic_saml")
    saved = await env.patch(
        created["id"], {"config": {"idp_metadata_url": "https://idp.example.org/md"}}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["metadata_fetched_at"] is not None
    assert saved.json()["metadata_error"] is None
    stored = await get_provider_by_slug(env.session, f"{SLUG}md-ok", secret_key=KEY)
    assert stored is not None and stored.metadata_xml == xml

    again = await env.client.post(f"{BASE}/{created['id']}/refresh-metadata")
    assert again.status_code == 200
    assert again.json()["metadata_error"] is None

    pasted = await env.patch(created["id"], {"config": {"idp_metadata_xml": "<not-metadata/>"}})
    assert pasted.status_code == 422
    assert "config.idp_metadata_xml" in pasted.json()["detail"]["errors"]

    oidc = await env.create("md-oidc")
    wrong = await env.client.post(f"{BASE}/{oidc['id']}/refresh-metadata")
    assert wrong.status_code == 422


# --------------------------------------------------------------------------- test login


async def test_test_login_on_disabled_provider(env: Env) -> None:
    provider = await env.oidc_provider("tl")
    assert provider["enabled"] is False
    secret_draft = "draft-only-secret-value"
    started = await env.client.post(
        f"{BASE}/{provider['id']}/tests",
        json={
            "audience": "agent",
            "draft": {"config": {"scopes": "openid email", "client_secret": secret_draft}},
        },
    )
    assert started.status_code == 200, started.text
    test_id = started.json()["test_id"]
    login_url = started.json()["login_url"]
    assert login_url == f"/api/v1/auth/sso/{SLUG}tl/login?test={test_id}"
    # The draft (with its secret) is encrypted in Redis.
    raw = env.redis.store[f"tiqora:idp:test:{test_id}"]
    assert secret_draft not in raw
    assert "scopes" not in raw

    # Another admin cannot start the flow, nor read the run, nor learn that it exists.
    env.login_as(ADMIN2)
    foreign = await env.client.get(login_url)
    assert foreign.status_code == 302
    assert parse_qs(urlparse(foreign.headers["location"]).query)["sso_error"] == [
        "provider_disabled"
    ]
    assert (await env.client.get(f"{BASE}/{provider['id']}/tests/{test_id}")).status_code == 404

    env.login_as(None)
    anon = await env.client.get(login_url)
    assert anon.status_code == 302
    assert anon.headers["location"].startswith("/login?")
    assert parse_qs(urlparse(anon.headers["location"]).query)["sso_error"] == ["provider_disabled"]

    # The starting admin reaches the IdP, with the draft's scopes.
    env.login_as(ADMIN1)
    resp = await env.client.get(login_url)
    assert resp.status_code == 302, resp.text
    assert resp.headers["location"].startswith("https://kc.example.org/realms/y/authorize")
    assert "scope=openid+email" in resp.headers["location"]
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["scope"] == ["openid email"]
    pending = (await env.client.get(f"{BASE}/{provider['id']}/tests/{test_id}")).json()
    assert pending["status"] == "started"
    assert pending["plan"] is None
    assert pending["attributes"] == {}

    env.idp.claims["c"] = {"sub": "sub-47201", "preferred_username": f"idp.adm.{ADMIN1}"}
    cb = await env.client.get(
        f"/api/v1/auth/oidc/{SLUG}tl/callback", params={"code": "c", "state": query["state"][0]}
    )
    assert cb.status_code == 302, cb.text
    assert cb.headers["location"] == f"/admin/identity-providers/{provider['id']}?test={test_id}"
    assert "tiqora_session=" not in cb.headers.get("set-cookie", "")
    assert env.client.cookies.get("tiqora_session") == env.tokens[ADMIN1]

    result = (await env.client.get(f"{BASE}/{provider['id']}/tests/{test_id}")).json()
    assert result["status"] == "done"
    assert result["subject"] == "sub-47201"
    assert result["attributes"]["preferred_username"] == [f"idp.adm.{ADMIN1}"]
    assert result["plan"]["decision"] == "login"
    assert result["plan"]["matched_by"] == "login"
    assert result["plan"]["user_id"] == ADMIN1

    # Nothing was written: no link, and the provider is still disabled.
    links = (
        await env.session.execute(
            text("SELECT COUNT(*) FROM tiqora_identity_link WHERE provider_id = :p"),
            {"p": provider["id"]},
        )
    ).scalar_one()
    assert links == 0
    assert (await env.client.get(f"{BASE}/{provider['id']}")).json()["enabled"] is False

    # The run is bound to its admin and cannot be replayed.
    env.login_as(ADMIN2)
    assert (await env.client.get(f"{BASE}/{provider['id']}/tests/{test_id}")).status_code == 404
    env.login_as(ADMIN1)
    assert (await env.client.get(f"{BASE}/{provider['id']}/tests/nope")).status_code == 404
    again = await env.client.get(login_url)
    assert parse_qs(urlparse(again.headers["location"]).query)["sso_error"] == ["provider_disabled"]


async def test_test_run_of_another_provider_or_audience_is_refused(env: Env) -> None:
    first = await env.oidc_provider("tl-a")
    second = await env.oidc_provider("tl-b")
    started = await env.client.post(f"{BASE}/{first['id']}/tests", json={"audience": "agent"})
    test_id = started.json()["test_id"]
    # A test id of provider A does not open provider B.
    resp = await env.client.get(f"/api/v1/auth/sso/{SLUG}tl-b/login?test={test_id}")
    assert parse_qs(urlparse(resp.headers["location"]).query)["sso_error"] == ["provider_disabled"]
    assert (await env.client.get(f"{BASE}/{second['id']}/tests/{test_id}")).status_code == 404
    # customer audience is not offered by an agent-only provider
    nope = await env.client.post(f"{BASE}/{first['id']}/tests", json={"audience": "customer"})
    assert nope.status_code == 422


async def test_test_login_failed_run_is_reported(env: Env) -> None:
    provider = await env.oidc_provider("tl-fail")
    started = await env.client.post(f"{BASE}/{provider['id']}/tests", json={"audience": "agent"})
    test_id = started.json()["test_id"]
    resp = await env.client.get(started.json()["login_url"])
    state = parse_qs(urlparse(resp.headers["location"]).query)["state"][0]
    cb = await env.client.get(
        f"/api/v1/auth/oidc/{SLUG}tl-fail/callback", params={"code": "unknown", "state": state}
    )
    assert cb.status_code == 302
    assert cb.headers["location"] == f"/admin/identity-providers/{provider['id']}?test={test_id}"
    result = (await env.client.get(f"{BASE}/{provider['id']}/tests/{test_id}")).json()
    assert result["status"] == "failed"
    assert result["error"]
    assert result["plan"] is None


async def test_test_login_invalid_draft_is_422(env: Env) -> None:
    provider = await env.oidc_provider("tl-bad")
    resp = await env.client.post(
        f"{BASE}/{provider['id']}/tests",
        json={"draft": {"role_rules": [{"attribute": "g", "value": "v", "role_ids": [999999]}]}},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"] == {"role_rules": "Unbekannte Rolle: 999999"}


async def test_ldap_has_no_test_login(env: Env) -> None:
    created = await env.create("ldap1", preset="ldap")
    resp = await env.client.post(f"{BASE}/{created['id']}/tests", json={"audience": "agent"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "message": (
            "Für LDAP gibt es keine Testanmeldung – bitte die Vorschau mit Beispielwerten nutzen."
        )
    }


# --------------------------------------------------------------------------- preview


async def test_preview_resolves_account_and_names(env: Env) -> None:
    provider = await env.oidc_provider("prev")
    resp = await env.client.post(
        f"{BASE}/{provider['id']}/preview",
        json={"attributes": {"preferred_username": [f"idp.adm.{ADMIN1}"]}},
    )
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["decision"] == "login"
    assert plan["matched_by"] == "login"
    assert plan["user_id"] == ADMIN1
    assert plan["roles_add"] == []

    # A draft is applied in memory only: it shows in the plan but is never persisted.
    draft = {"match_by": "email", "attribute_map": {"login": ["uid"], "email": ["mail"]}}
    resp = await env.client.post(
        f"{BASE}/{provider['id']}/preview",
        json={"attributes": {"uid": ["idp.nobody.47299"]}, "draft": draft},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile"]["login"] == "idp.nobody.47299"
    assert resp.json()["decision"] == "reject"
    stored = (await env.client.get(f"{BASE}/{provider['id']}")).json()
    assert stored["match_by"] == "login"
    assert stored["attribute_map"]["login"] == ["preferred_username"]

    nobody = await env.client.post(
        f"{BASE}/{provider['id']}/preview",
        json={"attributes": {"preferred_username": ["idp.nobody.47299"]}},
    )
    assert nobody.json()["decision"] == "reject"
    assert nobody.json()["reject_code"] == "no_match"
    assert nobody.json()["reject_message"]

    bad = await env.client.post(
        f"{BASE}/{provider['id']}/preview",
        json={"attributes": {}, "draft": {"config": {"pkce": "bogus"}}},
    )
    assert bad.status_code == 422
    assert "config.pkce" in bad.json()["detail"]["errors"]


# --------------------------------------------------------------------------- links / assignments


async def test_links_after_real_login_and_delete(env: Env) -> None:
    provider = await env.oidc_provider("lnk", enabled=True)
    env.idp.claims["c"] = {"sub": "sub-47204", "preferred_username": f"idp.link.{LINKED}"}
    env.login_as(None)
    start = await env.client.get(f"/api/v1/auth/sso/{SLUG}lnk/login")
    assert start.status_code == 302, start.text
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    done = await env.client.get(
        f"/api/v1/auth/oidc/{SLUG}lnk/callback", params={"code": "c", "state": state}
    )
    assert done.status_code == 302, done.text
    assert env.client.cookies.get("tiqora_session")

    env.login_as(ADMIN1)
    listed = await env.client.get(f"{BASE}/{provider['id']}/links")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert (rows[0]["subject"], rows[0]["user_id"], rows[0]["user_kind"]) == (
        "sub-47204",
        LINKED,
        "agent",
    )
    assert rows[0]["user_login"] == f"idp.link.{LINKED}"
    assert rows[0]["first_seen"].endswith("Z") or "+" in rows[0]["first_seen"]
    summary = next(r for r in (await env.client.get(BASE)).json() if r["id"] == provider["id"])
    assert summary["link_count"] == 1

    assert (await env.client.get(f"{BASE}/{provider['id']}/links?limit=0")).status_code == 422
    other = await env.oidc_provider("lnk2")
    foreign = await env.client.delete(f"{BASE}/{other['id']}/links/{rows[0]['id']}")
    assert foreign.status_code == 404
    gone = await env.client.delete(f"{BASE}/{provider['id']}/links/{rows[0]['id']}")
    assert gone.status_code == 204
    assert (await env.client.get(f"{BASE}/{provider['id']}/links")).json() == []
    assert (
        await env.client.delete(f"{BASE}/{provider['id']}/links/{rows[0]['id']}")
    ).status_code == 404


async def test_managed_assignments_empty_and_listed(env: Env) -> None:
    provider = await env.oidc_provider("man")
    empty = await env.client.get(f"{BASE}/{provider['id']}/managed-assignments")
    assert empty.status_code == 200
    assert empty.json() == []
    await env.session.execute(
        text(
            "INSERT INTO tiqora_identity_managed_assignment (provider_id, user_id, kind,"
            " target_id, permission_key, created) VALUES (:p, :u, 'role', :r, '', :t)"
        ),
        {"p": provider["id"], "u": ADMIN1, "r": ROLE_ID, "t": NOW},
    )
    await env.session.commit()
    rows = (await env.client.get(f"{BASE}/{provider['id']}/managed-assignments")).json()
    assert len(rows) == 1
    assert rows[0]["login"] == f"idp.adm.{ADMIN1}"
    assert (rows[0]["kind"], rows[0]["target_name"]) == ("role", "idp-role-47200")
    assert (await env.client.get(f"{BASE}/{provider['id'] + 100_000}/links")).status_code == 404


async def test_plan_to_out_resolves_role_and_group_names(env: Env) -> None:
    group_id = (
        await env.session.execute(text("SELECT id FROM permission_groups WHERE name = 'admin'"))
    ).scalar_one()
    plan = await admin_api.plan_to_out(
        env.session,
        {
            "user_kind": "agent",
            "decision": "login",
            "matched_by": "link",
            "user_id": ADMIN1,
            "login": "x",
            "reject_code": None,
            "reject_message": None,
            "profile": {"login": "x", "email": None},
            "profile_changes": {"email": ["a@example.org", "b@example.org"]},
            "assignments": {
                "add_roles": [ROLE_ID],
                "remove_roles": [888888],
                "add_groups": [[group_id, "rw"]],
                "remove_groups": [],
            },
        },
    )
    assert [(r.id, r.name) for r in plan.roles_add] == [(ROLE_ID, "idp-role-47200")]
    assert [(r.id, r.name) for r in plan.roles_remove] == [(888888, "888888")]
    assert [(g.group_name, g.permission_key) for g in plan.groups_add] == [("admin", "rw")]
    assert plan.profile_changes == {"email": ["a@example.org", "b@example.org"]}


def test_every_identity_admin_route_maps_to_the_admin_api_key_area() -> None:
    from tiqora.domain.api_key_scopes import path_to_area

    paths = [p for p in create_app(_settings()).openapi()["paths"] if p.startswith(BASE)]
    assert len(paths) >= 11
    assert {path_to_area(p) for p in paths} == {"admin"}


async def test_clearing_metadata_source_drops_stored_metadata(
    env: Env, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    xml = _metadata_xml(tmp_path_factory)
    monkeypatch.setattr(
        admin_api,
        "_metadata_transport",
        httpx.MockTransport(lambda _r: httpx.Response(200, text=xml)),
    )
    created = await env.create("md-clear", preset="generic_saml")
    ok = await env.patch(
        created["id"], {"config": {"idp_metadata_url": "https://idp.example.org/md"}}
    )
    assert ok.json()["metadata_fetched_at"] is not None
    cleared = await env.patch(created["id"], {"config": {"idp_metadata_url": ""}})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["metadata_fetched_at"] is None
    stored = await get_provider_by_slug(env.session, f"{SLUG}md-clear", secret_key=KEY)
    assert stored is not None
    assert stored.metadata_xml is None and stored.metadata_error is None


async def test_patch_without_config_never_refetches_metadata(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def unreachable(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(503)

    monkeypatch.setattr(admin_api, "_metadata_transport", httpx.MockTransport(unreachable))
    stored = await make_provider(
        env.session,
        slug=f"{SLUG}md-down",
        kind="saml",
        enabled=True,
        config={"idp_metadata_url": "https://idp.example.org/md"},
        settings=env.settings,
    )
    await env.session.commit()
    resp = await env.patch(stored.id, {"enabled": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False
    assert calls == []


async def test_refresh_with_pasted_xml_only_is_422_without_stored_error(
    env: Env, tmp_path_factory: pytest.TempPathFactory
) -> None:
    created = await env.create("md-pasted", preset="generic_saml")
    saved = await env.patch(
        created["id"], {"config": {"idp_metadata_xml": _metadata_xml(tmp_path_factory)}}
    )
    assert saved.status_code == 200, saved.text
    resp = await env.client.post(f"{BASE}/{created['id']}/refresh-metadata")
    assert resp.status_code == 422
    assert "Metadaten-URL" in resp.json()["detail"]["message"]
    assert (await env.client.get(f"{BASE}/{created['id']}")).json()["metadata_error"] is None


async def test_large_pasted_metadata_is_stored_intact(
    env: Env, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Pasted IdP metadata lives in the ``config`` JSON: ~200 KB (a full ADFS
    FederationMetadata.xml) must fit on MariaDB too (MEDIUMTEXT, not 64 KiB TEXT)."""
    base = _metadata_xml(tmp_path_factory)
    padding = "".join(
        f'<md:ContactPerson contactType="other"><md:EmailAddress>mailto:c{i}@example.org'
        "</md:EmailAddress></md:ContactPerson>\n"
        for i in range(2500)
    )
    xml = base.replace("</md:EntityDescriptor>", f"{padding}</md:EntityDescriptor>")
    assert len(xml.encode()) > 200_000
    created = await env.create("md-big", preset="generic_saml")
    saved = await env.patch(created["id"], {"config": {"idp_metadata_xml": xml}})
    assert saved.status_code == 200, saved.text[:500]
    env.session.expire_all()
    stored = await get_provider_by_slug(env.session, f"{SLUG}md-big", secret_key=KEY)
    assert stored is not None
    assert stored.config.idp_metadata_xml == xml  # type: ignore[union-attr]
    reloaded = (await env.client.get(f"{BASE}/{created['id']}")).json()
    assert reloaded["config"]["idp_metadata_xml"] == xml


async def test_preview_checks_audience(env: Env) -> None:
    provider = await env.oidc_provider("prev-aud")
    resp = await env.client.post(
        f"{BASE}/{provider['id']}/preview", json={"audience": "customer", "attributes": {}}
    )
    assert resp.status_code == 422


async def test_stored_draft_keeps_unset_fields(env: Env) -> None:
    from tiqora.identity import flow_state
    from tiqora.identity.store import ProviderChanges, get_provider, record_with_changes

    provider = await env.oidc_provider("draft-keep")
    await env.patch(provider["id"], {"button_label": "Mein Login"})
    run = flow_state.TestRun(
        provider_id=provider["id"],
        admin_id=ADMIN1,
        audience="agent",
        draft=ProviderChanges(name="Neuer Name"),
    )
    await flow_state.put_test(env.redis, "tid-draft", run, secret_key=KEY)
    back = await flow_state.get_test(env.redis, "tid-draft", secret_key=KEY)
    assert back is not None and back.draft is not None
    assert back.draft.model_fields_set == {"name"}
    record = await get_provider(env.session, provider["id"], secret_key=KEY)
    assert record is not None
    drafted = record_with_changes(record, back.draft)
    assert (drafted.name, drafted.button_label) == ("Neuer Name", "Mein Login")


# --------------------------------------------------------------------------- phase 3 fields


async def test_patch_provider_step_up_and_lifetime(env: Env) -> None:
    created = await env.oidc_provider("stepup")
    assert created["require_mfa_claim"] is False
    assert created["mfa_policy"]["fallback"] == "local_second_factor"
    assert created["session_idle_seconds"] is None
    assert created["scim_enabled"] is False

    resp = await env.patch(
        created["id"],
        {
            "require_mfa_claim": True,
            "mfa_policy": {"fallback": "reject"},
            "session_idle_seconds": 900,
            "scim_enabled": True,
            "scim_adopt_existing": True,
        },
    )
    assert resp.status_code == 200, resp.text
    shown = (await env.client.get(f"{BASE}/{created['id']}")).json()
    assert shown["require_mfa_claim"] is True
    assert shown["session_idle_seconds"] == 900
    assert shown["session_absolute_seconds"] is None
    assert shown["scim_enabled"] is True
    assert shown["scim_adopt_existing"] is True
    assert shown["mfa_policy"]["fallback"] == "reject"
    assert shown["mfa_policy"]["oidc_amr_values"] == ["mfa"]  # other fields keep their defaults

    # a partial policy is merged onto the stored one, not onto the defaults
    resp = await env.patch(created["id"], {"mfa_policy": {"oidc_acr_values": ["urn:mfa"]}})
    assert resp.status_code == 200, resp.text
    merged = resp.json()["mfa_policy"]
    assert merged["fallback"] == "reject"
    assert merged["oidc_acr_values"] == ["urn:mfa"]

    # an explicit null resets a lifetime to the global value
    resp = await env.patch(created["id"], {"session_idle_seconds": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["session_idle_seconds"] is None


async def test_patch_provider_phase3_validation(env: Env) -> None:
    created = await env.oidc_provider("stepval")
    cases: list[tuple[dict[str, Any], str]] = [
        ({"session_idle_seconds": 10}, "session_idle_seconds"),
        ({"session_absolute_seconds": 10**9}, "session_absolute_seconds"),
        (
            {"session_idle_seconds": 3600, "session_absolute_seconds": 1000},
            "session_idle_seconds",
        ),
        ({"mfa_policy": {"oidc_amr_values": [f"v{i}" for i in range(21)]}}, "mfa_policy"),
    ]
    for body, field in cases:
        resp = await env.patch(created["id"], body)
        assert resp.status_code == 422, (body, resp.text)
        assert field in resp.json()["detail"]["errors"], (body, resp.text)
    unknown = await env.patch(created["id"], {"mfa_policy": {"no_such_field": 1}})
    assert unknown.status_code == 422
    shown = (await env.client.get(f"{BASE}/{created['id']}")).json()
    assert shown["session_idle_seconds"] is None  # nothing was saved
    assert shown["mfa_policy"]["oidc_amr_values"] == ["mfa"]


async def test_capabilities_include_limits_and_scim_url(env: Env) -> None:
    resp = await env.client.get(f"{BASE}/capabilities")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_idle_max_seconds"] == env.settings.session_ttl_seconds
    assert body["session_absolute_max_seconds"] == env.settings.session_absolute_ttl_seconds
    assert body["scim_base_url"] == "https://helpdesk.example.org/scim/v2"
