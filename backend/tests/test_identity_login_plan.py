"""Identity links and login planning: match + link (wave 1, Task 6).

Seed band 47000-47099. Provider slugs use the ``idplogin-`` prefix.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._identity_helpers import cleanup_statements, make_provider, seed_agent, seed_customer
from tests.test_identity_store import _to_async_url
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth_config import AuthConfigService
from tiqora.identity import links as links_module
from tiqora.identity import login as login_module
from tiqora.identity.links import (
    LinkConflictError,
    delete_link,
    find_link,
    list_links,
    upsert_link,
)
from tiqora.identity.login import (
    REJECT_MESSAGES,
    ExternalIdentity,
    LoginRejected,
    apply_login,
    plan_login,
    plan_to_dict,
)
from tiqora.identity.types import SYSTEM_USER_ID, subject_hash

pytestmark = pytest.mark.db

KEY = "unit-test-secret-key-for-identity-login"
BAND = (47000, 47099)
SLUG = "idplogin-"
_URLS: set[str] = set()
SETTINGS = Settings(environment="test", secret_key=KEY)


def _stmts() -> tuple[tuple[str, dict[str, object]], ...]:
    return cleanup_statements(user_ids=BAND, slug_prefix=SLUG)


def _clean(url: str) -> None:
    engine = create_engine(url)
    try:
        TiqoraBase.metadata.create_all(engine)
        with engine.begin() as conn:
            for stmt, params in _stmts():
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
async def env(request: pytest.FixtureRequest) -> AsyncIterator[tuple[AsyncSession, str]]:
    url: str = request.getfixturevalue(request.param)
    _URLS.add(url)
    _clean(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s, request.param
        await s.rollback()
    await engine.dispose()
    _clean(url)


def _ident(provider_id: int, subject: str, **attrs: str) -> ExternalIdentity:
    return ExternalIdentity(
        provider_id=provider_id,
        subject=subject,
        attributes={k: (v,) for k, v in attrs.items()},
    )


async def _oidc(session: AsyncSession, slug: str, **kw):  # type: ignore[no-untyped-def]
    return await make_provider(session, slug=f"{SLUG}{slug}", settings=SETTINGS, **kw)


async def test_match_by_login_then_link_created(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(s, "login")
    ident = _ident(p.id, "sub-1", preferred_username="idp.anna.47001")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.matched_by, plan.user_id) == ("login", "login", 47001)
    assert plan.link_exists is False
    # planning is read-only
    assert await find_link(s, provider_id=p.id, user_kind="agent", subject="sub-1") is None
    assert await apply_login(s, p, ident, plan, settings=SETTINGS) == (47001, "idp.anna.47001")
    link = await find_link(s, provider_id=p.id, user_kind="agent", subject="sub-1")
    assert link is not None
    assert (link.user_id, link.user_login) == (47001, "idp.anna.47001")
    row = (
        await s.execute(
            text("SELECT subject_hash FROM tiqora_identity_link WHERE provider_id = :p"),
            {"p": p.id},
        )
    ).scalar_one()
    assert row == subject_hash("sub-1")


async def test_link_wins_over_claim(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    await seed_agent(s, 47002, "idp.bert.47002")
    p = await _oidc(s, "wins")
    first = _ident(p.id, "sub-1", preferred_username="idp.anna.47001")
    await apply_login(
        s,
        p,
        first,
        await plan_login(s, p, first, user_kind="agent", settings=SETTINGS),
        settings=SETTINGS,
    )
    second = _ident(p.id, "sub-1", preferred_username="idp.bert.47002")
    plan = await plan_login(s, p, second, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.matched_by, plan.user_id) == ("login", "link", 47001)
    assert plan.link_exists is True


async def test_renamed_login_still_logs_in(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(s, "rename")
    ident = _ident(p.id, "sub-1", preferred_username="idp.anna.47001")
    await apply_login(
        s,
        p,
        ident,
        await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS),
        settings=SETTINGS,
    )
    await s.execute(text("UPDATE users SET login = 'idp.anna.renamed.47001' WHERE id = 47001"))
    await s.commit()
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.matched_by, plan.login) == (
        "login",
        "link",
        "idp.anna.renamed.47001",
    )
    assert await apply_login(s, p, ident, plan, settings=SETTINGS) == (
        47001,
        "idp.anna.renamed.47001",
    )
    link = await find_link(s, provider_id=p.id, user_kind="agent", subject="sub-1")
    assert link is not None and link.user_login == "idp.anna.renamed.47001"


async def test_link_to_invalid_user_rejects_without_fallback(
    env: tuple[AsyncSession, str],
) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    await seed_agent(s, 47002, "idp.bert.47002")
    p = await _oidc(s, "invalid")
    first = _ident(p.id, "sub-1", preferred_username="idp.anna.47001")
    await apply_login(
        s,
        p,
        first,
        await plan_login(s, p, first, user_kind="agent", settings=SETTINGS),
        settings=SETTINGS,
    )
    await s.execute(text("UPDATE users SET valid_id = 2 WHERE id = 47001"))
    await s.commit()
    second = _ident(p.id, "sub-1", preferred_username="idp.bert.47002")
    plan = await plan_login(s, p, second, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.reject_code, plan.user_id) == ("reject", "user_invalid", None)
    with pytest.raises(ValueError):
        await apply_login(s, p, second, plan, settings=SETTINGS)


async def test_case_insensitive_login_needs_unique_hit(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47003, "Idp.Case.47003")
    await seed_agent(s, 47004, "idp.case.47004")
    p = await _oidc(s, "case")
    plan = await plan_login(
        s,
        p,
        _ident(p.id, "a", preferred_username="idp.case.47004"),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert (plan.decision, plan.user_id) == ("login", 47004)
    plan = await plan_login(
        s,
        p,
        _ident(p.id, "b", preferred_username="IDP.CASE.47003"),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert (plan.decision, plan.matched_by, plan.user_id) == ("login", "login", 47003)
    plan = await plan_login(
        s,
        p,
        _ident(p.id, "c", preferred_username="IDP.CASE.47005-unknown"),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert (plan.decision, plan.reject_code) == ("reject", "no_match")


async def test_exact_login_before_case_insensitive(env: tuple[AsyncSession, str]) -> None:
    s, which = env
    if which != "postgres_znuny_url":
        pytest.skip("true case-only login pairs need a case-sensitive collation (PostgreSQL)")
    await seed_agent(s, 47008, "Mia.47008")
    await seed_agent(s, 47009, "mia.47008")
    p = await _oidc(s, "exact")

    async def claim(value: str):  # type: ignore[no-untyped-def]
        return await plan_login(
            s,
            p,
            _ident(p.id, value, preferred_username=value),
            user_kind="agent",
            settings=SETTINGS,
        )

    assert (await claim("mia.47008")).user_id == 47009
    assert (await claim("Mia.47008")).user_id == 47008
    ambiguous = await claim("MIA.47008")  # two case-insensitive hits, no exact one
    assert (ambiguous.decision, ambiguous.reject_code) == ("reject", "no_match")


async def test_match_by_email_agent(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47006, "idp.mia.47006", email="Mia@Example.org")
    await seed_agent(s, 47010, "idp.twin1.47010", email="twin@example.org")
    await seed_agent(s, 47011, "idp.twin2.47011", email="Twin@example.org")
    await seed_agent(s, 47012, "idp.gone.47012", email="gone@example.org", valid_id=2)
    p = await _oidc(s, "email", match_by="email")

    def ident(sub: str, email: str, verified: bool | None) -> ExternalIdentity:
        return ExternalIdentity(
            provider_id=p.id, subject=sub, attributes={"email": (email,)}, email_verified=verified
        )

    plan = await plan_login(
        s, p, ident("e1", "mia@example.org", True), user_kind="agent", settings=SETTINGS
    )
    assert (plan.decision, plan.matched_by, plan.user_id) == ("login", "email", 47006)
    plan = await plan_login(
        s, p, ident("e2", "mia@example.org", False), user_kind="agent", settings=SETTINGS
    )
    assert plan.reject_code == "email_unverified"
    plan = await plan_login(
        s, p, ident("e3", "mia@example.org", None), user_kind="agent", settings=SETTINGS
    )
    assert plan.reject_code == "email_unverified"
    plan = await plan_login(
        s, p, ident("e4", "twin@example.org", True), user_kind="agent", settings=SETTINGS
    )
    assert plan.reject_code == "ambiguous_email"
    plan = await plan_login(
        s, p, ident("e5", "gone@example.org", True), user_kind="agent", settings=SETTINGS
    )
    assert plan.reject_code == "no_match"


async def test_match_by_email_customer(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    cid = await seed_customer(s, "idp.kunde.47007@example.org")
    p = await _oidc(s, "cust", audience="customer", match_by="email")
    ident = ExternalIdentity(
        provider_id=p.id,
        subject="c1",
        attributes={"email": ("IDP.Kunde.47007@Example.org",)},
        email_verified=True,
    )
    plan = await plan_login(s, p, ident, user_kind="customer", settings=SETTINGS)
    assert (plan.user_kind, plan.decision, plan.user_id) == ("customer", "login", cid)
    assert plan.login == "idp.kunde.47007@example.org"
    assert await apply_login(s, p, ident, plan, settings=SETTINGS) == (cid, plan.login)
    link = await find_link(s, provider_id=p.id, user_kind="customer", subject="c1")
    assert link is not None and link.user_kind == "customer"
    # the same subject as an agent is a different link space
    assert await find_link(s, provider_id=p.id, user_kind="agent", subject="c1") is None


async def test_sso_opt_in_gate(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(s, "gate", require_sso_opt_in=True)
    ident = _ident(p.id, "sub-1", preferred_username="idp.anna.47001")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "sso_not_enabled")
    await AuthConfigService(s).set(47001, sso_eligible=True)
    await s.commit()
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.user_id) == ("login", 47001)
    ldap = await make_provider(
        s, slug=f"{SLUG}gate-ldap", kind="ldap", require_sso_opt_in=True, settings=SETTINGS
    )
    await AuthConfigService(s).set(47001, sso_eligible=False)
    await s.commit()
    plan = await plan_login(
        s,
        ldap,
        _ident(ldap.id, "uid-1", uid="idp.anna.47001"),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert (plan.decision, plan.user_id) == ("login", 47001)


async def test_transient_name_id_never_links(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await make_provider(
        s,
        slug=f"{SLUG}saml",
        kind="saml",
        config={"name_id_format": "transient"},
        settings=SETTINGS,
    )
    ident = ExternalIdentity(
        provider_id=p.id,
        subject="_transient-abc",
        attributes={"saml:name_id": ("idp.anna.47001",)},
    )
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.user_id) == ("login", 47001)
    assert await apply_login(s, p, ident, plan, settings=SETTINGS) == (47001, "idp.anna.47001")
    assert await list_links(s, provider_id=p.id) == []


async def test_empty_subject_rejects(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(s, "nosub")
    plan = await plan_login(
        s,
        p,
        _ident(p.id, "", preferred_username="idp.anna.47001"),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert (plan.decision, plan.reject_code) == ("reject", "no_subject")
    assert plan.reject_code in REJECT_MESSAGES


async def test_link_functions_and_plan_dict(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(s, "links")
    a = await upsert_link(
        s, provider_id=p.id, user_kind="agent", subject="s-a", user_id=47001, user_login="x"
    )
    b = await upsert_link(
        s, provider_id=p.id, user_kind="agent", subject="s-b", user_id=47001, user_login="y"
    )
    again = await upsert_link(
        s, provider_id=p.id, user_kind="agent", subject="s-a", user_id=47001, user_login="z"
    )
    assert again.id == a.id and again.user_login == "z"
    assert {link.subject for link in await list_links(s, provider_id=p.id)} == {"s-a", "s-b"}
    assert await delete_link(s, provider_id=p.id, link_id=b.id) is True
    assert await delete_link(s, provider_id=p.id, link_id=b.id) is False
    assert await delete_link(s, provider_id=p.id + 1, link_id=a.id) is False
    await s.commit()
    plan = await plan_login(
        s, p, _ident(p.id, "s-a", preferred_username="nobody"), user_kind="agent", settings=SETTINGS
    )
    d = plan_to_dict(plan)
    assert d["decision"] == "login" and d["user_id"] == 47001 and d["link_exists"] is True


async def _plan(s, p, sub, kind="agent", **attrs):  # type: ignore[no-untyped-def]
    return await plan_login(s, p, _ident(p.id, sub, **attrs), user_kind=kind, settings=SETTINGS)


async def test_upsert_never_repoints_existing_link(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    await seed_agent(s, 47002, "idp.bert.47002")
    p = await _oidc(s, "repoint")
    await upsert_link(
        s, provider_id=p.id, user_kind="agent", subject="s", user_id=47001, user_login="a"
    )
    with pytest.raises(LinkConflictError):
        await upsert_link(
            s, provider_id=p.id, user_kind="agent", subject="s", user_id=47002, user_login="b"
        )
    link = await find_link(s, provider_id=p.id, user_kind="agent", subject="s")
    assert link is not None and (link.user_id, link.user_login) == (47001, "a")


async def test_concurrent_first_login_insert_race(
    env: tuple[AsyncSession, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    await seed_agent(s, 47002, "idp.bert.47002")
    p = await _oidc(s, "race")
    await upsert_link(
        s, provider_id=p.id, user_kind="agent", subject="s", user_id=47001, user_login="a"
    )
    await s.commit()
    real = links_module._get_row
    calls = {"n": 0}

    async def blind_first(*a, **kw):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(*a, **kw)

    # The other request inserted the row between our read and our insert.
    monkeypatch.setattr(links_module, "_get_row", blind_first)
    same = await upsert_link(
        s, provider_id=p.id, user_kind="agent", subject="s", user_id=47001, user_login="a2"
    )
    assert (same.user_id, same.user_login) == (47001, "a2")
    calls["n"] = 0
    with pytest.raises(LinkConflictError):
        await upsert_link(
            s, provider_id=p.id, user_kind="agent", subject="s", user_id=47002, user_login="b"
        )
    link = await find_link(s, provider_id=p.id, user_kind="agent", subject="s")
    assert link is not None and link.user_id == 47001
    assert len(await list_links(s, provider_id=p.id)) == 1


async def test_apply_login_maps_link_conflict_to_reject(
    env: tuple[AsyncSession, str],
) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    await seed_agent(s, 47002, "idp.bert.47002")
    p = await _oidc(s, "conflict")
    ident = _ident(p.id, "s", preferred_username="idp.bert.47002")
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    # another request links the subject to a different account after planning
    await upsert_link(
        s, provider_id=p.id, user_kind="agent", subject="s", user_id=47001, user_login="a"
    )
    await s.commit()
    with pytest.raises(LoginRejected) as exc:
        await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert exc.value.reject_code == "user_invalid"
    link = await find_link(s, provider_id=p.id, user_kind="agent", subject="s")
    assert link is not None and link.user_id == 47001


async def test_login_match_ignores_collation_folding(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47013, "idp.ts.47013 ")
    await seed_agent(s, 47014, "idp.josé.47014")
    p = await _oidc(s, "fold")
    # accent is not case: MariaDB *_ci says jose = josé, we must not
    plan = await _plan(s, p, "a", preferred_username="idp.jose.47014")
    assert plan.reject_code == "no_match"
    assert (await _plan(s, p, "b", preferred_username="IDP.JOSÉ.47014")).user_id == 47014
    # claimed value is stripped; stored trailing space is ignored for the fallback
    assert (await _plan(s, p, "c", preferred_username="  idp.ts.47013  ")).user_id == 47013


async def test_customer_email_ignores_collation_folding(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    cid = await seed_customer(s, "idp.kunde.47017@example.org", email="Jos\u00e9.47017@example.org")
    p = await _oidc(s, "cfold", audience="customer", match_by="email")

    def ident(email: str) -> ExternalIdentity:
        return ExternalIdentity(
            provider_id=p.id, subject=email, attributes={"email": (email,)}, email_verified=True
        )

    no = await plan_login(
        s, p, ident("jose.47017@example.org"), user_kind="customer", settings=SETTINGS
    )
    assert no.reject_code == "no_match"
    yes = await plan_login(
        s, p, ident(" JOS\u00c9.47017@example.org "), user_kind="customer", settings=SETTINGS
    )
    assert yes.user_id == cid


async def test_invalid_accounts_are_not_matched(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47016, "idp.old.47016", valid_id=2)
    await seed_customer(s, "idp.oldkunde.47018@example.org", valid_id=2)
    p = await _oidc(s, "inv")
    assert (await _plan(s, p, "a", preferred_username="idp.old.47016")).reject_code == "no_match"
    pc = await _oidc(s, "invc", audience="customer")
    plan = await _plan(
        s, pc, "b", kind="customer", preferred_username="idp.oldkunde.47018@example.org"
    )
    assert plan.reject_code == "no_match"


async def test_sso_gate_applies_to_link_matched_user(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(s, "gatelink")
    ident = _ident(p.id, "s", preferred_username="idp.anna.47001")
    await apply_login(
        s,
        p,
        ident,
        await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS),
        settings=SETTINGS,
    )
    from tiqora.identity.store import ProviderChanges, update_provider

    gated = await update_provider(
        s, p.id, ProviderChanges(require_sso_opt_in=True), secret_key=KEY, actor_id=1
    )
    plan = await plan_login(s, gated, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.reject_code, plan.link_exists) == ("sso_not_enabled", True)


async def test_second_subject_for_already_linked_account_is_allowed(
    env: tuple[AsyncSession, str],
) -> None:
    # Design is silent on 1:1; several IdP subjects (e.g. re-issued accounts) may map to
    # one local account. Each subject has its own link; pinned here deliberately.
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(s, "twosubs")
    for sub in ("sub-a", "sub-b"):
        ident = _ident(p.id, sub, preferred_username="idp.anna.47001")
        plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
        assert (plan.decision, plan.user_id) == ("login", 47001)
        await apply_login(s, p, ident, plan, settings=SETTINGS)
    assert len(await list_links(s, provider_id=p.id)) == 2


async def test_audience_mismatch_rejects(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(s, "aud", audience="customer")
    plan = await _plan(s, p, "a", preferred_username="idp.anna.47001")
    assert (plan.decision, plan.reject_code) == ("reject", "no_match")


async def _system_user_id(s: AsyncSession) -> int:
    """The Znuny seed's superuser; the guard keys on id 1, so the seed must agree."""
    uid = (
        await s.execute(text("SELECT id FROM users WHERE login = 'root@localhost'"))
    ).scalar_one()
    assert uid == SYSTEM_USER_ID
    return int(uid)


