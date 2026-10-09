"""Role and group sync from IdP claims with managed assignments (wave 1, Task 18).

Seed band 47400-47449 and 47480-47499 (47450-47479 belongs to Task 11): roles 47400-47403
(+ 47405 invalid), groups 47400-47401, agents 47410-47449, customer logins ``idp.*@example.org``.
Provider slugs use the ``idprole-`` prefix. Rules match the attribute ``groups``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

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
    seed_customer,
)
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import SessionStore
from tiqora.domain.user_provisioning import ProvisioningError
from tiqora.identity import provisioning
from tiqora.identity.config_models import AttributeMap, GroupGrant, RoleRule
from tiqora.identity.links import upsert_link
from tiqora.identity.login import ExternalIdentity, apply_login, plan_login
from tiqora.identity.mapping import AssignmentDiff
from tiqora.identity.store import ProviderChanges, ProviderRecord, update_provider

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-role-sync"
BAND = (47400, 47449)
HIGH = (47480, 47499)
SLUG = "idprole-"
R1, R2, R3, R_INVALID = 47400, 47401, 47402, 47405
G1, G2 = 47400, 47401
ADMIN = 47411
SETTINGS = Settings(
    environment="test", secret_key=KEY, public_base_url="https://helpdesk.example.org"
)
MAP = AttributeMap(login=["preferred_username"], email=["email"])
BASE = "/api/v1/admin/identity-providers"
_URLS: set[str] = set()
_MARK: dict[str, int] = {}


def _stmts(url: str) -> tuple[tuple[str, dict[str, object]], ...]:
    # Two blocks only: 47450-47479 belongs to Task 11 and must stay untouched.
    out: list[tuple[str, dict[str, object]]] = []
    for lo, hi in (BAND, HIGH):
        rng = {"lo": lo, "hi": hi}
        out += [
            ("DELETE FROM role_user WHERE user_id BETWEEN :lo AND :hi", rng),
            ("DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi", rng),
            ("DELETE FROM role_user WHERE role_id BETWEEN :lo AND :hi", rng),
            ("DELETE FROM group_user WHERE group_id BETWEEN :lo AND :hi", rng),
            ("DELETE FROM roles WHERE id BETWEEN :lo AND :hi", rng),
            ("DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi", rng),
        ]
    out.append(
        ("DELETE FROM tiqora_cache_invalidation WHERE id > :mx", {"mx": _MARK.get(url, 2**62)})
    )
    return (*out, *cleanup_statements(user_ids=BAND, slug_prefix=SLUG))


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


@pytest.fixture(autouse=True)
def _loggers_enabled() -> None:
    """Alembic's ``fileConfig`` (migration tests earlier in a full run) disables every logger that
    already exists, which blanks ``caplog`` for these modules' log assertions."""
    for name in ("tiqora.identity.login", "tiqora.identity.provisioning"):
        logging.getLogger(name).disabled = False


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


