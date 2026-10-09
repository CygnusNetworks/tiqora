"""Optional Keycloak round trip for OIDC and SAML login + logout (wave 1, Task 25).

Opt-in: ``TIQORA_KEYCLOAK_TESTS=1`` (needs Docker, pulls quay.io/keycloak/keycloak:26.0.7, ~1-2 min
start-up). Not part of CI; without the variable the module is skipped.

A real Keycloak realm ``tiqora`` gets a confidential OIDC client and a SAML client (created from
Tiqora's own SP metadata via Keycloak's client-description converter, signing RSA-SHA256) plus the
user ``kc.anna`` in the group ``support``. Tiqora runs in-process (ASGI) against the MariaDB test
container; every request to Keycloak is a real HTTP call. The browser leg is played by a second
``httpx`` client (login form, auto-POST form). Provider defaults are NOT weakened: id_token
verification against the discovery issuer + JWKS, signed SAML assertions, SHA-1 rejected.

Seed band 47180-47198 (role 47180); the JIT agent ``kc.anna`` gets an auto id and is removed by
login. Provider slugs use the ``idptest-kc-`` prefix.
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import AsyncIterator, Iterator
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import FakeRedis, cleanup_statements, make_provider
from tests._saml_idp import require_saml
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.oidc import OIDCService
from tiqora.identity import sso_routes
from tiqora.identity.config_models import AttributeMap, JitDefaults, RoleRule
from tiqora.identity.store import ProviderChanges, update_provider
from tiqora.security import outbound

pytestmark = [pytest.mark.keycloak, pytest.mark.db]

if os.environ.get("TIQORA_KEYCLOAK_TESTS") != "1":
    pytest.skip(
        "set TIQORA_KEYCLOAK_TESTS=1 to run the Keycloak round trip", allow_module_level=True
    )
require_saml()

IMAGE = "quay.io/keycloak/keycloak:26.0.7"
KEY = "unit-test-secret-key-for-identity-keycloak"
BAND = (47180, 47198)
ROLE_ID = 47180
SLUG = "idptest-kc-"
OIDC_SLUG = f"{SLUG}oidc"
SAML_SLUG = f"{SLUG}saml"
BASE = "https://helpdesk.example.org"
SAME = {"Origin": BASE}
REALM = "tiqora"
LOGIN, PASSWORD = "kc.anna", "anna-pw-4711"
NOW = "2026-10-08 12:00:00"
OIDC_SECRET = secrets.token_urlsafe(24)  # per run, nothing static to leak
_URLS: set[str] = set()
_INVAL_MARK: dict[str, int] = {}


# --------------------------------------------------------------------------- Keycloak


# Tiqora's SSRF guard blocks loopback for good (allow_private_network only admits RFC1918), so
# the IdP lives under a fake host name that the test resolver maps to a private address. Both the
# server-side fetches (pinned to that address) and the browser leg are carried to the container's
# published port by a transport that only rewrites the connect target; Host stays untouched.
KC_HOST = "keycloak.test"
KC_PRIVATE_IP = "10.255.0.1"


def _retarget(request: httpx.Request, port: int) -> httpx.Request:
    """A copy aimed at the container; the original keeps its URL (cookie jar domain, Host)."""
    return httpx.Request(
        request.method,
        request.url.copy_with(host="127.0.0.1", port=port),
        headers=request.headers,
        stream=request.stream,
        extensions=request.extensions,
    )


class _ToContainerAsync(httpx.AsyncBaseTransport):
    def __init__(self, port: int) -> None:
        self.port = port
        self.inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.inner.handle_async_request(_retarget(request, self.port))


class _ToContainerSync(httpx.BaseTransport):
    def __init__(self, port: int) -> None:
        self.port = port
        self.inner = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.inner.handle_request(_retarget(request, self.port))


class Keycloak:
    def __init__(self, port: int) -> None:
        self.port = port
        self.base = f"http://{KC_HOST}:{port}"  # what Tiqora and the browser call the IdP
        self.admin = httpx.Client(base_url=f"http://localhost:{port}", timeout=30)
        self.issuer = f"{self.base}/realms/{REALM}"

    def authenticate(self) -> None:
        resp = self.admin.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "admin",
                "password": "admin",
            },
        )
        resp.raise_for_status()
        self.admin.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

    def post(self, path: str, body: Any) -> httpx.Response:
        resp = self.admin.post(f"/admin/realms/{REALM}{path}", json=body)
        assert resp.status_code in (200, 201, 204), (path, resp.status_code, resp.text)
        return resp

    def put(self, path: str, body: Any = None) -> None:
        resp = self.admin.put(f"/admin/realms/{REALM}{path}", json=body)
        assert resp.status_code in (200, 204), (path, resp.status_code, resp.text)

    def get(self, path: str, **params: Any) -> Any:
        resp = self.admin.get(f"/admin/realms/{REALM}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def client_uuid(self, client_id: str) -> str:
        return str(self.get("/clients", clientId=client_id)[0]["id"])

    def setup_realm(self) -> None:
        self.authenticate()
        resp = self.admin.post("/admin/realms", json={"realm": REALM, "enabled": True})
        assert resp.status_code == 201, resp.text
        self.post(
            "/users",
            {
                "username": LOGIN,
                "email": "kc.anna@keycloak.test",
                "emailVerified": True,
                "firstName": "Anna",
                "lastName": "Keller",
                "enabled": True,
                "credentials": [{"type": "password", "value": PASSWORD, "temporary": False}],
            },
        )
        self.post("/groups", {"name": "support"})
        user_id = self.get("/users", username=LOGIN, exact="true")[0]["id"]
        group_id = self.get("/groups", search="support")[0]["id"]
        self.put(f"/users/{user_id}/groups/{group_id}")

    def create_oidc_client(self, redirect_uri: str, post_logout_uri: str) -> None:
        self.post(
            "/clients",
            {
                "clientId": "tiqora-oidc",
                "protocol": "openid-connect",
                "enabled": True,
                "publicClient": False,
                "secret": OIDC_SECRET,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "redirectUris": [redirect_uri],
                "attributes": {"post.logout.redirect.uris": post_logout_uri},
                "protocolMappers": [
                    {
                        "name": "groups",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-group-membership-mapper",
                        "config": {
                            "claim.name": "groups",
                            "full.path": "false",
                            "id.token.claim": "true",
                            "access.token.claim": "true",
                            "userinfo.token.claim": "true",
                        },
                    }
                ],
            },
        )

    def create_saml_client(self, sp_metadata_xml: str, sls_url: str) -> None:
        converted = self.admin.post(
            f"/admin/realms/{REALM}/client-description-converter",
            content=sp_metadata_xml,
            headers={"Content-Type": "application/xml"},
        )
        assert converted.status_code == 200, converted.text
        client: dict[str, Any] = converted.json()
        attrs: dict[str, str] = dict(client.get("attributes") or {})
        attrs.update(
            {
                "saml.force.name.id.format": "true",
                "saml_name_id_format": "persistent",
                "saml.server.signature": "true",
                "saml.assertion.signature": "true",
                "saml.signature.algorithm": "RSA_SHA256",
                "saml.client.signature": "true",  # Tiqora signs AuthnRequest + LogoutRequest
                "saml.encrypt": "false",
                "saml_single_logout_service_url_redirect": sls_url,
            }
        )
        client.update(
            {
                "protocol": "saml",
                "enabled": True,
                "frontchannelLogout": True,
                "attributes": attrs,
                "protocolMappers": [
                    _saml_prop("username", "username", "username"),
                    _saml_prop("email", "email", "email"),
                    _saml_prop("firstName", "firstName", "first_name"),
                    _saml_prop("lastName", "lastName", "last_name"),
                    {
                        "name": "groups",
                        "protocol": "saml",
                        "protocolMapper": "saml-group-membership-mapper",
                        "config": {
                            "attribute.name": "groups",
                            "attribute.nameformat": "Basic",
                            "single": "false",
                            "full.path": "false",
                        },
                    },
                ],
            }
        )
        self.post("/clients", client)


def _saml_prop(name: str, prop: str, attribute: str) -> dict[str, Any]:
    return {
        "name": name,
        "protocol": "saml",
        "protocolMapper": "saml-user-property-mapper",
        "config": {
            "user.attribute": prop,
            "attribute.name": attribute,
            "friendly.name": name,
            "attribute.nameformat": "Basic",
        },
    }


@pytest.fixture(scope="module")
def keycloak() -> Iterator[Keycloak]:
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415

    container = (
        DockerContainer(IMAGE)
        .with_command("start-dev")
        .with_env("KC_BOOTSTRAP_ADMIN_USERNAME", "admin")
        .with_env("KC_BOOTSTRAP_ADMIN_PASSWORD", "admin")
        .with_env("KC_HEALTH_ENABLED", "true")
        .with_exposed_ports(8080)
    )
    with container:
        port = int(container.get_exposed_port(8080))
        base = f"http://localhost:{port}"
        deadline = time.monotonic() + 240
        while True:
            try:
                if httpx.get(f"{base}/realms/master", timeout=5).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                logs = container.get_logs()
                pytest.fail(f"Keycloak did not start: {logs}")
            time.sleep(2)
        kc = Keycloak(port)
        kc.setup_realm()
        yield kc


# --------------------------------------------------------------------------- browser


class _Forms(HTMLParser):
    """Collects ``<form>`` elements with their hidden/text inputs."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "form":
            self.forms.append({"id": a.get("id", ""), "action": a.get("action", ""), "fields": {}})
        elif tag == "input" and self.forms and a.get("name"):
            self.forms[-1]["fields"][a["name"]] = a.get("value", "")


