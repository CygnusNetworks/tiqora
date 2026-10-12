"""SCIM endpoint ``/scim/v2``: authentication, throttles, CRUD, sessions, errors (wave 4, Task 12).

Full requests through ``httpx.ASGITransport`` on ``create_app()`` with FakeRedis, both dialects.
Seed band 63280-63319: provider slugs ``sc63api-*``, provisioned logins ``sc63api.*@example.test``,
Znuny ``sessions`` ids ``SC63API*``. ASGITransport does not run the lifespan, so ``get_db`` and
``get_session_factory_dep`` are both overridden (the audit log writes through the latter).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import FakeRedis
from tests._scim_helpers import KEY, scim_cleanup_statements, seed_scim_provider
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db, get_session_factory_dep
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthService, SessionStore
from tiqora.identity.scim import tokens as scim_tokens
from tiqora.identity.scim.groups import patch_group
from tiqora.identity.scim.patch import PatchOp
from tiqora.identity.scim.tokens import ScimTenant, create_token, revoke_token
from tiqora.identity.store import ProviderChanges, ProviderRecord, update_provider
from tiqora.identity.types import UserKind, utc_now

pytestmark = pytest.mark.db

PREFIX = "sc63api"
BASE = "/scim/v2"
SCHEMA_USER = "urn:ietf:params:scim:schemas:core:2.0:User"
SCHEMA_GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCHEMA_PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCHEMA_ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
SCIM_JSON = {"Content-Type": "application/scim+json"}
_URLS: set[str] = set()
_MARK: dict[str, int] = {}
_EVENT_MARK: dict[str, int] = {}


def _stmts(url: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("DELETE FROM sessions WHERE session_id LIKE 'SC63API%'", {}),
        # auth_failed rows of unknown tokens have no provider id
        ("DELETE FROM tiqora_scim_event WHERE id > :mx", {"mx": _EVENT_MARK.get(url, 2**62)}),
        (
            "DELETE FROM tiqora_cache_invalidation WHERE id > :mx",
            {"mx": _MARK.get(url, 2**62)},
        ),
        *scim_cleanup_statements(slug_prefix=PREFIX, login_like=f"{PREFIX}.%"),
    )


def _run(url: str, stmts: tuple[tuple[str, dict[str, Any]], ...]) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in stmts:
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


def _prepare(url: str) -> None:
    _run(url, ())
    if url not in _MARK:
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                row = conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
                )
                _MARK[url] = int(row.scalar_one())
                row = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM tiqora_scim_event"))
                _EVENT_MARK[url] = int(row.scalar_one())
        finally:
            engine.dispose()
    _run(url, _stmts(url))


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _run(url, _stmts(url))
    _URLS.clear()


def _settings(**extra: Any) -> Settings:
    return Settings(
        environment="test",
        secret_key=KEY,
        public_base_url="https://helpdesk.example.test",
        **extra,
    )


class RedisDownOnSmembers(FakeRedis):
    """Revocation of a user's Redis sessions raises (the index lookup fails)."""

    async def smembers(self, key: str) -> set[str]:
        raise ConnectionError("redis is down")


