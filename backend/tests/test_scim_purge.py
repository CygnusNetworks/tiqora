"""Account deletion and GDPR erasure purge the SCIM rows (agents: ``delete_user_rows``, customers:
``run_erasure``); the audit rows in ``tiqora_scim_event`` stay.

Seed band 63350-63359: accounts are provisioned through the SCIM services (auto ids, login prefix
``sc63p.``), providers use the slug prefix ``sc63p``. Shared agent/customer ids are made up by
inserting a second mapping row of the other kind with the same numeric ``user_id``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._scim_helpers import make_tenant, scim_cleanup_statements
from tests._scim_helpers import seed_scim_provider as seed_provider
from tests.test_identity_store import _to_async_url
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.user_delete import delete_user_rows
from tiqora.gdpr.erasure import run_erasure
from tiqora.identity.scim.groups import create_group
from tiqora.identity.scim.role_sync import enqueue_role_sync
from tiqora.identity.scim.tokens import ScimTenant
from tiqora.identity.scim.users import create_user
from tiqora.identity.types import UserKind, utc_now

pytestmark = pytest.mark.db

BASE = "https://helpdesk.example.test/scim/v2"
ACTOR = "sc63p-test"
_URLS: set[str] = set()
_MARK: dict[str, int] = {}
_CUSTOMERS: set[int] = set()  # anonymize renames the login, so the login prefix misses them


def _stmts(url: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    return (
        ("DELETE FROM tiqora_cache_invalidation WHERE id > :mx", {"mx": _MARK.get(url, 2**62)}),
        *(("DELETE FROM customer_user WHERE id = :i", {"i": i}) for i in sorted(_CUSTOMERS)),
        (
            "DELETE FROM tiqora_gdpr_backup WHERE job_id IN"
            " (SELECT id FROM tiqora_gdpr_job WHERE actor = :a)",
            {"a": ACTOR},
        ),
        ("DELETE FROM tiqora_gdpr_job WHERE actor = :a", {"a": ACTOR}),
        ("DELETE FROM tiqora_gdpr_audit WHERE actor = :a", {"a": ACTOR}),
        *scim_cleanup_statements(slug_prefix="sc63p", login_like="sc63p.%"),
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
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()
    _run(url, _stmts(url))


async def _tenant(db: AsyncSession, kind: UserKind, slug: str) -> ScimTenant:
    rec = await seed_provider(db, audience=kind, slug=slug)
    return make_tenant(rec, kind)


async def _account(db: AsyncSession, tenant: ScimTenant, name: str) -> tuple[str, int]:
    login = f"sc63p.{name}@example.test"
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


async def _mirror(db: AsyncSession, tenant: ScimTenant, kind: UserKind, user_id: int) -> None:
    """A mapping row of the *other* kind that happens to share the numeric account id."""
    now = utc_now()
    await db.execute(
        text(
            "INSERT INTO tiqora_scim_user (provider_id, user_kind, scim_id, user_id, user_name,"
            " user_name_fold, user_name_fold_hash, deleted, version, created, changed)"
            " VALUES (:p, :k, :s, :u, :n, :n, :h, :d, 1, :t, :t)"
        ),
        {
            "p": tenant.provider.id,
            "k": kind,
            "s": f"00000000-0000-4000-8000-{user_id:012d}",
            "u": user_id,
            "n": f"sc63p.mirror-{kind}",
            "h": "0" * 64,
            "d": False,
            "t": now,
        },
    )


async def _count(db: AsyncSession, sql: str, **params: Any) -> int:
    await db.commit()  # fresh snapshot: the code under test may write through other sessions
    return int((await db.execute(text(sql), params)).scalar_one())


async def _scim_rows(db: AsyncSession, kind: str, user_id: int) -> int:
    return await _count(
        db,
        "SELECT COUNT(*) FROM tiqora_scim_user WHERE user_kind = :k AND user_id = :u",
        k=kind,
        u=user_id,
    )


async def _event(db: AsyncSession, provider_id: int, kind: str, user_id: int) -> None:
    await db.execute(
        text(
            "INSERT INTO tiqora_scim_event (provider_id, created, method, resource, operation,"
            " user_kind, user_id, status) VALUES (:p, :t, 'POST', 'Users', 'create', :k, :u, 201)"
        ),
        {"p": provider_id, "t": utc_now(), "k": kind, "u": user_id},
    )


@pytest.mark.asyncio
async def test_user_delete_removes_scim_rows_agent_only(db: AsyncSession) -> None:
    tenant = await _tenant(db, "agent", "sc63p-a")
    scim_id, uid = await _account(db, tenant, "gone")
    keep_scim_id, keep_uid = await _account(db, tenant, "stays")
    await _mirror(db, tenant, "customer", uid)  # customer with the same numeric id
    await create_group(
        db,
        tenant,
        {"displayName": "Support", "members": [{"value": scim_id}, {"value": keep_scim_id}]},
        base=BASE,
        max_members=5000,
    )
    await enqueue_role_sync(db, tenant.provider.id, [uid, keep_uid])
    await db.commit()
    members = "SELECT COUNT(*) FROM tiqora_scim_group_member WHERE member_id IN (SELECT id FROM tiqora_scim_user WHERE user_id = :u AND user_kind = 'agent')"  # noqa: E501
    assert await _scim_rows(db, "agent", uid) == 1
    assert await _scim_rows(db, "customer", uid) == 1
    assert await _count(db, members, u=uid) == 1

    await delete_user_rows(db, uid)
    await db.commit()

    assert await _scim_rows(db, "agent", uid) == 0
    assert await _scim_rows(db, "customer", uid) == 1  # the other kind survives
    assert await _count(db, members, u=uid) == 0
    assert (
        await _count(db, "SELECT COUNT(*) FROM tiqora_scim_pending_sync WHERE user_id = :u", u=uid)
        == 0
    )
    # the other agent keeps mapping, membership and queue entry
    assert await _scim_rows(db, "agent", keep_uid) == 1
    assert await _count(db, members, u=keep_uid) == 1
    assert (
        await _count(
            db, "SELECT COUNT(*) FROM tiqora_scim_pending_sync WHERE user_id = :u", u=keep_uid
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["anonymize", "delete"])
async def test_erasure_removes_customer_scim_row(db: AsyncSession, mode: str) -> None:
    tenant = await _tenant(db, "customer", f"sc63p-c-{mode}")
    _, cid = await _account(db, tenant, f"cust-{mode}")
    _, other = await _account(db, tenant, f"other-{mode}")
    _CUSTOMERS.update((cid, other))
    await _mirror(db, tenant, "agent", cid)  # agent with the same numeric id: unrelated
    await db.commit()
    assert await _scim_rows(db, "customer", cid) == 1

    bind = db.bind
    assert bind is not None
    factory = async_sessionmaker(bind, expire_on_commit=False)  # type: ignore[arg-type]
    await run_erasure(
        factory,
        Settings(),
        customer_user_ids=[cid],
        mode=mode,  # type: ignore[arg-type]
        seed=3,
        force_parallel=True,
        actor=ACTOR,
    )

    assert await _scim_rows(db, "customer", cid) == 0
    assert await _scim_rows(db, "agent", cid) == 1
    assert await _scim_rows(db, "customer", other) == 1


@pytest.mark.asyncio
async def test_events_survive(db: AsyncSession) -> None:
    tenant = await _tenant(db, "agent", "sc63p-e")
    _, uid = await _account(db, tenant, "evt")
    await _event(db, tenant.provider.id, "agent", uid)
    await db.commit()

    await delete_user_rows(db, uid)
    await db.commit()

    assert (
        await _count(
            db,
            "SELECT COUNT(*) FROM tiqora_scim_event WHERE provider_id = :p AND user_id = :u",
            p=tenant.provider.id,
            u=uid,
        )
        == 1
    )
