"""P2A-PM-I2b: the SSO-managed-account check on the Znuny-compat GenericInterface and in
``ldap_sign_in`` (both dialects, through the real routes).

An account mapped by SCIM under an ENABLED provider with ``require_mfa_claim`` signs in through
its IdP only. The compat ``SessionCreate`` and the per-request ``UserLogin``/``CustomerUserLogin``
+ ``Password`` auth answer its (right) password exactly like a wrong one: same status, same body,
counted by the rate limiter, no session, no rehash; the log says ``path=compat``. A directory
login of such an account is refused between ``plan_login`` and ``apply_login`` (m-c), so nothing
is written.

Reuses the seed band, the ``env`` fixture and ``_scim_map`` of ``test_plugins_auth_db``
(55370-55389, ``pp55-*``, ``pp55scim*``); Znuny ``sessions`` rows a successful SessionCreate
writes are deleted by their SessionID.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from structlog.testing import capture_logs

from tests import test_plugins_auth_db as _auth_db
from tests._plugin_points import points_behaviour
from tests.test_auth_hardening import _MemRedis
from tests.test_plugins_auth_db import (
    LOCAL,
    LOCAL_PASSWORD,
    TWOFA,
    Env,
    _refusals,
    _scim_map,
)

pytestmark = pytest.mark.db

# The module fixture as an attribute, which pytest collects (an import would trip F811).
env = _auth_db.env

LEGACY = hashlib.sha256(LOCAL_PASSWORD.encode()).hexdigest()  # rehashed on a real login
WRONG = "falsch-55"


def _sql(url: str, sql: str, **params: Any) -> Any:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return result.scalar() if result.returns_rows else None
    finally:
        engine.dispose()


def _legacy_hash(url: str, *, agent_id: int | None = None, customer: str | None = None) -> None:
    if agent_id is not None:
        _sql(url, "UPDATE users SET pw = :pw WHERE id = :u", pw=LEGACY, u=agent_id)
    if customer is not None:
        _sql(url, "UPDATE customer_user SET pw = :pw WHERE login = :l", pw=LEGACY, l=customer)


def _agent_pw(url: str, uid: int = LOCAL) -> str:
    return str(_sql(url, "SELECT pw FROM users WHERE id = :u", u=uid))


def _customer_pw(url: str) -> str:
    return str(_sql(url, "SELECT pw FROM customer_user WHERE login = 'pp55-portal'"))


def _session_rows(url: str) -> int:
    return int(_sql(url, "SELECT COUNT(*) FROM sessions"))


def _drop_session(url: str, sid: str) -> None:
    _sql(url, "DELETE FROM sessions WHERE session_id = :s", s=sid)


async def _session_create(client: httpx.AsyncClient, kind: str, password: str) -> httpx.Response:
    body = (
        {"UserLogin": "pp55-local", "Password": password}
        if kind == "agent"
        else {"CustomerUserLogin": "pp55-portal", "Password": password}
    )
    return await client.post("/znuny-compat/Session", json=body)


async def _ticket_search(client: httpx.AsyncClient, kind: str, password: str) -> httpx.Response:
    params: dict[str, Any] = (
        {"UserLogin": "pp55-local", "Password": password, "Limit": 1}
        if kind == "agent"
        else {"CustomerUserLogin": "pp55-portal", "Password": password, "Limit": 1}
    )
    return await client.get("/znuny-compat/TicketSearch", params=params)


def _compat_refusals(logs: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return [(e["path"], e["user_kind"], e["login"]) for e in _refusals(logs)]


@pytest.mark.parametrize("write_znuny", [True, False], ids=["znuny_sessions", "store_token"])
async def test_session_create_refuses_sso_managed_agent(env: Env, write_znuny: bool) -> None:
    """The right local password of an SSO-managed agent mints no compat session (neither a
    Znuny ``sessions`` row nor a store token that would also work as a UI cookie), answers
    exactly like a wrong password and leaves the legacy hash alone."""
    _legacy_hash(env.url, agent_id=LOCAL)
    _scim_map(env.url, user_kind="agent", login="pp55-local", require_mfa=True)
    before = _session_rows(env.url)
    with capture_logs() as logs:
        async with env.client(compat_session_write_znuny=write_znuny) as client:
            refused = await _session_create(client, "agent", LOCAL_PASSWORD)
            wrong = await _session_create(client, "agent", WRONG)
    assert "SessionID" not in refused.json(), refused.text
    assert (refused.status_code, refused.json()) == (wrong.status_code, wrong.json())
    assert refused.json()["Error"] == {
        "ErrorCode": "SessionCreate.AuthFail",
        "ErrorMessage": "SessionCreate: Authorization failing!",
    }
    assert refused.status_code == 401
    assert _compat_refusals(logs) == [("compat", "agent", "pp55-local")]
    assert _session_rows(env.url) == before
    assert _agent_pw(env.url) == LEGACY  # no rehash on a refusal


@pytest.mark.parametrize("write_znuny", [True, False], ids=["znuny_sessions", "store_token"])
async def test_session_create_refuses_sso_managed_customer(env: Env, write_znuny: bool) -> None:
    _legacy_hash(env.url, customer="pp55-portal")
    _scim_map(env.url, user_kind="customer", login="pp55-portal", require_mfa=True)
    before = _session_rows(env.url)
    with capture_logs() as logs:
        async with env.client(compat_session_write_znuny=write_znuny) as client:
            refused = await _session_create(client, "customer", LOCAL_PASSWORD)
            wrong = await _session_create(client, "customer", WRONG)
    assert "SessionID" not in refused.json(), refused.text
    assert (refused.status_code, refused.json()) == (wrong.status_code, wrong.json())
    assert refused.json()["Error"]["ErrorCode"] == "SessionCreate.AuthFail"
    assert _compat_refusals(logs) == [("compat", "customer", "pp55-portal")]
    assert _session_rows(env.url) == before
    assert _customer_pw(env.url) == LEGACY


@pytest.mark.parametrize("kind", ["agent", "customer"])
async def test_per_request_password_refuses_sso_managed_account(env: Env, kind: str) -> None:
    """``UserLogin``/``CustomerUserLogin`` + ``Password`` on an operation (TicketSearch): the
    same AuthFail as a wrong password, and no rehash."""
    if kind == "agent":
        _legacy_hash(env.url, agent_id=LOCAL)
    else:
        _legacy_hash(env.url, customer="pp55-portal")
    login = "pp55-local" if kind == "agent" else "pp55-portal"
    _scim_map(env.url, user_kind=kind, login=login, require_mfa=True)
    with capture_logs() as logs:
        async with env.client() as client:
            refused = await _ticket_search(client, kind, LOCAL_PASSWORD)
            wrong = await _ticket_search(client, kind, WRONG)
    assert (refused.status_code, refused.json()) == (wrong.status_code, wrong.json())
    assert refused.status_code == 401
    assert refused.json()["Error"]["ErrorCode"] == "TicketSearch.AuthFail"
    assert _compat_refusals(logs) == [("compat", kind, login)]
    assert (_agent_pw(env.url) if kind == "agent" else _customer_pw(env.url)) == LEGACY


_PLUGIN_WS_ID = 55438
_PLUGIN_WS = "PP55SsoContracts"
_PLUGIN_PATH = f"/znuny-compat/Webservice/{_PLUGIN_WS}/Contract/1"


@pytest.mark.parametrize("kind", ["agent", "customer"])
async def test_plugin_gi_operation_refuses_sso_managed_account(env: Env, kind: str) -> None:
    """A plugin GI operation authenticates through ``ctx.authenticate`` (``_auth_from_params``,
    the gated function): the right password of an SSO-managed account is answered with the
    operation's AuthFail exactly like a wrong one, while an unmanaged account passes."""
    from tests._gi_fixtures import insert_webservice
    from tests.test_plugins_gi_provider_db import CONFIG

    login = "pp55-local" if kind == "agent" else "pp55-portal"
    key = "UserLogin" if kind == "agent" else "CustomerUserLogin"
    from tests._row_cleanup import delete_above_marks, snapshot_marks

    marks = snapshot_marks(env.url, ("tiqora_gi_debug_content", "tiqora_gi_debug_entry"))
    engine = create_engine(env.url)
    try:
        with engine.begin() as conn:
            insert_webservice(conn, ws_id=_PLUGIN_WS_ID, name=_PLUGIN_WS, config=CONFIG)
        async with env.client() as client:
            unmanaged = await client.post(
                _PLUGIN_PATH, json={key: login, "Password": LOCAL_PASSWORD}
            )
        assert unmanaged.status_code == 200, unmanaged.text
        assert unmanaged.json()["Contract"] == "V-1"
        _scim_map(env.url, user_kind=kind, login=login, require_mfa=True)
        with capture_logs() as logs:
            async with env.client() as client:
                refused = await client.post(
                    _PLUGIN_PATH, json={key: login, "Password": LOCAL_PASSWORD}
                )
                wrong = await client.post(_PLUGIN_PATH, json={key: login, "Password": WRONG})
        assert (refused.status_code, refused.json()) == (wrong.status_code, wrong.json())
        assert refused.status_code == 401
        assert refused.json()["Error"]["ErrorCode"].endswith("AuthFail")
        assert "Contract" not in refused.text
        assert _compat_refusals(logs) == [("compat", kind, login)]
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM gi_webservice_config WHERE id = :i"), {"i": _PLUGIN_WS_ID}
            )
        engine.dispose()
        delete_above_marks(env.url, marks)


