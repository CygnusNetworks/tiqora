"""In-memory LDAP directories for tests: real ldap3 ``MOCK_SYNC``, one DIT per host.

``install_directory(monkeypatch)`` replaces ``_ldap_core._import_ldap3`` with a shim whose
``Server(host)`` returns that host's mock server (unknown host -> socket error, like an
unreachable directory) and whose ``Connection`` is a recording ``MOCK_SYNC`` connection:
``events`` (open/start_tls/bind/rebind/search order), ``filters`` (every search filter)
and ``bind_users`` let tests check StartTLS ordering, escaping and the search bind.
ldap3's mock refuses StartTLS, so the recording connection answers it itself
(``starttls_ok``).
"""

from __future__ import annotations

from typing import Any

import ldap3
import pytest
from ldap3.core.exceptions import LDAPSocketOpenError

from tiqora.domain import _ldap_core

BIND_DN = "cn=admin,dc=example,dc=org"
BIND_PW = "admin-secret"  # noqa: S105 - test directory only


class Directory:
    def __init__(self) -> None:
        self.servers: dict[str, ldap3.Server] = {}
        self.events: list[str] = []
        self.filters: list[str] = []
        self.bind_users: list[str | None] = []
        self.starttls_ok = True

    def server(self, host: str) -> ldap3.Server:
        srv = ldap3.Server(host, get_info=ldap3.NONE)
        self.servers[host] = srv
        self.add(srv, BIND_DN, userPassword=BIND_PW, sn="admin", objectClass="person")
        return srv

    def _admin(self, srv: ldap3.Server) -> ldap3.Connection:
        return ldap3.Connection(
            srv, user=BIND_DN, password=BIND_PW, client_strategy=ldap3.MOCK_SYNC
        )

    def add(self, srv: ldap3.Server, dn: str, **attributes: Any) -> None:
        assert self._admin(srv).strategy.add_entry(dn, attributes)

    def replace(self, host: str, dn: str, **attributes: Any) -> None:
        conn = self._admin(self.servers[host])
        assert conn.bind()
        changes = {
            k: [(ldap3.MODIFY_REPLACE, v if isinstance(v, list) else [v])]
            for k, v in attributes.items()
        }
        assert conn.modify(dn, changes), conn.result

    def delete_value(self, host: str, dn: str, attribute: str, value: str) -> None:
        conn = self._admin(self.servers[host])
        assert conn.bind()
        assert conn.modify(dn, {attribute: [(ldap3.MODIFY_DELETE, [value])]}), conn.result


def install_directory(monkeypatch: pytest.MonkeyPatch) -> Directory:
    directory = Directory()

    class RecordingConnection(ldap3.Connection):  # type: ignore[misc]
        _in_rebind = False

        def open(self, *args: Any, **kwargs: Any) -> Any:
            directory.events.append("open")
            return super().open(*args, **kwargs)

        def start_tls(self, *args: Any, **kwargs: Any) -> bool:
            directory.events.append("start_tls")
            return directory.starttls_ok

        def bind(self, *args: Any, **kwargs: Any) -> Any:
            if not self._in_rebind:
                directory.events.append("bind")
                directory.bind_users.append(self.user)
            return super().bind(*args, **kwargs)

        def rebind(self, *args: Any, **kwargs: Any) -> Any:
            directory.events.append("rebind")
            self._in_rebind = True
            try:
                return super().rebind(*args, **kwargs)
            finally:
                self._in_rebind = False

        def search(self, search_base: str, search_filter: str, *args: Any, **kwargs: Any) -> Any:
            directory.events.append("search")
            directory.filters.append(search_filter)
            return super().search(search_base, search_filter, *args, **kwargs)

    class Shim:
        NONE = ldap3.NONE
        utils = ldap3.utils

        @staticmethod
        def Server(host: str, **kwargs: Any) -> ldap3.Server:  # noqa: N802 - ldap3 API
            if host not in directory.servers:
                raise LDAPSocketOpenError(f"cannot reach {host}")
            return directory.servers[host]

        @staticmethod
        def Connection(  # noqa: N802 - ldap3 API
            srv: ldap3.Server, user: str | None = None, password: str | None = None, **kwargs: Any
        ) -> ldap3.Connection:
            return RecordingConnection(
                srv, user=user, password=password, client_strategy=ldap3.MOCK_SYNC
            )

    monkeypatch.setattr(_ldap_core, "_import_ldap3", lambda: Shim())
    return directory
