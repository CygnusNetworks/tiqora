"""Just-in-time provisioning and profile sync (wave 1, Task 17).

Seed band 47350-47399 (role/group ids, pre-seeded agents). JIT-created accounts get
auto-increment ids, so the module cleanup removes them by their ``idp.%`` logins.
Provider slugs use the ``idpjit-`` prefix; e-mail domain ``example.test`` (Znuny's
CheckEmailInvalidAddress default rejects example.org/com).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import cleanup_statements, make_provider, seed_agent, seed_customer
from tests.test_identity_store import _to_async_url
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.identity.config_models import AttributeMap, GroupGrant, JitDefaults
from tiqora.identity.login import (
    ExternalIdentity,
    LoginRejected,
    apply_login,
    plan_login,
    plan_to_dict,
)
from tiqora.identity.store import ProviderChanges, ProviderRecord, update_provider
from tiqora.znuny.password import verify_password

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-jit"
BAND = (47350, 47399)
SLUG = "idpjit-"
ROLE_ID = 47350
GROUP_ID = 47350
NOW = datetime(2026, 10, 8, 12, 0, 0)
_URLS: set[str] = set()
_INVAL_MARK: dict[str, int] = {}  # url -> MAX(id) of tiqora_cache_invalidation at module start
SETTINGS = Settings(environment="test", secret_key=KEY)
MAP = AttributeMap(
    login=["preferred_username"],
    email=["email"],
    first_name=["given_name"],
    last_name=["family_name"],
    mobile=["phone"],
)
_JIT_USERS = "(SELECT id FROM users WHERE login LIKE 'idp.%.4735%' OR login LIKE 'idp.%.4736%')"


def _stmts(url: str) -> tuple[tuple[str, dict[str, object]], ...]:
    by_login = tuple(
        (f"DELETE FROM {table} WHERE user_id IN {_JIT_USERS}", {})
        for table in (
            "tiqora_identity_link",
            "tiqora_identity_managed_assignment",
            "tiqora_user_auth_config",
            "user_preferences",
            "role_user",
            "group_user",
        )
    )
    return (
        *by_login,
        ("DELETE FROM users WHERE login LIKE 'idp.%.4735%' OR login LIKE 'idp.%.4736%'", {}),
        ("DELETE FROM role_user WHERE role_id = :r", {"r": ROLE_ID}),
        ("DELETE FROM group_user WHERE group_id = :g", {"g": GROUP_ID}),
        ("DELETE FROM roles WHERE id = :r", {"r": ROLE_ID}),
        ("DELETE FROM permission_groups WHERE id = :g", {"g": GROUP_ID}),
        (
            "DELETE FROM tiqora_cache_invalidation WHERE id > :mx",
            {"mx": _INVAL_MARK.get(url, 2**62)},
        ),
        *cleanup_statements(user_ids=BAND, slug_prefix=SLUG),
    )


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


@pytest_asyncio.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[AsyncSession]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        for stmt in (
            "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:r, 'idp-jit-role', 1, :t, 1, :t, 1)",
            "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
            " change_time, change_by) VALUES (:g, 'idp-jit-group', 1, :t, 1, :t, 1)",
        ):
            await s.execute(text(stmt), {"r": ROLE_ID, "g": GROUP_ID, "t": NOW})
        await s.commit()
        yield s
        await s.rollback()
    await engine.dispose()
    _clean(url)


def _ident(
    provider_id: int, subject: str, *, verified: bool | None = True, **attrs: str
) -> ExternalIdentity:
    return ExternalIdentity(
        provider_id=provider_id,
        subject=subject,
        attributes={k: (v,) for k, v in attrs.items()},
        email_verified=verified,
    )


async def _jit_provider(
    s: AsyncSession, slug: str, *, defaults: JitDefaults | None = None, **kw: object
) -> ProviderRecord:
    p = await make_provider(
        s,
        slug=f"{SLUG}{slug}",
        settings=SETTINGS,
        attribute_map=MAP,
        jit_enabled=True,
        **kw,  # type: ignore[arg-type]
    )
    if defaults is not None:
        p = await update_provider(
            s, p.id, ProviderChanges(jit_defaults=defaults), secret_key=KEY, actor_id=1
        )
    return p


async def _count(s: AsyncSession, table: str) -> int:
    return int((await s.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one())


async def _pref(s: AsyncSession, user_id: int, key: str) -> str | None:
    row = (
        await s.execute(
            text(
                "SELECT preferences_value FROM user_preferences"
                " WHERE user_id = :u AND preferences_key = :k"
            ),
            {"u": user_id, "k": key},
        )
    ).first()
    if row is None:
        return None
    v = row[0]
    return v.decode() if isinstance(v, bytes) else str(v)


async def _login(s: AsyncSession, p: ProviderRecord, ident: ExternalIdentity, **kw: str):  # type: ignore[no-untyped-def]
    plan = await plan_login(s, p, ident, user_kind=kw.get("kind", "agent"), settings=SETTINGS)  # type: ignore[arg-type]
    return plan, await apply_login(s, p, ident, plan, settings=SETTINGS)


async def test_jit_creates_agent_with_unusable_password(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(
        s,
        "agent",
        defaults=JitDefaults(
            initial_role_ids=[ROLE_ID],
            initial_groups=[GroupGrant(group_id=GROUP_ID, permissions=["ro", "note"])],
        ),
    )
    ident = _ident(
        p.id,
        "sub-new",
        preferred_username="idp.neu.47351",
        email="neu@example.test",
        given_name="Nora",
        family_name="Neu",
        phone="+49 30 1234",
    )
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.matched_by, plan.user_id) == ("create", "jit", None)
    uid, login = await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert login == "idp.neu.47351"
    row = (
        await s.execute(
            text(
                "SELECT pw, valid_id, first_name, last_name, create_by, change_by"
                " FROM users WHERE id = :u"
            ),
            {"u": uid},
        )
    ).one()
    assert (row[1], row[2], row[3], row[4], row[5]) == (1, "Nora", "Neu", 1, 1)
    for guess in ("", "x", "idp.neu.47351", "neu@example.test"):
        assert not verify_password(guess, row[0])
    assert await _pref(s, uid, "UserEmail") == "neu@example.test"
    assert await _pref(s, uid, "UserMobile") == "+49 30 1234"
    eligible = (
        await s.execute(
            text("SELECT sso_eligible FROM tiqora_user_auth_config WHERE user_id = :u"), {"u": uid}
        )
    ).scalar_one()
    assert bool(eligible) is True
    roles = (
        await s.execute(text("SELECT role_id FROM role_user WHERE user_id = :u"), {"u": uid})
    ).all()
    assert [r[0] for r in roles] == [ROLE_ID]
    keys = (
        await s.execute(
            text("SELECT permission_key FROM group_user WHERE user_id = :u AND group_id = :g"),
            {"u": uid, "g": GROUP_ID},
        )
    ).all()
    assert sorted(k[0] for k in keys) == ["note", "ro"]
    # second login with the same subject uses the stored link, no second account
    again = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (again.decision, again.matched_by, again.user_id) == ("login", "link", uid)


async def test_jit_requires_email_and_login(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(s, "missing")
    ident = _ident(p.id, "sub-m", preferred_username="idp.miss.47352")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "jit_missing_fields")
    assert "Es fehlen: E-Mail" in str(plan_to_dict(plan)["reject_message"])
    with pytest.raises(LoginRejected) as exc:
        await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert exc.value.reject_code == "jit_missing_fields"
    both = await plan_login(s, p, _ident(p.id, "sub-m2"), user_kind="agent", settings=SETTINGS)
    assert "Es fehlen: Anmeldename, E-Mail" in str(plan_to_dict(both)["reject_message"])


async def test_jit_name_fallbacks(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(s, "names")
    ident = _ident(p.id, "sub-n", preferred_username="idp.nonames.47353", email="kurz@example.test")
    uid, _ = (await _login(s, p, ident))[1]
    row = (
        await s.execute(text("SELECT first_name, last_name FROM users WHERE id = :u"), {"u": uid})
    ).one()
    assert (row[0], row[1]) == ("kurz", "idp.nonames.47353")


async def test_jit_login_taken_rejects(env: AsyncSession) -> None:
    s = env
    await seed_agent(s, 47354, "idp.taken.47354", valid_id=2)
    p = await _jit_provider(s, "taken", match_by="email")
    before = (await _count(s, "users"), await _count(s, "tiqora_identity_link"))
    ident = _ident(p.id, "sub-t", preferred_username="idp.taken.47354", email="fremd@example.test")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "login_taken")
    with pytest.raises(LoginRejected):
        await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert (await _count(s, "users"), await _count(s, "tiqora_identity_link")) == before


async def test_jit_rejected_email_is_clean_reject(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(s, "badmail")
    before = await _count(s, "users")
    for bad in ("not-an-address", "x@example.org"):  # syntax / CheckEmailInvalidAddress
        ident = _ident(p.id, f"sub-{bad}", preferred_username="idp.bad.47355", email=bad)
        plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
        assert (plan.decision, plan.reject_code) == ("reject", "email_invalid")
        with pytest.raises(LoginRejected) as exc:
            await apply_login(s, p, ident, plan, settings=SETTINGS)
        assert exc.value.reject_code == "email_invalid"
    assert await _count(s, "users") == before


async def test_jit_apply_maps_provisioning_error_to_reject(env: AsyncSession) -> None:
    """A race (login taken, e-mail refused) between plan and apply is a reject, not a 500."""
    s = env
    p = await _jit_provider(s, "race")
    ident = _ident(p.id, "sub-r", preferred_username="idp.race.47356", email="r@example.test")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.decision == "create"
    await seed_agent(s, 47356, "idp.race.47356")  # appears after planning
    with pytest.raises(LoginRejected) as exc:
        await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert exc.value.reject_code == "login_taken"


async def test_jit_requires_verified_email_for_oidc(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(s, "verified")
    ident = _ident(
        p.id, "sub-v", verified=None, preferred_username="idp.ver.47357", email="v@example.test"
    )
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "email_unverified")
    relaxed = await update_provider(
        s,
        p.id,
        ProviderChanges(config={"email_must_be_verified": False}),
        secret_key=KEY,
        actor_id=1,
    )
    plan = await plan_login(s, relaxed, ident, user_kind="agent", settings=SETTINGS)
    assert plan.decision == "create"


async def test_jit_disabled_provider_does_not_create(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(s, "off")
    p = await update_provider(
        s, p.id, ProviderChanges(jit_enabled=False), secret_key=KEY, actor_id=1
    )
    ident = _ident(p.id, "sub-o", preferred_username="idp.off.47358", email="o@example.test")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "no_match")


async def test_jit_customer(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(
        s,
        "cust",
        audience="customer",
        defaults=JitDefaults(customer_id="idp-jit-co"),
    )
    ident = _ident(
        p.id,
        "sub-c",
        preferred_username="idp.kunde@example.org",
        email="kunde@example.test",
        given_name="Karl",
        family_name="Kunde",
    )
    plan, (cid, login) = await _login(s, p, ident, kind="customer")
    assert (plan.decision, login) == ("create", "idp.kunde@example.org")
    row = (
        await s.execute(
            text(
                "SELECT customer_id, email, pw, valid_id, first_name FROM customer_user"
                " WHERE id = :i"
            ),
            {"i": cid},
        )
    ).one()
    assert (row[0], row[1], row[3], row[4]) == ("idp-jit-co", "kunde@example.test", 1, "Karl")
    assert not verify_password("", row[2]) and not verify_password("x", row[2])


async def test_jit_customer_email_in_use_rejects(env: AsyncSession) -> None:
    s = env
    await seed_customer(s, "idp.vorhanden@example.org", email="gleich@example.test")
    p = await _jit_provider(s, "custdup", audience="customer", match_by="login")
    ident = _ident(
        p.id, "sub-d", preferred_username="idp.zweit@example.org", email="gleich@example.test"
    )
    plan = await plan_login(s, p, ident, user_kind="customer", settings=SETTINGS)
    with pytest.raises(LoginRejected) as exc:
        await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert exc.value.reject_code == "email_in_use"
    n = (
        await s.execute(
            text("SELECT COUNT(*) FROM customer_user WHERE login = 'idp.zweit@example.org'")
        )
    ).scalar_one()
    assert n == 0


async def test_jit_customer_needs_db_backend(
    env: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.identity import provisioning
    from tiqora.identity.provisioning import is_db_customer_module

    assert is_db_customer_module(None) is True
    assert is_db_customer_module("Kernel::System::CustomerUser::DB") is True
    assert is_db_customer_module("Kernel::System::CustomerUser::LDAP") is False

    async def _not_db(_session: AsyncSession) -> bool:
        return False

    s = env
    p = await _jit_provider(s, "custldap", audience="customer")
    ident = _ident(p.id, "sub-l", preferred_username="idp.ldap@example.org", email="l@example.test")
    assert (await plan_login(s, p, ident, user_kind="customer", settings=SETTINGS)).decision == (
        "create"
    )
    monkeypatch.setattr(provisioning, "customer_backend_is_db", _not_db)
    plan = await plan_login(s, p, ident, user_kind="customer", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "no_match")


async def test_profile_sync_only_when_changed(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(s, "sync")
    await seed_agent(s, 47360, "idp.sync.47360", email="sync@example.test")
    ident = _ident(
        p.id,
        "sub-s",
        preferred_username="idp.sync.47360",
        email="sync@example.test",
        given_name="Idp",
        family_name="Neu",
    )
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.profile_changes == {"last_name": ("Test", "Neu")}
    await apply_login(s, p, ident, plan, settings=SETTINGS)
    row = (
        await s.execute(
            text("SELECT last_name, change_by, change_time FROM users WHERE id = 47360")
        )
    ).one()
    assert (row[0], row[1]) == ("Neu", 1)
    # same values again: nothing planned, change_time untouched
    plan2 = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan2.profile_changes == {}
    await apply_login(s, p, ident, plan2, settings=SETTINGS)
    row2 = (await s.execute(text("SELECT change_time FROM users WHERE id = 47360"))).scalar_one()
    assert row2 == row[2]
    # an e-mail change goes to the UserEmail preference
    moved = _ident(
        p.id,
        "sub-s",
        preferred_username="idp.sync.47360",
        email="umgezogen@example.test",
        given_name="Idp",
        family_name="Neu",
    )
    plan3 = await plan_login(s, p, moved, user_kind="agent", settings=SETTINGS)
    assert plan3.profile_changes == {"email": ("sync@example.test", "umgezogen@example.test")}
    await apply_login(s, p, moved, plan3, settings=SETTINGS)
    assert await _pref(s, 47360, "UserEmail") == "umgezogen@example.test"


async def test_profile_sync_switched_off_and_unmapped(env: AsyncSession) -> None:
    s = env
    await seed_agent(s, 47361, "idp.off.47361", email="off@example.test")
    p = await _jit_provider(s, "syncoff", defaults=JitDefaults(update_profile_on_login=False))
    ident = _ident(p.id, "sub-x", preferred_username="idp.off.47361", family_name="Anders")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.profile_changes) == ("login", {})
    # env-oidc style provider: only the login claim is mapped -> nothing to sync
    q = await make_provider(
        s,
        slug=f"{SLUG}loginonly",
        settings=SETTINGS,
        attribute_map=AttributeMap(login=["preferred_username"]),
    )
    plan = await plan_login(
        s,
        q,
        _ident(q.id, "sub-y", preferred_username="idp.off.47361", family_name="Anders"),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert (plan.decision, plan.profile_changes) == ("login", {})


async def test_profile_sync_bad_email_does_not_block_login(env: AsyncSession) -> None:
    s = env
    await seed_agent(s, 47362, "idp.keep.47362", email="keep@example.test")
    p = await _jit_provider(s, "syncbad")
    ident = _ident(
        p.id,
        "sub-k",
        preferred_username="idp.keep.47362",
        email="kaputt",
        given_name="Idp",
        family_name="Neu",
    )
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.decision == "login"  # matched by link/login; e-mail not used to match
    assert "email" not in plan.profile_changes
    await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert await _pref(s, 47362, "UserEmail") == "keep@example.test"


async def test_plan_is_read_only(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(s, "ro")
    await seed_agent(s, 47363, "idp.ro.47363", email="ro@example.test")
    tables = ("users", "user_preferences", "tiqora_identity_link", "tiqora_user_auth_config")
    before = [await _count(s, t) for t in tables]
    for name in ("idp.ro.new.47364", "idp.ro.47363"):
        ident = _ident(
            p.id,
            f"sub-{name}",
            preferred_username=name,
            email=f"{name}@example.test",
            family_name="Anders",
        )
        await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert [await _count(s, t) for t in tables] == before
    changed = (await s.execute(text("SELECT last_name FROM users WHERE id = 47363"))).scalar_one()
    assert changed == "Test"
