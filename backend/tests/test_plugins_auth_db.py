"""Password auth providers from plugins (D12, both dialects), through the real login routes.

Seed band 55370-55389: agents 55370 ``pp55-agent`` (a password nobody types), 55371 ``pp55-2fa``
(TOTP enabled), 55372 ``pp55-agent2``, 55373 ``pp55-invalid`` (valid_id 2), 55374 ``pp55-local``
(a real local password); the customer ``pp55-portal`` (the same local password) is removed by
its login; SCIM providers ``pp55scim*`` and their mappings (R15, P2A-PM-I2) by slug. The rate
limiter is off except in the tests that exercise it, which use an in-memory Redis.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

from tests._identity_helpers import FakeRedis
from tests._plugin_points import boot_points_plugin, points_behaviour
from tests.test_auth_hardening import _MemRedis
from tests.test_identity_store import _to_async_url
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.config import Settings
from tiqora.plugins import auth_providers
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

AGENT, TWOFA, AGENT2, INVALID, LOCAL = 55370, 55371, 55372, 55373, 55374
LOCAL_PASSWORD = "lokal-geheim-55"
BASE: dict[str, Any] = dict(
    environment="test",
    secret_key="unit-test-secret-key-for-plugin-auth",
    public_base_url="https://helpdesk.example.org",
    portal_enabled=True,
    auth_rate_limit_enabled=False,
)


def _clean(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        for sql in (
            "DELETE FROM tiqora_user_totp WHERE user_id BETWEEN 55370 AND 55389",
            "DELETE FROM user_preferences WHERE user_id BETWEEN 55370 AND 55389",
            "DELETE FROM users WHERE id BETWEEN 55370 AND 55389",
            "DELETE FROM customer_user WHERE login = 'pp55-portal'",
            "DELETE FROM tiqora_scim_user WHERE provider_id IN"
            " (SELECT id FROM tiqora_identity_provider WHERE slug LIKE 'pp55scim%')",
            "DELETE FROM tiqora_identity_provider WHERE slug LIKE 'pp55scim%'",
        ):
            conn.execute(text(sql))
    engine.dispose()


def _seed(url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    _clean(url)
    local_hash = hash_password(LOCAL_PASSWORD)
    with engine.begin() as conn:
        for uid, login, valid, pw in (
            (AGENT, "pp55-agent", 1, "x"),
            (TWOFA, "pp55-2fa", 1, "x"),
            (AGENT2, "pp55-agent2", 1, "x"),
            (INVALID, "pp55-invalid", 2, "x"),
            (LOCAL, "pp55-local", 1, local_hash),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, :pw, 'Pia', 'Plugin', :valid,"
                    " current_timestamp, 1, current_timestamp, 1)"
                ),
                {"id": uid, "login": login, "pw": pw, "valid": valid},
            )
        conn.execute(
            text("INSERT INTO tiqora_user_totp (user_id, secret, enabled) VALUES (:u, 'x', :t)"),
            {"u": TWOFA, "t": True},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " pw, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES ('pp55-portal', 'portal@example.org', 'pp55', 'Carla', 'Kunde', :pw, 1,"
                " current_timestamp, 1, current_timestamp, 1)"
            ),
            {"pw": local_hash},
        )
    engine.dispose()


class Env:
    def __init__(self, maker: async_sessionmaker[AsyncSession], url: str = "") -> None:
        self.maker = maker
        self.url = url

    def client(self, redis: Any = None, **settings: Any) -> httpx.AsyncClient:
        app = create_app(Settings(**{**BASE, **settings}))  # type: ignore[arg-type]
        app.state.redis = redis if redis is not None else FakeRedis()
        maker = self.maker

        async def _get_db() -> AsyncIterator[AsyncSession]:
            async with maker() as s:
                yield s

        app.dependency_overrides[get_db] = _get_db
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://helpdesk.example.org",
            headers={"Origin": "https://helpdesk.example.org"},
        )


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    engine = create_async_engine(_to_async_url(url))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    boot_points_plugin(tmp_path, monkeypatch)
    yield Env(maker, url)
    await engine.dispose()
    _clean(url)


async def _agent(
    client: httpx.AsyncClient, login: str = "pp55-agent", password: str | None = None
) -> httpx.Response:
    body = {"login": login, "password": password or f"richtig-{login}"}
    return await client.post("/api/v1/auth/login", json=body)


async def _portal(
    client: httpx.AsyncClient, login: str = "pp55-portal", password: str | None = None
) -> httpx.Response:
    body = {"login": login, "password": password or f"richtig-{login}"}
    return await client.post("/api/portal/auth/login", json=body)


def _calls(name: str) -> list[Any]:
    return [c[1] for c in points_behaviour().CALLS if c[0] == name]


async def test_agent_login_via_plugin(env: Env) -> None:
    async with env.client() as client:
        r = await _agent(client)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["login"] == "pp55-agent"
    assert r.json()["pending_2fa"] is False
    assert [c[:2] for c in _calls("auth.directory")] == [("pp55-agent", "agent")]
    assert _calls("auth.directory")[0][2] is not None  # the client ip reaches the provider


async def test_agent_wrong_password_is_401(env: Env) -> None:
    async with env.client() as client:
        r = await _agent(client, password="falsch")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert len(_calls("auth.directory")) == 1 and len(_calls("auth.backup")) == 1


async def test_customer_login_via_plugin(env: Env) -> None:
    async with env.client() as client:
        r = await _portal(client)
    assert r.status_code == 200, r.text
    assert r.json()["customer"]["login"] == "pp55-portal"
    assert [c[1] for c in _calls("auth.directory")] == ["customer"]


@pytest.mark.parametrize("mode", ["raise", "hang", "unknown_login", "invalid_login"])
async def test_provider_crash_is_no(env: Env, mode: str) -> None:
    points_behaviour().MODE["auth"] = mode
    started = time.monotonic()
    async with env.client() as client:
        r = await _agent(client)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert "tiqora_session" not in r.cookies
    assert time.monotonic() - started < 5  # a hanging provider is cut at timeout_seconds=1
    assert len(_calls("auth.backup")) == 1  # the chain went on to the next provider


async def test_customer_provider_crash_is_no(env: Env) -> None:
    points_behaviour().MODE["auth"] = "raise"
    async with env.client() as client:
        r = await _portal(client)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"


async def test_other_login_mapping_allowed(env: Env) -> None:
    points_behaviour().MODE["auth"] = "other_login"
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _agent(client, password="irgendwas")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["login"] == "pp55-agent2"
    mapped = [e for e in logs if e["event"] == "plugin_auth_mapped"]
    assert len(mapped) == 1
    assert mapped[0]["login"] == "pp55-agent" and mapped[0]["account"] == "pp55-agent2"


async def test_password_never_logged(env: Env) -> None:
    secret = "falsch-geheim-55"
    for mode in ("ok", "raise_with_password"):
        points_behaviour().MODE["auth"] = mode
        with capture_logs() as logs:
            async with env.client() as client:
                r = await _agent(client, password=secret)
        assert r.status_code == 401
        assert secret not in repr(logs)
    # the failure was logged (scrubbed), so the assertion above had something to look at
    failed = [e for e in logs if e["event"] == "plugin_call_failed"]
    assert len(failed) == 1 and "***" in failed[0]["error"]
    assert secret not in repr(points_behaviour().CALLS)


async def test_2fa_still_required(env: Env) -> None:
    async with env.client() as client:
        r = await _agent(client, "pp55-2fa")
        # the pending cookie is no session: a protected endpoint refuses it
        assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending_2fa"] is True and body["user"] is None
    assert body["totp_enrolled"] is True


async def test_rate_limit_counts_plugin_failures(env: Env) -> None:
    async with env.client(
        _MemRedis(), auth_rate_limit_enabled=True, auth_rate_limit_login_max=3
    ) as client:
        codes = [(await _agent(client, password="falsch")).status_code for _ in range(3)]
        assert codes == [401, 401, 429]
        locked = await _agent(client)  # even the right password: locked out
    assert locked.status_code == 429
    assert len(_calls("auth.directory")) == 3  # the locked attempt never reached the plugin


async def test_local_password_wins_without_calling_plugin(env: Env) -> None:
    async with env.client() as client:
        r = await _agent(client, "pp55-local", LOCAL_PASSWORD)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["login"] == "pp55-local"
    assert points_behaviour().CALLS == []


async def test_audience_respected(env: Env) -> None:
    async with env.client() as client:
        await _agent(client, password="falsch")
        assert _calls("auth.customers_only") == []
        await _portal(client, password="falsch")
    assert _calls("auth.customers_only") == [("pp55-portal", "customer")]


async def test_chain_budget(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_providers, "CHAIN_BUDGET_SECONDS", 0.5)
    points_behaviour().MODE["auth"] = "hang"
    points_behaviour().MODE["auth.backup"] = "hang"
    async with env.client() as client:
        started = time.monotonic()
        r = await _agent(client)
        elapsed = time.monotonic() - started
    assert r.status_code == 401
    assert elapsed < 1.0, elapsed  # not 1 s + 1 s: the second provider is skipped
    assert _calls("auth.backup") == []


def _scim_map(
    url: str,
    *,
    user_kind: str,
    login: str,
    require_mfa: bool,
    enabled: bool = True,
    deleted: bool = False,
) -> None:
    """A SCIM mapping for ``login`` under a new provider (R15 seed; made-up values)."""
    import hashlib
    import uuid
    from datetime import datetime

    now = datetime(2026, 10, 13, 9, 0, 0)
    slug = f"pp55scim{uuid.uuid4().hex[:8]}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_identity_provider (slug, kind, source, name, audience,"
                " enabled, sort, config, match_by, require_sso_opt_in, attribute_map,"
                " jit_enabled, jit_defaults, role_rules, sync_mode, require_mfa_claim,"
                " scim_enabled, create_by, create_time, change_by, change_time)"
                " VALUES (:slug, 'oidc', 'db', 'pp55 idp', 'both', :en, 10, '{}', 'login', :f,"
                " '{}', :f, '{}', '[]', 'managed', :mfa, :en, 1, :now, 1, :now)"
            ),
            {"slug": slug, "en": enabled, "f": False, "mfa": require_mfa, "now": now},
        )
        provider_id = conn.execute(
            text("SELECT id FROM tiqora_identity_provider WHERE slug = :s"), {"s": slug}
        ).scalar_one()
        table = "users" if user_kind == "agent" else "customer_user"
        account_id = conn.execute(
            text(f"SELECT id FROM {table} WHERE login = :l"),  # noqa: S608
            {"l": login},
        ).scalar_one()
        fold = login.casefold()
        conn.execute(
            text(
                "INSERT INTO tiqora_scim_user (provider_id, user_kind, scim_id, user_id,"
                " user_name, user_name_fold, user_name_fold_hash, deleted, version, created,"
                " changed) VALUES (:p, :k, :sid, :u, :n, :fold, :h, :d, 1, :now, :now)"
            ),
            {
                "p": provider_id,
                "k": user_kind,
                "sid": str(uuid.uuid4()),
                "u": account_id,
                "n": login,
                "fold": fold,
                "h": hashlib.sha256(fold.encode()).hexdigest(),
                "d": deleted,
                "now": now,
            },
        )
    engine.dispose()


def _refusals(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == "password_login_rejected"]


async def test_scim_mfa_managed_agent_is_refused(env: Env) -> None:
    """R15 + m2: a plugin password provider cannot sign in an agent whose SCIM provider
    requires the IdP's MFA proof, and the typed password never reaches the plugin."""
    _scim_map(env.url, user_kind="agent", login="pp55-agent", require_mfa=True)
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _agent(client)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert "tiqora_session" not in r.cookies
    (refused,) = _refusals(logs)
    assert refused["reason"] == "sso_managed" and refused["path"] == "plugin"
    assert refused["login"] == "pp55-agent" and refused["user_kind"] == "agent"
    assert points_behaviour().CALLS == []  # checked before any provider was asked


