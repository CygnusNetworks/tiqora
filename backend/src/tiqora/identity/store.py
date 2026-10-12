"""Identity provider rows <-> ``ProviderRecord``; secrets are Fernet-encrypted at rest.

Layout: ``config`` holds the non-secret fields of the kind's config model as JSON;
``secrets_enc`` is ``encrypt_secret(secret_key, json.dumps({field: value}))`` or NULL.
Functions flush only; the caller owns the transaction.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from pydantic import BaseModel, SecretStr, TypeAdapter, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto.secret import decrypt_secret, encrypt_secret
from tiqora.db.legacy.user import PermissionGroups, Roles
from tiqora.db.tiqora.models import (
    TiqoraIdentityLink,
    TiqoraIdentityManagedAssignment,
    TiqoraIdentityProvider,
    TiqoraScimGroup,
    TiqoraScimGroupMember,
    TiqoraScimPendingSync,
    TiqoraScimToken,
    TiqoraScimUser,
)
from tiqora.identity.config_models import (
    CONFIG_MODELS,
    AttributeMap,
    GroupGrant,
    JitDefaults,
    RoleRule,
    SamlProviderConfig,
    merge_config,
    secret_fields,
)
from tiqora.identity.mapping import LDAP_GROUP_DN_ATTR
from tiqora.identity.presets import get_preset
from tiqora.identity.saml_keys import generate_sp_keypair
from tiqora.identity.types import (
    RESERVED_SLUGS,
    SLUG_RE,
    SYSTEM_USER_ID,
    Audience,
    MatchBy,
    ProviderKind,
    ProviderSource,
    SyncMode,
    UserKind,
    audience_includes,
    utc_now,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "ProviderChanges",
    "ProviderNotFound",
    "ProviderReadOnly",
    "ProviderRecord",
    "ProviderValidationError",
    "create_provider",
    "delete_provider",
    "get_provider",
    "get_provider_by_slug",
    "list_enabled",
    "list_providers",
    "merge_config",
    "provider_from_row",
    "record_with_changes",
    "update_provider",
    "validate_changes",
]

_NULLABLE_FIELDS = frozenset({"button_label", "button_icon"})
# Scalar ProviderChanges fields copied 1:1 onto the row / record (None = unchanged unless nullable).
_SIMPLE_FIELDS = (
    "name",
    "audience",
    "enabled",
    "sort",
    "button_label",
    "button_icon",
    "match_by",
    "require_sso_opt_in",
    "jit_enabled",
    "sync_mode",
)
_RULES_ADAPTER: TypeAdapter[list[RoleRule]] = TypeAdapter(list[RoleRule])

MSG_SLUG_INVALID = "Dieser Kurzname ist ungültig (nur a–z, 0–9 und Bindestrich, 2–63 Zeichen)."
MSG_SLUG_TAKEN = "Dieser Kurzname ist bereits vergeben."  # noqa: S105 — not a secret


class ProviderValidationError(ValueError):
    """Invalid admin input; ``errors`` maps field -> German message."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class ProviderReadOnly(Exception):
    """The provider row comes from environment variables and cannot be changed."""


class ProviderNotFound(LookupError):
    """No provider row with that id."""


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    id: int
    slug: str
    kind: ProviderKind
    source: ProviderSource
    name: str
    audience: Audience
    enabled: bool
    sort: int
    button_label: str | None
    button_icon: str | None
    preset: str | None
    config: BaseModel
    secrets_set: frozenset[str]
    match_by: MatchBy
    require_sso_opt_in: bool
    attribute_map: AttributeMap
    jit_enabled: bool
    jit_defaults: JitDefaults
    role_rules: tuple[RoleRule, ...]
    sync_mode: SyncMode
    metadata_xml: str | None
    metadata_fetched_at: datetime | None
    metadata_error: str | None

    @property
    def read_only(self) -> bool:
        return self.source == "env"

    @property
    def label(self) -> str:
        return self.button_label or self.name


class ProviderChanges(BaseModel):
    """Admin PATCH body (also the "draft" of a test login)."""

    name: str | None = None
    slug: str | None = None
    audience: Audience | None = None
    enabled: bool | None = None
    sort: int | None = None
    button_label: str | None = None
    button_icon: str | None = None
    config: dict[str, Any] | None = None
    match_by: MatchBy | None = None
    require_sso_opt_in: bool | None = None
    attribute_map: AttributeMap | None = None
    jit_enabled: bool | None = None
    jit_defaults: JitDefaults | None = None
    role_rules: list[RoleRule] | None = None
    sync_mode: SyncMode | None = None


