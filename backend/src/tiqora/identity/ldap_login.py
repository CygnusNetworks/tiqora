"""LDAP directories as identity providers: password login with Znuny AuthSync parity (D14).

Znuny tries ``AuthModule``, ``AuthModule1`` … in order and the first module that accepts
the password wins; ``AuthSyncModule`` then creates/updates the agent and its roles/groups.
Here every enabled ``kind="ldap"`` provider serving the audience is tried in ``sort`` order
(:func:`ldap_password_login`); the first successful bind wins, later providers are not
tried even when the resulting login plan is rejected (as in Znuny, where an accepted
password for a user that cannot be synced is a failed login). The caller then runs the
SAME ``plan_login`` / ``apply_login`` chain as OIDC and SAML, so JIT creation, profile sync
and role/group sync (``ldap:group_dn`` rules) share the one write path.

Env providers (``TIQORA_LDAP_*`` / ``TIQORA_CUSTOMER_LDAP_*``) keep today's behaviour:
connection data comes from Settings, the login is the directory UID matched against an
existing valid account, no JIT, no rules (``require_sso_opt_in`` never applies to LDAP).

The subject of an LDAP identity is the first value of ``subject_attr`` (``entryUUID``;
AD ``objectGUID`` as hex), else the entry DN.
"""

from __future__ import annotations

import dataclasses

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.domain._ldap_core import LdapConfig, LdapEntry, ldap_authenticate_entry
from tiqora.identity.config_models import AttributeMap, LdapProviderConfig
from tiqora.identity.env_providers import ensure_env_providers, env_ldap_config, sync_env_providers
from tiqora.identity.login import ExternalIdentity, LoginRejected, apply_login, plan_login
from tiqora.identity.mapping import LDAP_GROUP_DN_ATTR, lookup, normalize_attributes
from tiqora.identity.store import ProviderRecord, list_enabled
from tiqora.identity.types import ENV_CUSTOMER_LDAP_SLUG, ENV_LDAP_SLUG, UserKind

logger = structlog.get_logger(__name__)

__all__ = [
    "identity_from_entry",
    "ldap_core_config",
    "ldap_password_login",
    "ldap_providers",
    "ldap_sign_in",
    "requested_attributes",
    "rule_group_dns",
]

LDAP_DN_ATTR = "ldap:dn"
_PSEUDO_PREFIX = "ldap:"


def ldap_core_config(cfg: LdapProviderConfig) -> LdapConfig:
    """Connection parameters for ``_ldap_core`` (the secret is unwrapped only here)."""
    return LdapConfig(
        host=cfg.host,
        port=cfg.port,
        use_ssl=cfg.use_ssl,
        use_starttls=cfg.use_starttls,
        base_dn=cfg.base_dn,
        bind_dn=cfg.bind_dn,
        bind_password=cfg.bind_password.get_secret_value() if cfg.bind_password else "",
        uid_attr=cfg.uid_attr,
        always_filter=cfg.always_filter,
        group_dn=cfg.group_dn,
        access_attr=cfg.access_attr,
        user_attr=cfg.user_attr,
    )


def _ldap_config(record: ProviderRecord) -> LdapProviderConfig:
    if not isinstance(record.config, LdapProviderConfig):
        raise TypeError(f"provider {record.slug} is not an LDAP provider")
    return record.config


def _is_directory_attribute(name: str) -> bool:
    name = name.strip()
    return bool(name) and not name.startswith("_") and not name.lower().startswith(_PSEUDO_PREFIX)