async def test_scim_mapping_without_mfa_requirement_still_signs_in(env: Env) -> None:
    _scim_map(env.url, user_kind="agent", login="pp55-agent", require_mfa=False)
    async with env.client() as client:
        r = await _agent(client)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["login"] == "pp55-agent"


@pytest.mark.parametrize(
    ("enabled", "deleted"), [(False, False), (True, True)], ids=["provider_off", "deprovisioned"]
)
async def test_disabled_provider_or_deprovisioned_mapping_does_not_block(
    env: Env, enabled: bool, deleted: bool
) -> None:
    """P2A-PM-I2 narrows R15: only an active mapping under an ENABLED provider blocks; turning
    the provider off is the admin's way back to passwords (plugin and local alike)."""
    _scim_map(
        env.url,
        user_kind="agent",
        login="pp55-agent",
        require_mfa=True,
        enabled=enabled,
        deleted=deleted,
    )
    _scim_map(
        env.url,
        user_kind="agent",
        login="pp55-local",
        require_mfa=True,
        enabled=enabled,
        deleted=deleted,
    )
    with capture_logs() as logs:
        async with env.client() as client:
            via_plugin = await _agent(client)
            local = await _agent(client, "pp55-local", LOCAL_PASSWORD)
    assert via_plugin.status_code == 200, via_plugin.text
    assert via_plugin.json()["user"]["login"] == "pp55-agent"
    assert local.status_code == 200, local.text
    assert local.json()["user"]["login"] == "pp55-local"
    assert _refusals(logs) == []