# --------------------------------------------------------------------------- encoding


def _secret_values(kind: ProviderKind, config: BaseModel) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in secret_fields(kind):
        value = getattr(config, name)
        if isinstance(value, SecretStr) and value.get_secret_value():
            out[name] = value.get_secret_value()
    return out


def _encode_config(kind: ProviderKind, config: BaseModel) -> str:
    return json.dumps(config.model_dump(mode="json", exclude=set(secret_fields(kind))))


def _encode_secrets(secret_key: str, values: Mapping[str, str]) -> str | None:
    if not values:
        return None
    return encrypt_secret(secret_key, json.dumps(dict(values)))


def _decode_secrets(row: TiqoraIdentityProvider, secret_key: str) -> dict[str, str]:
    if not row.secrets_enc:
        return {}
    plain = decrypt_secret(secret_key, row.secrets_enc)
    if plain is None:
        logger.warning("identity_secret_undecryptable", provider_id=row.id, slug=row.slug)
        return {}
    try:
        data = json.loads(plain)
    except ValueError:
        logger.warning("identity_secret_undecryptable", provider_id=row.id, slug=row.slug)
        return {}
    if not isinstance(data, dict):
        return {}
    allowed = secret_fields(row.kind)  # type: ignore[arg-type]
    return {k: v for k, v in data.items() if k in allowed and isinstance(v, str) and v}


def provider_from_row(row: TiqoraIdentityProvider, *, secret_key: str) -> ProviderRecord:
    kind: ProviderKind = row.kind  # type: ignore[assignment]
    secrets = _decode_secrets(row, secret_key)
    data: dict[str, Any] = json.loads(row.config or "{}")
    data.update(secrets)
    config = CONFIG_MODELS[kind].model_validate(data)
    return ProviderRecord(
        id=row.id,
        slug=row.slug,
        kind=kind,
        source=row.source,  # type: ignore[arg-type]
        name=row.name,
        audience=row.audience,  # type: ignore[arg-type]
        enabled=bool(row.enabled),
        sort=row.sort,
        button_label=row.button_label,
        button_icon=row.button_icon,
        preset=row.preset,
        config=config,
        secrets_set=frozenset(secrets),
        match_by=row.match_by,  # type: ignore[arg-type]
        require_sso_opt_in=bool(row.require_sso_opt_in),
        attribute_map=AttributeMap.model_validate_json(row.attribute_map or "{}"),
        jit_enabled=bool(row.jit_enabled),
        jit_defaults=JitDefaults.model_validate_json(row.jit_defaults or "{}"),
        role_rules=tuple(_RULES_ADAPTER.validate_json(row.role_rules or "[]")),
        sync_mode=row.sync_mode,  # type: ignore[arg-type]
        metadata_xml=row.metadata_xml,
        metadata_fetched_at=row.metadata_fetched_at,
        metadata_error=row.metadata_error,
    )


# --------------------------------------------------------------------------- reads


async def get_provider(
    session: AsyncSession, provider_id: int, *, secret_key: str
) -> ProviderRecord | None:
    row = await session.get(TiqoraIdentityProvider, provider_id)
    return provider_from_row(row, secret_key=secret_key) if row else None


async def get_provider_by_slug(
    session: AsyncSession, slug: str, *, secret_key: str
) -> ProviderRecord | None:
    row = (
        await session.execute(
            select(TiqoraIdentityProvider).where(TiqoraIdentityProvider.slug == slug)
        )
    ).scalar_one_or_none()
    return provider_from_row(row, secret_key=secret_key) if row else None


async def list_providers(session: AsyncSession, *, secret_key: str) -> list[ProviderRecord]:
    rows = (
        await session.scalars(
            select(TiqoraIdentityProvider).order_by(
                TiqoraIdentityProvider.sort, TiqoraIdentityProvider.id
            )
        )
    ).all()
    return [provider_from_row(r, secret_key=secret_key) for r in rows]


async def list_enabled(
    session: AsyncSession,
    *,
    secret_key: str,
    kind: ProviderKind | None = None,
    user_kind: UserKind | None = None,
) -> list[ProviderRecord]:
    stmt = (
        select(TiqoraIdentityProvider)
        .where(TiqoraIdentityProvider.enabled.is_(True))
        .order_by(TiqoraIdentityProvider.sort, TiqoraIdentityProvider.id)
    )
    if kind is not None:
        stmt = stmt.where(TiqoraIdentityProvider.kind == kind)
    out: list[ProviderRecord] = []
    for row in (await session.scalars(stmt)).all():
        rec = provider_from_row(row, secret_key=secret_key)
        if user_kind is None or audience_includes(rec.audience, user_kind):
            out.append(rec)
    return out