_CI_OPERATIONS = [
    ("ConfigItemGet", "GET", "/znuny-compat/ConfigItem/1", None),
    ("ConfigItemSearch", "POST", "/znuny-compat/ConfigItemSearch", {"Class": "Computer"}),
    ("ConfigItemCreate", "POST", "/znuny-compat/ConfigItem", {"Class": "Computer"}),
    ("ConfigItemUpdate", "PATCH", "/znuny-compat/ConfigItem/1", {"Name": "pp55-ci"}),
    ("ConfigItemDelete", "DELETE", "/znuny-compat/ConfigItem/1", None),
]


@pytest.mark.parametrize(
    ("op", "method", "path", "item"), _CI_OPERATIONS, ids=[o[0] for o in _CI_OPERATIONS]
)
async def test_config_item_operations_refuse_sso_managed_agent(
    env: Env, op: str, method: str, path: str, item: dict[str, Any] | None
) -> None:
    """The ITSM import's ``ConfigItem*`` operations authenticate through the same gated
    ``_auth_from_params``: an SSO-managed agent's right password gets the wrong-password answer
    (``<Op>.AuthFail``) before any mode or parameter check, and is not rehashed."""
    _legacy_hash(env.url, agent_id=LOCAL)
    _scim_map(env.url, user_kind="agent", login="pp55-local", require_mfa=True)

    async def call(client: httpx.AsyncClient, password: str) -> httpx.Response:
        body: dict[str, Any] = {"UserLogin": "pp55-local", "Password": password}
        if item is not None:
            body["ConfigItem"] = item
        if method in ("GET", "DELETE"):
            return await client.request(method, path, params=body)
        return await client.request(method, path, json=body)

    with capture_logs() as logs:
        async with env.client() as client:
            refused = await call(client, LOCAL_PASSWORD)
            wrong = await call(client, WRONG)
    assert (refused.status_code, refused.json()) == (wrong.status_code, wrong.json())
    assert refused.status_code == 401, refused.text
    assert refused.json()["Error"]["ErrorCode"] == f"{op}.AuthFail"
    assert _compat_refusals(logs) == [("compat", "agent", "pp55-local")]
    assert _agent_pw(env.url) == LEGACY


