"""SCIM user service: create, adopt, revive, replace, patch, delete, list (both dialects).

Seed band 63120-63189: unmanaged agents 63121..63129, roles 63120 (JIT initial) and 63121 (SCIM
rule); provisioned accounts get auto ids and are found by the login prefix ``sc63.``; providers use
the slug prefix ``sc63u``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import seed_agent, seed_customer
from tests._scim_helpers import make_tenant, scim_cleanup_statements, seed_scim_provider
from tests.test_identity_store import _to_async_url
from tiqora.api.v1.admin.common import USER_CACHE_TYPES
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraScimGroup, TiqoraScimGroupMember, TiqoraScimUser
from tiqora.identity.config_models import JitDefaults, RoleRule
from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.patch import PatchOp, parse_patch
from tiqora.identity.scim.protocol import SCHEMA_PATCH, ListParams, ScimFilter
from tiqora.identity.scim.tokens import ScimTenant
from tiqora.identity.scim.users import (
    ScimConflictError,
    create_user,
    delete_user,
    get_user,
    list_users,
    patch_user,
    replace_user,
)
from tiqora.identity.types import utc_now

pytestmark = pytest.mark.db

BASE = "https://helpdesk.example.test/scim/v2"
ROLE_INITIAL, ROLE_RULE = 63120, 63121
UNMANAGED = 63122
PAGE = ListParams(start_index=1, count=100, attributes=None, excluded=frozenset())
_URLS: set[str] = set()
_MARK: dict[str, int] = {}


def _stmts(url: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    inval = (
        "DELETE FROM tiqora_cache_invalidation WHERE id > :mx",
        {"mx": _MARK.get(url, 2**62)},
    )
    return (
        inval,
        *scim_cleanup_statements(
            slug_prefix="sc63u",
            login_like="sc63.%",
            user_ids=(63120, 63129),
            role_ids=(ROLE_INITIAL, ROLE_RULE),
        ),
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
        finally:
            engine.dispose()
    _run(url, _stmts(url))
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for rid in (ROLE_INITIAL, ROLE_RULE):
                conn.execute(
                    text(
                        "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                        " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
                    ),
                    {"i": rid, "n": f"sc63-role-{rid}", "t": utc_now()},
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
async def db(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _prepare(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.info["url"] = url
        yield s
        await s.rollback()
    await engine.dispose()
    _run(url, _stmts(url))


def _doc(name: str, **kw: Any) -> dict[str, Any]:
    email = kw.pop("email", name if "@" in name else f"{name}@example.test")
    doc: dict[str, Any] = {
        "userName": name,
        "name": {"givenName": kw.pop("given", "Neu"), "familyName": kw.pop("family", "Agent")},
        "emails": [{"value": email, "primary": True}],
        "active": kw.pop("active", True),
    }
    doc.update(kw)
    return doc


def _ops(*ops: dict[str, Any]) -> list[PatchOp]:
    return parse_patch({"schemas": [SCHEMA_PATCH], "Operations": list(ops)})


def _replace(path: str, value: Any) -> list[PatchOp]:
    return _ops({"op": "Replace", "path": path, "value": value})


async def _agent_tenant(db: AsyncSession, slug: str = "sc63u-a", **kw: Any) -> ScimTenant:
    return make_tenant(await seed_scim_provider(db, audience="agent", slug=slug, **kw), "agent")


async def _scalar(db: AsyncSession, sql: str, **params: Any) -> Any:
    return (await db.execute(text(sql), params)).scalar_one_or_none()


async def _row(db: AsyncSession, sql: str, **params: Any) -> Any:
    return (await db.execute(text(sql), params)).first()


async def _pref(db: AsyncSession, login: str, key: str) -> Any:
    return await _scalar(
        db,
        "SELECT p.preferences_value FROM user_preferences p WHERE p.preferences_key = :k"
        " AND p.user_id = (SELECT id FROM users WHERE login = :l)",
        k=key,
        l=login,
    )


def _text(raw: Any) -> str:
    return raw if isinstance(raw, str) else bytes(raw).decode()


# ---------------------------------------------------------------------------- create


@pytest.mark.asyncio
async def test_create_agent_writes_through_provisioning(db: AsyncSession) -> None:
    tenant = await _agent_tenant(
        db, jit_defaults=JitDefaults(initial_role_ids=[ROLE_INITIAL], customer_id="")
    )
    login = "sc63.neu@example.test"
    r = await create_user(db, tenant, _doc(login, active=True), base=BASE)
    assert r.operation == "create" and r.resource["active"] is True and not r.deactivated
    assert r.resource["userName"] == login and r.resource["meta"]["version"] == 'W/"1"'
    assert r.resource["meta"]["location"] == f"{BASE}/Users/{r.resource['id']}"
    user = await _row(
        db, "SELECT id, pw, valid_id, first_name, last_name FROM users WHERE login = :l", l=login
    )
    assert user is not None and user.id == r.user_id and user.valid_id == 1
    assert user.pw not in ("", "x") and (user.first_name, user.last_name) == ("Neu", "Agent")
    assert _text(await _pref(db, login, "UserEmail")) == login
    assert await _scalar(
        db, "SELECT sso_eligible FROM tiqora_user_auth_config WHERE user_id = :u", u=r.user_id
    )
    assert (
        await _scalar(
            db,
            "SELECT COUNT(*) FROM role_user WHERE user_id = :u AND role_id = :r",
            u=r.user_id,
            r=ROLE_INITIAL,
        )
        == 1
    )
    placeholders = ", ".join(f":t{i}" for i in range(len(USER_CACHE_TYPES)))
    inval = await _scalar(
        db,
        "SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE id > :mx"
        f" AND cache_type IN ({placeholders})",
        mx=_MARK[db.info["url"]],
        **{f"t{i}": t for i, t in enumerate(USER_CACHE_TYPES)},
    )
    assert inval >= 1
    mapped = await _row(
        db,
        "SELECT user_id, user_name, deleted, version, user_name_fold_hash FROM tiqora_scim_user"
        " WHERE scim_id = :s",
        s=r.resource["id"],
    )
    assert mapped is not None and mapped.user_id == r.user_id and mapped.version == 1
    assert (
        mapped.user_name == login and not mapped.deleted and len(mapped.user_name_fold_hash) == 64
    )


@pytest.mark.asyncio
async def test_create_inactive_creates_invalid(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    r = await create_user(db, tenant, _doc("sc63.inaktiv@example.test", active=False), base=BASE)
    assert r.resource["active"] is False and r.operation == "create"
    assert await _scalar(db, "SELECT valid_id FROM users WHERE id = :u", u=r.user_id) == 2


@pytest.mark.asyncio
async def test_create_customer_customer_id_fallbacks(db: AsyncSession) -> None:
    with_default = make_tenant(
        await seed_scim_provider(
            db,
            audience="customer",
            slug="sc63u-cust1",
            jit_defaults=JitDefaults(customer_id="sc63-co"),
        ),
        "customer",
    )
    without = make_tenant(
        await seed_scim_provider(db, audience="customer", slug="sc63u-cust2"), "customer"
    )
    a = await create_user(db, with_default, _doc("sc63.kunde1@example.test"), base=BASE)
    b = await create_user(db, without, _doc("sc63.kunde2@example.test"), base=BASE)
    q = "SELECT customer_id FROM customer_user WHERE id = :u"
    assert await _scalar(db, q, u=a.user_id) == "sc63-co"
    assert await _scalar(db, q, u=b.user_id) == "sc63.kunde2@example.test"
    assert a.resource["emails"][0]["value"] == "sc63.kunde1@example.test"


@pytest.mark.asyncio
async def test_post_existing_login_conflicts_without_adoption(db: AsyncSession) -> None:
    await seed_agent(db, UNMANAGED, "sc63.alt", email="sc63.alt@example.test")
    tenant = await _agent_tenant(db)
    with pytest.raises(ScimConflictError) as exc:
        await create_user(db, tenant, _doc("sc63.alt", family="Anders"), base=BASE)
    assert (exc.value.status, exc.value.scim_type, exc.value.reason) == (
        409,
        "uniqueness",
        "not_adopted",
    )
    assert (
        await _scalar(db, "SELECT COUNT(*) FROM tiqora_scim_user WHERE user_id = :u", u=UNMANAGED)
        == 0
    )
    assert await _scalar(db, "SELECT last_name FROM users WHERE id = :u", u=UNMANAGED) == "Test"


@pytest.mark.asyncio
async def test_adopt_existing_when_enabled(db: AsyncSession) -> None:
    await seed_agent(db, UNMANAGED, "sc63.alt", email="sc63.alt@example.test")
    tenant = await _agent_tenant(db, scim_adopt_existing=True)
    r = await create_user(db, tenant, _doc("sc63.alt", given="Idp", family="Neuer"), base=BASE)
    assert r.operation == "adopt" and r.user_id == UNMANAGED and r.detail == "last_name"
    assert await _scalar(db, "SELECT last_name FROM users WHERE id = :u", u=UNMANAGED) == "Neuer"
    assert await _scalar(db, "SELECT login FROM users WHERE id = :u", u=UNMANAGED) == "sc63.alt"
    assert (
        await _scalar(
            db,
            "SELECT COUNT(*) FROM tiqora_scim_user WHERE user_id = :u AND deleted = :d",
            u=UNMANAGED,
            d=False,
        )
        == 1
    )
    assert r.resource["name"]["familyName"] == "Neuer" and r.resource["active"] is True


@pytest.mark.asyncio
async def test_never_adopts_system_user(db: AsyncSession) -> None:
    login = await _scalar(db, "SELECT login FROM users WHERE id = 1")
    assert login
    tenant = await _agent_tenant(db, scim_adopt_existing=True)
    before = await _row(db, "SELECT first_name, last_name, valid_id FROM users WHERE id = 1")
    with pytest.raises(ScimConflictError) as exc:
        await create_user(
            db, tenant, _doc(login, family="Hijack", email="root@example.test"), base=BASE
        )
    assert exc.value.reason == "system_user" and exc.value.status == 409
    assert tuple(
        await _row(db, "SELECT first_name, last_name, valid_id FROM users WHERE id = 1")
    ) == tuple(before)
    assert await _scalar(db, "SELECT COUNT(*) FROM tiqora_scim_user WHERE user_id = 1") == 0


@pytest.mark.asyncio
async def test_system_user_row_is_never_written(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    now = utc_now()
    db.add(
        TiqoraScimUser(
            provider_id=tenant.provider.id,
            user_kind="agent",
            scim_id="00000000-0000-4000-8000-000000630001",
            user_id=1,
            user_name="sc63.root",
            user_name_fold="sc63.root",
            user_name_fold_hash="0" * 64,
            deleted=False,
            version=1,
            created=now,
            changed=now,
        )
    )
    await db.flush()
    with pytest.raises(ScimError) as exc:
        await replace_user(
            db,
            tenant,
            "00000000-0000-4000-8000-000000630001",
            _doc("sc63.root", active=False),
            base=BASE,
        )
    assert (exc.value.status, exc.value.scim_type) == (409, "mutability")
    with pytest.raises(ScimError) as exc2:
        await delete_user(db, tenant, "00000000-0000-4000-8000-000000630001")
    assert exc2.value.scim_type == "mutability"
    assert await _scalar(db, "SELECT valid_id FROM users WHERE id = 1") == 1


@pytest.mark.asyncio
async def test_owned_by_other_provider_conflicts(db: AsyncSession) -> None:
    a = await _agent_tenant(db, "sc63u-a")
    b = await _agent_tenant(db, "sc63u-b", scim_adopt_existing=True)
    r = await create_user(db, a, _doc("sc63.shared@example.test"), base=BASE)
    with pytest.raises(ScimConflictError) as exc:
        await create_user(db, b, _doc("sc63.shared@example.test"), base=BASE)
    assert exc.value.reason == "other_provider"
    rows = await _scalar(
        db, "SELECT COUNT(*) FROM tiqora_scim_user WHERE user_id = :u", u=r.user_id
    )
    assert rows == 1


@pytest.mark.asyncio
async def test_duplicate_username_in_tenant_conflicts(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    await create_user(db, tenant, _doc("sc63.dup@example.test"), base=BASE)
    with pytest.raises(ScimConflictError) as exc:
        await create_user(
            db, tenant, _doc("SC63.DUP@example.test", email="sc63.dup2@example.test"), base=BASE
        )
    assert exc.value.reason == "duplicate_user_name" and exc.value.scim_type == "uniqueness"
    assert await _scalar(db, "SELECT COUNT(*) FROM users WHERE login LIKE 'sc63.dup%'") == 1


@pytest.mark.asyncio
async def test_email_in_use_customer_is_uniqueness(db: AsyncSession) -> None:
    await seed_customer(db, "sc63.vorhanden@example.test", email="sc63.mail@example.test")
    tenant = make_tenant(
        await seed_scim_provider(db, audience="customer", slug="sc63u-c"), "customer"
    )
    with pytest.raises(ScimError) as exc:
        await create_user(
            db,
            tenant,
            _doc("sc63.neukunde@example.test", email="sc63.mail@example.test"),
            base=BASE,
        )
    assert (exc.value.status, exc.value.scim_type, exc.value.detail) == (
        409,
        "uniqueness",
        "email_in_use",
    )
    assert (
        await _scalar(
            db, "SELECT COUNT(*) FROM customer_user WHERE login = 'sc63.neukunde@example.test'"
        )
        == 0
    )


@pytest.mark.asyncio
async def test_invalid_email_is_invalid_value(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    with pytest.raises(ScimError) as exc:
        await create_user(
            db, tenant, _doc("sc63.bad@example.test", email="x@example.org"), base=BASE
        )
    assert (exc.value.status, exc.value.scim_type, exc.value.detail) == (
        400,
        "invalidValue",
        "email_invalid",
    )


@pytest.mark.asyncio
async def test_missing_email_is_invalid_value(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    doc = {"userName": "sc63.ohnemail", "name": {"givenName": "A", "familyName": "B"}}
    with pytest.raises(ScimError) as exc:
        await create_user(db, tenant, doc, base=BASE)
    assert (exc.value.status, exc.value.scim_type) == (400, "invalidValue")


@pytest.mark.asyncio
async def test_create_refuses_domain_outside_allow_list(db: AsyncSession) -> None:
    tenant = await _agent_tenant(
        db, jit_defaults=JitDefaults(allowed_email_domains=["example.test"])
    )
    with pytest.raises(ScimError) as exc:
        await create_user(db, tenant, _doc("sc63.gast", email="gast@example.net"), base=BASE)
    assert (exc.value.status, exc.value.scim_type, exc.value.detail) == (
        400,
        "invalidValue",
        "domain_not_allowed",
    )
    assert await _scalar(db, "SELECT COUNT(*) FROM users WHERE login = 'sc63.gast'") == 0


# ---------------------------------------------------------------------------- update


@pytest.mark.asyncio
async def test_patch_active_false_deactivates(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    c = await create_user(db, tenant, _doc("sc63.pa@example.test"), base=BASE)
    r = await patch_user(db, tenant, c.resource["id"], _replace("active", "False"), base=BASE)
    assert r.deactivated and r.operation == "deactivate" and not r.reactivated
    assert r.resource["active"] is False and r.resource["meta"]["version"] == 'W/"2"'
    assert await _scalar(db, "SELECT valid_id FROM users WHERE id = :u", u=r.user_id) == 2


@pytest.mark.asyncio
async def test_patch_active_true_reactivates_and_enqueues(db: AsyncSession) -> None:
    rule = RoleRule(attribute="scim:groups", value="Support", role_ids=[ROLE_RULE])
    tenant = await _agent_tenant(db, role_rules=(rule,))
    c = await create_user(db, tenant, _doc("sc63.re@example.test", active=False), base=BASE)
    await db.execute(text("DELETE FROM tiqora_scim_pending_sync"))
    r = await patch_user(db, tenant, c.resource["id"], _replace("active", True), base=BASE)
    assert r.reactivated and r.operation == "reactivate" and not r.deactivated
    assert await _scalar(db, "SELECT valid_id FROM users WHERE id = :u", u=r.user_id) == 1
    pending = await _row(
        db,
        "SELECT user_id, generation FROM tiqora_scim_pending_sync WHERE provider_id = :p",
        p=tenant.provider.id,
    )
    assert pending is not None and pending.user_id == r.user_id and pending.generation >= 1


@pytest.mark.asyncio
async def test_no_enqueue_without_scim_rules(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    await create_user(db, tenant, _doc("sc63.keinsync@example.test"), base=BASE)
    assert (
        await _scalar(
            db,
            "SELECT COUNT(*) FROM tiqora_scim_pending_sync WHERE provider_id = :p",
            p=tenant.provider.id,
        )
        == 0
    )


@pytest.mark.asyncio
async def test_patch_invalid_email_is_invalid_value(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    c = await create_user(db, tenant, _doc("sc63.pm@example.test"), base=BASE)
    ops = _replace('emails[type eq "work"].value', "x@example.org")
    with pytest.raises(ScimError) as exc:
        await patch_user(db, tenant, c.resource["id"], ops, base=BASE)
    assert (exc.value.status, exc.value.scim_type, exc.value.detail) == (
        400,
        "invalidValue",
        "email_invalid",
    )


@pytest.mark.asyncio
async def test_patch_name_updates_znuny_profile(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    c = await create_user(db, tenant, _doc("sc63.pn@example.test"), base=BASE)
    r = await patch_user(
        db, tenant, c.resource["id"], _replace("name.familyName", "Umbenannt"), base=BASE
    )
    assert r.operation == "patch" and r.detail == "last_name"
    assert (
        await _scalar(db, "SELECT last_name FROM users WHERE id = :u", u=r.user_id) == "Umbenannt"
    )
    assert r.resource["name"]["familyName"] == "Umbenannt"


@pytest.mark.asyncio
async def test_username_change_does_not_rename_login(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    old, new = "sc63.old@example.test", "sc63.new@example.test"
    c = await create_user(db, tenant, _doc(old), base=BASE)
    r = await replace_user(db, tenant, c.resource["id"], _doc(new, email=old), base=BASE)
    assert r.operation == "replace" and r.detail is not None and "login_not_renamed" in r.detail
    assert await _scalar(db, "SELECT login FROM users WHERE id = :u", u=r.user_id) == old
    assert (
        await _scalar(
            db, "SELECT user_name FROM tiqora_scim_user WHERE scim_id = :s", s=c.resource["id"]
        )
        == new
    )
    found, docs = await list_users(db, tenant, ScimFilter("userName", new), PAGE, base=BASE)
    gone, _ = await list_users(db, tenant, ScimFilter("userName", old), PAGE, base=BASE)
    assert (found, gone) == (1, 0) and docs[0]["userName"] == new


@pytest.mark.asyncio
async def test_username_change_to_taken_name_conflicts(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    a = await create_user(db, tenant, _doc("sc63.t1@example.test"), base=BASE)
    await create_user(db, tenant, _doc("sc63.t2@example.test"), base=BASE)
    with pytest.raises(ScimConflictError) as exc:
        await replace_user(
            db,
            tenant,
            a.resource["id"],
            _doc("sc63.t2@example.test", email="sc63.t1@example.test"),
            base=BASE,
        )
    assert exc.value.reason == "duplicate_user_name"


@pytest.mark.asyncio
async def test_put_leaves_unsent_fields(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    login = "sc63.put@example.test"
    c = await create_user(
        db,
        tenant,
        _doc(login, phoneNumbers=[{"value": "+49 30 1234", "type": "mobile"}]),
        base=BASE,
    )
    assert c.resource["phoneNumbers"][0]["value"] == "+49 30 1234"
    r = await replace_user(db, tenant, c.resource["id"], _doc(login, family="Neu2"), base=BASE)
    assert _text(await _pref(db, login, "UserMobile")) == "+49 30 1234"
    assert r.resource["phoneNumbers"][0]["value"] == "+49 30 1234"
    assert await _scalar(db, "SELECT last_name FROM users WHERE id = :u", u=r.user_id) == "Neu2"


@pytest.mark.asyncio
async def test_noop_replace_keeps_version(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    login = "sc63.noop@example.test"
    c = await create_user(db, tenant, _doc(login), base=BASE)
    r = await replace_user(db, tenant, c.resource["id"], _doc(login), base=BASE)
    assert r.detail is None and r.resource["meta"]["version"] == 'W/"1"'


@pytest.mark.asyncio
async def test_external_id_set_on_replace(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    login = "sc63.ext@example.test"
    c = await create_user(db, tenant, _doc(login), base=BASE)
    r = await replace_user(
        db, tenant, c.resource["id"], _doc(login, externalId="entra-1"), base=BASE
    )
    assert r.resource["externalId"] == "entra-1" and r.detail == "externalId"
    total, docs = await list_users(db, tenant, ScimFilter("externalId", "entra-1"), PAGE, base=BASE)
    assert total == 1 and docs[0]["id"] == c.resource["id"]


# ---------------------------------------------------------------------------- delete


@pytest.mark.asyncio
async def test_delete_hides_and_deactivates(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    c = await create_user(db, tenant, _doc("sc63.del@example.test"), base=BASE)
    now = utc_now()
    group = TiqoraScimGroup(
        provider_id=tenant.provider.id,
        scim_id="00000000-0000-4000-8000-000000630002",
        display_name="Support",
        display_name_fold="support",
        display_name_fold_hash="1" * 64,
        version=1,
        created=now,
        changed=now,
    )
    db.add(group)
    await db.flush()
    member = await _scalar(
        db, "SELECT id FROM tiqora_scim_user WHERE scim_id = :s", s=c.resource["id"]
    )
    db.add(TiqoraScimGroupMember(group_id=group.id, member_id=member))
    await db.flush()
    assert (await get_user(db, tenant, c.resource["id"], base=BASE))["groups"][0][
        "display"
    ] == "Support"

    r = await delete_user(db, tenant, c.resource["id"])
    assert r.deactivated and r.operation == "delete"
    with pytest.raises(ScimError) as exc:
        await get_user(db, tenant, c.resource["id"], base=BASE)
    assert exc.value.status == 404
    total, _ = await list_users(db, tenant, None, PAGE, base=BASE)
    assert total == 0
    assert await _scalar(db, "SELECT valid_id FROM users WHERE id = :u", u=c.user_id) == 2
    assert (
        await _scalar(
            db, "SELECT COUNT(*) FROM tiqora_scim_group_member WHERE member_id = :m", m=member
        )
        == 0
    )


@pytest.mark.asyncio
async def test_repost_after_delete_revives(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    login = "sc63.rev@example.test"
    c = await create_user(db, tenant, _doc(login), base=BASE)
    await delete_user(db, tenant, c.resource["id"])
    r = await create_user(db, tenant, _doc(login), base=BASE)
    assert r.operation == "revive" and r.user_id == c.user_id and r.reactivated
    assert r.resource["id"] != c.resource["id"] and r.resource["active"] is True
    assert await _scalar(db, "SELECT valid_id FROM users WHERE id = :u", u=r.user_id) == 1
    with pytest.raises(ScimError):
        await get_user(db, tenant, c.resource["id"], base=BASE)
    assert (await get_user(db, tenant, r.resource["id"], base=BASE))["userName"] == login


# ---------------------------------------------------------------------------- read


@pytest.mark.asyncio
async def test_cross_tenant_ids_are_404(db: AsyncSession) -> None:
    both = await seed_scim_provider(db, audience="both", slug="sc63u-both")
    a_agent, a_cust = make_tenant(both, "agent"), make_tenant(both, "customer")
    b_agent = await _agent_tenant(db, "sc63u-b")
    made = await create_user(db, a_agent, _doc("sc63.cross@example.test"), base=BASE)
    sid = made.resource["id"]
    for other in (b_agent, a_cust):
        with pytest.raises(ScimError) as g:
            await get_user(db, other, sid, base=BASE)
        with pytest.raises(ScimError) as p:
            await replace_user(db, other, sid, _doc("sc63.cross@example.test"), base=BASE)
        with pytest.raises(ScimError) as pa:
            await patch_user(db, other, sid, _replace("active", False), base=BASE)
        with pytest.raises(ScimError) as d:
            await delete_user(db, other, sid)
        assert [e.value.status for e in (g, p, pa, d)] == [404, 404, 404, 404]
    assert await _scalar(db, "SELECT valid_id FROM users WHERE id = :u", u=made.user_id) == 1
    with pytest.raises(ScimError):
        await get_user(db, a_agent, "not-a-uuid" * 10, base=BASE)


@pytest.mark.asyncio
async def test_list_shows_only_managed(db: AsyncSession) -> None:
    await seed_agent(db, UNMANAGED, "sc63.unmanaged", email="sc63.unmanaged@example.test")
    tenant = await _agent_tenant(db)
    await create_user(db, tenant, _doc("sc63.m1@example.test"), base=BASE)
    await create_user(db, tenant, _doc("sc63.m2@example.test"), base=BASE)
    total, docs = await list_users(db, tenant, None, PAGE, base=BASE)
    assert total == 2 and [d["userName"] for d in docs] == [
        "sc63.m1@example.test",
        "sc63.m2@example.test",
    ]
    assert await list_users(
        db, tenant, ScimFilter("userName", "sc63.unmanaged"), PAGE, base=BASE
    ) == (0, [])
    assert await list_users(
        db, tenant, ScimFilter("emails.value", "sc63.unmanaged@example.test"), PAGE, base=BASE
    ) == (0, [])


@pytest.mark.asyncio
async def test_filter_by_email_and_external_id(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    a = await create_user(db, tenant, _doc("sc63.fa@example.test", externalId="ext-a"), base=BASE)
    await create_user(db, tenant, _doc("sc63.fb@example.test", externalId="ext-b"), base=BASE)
    t1, d1 = await list_users(
        db, tenant, ScimFilter("emails.value", "SC63.FA@example.test"), PAGE, base=BASE
    )
    t2, d2 = await list_users(db, tenant, ScimFilter("externalId", "ext-b"), PAGE, base=BASE)
    t3, d3 = await list_users(db, tenant, ScimFilter("id", a.resource["id"]), PAGE, base=BASE)
    t4, _ = await list_users(
        db, tenant, ScimFilter("userName", "SC63.FB@EXAMPLE.TEST"), PAGE, base=BASE
    )
    assert (t1, t2, t3, t4) == (1, 1, 1, 1)
    assert d1[0]["id"] == a.resource["id"] and d2[0]["userName"] == "sc63.fb@example.test"
    assert d3[0]["externalId"] == "ext-a"


@pytest.mark.asyncio
async def test_filter_by_email_customers(db: AsyncSession) -> None:
    tenant = make_tenant(
        await seed_scim_provider(db, audience="customer", slug="sc63u-c"), "customer"
    )
    c = await create_user(db, tenant, _doc("sc63.ce@example.test"), base=BASE)
    total, docs = await list_users(
        db, tenant, ScimFilter("emails.value", "SC63.CE@example.test"), PAGE, base=BASE
    )
    assert total == 1 and docs[0]["id"] == c.resource["id"]


@pytest.mark.asyncio
async def test_list_paging(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    for n in (1, 2, 3):
        await create_user(db, tenant, _doc(f"sc63.p{n}@example.test"), base=BASE)
    total, docs = await list_users(db, tenant, None, ListParams(2, 1, None, frozenset()), base=BASE)
    assert total == 3 and [d["userName"] for d in docs] == ["sc63.p2@example.test"]
    total0, none = await list_users(
        db, tenant, None, ListParams(1, 0, None, frozenset()), base=BASE
    )
    assert (total0, none) == (3, [])
    totalx, tail = await list_users(
        db, tenant, None, ListParams(3, 100, None, frozenset()), base=BASE
    )
    assert totalx == 3 and [d["userName"] for d in tail] == ["sc63.p3@example.test"]


@pytest.mark.asyncio
async def test_active_reflects_znuny(db: AsyncSession) -> None:
    tenant = await _agent_tenant(db)
    c = await create_user(db, tenant, _doc("sc63.live@example.test"), base=BASE)
    await patch_user(db, tenant, c.resource["id"], _replace("active", "False"), base=BASE)
    assert (await get_user(db, tenant, c.resource["id"], base=BASE))["active"] is False
    await db.execute(text("UPDATE users SET valid_id = 1 WHERE id = :u"), {"u": c.user_id})
    assert (await get_user(db, tenant, c.resource["id"], base=BASE))["active"] is True
