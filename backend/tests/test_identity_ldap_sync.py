"""LDAP directories as identity providers with Znuny AuthSync parity (wave 1, Task 19).

ASGI + DB, both dialects, directories are ldap3 ``MOCK_SYNC`` (``tests/ldap_directory.py``).
Seed band 47210-47249 (the brief's 47500-47599 belongs to reporting; every identity
100-block is claimed, this sub-range is unused by its owner, Task 13, which seeds only
47200-47204/47299; conftest restores the DB between modules anyway): agents 47210-47219,
role 47210, group 47210. JIT-created agents get auto-increment ids and are removed by
their ``idpldap.%`` logins, customers by ``idp.%@example.org``. Provider slugs use the
``idpldap-`` prefix; env rows (``env-*``) are removed like in the other identity modules.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import NOW, FakeRedis, cleanup_statements, make_provider, seed_agent
from tests._identity_helpers import seed_customer as _seed_customer
from tests.ldap_directory import BIND_DN, BIND_PW, Directory, install_directory
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.identity.config_models import GroupGrant, JitDefaults, RoleRule
from tiqora.identity.store import ProviderChanges, ProviderRecord, update_provider
from tiqora.security.ratelimit import AuthRateLimiter

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-ldap"
BAND = (47210, 47249)
SLUG = "idpldap-"
ROLE = 47210
GROUP = 47210
HOST = "ldap.example.org"
PEOPLE = "ou=people,dc=example,dc=org"
SUPPORT_DN = "cn=support,ou=groups,dc=example,dc=org"
BASE = dict(
    environment="test",
    secret_key=KEY,
    public_base_url="https://helpdesk.example.org",
    portal_enabled=True,
)
_URLS: set[str] = set()
_MARK: dict[str, int] = {}  # url -> MAX(id) of tiqora_cache_invalidation at module start
_JIT_USERS = "(SELECT id FROM users WHERE login LIKE 'idpldap.%')"


def _stmts(url: str) -> tuple[tuple[str, dict[str, object]], ...]:
    band = {"lo": BAND[0], "hi": BAND[1]}
    by_login = tuple(
        (f"DELETE FROM {table} WHERE user_id IN {_JIT_USERS}", {})
        for table in (
            "tiqora_identity_link",
            "tiqora_identity_managed_assignment",
            "tiqora_user_auth_config",
            "tiqora_user_totp",
            "user_preferences",
            "role_user",
            "group_user",
        )
    )
    env_rows = (
        (
            "DELETE FROM tiqora_identity_link WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'env-%')",
            {},
        ),
        (
            "DELETE FROM tiqora_identity_managed_assignment WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'env-%')",
            {},
        ),
        ("DELETE FROM tiqora_identity_provider WHERE slug LIKE 'env-%'", {}),
    )
    return (
        *by_login,
        ("DELETE FROM tiqora_user_totp WHERE user_id BETWEEN :lo AND :hi", band),
        ("DELETE FROM role_user WHERE role_id = :r", {"r": ROLE}),
        ("DELETE FROM group_user WHERE group_id = :g", {"g": GROUP}),
        ("DELETE FROM roles WHERE id = :r", {"r": ROLE}),
        ("DELETE FROM permission_groups WHERE id = :g", {"g": GROUP}),
        ("DELETE FROM tiqora_cache_invalidation WHERE id > :mx", {"mx": _MARK.get(url, 2**62)}),
        *env_rows,
        *cleanup_statements(user_ids=BAND, slug_prefix=SLUG),
        ("DELETE FROM users WHERE login LIKE 'idpldap.%'", {}),
    )


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            if url not in _MARK:
                row = conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
                )
                _MARK[url] = int(row.scalar_one())
            for stmt, params in _stmts(url):
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


class Limiter:
    """Records the rate limiter's failure accounting (real check(), fake bookkeeping)."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.resets: list[str] = []


@pytest.fixture
def limiter(monkeypatch: pytest.MonkeyPatch) -> Limiter:
    rec = Limiter()

    async def record_failure(self: AuthRateLimiter, *, login: str, ip: str) -> None:
        rec.failures.append(login)

    async def reset(self: AuthRateLimiter, *, login: str, ip: str | None = None) -> None:
        rec.resets.append(login)

    monkeypatch.setattr(AuthRateLimiter, "record_failure", record_failure)
    monkeypatch.setattr(AuthRateLimiter, "reset", reset)
    return rec


