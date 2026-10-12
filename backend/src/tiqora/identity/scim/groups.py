"""SCIM Group service: create, read, list, replace, patch, delete (S7).

Groups exist only for agent providers. They are mirrored in ``tiqora_scim_group*`` and change
Znuny roles/groups solely through the wave-1 rule engine (``scim:groups`` / ``scim:group_ids``):
after a write, the affected agents are queued with ``enqueue_role_sync`` when the provider's
rules read SCIM attributes; the ``scim_role_sync`` worker applies them.

Every function flushes and never commits (the router commits once and writes the audit event).
Only ``tiqora_*`` tables are touched; members are SCIM user rows of the same tenant, resolved with
IN-lists of at most 1000 ids and written with bulk inserts / IN-list deletes, so a 5000-member
push stays far below the request budget. Unknown member ids are ignored and counted
(``ignored_members``). Write paths lock the group row (``FOR UPDATE``) and read the member list
with a locking read, so parallel pushes of the same group serialise instead of colliding on the
member primary key.

R-TX precondition: ``_resolve`` and ``_render`` are plain reads. On InnoDB REPEATABLE READ a plain
read returns the snapshot of the transaction's first consistent read, so a write service must be
called on a transaction without an open consistent read: the router rolls back (or commits) after
``authenticate_token`` and before calling create/replace/patch/delete. Only the group row and the
member list are read with locks, which makes the diff itself independent of that snapshot.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Select, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraScimGroup, TiqoraScimGroupMember, TiqoraScimUser
from tiqora.identity.mapping import rules_use_scim
from tiqora.identity.scim.audit import ScimOperation
from tiqora.identity.scim.errors import ScimError
from tiqora.identity.scim.patch import PatchOp, apply_group_patch
from tiqora.identity.scim.protocol import SCHEMA_GROUP, ListParams, ScimFilter, meta
from tiqora.identity.scim.role_sync import enqueue_role_sync
from tiqora.identity.scim.tokens import ScimTenant
from tiqora.identity.scim.users_map import _clean_text
from tiqora.identity.types import utc_now

__all__ = [
    "ScimGroupResult",
    "create_group",
    "delete_group",
    "get_group",
    "list_groups",
    "patch_group",
    "replace_group",
]

_CHUNK: Final = 1000
_MAX_TEXT: Final = 255
_NOT_FOUND: Final = "Resource not found"

# scim id -> (tiqora_scim_user.id, users.id)
_Members = dict[str, tuple[int, int]]


@dataclass(frozen=True, slots=True)
class ScimGroupResult:
    resource: dict[str, Any]
    operation: ScimOperation
    affected_user_ids: frozenset[int]  # agents whose scim:groups changed
    ignored_members: int


def _not_found() -> ScimError:
    return ScimError(404, None, _NOT_FOUND)


def _invalid(detail: str) -> ScimError:
    return ScimError(400, "invalidValue", detail)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_agent(tenant: ScimTenant) -> None:
    if tenant.user_kind != "agent":
        raise ScimError(404, None, "Groups are not available for customer provisioning")


def _chunked[T](items: Sequence[T]) -> list[Sequence[T]]:
    return [items[i : i + _CHUNK] for i in range(0, len(items), _CHUNK)]


# --------------------------------------------------------------------------- validation


def _text(value: Any, what: str) -> str:
    """A cleaned, non-empty string of at most 255 characters that encodes as UTF-8."""
    if not isinstance(value, str):
        raise _invalid(f"{what} must be a string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid(f"{what} contains invalid characters") from None
    cleaned = _clean_text(value)
    if not cleaned:
        raise _invalid(f"{what} is required")
    if len(cleaned) > _MAX_TEXT:
        raise _invalid(f"{what} is too long")
    return cleaned


def _display_name(value: Any) -> str:
    return _text(value, "displayName")


def _external_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _text(value, "externalId")


def _doc_member_ids(doc: Mapping[str, Any], max_members: int) -> list[str]:
    raw = doc.get("members")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _invalid("members must be a list")
    if len(raw) > max_members:
        raise _invalid("Too many members in one request")
    ids: list[str] = []
    for item in raw:
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(value, str) or not value:
            raise _invalid("Each member needs a value")
        ids.append(value)
    return ids


# --------------------------------------------------------------------------- loading


def _tenant_groups(tenant: ScimTenant) -> Select[tuple[TiqoraScimGroup]]:
    return select(TiqoraScimGroup).where(TiqoraScimGroup.provider_id == tenant.provider.id)


async def _get_row(
    session: AsyncSession, tenant: ScimTenant, scim_id: str, *, lock: bool
) -> TiqoraScimGroup:
    _require_agent(tenant)
    if not scim_id or len(scim_id) > 36:
        raise _not_found()
    stmt = _tenant_groups(tenant).where(TiqoraScimGroup.scim_id == scim_id)
    if lock:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise _not_found()
    return row


async def _current_members(session: AsyncSession, group_id: int) -> _Members:
    """Stored members of a group, read with locks (a current read, never the tx snapshot).

    InnoDB serves plain SELECTs from the snapshot of the first consistent read, which can predate
    a push that committed while this transaction waited for the group lock; locking reads see the
    latest committed rows. The user rows are read with a shared lock for the same reason.
    """
    member_ids = [
        int(r)
        for r in (
            await session.execute(
                select(TiqoraScimGroupMember.member_id)
                .where(TiqoraScimGroupMember.group_id == group_id)
                .with_for_update()
            )
        ).scalars()
    ]
    found: _Members = {}
    for chunk in _chunked(member_ids):
        rows = (
            await session.execute(
                select(TiqoraScimUser.scim_id, TiqoraScimUser.id, TiqoraScimUser.user_id)
                .where(TiqoraScimUser.id.in_(list(chunk)))
                .with_for_update(read=True)
            )
        ).all()
        found.update({str(sid): (int(rid), int(uid)) for sid, rid, uid in rows})
    return found


async def _resolve(session: AsyncSession, tenant: ScimTenant, scim_ids: Sequence[str]) -> _Members:
    """Live SCIM users of this tenant among ``scim_ids`` (unknown / deleted / foreign ids drop)."""
    wanted = sorted({s for s in scim_ids if 0 < len(s) <= 36})
    found: _Members = {}
    for chunk in _chunked(wanted):
        rows = (
            await session.execute(
                select(TiqoraScimUser.scim_id, TiqoraScimUser.id, TiqoraScimUser.user_id).where(
                    TiqoraScimUser.provider_id == tenant.provider.id,
                    TiqoraScimUser.user_kind == "agent",
                    TiqoraScimUser.deleted.is_(False),
                    TiqoraScimUser.scim_id.in_(list(chunk)),
                )
            )
        ).all()
        found.update({str(sid): (int(rid), int(uid)) for sid, rid, uid in rows})
    return found


async def _render(
    session: AsyncSession,
    groups: Sequence[TiqoraScimGroup],
    base: str,
    *,
    include_members: bool,
) -> list[dict[str, Any]]:
    members: dict[int, list[dict[str, str]]] = {}
    if include_members:
        ids = [g.id for g in groups]
        for chunk in _chunked(ids):
            rows = (
                await session.execute(
                    select(
                        TiqoraScimGroupMember.group_id,
                        TiqoraScimUser.scim_id,
                        TiqoraScimUser.user_name,
                    )
                    .join(TiqoraScimUser, TiqoraScimUser.id == TiqoraScimGroupMember.member_id)
                    .where(
                        TiqoraScimGroupMember.group_id.in_(list(chunk)),
                        TiqoraScimUser.deleted.is_(False),
                    )
                    .order_by(TiqoraScimGroupMember.group_id, TiqoraScimUser.id)
                )
            ).all()
            for gid, sid, name in rows:
                members.setdefault(int(gid), []).append(
                    {"value": sid, "display": name, "$ref": f"{base}/Users/{sid}"}
                )
    out: list[dict[str, Any]] = []
    for g in groups:
        location = f"{base}/Groups/{g.scim_id}"
        doc: dict[str, Any] = {
            "schemas": [SCHEMA_GROUP],
            "id": g.scim_id,
            "displayName": g.display_name,
            "meta": meta("Group", location, g.created, g.changed, g.version),
        }
        if g.external_id:
            doc["externalId"] = g.external_id
        if include_members:
            doc["members"] = members.get(g.id, [])
        out.append(doc)
    return out


# --------------------------------------------------------------------------- writes


async def _write_members(
    session: AsyncSession, group_id: int, current: _Members, target: _Members
) -> tuple[frozenset[int], frozenset[int]]:
    """Make the stored members equal ``target``; returns (added user ids, removed user ids)."""
    add = [sid for sid in target if sid not in current]
    drop = [sid for sid in current if sid not in target]
    for chunk in _chunked([current[sid][0] for sid in drop]):
        await session.execute(
            delete(TiqoraScimGroupMember).where(
                TiqoraScimGroupMember.group_id == group_id,
                TiqoraScimGroupMember.member_id.in_(list(chunk)),
            )
        )
    for chunk in _chunked([target[sid][0] for sid in add]):
        await session.execute(
            insert(TiqoraScimGroupMember),
            [{"group_id": group_id, "member_id": rid} for rid in chunk],
        )
    return (
        frozenset(target[sid][1] for sid in add),
        frozenset(current[sid][1] for sid in drop),
    )


def _apply_names(row: TiqoraScimGroup, name: str, external_id: str | None) -> bool:
    """Set displayName / externalId; True when either changed (a rename touches every member)."""
    changed = False
    if name != row.display_name:
        fold = name.casefold()
        row.display_name = name
        row.display_name_fold = fold[:_MAX_TEXT]
        row.display_name_fold_hash = _sha(fold)
        changed = True
    if external_id != row.external_id:
        row.external_id = external_id
        row.external_id_hash = _sha(external_id) if external_id else None
        changed = True
    return changed


async def _enqueue(session: AsyncSession, tenant: ScimTenant, user_ids: frozenset[int]) -> None:
    if user_ids and rules_use_scim(tenant.provider.role_rules):
        await enqueue_role_sync(session, tenant.provider.id, user_ids)


async def _result(
    session: AsyncSession,
    tenant: ScimTenant,
    row: TiqoraScimGroup,
    operation: ScimOperation,
    affected: frozenset[int],
    ignored: int,
    base: str,
) -> ScimGroupResult:
    await _enqueue(session, tenant, affected)
    resource = (await _render(session, [row], base, include_members=True))[0]
    return ScimGroupResult(resource, operation, affected, ignored)


async def create_group(
    session: AsyncSession,
    tenant: ScimTenant,
    doc: Mapping[str, Any],
    *,
    base: str,
    max_members: int,
) -> ScimGroupResult:
    _require_agent(tenant)
    name = _display_name(doc.get("displayName"))
    external_id = _external_id(doc.get("externalId"))
    ids = _doc_member_ids(doc, max_members)
    resolved = await _resolve(session, tenant, ids)
    now = utc_now()
    fold = name.casefold()
    row = TiqoraScimGroup(
        provider_id=tenant.provider.id,
        scim_id=str(uuid.uuid4()),
        display_name=name,
        display_name_fold=fold[:_MAX_TEXT],
        display_name_fold_hash=_sha(fold),
        external_id=external_id,
        external_id_hash=_sha(external_id) if external_id else None,
        version=1,
        created=now,
        changed=now,
    )
    session.add(row)
    await session.flush()
    added, _ = await _write_members(session, row.id, {}, resolved)
    ignored = len(set(ids)) - len(resolved)
    return await _result(session, tenant, row, "create", added, ignored, base)


async def get_group(
    session: AsyncSession,
    tenant: ScimTenant,
    scim_id: str,
    *,
    base: str,
    include_members: bool,
) -> dict[str, Any]:
    row = await _get_row(session, tenant, scim_id, lock=False)
    return (await _render(session, [row], base, include_members=include_members))[0]


async def list_groups(
    session: AsyncSession,
    tenant: ScimTenant,
    flt: ScimFilter | None,
    params: ListParams,
    *,
    base: str,
    include_members: bool = True,
) -> tuple[int, list[dict[str, Any]]]:
    _require_agent(tenant)
    stmt = _tenant_groups(tenant)
    if flt is not None:
        if flt.attr == "id":
            stmt = stmt.where(TiqoraScimGroup.scim_id == flt.value[:64])
        elif flt.attr == "displayName":
            stmt = stmt.where(TiqoraScimGroup.display_name_fold_hash == _sha(flt.value.casefold()))
        elif flt.attr == "externalId":
            stmt = stmt.where(TiqoraScimGroup.external_id_hash == _sha(flt.value))
        else:
            raise ScimError(400, "invalidFilter", "Unsupported filter attribute")
    total = int(
        (
            await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
        ).scalar_one()
    )
    if params.count == 0:
        return total, []
    rows = list(
        (
            await session.execute(
                stmt.order_by(TiqoraScimGroup.id).offset(params.start_index - 1).limit(params.count)
            )
        ).scalars()
    )
    return total, await _render(session, rows, base, include_members=include_members)


async def replace_group(
    session: AsyncSession,
    tenant: ScimTenant,
    scim_id: str,
    doc: Mapping[str, Any],
    *,
    base: str,
    max_members: int,
) -> ScimGroupResult:
    row = await _get_row(session, tenant, scim_id, lock=True)
    name = _display_name(doc.get("displayName"))
    external_id = _external_id(doc.get("externalId"))
    ids = _doc_member_ids(doc, max_members)
    resolved = await _resolve(session, tenant, ids)
    current = await _current_members(session, row.id)
    added, removed = await _write_members(session, row.id, current, resolved)
    renamed = _apply_names(row, name, external_id)
    affected = added | removed
    if renamed:
        affected |= frozenset(uid for _, uid in current.values()) | added
    if renamed or added or removed:
        row.version += 1
        row.changed = utc_now()
        await session.flush()
    ignored = len(set(ids)) - len(resolved)
    return await _result(session, tenant, row, "replace", affected, ignored, base)


async def patch_group(
    session: AsyncSession,
    tenant: ScimTenant,
    scim_id: str,
    ops: list[PatchOp],
    *,
    base: str,
    max_members: int,
) -> ScimGroupResult:
    row = await _get_row(session, tenant, scim_id, lock=True)
    delta = apply_group_patch(ops, max_members=max_members)
    current = await _current_members(session, row.id)

    wanted = set(current) if delta.replace_all is None else set(delta.replace_all)
    wanted |= delta.add
    wanted -= delta.remove
    unknown = await _resolve(session, tenant, sorted(wanted - set(current)))
    target: _Members = {sid: current[sid] for sid in wanted if sid in current}
    target.update(unknown)
    ignored = len(wanted - set(current)) - len(unknown)

    name = row.display_name if delta.display_name is None else _display_name(delta.display_name)
    external_id = row.external_id if delta.external_id is False else _external_id(delta.external_id)
    added, removed = await _write_members(session, row.id, current, target)
    renamed = _apply_names(row, name, external_id)
    affected = added | removed
    if renamed:
        affected |= frozenset(uid for _, uid in target.values())
    if renamed or added or removed:
        row.version += 1
        row.changed = utc_now()
        await session.flush()
    return await _result(session, tenant, row, "patch", affected, ignored, base)


async def delete_group(session: AsyncSession, tenant: ScimTenant, scim_id: str) -> ScimGroupResult:
    row = await _get_row(session, tenant, scim_id, lock=True)
    members = await _current_members(session, row.id)
    affected = frozenset(uid for _, uid in members.values())
    await session.execute(
        delete(TiqoraScimGroupMember).where(TiqoraScimGroupMember.group_id == row.id)
    )
    await session.execute(delete(TiqoraScimGroup).where(TiqoraScimGroup.id == row.id))
    await session.flush()
    await _enqueue(session, tenant, affected)
    return ScimGroupResult({}, "delete", affected, 0)
