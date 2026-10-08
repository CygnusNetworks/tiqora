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
_MAKERS: list[async_sessionmaker[AsyncSession]] = []  # second sessions for race tests
_INVAL_MARK: dict[str, int] = {}  # url -> MAX(id) of tiqora_cache_invalidation at module start
SETTINGS = Settings(environment="test", secret_key=KEY)
MAP = AttributeMap(
    login=["preferred_username"],
    email=["email"],
    first_name=["given_name"],
    last_name=["family_name"],
    mobile=["phone"],
)
_JIT_USERS = "(SELECT id FROM users WHERE login LIKE 'idp.%.473%')"


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
        ("DELETE FROM users WHERE login LIKE 'idp.%.473%'", {}),
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
    _MAKERS[:] = [maker]
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
    await s.commit()  # race tests read the provider from a second session
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


async def test_jit_race_with_existing_login_logs_in_through_it(env: AsyncSession) -> None:
    """The account appears between plan and apply: the re-plan matches it by login."""
    s = env
    p = await _jit_provider(s, "race")
    ident = _ident(p.id, "sub-r", preferred_username="idp.race.47356", email="r@example.test")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.decision == "create"
    await seed_agent(s, 47356, "idp.race.47356")  # appears after planning
    assert await apply_login(s, p, ident, plan, settings=SETTINGS) == (47356, "idp.race.47356")


async def test_jit_race_with_taken_login_rejects_when_match_by_email(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(s, "race1b", match_by="email")
    ident = _ident(p.id, "sub-r1", preferred_username="idp.race1.47377", email="r1@example.test")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.decision == "create"
    await seed_agent(s, 47377, "idp.race1.47377", valid_id=2)  # invalid owner of the login
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


async def test_jit_customer_email_preflight_in_plan(env: AsyncSession) -> None:
    s = env
    await seed_customer(s, "idp.pre@example.org", email="pre@example.test")
    p = await _jit_provider(s, "custpre", audience="customer")
    ident = _ident(
        p.id, "sub-pre", preferred_username="idp.pre2@example.org", email="pre@example.test"
    )
    plan = await plan_login(s, p, ident, user_kind="customer", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "email_in_use")


async def test_jit_domain_gate(env: AsyncSession) -> None:
    s = env
    p = await _jit_provider(
        s, "domain", defaults=JitDefaults(allowed_email_domains=[" Example.Test. ", "", "a.test"])
    )
    assert p.jit_defaults.allowed_email_domains == ["example.test", "a.test"]
    ok = _ident(p.id, "d1", preferred_username="idp.dom.47370", email="Wer@EXAMPLE.test")
    assert (await plan_login(s, p, ok, user_kind="agent", settings=SETTINGS)).decision == "create"
    for i, mail in enumerate(("wer@evil.example.test", "wer@other.test", "wer@xexample.test")):
        bad = _ident(p.id, f"d{i + 2}", preferred_username="idp.dom.47371", email=mail)
        plan = await plan_login(s, p, bad, user_kind="agent", settings=SETTINGS)
        assert (plan.decision, plan.reject_code) == ("reject", "jit_domain_not_allowed"), mail
    # the gate only guards creation: an existing account still signs in
    await seed_agent(s, 47372, "idp.dom.47372", email="x@other.test")
    old = _ident(p.id, "d9", preferred_username="idp.dom.47372", email="x@other.test")
    assert (await plan_login(s, p, old, user_kind="agent", settings=SETTINGS)).decision == "login"


async def test_jit_case_only_login_collision(env: AsyncSession) -> None:
    s = env
    await seed_agent(s, 47373, "IDP.Case.47373")
    p = await _jit_provider(s, "casecol", match_by="email")
    ident = _ident(p.id, "c1", preferred_username="idp.case.47373", email="c@example.test")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "login_taken")