class Env:
    def __init__(self, session: AsyncSession, client: httpx.AsyncClient, token: str) -> None:
        self.s = session
        self.client = client
        self.token = token

    async def rows(self, sql: str, **params: object) -> list[tuple[Any, ...]]:
        await self.s.commit()  # fresh snapshot: the admin API writes through another session
        return [tuple(r) for r in (await self.s.execute(text(sql), params)).all()]

    async def roles(self, user_id: int) -> set[int]:
        rows = await self.rows("SELECT role_id FROM role_user WHERE user_id = :u", u=user_id)
        return {int(r[0]) for r in rows}

    async def keys(self, user_id: int, group_id: int) -> set[str]:
        rows = await self.rows(
            "SELECT permission_key FROM group_user WHERE user_id = :u AND group_id = :g",
            u=user_id,
            g=group_id,
        )
        return {str(r[0]) for r in rows}

    async def managed(
        self, user_id: int, provider_id: int | None = None
    ) -> set[tuple[str, int, str]]:
        sql = (
            "SELECT kind, target_id, permission_key FROM tiqora_identity_managed_assignment"
            " WHERE user_id = :u"
        )
        params: dict[str, object] = {"u": user_id}
        if provider_id is not None:
            sql += " AND provider_id = :p"
            params["p"] = provider_id
        return {(str(k), int(t), str(p)) for k, t, p in await self.rows(sql, **params)}

    async def grant_role(self, user_id: int, role_id: int) -> None:
        await self.s.execute(
            text(
                "INSERT INTO role_user (user_id, role_id, create_time, create_by, change_time,"
                " change_by) VALUES (:u, :r, :t, 1, :t, 1)"
            ),
            {"u": user_id, "r": role_id, "t": NOW},
        )
        await self.s.commit()

    async def grant_keys(self, user_id: int, group_id: int, *keys: str) -> None:
        for key in keys:
            await self.s.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": user_id, "g": group_id, "k": key, "t": NOW},
            )
        await self.s.commit()

    async def provider(
        self,
        slug: str,
        rules: list[RoleRule],
        *,
        mode: str = "managed",
        audience: str = "agent",
    ) -> ProviderRecord:
        p = await make_provider(
            self.s,
            slug=f"{SLUG}{slug}",
            settings=SETTINGS,
            attribute_map=MAP,
            audience=audience,  # type: ignore[arg-type]
        )
        p = await update_provider(
            self.s,
            p.id,
            ProviderChanges(role_rules=rules, sync_mode=mode),  # type: ignore[arg-type]
            secret_key=KEY,
            actor_id=1,
        )
        await self.s.commit()
        return p

    async def login(
        self,
        p: ProviderRecord,
        login: str,
        *groups: str,
        kind: str = "agent",
        claim: str = "groups",
        empty_claim: bool = False,
    ) -> tuple[int, str]:
        await self.s.commit()  # fresh snapshot (MariaDB repeatable read)
        attrs: dict[str, tuple[str, ...]] = {"preferred_username": (login,)}
        if groups or empty_claim:
            attrs[claim] = tuple(groups)
        ident = ExternalIdentity(provider_id=p.id, subject=f"sub-{login}", attributes=attrs)
        plan = await plan_login(self.s, p, ident, user_kind=kind, settings=SETTINGS)  # type: ignore[arg-type]
        return await apply_login(self.s, p, ident, plan, settings=SETTINGS)

    async def inval_count(self, cache_type: str, since: int) -> int:
        rows = await self.rows(
            "SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE cache_type = :c AND id > :i",
            c=cache_type,
            i=since,
        )
        return int(rows[0][0])

    async def inval_mark(self) -> int:
        rows = await self.rows("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
        return int(rows[0][0])


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(SETTINGS)
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
        for rid, valid in ((R1, 1), (R2, 1), (R3, 1), (R_INVALID, 2)):
            await session.execute(
                text(
                    "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
                    " change_by) VALUES (:r, :n, :v, :t, 1, :t, 1)"
                ),
                {"r": rid, "n": f"idp-role-{rid}", "v": valid, "t": NOW},
            )
        for gid in (G1, G2):
            await session.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:g, :n, 1, :t, 1, :t, 1)"
                ),
                {"g": gid, "n": f"idp-group-{gid}", "t": NOW},
            )
        await session.commit()
        for uid in range(47410, 47440):
            await seed_agent(session, uid, f"idp.sync.{uid}")
        await session.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) SELECT :u, id, 'rw', :t, 1, :t, 1"
                " FROM permission_groups WHERE name = 'admin'"
            ),
            {"u": ADMIN, "t": NOW},
        )
        await session.commit()
        token = await SessionStore(redis, SETTINGS).create(ADMIN, f"idp.sync.{ADMIN}")  # type: ignore[arg-type]
        client.cookies.set("tiqora_session", token)
        yield Env(session, client, token)
    await engine.dispose()
    _clean(url)


def _rule(
    *,
    roles: list[int] | None = None,
    groups: list[GroupGrant] | None = None,
    value: str = "support",
) -> RoleRule:
    return RoleRule(attribute="groups", value=value, role_ids=roles or [], groups=groups or [])


def _uid(n: int) -> tuple[int, str]:
    return n, f"idp.sync.{n}"