class Env:
    def __init__(
        self,
        maker: async_sessionmaker[AsyncSession],
        db: AsyncSession,
        redis: FakeRedis,
        settings: Settings,
    ) -> None:
        self.maker = maker
        self.db = db
        self.redis = redis
        self.settings = settings
        self.app = self.build_app(settings)
        self.client = self.make_client(self.app)
        self.tokens: dict[str, str] = {}
        self.providers: dict[str, ProviderRecord] = {}

    def build_app(self, settings: Settings) -> FastAPI:
        app = create_app(settings)
        app.state.redis = self.redis

        async def _get_db() -> AsyncIterator[AsyncSession]:
            async with self.maker() as s:
                yield s

        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_session_factory_dep] = lambda: self.maker
        return app

    @staticmethod
    def make_client(app: FastAPI) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://helpdesk.example.test"
        )

    def set(self, **changes: Any) -> None:
        """Change settings of the running app (the dependency reads ``app.state.settings``)."""
        self.settings = self.settings.model_copy(update=changes)
        self.app.state.settings = self.settings

    def h(self, name: str = "a", **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[name]}", **extra}

    async def add_tenant(
        self, name: str, kind: UserKind, *, slug: str | None = None
    ) -> ProviderRecord:
        rec = await seed_scim_provider(self.db, audience=kind, slug=slug or f"{PREFIX}-{name}")
        _, raw = await create_token(
            self.db, provider=rec, user_kind=kind, name=name, expires_in_days=30, actor_id=1
        )
        await self.db.commit()
        self.providers[name] = rec
        self.tokens[name] = raw
        return rec

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.maker() as s:
            return list((await s.execute(text(sql), params)).all())

    async def events(self, name: str = "a") -> list[tuple[str, int, str | None]]:
        rows = await self.rows(
            "SELECT operation, status, detail FROM tiqora_scim_event"
            " WHERE provider_id = :p AND method <> 'ADMIN' ORDER BY id",
            p=self.providers[name].id,
        )
        return [(r[0], int(r[1]), r[2]) for r in rows]

    async def post_user(self, name: str, login: str, **extra: Any) -> dict[str, Any]:
        body = user_doc(login, **extra)
        resp = await self.client.post(f"{BASE}/Users", json=body, headers=self.h(name))
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def agent_id(self, login: str) -> int:
        rows = await self.rows("SELECT id FROM users WHERE login = :l", l=login)
        return int(rows[0][0])


def user_doc(login: str, **extra: Any) -> dict[str, Any]:
    return {
        "schemas": [SCHEMA_USER],
        "userName": login,
        "name": {"givenName": "Erika", "familyName": "Muster"},
        "emails": [{"value": login, "primary": True, "type": "work"}],
        "active": True,
        **extra,
    }