async def test_concurrent_first_login_logs_in_through_committed_link(
    env: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = env
    p = await _jit_provider(s, "race2")
    ident = _ident(p.id, "sub-r2", preferred_username="idp.race2.47374", email="r2@example.test")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.decision == "create"
    async with _MAKERS[0]() as other:  # the concurrent request wins
        winner = await plan_login(other, p, ident, user_kind="agent", settings=SETTINGS)
        uid, _ = await apply_login(other, p, ident, winner, settings=SETTINGS)
    got = await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert got == (uid, "idp.race2.47374")
    n = (
        await s.execute(text("SELECT COUNT(*) FROM users WHERE login = 'idp.race2.47374'"))
    ).scalar_one()
    assert n == 1


async def test_integrity_error_on_create_replans(
    env: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The DB unique violation (not the pre-check) is turned into a re-plan, then login_taken."""
    from sqlalchemy.exc import IntegrityError

    from tiqora.identity import provisioning

    s = env
    p = await _jit_provider(s, "race3")
    ident = _ident(p.id, "sub-r3", preferred_username="idp.race3.47375", email="r3@example.test")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)

    async def _boom(*_a: object, **_k: object) -> None:
        raise IntegrityError("INSERT", {}, Exception("duplicate"))

    monkeypatch.setattr(provisioning, "create_agent", _boom)
    # nobody else created the account: re-plan still says "create" -> clean reject, no 500
    with pytest.raises(LoginRejected) as exc:
        await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert exc.value.reject_code == "login_taken"
    # someone else committed the link meanwhile: the re-plan logs in through it
    monkeypatch.undo()
    async with _MAKERS[0]() as other:
        w = await plan_login(other, p, ident, user_kind="agent", settings=SETTINGS)
        uid, _ = await apply_login(other, p, ident, w, settings=SETTINGS)
    monkeypatch.setattr(provisioning, "create_agent", _boom)
    assert (await apply_login(s, p, ident, plan, settings=SETTINGS))[0] == uid


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


async def test_profile_sync_customer_email_in_use_keeps_names_and_audit(
    env: AsyncSession,
) -> None:
    s = env
    await seed_customer(s, "idp.sync-a@example.org", email="a@example.test")
    cid = await seed_customer(s, "idp.sync-b@example.org", email="b@example.test")
    p = await _jit_provider(s, "custsync", audience="customer", match_by="login")
    ident = _ident(
        p.id,
        "sub-cs",
        preferred_username="idp.sync-b@example.org",
        email="a@example.test",  # taken by the other customer
        family_name="Umbenannt",
    )
    plan = await plan_login(s, p, ident, user_kind="customer", settings=SETTINGS)
    assert plan.profile_changes["last_name"] == ("Kunde", "Umbenannt")
    await apply_login(s, p, ident, plan, settings=SETTINGS)
    row = (
        await s.execute(
            text(
                "SELECT last_name, email, change_by, change_time FROM customer_user WHERE id = :i"
            ),
            {"i": cid},
        )
    ).one()
    assert (row[0], row[1], row[2]) == ("Umbenannt", "b@example.test", 1)
    assert row[3] != NOW  # the name change updated change_time
    # plan also covers a plain customer name/mobile sync
    again = await plan_login(s, p, ident, user_kind="customer", settings=SETTINGS)
    assert "last_name" not in again.profile_changes


async def test_profile_sync_unverified_oidc_email_not_applied(env: AsyncSession) -> None:
    s = env
    await seed_agent(s, 47376, "idp.unver.47376", email="alt@example.test")
    p = await _jit_provider(s, "unver")
    ident = _ident(
        p.id,
        "sub-u",
        verified=False,
        preferred_username="idp.unver.47376",
        email="neu@example.test",
        given_name="Idp",
        family_name="Neu",
    )
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert plan.decision == "login"
    assert plan.profile_changes == {"last_name": ("Test", "Neu")}
    await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert await _pref(s, 47376, "UserEmail") == "alt@example.test"