def requested_attributes(record: ProviderRecord) -> list[str]:
    """Directory attributes to read: every non-literal, non-pseudo source of the attribute
    map, the subject attribute and every non-pseudo rule attribute (e.g. ``memberOf``)."""
    cfg = _ldap_config(record)
    amap = record.attribute_map
    names = [
        *amap.login,
        *amap.email,
        *amap.first_name,
        *amap.last_name,
        *amap.customer_id,
        *amap.mobile,
        cfg.subject_attr,
        *(r.attribute for r in record.role_rules),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        name = name.strip()
        if _is_directory_attribute(name) and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def rule_group_dns(record: ProviderRecord) -> list[str]:
    """Group DNs referenced by ``ldap:group_dn`` rules (membership is checked per DN)."""
    dns = [
        r.value.strip()
        for r in record.role_rules
        if r.attribute.strip().lower() == LDAP_GROUP_DN_ATTR and r.value.strip()
    ]
    return list(dict.fromkeys(dns))


def _subject(cfg: LdapProviderConfig, entry: LdapEntry) -> str:
    if cfg.subject_attr.strip():
        for value in lookup(entry.attributes, cfg.subject_attr.strip()):
            if value.strip():
                return value.strip()
    return entry.dn


def identity_from_entry(record: ProviderRecord, entry: LdapEntry) -> ExternalIdentity:
    """External identity of a bound directory user. ``ldap:group_dn`` is always present
    (``()`` without memberships) so authoritative role sync is never skipped for LDAP."""
    cfg = _ldap_config(record)
    uid_key = cfg.uid_attr.strip() or "uid"
    raw: dict[str, object] = {
        k: list(v) for k, v in entry.attributes.items() if k.lower() != uid_key.lower()
    }
    raw[uid_key] = [entry.uid]
    raw[LDAP_DN_ATTR] = [entry.dn]
    raw[LDAP_GROUP_DN_ATTR] = list(entry.member_group_dns)
    return ExternalIdentity(
        provider_id=record.id,
        subject=_subject(cfg, entry),
        attributes=normalize_attributes(raw),
    )


def _runtime_record(record: ProviderRecord, settings: Settings) -> ProviderRecord:
    """Env rows: connection data from Settings and today's semantics (UID = login of an
    existing account, no JIT, no rules), whatever the row holds."""
    if record.source != "env":
        return record
    kind: UserKind = "customer" if record.slug == ENV_CUSTOMER_LDAP_SLUG else "agent"
    cfg = env_ldap_config(settings, kind)
    return dataclasses.replace(
        record,
        config=cfg,
        attribute_map=AttributeMap(login=[cfg.uid_attr]),
        match_by="login",
        require_sso_opt_in=False,
        jit_enabled=False,
        role_rules=(),
    )


async def _ensure_env(session: AsyncSession, settings: Settings) -> None:
    try:
        await ensure_env_providers(session, settings)
    except Exception as exc:  # noqa: BLE001 - a failed sync must not break password login
        logger.warning("env_provider_sync_failed", error=str(exc))
        await session.rollback()


async def _enabled(
    session: AsyncSession, settings: Settings, user_kind: UserKind
) -> list[ProviderRecord]:
    """Enabled LDAP rows; DB trouble means "no directory" (a 401, never a 500)."""
    try:
        return await list_enabled(
            session, secret_key=settings.secret_key, kind="ldap", user_kind=user_kind
        )
    except Exception as exc:  # noqa: BLE001 - a failed password login stays a failed login
        logger.warning("ldap_providers_unavailable", error=str(exc))
        await session.rollback()
        return []


async def ldap_providers(
    session: AsyncSession, settings: Settings, *, user_kind: UserKind
) -> list[ProviderRecord]:
    """Enabled LDAP providers for the audience in ``sort`` order, ready to connect.

    The env row is re-synced when its flag is on but the row is missing or disabled (the
    once-per-process sync cannot notice a row deleted later); the sync is best effort.
    """
    await _ensure_env(session, settings)
    env_on = settings.ldap_enabled if user_kind == "agent" else settings.customer_ldap_enabled
    env_slug = ENV_LDAP_SLUG if user_kind == "agent" else ENV_CUSTOMER_LDAP_SLUG
    records = await _enabled(session, settings, user_kind)
    if env_on and not any(r.slug == env_slug for r in records):
        try:
            await sync_env_providers(session, settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("env_provider_sync_failed", error=str(exc))
            await session.rollback()
        records = await _enabled(session, settings, user_kind)
    out: list[ProviderRecord] = []
    for rec in records:
        if rec.source == "env" and (rec.slug != env_slug or not env_on):
            continue  # env row of the other audience, or its flag is off
        out.append(_runtime_record(rec, settings))
    return out


async def ldap_password_login(
    session: AsyncSession,
    settings: Settings,
    *,
    login: str,
    password: str,
    user_kind: UserKind,
) -> tuple[ProviderRecord, ExternalIdentity] | None:
    """Try the enabled LDAP providers in order; the first successful bind wins.

    An empty login or password never reaches a directory (an empty-password simple bind
    is an unauthenticated bind that many servers accept).
    """
    if not login.strip() or not password:
        return None
    for record in await ldap_providers(session, settings, user_kind=user_kind):
        cfg = _ldap_config(record)
        if not cfg.host.strip():
            logger.warning("ldap_provider_without_host", provider=record.slug)
            continue
        entry = await ldap_authenticate_entry(
            ldap_core_config(cfg),
            login,
            password,
            attributes=requested_attributes(record),
            member_of_group_dns=rule_group_dns(record),
        )
        if entry is None:
            continue
        logger.info("ldap_provider_accepted", provider=record.slug, user_kind=user_kind)
        return record, identity_from_entry(record, entry)
    return None


async def ldap_sign_in(
    session: AsyncSession,
    settings: Settings,
    *,
    login: str,
    password: str,
    user_kind: UserKind,
) -> str | None:
    """Password login against the directories, then the shared login chain (link, login
    match, JIT, profile and role sync; commits). Returns the account's login, or ``None``
    for every failure: no directory accepted the password, or the plan was rejected
    (the reason is logged, never shown on the password form)."""
    found = await ldap_password_login(
        session, settings, login=login, password=password, user_kind=user_kind
    )
    if found is None:
        return None
    record, identity = found
    plan = await plan_login(session, record, identity, user_kind=user_kind, settings=settings)
    if plan.decision == "reject":
        logger.info("ldap_login_rejected", provider=record.slug, reason=plan.reject_code)
        return None
    try:
        _user_id, account_login = await apply_login(
            session, record, identity, plan, settings=settings
        )
    except LoginRejected as exc:
        logger.info("ldap_login_rejected", provider=record.slug, reason=exc.reject_code)
        return None
    return account_login
