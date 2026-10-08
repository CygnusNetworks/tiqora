"""Environment-configured OIDC/LDAP as read-only ``source="env"`` provider rows.

The rows exist so links, the admin list and the login chain treat env providers
like any other. They carry NO connection data: ``env_runtime_config`` always
rebuilds it from ``Settings``, so production SSO never depends on row contents
(and a failed sync cannot disable it).
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Final

import structlog
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraIdentityProvider
from tiqora.identity.config_models import AttributeMap, LdapProviderConfig, OidcProviderConfig
from tiqora.identity.store import ProviderRecord, ProviderValidationError, _insert_env_row
from tiqora.identity.types import (
    ENV_CUSTOMER_LDAP_SLUG,
    ENV_LDAP_SLUG,
    ENV_OIDC_SLUG,
    SYSTEM_USER_ID,
    UserKind,
    utc_now,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "ensure_env_providers",
    "env_ldap_config",
    "env_runtime_config",
    "reset_env_sync_cache_for_tests",
    "runtime_config",
    "sync_env_providers",
]

_SYNCED_FINGERPRINT: str | None = None
_LOCK: asyncio.Lock = asyncio.Lock()
_USER_ATTRS: Final = ("DN", "UID")


def _fingerprint(settings: Settings) -> str:
    """Hash of every setting that influences the env rows' DB state."""
    parts = (
        settings.oidc_enabled,
        settings.oidc_claim,
        settings.ldap_enabled,
        settings.ldap_uid_attr,
        settings.customer_ldap_enabled,
        settings.customer_ldap_uid_attr,
    )
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def reset_env_sync_cache_for_tests() -> None:
    """Forget the once-per-process sync (and rebind the lock to the current loop)."""
    global _SYNCED_FINGERPRINT, _LOCK  # noqa: PLW0603
    _SYNCED_FINGERPRINT = None
    _LOCK = asyncio.Lock()


def _user_attr(value: str) -> Any:
    upper = value.strip().upper()
    return upper if upper in _USER_ATTRS else "DN"


def _secret(value: str) -> SecretStr | None:
    return SecretStr(value) if value else None


def _ldap_config(settings: Settings, prefix: str) -> LdapProviderConfig:
    def get(name: str) -> Any:
        return getattr(settings, f"{prefix}{name}")

    return LdapProviderConfig(
        host=get("host"),
        port=get("port"),
        use_ssl=get("use_ssl"),
        use_starttls=get("use_starttls"),
        base_dn=get("base_dn"),
        bind_dn=get("bind_dn"),
        bind_password=_secret(get("bind_password")),
        uid_attr=get("uid_attr"),
        always_filter=get("always_filter"),
        group_dn=get("group_dn"),
        access_attr=get("access_attr"),
        user_attr=_user_attr(get("user_attr")),
    )


def env_ldap_config(settings: Settings, user_kind: UserKind) -> LdapProviderConfig:
    """``TIQORA_LDAP_*`` (agents) or ``TIQORA_CUSTOMER_LDAP_*`` (customers) as a config model."""
    return _ldap_config(settings, "ldap_" if user_kind == "agent" else "customer_ldap_")


def env_runtime_config(record: ProviderRecord, settings: Settings) -> BaseModel:
    """Config of an env provider, ALWAYS rebuilt from Settings (row JSON is ignored)."""
    if record.slug == ENV_OIDC_SLUG:
        return OidcProviderConfig(
            issuer=settings.oidc_issuer,
            client_id=settings.oidc_client_id,
            client_secret=_secret(settings.oidc_client_secret),
            scopes=settings.oidc_scopes,
            redirect_uri=settings.oidc_redirect_uri,
        )
    if record.slug == ENV_LDAP_SLUG:
        return _ldap_config(settings, "ldap_")
    if record.slug == ENV_CUSTOMER_LDAP_SLUG:
        return _ldap_config(settings, "customer_ldap_")
    raise ValueError(f"unknown env provider slug: {record.slug}")


def runtime_config(record: ProviderRecord, settings: Settings) -> BaseModel:
    """Config to connect with: Settings for env providers, the stored config otherwise."""
    return env_runtime_config(record, settings) if record.source == "env" else record.config


