"""Approver lists ("Freigabegruppen", e.g. a CAB): named sets of agents.

Service functions take an ``AsyncSession``, ``flush`` and never commit. Lists are referenced by
id from template and consumer definitions (``ApproverEntry(kind="list")``); deleting a list a
template still names is refused.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import ApprovalDefinition, ApprovalError
from tiqora.approvals.models import (
    TiqoraApprovalList,
    TiqoraApprovalListMember,
    TiqoraApprovalTemplate,
)
from tiqora.jobs.service import utc_now

_CHUNK = 500


@dataclass(frozen=True, slots=True)
class ApprovalListRow:
    id: int
    name: str
    description: str | None
    valid: bool
    member_ids: tuple[int, ...]


def _chunks(ids: Sequence[int]) -> Iterable[Sequence[int]]:
    for start in range(0, len(ids), _CHUNK):
        yield ids[start : start + _CHUNK]


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 190:
        raise ApprovalError("invalid_definition", 422, "list name must be 1-190 characters")
    return name


async def _valid_agent_ids(session: AsyncSession, user_ids: Sequence[int]) -> set[int]:
    found: set[int] = set()
    stmt = text("SELECT id FROM users WHERE valid_id = 1 AND id IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    for chunk in _chunks(list(user_ids)):
        found.update(int(r[0]) for r in (await session.execute(stmt, {"ids": list(chunk)})))
    return found


async def _check_members(session: AsyncSession, member_ids: Iterable[int]) -> list[int]:
    ordered = sorted(set(member_ids))
    valid = await _valid_agent_ids(session, ordered)
    bad = [uid for uid in ordered if uid not in valid]
    if bad:
        raise ApprovalError("invalid_member", 422, ",".join(str(b) for b in bad))
    return ordered


async def _name_taken(session: AsyncSession, name: str, *, except_id: int | None = None) -> bool:
    stmt = select(TiqoraApprovalList.id).where(TiqoraApprovalList.name == name)
    if except_id is not None:
        stmt = stmt.where(TiqoraApprovalList.id != except_id)
    return (await session.execute(stmt)).first() is not None


async def list_member_ids(
    session: AsyncSession, list_ids: Sequence[int]
) -> dict[int, tuple[int, ...]]:
    """Members of several lists in one pass (every requested id is a key, possibly empty)."""
    out: dict[int, list[int]] = {i: [] for i in list_ids}
    for chunk in _chunks(list(out)):
        rows = await session.execute(
            select(TiqoraApprovalListMember.list_id, TiqoraApprovalListMember.user_id).where(
                TiqoraApprovalListMember.list_id.in_(chunk)
            )
        )
        for list_id, user_id in rows:
            out[int(list_id)].append(int(user_id))
    return {k: tuple(sorted(v)) for k, v in out.items()}


def _row(entity: TiqoraApprovalList, members: tuple[int, ...]) -> ApprovalListRow:
    return ApprovalListRow(
        id=entity.id,
        name=entity.name,
        description=entity.description,
        valid=bool(entity.valid),
        member_ids=members,
    )


async def get_list(session: AsyncSession, list_id: int) -> ApprovalListRow:
    entity = await session.get(TiqoraApprovalList, list_id)
    if entity is None:
        raise ApprovalError("list_not_found", 404)
    members = (await list_member_ids(session, [list_id]))[list_id]
    return _row(entity, members)


async def list_lists(
    session: AsyncSession, *, include_invalid: bool = False
) -> list[ApprovalListRow]:
    stmt = select(TiqoraApprovalList).order_by(TiqoraApprovalList.name, TiqoraApprovalList.id)
    if not include_invalid:
        stmt = stmt.where(TiqoraApprovalList.valid.is_(True))
    entities = list((await session.execute(stmt)).scalars())
    members = await list_member_ids(session, [e.id for e in entities])
    return [_row(e, members[e.id]) for e in entities]


async def create_list(
    session: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    member_ids: Iterable[int] = (),
    actor_id: int,
) -> ApprovalListRow:
    name = _clean_name(name)
    members = await _check_members(session, member_ids)
    if await _name_taken(session, name):
        raise ApprovalError("list_name_taken", 409)
    now = utc_now()
    entity = TiqoraApprovalList(
        name=name,
        description=description,
        valid=True,
        create_time=now,
        create_by=actor_id,
        change_time=now,
        change_by=actor_id,
    )
    session.add(entity)
    await session.flush()
    session.add_all(TiqoraApprovalListMember(list_id=entity.id, user_id=u) for u in members)
    await session.flush()
    return _row(entity, tuple(members))


async def update_list(
    session: AsyncSession,
    list_id: int,
    *,
    actor_id: int,
    name: str | None = None,
    description: str | None = None,
    member_ids: Iterable[int] | None = None,
    valid: bool | None = None,
) -> ApprovalListRow:
    """Changes the given fields; ``member_ids`` replaces the whole member set."""
    entity = await session.get(TiqoraApprovalList, list_id)
    if entity is None:
        raise ApprovalError("list_not_found", 404)
    if name is not None:
        name = _clean_name(name)
        if name != entity.name and await _name_taken(session, name, except_id=list_id):
            raise ApprovalError("list_name_taken", 409)
        entity.name = name
    if description is not None:
        entity.description = description
    if valid is not None:
        entity.valid = valid
    if member_ids is not None:
        members = await _check_members(session, member_ids)
        await session.execute(
            delete(TiqoraApprovalListMember).where(TiqoraApprovalListMember.list_id == list_id)
        )
        session.add_all(TiqoraApprovalListMember(list_id=list_id, user_id=u) for u in members)
    entity.change_time = utc_now()
    entity.change_by = actor_id
    await session.flush()
    current = (await list_member_ids(session, [list_id]))[list_id]
    return _row(entity, current)


def definition_list_refs(definition: ApprovalDefinition) -> set[int]:
    """Ids of the approver lists a definition names (approvers and escalation)."""
    refs: set[int] = set()
    for step in definition.steps:
        for entry in (*step.approvers, *step.escalation):
            if entry.kind == "list" and entry.ref.isascii() and entry.ref.isdigit():
                refs.add(int(entry.ref))
    return refs


async def templates_using_list(session: AsyncSession, list_id: int) -> list[str]:
    """Keys of the templates whose definition names the list (few templates: scanned in Python)."""
    keys: list[str] = []
    rows = await session.execute(
        select(TiqoraApprovalTemplate.template_key, TiqoraApprovalTemplate.definition)
    )
    for key, raw in rows:
        try:
            definition = ApprovalDefinition.model_validate(json.loads(raw))
        except ValueError:
            continue  # unreadable definitions cannot reference anything
        if list_id in definition_list_refs(definition):
            keys.append(key)
    return sorted(keys)


async def delete_list(session: AsyncSession, list_id: int) -> None:
    if await session.get(TiqoraApprovalList, list_id) is None:
        raise ApprovalError("list_not_found", 404)
    users = await templates_using_list(session, list_id)
    if users:
        raise ApprovalError("list_in_use", 409, ",".join(users))
    await session.execute(
        delete(TiqoraApprovalListMember).where(TiqoraApprovalListMember.list_id == list_id)
    )
    await session.execute(delete(TiqoraApprovalList).where(TiqoraApprovalList.id == list_id))
    session.expire_all()
    await session.flush()
