"""SCIM User service: create, adopt, revive, read, list, replace, patch, delete (S6).

Every function flushes and never commits: the router commits once, then ends sessions of a
deactivated account and writes the audit event in its own transaction (S8). All Znuny writes go
through ``user_provisioning`` / ``identity.provisioning``; this module writes only
``tiqora_scim_*`` rows itself. There is no JOIN between ``tiqora_*`` and Znuny tables: accounts
are loaded with IN-lists of ids. Znuny rows are never deleted ("delete" deactivates and hides).

``ScimConflictError`` carries a ``reason`` (``system_user``, ``other_provider``, ``not_adopted``,
``already_managed``, ``duplicate_user_name``, ``ambiguous_login``, ``race``) for the router's
``conflict`` audit row; the response text stays generic.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.user import UserPreferences, Users
from tiqora.db.tiqora.models import TiqoraScimGroup, TiqoraScimGroupMember, TiqoraScimUser
from tiqora.domain.user_provisioning import (
    ProvisioningError,
    set_agent_validity,
    set_customer_validity,
)
from tiqora.identity.mapping import rules_use_scim
from tiqora.identity.provisioning import (
    current_profile,
    diff_profile,
    email_domain_allowed,
    provision_agent,
    provision_customer,
    sync_profile,
)
from tiqora.identity.scim.audit import ScimOperation
from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.patch import PatchOp, apply_user_patch
from tiqora.identity.scim.protocol import ListParams, ScimFilter
from tiqora.identity.scim.role_sync import enqueue_role_sync
from tiqora.identity.scim.tokens import ScimTenant
from tiqora.identity.scim.users_map import ScimUserInput, parse_user, to_profile, user_resource
from tiqora.identity.types import SYSTEM_USER_ID, UserKind, utc_now

logger = structlog.get_logger(__name__)

__all__ = [
    "ScimConflictError",
    "ScimOperation",
    "ScimUserResult",
    "create_user",
    "delete_user",
    "get_user",
    "list_users",
    "patch_user",
    "replace_user",
]

_CHUNK = 1000
_NOT_MANAGED = "An account with this userName exists and is not managed by this provider"
_NOT_FOUND = "Resource not found"
_IGNORED_NOISE = frozenset({"schemas", "id", "meta"})
_CONFLICT_CODES = frozenset({"email_in_use", "login_exists"})


class ScimConflictError(ScimError):
    """409 ``uniqueness`` with a machine-readable ``reason`` for the audit row."""

    def __init__(self, reason: str, detail: str = _NOT_MANAGED) -> None:
        super().__init__(409, "uniqueness", detail)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ScimUserResult:
    resource: dict[str, Any]
    user_id: int
    operation: ScimOperation
    deactivated: bool  # True: the router ends the account's sessions after the commit
    reactivated: bool
    detail: str | None
    # Login of the Znuny account, read before the commit: ending customer sessions after the
    # commit needs no further query (a failing read must not leave a deactivated session alive).
    login: str | None = None


@dataclass(frozen=True, slots=True)
class _Account:
    id: int
    login: str
    first_name: str
    last_name: str
    valid_id: int
    email: str | None
    mobile: str | None


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _not_found() -> ScimError:
    return ScimError(404, None, _NOT_FOUND)


def _provisioning_to_scim(exc: ProvisioningError) -> ScimError:
    """One mapping for every write path (create, adopt, revive, replace, patch)."""
    if exc.code in _CONFLICT_CODES:
        return ScimError(409, "uniqueness", exc.code)
    # email_invalid/email_required/name_required/jit_missing_fields and unknown codes: 400
    return ScimError(400, "invalidValue", exc.code)


def _decode(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    return raw if isinstance(raw, str) else bytes(raw).decode("utf-8", errors="replace")


def _forbid_system_user(kind: UserKind, user_id: int) -> None:
    if kind == "agent" and user_id == SYSTEM_USER_ID:
        raise ScimError(409, "mutability", "This account cannot be changed")


def _chunks(ids: Sequence[int]) -> list[list[int]]:
    unique = sorted(set(ids))
    return [unique[i : i + _CHUNK] for i in range(0, len(unique), _CHUNK)]


# --------------------------------------------------------------------------- loading


async def _load_accounts(
    session: AsyncSession, kind: UserKind, ids: Sequence[int]
) -> dict[int, _Account]:
    out: dict[int, _Account] = {}
    for chunk in _chunks(ids):
        if kind == "agent":
            rows = (
                await session.execute(
                    select(
                        Users.id, Users.login, Users.first_name, Users.last_name, Users.valid_id
                    ).where(Users.id.in_(chunk))
                )
            ).all()
            prefs: dict[tuple[int, str], str | None] = {
                (int(uid), str(key)): _decode(value)
                for uid, key, value in (
                    await session.execute(
                        select(
                            UserPreferences.user_id,
                            UserPreferences.preferences_key,
                            UserPreferences.preferences_value,
                        ).where(
                            UserPreferences.user_id.in_(chunk),
                            UserPreferences.preferences_key.in_(("UserEmail", "UserMobile")),
                        )
                    )
                ).all()
            }
            for r in rows:
                out[int(r[0])] = _Account(
                    id=int(r[0]),
                    login=r[1],
                    first_name=r[2],
                    last_name=r[3],
                    valid_id=int(r[4]),
                    email=prefs.get((int(r[0]), "UserEmail")) or None,
                    mobile=prefs.get((int(r[0]), "UserMobile")) or None,
                )
        else:
            crows = (
                await session.execute(
                    select(
                        CustomerUser.id,
                        CustomerUser.login,
                        CustomerUser.first_name,
                        CustomerUser.last_name,
                        CustomerUser.valid_id,
                        CustomerUser.email,
                        CustomerUser.mobile,
                    ).where(CustomerUser.id.in_(chunk))
                )
            ).all()
            for c in crows:
                out[int(c[0])] = _Account(
                    id=int(c[0]),
                    login=c[1],
                    first_name=c[2],
                    last_name=c[3],
                    valid_id=int(c[4]),
                    email=c[5] or None,
                    mobile=c[6] or None,
                )
    return out


async def _groups_of(
    session: AsyncSession, member_ids: Sequence[int]
) -> dict[int, list[tuple[str, str]]]:
    out: dict[int, list[tuple[str, str]]] = {}
    for chunk in _chunks(member_ids):
        rows = (
            await session.execute(
                select(
                    TiqoraScimGroupMember.member_id,
                    TiqoraScimGroup.scim_id,
                    TiqoraScimGroup.display_name,
                )
                .join(TiqoraScimGroup, TiqoraScimGroup.id == TiqoraScimGroupMember.group_id)
                .where(TiqoraScimGroupMember.member_id.in_(chunk))
                .order_by(TiqoraScimGroup.id)
            )
        ).all()
        for member_id, gid, display in rows:
            out.setdefault(int(member_id), []).append((gid, display))
    return out


async def _render(
    session: AsyncSession, tenant: ScimTenant, rows: Sequence[TiqoraScimUser], base: str
) -> list[dict[str, Any]]:
    accounts = await _load_accounts(session, tenant.user_kind, [r.user_id for r in rows])
    groups = await _groups_of(session, [r.id for r in rows])
    out: list[dict[str, Any]] = []
    for r in rows:
        acc = accounts.get(r.user_id)
        if acc is None:
            continue
        out.append(
            user_resource(
                scim_id=r.scim_id,
                user_name=r.user_name,
                external_id=r.external_id,
                active=acc.valid_id == 1,
                first_name=acc.first_name,
                last_name=acc.last_name,
                email=acc.email,
                mobile=acc.mobile,
                groups=groups.get(r.id, []),
                location=f"{base}/Users/{r.scim_id}",
                created=r.created,
                changed=r.changed,
                version=r.version,
            )
        )
    return out


async def _render_one(
    session: AsyncSession, tenant: ScimTenant, row: TiqoraScimUser, base: str
) -> dict[str, Any]:
    docs = await _render(session, tenant, [row], base)
    if not docs:
        raise _not_found()
    return docs[0]


def _tenant_rows(tenant: ScimTenant) -> Select[tuple[TiqoraScimUser]]:
    return select(TiqoraScimUser).where(
        TiqoraScimUser.provider_id == tenant.provider.id,
        TiqoraScimUser.user_kind == tenant.user_kind,
        TiqoraScimUser.deleted.is_(False),
    )


async def _get_row(session: AsyncSession, tenant: ScimTenant, scim_id: str) -> TiqoraScimUser:
    if not scim_id or len(scim_id) > 36:
        raise _not_found()
    row = (
        await session.execute(_tenant_rows(tenant).where(TiqoraScimUser.scim_id == scim_id))
    ).scalar_one_or_none()
    if row is None:
        raise _not_found()
    return row


async def _name_taken(
    session: AsyncSession, tenant: ScimTenant, fold_hash: str, *, exclude_id: int | None
) -> bool:
    stmt = _tenant_rows(tenant).where(TiqoraScimUser.user_name_fold_hash == fold_hash)
    if exclude_id is not None:
        stmt = stmt.where(TiqoraScimUser.id != exclude_id)
    return (await session.execute(stmt.limit(1))).first() is not None


async def _account_by_login(
    session: AsyncSession, kind: UserKind, login: str
) -> tuple[int, str] | None:
    """(id, login) of the account with this login, any validity. Exact case wins, else exactly one
    case-insensitive hit (the SQL ``lower()`` is only a prefilter, as in ``login._match_login``)."""
    wanted = login.strip()
    if kind == "agent":
        stmt = select(Users.id, Users.login).where(
            func.lower(func.rtrim(Users.login)) == wanted.lower()
        )
    else:
        stmt = select(CustomerUser.id, CustomerUser.login).where(
            func.lower(func.rtrim(CustomerUser.login)) == wanted.lower()
        )
    rows = [(int(r[0]), str(r[1])) for r in (await session.execute(stmt)).all()]
    exact = [r for r in rows if r[1] == wanted]
    if len(exact) == 1:
        return exact[0]
    if exact:
        raise ScimConflictError("ambiguous_login", "userName is ambiguous")
    folded = [r for r in rows if r[1].strip().casefold() == wanted.casefold()]
    if len(folded) > 1:
        raise ScimConflictError("ambiguous_login", "userName is ambiguous")
    return folded[0] if folded else None


async def _row_of_account(
    session: AsyncSession, kind: UserKind, user_id: int
) -> TiqoraScimUser | None:
    return (
        await session.execute(
            select(TiqoraScimUser).where(
                TiqoraScimUser.user_kind == kind, TiqoraScimUser.user_id == user_id
            )
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------------------- writes


async def _set_validity(session: AsyncSession, kind: UserKind, user_id: int, valid_id: int) -> bool:
    _forbid_system_user(kind, user_id)
    if kind == "agent":
        user = await session.get(Users, user_id)
        if user is None:
            raise _not_found()
        return await set_agent_validity(session, user, valid_id=valid_id, actor_id=SYSTEM_USER_ID)
    cu = await session.get(CustomerUser, user_id)
    if cu is None:
        raise _not_found()
    return await set_customer_validity(session, cu, valid_id=valid_id, actor_id=SYSTEM_USER_ID)


async def _apply_active(
    session: AsyncSession, kind: UserKind, account: _Account, active: bool
) -> tuple[bool, bool]:
    """(deactivated, reactivated). Invalid ids other than 1 count as inactive."""
    if not active and account.valid_id == 1:
        await _set_validity(session, kind, account.id, 2)
        return True, False
    if active and account.valid_id != 1:
        await _set_validity(session, kind, account.id, 1)
        return False, True
    return False, False


async def _apply_profile(
    session: AsyncSession, tenant: ScimTenant, account: _Account, inp: ScimUserInput
) -> list[str]:
    """Sync name/e-mail/mobile that the IdP sent and that differ; returns the field names."""
    kind = tenant.user_kind
    _forbid_system_user(kind, account.id)
    changes = diff_profile(to_profile(inp), await current_profile(session, kind, account.id))
    if "email" in changes and not email_domain_allowed(tenant.provider, changes["email"][1]):
        raise ScimError(400, "invalidValue", "domain_not_allowed")
    try:
        await sync_profile(session, user_kind=kind, user_id=account.id, changes=changes)
    except ProvisioningError as exc:
        raise _provisioning_to_scim(exc) from exc
    return sorted(changes)


async def _enqueue(session: AsyncSession, tenant: ScimTenant, user_id: int) -> None:
    if tenant.user_kind == "agent" and rules_use_scim(tenant.provider.role_rules):
        await enqueue_role_sync(session, tenant.provider.id, [user_id])


def _new_row(tenant: ScimTenant, user_id: int, inp: ScimUserInput) -> TiqoraScimUser:
    now = utc_now()
    fold = inp.user_name.casefold()
    return TiqoraScimUser(
        provider_id=tenant.provider.id,
        user_kind=tenant.user_kind,
        scim_id=str(uuid.uuid4()),
        user_id=user_id,
        user_name=inp.user_name,
        user_name_fold=fold,
        user_name_fold_hash=_sha(fold),
        external_id=inp.external_id,
        external_id_hash=_sha(inp.external_id) if inp.external_id else None,
        deleted=False,
        version=1,
        created=now,
        changed=now,
    )


async def _flush_row(session: AsyncSession, row: TiqoraScimUser) -> None:
    """Insert/flush a mapping row; a parallel POST on the unique account slot answers 409."""
    try:
        session.add(row)
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise ScimConflictError("race") from None


async def _drop_row(session: AsyncSession, row: TiqoraScimUser) -> None:
    await session.execute(
        delete(TiqoraScimGroupMember).where(TiqoraScimGroupMember.member_id == row.id)
    )
    await session.execute(delete(TiqoraScimUser).where(TiqoraScimUser.id == row.id))
    await session.flush()


def _log_ignored(doc: Mapping[str, Any], sent: frozenset[str]) -> None:
    ignored = sorted(str(k) for k in doc if k not in sent and k not in _IGNORED_NOISE)
    if ignored:
        logger.info("scim.user.ignored_fields", fields=ignored)


async def create_user(
    session: AsyncSession, tenant: ScimTenant, doc: Mapping[str, Any], *, base: str
) -> ScimUserResult:
    inp = parse_user(doc)
    _log_ignored(doc, inp.sent)
    kind, provider = tenant.user_kind, tenant.provider
    if await _name_taken(session, tenant, _sha(inp.user_name.casefold()), exclude_id=None):
        raise ScimConflictError("duplicate_user_name", "userName already exists")

    found = await _account_by_login(session, kind, inp.user_name)
    operation: ScimOperation
    deactivated = reactivated = False
    detail: str | None = None
    login: str | None = None

    if found is None:
        if inp.email and not email_domain_allowed(provider, inp.email):
            raise ScimError(400, "invalidValue", "domain_not_allowed")
        try:
            if kind == "agent":
                user_id = (await provision_agent(session, provider, to_profile(inp))).id
            else:
                user_id = (await provision_customer(session, provider, to_profile(inp))).id
        except ProvisioningError as exc:
            raise _provisioning_to_scim(exc) from exc
        except IntegrityError:  # a parallel POST won Znuny's unique login/e-mail slot
            await session.rollback()
            raise ScimConflictError("race") from None
        if inp.active is False:
            await _set_validity(session, kind, user_id, 2)
        row = _new_row(tenant, user_id, inp)
        await _flush_row(session, row)
        operation = "create"
    else:
        user_id = found[0]
        if kind == "agent" and user_id == SYSTEM_USER_ID:
            raise ScimConflictError("system_user")
        existing = await _row_of_account(session, kind, user_id)
        if existing is not None and existing.provider_id != provider.id:
            if not existing.deleted:
                raise ScimConflictError("other_provider")
            # S6 amendment: a deleted mapping of another provider is released, and only to a
            # provider that adopts; the gate runs before anything is written.
            if not provider.scim_adopt_existing:
                raise ScimConflictError("not_adopted")
            await _drop_row(session, existing)
            existing = None
        if existing is not None and not existing.deleted:
            raise ScimConflictError("already_managed", "userName already exists")
        if existing is None and not provider.scim_adopt_existing:
            raise ScimConflictError("not_adopted")
        account = (await _load_accounts(session, kind, [user_id])).get(user_id)
        if account is None:
            raise _not_found()
        login = account.login
        fields = await _apply_profile(session, tenant, account, inp)
        detail = ",".join(fields) or None
        if existing is None:  # adopt: an absent ``active`` leaves the account alone
            active = inp.active
            operation = "adopt"
            row = _new_row(tenant, user_id, inp)
        else:  # revive: an absent ``active`` means active
            active = True if inp.active is None else inp.active
            operation = "revive"
            row = existing
            # A member row left by a delete/group-write race must not become effective again.
            await session.execute(
                delete(TiqoraScimGroupMember).where(TiqoraScimGroupMember.member_id == row.id)
            )
            fold = inp.user_name.casefold()
            row.scim_id = str(uuid.uuid4())
            row.deleted = False
            row.user_name = inp.user_name
            row.user_name_fold = fold
            row.user_name_fold_hash = _sha(fold)
            row.external_id = inp.external_id
            row.external_id_hash = _sha(inp.external_id) if inp.external_id else None
            row.version += 1
            row.changed = utc_now()
        if active is not None:
            deactivated, reactivated = await _apply_active(session, kind, account, active)
        await _flush_row(session, row)

    await _enqueue(session, tenant, user_id)
    resource = await _render_one(session, tenant, row, base)
    return ScimUserResult(resource, user_id, operation, deactivated, reactivated, detail, login)


async def get_user(
    session: AsyncSession, tenant: ScimTenant, scim_id: str, *, base: str
) -> dict[str, Any]:
    row = await _get_row(session, tenant, scim_id)
    return await _render_one(session, tenant, row, base)


async def _email_matches(
    session: AsyncSession, kind: UserKind, user_ids: Sequence[int], value: str
) -> set[int]:
    wanted = value.strip().lower()
    hits: set[int] = set()
    for chunk in _chunks(user_ids):
        if kind == "agent":
            rows = (
                await session.execute(
                    select(UserPreferences.user_id, UserPreferences.preferences_value).where(
                        UserPreferences.user_id.in_(chunk),
                        UserPreferences.preferences_key == "UserEmail",
                    )
                )
            ).all()
            hits |= {
                int(uid) for uid, raw in rows if (_decode(raw) or "").strip().lower() == wanted
            }
        else:
            crows = (
                await session.execute(
                    select(CustomerUser.id, CustomerUser.email).where(
                        CustomerUser.id.in_(chunk), func.lower(CustomerUser.email) == wanted
                    )
                )
            ).all()
            hits |= {int(uid) for uid, email in crows if (email or "").strip().lower() == wanted}
    return hits


async def list_users(
    session: AsyncSession,
    tenant: ScimTenant,
    flt: ScimFilter | None,
    params: ListParams,
    *,
    base: str,
) -> tuple[int, list[dict[str, Any]]]:
    stmt = _tenant_rows(tenant)
    email_filter: str | None = None
    if flt is not None:
        if flt.attr == "id":
            stmt = stmt.where(TiqoraScimUser.scim_id == flt.value[:64])
        elif flt.attr == "userName":
            stmt = stmt.where(TiqoraScimUser.user_name_fold_hash == _sha(flt.value.casefold()))
        elif flt.attr == "externalId":
            stmt = stmt.where(TiqoraScimUser.external_id_hash == _sha(flt.value))
        elif flt.attr == "emails.value":
            email_filter = flt.value
        else:
            raise ScimError(400, "invalidFilter", "Unsupported filter attribute")
    stmt = stmt.order_by(TiqoraScimUser.id)
    offset = params.start_index - 1

    if email_filter is None:
        total = int(
            (
                await session.execute(
                    select(func.count()).select_from(stmt.order_by(None).subquery())
                )
            ).scalar_one()
        )
        if params.count == 0:
            return total, []
        rows = list((await session.execute(stmt.offset(offset).limit(params.count))).scalars())
    else:
        everyone = list((await session.execute(stmt)).scalars())
        hits = await _email_matches(
            session, tenant.user_kind, [r.user_id for r in everyone], email_filter
        )
        matched = [r for r in everyone if r.user_id in hits]
        total = len(matched)
        rows = matched[offset : offset + params.count] if params.count else []
    return total, await _render(session, tenant, rows, base)


async def _update(
    session: AsyncSession,
    tenant: ScimTenant,
    row: TiqoraScimUser,
    inp: ScimUserInput,
    *,
    sent: frozenset[str],
    operation: ScimOperation,
    base: str,
) -> ScimUserResult:
    kind = tenant.user_kind
    _forbid_system_user(kind, row.user_id)
    account = (await _load_accounts(session, kind, [row.user_id])).get(row.user_id)
    if account is None:
        raise _not_found()
    fields: list[str] = []
    notes: list[str] = []

    if inp.user_name != row.user_name:
        fold = inp.user_name.casefold()
        if await _name_taken(session, tenant, _sha(fold), exclude_id=row.id):
            raise ScimConflictError("duplicate_user_name", "userName already exists")
        row.user_name = inp.user_name
        row.user_name_fold = fold
        row.user_name_fold_hash = _sha(fold)
        fields.append("userName")
        if fold != account.login.strip().casefold():
            notes.append("login_not_renamed")
    if "externalId" in sent and inp.external_id != row.external_id:
        row.external_id = inp.external_id
        row.external_id_hash = _sha(inp.external_id) if inp.external_id else None
        fields.append("externalId")

    fields.extend(await _apply_profile(session, tenant, account, inp))

    deactivated = reactivated = False
    if "active" in sent and inp.active is not None:
        deactivated, reactivated = await _apply_active(session, kind, account, inp.active)
        if deactivated or reactivated:
            fields.append("active")
    if reactivated:
        await _enqueue(session, tenant, row.user_id)

    if fields:
        row.version += 1
        row.changed = utc_now()
        await session.flush()
    if deactivated:
        operation = "deactivate"
    elif reactivated:
        operation = "reactivate"
    detail = ",".join([*fields, *notes]) or None
    resource = await _render_one(session, tenant, row, base)
    return ScimUserResult(
        resource, row.user_id, operation, deactivated, reactivated, detail, account.login
    )


async def replace_user(
    session: AsyncSession,
    tenant: ScimTenant,
    scim_id: str,
    doc: Mapping[str, Any],
    *,
    base: str,
) -> ScimUserResult:
    row = await _get_row(session, tenant, scim_id)
    inp = parse_user(doc)
    _log_ignored(doc, inp.sent)
    return await _update(session, tenant, row, inp, sent=inp.sent, operation="replace", base=base)


async def patch_user(
    session: AsyncSession,
    tenant: ScimTenant,
    scim_id: str,
    ops: list[PatchOp],
    *,
    base: str,
) -> ScimUserResult:
    row = await _get_row(session, tenant, scim_id)
    current = await _render_one(session, tenant, row, base)
    patched, touched = apply_user_patch(current, ops)
    inp = parse_user(patched)
    return await _update(session, tenant, row, inp, sent=touched, operation="patch", base=base)


async def delete_user(session: AsyncSession, tenant: ScimTenant, scim_id: str) -> ScimUserResult:
    row = await _get_row(session, tenant, scim_id)
    kind = tenant.user_kind
    _forbid_system_user(kind, row.user_id)
    account = (await _load_accounts(session, kind, [row.user_id])).get(row.user_id)
    deactivated = False
    if account is not None and account.valid_id == 1:
        deactivated = await _set_validity(session, kind, row.user_id, 2)
    await session.execute(
        delete(TiqoraScimGroupMember).where(TiqoraScimGroupMember.member_id == row.id)
    )
    row.deleted = True
    row.version += 1
    row.changed = utc_now()
    await session.flush()
    login = account.login if account is not None else None
    return ScimUserResult({}, row.user_id, "delete", deactivated, False, None, login)