@pytest.fixture
def directory(monkeypatch: pytest.MonkeyPatch) -> Directory:
    d = install_directory(monkeypatch)
    for host in (HOST, "ldap2.example.org"):
        d.server(host)
    return d


def person(d: Directory, uid: str, password: str, host: str = HOST, **attrs: Any) -> str:
    dn = f"uid={uid},{PEOPLE}"
    d.add(
        d.servers[host],
        dn,
        uid=uid,
        userPassword=password,
        objectClass="inetOrgPerson",
        entryUUID=f"uuid-{uid}",
        **attrs,
    )
    return dn


class Env:
    def __init__(self, session: AsyncSession, maker: async_sessionmaker[AsyncSession]) -> None:
        self.s = session
        self.maker = maker

    async def rows(self, sql: str, **params: object) -> list[tuple[Any, ...]]:
        await self.s.commit()  # fresh snapshot: the app writes through other sessions
        return [tuple(r) for r in (await self.s.execute(text(sql), params)).all()]

    def client(self, settings: Settings) -> httpx.AsyncClient:
        app = create_app(settings)
        app.state.redis = FakeRedis()
        maker = self.maker

        async def _get_db() -> AsyncIterator[AsyncSession]:
            async with maker() as s:
                yield s

        app.dependency_overrides[get_db] = _get_db
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://helpdesk.example.org",
            headers={"Origin": "https://helpdesk.example.org"},
        )

    async def provider(
        self,
        name: str,
        *,
        host: str = HOST,
        audience: str = "agent",
        sort: int = 100,
        jit: bool = False,
        jit_defaults: JitDefaults | None = None,
        rules: list[RoleRule] | None = None,
        require_sso_opt_in: bool = False,
    ) -> ProviderRecord:
        p = await make_provider(
            self.s,
            slug=f"{SLUG}{name}",
            kind="ldap",
            audience=audience,  # type: ignore[arg-type]
            jit_enabled=jit,
            require_sso_opt_in=require_sso_opt_in,
            config={
                "host": host,
                "base_dn": "dc=example,dc=org",
                "bind_dn": BIND_DN,
                "bind_password": BIND_PW,
                "access_attr": "memberUid",
                "user_attr": "UID",
            },
            settings=Settings(**BASE),  # type: ignore[arg-type]
        )
        p = await update_provider(
            self.s,
            p.id,
            ProviderChanges(sort=sort, jit_defaults=jit_defaults, role_rules=rules),
            secret_key=KEY,
            actor_id=1,
        )
        await self.s.commit()
        return p

    async def links(self, provider_slug: str) -> set[tuple[str, int]]:
        rows = await self.rows(
            "SELECT l.subject, l.user_id FROM tiqora_identity_link l"
            " JOIN tiqora_identity_provider p ON p.id = l.provider_id WHERE p.slug = :s",
            s=provider_slug,
        )
        return {(str(a), int(b)) for a, b in rows}


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        for stmt in (
            "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:r, 'idp-ldap-role', 1, :t, 1, :t, 1)",
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:g, 'idp-ldap-group', 1, :t, 1, :t, 1)",
        ):
            await s.execute(text(stmt), {"r": ROLE, "g": GROUP, "t": NOW})
        await s.commit()
        yield Env(s, maker)
        await s.rollback()
    await engine.dispose()
    _clean(url)


def _env_ldap(**kw: Any) -> Settings:
    return Settings(  # type: ignore[arg-type]
        **BASE,
        ldap_enabled=True,
        ldap_host=HOST,
        ldap_base_dn="dc=example,dc=org",
        ldap_bind_dn=BIND_DN,
        ldap_bind_password=BIND_PW,
        ldap_uid_attr="uid",
        **kw,
    )


async def _login(client: httpx.AsyncClient, login: str, password: str) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"login": login, "password": password})


