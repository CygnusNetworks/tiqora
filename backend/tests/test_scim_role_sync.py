"""SCIM role sync: pending queue worker, ``scim:*`` attributes, SSO login keeps SCIM roles.

Seed band 63240-63279: roles 63241 (rule target) and 63242 (manual); provisioned agents get auto
ids and are found by the login prefix ``sc63t.``; providers use the slug prefix ``sc63t``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._scim_helpers import KEY, SETTINGS, make_tenant, scim_cleanup_statements
from tests._scim_helpers import seed_scim_provider as seed_provider
from tests.test_identity_store import _to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.settings_store import KEY_SCIM_ROLE_SYNC_ENABLED
from tiqora.identity import provisioning
from tiqora.identity.config_models import RoleRule
from tiqora.identity.login import ExternalIdentity, plan_login
from tiqora.identity.scim import role_sync
from tiqora.identity.scim.groups import create_group, patch_group
from tiqora.identity.scim.patch import PatchOp, parse_patch
from tiqora.identity.scim.protocol import SCHEMA_PATCH
from tiqora.identity.scim.role_sync import (
    enqueue_role_sync,
    run_scim_role_sync_tick,
    scim_attributes,
)
from tiqora.identity.scim.tokens import ScimTenant
from tiqora.identity.scim.users import create_user
from tiqora.identity.store import ProviderChanges, update_provider
from tiqora.identity.types import utc_now
from tiqora.worker.services import DAEMON_SERVICES

pytestmark = pytest.mark.db

BASE = "https://helpdesk.example.test/scim/v2"
ROLE, MANUAL = 63241, 63242
RULE = RoleRule(attribute="scim:groups", value="Support", role_ids=[ROLE])
MAXM = 5000
_URLS: set[str] = set()
_MARK: dict[str, int] = {}


def _stmts(url: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("DELETE FROM tiqora_cache_invalidation WHERE id > :mx", {"mx": _MARK.get(url, 2**62)}),
        *scim_cleanup_statements(
            slug_prefix="sc63t", login_like="sc63t.%", role_ids=(ROLE, MANUAL)
        ),
    )


def _run(url: str, stmts: tuple[tuple[str, dict[str, Any]], ...]) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in stmts:
                conn.execute(text(stmt), params)
            if stmts:
                conn.execute(
                    delete(TiqoraSettings).where(TiqoraSettings.key == KEY_SCIM_ROLE_SYNC_ENABLED)
                )
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
        finally:
            engine.dispose()
    _run(url, _stmts(url))
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for rid in (ROLE, MANUAL):
                conn.execute(
                    text(
                        "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                        " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
                    ),
                    {"i": rid, "n": f"sc63t-role-{rid}", "t": utc_now()},
                )
    finally:
        engine.dispose()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_rows() -> Iterator[None]:
    yield
    for url in _URLS:
        _run(url, _stmts(url))
    _URLS.clear()


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def db(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _prepare(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(role_sync, "get_session_factory", lambda: maker)
    monkeypatch.setattr(role_sync, "get_settings", lambda: SETTINGS)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()
    _run(url, _stmts(url))


async def _tenant(
    db: AsyncSession, slug: str = "sc63t-a", rules: tuple[RoleRule, ...] = (RULE,)
) -> ScimTenant:
    rec = await seed_provider(db, audience="agent", slug=slug, role_rules=rules)
    return make_tenant(rec, "agent")


async def _agent(db: AsyncSession, tenant: ScimTenant, name: str) -> tuple[str, int]:
    login = f"sc63t.{name}@example.test"
    r = await create_user(
        db,
        tenant,
        {
            "userName": login,
            "name": {"givenName": "G", "familyName": name},
            "emails": [{"value": login, "primary": True}],
            "active": True,
        },
        base=BASE,
    )
    return r.resource["id"], r.user_id


async def _group(
    db: AsyncSession, tenant: ScimTenant, name: str, *ids: str, **kw: Any
) -> dict[str, Any]:
    doc = {"displayName": name, "members": [{"value": i} for i in ids], **kw}
    r = await create_group(db, tenant, doc, base=BASE, max_members=MAXM)
    return r.resource


def _ops(*ops: dict[str, Any]) -> list[PatchOp]:
    return parse_patch({"schemas": [SCHEMA_PATCH], "Operations": list(ops)})


async def _q(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    await db.commit()  # fresh snapshot: the tick writes through other sessions
    return [tuple(r) for r in (await db.execute(text(sql), params)).all()]


async def _roles(db: AsyncSession, user_id: int) -> set[int]:
    rows = await _q(db, "SELECT role_id FROM role_user WHERE user_id = :u", u=user_id)
    return {int(r[0]) for r in rows}


async def _marks(db: AsyncSession, user_id: int) -> set[int]:
    rows = await _q(
        db,
        "SELECT target_id FROM tiqora_identity_managed_assignment"
        " WHERE user_id = :u AND kind = 'role'",
        u=user_id,
    )
    return {int(r[0]) for r in rows}


async def _pending(db: AsyncSession) -> list[tuple[int, int]]:
    rows = await _q(db, "SELECT user_id, generation FROM tiqora_scim_pending_sync ORDER BY user_id")
    return [(int(u), int(g)) for u, g in rows]


async def _only_pending(db: AsyncSession, *user_ids: int) -> None:
    """Leave exactly these users queued (other tests' leftovers in the table are not ours)."""
    await db.execute(text("DELETE FROM tiqora_scim_pending_sync"))
    tenant_provider = (
        await db.execute(text("SELECT id FROM tiqora_identity_provider WHERE slug = 'sc63t-a'"))
    ).scalar_one()
    await enqueue_role_sync(db, int(tenant_provider), user_ids)
    await db.commit()


# ---------------------------------------------------------------------------- the tick


@pytest.mark.asyncio
async def test_tick_grants_role_from_group(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Support", a)
    await _only_pending(db, uid)
    assert await _roles(db, uid) == set()
    assert await run_scim_role_sync_tick() == {"processed": 1, "ignored": 0, "errors": 0}
    assert await _roles(db, uid) == {ROLE}
    assert await _marks(db, uid) == {ROLE}
    assert await _pending(db) == []


@pytest.mark.asyncio
async def test_other_group_name_grants_nothing(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Sales", a)
    await _only_pending(db, uid)
    assert (await run_scim_role_sync_tick())["processed"] == 1
    assert await _roles(db, uid) == set()


@pytest.mark.asyncio
async def test_group_ids_attribute(db: AsyncSession) -> None:
    rule = RoleRule(attribute="scim:group_ids", value="ext-support-1", role_ids=[ROLE])
    tenant = await _tenant(db, rules=(rule,))
    a, uid = await _agent(db, tenant, "a")
    b, uid_b = await _agent(db, tenant, "b")
    await _group(db, tenant, "Anything", a, externalId="ext-support-1")
    await _group(db, tenant, "Other", b, externalId="ext-other")
    await _only_pending(db, uid, uid_b)
    assert (await run_scim_role_sync_tick())["processed"] == 2
    assert await _roles(db, uid) == {ROLE}
    assert await _roles(db, uid_b) == set()
    assert await scim_attributes(db, provider_id=tenant.provider.id, user_id=uid) == {
        "scim:groups": ("Anything",),
        "scim:group_ids": ("ext-support-1",),
    }


@pytest.mark.asyncio
async def test_manual_role_survives_group_removal(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    g = await _group(db, tenant, "Support", a)
    await db.execute(
        text(
            "INSERT INTO role_user (user_id, role_id, create_time, create_by, change_time,"
            " change_by) VALUES (:u, :r, :t, 1, :t, 1)"
        ),
        {"u": uid, "r": MANUAL, "t": utc_now()},
    )
    await _only_pending(db, uid)
    await run_scim_role_sync_tick()
    assert await _roles(db, uid) == {ROLE, MANUAL}
    await patch_group(
        db,
        tenant,
        g["id"],
        _ops({"op": "remove", "path": "members", "value": [{"value": a}]}),
        base=BASE,
        max_members=MAXM,
    )
    await db.commit()
    assert await _pending(db) == [(uid, 1)]
    assert (await run_scim_role_sync_tick())["processed"] == 1
    assert await _roles(db, uid) == {MANUAL}
    assert await _marks(db, uid) == set()


@pytest.mark.asyncio
async def test_newer_enqueue_survives_tick(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Support", a)
    await _only_pending(db, uid)
    original = provisioning.apply_assignments

    async def apply_then_requeue(session: AsyncSession, **kw: Any) -> None:
        await original(session, **kw)
        await enqueue_role_sync(session, kw["provider_id"], [kw["user_id"]])

    monkeypatch.setattr(provisioning, "apply_assignments", apply_then_requeue)
    assert (await run_scim_role_sync_tick())["processed"] == 1
    assert await _roles(db, uid) == {ROLE}
    assert await _pending(db) == [(uid, 2)]  # generation 2 != the generation read (1)
    monkeypatch.setattr(provisioning, "apply_assignments", original)
    assert (await run_scim_role_sync_tick())["processed"] == 1
    assert await _pending(db) == []


@pytest.mark.asyncio
async def test_invalid_or_system_user_skipped(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Support", a)
    await db.execute(text("UPDATE users SET valid_id = 2 WHERE id = :u"), {"u": uid})
    await _only_pending(db, uid, 1)
    assert await run_scim_role_sync_tick() == {"processed": 0, "ignored": 2, "errors": 0}
    assert await _roles(db, uid) == set()
    assert await _roles(db, 1) == set() or ROLE not in await _roles(db, 1)
    assert await _pending(db) == []


@pytest.mark.asyncio
async def test_deleted_scim_user_skipped(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Support", a)
    await db.execute(
        text("UPDATE tiqora_scim_user SET deleted = :d WHERE user_id = :u"),
        {"u": uid, "d": True},
    )
    await db.commit()
    assert await scim_attributes(db, provider_id=tenant.provider.id, user_id=uid) == {
        "scim:groups": (),
        "scim:group_ids": (),
    }
    await _only_pending(db, uid)
    assert await run_scim_role_sync_tick() == {"processed": 0, "ignored": 1, "errors": 0}
    assert await _roles(db, uid) == set()


@pytest.mark.asyncio
async def test_rules_no_longer_scim_drops_row(db: AsyncSession) -> None:
    tenant = await _tenant(db, rules=(RoleRule(attribute="groups", value="x", role_ids=[ROLE]),))
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Support", a)
    await _only_pending(db, uid)
    assert await run_scim_role_sync_tick() == {"processed": 0, "ignored": 1, "errors": 0}
    assert await _roles(db, uid) == set()
    assert await _pending(db) == []


@pytest.mark.asyncio
async def test_error_keeps_row(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Support", a)
    await _only_pending(db, uid)

    async def boom(session: AsyncSession, **kw: Any) -> None:
        raise RuntimeError("znuny down")

    monkeypatch.setattr(provisioning, "apply_assignments", boom)
    assert await run_scim_role_sync_tick() == {"processed": 0, "ignored": 0, "errors": 1}
    assert await _pending(db) == [(uid, 1)]
    assert await _roles(db, uid) == set()


@pytest.mark.asyncio
async def test_tick_honours_switch(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Support", a)
    await _only_pending(db, uid)
    db.add(TiqoraSettings(key=KEY_SCIM_ROLE_SYNC_ENABLED, value="false"))
    await db.commit()
    assert await run_scim_role_sync_tick() == {"skipped": 1}
    assert await _pending(db) == [(uid, 1)]
    assert await _roles(db, uid) == set()


# ---------------------------------------------------------------------------- SSO login


def _identity(provider_id: int, login: str, **extra: tuple[str, ...]) -> ExternalIdentity:
    return ExternalIdentity(
        provider_id=provider_id,
        subject=f"sub-{uuid.uuid4()}",
        attributes={"preferred_username": (login,), "email": (login,), **extra},
        email_verified=True,
    )


@pytest.mark.asyncio
async def test_sso_login_keeps_scim_roles(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    provider = tenant.provider
    a, uid = await _agent(db, tenant, "a")
    g = await _group(db, tenant, "Support", a)
    await _only_pending(db, uid)
    await run_scim_role_sync_tick()
    assert await _roles(db, uid) == {ROLE}
    login = "sc63t.a@example.test"
    # The assertion carries no scim:* attributes: the groups come from the mirror, so the login
    # plan neither strips the role nor forgets it.
    plan = await plan_login(
        db, provider, _identity(provider.id, login), user_kind="agent", settings=SETTINGS
    )
    assert (plan.decision, plan.user_id) == ("login", uid)
    assert plan.assignments.remove_roles == frozenset()
    assert plan.assignments.add_roles == frozenset()
    # Removed from the group before the worker ran: the DB state drives the diff.
    await patch_group(
        db,
        tenant,
        g["id"],
        _ops({"op": "remove", "path": "members", "value": [{"value": a}]}),
        base=BASE,
        max_members=MAXM,
    )
    await db.commit()
    plan = await plan_login(
        db, provider, _identity(provider.id, login), user_kind="agent", settings=SETTINGS
    )
    assert plan.assignments.remove_roles == frozenset({ROLE})
    # A forged scim:groups claim in the assertion is ignored in favour of the mirror.
    plan = await plan_login(
        db,
        provider,
        _identity(provider.id, login, **{"scim:groups": ("Support",)}),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert plan.assignments.remove_roles == frozenset({ROLE})


@pytest.mark.asyncio
async def test_sso_login_grants_from_group_before_worker(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, uid = await _agent(db, tenant, "a")
    await _group(db, tenant, "Support", a)
    await db.commit()
    plan = await plan_login(
        db,
        tenant.provider,
        _identity(tenant.provider.id, "sc63t.a@example.test"),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert plan.user_id == uid and plan.assignments.add_roles == frozenset({ROLE})


@pytest.mark.asyncio
async def test_jit_preview_with_scim_rules_grants_only_defaults(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    provider = await update_provider(
        db,
        tenant.provider.id,
        ProviderChanges(jit_enabled=True),
        secret_key=KEY,
        actor_id=1,
        settings=SETTINGS,
    )
    await db.commit()
    ident = ExternalIdentity(
        provider_id=provider.id,
        subject="sub-new",
        attributes={
            "preferred_username": ("sc63t.new@example.test",),
            "email": ("sc63t.new@example.test",),
            "given_name": ("New",),
            "family_name": ("Hire",),
        },
        email_verified=True,
    )
    plan = await plan_login(db, provider, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.user_id) == ("create", None)
    # No account, no group membership: the rules contribute nothing (JIT defaults are separate).
    assert plan.assignments.add_roles == frozenset() and plan.assignments.empty


def test_daemon_catalog_has_role_sync() -> None:
    svc = {s.slug: s for s in DAEMON_SERVICES}["scim_role_sync"]
    assert svc.schedule_kind == "interval"
    assert svc.enabled_key == KEY_SCIM_ROLE_SYNC_ENABLED
    assert svc.interval_settings_attr == "scim_role_sync_interval_seconds"
    assert svc.default_enabled and svc.toggleable
