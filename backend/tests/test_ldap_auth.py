"""Unit tests for LDAP/AD bind-search-bind auth (agent + customer portal).

Uses ldap3's own in-memory ``MOCK_SYNC`` client strategy — a real ``ldap3``
directory simulation, not a hand-rolled fake — so the bind-search-bind
sequence (first bind, user search, optional group search, re-bind as the
resolved user DN) is exercised against real ldap3 semantics. ``ldap3`` calls
are indirected through ``_import_ldap3`` (see ``domain/_ldap_core.py``) so we
can substitute a thin shim that forces ``client_strategy=MOCK_SYNC`` while
passing everything else straight through to the real module.
"""

from __future__ import annotations

from typing import Any

import ldap3
import pytest

from tiqora.config import Settings
from tiqora.domain import _ldap_core
from tiqora.domain.auth_ldap import LdapAuthService
from tiqora.domain.customer_auth_ldap import CustomerLdapAuthService

BASE_DN = "dc=example,dc=com"
BIND_DN = "cn=admin,dc=example,dc=com"
BIND_PW = "admin-secret"
GROUP_DN = "cn=helpdesk,ou=groups,dc=example,dc=com"


def _seed_directory(server: ldap3.Server) -> None:
    """Seed a shared in-memory DIT (MOCK_SYNC entries persist per Server object)."""
    seeder = ldap3.Connection(
        server, user=BIND_DN, password=BIND_PW, client_strategy=ldap3.MOCK_SYNC
    )
    seeder.strategy.add_entry(
        BIND_DN, {"userPassword": BIND_PW, "sn": "admin", "objectClass": "person"}
    )
    seeder.strategy.add_entry(
        "uid=alice,ou=people,dc=example,dc=com",
        {
            "uid": "alice",
            "userPassword": "alice-secret",
            "sn": "Alice",
            "objectClass": "inetOrgPerson",
        },
    )
    seeder.strategy.add_entry(
        GROUP_DN,
        {
            "cn": "helpdesk",
            "objectClass": "groupOfNames",
            "memberUid": "alice",
        },
    )
    assert seeder.bind()


@pytest.fixture
def mock_ldap3(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch ``_import_ldap3`` to return real ldap3 with Connection forced to MOCK_SYNC."""
    server = ldap3.Server("mock-directory")
    _seed_directory(server)

    class _Shim:
        NONE = ldap3.NONE
        utils = ldap3.utils

        @staticmethod
        def Server(host: str, **kwargs: Any) -> ldap3.Server:
            return server

        @staticmethod
        def Connection(
            srv: ldap3.Server, user: str | None = None, password: str | None = None, **kwargs: Any
        ) -> ldap3.Connection:
            return ldap3.Connection(
                srv, user=user, password=password, client_strategy=ldap3.MOCK_SYNC
            )

    monkeypatch.setattr(_ldap_core, "_import_ldap3", lambda: _Shim())
    return _Shim()


def _settings(**overrides: Any) -> Settings:
    base = dict(
        ldap_enabled=True,
        ldap_host="mock-directory",
        ldap_base_dn=BASE_DN,
        ldap_bind_dn=BIND_DN,
        ldap_bind_password=BIND_PW,
        ldap_uid_attr="uid",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_bind_search_bind_success_returns_uid(mock_ldap3: Any) -> None:
    service = LdapAuthService(_settings())
    uid = await service.authenticate("alice", "alice-secret")
    assert uid == "alice"


@pytest.mark.asyncio
async def test_bind_search_bind_wrong_password_rejected(mock_ldap3: Any) -> None:
    service = LdapAuthService(_settings())
    assert await service.authenticate("alice", "not-the-password") is None


@pytest.mark.asyncio
async def test_unknown_user_rejected(mock_ldap3: Any) -> None:
    service = LdapAuthService(_settings())
    assert await service.authenticate("nosuchuser", "whatever") is None


@pytest.mark.asyncio
async def test_group_mapping_allows_member(mock_ldap3: Any) -> None:
    service = LdapAuthService(
        _settings(
            ldap_group_dn=GROUP_DN,
            ldap_access_attr="memberUid",
            ldap_user_attr="uid",
        )
    )
    uid = await service.authenticate("alice", "alice-secret")
    assert uid == "alice"


@pytest.mark.asyncio
async def test_group_mapping_rejects_non_member(mock_ldap3: Any) -> None:
    service = LdapAuthService(
        _settings(
            ldap_group_dn=GROUP_DN,
            ldap_access_attr="memberUid",
            ldap_user_attr="uid",
            ldap_uid_attr="uid",
        )
    )
    # "bob" isn't seeded at all, so this also covers the "no such user"
    # short-circuit before the group check would even run.
    assert await service.authenticate("bob", "whatever") is None


@pytest.mark.asyncio
async def test_customer_ldap_bind_search_bind_success(mock_ldap3: Any) -> None:
    settings = Settings(
        customer_ldap_enabled=True,
        customer_ldap_host="mock-directory",
        customer_ldap_base_dn=BASE_DN,
        customer_ldap_bind_dn=BIND_DN,
        customer_ldap_bind_password=BIND_PW,
        customer_ldap_uid_attr="uid",
    )
    service = CustomerLdapAuthService(settings)
    uid = await service.authenticate("alice", "alice-secret")
    assert uid == "alice"


@pytest.mark.asyncio
async def test_customer_ldap_unknown_user_rejected(mock_ldap3: Any) -> None:
    settings = Settings(
        customer_ldap_enabled=True,
        customer_ldap_host="mock-directory",
        customer_ldap_base_dn=BASE_DN,
        customer_ldap_bind_dn=BIND_DN,
        customer_ldap_bind_password=BIND_PW,
        customer_ldap_uid_attr="uid",
    )
    service = CustomerLdapAuthService(settings)
    assert await service.authenticate("ghost", "whatever") is None


@pytest.mark.asyncio
async def test_empty_credentials_rejected_without_network(mock_ldap3: Any) -> None:
    service = LdapAuthService(_settings())
    assert await service.authenticate("", "whatever") is None
    assert await service.authenticate("alice", "") is None