@pytest.mark.parametrize(
    ("enabled", "deleted"), [(False, False), (True, True)], ids=["provider_off", "deprovisioned"]
)
async def test_disabled_provider_or_deprovisioned_mapping_restores_compat_passwords(
    env: Env, enabled: bool, deleted: bool
) -> None:
    """The narrowing of P2A-PM-I2 holds on the GenericInterface too: SessionCreate and the
    per-request password work again, and the weak hash is rehashed (so the "no rehash"
    assertions above are not vacuous)."""
    _legacy_hash(env.url, agent_id=LOCAL)
    for kind, login in (("agent", "pp55-local"), ("customer", "pp55-portal")):
        _scim_map(
            env.url,
            user_kind=kind,
            login=login,
            require_mfa=True,
            enabled=enabled,
            deleted=deleted,
        )
    sids: list[str] = []
    try:
        with capture_logs() as logs:
            async with env.client() as client:
                agent = await _session_create(client, "agent", LOCAL_PASSWORD)
                sids += [agent.json().get("SessionID") or ""]
                customer = await _session_create(client, "customer", LOCAL_PASSWORD)
                sids += [customer.json().get("SessionID") or ""]
                search = await _ticket_search(client, "agent", LOCAL_PASSWORD)
        assert agent.status_code == 200 and sids[0], agent.text
        assert customer.status_code == 200 and sids[1], customer.text
        assert search.status_code == 200 and "Error" not in search.json(), search.text
        assert _refusals(logs) == []
        assert _agent_pw(env.url) != LEGACY  # rehashed on success
    finally:
        for sid in sids:
            if sid:
                _drop_session(env.url, sid)


