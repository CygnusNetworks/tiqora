"""Shared bind-search-bind LDAP core, used by both agent and customer auth.

Ports the semantics of Znuny's ``Kernel::System::Auth::LDAP`` /
``Kernel::System::CustomerAuth::LDAP`` (see ``znuny-6.5.22/Kernel/System/Auth/LDAP.pm``
and ``.../CustomerAuth/LDAP.pm``, both read-only references):

1. Connect and bind with the configured search account (``ldap_bind_dn`` /
   ``ldap_bind_password``), or anonymously if unset.
2. Search ``ldap_base_dn`` for ``(<uid_attr>=<login>)`` (optionally ANDed
   with ``ldap_always_filter``) to resolve the user's DN.
3. If a group DN is configured, verify the user's DN (or UID, depending on
   ``ldap_user_attr``) is a member per ``ldap_access_attr`` under that group.
4. Re-bind as the resolved DN with the supplied password — this *is* the
   actual authentication check.

``ldap3`` is a synchronous, C-extension-adjacent library, so every call is
dispatched via ``run_in_executor`` per the project's async rule. Import is
indirected through :func:`_import_ldap3` so tests can substitute a fake/mock
module (ldap3's own ``MOCK_SYNC`` strategy is used in the real test suite).

Simplifications vs. Znuny:

* No ``Die`` config knob — connection errors are always logged and treated
  as auth failure (never a hard process crash).
* No charset conversion knob (``AuthModule::LDAP::Charset``) — Tiqora is
  UTF-8 throughout.
* No ``UserSuffix`` / ``UserLowerCase`` knobs — callers are expected to pass
  the login as typed; add these back if a deployment needs them.
* No auto-provisioning: the resolved LDAP UID must match an existing valid
  local user row, checked by the caller (``auth_ldap.py`` /
  ``customer_auth_ldap.py``), not by this module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class LdapUnavailable(Exception):
    """Raised when ldap3 is not installed."""


def _import_ldap3() -> Any:
    try:
        import ldap3
    except ImportError as exc:
        raise LdapUnavailable(
            "ldap3 is not installed; it is a core dependency, this should not happen"
        ) from exc
    return ldap3


@dataclass(frozen=True, slots=True)
class LdapConfig:
    """Directory connection + search parameters for one LDAP backend."""

    host: str
    port: int = 389
    use_ssl: bool = False
    use_starttls: bool = False
    base_dn: str = ""
    bind_dn: str = ""
    bind_password: str = ""
    uid_attr: str = "uid"
    always_filter: str = ""
    group_dn: str = ""
    access_attr: str = "memberUid"
    user_attr: str = "DN"


def _escape_filter_value(ldap3: Any, value: str) -> str:
    return str(ldap3.utils.conv.escape_filter_chars(value))


def _authenticate_sync(config: LdapConfig, login: str, password: str) -> str | None:
    """Bind-search-bind. Returns the LDAP UID on success, ``None`` on failure."""
    ldap3 = _import_ldap3()

    login = login.strip()
    if not login or not password:
        return None

    try:
        server = ldap3.Server(
            config.host,
            port=config.port,
            use_ssl=config.use_ssl,
            get_info=ldap3.NONE,
        )
        conn = ldap3.Connection(
            server,
            user=config.bind_dn or None,
            password=config.bind_password or None,
            auto_bind=False,
        )
        if not conn.bind():
            logger.error("ldap_first_bind_failed", host=config.host, error=str(conn.result))
            return None
    except Exception as exc:  # noqa: BLE001 — any connection error is an auth failure
        logger.error("ldap_connect_failed", host=config.host, error=str(exc))
        return None

    try:
        if config.use_starttls:
            conn.start_tls()

        filt = f"({config.uid_attr}={_escape_filter_value(ldap3, login)})"
        if config.always_filter:
            filt = f"(&{filt}{config.always_filter})"

        if not conn.search(
            search_base=config.base_dn,
            search_filter=filt,
            attributes=[config.uid_attr],
        ):
            logger.warning("ldap_search_failed", base_dn=config.base_dn, filter=filt)
            return None

        entries = conn.entries
        if not entries:
            logger.info("ldap_no_user_entry", login=login, base_dn=config.base_dn, filter=filt)
            return None

        entry = entries[0]
        user_dn = str(entry.entry_dn)
        uid_values = entry[config.uid_attr].values if config.uid_attr in entry else []
        user_uid = str(uid_values[0]) if uid_values else login

        if config.access_attr and config.group_dn:
            member_value = user_dn if config.user_attr == "DN" else login
            group_filter = f"({config.access_attr}={_escape_filter_value(ldap3, member_value)})"
            if not conn.search(
                search_base=config.group_dn,
                search_filter=group_filter,
                attributes=["1.1"],
            ):
                logger.warning(
                    "ldap_group_search_failed", group_dn=config.group_dn, filter=group_filter
                )
                return None
            if not conn.entries:
                logger.info(
                    "ldap_group_membership_missing",
                    login=login,
                    group_dn=config.group_dn,
                    filter=group_filter,
                )
                return None

        # Re-bind as the resolved user DN with the supplied password: this
        # is the actual credential check.
        user_conn = ldap3.Connection(server, user=user_dn, password=password, auto_bind=False)
        try:
            if not user_conn.bind():
                logger.info("ldap_user_bind_failed", login=login, user_dn=user_dn)
                return None
        finally:
            user_conn.unbind()

        logger.info("ldap_auth_ok", login=login, user_dn=user_dn)
        return user_uid
    finally:
        conn.unbind()


async def ldap_authenticate(config: LdapConfig, login: str, password: str) -> str | None:
    """Async wrapper around :func:`_authenticate_sync` via ``run_in_executor``."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _authenticate_sync, config, login, password)