async def test_scim_mfa_check_applies_to_the_mapped_account(env: Env) -> None:
    """The check also looks at the account the provider names, not only at the typed login."""
    _scim_map(env.url, user_kind="agent", login="pp55-agent2", require_mfa=True)
    points_behaviour().MODE["auth"] = "other_login"
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _agent(client, password="irgendwas")
    assert r.status_code == 401
    (refused,) = _refusals(logs)
    assert refused["path"] == "plugin" and refused["account"] == "pp55-agent2"
    assert refused["provider"] == "org.example.points:directory"


async def test_later_provider_cannot_sign_in_the_refused_account(env: Env) -> None:
    """The chain goes on after a refusal, but a later provider naming the same SSO-managed
    account is refused as well."""
    _scim_map(env.url, user_kind="agent", login="pp55-agent2", require_mfa=True)
    points_behaviour().MODE["auth"] = "other_login"
    points_behaviour().MODE["auth.backup"] = "other_login"
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _agent(client, password="irgendwas")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert "tiqora_session" not in r.cookies
    assert [e["provider"] for e in _refusals(logs)] == [
        "org.example.points:directory",
        "org.example.points:backup",
    ]


async def test_chain_goes_on_to_an_unmanaged_account(env: Env) -> None:
    """m1: the first provider names a managed account (refused), the second an unmanaged one,
    which signs in."""
    _scim_map(env.url, user_kind="agent", login="pp55-agent2", require_mfa=True)
    points_behaviour().MODE["auth"] = "other_login"
    points_behaviour().MODE["auth.backup"] = "typed_login"
    async with env.client() as client:
        r = await _agent(client, password="irgendwas")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["login"] == "pp55-agent"
    assert len(_calls("auth.backup")) == 1