async def test_env_ldap_login_unchanged(env: Env, directory: Directory, limiter: Limiter) -> None:
    person(directory, "idpldap.anna", "anna-secret", givenName="Anna", sn="Directory")
    person(directory, "idpldap.bert", "bert-secret")
    person(directory, "idpldap.ghost", "ghost-secret")  # in the directory, no agent
    await seed_agent(env.s, 47210, "idpldap.anna")
    await seed_agent(env.s, 47211, "idpldap.bert")
    await env.s.execute(
        text("INSERT INTO tiqora_user_totp (user_id, secret, enabled) VALUES (47211, 'x', :t)"),
        {"t": True},
    )
    await env.s.commit()

    async with env.client(_env_ldap()) as client:
        ok = await _login(client, "idpldap.anna", "anna-secret")
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["user"]["id"] == 47210 and body["user"]["login"] == "idpldap.anna"
        assert body["pending_2fa"] is False and body.get("must_enroll_2fa") is False
        assert "tiqora_session" in ok.cookies
        assert limiter.resets == ["idpldap.anna"] and limiter.failures == []

        pending = await _login(client, "idpldap.bert", "bert-secret")
        assert pending.status_code == 200, pending.text
        assert pending.json()["pending_2fa"] is True and pending.json()["user"] is None
        assert pending.json()["totp_enrolled"] is True

        for login, password in (
            ("idpldap.ghost", "ghost-secret"),  # bind ok, no account: plan rejected
            ("idpldap.anna", "wrong"),
            ("idpldap.nobody", "x"),
            ("idpldap.anna", ""),
        ):
            resp = await _login(client, login, password)
            assert resp.status_code in (401, 422), (login, resp.text)
            if resp.status_code == 401:
                assert resp.json()["detail"] == "Invalid credentials"
        assert limiter.failures[:3] == ["idpldap.ghost", "idpldap.anna", "idpldap.nobody"]

    # Profile untouched (env map = login only), link recorded with the entryUUID subject.
    names = await env.rows("SELECT first_name, last_name FROM users WHERE id = 47210")
    assert names == [("Idp", "Test")]
    assert await env.links("env-ldap") == {
        ("uuid-idpldap.anna", 47210),
        ("uuid-idpldap.bert", 47211),
    }


async def test_two_ldap_providers_in_order(
    env: Env, directory: Directory, limiter: Limiter
) -> None:
    person(directory, "idpldap.cora", "old-password", host=HOST)
    person(directory, "idpldap.cora", "new-password", host="ldap2.example.org")
    await seed_agent(env.s, 47212, "idpldap.cora")
    first = await env.provider("first", sort=10)
    second = await env.provider("second", host="ldap2.example.org", sort=20)
    _ = first

    async with env.client(Settings(**BASE)) as client:  # type: ignore[arg-type]
        resp = await _login(client, "idpldap.cora", "new-password")
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["id"] == 47212
        assert await env.links(second.slug) == {("uuid-idpldap.cora", 47212)}
        assert await env.links(f"{SLUG}first") == set()

        resp = await _login(client, "idpldap.cora", "old-password")  # provider 1 wins
        assert resp.status_code == 200, resp.text
        assert await env.links(f"{SLUG}first") == {("uuid-idpldap.cora", 47212)}

        resp = await _login(client, "idpldap.cora", "neither")
        assert resp.status_code == 401
    assert limiter.failures == ["idpldap.cora"]


async def test_ldap_jit_and_role_sync(env: Env, directory: Directory, limiter: Limiter) -> None:
    dn = person(
        directory,
        "idpldap.emil",
        "emil-secret",
        mail="emil@example.test",
        givenName="Emil",
        sn="Alt",
    )
    directory.add(
        directory.servers[HOST],
        SUPPORT_DN,
        cn="support",
        objectClass="posixGroup",
        memberUid="idpldap.emil",
    )
    rule = RoleRule.model_validate(
        {"attribute": "ldap:group_dn", "value": SUPPORT_DN, "role_ids": [ROLE]}
    )
    await env.provider(
        "jit",
        jit=True,
        jit_defaults=JitDefaults(initial_groups=[GroupGrant(group_id=GROUP)]),
        rules=[rule],
    )

    async with env.client(Settings(**BASE)) as client:  # type: ignore[arg-type]
        resp = await _login(client, "idpldap.emil", "emil-secret")
        assert resp.status_code == 200, resp.text
        uid = resp.json()["user"]["id"]
        assert await env.rows("SELECT first_name, last_name FROM users WHERE id = :u", u=uid) == [
            ("Emil", "Alt")
        ]
        email = await env.rows(
            "SELECT preferences_value FROM user_preferences"
            " WHERE user_id = :u AND preferences_key = 'UserEmail'",
            u=uid,
        )
        value = email[0][0]
        assert (value.decode() if isinstance(value, bytes) else value) == "emil@example.test"
        assert await env.rows("SELECT role_id FROM role_user WHERE user_id = :u", u=uid) == [
            (ROLE,)
        ]
        keys = await env.rows(
            "SELECT permission_key FROM group_user WHERE user_id = :u AND group_id = :g",
            u=uid,
            g=GROUP,
        )
        assert keys == [("rw",)]

        # AuthSync on the next login: changed surname is written, group left -> role goes.
        directory.replace(HOST, dn, sn="Neu")
        directory.delete_value(HOST, SUPPORT_DN, "memberUid", "idpldap.emil")
        resp = await _login(client, "idpldap.emil", "emil-secret")
        assert resp.status_code == 200, resp.text
        assert resp.json()["user"]["id"] == uid
    assert await env.rows("SELECT last_name FROM users WHERE id = :u", u=uid) == [("Neu",)]
    assert await env.rows("SELECT role_id FROM role_user WHERE user_id = :u", u=uid) == []
    # The initial group was written without a managed mark: it stays.
    assert await env.rows(
        "SELECT permission_key FROM group_user WHERE user_id = :u AND group_id = :g",
        u=uid,
        g=GROUP,
    ) == [("rw",)]
    assert limiter.failures == []