async def test_compat_refusal_counts_as_a_failure(env: Env) -> None:
    """Same failure count as a wrong password: the third refused SessionCreate locks."""
    _scim_map(env.url, user_kind="agent", login="pp55-local", require_mfa=True)
    async with env.client(
        _MemRedis(), auth_rate_limit_enabled=True, auth_rate_limit_login_max=3
    ) as client:
        codes = [
            (await _session_create(client, "agent", LOCAL_PASSWORD)).status_code for _ in range(3)
        ]
        locked = await _ticket_search(client, "agent", LOCAL_PASSWORD)
    assert codes == [401, 401, 429]
    assert locked.status_code == 429
    assert locked.json()["Error"]["ErrorCode"].endswith(".RateLimited")


async def test_sso_check_runs_before_the_2fa_check(env: Env) -> None:
    """A managed agent with Tiqora TOTP gets the wrong-password text, not the 2FA hint (which
    would tell a password holder that the password was right)."""
    _sql(
        env.url,
        "UPDATE users SET pw = (SELECT pw FROM users WHERE id = :l) WHERE id = :t",
        l=LOCAL,
        t=TWOFA,
    )
    body = {"UserLogin": "pp55-2fa", "Password": LOCAL_PASSWORD}
    async with env.client() as client:
        unmanaged = await client.post("/znuny-compat/Session", json=body)
        _scim_map(env.url, user_kind="agent", login="pp55-2fa", require_mfa=True)
        managed = await client.post("/znuny-compat/Session", json=body)
    assert "2FA" in unmanaged.json()["Error"]["ErrorMessage"]  # the hint without a mapping
    assert managed.json()["Error"] == {
        "ErrorCode": "SessionCreate.AuthFail",
        "ErrorMessage": "SessionCreate: Authorization failing!",
    }


# --- m-c: ldap_sign_in refuses before apply_login writes anything --------------------------


def _ldap_record(user_kind: str) -> Any:
    from tiqora.identity.config_models import JitDefaults, LdapProviderConfig
    from tiqora.identity.presets import get_preset
    from tiqora.identity.store import ProviderRecord

    return ProviderRecord(
        id=955370,  # not stored: plan_login only reads links by provider id
        slug="pp55ldap-unit",
        kind="ldap",
        source="db",
        name="Verzeichnis",
        audience=user_kind,  # type: ignore[arg-type]
        enabled=True,
        sort=0,
        button_label=None,
        button_icon=None,
        preset="ldap",
        config=LdapProviderConfig(host="dir.example.org"),
        secrets_set=frozenset(),
        match_by="login",
        require_sso_opt_in=False,
        attribute_map=get_preset("ldap").attribute_map,
        jit_enabled=False,
        jit_defaults=JitDefaults(),
        role_rules=(),
        sync_mode="managed",
        metadata_xml=None,
        metadata_fetched_at=None,
        metadata_error=None,
    )


class _FakeDirectory:
    """``ldap_password_login`` stand-in: ``ldap-geheim`` binds as ``uid``; ``apply_login`` is
    recorded and answers like the real one would for an existing account."""

    def __init__(self, uid: str, user_kind: str) -> None:
        self.uid = uid
        self.record = _ldap_record(user_kind)
        self.applied: list[tuple[str, int | None]] = []

    async def login(self, session: Any, settings: Any, **kw: Any) -> Any:
        from tiqora.domain._ldap_core import LdapEntry
        from tiqora.identity.ldap_login import identity_from_entry

        if kw["password"] != "ldap-geheim":
            return None
        entry = LdapEntry(
            uid=self.uid,
            dn=f"uid={self.uid},ou=people,dc=example,dc=org",
            attributes={"entryUUID": (f"uuid-{self.uid}",)},
            member_group_dns=(),
        )
        return self.record, identity_from_entry(self.record, entry)

    async def apply(self, session: Any, record: Any, identity: Any, plan: Any, **kw: Any) -> Any:
        self.applied.append((plan.decision, plan.user_id))
        return plan.user_id, plan.login


