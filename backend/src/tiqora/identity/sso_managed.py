"""One SSO-managed-account check for every password sign-in path (P2A-PM-I2).

An account that a SCIM provider manages and whose provider requires the IdP's MFA proof
(``require_mfa_claim``) signs in through that IdP, with its step-up. A password, whichever
link of the chain checks it (Tiqora's own ``users.pw``/``customer_user.pw``, an LDAP
directory, a plugin password provider) and whichever interface asks (the login routes, the
Znuny-compat GenericInterface's ``SessionCreate`` and per-request ``UserLogin``/``Password``),
must not be a way around that. The rule:

* the mapping is active (``tiqora_scim_user.deleted`` is false), and
* its provider is **enabled** and has ``require_mfa_claim`` set.

A disabled provider or a deprovisioned mapping does not block: disabling the provider is the
admin's explicit way back to passwords, and deprovisioning deactivates the account anyway.

A refusal looks exactly like a wrong password (401 "Invalid credentials", or the compat
operation's own ``AuthFail`` text; counted by the rate limiter); only the log says why
(``password_login_rejected reason=sso_managed``). The local, LDAP and compat checks run only
after the password was confirmed, so the answer and its timing for
someone without the password stay the same. Only Tiqora tables, joined on the integer
provider id: no join with a Znuny table (collations).
"""

from __future__ import annotations

from typing import Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraIdentityProvider, TiqoraScimUser

logger = structlog.get_logger(__name__)

__all__ = [
    "PasswordPath",
    "SsoManagedGate",
    "existing_account",
    "is_sso_managed",
    "log_refusal",
]

UserKind = Literal["agent", "customer"]
PasswordPath = Literal["local", "ldap", "plugin", "compat"]


async def existing_account(
    session: AsyncSession, user_kind: UserKind, login: str
) -> tuple[str, int] | None:
    """The stored spelling and id of a valid account login, or None."""
    if user_kind == "agent":
        stmt = select(Users.login, Users.id).where(Users.login == login, Users.valid_id == 1)
    else:
        stmt = select(CustomerUser.login, CustomerUser.id).where(
            CustomerUser.login == login, CustomerUser.valid_id == 1
        )
    row = (await session.execute(stmt)).first()
    return None if row is None else (str(row[0]), int(row[1]))


async def is_sso_managed(session: AsyncSession, user_kind: UserKind, account_id: int) -> bool:
    """The account has an active SCIM mapping under an enabled provider that requires MFA."""
    stmt = (
        select(TiqoraScimUser.id)
        .join(TiqoraIdentityProvider, TiqoraIdentityProvider.id == TiqoraScimUser.provider_id)
        .where(
            TiqoraScimUser.user_kind == user_kind,
            TiqoraScimUser.user_id == account_id,
            TiqoraScimUser.deleted.is_(False),
            TiqoraIdentityProvider.enabled.is_(True),
            TiqoraIdentityProvider.require_mfa_claim.is_(True),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


def log_refusal(user_kind: UserKind, login: str, path: PasswordPath, **fields: str | None) -> None:
    logger.warning(
        "password_login_rejected",
        reason="sso_managed",
        path=path,
        user_kind=user_kind,
        login=login,
        **fields,
    )


class SsoManagedGate:
    """One password login's check across the chain; ``refused`` stops the remaining links.

    The login routes hand :meth:`local` to ``authenticate_password`` (it runs after the hash
    compare and before the opportunistic rehash, so a refused login writes nothing) and
    :meth:`ldap` to ``ldap_sign_in`` (it runs between ``plan_login`` and ``apply_login``, so a
    refused directory login writes no link, profile or role sync). Once refused, the route
    skips every later link and answers as for a wrong password.
    """

    __slots__ = ("_session", "login", "refused", "user_kind")

    def __init__(self, session: AsyncSession, user_kind: UserKind, login: str) -> None:
        self._session = session
        self.user_kind: UserKind = user_kind
        self.login = login
        self.refused = False

    async def refuses(self, account_id: int, *, path: PasswordPath) -> bool:
        if not await is_sso_managed(self._session, self.user_kind, account_id):
            return False
        self.refused = True
        log_refusal(self.user_kind, self.login, path)
        return True

    async def local(self, account_id: int) -> bool:
        return await self.refuses(account_id, path="local")

    async def ldap(self, account_id: int) -> bool:
        return await self.refuses(account_id, path="ldap")