async def test_scim_mfa_managed_customer_is_refused(env: Env) -> None:
    _scim_map(env.url, user_kind="customer", login="pp55-portal", require_mfa=True)
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _portal(client)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert [(e["path"], e["user_kind"]) for e in _refusals(logs)] == [("plugin", "customer")]
    assert points_behaviour().CALLS == []


# --- P2A-PM-I2: local password and LDAP refuse SSO-managed accounts too ----------------------


class _LdapSpy:
    """Stands in for ``ldap_sign_in`` in a login route: password ``ldap-geheim`` binds and
    resolves to ``account``."""

    def __init__(self, account: str) -> None:
        self.account = account
        self.calls: list[str] = []

    async def __call__(self, session: Any, settings: Any, **kw: Any) -> str | None:
        self.calls.append(kw["login"])
        return self.account if kw["password"] == "ldap-geheim" else None


def _pw_of(url: str, uid: int) -> str:
    engine = create_engine(url)
    with engine.begin() as conn:
        pw = conn.execute(text("SELECT pw FROM users WHERE id = :u"), {"u": uid}).scalar_one()
    engine.dispose()
    return str(pw)


async def test_local_password_of_sso_managed_agent_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The right local password of an SSO-managed agent is a wrong password: 401, no session,
    no later link asked (LDAP, plugins), and the opportunistic rehash does not write."""
    import hashlib

    from tiqora.api.v1 import auth as agent_auth

    legacy = hashlib.sha256(LOCAL_PASSWORD.encode()).hexdigest()  # rehashed on a real login
    engine = create_engine(env.url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET pw = :pw WHERE id = :u"), {"pw": legacy, "u": LOCAL})
    engine.dispose()
    ldap = _LdapSpy("pp55-local")
    monkeypatch.setattr(agent_auth, "ldap_sign_in", ldap)
    _scim_map(env.url, user_kind="agent", login="pp55-local", require_mfa=True)
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _agent(client, "pp55-local", LOCAL_PASSWORD)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert "tiqora_session" not in r.cookies
    (refused,) = _refusals(logs)
    assert (refused["path"], refused["login"], refused["user_kind"]) == (
        "local",
        "pp55-local",
        "agent",
    )
    assert ldap.calls == [] and points_behaviour().CALLS == []
    assert _pw_of(env.url, LOCAL) == legacy


async def test_local_password_of_sso_managed_customer_is_refused(env: Env) -> None:
    _scim_map(env.url, user_kind="customer", login="pp55-portal", require_mfa=True)
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _portal(client, password=LOCAL_PASSWORD)
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert "tiqora_customer_session" not in r.cookies
    assert [(e["path"], e["user_kind"]) for e in _refusals(logs)] == [("local", "customer")]
    assert points_behaviour().CALLS == []


async def test_local_password_of_unmanaged_customer_signs_in(env: Env) -> None:
    async with env.client() as client:
        r = await _portal(client, password=LOCAL_PASSWORD)
    assert r.status_code == 200, r.text
    assert r.json()["customer"]["login"] == "pp55-portal"


async def test_sso_managed_refusal_counts_as_a_failure(env: Env) -> None:
    """Same failure count as a wrong password: the third refusal of the right password locks."""
    _scim_map(env.url, user_kind="agent", login="pp55-local", require_mfa=True)
    async with env.client(
        _MemRedis(), auth_rate_limit_enabled=True, auth_rate_limit_login_max=3
    ) as client:
        codes = [(await _agent(client, "pp55-local", LOCAL_PASSWORD)).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


async def test_ldap_login_of_sso_managed_agent_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory that accepts the password of an SSO-managed agent does not sign it in, and
    the plugins are not asked afterwards."""
    from tiqora.api.v1 import auth as agent_auth

    ldap = _LdapSpy("pp55-agent")
    monkeypatch.setattr(agent_auth, "ldap_sign_in", ldap)
    async with env.client() as client:
        unmanaged = await _agent(client, password="ldap-geheim")
    assert unmanaged.status_code == 200, unmanaged.text  # the fake directory works
    assert unmanaged.json()["user"]["login"] == "pp55-agent"
    points_behaviour().CALLS.clear()
    _scim_map(env.url, user_kind="agent", login="pp55-agent", require_mfa=True)
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _agent(client, password="ldap-geheim")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert "tiqora_session" not in r.cookies
    assert [e["path"] for e in _refusals(logs)] == ["ldap"]
    assert ldap.calls == ["pp55-agent", "pp55-agent"]
    assert points_behaviour().CALLS == []


async def test_ldap_login_of_sso_managed_customer_is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.api.portal import auth as portal_auth

    ldap = _LdapSpy("pp55-portal")
    monkeypatch.setattr(portal_auth, "ldap_sign_in", ldap)
    _scim_map(env.url, user_kind="customer", login="pp55-portal", require_mfa=True)
    with capture_logs() as logs:
        async with env.client() as client:
            r = await _portal(client, password="ldap-geheim")
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"
    assert [(e["path"], e["user_kind"]) for e in _refusals(logs)] == [("ldap", "customer")]
    assert ldap.calls == ["pp55-portal"] and points_behaviour().CALLS == []
