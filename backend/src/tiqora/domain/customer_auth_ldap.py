"""LDAP/AD customer (portal) authentication (bind-search-bind against `customer_user.login`).

Ports ``Kernel::System::CustomerAuth::LDAP`` (see
``znuny-6.5.22/Kernel/System/CustomerAuth/LDAP.pm``, read-only reference). No
auto-provisioning in v1: the LDAP UID resolved by the bind-search-bind must
match an existing, valid ``customer_user.login`` row or the login is
rejected — see :class:`tiqora.domain._ldap_core.LdapConfig` for the
simplifications made relative to the Perl module.
"""

from __future__ import annotations

from tiqora.config import Settings
from tiqora.domain._ldap_core import LdapConfig, ldap_authenticate


def customer_ldap_config_from_settings(settings: Settings) -> LdapConfig:
    return LdapConfig(
        host=settings.customer_ldap_host,
        port=settings.customer_ldap_port,
        use_ssl=settings.customer_ldap_use_ssl,
        use_starttls=settings.customer_ldap_use_starttls,
        base_dn=settings.customer_ldap_base_dn,
        bind_dn=settings.customer_ldap_bind_dn,
        bind_password=settings.customer_ldap_bind_password,
        uid_attr=settings.customer_ldap_uid_attr,
        always_filter=settings.customer_ldap_always_filter,
        group_dn=settings.customer_ldap_group_dn,
        access_attr=settings.customer_ldap_access_attr,
        user_attr=settings.customer_ldap_user_attr,
    )


class CustomerLdapAuthService:
    """Resolves an LDAP UID for a login/password pair (customer portal side)."""

    def __init__(self, settings: Settings) -> None:
        self._config = customer_ldap_config_from_settings(settings)

    async def authenticate(self, login: str, password: str) -> str | None:
        """Bind-search-bind against the directory; returns the LDAP UID on success."""
        return await ldap_authenticate(self._config, login, password)