def _forms(html: str) -> list[dict[str, Any]]:
    parser = _Forms()
    parser.feed(html)
    return parser.forms


class Browser:
    """The user's browser talking to Keycloak (cookies kept across the login and the logout)."""

    def __init__(self, kc: Keycloak) -> None:
        self.kc = kc
        self.http = httpx.Client(
            transport=_ToContainerSync(kc.port), timeout=30, follow_redirects=False
        )

    def login(self, url: str) -> httpx.Response:
        """Follow *url* through Keycloak's login form; return the response after the POST."""
        page = self.http.get(url)
        while page.status_code in (301, 302, 303):
            page = self.http.get(urljoin(str(page.url), page.headers["location"]))
        assert page.status_code == 200, page.text
        form = next((f for f in _forms(page.text) if f["id"] == "kc-form-login"), None)
        assert form is not None, page.text[:2000]
        fields = {**form["fields"], "username": LOGIN, "password": PASSWORD}
        fields.pop("credentialId", None)
        resp = self.http.post(urljoin(str(page.url), form["action"]), data=fields)
        return resp

    def close(self) -> None:
        self.http.close()


def _location_path(resp: httpx.Response) -> str:
    """Path + query of a redirect aimed at the Tiqora base."""
    assert resp.status_code == 302, (resp.status_code, resp.text[:1500])
    target = urlparse(resp.headers["location"])
    assert f"{target.scheme}://{target.netloc}" == BASE, resp.headers["location"]
    return target.path + (f"?{target.query}" if target.query else "")


