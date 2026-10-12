"""SCIM group service: membership, role-sync enqueueing, filters, limits (both dialects).

Seed band 63190-63239: role 63190 (SCIM rule target); provisioned agents get auto ids and are found
by the login prefix ``sc63g.``; providers use the slug prefix ``sc63g``. The 5000-member test seeds
``tiqora_scim_user`` rows with soft-reference user ids 2_000_000_000..2_000_004_999 (never ``users``
rows); they are removed with the provider's other SCIM rows.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._scim_helpers import make_tenant, scim_cleanup_statements, seed_scim_provider
from tests.test_identity_store import _to_async_url
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraScimUser
from tiqora.identity.config_models import RoleRule
from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.groups import (
    create_group,
    delete_group,
    get_group,
    list_groups,
    patch_group,
    replace_group,
)
from tiqora.identity.scim.patch import PatchOp, parse_patch
from tiqora.identity.scim.protocol import SCHEMA_PATCH, ListParams, ScimFilter
from tiqora.identity.scim.tokens import ScimTenant
from tiqora.identity.scim.users import create_user, delete_user
from tiqora.identity.types import utc_now

pytestmark = pytest.mark.db

BASE = "https://helpdesk.example.test/scim/v2"
ROLE_RULE = 63190
RULE = RoleRule(attribute="scim:groups", value="Support", role_ids=[ROLE_RULE])
PAGE = ListParams(start_index=1, count=100, attributes=None, excluded=frozenset())
MAXM = 5000
_URLS: set[str] = set()
_MARK: dict[str, int] = {}


def _stmts(url: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    inval = (
        "DELETE FROM tiqora_cache_invalidation WHERE id > :mx",
        {"mx": _MARK.get(url, 2**62)},
    )
    return (
        inval,
        *scim_cleanup_statements(slug_prefix="sc63g", login_like="sc63g.%", role_ids=(ROLE_RULE,)),
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
            conn.execute(
                text(
                    "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
                ),
                {"i": ROLE_RULE, "n": f"sc63g-role-{ROLE_RULE}", "t": utc_now()},
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
        yield s
        await s.rollback()
    await engine.dispose()
    _run(url, _stmts(url))


async def _tenant(db: AsyncSession, slug: str = "sc63g-a", *, rules: bool = True) -> ScimTenant:
    rec = await seed_scim_provider(
        db, audience="agent", slug=slug, role_rules=(RULE,) if rules else ()
    )
    return make_tenant(rec, "agent")


async def _agent(db: AsyncSession, tenant: ScimTenant, name: str) -> tuple[str, int]:
    login = f"sc63g.{name}@example.test"
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


async def _clear_pending(db: AsyncSession) -> None:
    await db.execute(text("DELETE FROM tiqora_scim_pending_sync"))


async def _pending(db: AsyncSession) -> set[int]:
    rows = await db.execute(text("SELECT user_id FROM tiqora_scim_pending_sync"))
    return {int(r[0]) for r in rows}


async def _member_scim_ids(db: AsyncSession, group_scim_id: str) -> set[str]:
    rows = await db.execute(
        text(
            "SELECT u.scim_id FROM tiqora_scim_group_member m"
            " JOIN tiqora_scim_group g ON g.id = m.group_id"
            " JOIN tiqora_scim_user u ON u.id = m.member_id WHERE g.scim_id = :s"
        ),
        {"s": group_scim_id},
    )
    return {str(r[0]) for r in rows}


def _members(*ids: str) -> list[dict[str, str]]:
    return [{"value": i} for i in ids]


def _doc(name: str, *ids: str, **kw: Any) -> dict[str, Any]:
    return {"displayName": name, "members": _members(*ids), **kw}


def _ops(*ops: dict[str, Any]) -> list[PatchOp]:
    return parse_patch({"schemas": [SCHEMA_PATCH], "Operations": list(ops)})


async def _group(
    db: AsyncSession, tenant: ScimTenant, name: str, *ids: str, **kw: Any
) -> dict[str, Any]:
    r = await create_group(db, tenant, _doc(name, *ids, **kw), base=BASE, max_members=MAXM)
    return r.resource


async def _expect(coro: Any, status: int, scim_type: str | None) -> ScimError:
    with pytest.raises(ScimError) as info:
        await coro
    assert (info.value.status, info.value.scim_type) == (status, scim_type)
    return info.value


# ---------------------------------------------------------------------------- create


@pytest.mark.asyncio
async def test_create_group_with_members_enqueues(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, a_uid = await _agent(db, tenant, "a")
    b, b_uid = await _agent(db, tenant, "b")
    await _clear_pending(db)
    unknown = str(uuid.uuid4())
    r = await create_group(
        db,
        tenant,
        _doc("Support", a, b, unknown, "x" * 40, externalId="ext-1"),
        base=BASE,
        max_members=MAXM,
    )
    assert r.operation == "create" and r.ignored_members == 2
    assert r.affected_user_ids == {a_uid, b_uid}
    assert await _pending(db) == {a_uid, b_uid}
    res = r.resource
    assert res["displayName"] == "Support" and res["externalId"] == "ext-1"
    assert res["meta"]["version"] == 'W/"1"' and res["meta"]["resourceType"] == "Group"
    assert res["meta"]["location"] == f"{BASE}/Groups/{res['id']}"
    by_id = {m["value"]: m for m in res["members"]}
    assert set(by_id) == {a, b}
    assert by_id[a]["display"] == "sc63g.a@example.test"
    assert by_id[a]["$ref"] == f"{BASE}/Users/{a}"
    assert await _member_scim_ids(db, res["id"]) == {a, b}


@pytest.mark.asyncio
async def test_no_enqueue_without_scim_rules(db: AsyncSession) -> None:
    tenant = await _tenant(db, rules=False)
    a, a_uid = await _agent(db, tenant, "a")
    r = await create_group(db, tenant, _doc("Support", a), base=BASE, max_members=MAXM)
    assert r.affected_user_ids == {a_uid}  # reported to the router for the audit row
    assert await _pending(db) == set()
    await delete_group(db, tenant, r.resource["id"])
    assert await _pending(db) == set()


@pytest.mark.asyncio
async def test_duplicate_names_allowed_and_foreign_members_ignored(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    other = await _tenant(db, "sc63g-b")
    mine, _ = await _agent(db, tenant, "mine")
    theirs, _ = await _agent(db, other, "theirs")
    g1 = await _group(db, tenant, "Support", mine)
    r = await create_group(db, tenant, _doc("Support", mine, theirs), base=BASE, max_members=MAXM)
    assert r.ignored_members == 1 and g1["id"] != r.resource["id"]
    assert [m["value"] for m in r.resource["members"]] == [mine]
    # the other provider cannot see this tenant's group
    await _expect(get_group(db, other, g1["id"], base=BASE, include_members=True), 404, None)
    total, _ = await list_groups(db, other, None, PAGE, base=BASE)
    assert total == 0


@pytest.mark.asyncio
async def test_deleted_users_are_not_members(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, _ = await _agent(db, tenant, "a")
    b, b_uid = await _agent(db, tenant, "b")
    g = await _group(db, tenant, "Support", a, b)
    await delete_user(db, tenant, a)
    res = await get_group(db, tenant, g["id"], base=BASE, include_members=True)
    assert [m["value"] for m in res["members"]] == [b]
    r = await create_group(db, tenant, _doc("Other", a, b), base=BASE, max_members=MAXM)
    assert r.ignored_members == 1 and r.affected_user_ids == {b_uid}


@pytest.mark.asyncio
async def test_customer_tenant_has_no_groups(db: AsyncSession) -> None:
    rec = await seed_scim_provider(db, audience="customer", slug="sc63g-c")
    tenant = make_tenant(rec, "customer")
    await _expect(create_group(db, tenant, _doc("X"), base=BASE, max_members=MAXM), 404, None)
    await _expect(list_groups(db, tenant, None, PAGE, base=BASE), 404, None)


# ---------------------------------------------------------------------------- change


@pytest.mark.asyncio
async def test_patch_add_remove_affects_only_changed(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, a_uid = await _agent(db, tenant, "a")
    b, _ = await _agent(db, tenant, "b")
    c, c_uid = await _agent(db, tenant, "c")
    g = await _group(db, tenant, "Support", a, b)
    await _clear_pending(db)
    ops = _ops(
        {"op": "Add", "path": "members", "value": _members(c)},
        {"op": "Remove", "path": f'members[value eq "{a}"]'},
    )
    r = await patch_group(db, tenant, g["id"], ops, base=BASE, max_members=MAXM)
    assert r.operation == "patch" and r.affected_user_ids == {a_uid, c_uid}
    assert await _pending(db) == {a_uid, c_uid}
    assert {m["value"] for m in r.resource["members"]} == {b, c}
    assert r.resource["meta"]["version"] == 'W/"2"'
    assert await _member_scim_ids(db, g["id"]) == {b, c}


@pytest.mark.asyncio
async def test_patch_noop_and_unknown_member(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, _ = await _agent(db, tenant, "a")
    g = await _group(db, tenant, "Support", a)
    await _clear_pending(db)
    ops = _ops({"op": "Add", "path": "members", "value": _members(a, str(uuid.uuid4()))})
    r = await patch_group(db, tenant, g["id"], ops, base=BASE, max_members=MAXM)
    assert r.affected_user_ids == frozenset() and r.ignored_members == 1
    assert r.resource["meta"]["version"] == 'W/"1"' and await _pending(db) == set()


@pytest.mark.asyncio
async def test_rename_affects_all_members(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, a_uid = await _agent(db, tenant, "a")
    b, b_uid = await _agent(db, tenant, "b")
    g = await _group(db, tenant, "Support", a, b)
    await _clear_pending(db)
    ops = _ops({"op": "Replace", "path": "displayName", "value": "Helpdesk"})
    r = await patch_group(db, tenant, g["id"], ops, base=BASE, max_members=MAXM)
    assert r.affected_user_ids == {a_uid, b_uid} and await _pending(db) == {a_uid, b_uid}
    assert r.resource["displayName"] == "Helpdesk" and r.resource["meta"]["version"] == 'W/"2"'
    await _clear_pending(db)
    r2 = await replace_group(
        db, tenant, g["id"], _doc("Helpdesk", a, b), base=BASE, max_members=MAXM
    )
    assert r2.affected_user_ids == frozenset() and r2.resource["meta"]["version"] == 'W/"2"'
    r3 = await replace_group(
        db, tenant, g["id"], _doc("Helpdesk", a, b, externalId="e1"), base=BASE, max_members=MAXM
    )
    assert r3.affected_user_ids == {a_uid, b_uid} and r3.resource["externalId"] == "e1"


@pytest.mark.asyncio
async def test_put_replaces_member_list(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, a_uid = await _agent(db, tenant, "a")
    b, _ = await _agent(db, tenant, "b")
    c, c_uid = await _agent(db, tenant, "c")
    g = await _group(db, tenant, "Support", a, b)
    await _clear_pending(db)
    r = await replace_group(db, tenant, g["id"], _doc("Support", b, c), base=BASE, max_members=MAXM)
    assert r.operation == "replace" and r.affected_user_ids == {a_uid, c_uid}
    assert await _member_scim_ids(db, g["id"]) == {b, c}
    assert {m["value"] for m in r.resource["members"]} == {b, c}
    emptied = await replace_group(
        db, tenant, g["id"], {"displayName": "Support"}, base=BASE, max_members=MAXM
    )
    assert emptied.resource["members"] == [] and await _member_scim_ids(db, g["id"]) == set()


@pytest.mark.asyncio
async def test_delete_group_affects_all_and_removes_rows(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, a_uid = await _agent(db, tenant, "a")
    b, b_uid = await _agent(db, tenant, "b")
    g = await _group(db, tenant, "Support", a, b)
    await _clear_pending(db)
    r = await delete_group(db, tenant, g["id"])
    assert r.operation == "delete" and r.affected_user_ids == {a_uid, b_uid}
    assert await _pending(db) == {a_uid, b_uid}
    assert await _member_scim_ids(db, g["id"]) == set()
    count = await db.execute(
        text("SELECT COUNT(*) FROM tiqora_scim_group WHERE scim_id = :s"), {"s": g["id"]}
    )
    assert count.scalar_one() == 0
    await _expect(get_group(db, tenant, g["id"], base=BASE, include_members=True), 404, None)
    await _expect(delete_group(db, tenant, g["id"]), 404, None)


# ---------------------------------------------------------------------------- read


@pytest.mark.asyncio
async def test_excluded_members(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    a, _ = await _agent(db, tenant, "a")
    g = await _group(db, tenant, "Support", a)
    lean = await get_group(db, tenant, g["id"], base=BASE, include_members=False)
    assert "members" not in lean and lean["displayName"] == "Support"
    full = await get_group(db, tenant, g["id"], base=BASE, include_members=True)
    assert [m["value"] for m in full["members"]] == [a]
    _, rows = await list_groups(db, tenant, None, PAGE, base=BASE, include_members=False)
    assert rows and all("members" not in row for row in rows)


@pytest.mark.asyncio
async def test_filter_display_name_case_insensitive(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    g1 = await _group(db, tenant, "Support", externalId="ext-s")
    await _group(db, tenant, "Sales")
    total, rows = await list_groups(
        db, tenant, ScimFilter("displayName", "SUPPORT"), PAGE, base=BASE
    )
    assert total == 1 and rows[0]["id"] == g1["id"]
    total, rows = await list_groups(db, tenant, ScimFilter("externalId", "ext-s"), PAGE, base=BASE)
    assert total == 1 and rows[0]["id"] == g1["id"]
    total, rows = await list_groups(db, tenant, ScimFilter("id", g1["id"]), PAGE, base=BASE)
    assert total == 1 and rows[0]["displayName"] == "Support"
    total, rows = await list_groups(db, tenant, ScimFilter("displayName", "nope"), PAGE, base=BASE)
    assert (total, rows) == (0, [])
    await _expect(
        list_groups(db, tenant, ScimFilter("userName", "x"), PAGE, base=BASE), 400, "invalidFilter"
    )


@pytest.mark.asyncio
async def test_list_pagination(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    for n in ("G1", "G2", "G3"):
        await _group(db, tenant, n)
    page = ListParams(start_index=2, count=1, attributes=None, excluded=frozenset())
    total, rows = await list_groups(db, tenant, None, page, base=BASE)
    assert total == 3 and [r["displayName"] for r in rows] == ["G2"]
    zero = ListParams(start_index=1, count=0, attributes=None, excluded=frozenset())
    assert await list_groups(db, tenant, None, zero, base=BASE) == (3, [])


# ---------------------------------------------------------------------------- limits


@pytest.mark.asyncio
async def test_name_and_external_id_validation(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    for bad in (
        {},
        {"displayName": "  "},
        {"displayName": 5},
        {"displayName": "x" * 256},
        {"displayName": "ok", "externalId": "e" * 256},
        {"displayName": "bad\ud800name"},
        {"displayName": "ok", "members": "a"},
        {"displayName": "ok", "members": [{"nope": 1}]},
    ):
        await _expect(
            create_group(db, tenant, bad, base=BASE, max_members=MAXM), 400, "invalidValue"
        )
    ok = await _group(db, tenant, "x" * 255, externalId="e" * 255)
    assert len(ok["displayName"]) == 255 and len(ok["externalId"]) == 255
    g = await _group(db, tenant, "Support")
    await _expect(
        replace_group(db, tenant, g["id"], {"displayName": "x" * 256}, base=BASE, max_members=MAXM),
        400,
        "invalidValue",
    )
    for path, value in (("displayName", "y" * 256), ("externalId", "z" * 256)):
        await _expect(
            patch_group(
                db,
                tenant,
                g["id"],
                _ops({"op": "Replace", "path": path, "value": value}),
                base=BASE,
                max_members=MAXM,
            ),
            400,
            "invalidValue",
        )


@pytest.mark.asyncio
async def test_member_limit_400(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    ids = [str(uuid.uuid4()) for _ in range(MAXM + 1)]
    await _expect(
        create_group(db, tenant, _doc("Big", *ids), base=BASE, max_members=MAXM),
        400,
        "invalidValue",
    )
    g = await _group(db, tenant, "Support")
    await _expect(
        replace_group(db, tenant, g["id"], _doc("Big", *ids), base=BASE, max_members=MAXM),
        400,
        "invalidValue",
    )
    ops = _ops({"op": "Add", "path": "members", "value": _members(*ids)})
    await _expect(
        patch_group(db, tenant, g["id"], ops, base=BASE, max_members=MAXM), 400, "invalidValue"
    )
    just = [str(uuid.uuid4()) for _ in range(MAXM)]
    r = await create_group(db, tenant, _doc("Edge", *just), base=BASE, max_members=MAXM)
    assert r.ignored_members == MAXM and r.resource["members"] == []


@pytest.mark.asyncio
async def test_put_group_5000_members_is_fast(db: AsyncSession) -> None:
    tenant = await _tenant(db)
    now = utc_now()
    rows = [
        {
            "provider_id": tenant.provider.id,
            "user_kind": "agent",
            "scim_id": str(uuid.uuid4()),
            "user_id": 2_000_000_000 + i,
            "user_name": f"sc63g.bulk{i}@example.test",
            "user_name_fold": f"sc63g.bulk{i}@example.test",
            "user_name_fold_hash": f"{i:064x}",
            "deleted": False,
            "version": 1,
            "created": now,
            "changed": now,
        }
        for i in range(MAXM)
    ]
    await db.execute(insert(TiqoraScimUser), rows)
    g = await _group(db, tenant, "Bulk")
    started = time.monotonic()
    r = await replace_group(
        db,
        tenant,
        g["id"],
        _doc("Bulk", *[row["scim_id"] for row in rows]),
        base=BASE,
        max_members=MAXM,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 10, elapsed
    assert r.ignored_members == 0 and len(r.resource["members"]) == MAXM
    members = await db.execute(
        text(
            "SELECT COUNT(*) FROM tiqora_scim_group_member m JOIN tiqora_scim_group g"
            " ON g.id = m.group_id WHERE g.scim_id = :s"
        ),
        {"s": g["id"]},
    )
    assert members.scalar_one() == MAXM
    assert len(await _pending(db)) == MAXM
    # and back down to nothing
    gone = await replace_group(db, tenant, g["id"], _doc("Bulk"), base=BASE, max_members=MAXM)
    assert len(gone.affected_user_ids) == MAXM and gone.resource["members"] == []
