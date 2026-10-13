"""Password auth providers from plugins (D12).

A provider is one more link in the password chain: after Tiqora's own password check and the
LDAP directories, a plugin may confirm ``login + password`` and name an **existing, valid**
account (agent: ``users``, customer: ``customer_user``). It cannot create accounts, redirect to
an SSO page or replace two-factor authentication: 2FA, enrollment, the rate limiter and the
session creation behind the login stay exactly as they are.

Anything but a valid account login counts as "no" and the next provider is asked: a provider
that raises, hangs, returns something else or names an unknown or invalid account never lets
anyone in. An SSO-managed account (:mod:`tiqora.identity.sso_managed`) is never signed in: a
typed login that is one never reaches a provider, and a provider that names one is a "no".
The password is handed to the provider and nowhere else: it is not logged, and it is scrubbed
from the error text of a failing provider before that text is logged.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Final, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import ExtensionPoint
from tiqora.identity.sso_managed import existing_account, is_sso_managed, log_refusal
from tiqora.plugins.calls import PluginCallBudget, call_plugin
from tiqora.plugins.context import PluginContext, minimal_context
from tiqora.plugins.state import is_active

logger = structlog.get_logger(__name__)

__all__ = [
    "AUTH_PROVIDERS",
    "CHAIN_BUDGET_SECONDS",
    "AuthContext",
    "PasswordAuthProvider",
    "PasswordAuthenticate",
    "PasswordCheck",
    "plugin_password_sign_in",
]

#: all providers of one login together; providers beyond it are skipped
CHAIN_BUDGET_SECONDS: float = 20.0

_LOGIN_MAX: Final = 200


@dataclass(frozen=True, slots=True)
class PasswordCheck:
    login: str
    password: str = field(repr=False)
    user_kind: Literal["agent", "customer"]
    client_ip: str | None


@dataclass(frozen=True, slots=True)
class AuthContext:
    plugin: PluginContext


#: ``async def (ctx, check) -> str | None``: the login of an existing account, or None for "no"
PasswordAuthenticate = Callable[[AuthContext, PasswordCheck], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class PasswordAuthProvider:
    key: str  # "<plugin_id>:<name>"
    plugin_id: str
    audience: Literal["agent", "customer", "both"]
    authenticate: PasswordAuthenticate
    order: int
    timeout_seconds: int  # 1..15
    context: PluginContext | None = None


AUTH_PROVIDERS: Final[ExtensionPoint[PasswordAuthProvider]] = ExtensionPoint(
    key="core.auth.password_providers",
    item_key=lambda p: p.key,
    description="Password checks a plugin adds after the local password and the LDAP chain.",
    sdk_stable=True,
)


def _providers(user_kind: Literal["agent", "customer"]) -> list[PasswordAuthProvider]:
    found = [
        p
        for p in get_registry().items(AUTH_PROVIDERS)
        if p.audience in (user_kind, "both") and is_active(p.plugin_id)
    ]
    found.sort(key=lambda p: (p.order, p.key))
    return found


async def plugin_password_sign_in(
    session: AsyncSession,
    *,
    login: str,
    password: str,
    user_kind: Literal["agent", "customer"],
    client_ip: str | None,
) -> tuple[str, str] | None:
    """``(account_login, plugin_id)`` of the first provider that confirms the password, or None.

    Providers of the audience run in ``(order, key)`` order under one shared time budget. The
    account a provider names may differ from the login that was typed (a directory that maps
    an e-mail address to a user name); it must exist and be valid.
    """
    if not login or not password:
        return None
    providers = _providers(user_kind)
    if not providers:
        return None
    # P2A-PM-I2 / m2: the password of an SSO-managed account never reaches a plugin
    typed = await existing_account(session, user_kind, login)
    if typed is not None and await is_sso_managed(session, user_kind, typed[1]):
        log_refusal(user_kind, login, "plugin")
        return None
    budget = PluginCallBudget(CHAIN_BUDGET_SECONDS)
    check = PasswordCheck(login=login, password=password, user_kind=user_kind, client_ip=client_ip)
    for provider in providers:
        timeout = budget.timeout_for(float(provider.timeout_seconds))
        if timeout <= 0:
            logger.warning(
                "plugin_auth_failed",
                plugin=provider.plugin_id,
                provider=provider.key,
                login=login,
                reason="budget",
            )
            break
        ctx = AuthContext(plugin=provider.context or minimal_context(provider.plugin_id))
        outcome = await call_plugin(
            provider.plugin_id,
            f"password auth provider {provider.key}",
            functools.partial(provider.authenticate, ctx, check),
            timeout=timeout,
            redact=(password,),
        )
        if not outcome.ok:
            # call_plugin logged the scrubbed error; this line names the login it was about
            logger.warning(
                "plugin_auth_failed",
                plugin=provider.plugin_id,
                provider=provider.key,
                login=login,
                reason="timeout" if outcome.timed_out else "error",
            )
            continue
        named = outcome.value
        if named is None:
            continue
        if not isinstance(named, str) or not named or len(named) > _LOGIN_MAX:
            logger.warning(
                "plugin_auth_rejected",
                plugin=provider.plugin_id,
                provider=provider.key,
                login=login,
                reason="bad_result",
            )
            continue
        found = await existing_account(session, user_kind, named)
        if found is None:
            logger.warning(
                "plugin_auth_rejected",
                plugin=provider.plugin_id,
                provider=provider.key,
                login=login,
                reason="unknown_or_invalid_account",
            )
            continue
        account, account_id = found
        if await is_sso_managed(session, user_kind, account_id):
            # a mapped account (the provider named another login): refused like the typed one;
            # the chain goes on, but every later provider naming this account is refused too
            log_refusal(
                user_kind,
                login,
                "plugin",
                account=account,
                plugin=provider.plugin_id,
                provider=provider.key,
            )
            continue
        if account != login:
            logger.info(
                "plugin_auth_mapped",
                plugin=provider.plugin_id,
                provider=provider.key,
                login=login,
                account=account,
            )
        return account, provider.plugin_id
    return None