# --------------------------------------------------------------------------- Tiqora env


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
            by_login = "(SELECT id FROM users WHERE login = :login)"
            for table in (
                "tiqora_identity_link",
                "tiqora_identity_managed_assignment",
                "tiqora_user_auth_config",
                "user_preferences",
                "role_user",
                "group_user",
            ):
                conn.execute(
                    text(f"DELETE FROM {table} WHERE user_id IN {by_login}"), {"login": LOGIN}
                )
            conn.execute(text("DELETE FROM users WHERE login = :login"), {"login": LOGIN})
            conn.execute(text("DELETE FROM role_user WHERE role_id = :r"), {"r": ROLE_ID})
            conn.execute(text("DELETE FROM roles WHERE id = :r"), {"r": ROLE_ID})
            conn.execute(
                text("DELETE FROM tiqora_cache_invalidation WHERE id > :mx"),
                {"mx": _INVAL_MARK[url]},
            )
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


class Env:
    def __init__(
        self, session: AsyncSession, client: httpx.AsyncClient, settings: Settings, kc: Keycloak
    ) -> None:
        self.session = session
        self.client = client
        self.settings = settings
        self.kc = kc
        self.browser = Browser(kc)

    async def provider(
        self, slug: str, kind: str, config: dict[str, Any], amap: AttributeMap
    ) -> None:
        rec = await make_provider(
            self.session,
            slug=slug,
            kind=kind,  # type: ignore[arg-type]
            attribute_map=amap,
            jit_enabled=True,
            config=config,
            settings=self.settings,
        )
        await update_provider(
            self.session,
            rec.id,
            ProviderChanges(
                jit_defaults=JitDefaults(),
                role_rules=[RoleRule(attribute="groups", value="support", role_ids=[ROLE_ID])],
                sync_mode="managed",
            ),
            secret_key=self.settings.secret_key,
            actor_id=1,
        )
        await self.session.commit()

    async def scalars(self, sql: str, **params: Any) -> list[Any]:
        rows = await self.session.execute(text(sql), params)
        return [r[0] for r in rows.all()]

    async def assert_jit_session(self) -> None:
        """The Tiqora session cookie belongs to JIT agent ``kc.anna`` with the group role."""
        me = await self.client.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text
        body = me.json()
        assert body["login"] == LOGIN
        assert body["sso_logout"] is True  # the IdP logout can be offered
        ids = await self.scalars("SELECT id FROM users WHERE login = :l", l=LOGIN)
        assert len(ids) == 1
        roles = await self.scalars("SELECT role_id FROM role_user WHERE user_id = :u", u=ids[0])
        assert roles == [ROLE_ID]  # from the Keycloak ``groups`` claim/attribute (rule "support")

    def close(self) -> None:
        self.browser.close()