async def test_rule_grants_role_and_records_managed(env: Env) -> None:
    uid, login = _uid(47412)
    p = await env.provider("grant", [_rule(roles=[R1])])
    assert await env.login(p, login, "support") == (uid, login)
    assert await env.roles(uid) == {R1}
    assert await env.managed(uid) == {("role", R1, "")}
    created = await env.rows("SELECT create_by FROM role_user WHERE user_id = :u", u=uid)
    assert created == [(1,)]  # system user is the actor


async def test_manual_role_survives(env: Env) -> None:
    uid, login = _uid(47413)
    await env.grant_role(uid, R3)
    p = await env.provider("manual", [_rule(roles=[R3, R1])])
    await env.login(p, login, "support")
    assert await env.roles(uid) == {R1, R3}
    assert await env.managed(uid) == {("role", R1, "")}  # R3 was manual: no mark
    await env.login(p, login, "other")
    assert await env.roles(uid) == {R3}
    assert await env.managed(uid) == set()


async def test_managed_role_removed_when_group_gone(env: Env) -> None:
    uid, login = _uid(47414)
    p = await env.provider("gone", [_rule(roles=[R1])])
    await env.login(p, login, "support")
    mark = await env.inval_mark()
    await env.login(p, login, empty_claim=True)  # `groups: []`
    assert await env.roles(uid) == set()
    assert await env.managed(uid) == set()
    for cache_type in ("DBRoleUserGet", "GroupPermissionUserGet", "GroupPermissionGroupGet"):
        assert await env.inval_count(cache_type, mark) >= 1, cache_type


async def test_hand_removed_managed_role_not_resurrected_as_manual(env: Env) -> None:
    uid, login = _uid(47415)
    p = await env.provider("hand", [_rule(roles=[R1])])
    await env.login(p, login, "support")
    assert await env.managed(uid) == {("role", R1, "")}
    resp = await env.client.delete(f"/api/v1/admin/users/{uid}/roles/{R1}")
    assert resp.status_code == 204, resp.text
    assert await env.roles(uid) == set()
    assert await env.managed(uid) == set()
    await env.login(p, login, "support")  # Znuny parity: sync re-applies at login ...
    assert await env.roles(uid) == {R1}
    assert await env.managed(uid) == {("role", R1, "")}  # ... as managed again
    # Re-assigned by hand while managed, then removed by the rule -> stays only if the mark
    # was dropped: revoke + manual re-grant makes it manual.
    assert (await env.client.delete(f"/api/v1/admin/users/{uid}/roles/{R1}")).status_code == 204
    assert (
        await env.client.put(f"/api/v1/admin/users/{uid}/roles", json={"role_id": R1})
    ).status_code == 204
    await env.login(p, login, "other")
    assert await env.roles(uid) == {R1}  # manual from the hand-removal on


async def test_hand_removed_group_key_loses_mark(env: Env) -> None:
    uid, login = _uid(47416)
    p = await env.provider(
        "handg", [_rule(groups=[GroupGrant(group_id=G1, permissions=["ro", "note"])])]
    )
    await env.login(p, login, "support")
    assert await env.keys(uid, G1) == {"ro", "note"}
    resp = await env.client.delete(f"/api/v1/admin/users/{uid}/groups/{G1}/note")
    assert resp.status_code == 204, resp.text
    assert await env.managed(uid) == {("group", G1, "ro")}


async def test_group_permissions_rw_wins(env: Env) -> None:
    uid, login = _uid(47417)
    p = await env.provider(
        "rw", [_rule(groups=[GroupGrant(group_id=G1, permissions=["ro", "rw"])])]
    )
    await env.login(p, login, "support")
    assert await env.keys(uid, G1) == {"rw"}
    assert await env.managed(uid) == {("group", G1, "rw")}
    await env.login(p, login, "other")
    assert await env.keys(uid, G1) == set()
    assert await env.managed(uid) == set()


