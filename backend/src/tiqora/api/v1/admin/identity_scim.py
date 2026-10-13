"""Admin API for SCIM provisioning of one identity provider (S11).

Reads the ``tiqora_scim_*`` tables and the Znuny account tables separately: logins and
validity are resolved by IN-list, never by a JOIN across the two (mixed collations, error
1267). Admin-only; the raw token is returned exactly once, on creation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.api.v1.admin.identity_schemas import (
    ScimAccountOut,
    ScimAccountPageOut,
    ScimEventOut,
    ScimGroupOut,
    ScimGroupPageOut,
    ScimStatusOut,
    ScimTokenCreatedOut,
    ScimTokenCreateIn,
    ScimTokenOut,
)
from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraScimEvent,
    TiqoraScimGroup,
    TiqoraScimGroupMember,
    TiqoraScimPendingSync,
    TiqoraScimUser,
)
from tiqora.identity.scim.audit import list_events
from tiqora.identity.scim.tokens import (
    ScimTokenError,
    ScimTokenRecord,
    create_token,
    list_tokens,
    revoke_token,
)
from tiqora.identity.store import ProviderRecord, get_provider
from tiqora.identity.types import SCIM_KINDS
from tiqora.identity.urls import scim_base_url

router = APIRouter(prefix="/identity-providers/{provider_id}/scim", tags=["admin:identity:scim"])

MSG_NOT_FOUND = "Anmeldedienst nicht gefunden."
MSG_CHECK_FIELDS = "Bitte die markierten Felder prüfen."
MSG_UNAVAILABLE = (
    "Bereitstellung ist nur für OpenID-Connect- und SAML-Anmeldedienste aus der Verwaltung"
    " möglich, nicht für LDAP oder Dienste aus den Umgebungsvariablen."
)

_IN_CHUNK = 500
# Operations that are not provisioning work (they must not count as "last activity").
_NOISE_OPERATIONS = ("auth_failed", "rate_limited", "token_create", "token_revoke")


# --------------------------------------------------------------------------- helpers


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chunks(ids: Iterable[int]) -> list[list[int]]:
    items = sorted(set(ids))
    return [items[i : i + _IN_CHUNK] for i in range(0, len(items), _IN_CHUNK)]


async def _require(session: AsyncSession, settings: Settings, provider_id: int) -> ProviderRecord:
    record = await get_provider(session, provider_id, secret_key=settings.secret_key)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=MSG_NOT_FOUND)
    return record


def _available(record: ProviderRecord) -> bool:
    return record.source == "db" and record.kind in SCIM_KINDS


def _token_error(exc: ScimTokenError) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"message": MSG_CHECK_FIELDS, "errors": exc.errors},
    )


async def _logins(session: AsyncSession, kind: str, ids: set[int]) -> dict[int, str]:
    """Account logins by id (IN-list, chunked) for one account kind."""
    out: dict[int, str] = {}
    column_id, column_login = (
        (Users.id, Users.login) if kind == "agent" else (CustomerUser.id, CustomerUser.login)
    )
    for chunk in _chunks(ids):
        rows = await session.execute(select(column_id, column_login).where(column_id.in_(chunk)))
        out.update({int(i): str(login) for i, login in rows.all()})
    return out


async def _valid(session: AsyncSession, kind: str, ids: set[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    column_id, column_valid = (
        (Users.id, Users.valid_id) if kind == "agent" else (CustomerUser.id, CustomerUser.valid_id)
    )
    for chunk in _chunks(ids):
        rows = await session.execute(select(column_id, column_valid).where(column_id.in_(chunk)))
        out.update({int(i): int(v) for i, v in rows.all()})
    return out


async def _token_out(session: AsyncSession, records: list[ScimTokenRecord]) -> list[ScimTokenOut]:
    names: dict[int, str] = {}
    for chunk in _chunks(r.created_by for r in records):
        rows = await session.execute(
            select(Users.id, Users.first_name, Users.last_name, Users.login).where(
                Users.id.in_(chunk)
            )
        )
        for uid, first, last, login in rows.all():
            full = f"{first} {last}".strip()
            names[int(uid)] = full or str(login)
    return [
        ScimTokenOut(
            id=r.id,
            name=r.name,
            user_kind=r.user_kind,
            token_hint=r.token_hint,
            created_at=r.created_at,
            created_by_name=names.get(r.created_by),
            expires_at=r.expires_at,
            revoked_at=r.revoked_at,
            last_used_at=r.last_used_at,
            active=r.active,
        )
        for r in records
    ]


# --------------------------------------------------------------------------- status / tokens


@router.get("", response_model=ScimStatusOut)
async def scim_status(
    provider_id: int, admin: AdminUser, session: DbSession, settings: AppSettings
) -> ScimStatusOut:
    _ = admin
    record = await _require(session, settings, provider_id)
    available = _available(record)
    base = scim_base_url(settings)
    tokens = await _token_out(session, await list_tokens(session, provider_id)) if available else []

    users = groups = pending = active = 0
    last_activity = None
    if available:
        rows = (
            await session.execute(
                select(TiqoraScimUser.user_kind, TiqoraScimUser.user_id).where(
                    TiqoraScimUser.provider_id == provider_id, TiqoraScimUser.deleted.is_(False)
                )
            )
        ).all()
        users = len(rows)
        for kind in ("agent", "customer"):
            ids = {int(uid) for k, uid in rows if k == kind}
            active += sum(1 for v in (await _valid(session, kind, ids)).values() if v == 1)
        groups = int(
            (
                await session.execute(
                    select(func.count(TiqoraScimGroup.id)).where(
                        TiqoraScimGroup.provider_id == provider_id
                    )
                )
            ).scalar_one()
        )
        pending = int(
            (
                await session.execute(
                    select(func.count()).where(TiqoraScimPendingSync.provider_id == provider_id)
                )
            ).scalar_one()
        )
        last_activity = (
            await session.execute(
                select(func.max(TiqoraScimEvent.created)).where(
                    TiqoraScimEvent.provider_id == provider_id,
                    TiqoraScimEvent.operation.not_in(_NOISE_OPERATIONS),
                )
            )
        ).scalar_one()
    return ScimStatusOut(
        base_url=base,
        available=available,
        reason=None if available else MSG_UNAVAILABLE,
        enabled=record.scim_enabled,
        adopt_existing=record.scim_adopt_existing,
        tokens=tokens,
        users=users,
        active_users=active,
        groups=groups,
        pending_sync=pending,
        last_activity_at=last_activity,
    )


@router.post("/tokens", response_model=ScimTokenCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_scim_token(
    provider_id: int,
    body: ScimTokenCreateIn,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> ScimTokenCreatedOut:
    record = await _require(session, settings, provider_id)
    try:
        # Pre-checks (provider kind, audience, limits) run before any write.
        token, raw = await create_token(
            session,
            provider=record,
            user_kind=body.user_kind,
            name=body.name,
            expires_in_days=body.expires_in_days,
            actor_id=admin.id,
        )
        await session.commit()
    except ScimTokenError as exc:
        await session.rollback()
        raise _token_error(exc) from exc
    (out,) = await _token_out(session, [token])
    return ScimTokenCreatedOut(token=raw, record=out)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_scim_token(
    provider_id: int,
    token_id: int,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
) -> Response:
    await _require(session, settings, provider_id)
    try:
        await revoke_token(session, provider_id=provider_id, token_id=token_id, actor_id=admin.id)
        await session.commit()
    except ScimTokenError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.errors["token"]) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- accounts / groups


async def _search_ids(
    session: AsyncSession, provider_id: int, q: str, *, include_deleted: bool
) -> list[int]:
    """Mapping ids of *provider_id* whose stored userName equals ``q`` (casefold hash) or whose
    local login contains ``q`` (case-insensitive on both dialects).

    The provider's own mappings are loaded first and the Znuny logins are matched by chunked
    IN-list, so the result never depends on other providers' or unmanaged accounts.
    """
    needle = q.strip()
    name_hash = _sha(needle.casefold())
    pattern = (
        "%" + needle.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )
    where: list[Any] = [TiqoraScimUser.provider_id == provider_id]
    if not include_deleted:
        where.append(TiqoraScimUser.deleted.is_(False))
    mapped = (
        await session.execute(
            select(
                TiqoraScimUser.id,
                TiqoraScimUser.user_kind,
                TiqoraScimUser.user_id,
                TiqoraScimUser.user_name_fold_hash,
            ).where(*where)
        )
    ).all()
    hits = {int(row_id) for row_id, _, _, fold_hash in mapped if fold_hash == name_hash}
    for kind in ("agent", "customer"):
        by_account: dict[int, list[int]] = {}
        for row_id, row_kind, user_id, _ in mapped:
            if row_kind == kind:
                by_account.setdefault(int(user_id), []).append(int(row_id))
        column_id, column_login = (
            (Users.id, Users.login) if kind == "agent" else (CustomerUser.id, CustomerUser.login)
        )
        for chunk in _chunks(by_account):
            matched = await session.scalars(
                select(column_id).where(
                    column_id.in_(chunk), func.lower(column_login).like(pattern, escape="\\")
                )
            )
            for account_id in matched.all():
                hits.update(by_account[int(account_id)])
    return sorted(hits)


@router.get("/accounts", response_model=ScimAccountPageOut)
async def scim_accounts(
    provider_id: int,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[str | None, Query(max_length=200)] = None,
    include_deleted: bool = False,
) -> ScimAccountPageOut:
    _ = admin
    await _require(session, settings, provider_id)
    where: list[Any] = [TiqoraScimUser.provider_id == provider_id]
    if not include_deleted:
        where.append(TiqoraScimUser.deleted.is_(False))
    if q is not None and q.strip():
        matched = await _search_ids(session, provider_id, q, include_deleted=include_deleted)
        total = len(matched)
        page = matched[offset : offset + limit]
        rows = (
            (
                await session.scalars(
                    select(TiqoraScimUser)
                    .where(TiqoraScimUser.id.in_(page))
                    .order_by(TiqoraScimUser.id)
                )
            ).all()
            if page
            else []
        )
    else:
        total = int(
            (
                await session.execute(select(func.count(TiqoraScimUser.id)).where(*where))
            ).scalar_one()
        )
        rows = (
            await session.scalars(
                select(TiqoraScimUser)
                .where(*where)
                .order_by(TiqoraScimUser.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    items: list[ScimAccountOut] = []
    logins: dict[str, dict[int, str]] = {}
    valid: dict[str, dict[int, int]] = {}
    for account_kind in ("agent", "customer"):
        ids = {int(r.user_id) for r in rows if r.user_kind == account_kind}
        logins[account_kind] = await _logins(session, account_kind, ids)
        valid[account_kind] = await _valid(session, account_kind, ids)
    for r in rows:
        kind: Literal["agent", "customer"] = "customer" if r.user_kind == "customer" else "agent"
        uid = int(r.user_id)
        items.append(
            ScimAccountOut(
                scim_id=r.scim_id,
                user_kind=kind,
                user_id=uid,
                login=logins[kind].get(uid),
                user_name=r.user_name,
                external_id=r.external_id,
                active=(not r.deleted) and valid[kind].get(uid) == 1,
                deleted=bool(r.deleted),
                changed=r.changed,
            )
        )
    return ScimAccountPageOut(total=total, items=items)


@router.get("/groups", response_model=ScimGroupPageOut)
async def scim_groups(
    provider_id: int,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ScimGroupPageOut:
    _ = admin
    await _require(session, settings, provider_id)
    total = int(
        (
            await session.execute(
                select(func.count(TiqoraScimGroup.id)).where(
                    TiqoraScimGroup.provider_id == provider_id
                )
            )
        ).scalar_one()
    )
    rows = (
        await session.scalars(
            select(TiqoraScimGroup)
            .where(TiqoraScimGroup.provider_id == provider_id)
            .order_by(TiqoraScimGroup.display_name_fold, TiqoraScimGroup.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    counts: dict[int, int] = {}
    group_ids = [int(r.id) for r in rows]
    if group_ids:
        counted = await session.execute(
            select(TiqoraScimGroupMember.group_id, func.count())
            .where(TiqoraScimGroupMember.group_id.in_(group_ids))
            .group_by(TiqoraScimGroupMember.group_id)
        )
        counts = {int(g): int(n) for g, n in counted.all()}
    return ScimGroupPageOut(
        total=total,
        items=[
            ScimGroupOut(
                scim_id=r.scim_id,
                display_name=r.display_name,
                external_id=r.external_id,
                member_count=counts.get(int(r.id), 0),
                changed=r.changed,
            )
            for r in rows
        ],
    )


@router.get("/events", response_model=list[ScimEventOut])
async def scim_events(
    provider_id: int,
    admin: AdminUser,
    session: DbSession,
    settings: AppSettings,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    failures_only: bool = False,
) -> list[ScimEventOut]:
    _ = admin
    await _require(session, settings, provider_id)
    rows = await list_events(
        session,
        provider_id=provider_id,
        before_id=before_id,
        limit=limit,
        failures_only=failures_only,
    )
    logins: dict[str, dict[int, str]] = {}
    for account_kind in ("agent", "customer"):
        ids = {
            int(r.user_id) for r in rows if r.user_kind == account_kind and r.user_id is not None
        }
        logins[account_kind] = await _logins(session, account_kind, ids)
    out: list[ScimEventOut] = []
    for r in rows:
        kind: Literal["agent", "customer"] | None = (
            "customer" if r.user_kind == "customer" else "agent" if r.user_kind == "agent" else None
        )
        out.append(
            ScimEventOut(
                id=int(r.id),
                created=r.created,
                method=r.method,
                resource=r.resource,
                operation=r.operation,
                status=int(r.status),
                scim_id=r.scim_id,
                user_kind=kind,
                user_id=r.user_id,
                login=logins[kind].get(int(r.user_id)) if kind and r.user_id is not None else None,
                detail=r.detail,
            )
        )
    return out