@pytest_asyncio.fixture
async def env(
    mariadb_znuny_url: str, keycloak: Keycloak, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    url = mariadb_znuny_url
    real_resolve = outbound._default_resolve
    monkeypatch.setattr(
        outbound,
        "_default_resolve",
        lambda host, port: [KC_PRIVATE_IP] if host == KC_HOST else real_resolve(host, port),
    )
    monkeypatch.setattr(
        sso_routes,
        "new_oidc_service",
        lambda config: OIDCService(config, transport=_ToContainerAsync(keycloak.port)),
    )
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        environment="test", secret_key=KEY, public_base_url=BASE, session_cookie_secure=True
    )
    app = create_app(settings)
    app.state.redis = FakeRedis()

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    async with (
        maker() as session,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client,
    ):
        await session.execute(
            text(
                "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:r, 'idp-kc-role', 1, :t, 1, :t, 1)"
            ),
            {"r": ROLE_ID, "t": NOW},
        )
        await session.commit()
        e = Env(session, client, settings, keycloak)
        yield e
        e.close()
    await engine.dispose()
    _clean(url)


def _ensure_oidc_client(kc: Keycloak) -> None:
    if kc.get("/clients", clientId="tiqora-oidc"):
        return
    kc.create_oidc_client(
        f"{BASE}/api/v1/auth/oidc/{OIDC_SLUG}/callback", f"{BASE}/login?logged_out=1"
    )


# --------------------------------------------------------------------------- tests


async def test_oidc_login_jit_roles_and_logout(env: Env) -> None:
    kc = env.kc
    _ensure_oidc_client(kc)
    await env.provider(
        OIDC_SLUG,
        "oidc",
        {
            "issuer": kc.issuer,
            "client_id": "tiqora-oidc",
            "client_secret": OIDC_SECRET,
            "allow_private_network": True,
        },
        AttributeMap(
            login=["preferred_username"],
            email=["email"],
            first_name=["given_name"],
            last_name=["family_name"],
        ),
    )

    start = await env.client.get(f"/api/v1/auth/sso/{OIDC_SLUG}/login?next=/tickets")
    assert start.status_code == 302, start.text
    auth_url = start.headers["location"]
    assert auth_url.startswith(f"{kc.issuer}/protocol/openid-connect/auth")
    query = parse_qs(urlparse(auth_url).query)
    assert query["code_challenge_method"] == ["S256"]

    after_login = env.browser.login(auth_url)
    callback = _location_path(after_login)
    assert callback.startswith(f"/api/v1/auth/oidc/{OIDC_SLUG}/callback?")

    done = await env.client.get(callback)
    assert done.status_code == 302, done.text
    assert done.headers["location"] == "/tickets"
    assert env.client.cookies.get("tiqora_session")
    await env.assert_jit_session()

    # SP-initiated logout: local session gone, then Keycloak end_session with id_token_hint.
    out = await env.client.post("/api/v1/auth/sso/logout", headers=SAME)
    assert out.status_code == 303, out.text
    end_session = out.headers["location"]
    assert end_session.startswith(f"{kc.issuer}/protocol/openid-connect/logout")
    assert "id_token_hint=" in end_session
    assert (await env.client.get("/api/v1/auth/me")).status_code == 401

    kc_out = env.browser.http.get(end_session)
    assert kc_out.status_code == 302, kc_out.text[:1500]
    assert kc_out.headers["location"] == f"{BASE}/login?logged_out=1"
    # the Keycloak SSO session is really gone: the auth URL asks for credentials again
    again = env.browser.http.get(auth_url)
    assert again.status_code == 200, again.text[:500]
    assert any(f["id"] == "kc-form-login" for f in _forms(again.text))