async def test_managed_mode_keeps_manual_group_keys(env: Env) -> None:
    uid, login = _uid(47418)
    await env.grant_keys(uid, G1, "note")  # manual
    await env.grant_keys(uid, G2, "rw")  # manual rw must not be downgraded
    p = await env.provider(
        "keep",
        [
            _rule(
                groups=[
                    GroupGrant(group_id=G1, permissions=["ro"]),
                    GroupGrant(group_id=G2, permissions=["ro"]),
                ]
            )
        ],
    )
    await env.login(p, login, "support")
    assert await env.keys(uid, G1) == {"note", "ro"}
    assert await env.keys(uid, G2) == {"rw"}
    assert await env.managed(uid) == {("group", G1, "ro")}
    await env.login(p, login, "other")
    assert await env.keys(uid, G1) == {"note"}  # only the managed key goes
    assert await env.keys(uid, G2) == {"rw"}
    assert await env.managed(uid) == set()


async def test_authoritative_mode_replaces(env: Env) -> None:
    uid, login = _uid(47419)
    await env.grant_role(uid, R3)  # manual role is replaced in authoritative mode
    await env.grant_keys(uid, G2, "rw")  # no group rules -> untouched
    p = await env.provider("auth", [_rule(roles=[R1])], mode="authoritative")
    await env.login(p, login, "support")
    assert await env.roles(uid) == {R1}
    assert await env.keys(uid, G2) == {"rw"}
    assert await env.managed(uid) == {("role", R1, "")}


async def test_authoritative_group_keys_replaced_in_one_step(env: Env) -> None:
    uid, login = _uid(47420)
    await env.grant_keys(uid, G1, "note", "owner")
    await env.grant_keys(uid, G2, "ro")
    p = await env.provider(
        "authg",
        [_rule(groups=[GroupGrant(group_id=G1, permissions=["ro", "create"])])],
        mode="authoritative",
    )
    await env.login(p, login, "support")
    assert await env.keys(uid, G1) == {"ro", "create"}
    assert await env.keys(uid, G2) == set()  # authoritative: everything not desired goes
    assert await env.managed(uid) == {("group", G1, "ro"), ("group", G1, "create")}
    # A desired rw collapses the whole set, also in authoritative mode.
    p2 = await env.provider(
        "authrw",
        [_rule(groups=[GroupGrant(group_id=G1, permissions=["rw", "ro"])])],
        mode="authoritative",
    )
    await env.login(p2, login, "support")
    assert await env.keys(uid, G1) == {"rw"}


async def test_invalid_role_in_rule_skipped(env: Env) -> None:
    uid, login = _uid(47421)
    p = await env.provider("invalid", [_rule(roles=[R_INVALID, R1])])
    await env.login(p, login, "support")
    assert await env.roles(uid) == {R1}
    assert await env.managed(uid) == {("role", R1, "")}