@pytest.mark.parametrize("kind", ["agent", "customer"])
async def test_ldap_refusal_writes_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """The real ``ldap_sign_in`` asks the gate between ``plan_login`` and ``apply_login``: an
    unmanaged account is applied and signs in; a managed one is refused before any write."""
    from tiqora.identity import ldap_login

    uid = "pp55-local" if kind == "agent" else "pp55-portal"
    directory = _FakeDirectory(uid, kind)
    monkeypatch.setattr(ldap_login, "ldap_password_login", directory.login)
    monkeypatch.setattr(ldap_login, "apply_login", directory.apply)
    path = "/api/v1/auth/login" if kind == "agent" else "/api/portal/auth/login"
    body = {"login": uid, "password": "ldap-geheim"}
    async with env.client() as client:
        unmanaged = await client.post(path, json=body)
    assert unmanaged.status_code == 200, unmanaged.text  # the fake directory works
    assert [d for d, _ in directory.applied] == ["login"]
    directory.applied.clear()
    points_behaviour().CALLS.clear()
    _scim_map(env.url, user_kind=kind, login=uid, require_mfa=True)
    with capture_logs() as logs:
        async with env.client() as client:
            refused = await client.post(path, json=body)
    assert refused.status_code == 401 and refused.json()["detail"] == "Invalid credentials"
    assert directory.applied == []  # refused before apply_login
    assert [(e["path"], e["user_kind"]) for e in _refusals(logs)] == [("ldap", kind)]
    assert points_behaviour().CALLS == []


@pytest.mark.parametrize("managed", [True, False], ids=["managed", "unmanaged"])
async def test_apply_login_race_replan_asks_the_gate_before_writing(
    env: Env, monkeypatch: pytest.MonkeyPatch, managed: bool
) -> None:
    """m-i: a ``create`` plan that loses the JIT race re-plans to ``login`` on an existing
    account. When that account is SSO-managed, the refuse check runs on the re-plan and ends the
    login before the profile sync (nothing written); an unmanaged account is synced."""
    import dataclasses

    from tiqora.config import Settings
    from tiqora.identity import login as login_mod
    from tiqora.identity.login import LoginRejected, apply_login, plan_login
    from tiqora.identity.sso_managed import SsoManagedGate

    directory = _FakeDirectory("pp55-local", "agent")
    found = await directory.login(None, None, password="ldap-geheim")
    record, identity = found
    settings = Settings(environment="test")
    synced: list[int] = []

    async def lost_race(*_a: Any, **_kw: Any) -> Any:
        raise login_mod._RaceLost

    async def sync(_session: Any, _plan: Any, user_id: int) -> None:
        synced.append(user_id)

    monkeypatch.setattr(login_mod, "_create_account", lost_race)
    monkeypatch.setattr(login_mod, "_sync_existing", sync)
    monkeypatch.setattr(login_mod, "_stores_links", lambda _provider: False)  # no stored provider
    if managed:
        _scim_map(env.url, user_kind="agent", login="pp55-local", require_mfa=True)
    with capture_logs() as logs:
        async with env.maker() as session:
            real = await plan_login(session, record, identity, user_kind="agent", settings=settings)
            assert (real.decision, real.user_id) == ("login", LOCAL)
            create_plan = dataclasses.replace(real, decision="create", user_id=None, login=None)
            gate = SsoManagedGate(session, "agent", "pp55-local")
            if managed:
                with pytest.raises(LoginRejected):
                    await apply_login(
                        session, record, identity, create_plan, settings=settings, refuse=gate.ldap
                    )
            else:
                await apply_login(
                    session, record, identity, create_plan, settings=settings, refuse=gate.ldap
                )
    if managed:
        assert synced == []
        assert gate.refused
        assert [(e["path"], e["user_kind"]) for e in _refusals(logs)] == [("ldap", "agent")]
    else:
        assert synced == [LOCAL]
        assert not gate.refused