async def test_saml_login_jit_roles_and_logout(env: Env) -> None:
    kc = env.kc
    descriptor = env.browser.http.get(f"{kc.issuer}/protocol/saml/descriptor")
    descriptor.raise_for_status()
    await env.provider(
        SAML_SLUG,
        "saml",
        {
            "idp_metadata_xml": descriptor.text,
            "name_id_format": "persistent",
            "allow_private_network": True,
        },
        AttributeMap(
            login=["username"], email=["email"], first_name=["first_name"], last_name=["last_name"]
        ),
    )
    meta = await env.client.get(f"/api/v1/auth/saml/{SAML_SLUG}/metadata")
    assert meta.status_code == 200, meta.text
    if not kc.get("/clients", clientId=f"{BASE}/api/v1/auth/saml/{SAML_SLUG}/metadata"):
        kc.create_saml_client(meta.text, f"{BASE}/api/v1/auth/saml/{SAML_SLUG}/sls")

    start = await env.client.get(f"/api/v1/auth/sso/{SAML_SLUG}/login?next=/tickets")
    assert start.status_code == 302, start.text
    sso_url = start.headers["location"]
    assert sso_url.startswith(f"{kc.issuer}/protocol/saml")
    assert "Signature=" in sso_url  # signed AuthnRequest (redirect binding)

    page = env.browser.login(sso_url)
    assert page.status_code == 200, page.text[:1500]
    acs_form = next((f for f in _forms(page.text) if "SAMLResponse" in f["fields"]), None)
    assert acs_form is not None, page.text[:1500]
    assert acs_form["action"] == f"{BASE}/api/v1/auth/saml/{SAML_SLUG}/acs"

    posted = await env.client.post(
        urlparse(acs_form["action"]).path,
        data=acs_form["fields"],
        headers={"Origin": kc.base},  # cross-site POST from the IdP: ACS is CSRF-exempt
    )
    # success answers with a meta-refresh page (the session cookie rides on a 200, not a redirect)
    assert posted.status_code == 200, posted.text[:1500]
    assert 'http-equiv="refresh" content="0;url=/tickets"' in posted.text, posted.text[:1500]
    assert env.client.cookies.get("tiqora_session")
    await env.assert_jit_session()

    # SP-initiated single logout through Keycloak's SLO endpoint (signed LogoutRequest).
    out = await env.client.post("/api/v1/auth/sso/logout", headers=SAME)
    assert out.status_code == 303, out.text
    slo_url = out.headers["location"]
    assert slo_url.startswith(f"{kc.issuer}/protocol/saml")
    assert "SAMLRequest=" in slo_url and "Signature=" in slo_url
    assert (await env.client.get("/api/v1/auth/me")).status_code == 401

    kc_resp = env.browser.http.get(slo_url)
    assert kc_resp.status_code == 302, kc_resp.text[:1500]
    sls_path = _location_path(kc_resp)
    assert sls_path.startswith(f"/api/v1/auth/saml/{SAML_SLUG}/sls?")
    assert "SAMLResponse=" in sls_path and "Signature=" in sls_path

    final = await env.client.get(sls_path)
    assert final.status_code == 303, final.text
    assert final.headers["location"] == "/login?logged_out=1"