async def test_root_user_never_synced(env: Env) -> None:
    before = await env.rows("SELECT role_id FROM role_user WHERE user_id = 1 ORDER BY role_id")
    p = await env.provider(
        "root", [_rule(roles=[R1], groups=[GroupGrant(group_id=G1)])], mode="authoritative"
    )
    await upsert_link(
        env.s,
        provider_id=p.id,
        user_kind="agent",
        subject="sub-root",
        user_id=1,
        user_login="root@localhost",
    )
    await env.s.commit()
    ident = ExternalIdentity(
        provider_id=p.id, subject="sub-root", attributes={"groups": ("support",)}
    )
    plan = await plan_login(env.s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.decision == "login" and plan.user_id == 1
    assert plan.assignments.empty
    await apply_login(env.s, p, ident, plan, settings=SETTINGS)
    after = await env.rows("SELECT role_id FROM role_user WHERE user_id = 1 ORDER BY role_id")
    assert after == before
    assert await env.managed(1) == set()
    assert await env.keys(1, G1) == set()


async def test_two_providers_do_not_steal_management(env: Env) -> None:
    uid, login = _uid(47422)
    a = await env.provider("pa", [_rule(roles=[R1])])
    b = await env.provider("pb", [_rule(roles=[R1])])
    await env.login(a, login, "support")
    await env.login(b, login, "support")
    assert await env.managed(uid, a.id) == {("role", R1, "")}
    assert await env.managed(uid, b.id) == set()  # B recorded nothing
    await env.login(a, login, "other")  # A's removal removes R (documented) ...
    assert await env.roles(uid) == set()
    await env.login(b, login, "support")  # ... B re-adds it on its next login
    assert await env.roles(uid) == {R1}
    assert await env.managed(uid, b.id) == {("role", R1, "")}


async def test_provider_without_rules_changes_nothing(env: Env) -> None:
    uid, login = _uid(47423)
    await env.grant_role(uid, R3)
    p = await env.provider("norules", [], mode="authoritative")
    await env.login(p, login, "support")
    assert await env.roles(uid) == {R3}


async def test_customer_login_is_not_synced(env: Env) -> None:
    cid = await seed_customer(env.s, "idp.role.kunde@example.org", email="kunde@example.test")
    p = await env.provider("cust", [_rule(roles=[R1])], audience="customer")
    attrs = {"preferred_username": ("idp.role.kunde@example.org",), "groups": ("support",)}
    ident = ExternalIdentity(provider_id=p.id, subject="sub-cust", attributes=attrs)
    plan = await plan_login(env.s, p, ident, user_kind="customer", settings=SETTINGS)
    assert plan.decision == "login" and plan.user_id == cid
    assert plan.assignments.empty
    await apply_login(env.s, p, ident, plan, settings=SETTINGS)
    assert await env.managed(cid, p.id) == set()
    # Customer ids share the number space of agent ids: nothing may be written for either.
    assert await env.rows("SELECT COUNT(*) FROM role_user WHERE role_id = :r", r=R1) == [(0,)]


async def test_plan_reports_assignments_without_writing(env: Env) -> None:
    uid, login = _uid(47424)
    p = await env.provider("plan", [_rule(roles=[R1, R_INVALID])])
    ident = ExternalIdentity(
        provider_id=p.id,
        subject="sub-plan",
        attributes={"preferred_username": (login,), "groups": ("support",)},
    )
    plan = await plan_login(env.s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.assignments.add_roles == {R1}
    assert plan.assignments.record_managed_roles == {R1}
    assert await env.roles(uid) == set()


async def test_admin_api_lists_managed_assignments(env: Env) -> None:
    uid, login = _uid(47425)
    p = await env.provider(
        "list", [_rule(roles=[R1], groups=[GroupGrant(group_id=G1, permissions=["ro"])])]
    )
    await env.login(p, login, "support")
    resp = await env.client.get(f"{BASE}/{p.id}/managed-assignments")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {
        (r["kind"], r["target_id"], r["target_name"], r["permission_key"], r["login"]) for r in body
    } == {
        ("role", R1, f"idp-role-{R1}", "", login),
        ("group", G1, f"idp-group-{G1}", "ro", login),
    }
    assert all(r["user_id"] == uid for r in body)


# ------------------------------------------------------------------ T17 residuals


async def test_sync_existing_retries_without_email_after_provisioning_error(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid, login = _uid(47426)
    p = await env.provider("race", [])
    real = provisioning.sync_profile
    calls: list[set[str]] = []

    async def racing(session: AsyncSession, *, user_kind: str, user_id: int, changes: Any) -> None:
        calls.append(set(changes))
        if "email" in changes:
            # A partial ORM write happened before the refusal: it must not survive.
            await session.execute(
                text("UPDATE users SET last_name = 'Partial' WHERE id = :u"), {"u": user_id}
            )
            raise ProvisioningError("email_in_use", "taken meanwhile")
        await real(session, user_kind=user_kind, user_id=user_id, changes=changes)  # type: ignore[arg-type]

    monkeypatch.setattr(provisioning, "sync_profile", racing)
    attrs = {"preferred_username": (login,), "email": ("fresh@example.test",)}
    ident = ExternalIdentity(
        provider_id=p.id, subject="sub-race", attributes=attrs, email_verified=True
    )
    plan = await plan_login(env.s, p, ident, user_kind="agent", settings=SETTINGS)
    assert "email" in plan.profile_changes
    assert await apply_login(env.s, p, ident, plan, settings=SETTINGS) == (uid, login)
    assert len(calls) == 2 and "email" not in calls[1]
    assert await env.rows("SELECT last_name FROM users WHERE id = :u", u=uid) == [("Test",)]
    assert (
        await env.rows(
            "SELECT preferences_value FROM user_preferences"
            " WHERE user_id = :u AND preferences_key = 'UserEmail'",
            u=uid,
        )
        == []
    )


async def test_managed_rw_keeps_manual_ro_as_separate_row(env: Env) -> None:
    uid, login = _uid(47427)
    await env.grant_keys(uid, G1, "ro")  # manual
    p = await env.provider("rwro", [_rule(groups=[GroupGrant(group_id=G1, permissions=["rw"])])])
    await env.login(p, login, "support")
    assert await env.keys(uid, G1) == {"rw", "ro"}  # side by side, ro not absorbed
    assert await env.managed(uid) == {("group", G1, "rw")}
    await env.login(p, login, "other")  # rule stops: only the managed rw goes
    assert await env.keys(uid, G1) == {"ro"}
    assert await env.managed(uid) == set()


async def test_authoritative_leaves_invalid_roles_alone(env: Env) -> None:
    uid, login = _uid(47428)
    await env.grant_role(uid, R_INVALID)
    await env.grant_role(uid, R3)
    p = await env.provider("authvalid", [_rule(roles=[R1])], mode="authoritative")
    await env.login(p, login, "support")
    assert await env.roles(uid) == {R1, R_INVALID}  # valid manual R3 replaced, invalid kept


async def test_absent_claim_skips_sync_but_empty_claim_strips(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    uid, login = _uid(47429)
    await env.grant_role(uid, R3)
    p = await env.provider("absent", [_rule(roles=[R1])], mode="authoritative")
    await env.login(p, login, "support")
    assert await env.roles(uid) == {R1}
    await env.grant_role(uid, R3)
    with caplog.at_level("WARNING", logger="tiqora.identity.login"):
        await env.login(p, login, claim="memberOf", empty_claim=True)  # no `groups` claim
    assert await env.roles(uid) == {R1, R3}  # nothing stripped
    assert any("not released" in r.getMessage() for r in caplog.records)
    await env.login(p, login, empty_claim=True)  # `groups: []` -> really no groups
    assert await env.roles(uid) == set()


async def test_sync_logs_grants_and_revokes(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    uid, login = _uid(47430)
    p = await env.provider("log", [_rule(roles=[R1])])
    with caplog.at_level("INFO", logger="tiqora.identity.provisioning"):
        await env.login(p, login, "support")
        await env.login(p, login, "other")
    msgs = [r.getMessage() for r in caplog.records]
    assert any("sync role grant" in m and f"role {R1}" in m and f"{SLUG}log" in m for m in msgs)
    assert any("sync role revoke" in m and f"role {R1}" in m for m in msgs)


async def test_concurrent_duplicate_managed_mark_is_tolerated(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    uid, _ = _uid(47431)
    p = await env.provider("dup", [_rule(roles=[R1])])
    real_marks = provisioning._managed_marks

    async def marks_then_concurrent_insert(
        session: AsyncSession, provider_id: int, user_id: int
    ) -> tuple[frozenset[int], frozenset[tuple[int, str]]]:
        seen = await real_marks(session, provider_id, user_id)
        # Another login inserts the mark AFTER our read, BEFORE our insert.
        await session.execute(
            text(
                "INSERT INTO tiqora_identity_managed_assignment (provider_id, user_id, kind,"
                " target_id, permission_key, created) VALUES (:p, :u, 'role', :r, '', :t)"
            ),
            {"p": provider_id, "u": user_id, "r": R1, "t": NOW},
        )
        return seen

    monkeypatch.setattr(provisioning, "_managed_marks", marks_then_concurrent_insert)
    diff = AssignmentDiff(add_roles=frozenset({R1}), record_managed_roles=frozenset({R1}))
    with caplog.at_level("INFO", logger="tiqora.identity.provisioning"):
        await provisioning.apply_assignments(env.s, provider_id=p.id, user_id=uid, diff=diff)
    await env.s.commit()
    # The duplicate insert hit the unique key (IntegrityError path) and was ignored.
    assert any("managed mark already present" in r.getMessage() for r in caplog.records)
    assert await env.roles(uid) == {R1}
    assert await env.managed(uid) == {("role", R1, "")}