async def test_system_user_never_matched_by_login_or_link(
    env: tuple[AsyncSession, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """M10: an IdP user who picks the username root@localhost gets the generic no_match."""
    s, _ = env
    # Recorded on the module logger itself: other tests' logging setup may disable
    # existing stdlib loggers, which would make caplog order-dependent.
    warnings: list[str] = []
    monkeypatch.setattr(login_module.log, "warning", lambda msg, *args: warnings.append(msg % args))
    root = await _system_user_id(s)
    p = await _oidc(s, "root-login", jit_enabled=True)
    for name in ("root@localhost", "ROOT@localhost"):
        plan = await _plan(s, p, f"sub-{name}", preferred_username=name)
        assert (plan.decision, plan.reject_code, plan.user_id) == ("reject", "no_match", None)
        with pytest.raises(LoginRejected) as exc:
            await apply_login(s, p, _ident(p.id, f"sub-{name}"), plan, settings=SETTINGS)
        assert exc.value.reject_code == "no_match"
    assert len(warnings) == 2
    assert all("system user" in w and f"{SLUG}root-login" in w for w in warnings)
    assert await list_links(s, provider_id=p.id) == []
    # A link onto the system user (e.g. stored before this guard) is refused as well.
    await upsert_link(
        s,
        provider_id=p.id,
        user_kind="agent",
        subject="sub-root",
        user_id=root,
        user_login="root@localhost",
    )
    await s.commit()
    plan = await _plan(s, p, "sub-root", preferred_username="someone.else")
    assert (plan.decision, plan.reject_code, plan.link_exists) == ("reject", "no_match", True)
    # Directory logins (LDAP) take the same chain.
    ldap = await make_provider(s, slug=f"{SLUG}root-ldap", kind="ldap", settings=SETTINGS)
    plan = await plan_login(
        s,
        ldap,
        _ident(ldap.id, "uid-root", uid="root@localhost"),
        user_kind="agent",
        settings=SETTINGS,
    )
    assert (plan.decision, plan.reject_code) == ("reject", "no_match")


async def test_system_user_never_matched_by_email(env: tuple[AsyncSession, str]) -> None:
    s, _ = env
    root = await _system_user_id(s)
    existing = (
        await s.execute(
            text(
                "SELECT preferences_value FROM user_preferences"
                " WHERE user_id = :u AND preferences_key = 'UserEmail'"
            ),
            {"u": root},
        )
    ).scalar_one_or_none()
    inserted = existing is None
    if inserted:
        email = "idp.root.m10@example.org"
        await s.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (:u, 'UserEmail', :v)"
            ),
            {"u": root, "v": email},
        )
        await s.commit()
    else:
        raw = existing
        email = bytes(raw).decode() if isinstance(raw, bytes | memoryview) else str(raw)
    try:
        p = await _oidc(s, "root-email", match_by="email", jit_enabled=True)
        ident = ExternalIdentity(
            provider_id=p.id, subject="e-root", attributes={"email": (email,)}, email_verified=True
        )
        plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
        # Refused outright: neither a login nor a JIT account for that address.
        assert (plan.decision, plan.reject_code, plan.user_id) == ("reject", "no_match", None)
    finally:
        if inserted:
            await s.rollback()
            await s.execute(
                text(
                    "DELETE FROM user_preferences"
                    " WHERE user_id = :u AND preferences_key = 'UserEmail'"
                ),
                {"u": root},
            )
            await s.commit()