# --------------------------------------------------------------------------- validation


async def _check_slug(
    session: AsyncSession, slug: str, *, allow_reserved: bool = False, exclude_id: int | None = None
) -> None:
    if not SLUG_RE.fullmatch(slug) or (not allow_reserved and slug in RESERVED_SLUGS):
        raise ProviderValidationError({"slug": MSG_SLUG_INVALID})
    stmt = select(TiqoraIdentityProvider.id).where(TiqoraIdentityProvider.slug == slug)
    if exclude_id is not None:
        stmt = stmt.where(TiqoraIdentityProvider.id != exclude_id)
    if (await session.execute(stmt)).first() is not None:
        raise ProviderValidationError({"slug": MSG_SLUG_TAKEN})


async def _flush_unique_slug(session: AsyncSession) -> None:
    """Flush inside a savepoint; a concurrent duplicate slug becomes a validation error."""
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise ProviderValidationError({"slug": MSG_SLUG_TAKEN}) from exc


async def _existing_ids(session: AsyncSession, column: Any, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    return set((await session.scalars(select(column).where(column.in_(ids)))).all())


def _grant_group_ids(grants: list[GroupGrant]) -> list[int]:
    return [g.group_id for g in grants]


async def _validate_assignments(
    session: AsyncSession,
    *,
    role_rules: list[RoleRule] | None,
    jit_defaults: JitDefaults | None,
) -> None:
    """Role/group ids in rules and JIT defaults must exist (IN-lists, no joins)."""
    wanted: dict[str, tuple[list[int], list[int]]] = {}
    if role_rules is not None:
        wanted["role_rules"] = (
            [i for r in role_rules for i in r.role_ids],
            [i for r in role_rules for i in _grant_group_ids(r.groups)],
        )
    if jit_defaults is not None:
        wanted["jit_defaults"] = (
            list(jit_defaults.initial_role_ids),
            _grant_group_ids(jit_defaults.initial_groups),
        )
    if not wanted:
        return
    all_roles = {i for roles, _ in wanted.values() for i in roles}
    all_groups = {i for _, groups in wanted.values() for i in groups}
    valid_roles = await _existing_ids(session, Roles.id, all_roles)
    valid_groups = await _existing_ids(session, PermissionGroups.id, all_groups)
    errors: dict[str, str] = {}
    for field_name, (roles, groups) in wanted.items():
        for rid in roles:
            if rid not in valid_roles:
                errors[field_name] = f"Unbekannte Rolle: {rid}"
                break
        else:
            for gid in groups:
                if gid not in valid_groups:
                    errors[field_name] = f"Unbekannte Gruppe: {gid}"
                    break
    if errors:
        raise ProviderValidationError(errors)


def _validate_rule_dns(role_rules: list[RoleRule] | None) -> None:
    """``ldap:group_dn`` rule values must be syntactically valid DNs (RFC 4514): a
    malformed base DN would make every group search of that provider fail."""
    if not role_rules:
        return
    from ldap3.core.exceptions import LDAPInvalidDnError
    from ldap3.utils.dn import safe_dn

    for rule in role_rules:
        if rule.attribute.strip().lower() != LDAP_GROUP_DN_ATTR:
            continue
        try:
            safe_dn(rule.value.strip())
        except (LDAPInvalidDnError, ValueError, IndexError):
            raise ProviderValidationError(
                {
                    "role_rules": (
                        f"Ungültiger Gruppen-DN: {rule.value.strip()!r} "
                        "(Beispiel: cn=support,ou=groups,dc=example,dc=org)."
                    )
                }
            ) from None


def _config_errors(exc: ValidationError) -> ProviderValidationError:
    errors: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "config"
        errors.setdefault(f"config.{loc}", err["msg"])
    return ProviderValidationError(errors)


# --------------------------------------------------------------------------- writes


def _new_row(
    *,
    secret_key: str,
    actor_id: int,
    slug: str,
    kind: ProviderKind,
    source: ProviderSource,
    name: str,
    audience: Audience,
    enabled: bool,
    sort: int,
    preset: str | None,
    button_icon: str | None,
    config: BaseModel,
    attribute_map: AttributeMap,
    match_by: MatchBy,
    require_sso_opt_in: bool,
) -> TiqoraIdentityProvider:
    now = utc_now()
    return TiqoraIdentityProvider(
        slug=slug,
        kind=kind,
        source=source,
        name=name,
        audience=audience,
        enabled=enabled,
        sort=sort,
        button_label=None,
        button_icon=button_icon,
        preset=preset,
        config=_encode_config(kind, config),
        secrets_enc=_encode_secrets(secret_key, _secret_values(kind, config)),
        match_by=match_by,
        require_sso_opt_in=require_sso_opt_in,
        attribute_map=attribute_map.model_dump_json(),
        jit_enabled=False,
        jit_defaults=JitDefaults().model_dump_json(),
        role_rules="[]",
        sync_mode="managed",
        metadata_xml=None,
        metadata_fetched_at=None,
        metadata_error=None,
        create_by=actor_id,
        create_time=now,
        change_by=actor_id,
        change_time=now,
    )


async def create_provider(
    session: AsyncSession,
    *,
    secret_key: str,
    actor_id: int,
    preset: str,
    name: str,
    slug: str,
    audience: Audience,
) -> ProviderRecord:
    """Create a disabled provider from ``preset``; SAML gets a fresh SP key pair."""
    await _check_slug(session, slug)
    try:
        pre = get_preset(preset)
    except KeyError:
        raise ProviderValidationError({"preset": "Unbekannte Vorlage."}) from None
    config = CONFIG_MODELS[pre.kind].model_validate(pre.config)
    if isinstance(config, SamlProviderConfig):
        # RSA keygen is CPU-bound: keep it off the event loop.
        key_pem, cert_pem = await asyncio.get_running_loop().run_in_executor(
            None, lambda: generate_sp_keypair(slug)
        )
        config.sp_certificate = cert_pem
        config.sp_private_key = SecretStr(key_pem)
    row = _new_row(
        secret_key=secret_key,
        actor_id=actor_id,
        slug=slug,
        kind=pre.kind,
        source="db",
        name=name,
        audience=audience,
        enabled=False,
        sort=100,
        preset=pre.key,
        button_icon=pre.button_icon,
        config=config,
        attribute_map=pre.attribute_map,
        match_by=pre.match_by,
        require_sso_opt_in=False,
    )
    session.add(row)
    await _flush_unique_slug(session)
    return provider_from_row(row, secret_key=secret_key)


async def _insert_env_row(
    session: AsyncSession,
    *,
    secret_key: str,
    slug: str,
    kind: ProviderKind,
    name: str,
    audience: Audience,
    config: BaseModel,
    attribute_map: AttributeMap | None = None,
    match_by: MatchBy = "login",
    require_sso_opt_in: bool = False,
    enabled: bool = True,
    sort: int = 0,
    button_icon: str | None = None,
) -> ProviderRecord:
    """Insert a read-only ``source="env"`` row (reserved slugs allowed). For env_providers."""
    await _check_slug(session, slug, allow_reserved=True)
    row = _new_row(
        secret_key=secret_key,
        actor_id=SYSTEM_USER_ID,
        slug=slug,
        kind=kind,
        source="env",
        name=name,
        audience=audience,
        enabled=enabled,
        sort=sort,
        preset=None,
        button_icon=button_icon,
        config=config,
        attribute_map=attribute_map or AttributeMap(),
        match_by=match_by,
        require_sso_opt_in=require_sso_opt_in,
    )
    # Env rows never persist connection data: the runtime config is rebuilt from Settings.
    row.config = "{}"
    row.secrets_enc = None
    session.add(row)
    await session.flush()
    return provider_from_row(row, secret_key=secret_key)


async def update_provider(
    session: AsyncSession,
    provider_id: int,
    changes: ProviderChanges,
    *,
    secret_key: str,
    actor_id: int,
) -> ProviderRecord:
    row = await session.get(TiqoraIdentityProvider, provider_id)
    if row is None:
        raise ProviderNotFound(provider_id)
    if row.source == "env":
        raise ProviderReadOnly(row.slug)
    current = provider_from_row(row, secret_key=secret_key)
    kind = current.kind

    # Validate everything first, mutate afterwards (an autoflush must not persist a
    # half-applied change).
    new_slug = changes.slug if changes.slug is not None and changes.slug != row.slug else None
    if new_slug is not None:
        await _check_slug(session, new_slug, exclude_id=row.id)
    _validate_rule_dns(changes.role_rules)
    await _validate_assignments(
        session, role_rules=changes.role_rules, jit_defaults=changes.jit_defaults
    )
    merged: BaseModel | None = None
    if changes.config is not None:
        try:
            merged = merge_config(kind, current.config, changes.config)
        except ValidationError as exc:
            raise _config_errors(exc) from exc

    if new_slug is not None:
        row.slug = new_slug
    if merged is not None:
        new_secrets = _secret_values(kind, merged)
        row.config = _encode_config(kind, merged)
        if (
            isinstance(merged, SamlProviderConfig)
            and not merged.idp_metadata_url.strip()
            and not merged.idp_metadata_xml.strip()
        ):
            # Stored trust data must not outlive the admin's configuration.
            row.metadata_xml = None
            row.metadata_fetched_at = None
            row.metadata_error = None
        # Re-encrypting yields a different Fernet token; only rewrite on a real change.
        if new_secrets != _secret_values(kind, current.config):
            row.secrets_enc = _encode_secrets(secret_key, new_secrets)

    sent = changes.model_fields_set
    for field_name in _SIMPLE_FIELDS:
        value = getattr(changes, field_name)
        # Only the button fields are nullable; None elsewhere means "unchanged".
        if field_name in sent and (value is not None or field_name in _NULLABLE_FIELDS):
            setattr(row, field_name, value)
    if changes.attribute_map is not None:
        row.attribute_map = changes.attribute_map.model_dump_json()
    if changes.jit_defaults is not None:
        row.jit_defaults = changes.jit_defaults.model_dump_json()
    if changes.role_rules is not None:
        row.role_rules = _RULES_ADAPTER.dump_json(changes.role_rules).decode()
    row.change_by = actor_id
    row.change_time = utc_now()
    await _flush_unique_slug(session)
    return provider_from_row(row, secret_key=secret_key)


async def validate_changes(session: AsyncSession, changes: ProviderChanges) -> None:
    """Role/group ids of *changes* must exist and ``ldap:group_dn`` values be valid DNs
    (``ProviderValidationError``). Reads only."""
    _validate_rule_dns(changes.role_rules)
    await _validate_assignments(
        session, role_rules=changes.role_rules, jit_defaults=changes.jit_defaults
    )


def record_with_changes(record: ProviderRecord, changes: ProviderChanges) -> ProviderRecord:
    """*record* with *changes* applied in memory only (admin test-login drafts).

    Same merge rules as :func:`update_provider` (secret mask, read-only fields) but nothing is
    persisted. Env rows are returned unchanged: they are rebuilt from ``Settings``.
    Raises ``ProviderValidationError`` for an invalid config.
    """
    if record.read_only:
        return record
    config = record.config
    secrets = record.secrets_set
    if changes.config is not None:
        try:
            config = merge_config(record.kind, record.config, changes.config)
        except ValidationError as exc:
            raise _config_errors(exc) from exc
        secrets = frozenset(_secret_values(record.kind, config))
    sent = changes.model_fields_set
    simple: dict[str, Any] = {}
    for name in (*_SIMPLE_FIELDS, "attribute_map", "jit_defaults"):
        value = getattr(changes, name)
        if name in sent and (value is not None or name in _NULLABLE_FIELDS):
            simple[name] = value
    if changes.role_rules is not None:
        simple["role_rules"] = tuple(changes.role_rules)
    return dataclasses.replace(record, config=config, secrets_set=secrets, **simple)


async def delete_provider(session: AsyncSession, provider_id: int) -> None:
    row = await session.get(TiqoraIdentityProvider, provider_id)
    if row is None:
        raise ProviderNotFound(provider_id)
    if row.source == "env":
        raise ProviderReadOnly(row.slug)
    # Explicit child deletes (children first): do not rely on FK cascade enforcement; the FKs
    # stay as a backstop. Otherwise a later provider reusing this id would inherit SCIM tokens
    # and ownership rows. ``tiqora_scim_event`` is the audit log and is kept on purpose.
    await session.execute(
        delete(TiqoraScimPendingSync).where(TiqoraScimPendingSync.provider_id == provider_id)
    )
    await session.execute(
        delete(TiqoraScimGroupMember).where(
            TiqoraScimGroupMember.group_id.in_(
                select(TiqoraScimGroup.id).where(TiqoraScimGroup.provider_id == provider_id)
            )
        )
    )
    for scim_model in (TiqoraScimGroup, TiqoraScimUser, TiqoraScimToken):
        await session.execute(delete(scim_model).where(scim_model.provider_id == provider_id))
    await session.execute(
        delete(TiqoraIdentityLink).where(TiqoraIdentityLink.provider_id == provider_id)
    )
    await session.execute(
        delete(TiqoraIdentityManagedAssignment).where(
            TiqoraIdentityManagedAssignment.provider_id == provider_id
        )
    )
    await session.delete(row)
    await session.flush()