async def test_customer_ldap_provider(env: Env, directory: Directory, limiter: Limiter) -> None:
    carla = "idp.ldap.carla@example.org"
    dora = "idp.ldap.dora@example.org"
    person(directory, carla, "carla-secret")
    person(
        directory,
        dora,
        "dora-secret",
        host="ldap2.example.org",
        mail="dora@example.test",
        givenName="Dora",
        sn="Kundin",
    )
    carla_id = await _seed_customer(env.s, carla)

    env_settings = Settings(  # type: ignore[arg-type]
        **BASE,
        customer_ldap_enabled=True,
        customer_ldap_host=HOST,
        customer_ldap_base_dn="dc=example,dc=org",
        customer_ldap_bind_dn=BIND_DN,
        customer_ldap_bind_password=BIND_PW,
        customer_ldap_uid_attr="uid",
    )
    async with env.client(env_settings) as client:
        resp = await client.post(
            "/api/portal/auth/login", json={"login": carla, "password": "carla-secret"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["customer"]["id"] == carla_id
        assert resp.json()["customer"]["login"] == carla
        resp = await client.post(
            "/api/portal/auth/login", json={"login": carla, "password": "wrong"}
        )
        assert resp.status_code == 401 and resp.json()["detail"] == "Invalid credentials"
        # An agent directory does not serve the portal (and vice versa).
        resp = await _login(client, carla, "carla-secret")
        assert resp.status_code == 401
    assert await env.links("env-customer-ldap") == {(f"uuid-{carla}", carla_id)}

    await env.provider(
        "cust",
        host="ldap2.example.org",
        audience="customer",
        jit=True,
        jit_defaults=JitDefaults(customer_id="idp-ldap-co"),
    )
    async with env.client(Settings(**BASE)) as client:  # type: ignore[arg-type]
        resp = await client.post(
            "/api/portal/auth/login", json={"login": dora, "password": "dora-secret"}
        )
        assert resp.status_code == 200, resp.text
    created = await env.rows(
        "SELECT email, customer_id, first_name, last_name FROM customer_user WHERE login = :l",
        l=dora,
    )
    assert created == [("dora@example.test", "idp-ldap-co", "Dora", "Kundin")]
    assert limiter.failures == [carla, carla]


async def test_require_sso_opt_in_ignored_for_ldap(
    env: Env, directory: Directory, limiter: Limiter
) -> None:
    person(directory, "idpldap.dirk", "dirk-secret")
    await seed_agent(env.s, 47213, "idpldap.dirk")  # no sso_eligible flag at all
    p = await env.provider("optin", require_sso_opt_in=True)
    assert p.require_sso_opt_in
    async with env.client(Settings(**BASE)) as client:  # type: ignore[arg-type]
        resp = await _login(client, "idpldap.dirk", "dirk-secret")
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["id"] == 47213
    assert limiter.failures == []


async def test_disabled_env_flag_ignores_stale_env_row(
    env: Env, directory: Directory, limiter: Limiter
) -> None:
    person(directory, "idpldap.anna", "anna-secret")
    await seed_agent(env.s, 47214, "idpldap.anna")
    async with env.client(_env_ldap()) as client:
        assert (await _login(client, "idpldap.anna", "anna-secret")).status_code == 200
    # TIQORA_LDAP_ENABLED switched off: the kept env row must not authenticate anyone.
    async with env.client(Settings(**BASE)) as client:  # type: ignore[arg-type]
        assert (await _login(client, "idpldap.anna", "anna-secret")).status_code == 401