async def test_jit_cannot_reach_the_system_user(env: tuple[AsyncSession, str]) -> None:
    """JIT never creates or reuses root@localhost, and apply_login refuses a plan onto id 1."""
    s, _ = env
    await _system_user_id(s)
    await seed_agent(s, 47001, "idp.anna.47001")
    p = await _oidc(
        s,
        "root-jit",
        match_by="email",
        jit_enabled=True,
    )
    ident = ExternalIdentity(
        provider_id=p.id,
        subject="j-root",
        attributes={
            "preferred_username": ("root@localhost",),
            "email": ("idp.nobody.m10@example.org",),
            "given_name": ("Root",),
            "family_name": ("Jit",),
        },
        email_verified=True,
    )
    plan = await plan_login(s, p, ident, user_kind="agent", settings=SETTINGS)
    assert (plan.decision, plan.reject_code) == ("reject", "login_taken")
    # Backstop: a plan pointing at id 1 that did not come from plan_login.
    ok = await _plan(s, p, "j-anna", preferred_username="idp.anna.47001")
    forged = dataclasses.replace(
        ok, decision="login", matched_by="login", user_id=SYSTEM_USER_ID, login="root@localhost"
    )
    with pytest.raises(LoginRejected) as exc:
        await apply_login(s, p, ident, forged, settings=SETTINGS)
    assert exc.value.reject_code == "no_match"
    assert await list_links(s, provider_id=p.id) == []
