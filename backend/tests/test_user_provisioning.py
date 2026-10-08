"""One write path for Znuny agents, roles, groups and customers (wave 1, Task 16).

DB, both dialects. Seed band 47300-47349 (agent ids, group/role ids), customer logins
``idp.prov-*@example.test`` (company ``prov-co``; example.org is rejected by Znuny's default
CheckEmailInvalidAddress). Invalidation rows are counted before/after (shared table).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import make_provider
from tests.test_identity_store import _to_async_url
from tiqora.api.v1.admin import customers as admin_customers
from tiqora.api.v1.admin import users as admin_users
from tiqora.api.v1.admin.common import (
    CUSTOMER_USER_CACHE_TYPES,
    USER_CACHE_TYPES,
    USER_GROUP_CACHE_TYPES,
    USER_ROLE_CACHE_TYPES,
)
from tiqora.api.v1.admin.schemas import (
    CustomerUserAdminCreate,
    GroupAssignment,
    UserCreate,
    UserUpdate,
)
from tiqora.config import Settings
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain import user_provisioning as up
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.user_delete import delete_user_rows
from tiqora.gdpr.erasure import run_erasure
from tiqora.identity.links import upsert_link

pytestmark = pytest.mark.db

NOW = datetime(2020, 1, 1, 12, 0, 0)
BAND = (47300, 47349)
ACTOR = 47300
ROLE_OK, ROLE_OFF = 47301, 47302
GROUP_A, GROUP_B = 47303, 47304
CUSTOMER_LIKE = "idp.prov-%@example.test"
_URLS: set[str] = set()
_INVAL_MARK: dict[str, int] = {}  # url -> MAX(id) of tiqora_cache_invalidation before the module


def _stmts(url: str) -> tuple[tuple[str, dict[str, object]], ...]:
    band = {"lo": BAND[0], "hi": BAND[1], "p": "prov-%"}
    mine = "(SELECT id FROM users WHERE login LIKE :p OR id BETWEEN :lo AND :hi)"
    return (
        (
            "DELETE FROM tiqora_gdpr_backup WHERE job_id IN"
            " (SELECT id FROM tiqora_gdpr_job WHERE actor = :actor)",
            {"actor": "prov-test"},
        ),
        ("DELETE FROM tiqora_gdpr_job WHERE actor = :actor", {"actor": "prov-test"}),
        ("DELETE FROM tiqora_gdpr_audit WHERE actor = :actor", {"actor": "prov-test"}),
        (
            "DELETE FROM tiqora_identity_link WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE :s)",
            {"s": "prov-%"},
        ),
        (
            "DELETE FROM tiqora_identity_managed_assignment WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE :s)",
            {"s": "prov-%"},
        ),
        ("DELETE FROM tiqora_identity_provider WHERE slug LIKE :s", {"s": "prov-%"}),
        (
            "DELETE FROM tiqora_cache_invalidation WHERE id > :mx",
            {"mx": _INVAL_MARK.get(url, 2**62)},
        ),
        ("DELETE FROM customer_user WHERE login LIKE :c", {"c": CUSTOMER_LIKE}),
        ("DELETE FROM customer_user WHERE customer_id = :cid", {"cid": "prov-co"}),
        (f"DELETE FROM group_user WHERE user_id IN {mine}", band),
        (f"DELETE FROM role_user WHERE user_id IN {mine}", band),
        (f"DELETE FROM user_preferences WHERE user_id IN {mine}", band),
        ("DELETE FROM users WHERE login LIKE :p AND id <> :a", {"p": "prov-%", "a": ACTOR}),
        ("DELETE FROM users WHERE id BETWEEN :lo AND :hi", band),
        ("DELETE FROM roles WHERE id BETWEEN :lo AND :hi", band),
        ("DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi", band),
    )


def _run(url: str, stmts: tuple[tuple[str, dict[str, object]], ...]) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in stmts:
                conn.execute(text(stmt), params)
    finally:
        engine.dispose()


def _seed(url: str) -> None:
    _run(url, ())  # make sure the tiqora tables exist
    if url not in _INVAL_MARK:
        engine = create_engine(url)
        try:
            with engine.begin() as conn:
                row = conn.execute(
                    text("SELECT COALESCE(MAX(id), 0) FROM tiqora_cache_invalidation")
                )
                _INVAL_MARK[url] = int(row.scalar_one())
        finally:
            engine.dispose()
    _run(url, _stmts(url))
    seed: list[tuple[str, dict[str, object]]] = [
        (
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
            " create_by, change_time, change_by) VALUES (:id, 'prov-actor', 'x', 'A', 'B', 1,"
            " :t, 1, :t, 1)",
            {"id": ACTOR, "t": NOW},
        )
    ]
    for rid, valid in ((ROLE_OK, 1), (ROLE_OFF, 2)):
        seed.append(
            (
                "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:id, :n, :v, :t, 1, :t, 1)",
                {"id": rid, "n": f"prov-role-{rid}", "v": valid, "t": NOW},
            )
        )
    for gid in (GROUP_A, GROUP_B):
        seed.append(
            (
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)",
                {"id": gid, "n": f"prov-group-{gid}", "t": NOW},
            )
        )
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for stmt, params in seed:
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
async def session(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _seed(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
        await s.rollback()
    await engine.dispose()
    _run(url, _stmts(url))


async def _inval(session: AsyncSession, types: tuple[str, ...]) -> int:
    count = 0
    for cache_type in types:
        count += int(
            (
                await session.execute(
                    text("SELECT COUNT(*) FROM tiqora_cache_invalidation WHERE cache_type = :c"),
                    {"c": cache_type},
                )
            ).scalar_one()
        )
    return count


async def _agent(session: AsyncSession, login: str, **kw: object) -> Users:
    args: dict[str, object] = {
        "login": login,
        "first_name": "Ada",
        "last_name": "Example",
        "email": f"{login}@example.test",
        "mobile": None,
        "title": None,
        "pw_hash": "x",
        "valid_id": 1,
        "actor_id": ACTOR,
    }
    args.update(kw)
    user = await up.create_agent(session, **args)  # type: ignore[arg-type]
    await session.commit()
    return user


async def test_create_agent_writes_row_email_and_invalidation(session: AsyncSession) -> None:
    before = await _inval(session, USER_CACHE_TYPES)
    user = await _agent(session, "prov-one", mobile="+49 30 1")
    row = (
        (
            await session.execute(
                text(
                    "SELECT create_time, create_by, change_time, change_by FROM users WHERE id=:i"
                ),
                {"i": user.id},
            )
        )
        .mappings()
        .one()
    )
    assert row["create_by"] == row["change_by"] == ACTOR
    assert abs(row["create_time"] - datetime.utcnow()) < timedelta(minutes=2)  # noqa: DTZ003
    assert row["create_time"].tzinfo is None
    prefs = dict(
        (
            await session.execute(
                text(
                    "SELECT preferences_key, preferences_value FROM user_preferences"
                    " WHERE user_id=:i"
                ),
                {"i": user.id},
            )
        ).all()
    )
    assert {k: (v.decode() if isinstance(v, bytes) else v) for k, v in prefs.items()} == {
        "UserEmail": "prov-one@example.test",
        "UserMobile": "+49 30 1",
    }
    assert await _inval(session, USER_CACHE_TYPES) - before == len(set(USER_CACHE_TYPES))


async def test_create_agent_rejects_bad_email_duplicate_login_and_missing_email(
    session: AsyncSession,
) -> None:
    await _agent(session, "prov-dup")
    with pytest.raises(up.ProvisioningError) as bad:
        await _agent(session, "prov-bad", email="not-an-address")
    assert bad.value.code == "email_invalid"
    with pytest.raises(up.ProvisioningError) as dup:
        await _agent(session, "PROV-DUP")
    assert dup.value.code == "login_exists"
    with pytest.raises(up.ProvisioningError) as none:
        await _agent(session, "prov-none", email=None, require_email=True)
    assert none.value.code == "email_required"
    await session.rollback()
    # an invalid account is not syntax-checked (UserAdd checks only valid ones)
    ok = await _agent(session, "prov-off", email="whatever", valid_id=2)
    assert ok.id


async def test_grant_role_twice_and_revoke_missing(session: AsyncSession) -> None:
    user = await _agent(session, "prov-role")
    before = await _inval(session, USER_ROLE_CACHE_TYPES)
    assert await up.grant_role(session, user_id=user.id, role_id=ROLE_OK, actor_id=ACTOR) is True
    assert await up.grant_role(session, user_id=user.id, role_id=ROLE_OK, actor_id=ACTOR) is False
    await session.commit()
    rows = (
        await session.execute(
            text("SELECT user_id, role_id FROM role_user WHERE user_id=:u"), {"u": user.id}
        )
    ).all()
    assert [tuple(r) for r in rows] == [(user.id, ROLE_OK)]
    assert await up.current_roles(session, user.id) == frozenset({ROLE_OK})
    mid = await _inval(session, USER_ROLE_CACHE_TYPES)
    assert mid - before == len(set(USER_ROLE_CACHE_TYPES))

    assert await up.revoke_role(session, user_id=user.id, role_id=ROLE_OK) is True
    await session.commit()
    after_revoke = await _inval(session, USER_ROLE_CACHE_TYPES)
    assert await up.revoke_role(session, user_id=user.id, role_id=ROLE_OK) is False
    assert await _inval(session, USER_ROLE_CACHE_TYPES) == after_revoke
    assert await up.current_roles(session, user.id) == frozenset()


async def test_group_permissions_follow_znuny_replace_semantics(session: AsyncSession) -> None:
    user = await _agent(session, "prov-group")
    g = dict(user_id=user.id, group_id=GROUP_A, actor_id=ACTOR)
    assert await up.grant_group_permission(session, permission_key="rw", **g) is True
    assert await up.current_group_perms(session, user.id) == frozenset({(GROUP_A, "rw")})
    # under rw every other key is already included -> no-op, rw stays alone
    assert await up.grant_group_permission(session, permission_key="ro", **g) is False
    assert await up.current_group_perms(session, user.id) == frozenset({(GROUP_A, "rw")})

    # ro + note stack until rw arrives, which replaces the whole set
    other = dict(user_id=user.id, group_id=GROUP_B, actor_id=ACTOR)
    assert await up.grant_group_permission(session, permission_key="ro", **other) is True
    assert await up.grant_group_permission(session, permission_key="note", **other) is True
    assert await up.current_group_perms(session, user.id) == frozenset(
        {(GROUP_A, "rw"), (GROUP_B, "ro"), (GROUP_B, "note")}
    )
    assert await up.grant_group_permission(session, permission_key="rw", **other) is True
    assert await up.current_group_perms(session, user.id) == frozenset(
        {(GROUP_A, "rw"), (GROUP_B, "rw")}
    )
    # replacing with an explicit set (Znuny PermissionGroupUserAdd)
    assert (
        await up.set_group_permissions(session, permission_keys={"ro", "move_into"}, **other)
        is True
    )
    assert (
        await up.set_group_permissions(session, permission_keys={"move_into", "ro"}, **other)
        is False
    )
    await session.commit()
    assert await up.current_group_perms(session, user.id) == frozenset(
        {(GROUP_A, "rw"), (GROUP_B, "ro"), (GROUP_B, "move_into")}
    )


async def test_revoke_group_permission_and_invalidation(session: AsyncSession) -> None:
    user = await _agent(session, "prov-grev")
    g = dict(user_id=user.id, group_id=GROUP_A)
    await up.grant_group_permission(session, permission_key="rw", actor_id=ACTOR, **g)
    await session.commit()
    before = await _inval(session, USER_GROUP_CACHE_TYPES)
    assert await up.revoke_group_permission(session, permission_key="ro", **g) is False
    assert await _inval(session, USER_GROUP_CACHE_TYPES) == before
    assert await up.revoke_group_permission(session, permission_key="rw", **g) is True
    await session.commit()
    assert await _inval(session, USER_GROUP_CACHE_TYPES) - before == len(
        set(USER_GROUP_CACHE_TYPES)
    )
    assert await up.current_group_perms(session, user.id) == frozenset()


async def test_valid_ids_exclude_invalid_rows(session: AsyncSession) -> None:
    roles = await up.valid_role_ids(session)
    assert ROLE_OK in roles and ROLE_OFF not in roles
    assert {GROUP_A, GROUP_B} <= await up.valid_group_ids(session)


async def test_update_agent_profile_identical_values_is_noop(session: AsyncSession) -> None:
    user = await _agent(session, "prov-upd", mobile="123")
    await session.execute(
        text("UPDATE users SET change_time = :t, change_by = 1 WHERE id = :i"),
        {"t": NOW, "i": user.id},
    )
    await session.commit()
    await session.refresh(user)
    before = await _inval(session, USER_CACHE_TYPES)
    same = await up.update_agent_profile(
        session,
        user,
        first_name="Ada",
        last_name="Example",
        email="prov-upd@example.test",
        mobile="123",
        actor_id=ACTOR,
    )
    assert same is False
    assert user.change_time == NOW and user.change_by == 1
    assert await _inval(session, USER_CACHE_TYPES) == before

    changed = await up.update_agent_profile(
        session, user, first_name="Grace", last_name=None, email=None, mobile="", actor_id=ACTOR
    )
    await session.commit()
    assert changed is True
    assert user.first_name == "Grace" and user.change_by == ACTOR and user.change_time > NOW
    gone = (
        await session.execute(
            text(
                "SELECT 1 FROM user_preferences WHERE user_id=:i AND preferences_key='UserMobile'"
            ),
            {"i": user.id},
        )
    ).first()
    assert gone is None
    assert await _inval(session, USER_CACHE_TYPES) - before == len(set(USER_CACHE_TYPES))


async def test_create_customer_user_writes_row_and_invalidates(session: AsyncSession) -> None:
    before = await _inval(session, CUSTOMER_USER_CACHE_TYPES)
    cu = await up.create_customer_user(
        session,
        login="idp.prov-1@example.test",
        email="idp.prov-1@example.test",
        customer_id="prov-co",
        first_name="Kim",
        last_name="Kunde",
        pw_hash=None,
        actor_id=ACTOR,
        phone="+49 1",
    )
    await session.commit()
    row = (
        (
            await session.execute(
                text(
                    "SELECT email, customer_id, phone, pw, valid_id, create_by, change_by"
                    " FROM customer_user WHERE id=:i"
                ),
                {"i": cu.id},
            )
        )
        .mappings()
        .one()
    )
    assert dict(row) == {
        "email": "idp.prov-1@example.test",
        "customer_id": "prov-co",
        "phone": "+49 1",
        "pw": None,
        "valid_id": 1,
        "create_by": ACTOR,
        "change_by": ACTOR,
    }
    assert await _inval(session, CUSTOMER_USER_CACHE_TYPES) - before == len(
        set(CUSTOMER_USER_CACHE_TYPES)
    )
    assert (
        await up.update_customer_profile(
            session,
            cu,
            first_name="Kim",
            last_name="Kunde",
            email=None,
            mobile=None,
            actor_id=ACTOR,
        )
        is False
    )
    assert (
        await up.update_customer_profile(
            session, cu, first_name=None, last_name=None, email=None, mobile="+49 2", actor_id=ACTOR
        )
        is True
    )
    await session.commit()


async def _link_counts(session: AsyncSession, provider_id: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for kind in ("agent", "customer"):
        out[kind] = int(
            (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM tiqora_identity_link"
                        " WHERE provider_id = :p AND user_kind = :k"
                    ),
                    {"p": provider_id, "k": kind},
                )
            ).scalar_one()
        )
    return out


async def test_user_delete_purges_identity_links_and_managed_assignments(
    session: AsyncSession,
) -> None:
    user = await _agent(session, "prov-del")
    rec = await make_provider(session, slug="prov-del", settings=Settings(secret_key="k" * 40))
    # a customer link that happens to share the numeric id must survive the agent delete
    await upsert_link(
        session,
        provider_id=rec.id,
        user_kind="agent",
        subject="s-agent",
        user_id=user.id,
        user_login="prov-del",
    )
    await upsert_link(
        session,
        provider_id=rec.id,
        user_kind="customer",
        subject="s-cust",
        user_id=user.id,
        user_login="idp.prov-x@example.test",
    )
    await session.execute(
        text(
            "INSERT INTO tiqora_identity_managed_assignment"
            " (provider_id, user_id, kind, target_id, permission_key, created)"
            " VALUES (:p, :u, 'role', :r, '', :t)"
        ),
        {"p": rec.id, "u": user.id, "r": ROLE_OK, "t": NOW},
    )
    await session.commit()
    assert await _link_counts(session, rec.id) == {"agent": 1, "customer": 1}

    await delete_user_rows(session, user.id)
    await session.commit()

    assert await _link_counts(session, rec.id) == {"agent": 0, "customer": 1}
    left = (
        await session.execute(
            text("SELECT COUNT(*) FROM tiqora_identity_managed_assignment WHERE user_id = :u"),
            {"u": user.id},
        )
    ).scalar_one()
    assert int(left) == 0


@pytest.mark.parametrize("mode", ["anonymize", "delete"])
async def test_customer_erasure_purges_identity_links(session: AsyncSession, mode: str) -> None:
    login = f"idp.prov-{mode}@example.test"
    cu = await up.create_customer_user(
        session,
        login=login,
        email=login,
        customer_id="prov-co",
        first_name="Er",
        last_name="Asure",
        pw_hash=None,
        actor_id=ACTOR,
    )
    rec = await make_provider(
        session, slug=f"prov-er-{mode}", audience="customer", settings=Settings(secret_key="k" * 40)
    )
    await upsert_link(
        session,
        provider_id=rec.id,
        user_kind="customer",
        subject="s-c",
        user_id=cu.id,
        user_login=login,
    )
    # an agent link with the same numeric id is unrelated and must stay
    await upsert_link(
        session,
        provider_id=rec.id,
        user_kind="agent",
        subject="s-a",
        user_id=cu.id,
        user_login="prov-a",
    )
    await session.commit()
    assert await _link_counts(session, rec.id) == {"agent": 1, "customer": 1}

    bind = session.bind
    assert bind is not None
    factory = async_sessionmaker(bind, expire_on_commit=False)  # type: ignore[arg-type]
    await run_erasure(
        factory,
        Settings(),
        customer_user_ids=[cu.id],
        mode=mode,  # type: ignore[arg-type]
        seed=3,
        force_parallel=True,
        actor="prov-test",
    )

    await session.rollback()
    assert await _link_counts(session, rec.id) == {"agent": 1, "customer": 0}


# --- e-mail rules (Znuny CheckItem::CheckEmail driven by SysConfig) ---------------------


class _Cfg:
    """Stand-in for SysConfig with explicit values; absent names fall back to the call default."""

    values: dict[str, object] = {}

    def __init__(self, session: object) -> None:
        pass

    async def get(self, name: str, default: object = None) -> object:
        return self.values.get(name, default)


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> type[_Cfg]:
    _Cfg.values = {}
    monkeypatch.setattr(up, "SysConfig", _Cfg)
    return _Cfg


@pytest.mark.parametrize(
    ("address", "ok"),
    [
        ("agent@example.test", True),
        ("first.last@mail.example.test", True),
        ("root@localhost", True),  # CheckEmailValidAddress default whitelist
        ("ADMIN@LOCALHOST", True),  # whitelist is case-insensitive
        ("plain", False),
        ("a@b", False),  # no dotted domain
        ("a..b@example.test", False),  # Znuny rule (\.\.)
        ("a.@example.test", False),  # Znuny rule (\.@)
        (".a@example.test", False),  # leading dot
        ("a b@example.test", False),
        ("a@@example.test", False),
        ("x@example.org", False),  # CheckEmailInvalidAddress default @(example)\.(..|...)$
        ("x@example.com", False),
        ("x@sub.example.de", True),
    ],
)
async def test_check_email_defaults(cfg: type[_Cfg], address: str, ok: bool) -> None:
    assert await up.check_email(None, address) is ok  # type: ignore[arg-type]


async def test_check_email_switch_off_and_custom_patterns(cfg: type[_Cfg]) -> None:
    cfg.values = {"CheckEmailAddresses": 0}
    assert await up.check_email(None, "not an address") is True  # type: ignore[arg-type]
    cfg.values = {"CheckEmailAddresses": "0"}
    assert await up.check_email(None, "x@example.org") is True  # type: ignore[arg-type]
    cfg.values = {"CheckEmailValidAddress": r"^svc@intranet$"}
    assert await up.check_email(None, "svc@intranet") is True  # type: ignore[arg-type]
    assert await up.check_email(None, "root@localhost") is False  # type: ignore[arg-type]
    cfg.values = {"CheckEmailInvalidAddress": r"@blocked\.test$"}
    assert await up.check_email(None, "x@example.org") is True  # type: ignore[arg-type]
    assert await up.check_email(None, "x@BLOCKED.test") is False  # type: ignore[arg-type]


async def test_email_is_validated_and_stored_trimmed(session: AsyncSession) -> None:
    user = await _agent(session, "prov-trim", email="  prov-trim@example.test ")
    prefs = (
        await session.execute(
            text(
                "SELECT preferences_value FROM user_preferences"
                " WHERE user_id=:i AND preferences_key='UserEmail'"
            ),
            {"i": user.id},
        )
    ).scalar_one()
    assert (prefs.decode() if isinstance(prefs, bytes) else prefs) == "prov-trim@example.test"


async def test_create_agent_requires_names(session: AsyncSession) -> None:
    for kw in ({"first_name": ""}, {"last_name": "  "}):
        with pytest.raises(up.ProvisioningError) as exc:
            await _agent(session, "prov-noname", **kw)
        assert exc.value.code == "name_required"
    await session.rollback()


# --- customers: e-mail uniqueness --------------------------------------------------------


async def _customer(session: AsyncSession, login: str, email: str, **kw: object) -> object:
    return await up.create_customer_user(
        session,
        login=login,
        email=email,
        customer_id="prov-co",
        first_name="K",
        last_name="U",
        pw_hash=None,
        actor_id=ACTOR,
        **kw,  # type: ignore[arg-type]
    )


async def test_customer_email_must_be_unique_even_for_invalid_users(
    session: AsyncSession,
) -> None:
    await _customer(session, "idp.prov-u1@example.test", "Shared@example.test", valid_id=2)
    await session.commit()
    with pytest.raises(up.ProvisioningError) as exc:
        await _customer(session, "idp.prov-u2@example.test", "shared@EXAMPLE.test")
    assert exc.value.code == "email_in_use"
    assert "bereits" in str(exc.value)
    await session.rollback()
    other = await _customer(session, "idp.prov-u3@example.test", "other@example.test")
    with pytest.raises(up.ProvisioningError):  # update path checks too, excluding itself
        await up.update_customer_profile(
            session,
            other,
            first_name=None,
            last_name=None,  # type: ignore[arg-type]
            email="shared@example.test",
            mobile=None,
            actor_id=ACTOR,
        )
    await session.rollback()


async def test_customer_email_uniqueness_honours_setting(
    session: AsyncSession, cfg: type[_Cfg]
) -> None:
    await _customer(session, "idp.prov-s1@example.test", "dup@example.test")
    await session.commit()
    cfg.values = {"CustomerUser": {"CustomerUserEmailUniqCheck": 0}}
    await _customer(session, "idp.prov-s2@example.test", "dup@example.test")
    await session.commit()


# --- group keys: custom permission types survive -----------------------------------------


async def test_grant_keeps_custom_keys_and_rejects_unknown_new_key(
    session: AsyncSession,
) -> None:
    user = await _agent(session, "prov-custom")
    await session.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (:u, :g, 'site_key', :t, 1, :t, 1)"
        ),
        {"u": user.id, "g": GROUP_A, "t": NOW},
    )
    await session.commit()
    assert await up.grant_group_permission(
        session, user_id=user.id, group_id=GROUP_A, permission_key="ro", actor_id=ACTOR
    )
    assert await up.current_group_perms(session, user.id) == frozenset(
        {(GROUP_A, "site_key"), (GROUP_A, "ro")}
    )
    with pytest.raises(up.ProvisioningError):
        await up.grant_group_permission(
            session, user_id=user.id, group_id=GROUP_A, permission_key="bogus", actor_id=ACTOR
        )
    await session.rollback()


# --- managed assignments are agent-only ----------------------------------------------------


async def test_managed_assignment_rejects_customers(session: AsyncSession) -> None:
    rec = await make_provider(session, slug="prov-mgd", settings=Settings(secret_key="k" * 40))
    with pytest.raises(up.ProvisioningError) as exc:
        await up.record_managed_assignment(
            session,
            provider_id=rec.id,
            user_kind="customer",
            user_id=5,
            kind="role",
            target_id=ROLE_OK,
            permission_key="",
        )
    assert exc.value.code == "managed_agent_only"
    row = await up.record_managed_assignment(
        session,
        provider_id=rec.id,
        user_kind="agent",
        user_id=ACTOR,
        kind="role",
        target_id=ROLE_OK,
        permission_key="",
    )
    assert row.id
    await session.commit()


# --- HTTP-level behaviour of the refactored admin routes -----------------------------------


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=ACTOR, login="prov-actor", first_name="A", last_name="B", auth_method="session"
    )


def _create(login: str, email: str) -> UserCreate:
    return UserCreate(
        login=login, password="s3cret-pw-1234", first_name="Ro", last_name="Ute", email=email
    )


async def test_route_duplicate_login_is_409_and_invalid_email_is_422(
    session: AsyncSession,
) -> None:
    settings = Settings(secret_key="k" * 40)
    created = await admin_users.create_user(
        _create("prov-http", "prov-http@example.test"), _admin(), session, settings
    )
    assert created.id
    with pytest.raises(HTTPException) as dup:
        await admin_users.create_user(
            _create("PROV-HTTP", "other@example.test"), _admin(), session, settings
        )
    assert dup.value.status_code == 409
    await session.rollback()
    with pytest.raises(HTTPException) as bad:
        await admin_users.create_user(
            _create("prov-http2", "x@example.org"), _admin(), session, settings
        )
    assert bad.value.status_code == 422
    await session.rollback()
    with pytest.raises(HTTPException) as upd:
        await admin_users.update_user(
            created.id,
            UserUpdate(email="nope"),
            None,
            _admin(),
            session,
            settings,  # type: ignore[arg-type]
        )
    assert upd.value.status_code == 422
    await session.rollback()


async def test_route_update_user_enqueues_user_invalidation_once(session: AsyncSession) -> None:
    settings = Settings(secret_key="k" * 40)
    created = await admin_users.create_user(
        _create("prov-once", "prov-once@example.test"), _admin(), session, settings
    )
    before = await _inval(session, ("User",))
    await admin_users.update_user(
        created.id,
        UserUpdate(first_name="Changed"),
        None,
        _admin(),
        session,
        settings,  # type: ignore[arg-type]
    )
    assert await _inval(session, ("User",)) - before == 1


async def test_route_put_groups_rw_collapses_stacked_keys(session: AsyncSession) -> None:
    created = await admin_users.create_user(
        _create("prov-grp", "prov-grp@example.test"),
        _admin(),
        session,
        Settings(secret_key="k" * 40),
    )
    for key in ("ro", "note", "rw"):
        await admin_users.assign_group(
            created.id, GroupAssignment(group_id=GROUP_A, permission_key=key), _admin(), session
        )
    assert await up.current_group_perms(session, created.id) == frozenset({(GROUP_A, "rw")})


async def test_route_customer_duplicate_email_is_409(session: AsyncSession) -> None:
    def body(login: str) -> CustomerUserAdminCreate:
        return CustomerUserAdminCreate(
            login=login,
            email="same@example.test",
            customer_id="prov-co",
            first_name="K",
            last_name="U",
        )

    await admin_customers.create_customer_user(body("idp.prov-h1@example.test"), _admin(), session)
    with pytest.raises(HTTPException) as exc:
        await admin_customers.create_customer_user(
            body("idp.prov-h2@example.test"), _admin(), session
        )
    assert exc.value.status_code == 409
    await session.rollback()