async def _get_row(session: AsyncSession, slug: str) -> TiqoraIdentityProvider | None:
    return (
        await session.execute(
            select(TiqoraIdentityProvider).where(TiqoraIdentityProvider.slug == slug)
        )
    ).scalar_one_or_none()


async def _sync_one(
    session: AsyncSession,
    settings: Settings,
    *,
    slug: str,
    enabled: bool,
    insert: Any,
    login_attr: list[str] | None = None,
) -> bool:
    """Create/refresh one env row; True when the row was touched. Never raises on races."""
    row = await _get_row(session, slug)
    if row is None:
        if not enabled:
            return False
        try:
            await insert(session, secret_key=settings.secret_key)
            await session.commit()
            return True
        except (IntegrityError, ProviderValidationError):
            # Another replica inserted the slug first: re-read and reconcile below.
            await session.rollback()
            row = await _get_row(session, slug)
            if row is None:
                raise
    changed = False
    if bool(row.enabled) != enabled:
        row.enabled = enabled
        changed = True
    if login_attr is not None:
        wanted = AttributeMap(login=login_attr).model_dump_json()
        if row.attribute_map != wanted:
            row.attribute_map = wanted
            changed = True
    if changed:
        row.change_by = SYSTEM_USER_ID
        row.change_time = utc_now()
        await session.commit()
    return changed


async def sync_env_providers(session: AsyncSession, settings: Settings) -> list[str]:
    """Reconcile the ``env-*`` rows with ``Settings``; returns the slugs touched; commits.

    A flag turned off keeps its row (links reference it) with ``enabled=False``.
    """
    touched: list[str] = []

    async def oidc(s: AsyncSession, *, secret_key: str) -> ProviderRecord:
        return await _insert_env_row(
            s,
            secret_key=secret_key,
            slug=ENV_OIDC_SLUG,
            kind="oidc",
            name="SSO",
            audience="agent",
            config=OidcProviderConfig(),
            attribute_map=AttributeMap(login=[settings.oidc_claim]),
            match_by="login",
            require_sso_opt_in=True,
            sort=0,
            enabled=settings.oidc_enabled,
        )

    def ldap(slug: str, name: str, audience: Any, uid_attr: str) -> Any:
        async def insert(s: AsyncSession, *, secret_key: str) -> ProviderRecord:
            return await _insert_env_row(
                s,
                secret_key=secret_key,
                slug=slug,
                kind="ldap",
                name=name,
                audience=audience,
                config=LdapProviderConfig(port=389),
                attribute_map=AttributeMap(login=[uid_attr]),
                sort=1000,
            )

        return insert

    # The LDAP rows' login mapping mirrors the env UID attribute (shown in the admin list
    # and used by the preview); the login itself rebuilds it from Settings anyway.
    plan = (
        (ENV_OIDC_SLUG, settings.oidc_enabled, oidc, [settings.oidc_claim]),
        (
            ENV_LDAP_SLUG,
            settings.ldap_enabled,
            ldap(ENV_LDAP_SLUG, "LDAP", "agent", settings.ldap_uid_attr),
            [settings.ldap_uid_attr],
        ),
        (
            ENV_CUSTOMER_LDAP_SLUG,
            settings.customer_ldap_enabled,
            ldap(
                ENV_CUSTOMER_LDAP_SLUG,
                "LDAP (Kunden)",
                "customer",
                settings.customer_ldap_uid_attr,
            ),
            [settings.customer_ldap_uid_attr],
        ),
    )
    for slug, enabled, insert, login_attr in plan:
        if await _sync_one(
            session, settings, slug=slug, enabled=enabled, insert=insert, login_attr=login_attr
        ):
            touched.append(slug)
    return touched


async def ensure_env_providers(session: AsyncSession, settings: Settings) -> None:
    """Sync once per process and env fingerprint; failures are not cached.

    Raises when the DB is unreachable (the flag stays unset so the next call
    retries); the lifespan wraps this in try/except and every login path may
    call it again, so a failed startup sync never disables production SSO.
    """
    global _SYNCED_FINGERPRINT  # noqa: PLW0603
    fingerprint = _fingerprint(settings)
    if fingerprint == _SYNCED_FINGERPRINT:
        return
    async with _LOCK:
        if fingerprint == _SYNCED_FINGERPRINT:
            return
        await sync_env_providers(session, settings)
        _SYNCED_FINGERPRINT = fingerprint