def patch_doc(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"schemas": [SCHEMA_PATCH], "Operations": list(ops)}


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _prepare(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        e = Env(maker, db, FakeRedis(), _settings())
        await e.add_tenant("a", "agent")
        await e.add_tenant("b", "agent")
        await e.add_tenant("c", "customer")
        async with e.client:
            yield e
    await engine.dispose()
    _run(url, _stmts(url))


def assert_scim_error(resp: httpx.Response, status: int, scim_type: str | None = None) -> None:
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/scim+json")
    body = resp.json()
    assert body["schemas"] == [SCHEMA_ERROR]
    assert body["status"] == str(status)
    assert body.get("scimType") == scim_type
    assert isinstance(body["detail"], str)


# --------------------------------------------------------------------------- authentication


async def test_discovery_requires_token(env: Env) -> None:
    resp = await env.client.get(f"{BASE}/ServiceProviderConfig")
    assert_scim_error(resp, 401)
    assert resp.headers["www-authenticate"] == 'Bearer realm="scim"'
    assert resp.json()["detail"] == "Authentication failed"

    ok = await env.client.get(f"{BASE}/ServiceProviderConfig", headers=env.h())
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("application/scim+json")
    cfg = ok.json()
    assert cfg["patch"]["supported"] is True and cfg["bulk"]["supported"] is False
    assert cfg["meta"]["location"] == "https://helpdesk.example.test/scim/v2/ServiceProviderConfig"


async def test_unauthorised_bodies_identical(env: Env) -> None:
    rec_a = env.providers["a"]
    # revoked
    revoked_rec, revoked = await create_token(
        env.db, provider=rec_a, user_kind="agent", name="r", expires_in_days=5, actor_id=1
    )
    await revoke_token(env.db, provider_id=rec_a.id, token_id=revoked_rec.id, actor_id=1)
    # expired
    _, expired = await create_token(
        env.db, provider=rec_a, user_kind="agent", name="e", expires_in_days=5, actor_id=1
    )
    await env.db.execute(
        text("UPDATE tiqora_scim_token SET expires_at = :t WHERE token_hint = :h"),
        {"t": utc_now().replace(year=2020), "h": expired[-4:]},
    )
    # token of a disabled provider
    rec_off = await seed_scim_provider(env.db, audience="agent", slug=f"{PREFIX}-off")
    _, off_raw = await create_token(
        env.db, provider=rec_off, user_kind="agent", name="o", expires_in_days=5, actor_id=1
    )
    await update_provider(
        env.db,
        rec_off.id,
        ProviderChanges(enabled=False),
        secret_key=KEY,
        actor_id=1,
        settings=env.settings,
    )
    await env.db.commit()

    bad_headers: list[dict[str, str]] = [
        {},
        {"Authorization": "Bearer tqscim_" + "x" * 43},
        {"Authorization": "Bearer nonsense"},
        {"Authorization": "Basic dXNlcjpwdw=="},
        {"Authorization": f"Bearer {revoked}"},
        {"Authorization": f"Bearer {expired}"},
        {"Authorization": f"Bearer {off_raw}"},
        {"Authorization": f"Bearer {env.tokens['a']} extra"},
    ]
    seen = set()
    for headers in bad_headers:
        resp = await env.client.get(f"{BASE}/Users", headers=headers)
        assert_scim_error(resp, 401)
        assert resp.headers["www-authenticate"] == 'Bearer realm="scim"'
        seen.add(resp.content)
    assert len(seen) == 1
    assert (await env.client.get(f"{BASE}/Users", headers=env.h())).status_code == 200


async def test_query_string_token_ignored(env: Env) -> None:
    resp = await env.client.get(f"{BASE}/Users", params={"access_token": env.tokens["a"]})
    assert_scim_error(resp, 401)
    resp = await env.client.get(
        f"{BASE}/Users",
        params={"access_token": env.tokens["a"]},
        headers={"Cookie": f"access_token={env.tokens['a']}"},
    )
    assert_scim_error(resp, 401)


async def test_locked_ip_still_serves_valid_token_but_not_bad_ones(env: Env) -> None:
    env.set(scim_auth_fail_max=3)
    bad = {"Authorization": "Bearer tqscim_" + "y" * 43}
    for _ in range(3):
        assert_scim_error(await env.client.get(f"{BASE}/Users", headers=bad), 401)
    # The IP is locked now: the IdP's valid token keeps working (F1) ...
    ok = await env.client.get(f"{BASE}/Users", headers=env.h())
    assert ok.status_code == 200
    # ... while a bad token is answered 429 with Retry-After, without another failure count.
    locked = await env.client.get(f"{BASE}/Users", headers=bad)
    assert_scim_error(locked, 429)
    assert int(locked.headers["retry-after"]) > 0
    assert (await env.client.get(f"{BASE}/Users")).status_code == 429


async def test_auth_failed_event_is_throttled_per_ip(env: Env) -> None:
    env.set(scim_auth_fail_max=1000)
    bad = {"Authorization": f"Bearer {env.tokens['a']}x"}  # unknown hash: no provider/token id
    for _ in range(6):
        assert_scim_error(await env.client.get(f"{BASE}/Users", headers=bad), 401)
    rows = await env.rows("SELECT operation, status, provider_id FROM tiqora_scim_event")
    mine = [r for r in rows if r[0] == "auth_failed" and r[2] is None]
    assert len(mine) >= 1
    # one row per IP and window: the six failures above wrote one row, not six
    fail_keys = [k for k in env.redis.store if k.startswith("tiqora:scim:fail:logged:")]
    assert fail_keys == ["tiqora:scim:fail:logged:127.0.0.1"]
    assert env.redis.ttls[fail_keys[0]] == env.settings.scim_auth_fail_window_seconds
    count_before = len(mine)
    for _ in range(3):
        await env.client.get(f"{BASE}/Users", headers=bad)
    rows = await env.rows("SELECT operation, status, provider_id FROM tiqora_scim_event")
    assert len([r for r in rows if r[0] == "auth_failed" and r[2] is None]) == count_before


async def test_revoked_token_audit_row_names_provider_and_token(env: Env) -> None:
    rec = env.providers["a"]
    rows = await env.rows("SELECT id FROM tiqora_scim_token WHERE provider_id = :p", p=rec.id)
    token_id = int(rows[0][0])
    await revoke_token(env.db, provider_id=rec.id, token_id=token_id, actor_id=1)
    await env.db.commit()
    assert_scim_error(await env.client.get(f"{BASE}/Users", headers=env.h()), 401)
    events = await env.rows(
        "SELECT operation, token_id, detail FROM tiqora_scim_event"
        " WHERE provider_id = :p AND operation = 'auth_failed'",
        p=rec.id,
    )
    assert [(e[0], int(e[1]), e[2]) for e in events] == [("auth_failed", token_id, "revoked")]


async def test_token_rate_limit_429(env: Env) -> None:
    env.set(scim_rate_limit_max=2)
    for _ in range(2):
        assert (await env.client.get(f"{BASE}/Users", headers=env.h())).status_code == 200
    for _ in range(3):
        limited = await env.client.get(f"{BASE}/Users", headers=env.h())
        assert_scim_error(limited, 429)
        assert int(limited.headers["retry-after"]) >= 1
    # another token is not affected, and the throttled token left exactly one event
    assert (await env.client.get(f"{BASE}/Users", headers=env.h("b"))).status_code == 200
    assert [e for e in await env.events("a") if e[0] == "rate_limited"] == [
        ("rate_limited", 429, None)
    ]


# --------------------------------------------------------------------------- users


async def test_user_crud_roundtrip(env: Env) -> None:
    login = f"{PREFIX}.crud@example.test"
    resp = await env.client.post(
        f"{BASE}/Users", json=user_doc(login, externalId="ext-1"), headers=env.h()
    )
    assert resp.status_code == 201, resp.text
    assert resp.headers["content-type"].startswith("application/scim+json")
    created = resp.json()
    assert resp.headers["location"] == created["meta"]["location"]
    assert created["meta"]["location"].startswith("https://helpdesk.example.test/scim/v2/Users/")
    uid = created["id"]
    assert created["userName"] == login and created["active"] is True

    got = await env.client.get(f"{BASE}/Users/{uid}", headers=env.h())
    assert got.status_code == 200 and got.json()["id"] == uid

    patched = await env.client.patch(
        f"{BASE}/Users/{uid}",
        json=patch_doc({"op": "Replace", "path": "name.familyName", "value": "Beispiel"}),
        headers=env.h(),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"]["familyName"] == "Beispiel"

    put_body = user_doc(login, externalId="ext-1")
    put_body["name"] = {"givenName": "Erika", "familyName": "Neu"}
    replaced = await env.client.put(f"{BASE}/Users/{uid}", json=put_body, headers=env.h())
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["name"]["familyName"] == "Neu"

    listed = await env.client.get(
        f"{BASE}/Users", params={"filter": f'userName eq "{login}"'}, headers=env.h()
    )
    assert listed.status_code == 200
    assert listed.json()["totalResults"] == 1
    assert listed.json()["Resources"][0]["id"] == uid

    deleted = await env.client.delete(f"{BASE}/Users/{uid}", headers=env.h())
    assert deleted.status_code == 204 and deleted.content == b""
    assert_scim_error(await env.client.get(f"{BASE}/Users/{uid}", headers=env.h()), 404)


async def test_patch_active_string_false_deactivates(env: Env) -> None:
    login = f"{PREFIX}.entra@example.test"
    created = await env.post_user("a", login)
    resp = await env.client.patch(
        f"{BASE}/Users/{created['id']}",
        json=patch_doc({"op": "Replace", "path": "active", "value": "False"}),
        headers=env.h(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is False
    rows = await env.rows("SELECT valid_id FROM users WHERE login = :l", l=login)
    assert int(rows[0][0]) == 2


async def _znuny_session(env: Env, session_id: str, user_id: str, utype: str) -> None:
    async with env.maker() as s:
        for key, val in (("UserID", user_id), ("UserType", utype)):
            await s.execute(
                text(
                    "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                    " VALUES (:s, :k, :v, 0)"
                ),
                {"s": session_id, "k": key, "v": val},
            )
        await s.commit()


async def _znuny_session_count(env: Env, session_id: str) -> int:
    rows = await env.rows("SELECT COUNT(*) FROM sessions WHERE session_id = :s", s=session_id)
    return int(rows[0][0])


async def test_deactivate_ends_sessions_before_response(env: Env) -> None:
    login = f"{PREFIX}.leaver@example.test"
    created = await env.post_user("a", login)
    uid = await env.agent_id(login)
    store = SessionStore(env.redis, env.settings)  # type: ignore[arg-type]
    token = await store.create(uid, login)
    await _znuny_session(env, "SC63APIA", str(uid), "User")
    assert await store.get(token) is not None

    resp = await env.client.patch(
        f"{BASE}/Users/{created['id']}",
        json=patch_doc({"op": "replace", "path": "active", "value": False}),
        headers=env.h(),
    )
    assert resp.status_code == 200, resp.text
    # the response is back: every session is already gone
    assert await store.get(token) is None
    assert await _znuny_session_count(env, "SC63APIA") == 0
    async with env.maker() as s:
        auth = AuthService(s, store, env.settings)
        assert await auth.resolve_session(token) is None
    assert [e[:2] for e in await env.events("a")] == [("create", 201), ("deactivate", 200)]


async def test_deactivate_with_redis_down_still_blocks_session(env: Env) -> None:
    login = f"{PREFIX}.redisdown@example.test"
    created = await env.post_user("a", login)
    uid = await env.agent_id(login)
    # Same Redis content, but the user-index lookup raises while the sessions are revoked.
    store = SessionStore(env.redis, env.settings)  # type: ignore[arg-type]
    token = await store.create(uid, login)
    broken = RedisDownOnSmembers()
    broken.store, broken.sets, broken.ttls = env.redis.store, env.redis.sets, env.redis.ttls
    env.app.state.redis = broken

    resp = await env.client.delete(f"{BASE}/Users/{created['id']}", headers=env.h())
    assert resp.status_code == 204, resp.text
    # Redis revocation failed (token still stored) but the invalid account is refused (valid_id 2)
    assert await store.get(token) is not None
    async with env.maker() as s:
        assert await AuthService(s, store, env.settings).resolve_session(token) is None


async def test_customer_deactivate_ends_znuny_sessions(env: Env) -> None:
    login = f"{PREFIX}.kunde@example.test"
    created = await env.post_user("c", login)
    await _znuny_session(env, "SC63APIC", login, "Customer")
    resp = await env.client.patch(
        f"{BASE}/Users/{created['id']}",
        json=patch_doc({"op": "replace", "path": "active", "value": False}),
        headers=env.h("c"),
    )
    assert resp.status_code == 200, resp.text
    assert await _znuny_session_count(env, "SC63APIC") == 0


async def test_customer_token_has_no_groups(env: Env) -> None:
    for method, path in (
        ("GET", "/Groups"),
        ("POST", "/Groups"),
        ("GET", "/Groups/abc"),
        ("PUT", "/Groups/abc"),
        ("PATCH", "/Groups/abc"),
        ("DELETE", "/Groups/abc"),
    ):
        resp = await env.client.request(
            method,
            f"{BASE}{path}",
            headers=env.h("c", **SCIM_JSON),
            content=b"{}" if method in ("POST", "PUT", "PATCH") else None,
        )
        assert_scim_error(resp, 404)
        assert resp.json()["detail"] == "Groups are not available for customer provisioning"
    types = await env.client.get(f"{BASE}/ResourceTypes", headers=env.h("c"))
    assert [t["id"] for t in types.json()["Resources"]] == ["User"]
    assert_scim_error(await env.client.get(f"{BASE}/ResourceTypes/Group", headers=env.h("c")), 404)
    agent_types = await env.client.get(f"{BASE}/ResourceTypes", headers=env.h("a"))
    assert [t["id"] for t in agent_types.json()["Resources"]] == ["User", "Group"]
    schemas = await env.client.get(f"{BASE}/Schemas", headers=env.h("c"))
    names = {d["name"] for d in schemas.json()["Resources"]}
    assert "User" in names and "Group" not in names


async def test_discovery_documents(env: Env) -> None:
    schemas = await env.client.get(f"{BASE}/Schemas", headers=env.h())
    ids = [d["id"] for d in schemas.json()["Resources"]]
    assert SCHEMA_USER in ids and SCHEMA_GROUP in ids
    enterprise = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
    single = await env.client.get(f"{BASE}/Schemas/{enterprise}", headers=env.h())
    assert single.status_code == 200 and single.json()["id"] == enterprise
    one = await env.client.get(f"{BASE}/ResourceTypes/user", headers=env.h())
    assert one.status_code == 200 and one.json()["id"] == "User"
    assert_scim_error(await env.client.get(f"{BASE}/Schemas/urn:nope", headers=env.h()), 404)


async def test_cross_tenant_ids_are_404(env: Env) -> None:
    created = await env.post_user("a", f"{PREFIX}.mine@example.test")
    uid = created["id"]
    group = await env.client.post(
        f"{BASE}/Groups",
        json={"schemas": [SCHEMA_GROUP], "displayName": "Support", "members": []},
        headers=env.h("a"),
    )
    assert group.status_code == 201, group.text
    gid = group.json()["id"]

    for other in ("b", "c"):
        h = env.h(other, **SCIM_JSON)
        assert_scim_error(await env.client.get(f"{BASE}/Users/{uid}", headers=h), 404)
        assert_scim_error(
            await env.client.put(f"{BASE}/Users/{uid}", json=user_doc("x@example.test"), headers=h),
            404,
        )
        assert_scim_error(
            await env.client.patch(
                f"{BASE}/Users/{uid}",
                json=patch_doc({"op": "replace", "path": "active", "value": False}),
                headers=h,
            ),
            404,
        )
        assert_scim_error(await env.client.delete(f"{BASE}/Users/{uid}", headers=h), 404)
    h_b = env.h("b", **SCIM_JSON)
    assert_scim_error(await env.client.get(f"{BASE}/Groups/{gid}", headers=h_b), 404)
    assert_scim_error(await env.client.delete(f"{BASE}/Groups/{gid}", headers=h_b), 404)
    # nothing leaked into the listings and nothing was changed
    assert (await env.client.get(f"{BASE}/Users", headers=env.h("b"))).json()["totalResults"] == 0
    still = await env.client.get(f"{BASE}/Users/{uid}", headers=env.h("a"))
    assert still.status_code == 200 and still.json()["active"] is True
    assert (await env.client.get(f"{BASE}/Groups/{gid}", headers=env.h("a"))).status_code == 200


# --------------------------------------------------------------------------- groups


async def test_group_roundtrip_members_and_excluded_attributes(env: Env) -> None:
    u1 = await env.post_user("a", f"{PREFIX}.g1@example.test")
    u2 = await env.post_user("a", f"{PREFIX}.g2@example.test")
    resp = await env.client.post(
        f"{BASE}/Groups",
        json={
            "schemas": [SCHEMA_GROUP],
            "displayName": "Support",
            "members": [{"value": u1["id"]}, {"value": "00000000-0000-0000-0000-000000000000"}],
        },
        headers=env.h(),
    )
    assert resp.status_code == 201, resp.text
    group = resp.json()
    assert resp.headers["location"] == group["meta"]["location"]
    assert [m["value"] for m in group["members"]] == [u1["id"]]
    gid = group["id"]

    plain = await env.client.get(f"{BASE}/Groups/{gid}", headers=env.h())
    assert [m["value"] for m in plain.json()["members"]] == [u1["id"]]
    slim = await env.client.get(
        f"{BASE}/Groups/{gid}", params={"excludedAttributes": "members"}, headers=env.h()
    )
    assert "members" not in slim.json() and slim.json()["displayName"] == "Support"
    only = await env.client.get(
        f"{BASE}/Groups", params={"attributes": "displayName"}, headers=env.h()
    )
    assert only.json()["totalResults"] == 1
    assert "members" not in only.json()["Resources"][0]

    patched = await env.client.patch(
        f"{BASE}/Groups/{gid}",
        json=patch_doc({"op": "add", "path": "members", "value": [{"value": u2["id"]}]}),
        headers=env.h(),
    )
    assert patched.status_code == 200, patched.text
    assert {m["value"] for m in patched.json()["members"]} == {u1["id"], u2["id"]}
    replaced = await env.client.put(
        f"{BASE}/Groups/{gid}",
        json={"schemas": [SCHEMA_GROUP], "displayName": "Support 2", "members": []},
        headers=env.h(),
    )
    assert replaced.status_code == 200 and replaced.json().get("members", []) == []
    assert (await env.client.delete(f"{BASE}/Groups/{gid}", headers=env.h())).status_code == 204
    assert_scim_error(await env.client.get(f"{BASE}/Groups/{gid}", headers=env.h()), 404)

    events = await env.events("a")
    ops = [e[0] for e in events]
    assert ops.count("group_create") == 1 and ops.count("group_delete") == 1
    assert ("group_create", 201, "ignored_members=1") in events


async def test_group_put_sees_commit_made_after_authentication(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-TX: the request session ends the auth read's transaction before any group write.

    A concurrent push commits a new member between authentication and the write. On MariaDB
    (REPEATABLE READ) the snapshot of the authentication read would hide that row and the PUT
    would leave the stranger in the group.
    """
    u1 = await env.post_user("a", f"{PREFIX}.rtx1@example.test")
    u2 = await env.post_user("a", f"{PREFIX}.rtx2@example.test")
    created = await env.client.post(
        f"{BASE}/Groups",
        json={"schemas": [SCHEMA_GROUP], "displayName": "Push", "members": [{"value": u1["id"]}]},
        headers=env.h(),
    )
    gid = created.json()["id"]
    # a first authenticated call stamps last_used_at, so the next authentication does not commit
    assert (await env.client.get(f"{BASE}/Users", headers=env.h())).status_code == 200

    from tiqora.api.scim import auth as scim_auth

    real = scim_tokens.authenticate_token
    state = {"fired": False}

    async def racing(session: AsyncSession, raw: str, *, settings: Settings) -> Any:
        outcome = await real(session, raw, settings=settings)
        if outcome.tenant is not None and not state["fired"]:
            state["fired"] = True
            async with env.maker() as other:
                tenant = ScimTenant(provider=outcome.tenant.provider, user_kind="agent", token_id=1)
                await patch_group(
                    other,
                    tenant,
                    gid,
                    [PatchOp("add", "members", [{"value": u2["id"]}])],
                    base="https://helpdesk.example.test/scim/v2",
                    max_members=5000,
                )
                await other.commit()
        return outcome

    monkeypatch.setattr(scim_auth, "authenticate_token", racing)
    resp = await env.client.put(
        f"{BASE}/Groups/{gid}",
        json={"schemas": [SCHEMA_GROUP], "displayName": "Push", "members": [{"value": u1["id"]}]},
        headers=env.h(),
    )
    assert state["fired"]
    assert resp.status_code == 200, resp.text
    assert [m["value"] for m in resp.json()["members"]] == [u1["id"]]
    stored = await env.client.get(f"{BASE}/Groups/{gid}", headers=env.h())
    assert [m["value"] for m in stored.json()["members"]] == [u1["id"]]


# --------------------------------------------------------------------------- errors


async def test_errors_are_scim_shaped(env: Env) -> None:
    h = env.h()
    # 415: wrong content type
    resp = await env.client.post(
        f"{BASE}/Users", content=b"{}", headers={**h, "Content-Type": "text/plain"}
    )
    assert_scim_error(resp, 415)
    # 400 invalidSyntax: not JSON, and not an object
    resp = await env.client.post(f"{BASE}/Users", content=b"{nope", headers={**h, **SCIM_JSON})
    assert_scim_error(resp, 400, "invalidSyntax")
    resp = await env.client.post(f"{BASE}/Users", content=b"[1]", headers={**h, **SCIM_JSON})
    assert_scim_error(resp, 400, "invalidSyntax")
    # 413: over scim_max_body_bytes
    env.set(scim_max_body_bytes=200)
    big = user_doc(f"{PREFIX}.big@example.test", externalId="x" * 400)
    assert_scim_error(await env.client.post(f"{BASE}/Users", json=big, headers=h), 413)
    env.set(scim_max_body_bytes=2097152)
    # 400: bad filter and bad paging value
    assert_scim_error(
        await env.client.get(f"{BASE}/Users", params={"filter": "userName co x"}, headers=h),
        400,
        "invalidFilter",
    )
    assert_scim_error(
        await env.client.get(f"{BASE}/Users", params={"count": "abc"}, headers=h),
        400,
        "invalidValue",
    )
    # unknown path 404 (also below a known resource), wrong method 405 with Allow
    assert_scim_error(await env.client.get(f"{BASE}/Foo", headers=h), 404)
    assert_scim_error(await env.client.get(f"{BASE}/Users/a/b", headers=h), 404)
    assert_scim_error(await env.client.get(f"{BASE}/", headers=h), 404)
    post_cfg = await env.client.post(f"{BASE}/ServiceProviderConfig", json={}, headers=h)
    assert_scim_error(post_cfg, 405)
    assert post_cfg.headers["allow"] == "GET"
    put_users = await env.client.put(f"{BASE}/Users", json={}, headers=h)
    assert_scim_error(put_users, 405)
    assert put_users.headers["allow"] == "GET, POST"
    # unauthenticated probing of unknown paths is a 401, not a 404
    assert_scim_error(await env.client.get(f"{BASE}/Foo"), 401)
    # path ids that cannot be ours are plain 404s (no database error)
    assert_scim_error(await env.client.get(f"{BASE}/Users/%00", headers=h), 404)
    assert_scim_error(await env.client.get(f"{BASE}/Users/{'z' * 200}", headers=h), 404)


async def test_lone_surrogate_is_400_not_500(env: Env) -> None:
    body = (
        b'{"schemas":["' + SCHEMA_USER.encode() + b'"],"userName":"sc63api.\\udc80@example.test"}'
    )
    resp = await env.client.post(f"{BASE}/Users", content=body, headers={**env.h(), **SCIM_JSON})
    assert_scim_error(resp, 400, "invalidValue")


async def test_unexpected_error_is_scim_500_and_rolled_back(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.api.scim import router as scim_router_module

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("secret internals")

    monkeypatch.setattr(scim_router_module, "create_user", boom)
    resp = await env.client.post(
        f"{BASE}/Users", json=user_doc(f"{PREFIX}.boom@example.test"), headers=env.h()
    )
    assert_scim_error(resp, 500)
    assert "secret internals" not in resp.text
    assert ("rejected", 500, "status_500") in await env.events("a")


async def test_bulk_and_me_not_implemented(env: Env) -> None:
    h = env.h(**SCIM_JSON)
    for method, path in (
        ("POST", "/Bulk"),
        ("GET", "/Me"),
        ("PUT", "/Me"),
        ("POST", "/.search"),
        ("POST", "/Users/.search"),
        ("POST", "/Groups/.search"),
    ):
        resp = await env.client.request(
            method, f"{BASE}{path}", headers=h, content=b"{}" if method != "GET" else None
        )
        assert_scim_error(resp, 501)


async def test_events_written_for_writes_and_failures(env: Env) -> None:
    login = f"{PREFIX}.events@example.test"
    created = await env.post_user("a", login)
    dup = await env.client.post(f"{BASE}/Users", json=user_doc(login), headers=env.h())
    assert_scim_error(dup, 409, "uniqueness")
    assert (
        await env.client.get(f"{BASE}/Users/{created['id']}", headers=env.h())
    ).status_code == 200
    assert_scim_error(await env.client.get(f"{BASE}/Users/nope", headers=env.h()), 404)
    missing = await env.client.delete(
        f"{BASE}/Users/{'0' * 8}-0000-0000-0000-000000000000", headers=env.h()
    )
    assert_scim_error(missing, 404)
    bad = await env.client.post(f"{BASE}/Users", content=b"{nope", headers={**env.h(), **SCIM_JSON})
    assert_scim_error(bad, 400, "invalidSyntax")

    events = await env.events("a")
    assert events == [
        ("create", 201, None),
        ("conflict", 409, "duplicate_user_name"),
        ("rejected", 404, "status_404"),
        ("rejected", 400, "invalidSyntax"),
    ]
    rows = await env.rows(
        "SELECT method, resource, scim_id, user_kind, user_id FROM tiqora_scim_event"
        " WHERE provider_id = :p AND method <> 'ADMIN' ORDER BY id",
        p=env.providers["a"].id,
    )
    assert rows[0][0] == "POST" and rows[0][1] == "User"
    assert rows[0][2] == created["id"] and rows[0][3] == "agent"
    assert rows[0][4] == await env.agent_id(login)


# --------------------------------------------------------------------------- mounting


async def test_spa_fallback_does_not_shadow_scim(env: Env, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>spa</title>")
    spa_settings = _settings(serve_frontend=True, frontend_dist_dir=str(dist))
    app = env.build_app(spa_settings)
    async with env.make_client(app) as client:
        spa = await client.get("/helpdesk")
        assert spa.status_code == 200 and "<title>spa</title>" in spa.text  # fallback is mounted
        unauth = await client.get(f"{BASE}/Users")
        assert_scim_error(unauth, 401)
        users = await client.get(f"{BASE}/Users", headers=env.h())
        assert users.status_code == 200
        assert users.headers["content-type"].startswith("application/scim+json")
        assert users.json()["totalResults"] == 0
        assert_scim_error(await client.get(f"{BASE}/Nothing", headers=env.h()), 404)
