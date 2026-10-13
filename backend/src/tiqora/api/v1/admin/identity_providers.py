"""Admin API for identity providers (OIDC, SAML, LDAP) incl. test login and preview (D16).

Static paths are declared before ``/{provider_id}``. Secrets are never returned: they show
as ``SECRET_MASK`` when set. Env rows (``source="env"``) are read-only. Every route is
admin-only and lives under ``/api/v1/admin`` (API-key scope area ``admin``).

SAML test logins use the SAVED IdP metadata: a draft's metadata URL or XML is not fetched or
parsed for the test (save first, then test). Other draft fields apply in memory.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Mapping
from typing import Annotated, Any

import httpx
import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession, get_redis
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.identity_schemas import (
    GroupPermRefOut,
    IdentityCapabilitiesOut,
    IdentityKindOut,
    IdentityLinkOut,
    IdentityPresetOut,
    IdentityProviderCreate,
    IdentityProviderOut,
    IdentityProviderPatch,
    IdentityProviderSummaryOut,
    IdentityTestMfaOut,
    IdentityTestResultOut,
    IdentityTestStartIn,
    IdentityTestStartOut,
    LoginPlanOut,
    ManagedAssignmentOut,
    MappingPreviewIn,
    MfaPolicyOut,
    NamedRefOut,
    ProviderEndpointsOut,
)
from tiqora.config import Settings
from tiqora.db.legacy.user import PermissionGroups, Roles, Users
from tiqora.db.tiqora.models import TiqoraIdentityLink, TiqoraIdentityManagedAssignment
from tiqora.domain.passkey import webauthn_enabled
from tiqora.identity import flow_state, links, saml_runtime
from tiqora.identity.config_models import (
    CONFIG_MODELS,
    HIDDEN_CONFIG_FIELDS,
    READ_ONLY_CONFIG_FIELDS,
    MfaPolicy,
    SamlProviderConfig,
    secret_fields,
)
from tiqora.identity.env_providers import ensure_env_providers, runtime_config
from tiqora.identity.login import ExternalIdentity, plan_login, plan_to_dict
from tiqora.identity.metadata_refresh import (
    record_metadata_error,
    refresh_provider_metadata,
)
from tiqora.identity.presets import PRESETS
from tiqora.identity.saml_metadata import SamlMetadataError, parse_idp_metadata
from tiqora.identity.store import (
    ProviderChanges,
    ProviderNotFound,
    ProviderReadOnly,
    ProviderRecord,
    ProviderValidationError,
    create_provider,
    delete_provider,
    get_provider,
    list_providers,
    record_with_changes,
    update_provider,
    validate_changes,
)
from tiqora.identity.types import audience_includes
from tiqora.identity.urls import IdentityUrlsUnavailable, provider_urls, scim_base_url
from tiqora.settings_sections.section import SECRET_MASK

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/identity-providers", tags=["admin:identity"])

MSG_CHECK_FIELDS = "Bitte die markierten Felder prüfen."
MSG_READ_ONLY = (
    "Dieser Anmeldedienst kommt aus den Umgebungsvariablen und kann hier nicht geändert werden."
)
MSG_NO_LDAP_TEST = (
    "Für LDAP gibt es keine Testanmeldung – bitte die Vorschau mit Beispielwerten nutzen."
)
MSG_NOT_FOUND = "Anmeldedienst nicht gefunden."
MSG_NO_METADATA_URL = "Für diesen Anmeldedienst ist keine Metadaten-URL hinterlegt."
MSG_NOT_SAML = "Metadaten gibt es nur für SAML-Anmeldedienste."
MSG_AUDIENCE = "Dieser Anmeldedienst gilt nicht für diese Zielgruppe."

# Test seam: tests inject a fake IdP metadata server here.
_metadata_transport: httpx.AsyncBaseTransport | None = None

# Config fields whose change makes the stored IdP metadata stale.
_METADATA_FIELDS = frozenset(
    {"idp_metadata_url", "idp_metadata_xml", "idp_entity_id", "allow_private_network"}
)

RedisDep = Annotated[redis.Redis, Depends(get_redis)]


# --------------------------------------------------------------------------- helpers


def _validation_error(exc: ProviderValidationError) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"message": MSG_CHECK_FIELDS, "errors": exc.errors},
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=MSG_NOT_FOUND)


def _read_only() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"message": MSG_READ_ONLY})


async def _require(session: AsyncSession, settings: Settings, provider_id: int) -> ProviderRecord:
    record = await get_provider(session, provider_id, secret_key=settings.secret_key)
    if record is None:
        raise _not_found()
    return record


def _shown_config(record: ProviderRecord, settings: Settings) -> tuple[dict[str, Any], list[str]]:
    """Config for the admin form: secrets masked; env rows from the environment."""
    config: BaseModel = record.config
    if record.read_only:
        try:
            config = runtime_config(record, settings)
        except ValueError:
            config = record.config
    data = config.model_dump(mode="json", exclude=set(secret_fields(record.kind)))
    present: list[str] = []
    for name in sorted(secret_fields(record.kind)):
        value = getattr(config, name, None)
        is_set = isinstance(value, SecretStr) and bool(value.get_secret_value())
        data[name] = SECRET_MASK if is_set else None
        if is_set:
            present.append(name)
    return data, present


def _endpoints(record: ProviderRecord, settings: Settings) -> ProviderEndpointsOut:
    empty = ProviderEndpointsOut(
        agent_redirect_uri=None,
        customer_redirect_uri=None,
        agent_acs_url=None,
        customer_acs_url=None,
        sp_entity_id=None,
        sp_metadata_url=None,
        slo_url=None,
        backchannel_logout_url=None,
        base_url_missing=False,
    )
    if record.kind == "ldap":
        return empty
    try:
        urls = provider_urls(settings, record)
    except IdentityUrlsUnavailable:
        return empty.model_copy(update={"base_url_missing": True})
    agent = audience_includes(record.audience, "agent")
    customer = audience_includes(record.audience, "customer")
    if record.kind == "oidc":
        return empty.model_copy(
            update={
                "agent_redirect_uri": (urls.agent_redirect_uri or None) if agent else None,
                "customer_redirect_uri": (urls.customer_redirect_uri or None) if customer else None,
                "backchannel_logout_url": urls.backchannel_logout_url or None,
            }
        )
    return empty.model_copy(
        update={
            "agent_acs_url": (urls.agent_acs_url or None) if agent else None,
            "customer_acs_url": (urls.customer_acs_url or None) if customer else None,
            "sp_entity_id": urls.sp_entity_id or None,
            "sp_metadata_url": urls.sp_metadata_url or None,
            "slo_url": urls.slo_url or None,
        }
    )


def _summary_fields(record: ProviderRecord, link_count: int) -> dict[str, Any]:
    return {
        "id": record.id,
        "slug": record.slug,
        "kind": record.kind,
        "source": record.source,
        "name": record.name,
        "audience": record.audience,
        "enabled": record.enabled,
        "sort": record.sort,
        "button_label": record.button_label,
        "button_icon": record.button_icon,
        "preset": record.preset,
        "jit_enabled": record.jit_enabled,
        "read_only": record.read_only,
        "metadata_error": record.metadata_error,
        "link_count": link_count,
    }


async def _link_counts(
    session: AsyncSession, provider_ids: list[int] | None = None
) -> dict[int, int]:
    stmt = select(TiqoraIdentityLink.provider_id, func.count(TiqoraIdentityLink.id)).group_by(
        TiqoraIdentityLink.provider_id
    )
    if provider_ids is not None:
        stmt = stmt.where(TiqoraIdentityLink.provider_id.in_(provider_ids))
    return {int(pid): int(n) for pid, n in (await session.execute(stmt)).all()}


async def _out(
    session: AsyncSession, settings: Settings, record: ProviderRecord
) -> IdentityProviderOut:
    count = (await _link_counts(session, [record.id])).get(record.id, 0)
    config, secrets_set = _shown_config(record, settings)
    return IdentityProviderOut(
        **_summary_fields(record, count),
        config=config,
        secrets_set=secrets_set,
        match_by=record.match_by,
        require_sso_opt_in=record.require_sso_opt_in,
        attribute_map=record.attribute_map,
        jit_defaults=record.jit_defaults,
        role_rules=list(record.role_rules),
        sync_mode=record.sync_mode,
        metadata_fetched_at=record.metadata_fetched_at,
        endpoints=_endpoints(record, settings),
        require_mfa_claim=record.require_mfa_claim,
        mfa_policy=MfaPolicyOut.model_validate(record.mfa_policy.model_dump()),
        session_idle_seconds=record.session_idle_seconds,
        session_absolute_seconds=record.session_absolute_seconds,
        scim_enabled=record.scim_enabled,
        scim_adopt_existing=record.scim_adopt_existing,
    )


async def _named(session: AsyncSession, column: Any, name: Any, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await session.execute(select(column, name).where(column.in_(ids)))
    return {int(i): str(n) for i, n in rows.all()}


async def plan_to_out(session: AsyncSession, plan_dict: Mapping[str, Any]) -> LoginPlanOut:
    """``login.plan_to_dict`` (ids only) with role/group names resolved by IN-list."""
    assignments = plan_dict.get("assignments") or {}
    add_roles = [int(i) for i in assignments.get("add_roles", [])]
    remove_roles = [int(i) for i in assignments.get("remove_roles", [])]
    add_groups = [(int(g), str(p)) for g, p in assignments.get("add_groups", [])]
    remove_groups = [(int(g), str(p)) for g, p in assignments.get("remove_groups", [])]
    role_names = await _named(session, Roles.id, Roles.name, {*add_roles, *remove_roles})
    group_names = await _named(
        session,
        PermissionGroups.id,
        PermissionGroups.name,
        {g for g, _ in add_groups} | {g for g, _ in remove_groups},
    )

    def roles(ids: list[int]) -> list[NamedRefOut]:
        return [NamedRefOut(id=i, name=role_names.get(i, str(i))) for i in ids]

    def groups(pairs: list[tuple[int, str]]) -> list[GroupPermRefOut]:
        return [
            GroupPermRefOut(group_id=g, group_name=group_names.get(g, str(g)), permission_key=p)
            for g, p in pairs
        ]

    return LoginPlanOut(
        user_kind=plan_dict["user_kind"],
        decision=plan_dict["decision"],
        matched_by=plan_dict.get("matched_by"),
        user_id=plan_dict.get("user_id"),
        login=plan_dict.get("login"),
        reject_code=plan_dict.get("reject_code"),
        reject_message=plan_dict.get("reject_message"),
        profile=dict(plan_dict.get("profile") or {}),
        profile_changes={k: list(v) for k, v in (plan_dict.get("profile_changes") or {}).items()},
        roles_add=roles(add_roles),
        roles_remove=roles(remove_roles),
        groups_add=groups(add_groups),
        groups_remove=groups(remove_groups),
    )


async def _ensure_env(session: AsyncSession, settings: Settings) -> None:
    """Env rows are created lazily; a failed sync must not break the admin list."""
    try:
        await ensure_env_providers(session, settings)
    except Exception as exc:  # noqa: BLE001 - best effort, same as the login path
        logger.warning("env_provider_sync_failed", error=str(exc))
        with contextlib.suppress(Exception):
            await session.rollback()


def _metadata_stale(before: ProviderRecord, after: ProviderRecord) -> bool:
    """True when a save changed the metadata source (or a URL has not been fetched yet)."""
    old, new = before.config, after.config
    if not isinstance(old, SamlProviderConfig) or not isinstance(new, SamlProviderConfig):
        return False
    if any(getattr(old, f) != getattr(new, f) for f in _METADATA_FIELDS):
        return True
    return (
        bool(new.idp_metadata_url.strip())
        and not new.idp_metadata_xml.strip()
        and not after.metadata_xml
    )


async def _check_metadata(
    session: AsyncSession, settings: Settings, record: ProviderRecord
) -> ProviderRecord:
    """Fetch (URL) or validate (pasted XML) SAML metadata after a save. Flushes only.

    Raises ``ProviderValidationError`` so the whole save is rolled back and the admin sees
    the reason on the field.
    """
    cfg = record.config
    if not isinstance(cfg, SamlProviderConfig):
        return record
    xml = cfg.idp_metadata_xml.strip()
    if xml:
        try:
            parse_idp_metadata(xml, entity_id=cfg.idp_entity_id or None)
        except SamlMetadataError as exc:
            raise ProviderValidationError({"config.idp_metadata_xml": str(exc)}) from exc
        return record
    if not cfg.idp_metadata_url.strip():
        return record
    try:
        return await refresh_provider_metadata(
            session, record, settings=settings, transport=_metadata_transport
        )
    except SamlMetadataError as exc:
        raise ProviderValidationError({"config.idp_metadata_url": str(exc)}) from exc


# --------------------------------------------------------------------------- static routes


@router.get("", response_model=list[IdentityProviderSummaryOut])
async def list_identity_providers(
    admin: AdminUser, session: DbSession, settings: AppSettings
) -> list[IdentityProviderSummaryOut]:
    _ = admin
    await _ensure_env(session, settings)
    records = await list_providers(session, secret_key=settings.secret_key)
    counts = await _link_counts(session)
    return [IdentityProviderSummaryOut(**_summary_fields(r, counts.get(r.id, 0))) for r in records]


@router.get("/kinds", response_model=list[IdentityKindOut])
async def list_identity_kinds(admin: AdminUser) -> list[IdentityKindOut]:
    _ = admin
    return [
        IdentityKindOut(
            kind=kind,
            json_schema=model.model_json_schema(),
            i18n_prefix=f"admin.identity.config.{kind}",
            read_only_fields=sorted(READ_ONLY_CONFIG_FIELDS[kind]),
            hidden_fields=sorted(HIDDEN_CONFIG_FIELDS[kind]),
        )
        for kind, model in CONFIG_MODELS.items()
    ]


@router.get("/presets", response_model=list[IdentityPresetOut])
async def list_identity_presets(admin: AdminUser) -> list[IdentityPresetOut]:
    _ = admin
    return [
        IdentityPresetOut(
            key=p.key,
            kind=p.kind,
            name_key=p.name_key,
            button_icon=p.button_icon,
            audience_default=p.audience_default,
        )
        for p in PRESETS
    ]


@router.get("/capabilities", response_model=IdentityCapabilitiesOut)
async def identity_capabilities(admin: AdminUser, settings: AppSettings) -> IdentityCapabilitiesOut:
    _ = admin
    availability = saml_runtime.saml_availability()
    return IdentityCapabilitiesOut(
        saml_available=availability.available,
        saml_missing=availability.missing,
        public_base_url_set=bool(settings.public_base_url.strip()),
        webauthn=webauthn_enabled(settings),
        session_idle_max_seconds=settings.session_ttl_seconds,
        session_absolute_max_seconds=settings.session_absolute_ttl_seconds,
        scim_base_url=scim_base_url(settings),
    )


@router.post("", response_model=IdentityProviderOut, status_code=status.HTTP_201_CREATED)
async def create_identity_provider(
    body: IdentityProviderCreate, admin: AdminUser, session: DbSession, settings: AppSettings
) -> IdentityProviderOut:
    try:
        record = await create_provider(
            session,
            secret_key=settings.secret_key,
            actor_id=admin.id,
            preset=body.preset,
            name=body.name,
            slug=body.slug,
            audience=body.audience,
        )
        await session.commit()
    except ProviderValidationError as exc:
        await session.rollback()
        raise _validation_error(exc) from exc
    return await _out(session, settings, record)


# --------------------------------------------------------------------------- one provider


@router.get("/{provider_id}", response_model=IdentityProviderOut)
async def get_identity_provider(
    provider_id: int, admin: AdminUser, session: DbSession, settings: AppSettings
) -> IdentityProviderOut:
    _ = admin
    await _ensure_env(session, settings)
    return await _out(session, settings, await _require(session, settings, provider_id))


def _merged_changes(patch: IdentityProviderPatch, before: ProviderRecord | None) -> ProviderChanges:
    """The PATCH body as ``ProviderChanges``, its partial MFA policy merged onto the stored one.

    The merged policy is validated here (the only write path), so out-of-range lists answer
    422 with the field name instead of a 500.
    """
    data = patch.model_dump(exclude_unset=True)
    given = data.pop("mfa_policy", None)
    if given is not None:
        base = before.mfa_policy if before is not None else MfaPolicy()
        updates = {k: v for k, v in given.items() if v is not None}
        try:
            data["mfa_policy"] = MfaPolicy.model_validate(
                base.model_copy(update=updates).model_dump()
            )
        except ValidationError as exc:
            msg = "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())
            raise _validation_error(ProviderValidationError({"mfa_policy": msg})) from exc
    return ProviderChanges.model_validate(data)


@router.patch("/{provider_id}", response_model=IdentityProviderOut)
async def update_identity_provider(
    provider_id: int,
    patch: IdentityProviderPatch,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> IdentityProviderOut:
    try:
        before = await get_provider(session, provider_id, secret_key=settings.secret_key)
        body = _merged_changes(patch, before)
        record = await update_provider(
            session,
            provider_id,
            body,
            secret_key=settings.secret_key,
            actor_id=admin.id,
            settings=settings,
        )
        if body.config is not None and before is not None and _metadata_stale(before, record):
            record = await _check_metadata(session, settings, record)
        await session.commit()
    except ProviderNotFound:
        await session.rollback()
        raise _not_found() from None
    except ProviderReadOnly:
        await session.rollback()
        raise _read_only() from None
    except ProviderValidationError as exc:
        await session.rollback()
        raise _validation_error(exc) from exc
    return await _out(session, settings, record)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity_provider(
    provider_id: int, admin: AdminUser, session: DbSession
) -> Response:
    _ = admin
    try:
        await delete_provider(session, provider_id)
        await session.commit()
    except ProviderNotFound:
        await session.rollback()
        raise _not_found() from None
    except ProviderReadOnly:
        await session.rollback()
        raise _read_only() from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{provider_id}/refresh-metadata", response_model=IdentityProviderOut)
async def refresh_identity_provider_metadata(
    provider_id: int, admin: AdminUser, session: DbSession, settings: AppSettings
) -> IdentityProviderOut:
    _ = admin
    record = await _require(session, settings, provider_id)
    if record.kind != "saml":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"message": MSG_NOT_SAML})
    cfg = record.config
    if isinstance(cfg, SamlProviderConfig) and not cfg.idp_metadata_url.strip():
        # Pasted XML (or nothing): there is nothing to fetch and nothing to record as an error.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"message": MSG_NO_METADATA_URL}
        )
    try:
        record = await refresh_provider_metadata(
            session, record, settings=settings, transport=_metadata_transport
        )
        await session.commit()
    except SamlMetadataError as exc:
        message = str(exc)
        with contextlib.suppress(Exception):
            await record_metadata_error(session, record.id, message)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"message": message}
        ) from exc
    return await _out(session, settings, record)


# --------------------------------------------------------------------------- test login / preview


def _login_url(slug: str, audience: str, test_id: str) -> str:
    prefix = "/api/v1/auth" if audience == "agent" else "/api/portal/auth"
    return f"{prefix}/sso/{slug}/login?test={test_id}"


async def _check_draft(
    session: AsyncSession, settings: Settings, record: ProviderRecord, draft: ProviderChanges | None
) -> ProviderRecord:
    """The record with the (validated) unsaved draft applied; env rows take no draft."""
    if draft is None:
        return record
    if record.read_only:
        raise _read_only()
    try:
        await validate_changes(session, draft, settings=settings, record=record)
        return record_with_changes(record, draft)
    except ProviderValidationError as exc:
        raise _validation_error(exc) from exc


@router.post("/{provider_id}/tests", response_model=IdentityTestStartOut)
async def start_identity_test(
    provider_id: int,
    body: IdentityTestStartIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    redis_client: RedisDep,
) -> IdentityTestStartOut:
    await _ensure_env(session, settings)
    record = await _require(session, settings, provider_id)
    if record.kind == "ldap":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"message": MSG_NO_LDAP_TEST}
        )
    if not audience_includes(record.audience, body.audience):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"message": MSG_AUDIENCE})
    await _check_draft(session, settings, record, body.draft)
    test_id = secrets.token_urlsafe(24)
    await flow_state.put_test(
        redis_client,
        test_id,
        flow_state.TestRun(
            provider_id=record.id, admin_id=admin.id, audience=body.audience, draft=body.draft
        ),
        secret_key=settings.secret_key,
    )
    return IdentityTestStartOut(
        test_id=test_id, login_url=_login_url(record.slug, body.audience, test_id)
    )


@router.get("/{provider_id}/tests/{test_id}", response_model=IdentityTestResultOut)
async def get_identity_test(
    provider_id: int,
    test_id: str,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    redis_client: RedisDep,
) -> IdentityTestResultOut:
    run = await flow_state.get_test(redis_client, test_id, secret_key=settings.secret_key)
    if run is None or run.provider_id != provider_id or run.admin_id != admin.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Test nicht gefunden.")
    result = run.result
    return IdentityTestResultOut(
        status=run.status,
        error=run.error,
        subject=result.subject if result else None,
        attributes=result.attributes if result else {},
        plan=await plan_to_out(session, result.plan) if result else None,
        mfa=IdentityTestMfaOut.model_validate(result.mfa) if result and result.mfa else None,
    )


@router.post("/{provider_id}/preview", response_model=LoginPlanOut)
async def preview_identity_mapping(
    provider_id: int,
    body: MappingPreviewIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> LoginPlanOut:
    _ = admin
    await _ensure_env(session, settings)
    record = await _require(session, settings, provider_id)
    if not audience_includes(record.audience, body.audience):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"message": MSG_AUDIENCE})
    record = await _check_draft(session, settings, record, body.draft)
    identity = ExternalIdentity(
        provider_id=record.id,
        subject=body.subject,
        attributes={k: tuple(v) for k, v in body.attributes.items()},
    )
    plan = await plan_login(session, record, identity, user_kind=body.audience, settings=settings)
    return await plan_to_out(session, plan_to_dict(plan))


# --------------------------------------------------------------------------- links / assignments


@router.get("/{provider_id}/links", response_model=list[IdentityLinkOut])
async def list_identity_links(
    provider_id: int,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[IdentityLinkOut]:
    _ = admin
    await _require(session, settings, provider_id)
    rows = await links.list_links(session, provider_id=provider_id, limit=limit)
    return [
        IdentityLinkOut(
            id=r.id,
            user_kind=r.user_kind,
            subject=r.subject,
            user_id=r.user_id,
            user_login=r.user_login,
            first_seen=r.first_seen,
            last_seen=r.last_seen,
        )
        for r in rows
    ]


@router.delete("/{provider_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity_link(
    provider_id: int, link_id: int, admin: AdminUser, session: DbSession
) -> Response:
    _ = admin
    if not await links.delete_link(session, provider_id=provider_id, link_id=link_id):
        await session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Verknüpfung nicht gefunden.")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{provider_id}/managed-assignments", response_model=list[ManagedAssignmentOut])
async def list_managed_assignments(
    provider_id: int,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=2000)] = 1000,
) -> list[ManagedAssignmentOut]:
    _ = admin
    await _require(session, settings, provider_id)
    rows = (
        await session.scalars(
            select(TiqoraIdentityManagedAssignment)
            .where(TiqoraIdentityManagedAssignment.provider_id == provider_id)
            .order_by(TiqoraIdentityManagedAssignment.id)
            .limit(limit)
        )
    ).all()
    logins = await _named(session, Users.id, Users.login, {r.user_id for r in rows})
    roles = await _named(
        session, Roles.id, Roles.name, {r.target_id for r in rows if r.kind == "role"}
    )
    groups = await _named(
        session,
        PermissionGroups.id,
        PermissionGroups.name,
        {r.target_id for r in rows if r.kind == "group"},
    )
    out: list[ManagedAssignmentOut] = []
    for r in rows:
        names = roles if r.kind == "role" else groups
        out.append(
            ManagedAssignmentOut(
                user_id=r.user_id,
                login=logins.get(r.user_id, str(r.user_id)),
                kind="role" if r.kind == "role" else "group",
                target_id=r.target_id,
                target_name=names.get(r.target_id, str(r.target_id)),
                permission_key=r.permission_key,
                created=r.created,
            )
        )
    return out
