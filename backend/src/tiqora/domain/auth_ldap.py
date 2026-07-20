"""LDAP/AD agent authentication (bind-search-bind against `users.login`).

Ports ``Kernel::System::Auth::LDAP`` (see ``znuny-6.5.22/Kernel/System/Auth/LDAP.pm``,
read-only reference). No auto-provisioning in v1: the LDAP UID resolved by
the bind-search-bind must match an existing, valid ``users.login`` row or
the login is rejected — see :class:`tiqora.domain._ldap_core.LdapConfig` for
the simplifications made relative to the Perl module.
"""

from __future__ import annotations

from tiqora.config import Settings
from tiqora.domain._ldap_core import LdapConfig, ldap_authenticate


def ldap_config_from_settings(settings: Settings) -> LdapConfig:
    return LdapConfig(
        host=settings.ldap_host,
        port=settings.ldap_port,
        use_ssl=settings.ldap_use_ssl,
        use_starttls=settings.ldap_use_starttls,
        base_dn=settings.ldap_base_dn,
        bind_dn=settings.ldap_bind_dn,
        bind_password=settings.ldap_bind_password,
        uid_attr=settings.ldap_uid_attr,
        always_filter=settings.ldap_always_filter,
        group_dn=settings.ldap_group_dn,
        access_attr=settings.ldap_access_attr,
        user_attr=settings.ldap_user_attr,
    )


class LdapAuthService:
    """Resolves an LDAP UID for a login/password pair (agent side)."""

    def __init__(self, settings: Settings) -> None:
        self._config = ldap_config_from_settings(settings)

    async def authenticate(self, login: str, password: str) -> str | None:
        """Bind-search-bind against the directory; returns the LDAP UID on success."""
        return await ldap_authenticate(self._config, login, password)
