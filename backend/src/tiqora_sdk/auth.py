"""Names for password auth providers (``registry.password_provider``).

A provider adds one more link to the password chain of the agent and customer login: after
Tiqora's own password check and the LDAP directories, it may confirm ``login + password`` and
name an **existing, valid** account. It cannot create accounts, redirect to a login page or
skip two-factor authentication, which still applies after a provider said yes.

``authenticate`` is ``async def (ctx: AuthContext, check: PasswordCheck) -> str | None``:
return the login of the account (usually ``check.login``) or ``None`` for "no". Anything else,
an exception or a timeout counts as "no" too. The password is only in ``check``; never log it.
"""

from __future__ import annotations

from tiqora.plugins.auth_providers import AuthContext, PasswordAuthenticate, PasswordCheck

__all__ = ["AuthContext", "PasswordAuthenticate", "PasswordCheck"]
