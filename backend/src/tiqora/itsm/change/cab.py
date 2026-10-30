"""CAB boards, their members, meetings and agendas (design I12).

A CAB is a *board*: name, rule, quorum, emergency flag; meetings and the agenda hang off it. Its
members speak the approval engine's vocabulary only (ruling CX6): ``member_kind`` is an
``ApproverEntry.kind`` (``user|group|role|list|customer_user|resolver``), ``member_ref`` the id as
text (user, group, role, approval list), the customer login or the resolver key, never a name. The
CAB is no second membership model: only the engine resolves the entries into voters
(Freigabegruppen are referenced as kind ``list``). :func:`resolved_members` is display only; for
groups it counts with ``PermissionEngine.users_for_group_permission(group_id, "rw")``, the engine's
own rule, so the counts agree.

Member validation (rulings N4, CX21): ``ApproverEntry`` shapes, ``rules.validate_definition`` on
the definition the CAB would request, the existence of user / group / role ids in the Znuny tables
(``IN`` lists) and the engine's own reference check ``templates.validate_definition_refs`` for the
subject type ``itsm.change``: an unknown resolver or Freigabegruppe is refused, a customer login
the customer table does not know is only flagged ``missing`` (customer backends may live outside
it). Customer users and resolvers that yield customers are refused (422
``cab_customer_members_disabled``) while the engine's setting
``approvals.customer_approvers_enabled`` is off: the request would fail at the transition.
Freigabegruppen are shown with their name and size (``lists.list_lists``).

Meetings are organisational: agenda, minutes, status. Votes are not stored here; the meeting page
shows each agenda change's approval snapshot. Reading is open to every agent, writing is the change
manager's. An agenda entry of a change the caller cannot read is answered as ``hidden`` with its
``position`` only; such entries are kept when somebody else replaces the agenda.

Services never commit; the route does. Meetings and agenda times are naive UTC.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from sqlalchemy import and_, delete, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import lists as approval_lists
from tiqora.approvals.contract import (
    APPROVER_RESOLVERS,
    ApprovalDefinition,
    ApprovalError,
    ApproverEntry,
)
from tiqora.approvals.rules import validate_definition
from tiqora.approvals.settings import load_approval_settings
from tiqora.approvals.templates import validate_definition_refs
from tiqora.calendar.recurrence import to_utc_naive
from tiqora.config import Settings, get_settings
from tiqora.db.legacy.user import PermissionGroups, Roles, RoleUser, Users
from tiqora.domain.ticket_access import readable_tickets
from tiqora.extensions.core import get_registry
from tiqora.itsm.change.approval_port import SUBJECT_TYPE, ApprovalSnapshot, get_approval_port
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.gate import is_change_manager
from tiqora.itsm.change.models import (
    TiqoraItsmCab,
    TiqoraItsmCabAgendaItem,
    TiqoraItsmCabMeeting,
    TiqoraItsmCabMember,
    TiqoraItsmChange,
)
from tiqora.itsm.change.read import ChangeListItem, items_for_changes
from tiqora.itsm.change.service import MAX_TEXT
from tiqora.itsm.change.settings import load_change_settings
from tiqora.itsm.change.transitions import effective_cab_id
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine

#: ``StepSpec.approvers`` cap of the engine
MAX_MEMBERS: Final = 50
MAX_AGENDA: Final = 100
#: phases a change may be on an agenda in
AGENDA_PHASES: Final = ("submitted", "assessment", "approval", "scheduled")
#: most changes one suggestion list looks at
MAX_SUGGESTIONS: Final = 500

Rule = Literal["any", "all", "majority", "quorum"]
MemberKind = Literal["user", "group", "role", "list", "customer_user", "resolver"]
MeetingStatus = Literal["planned", "held", "cancelled"]

_ID = re.compile(r"^[1-9][0-9]{0,9}$")
_ID_KINDS: Final = ("user", "group", "role", "list")
CUSTOMERS_DISABLED_TEXT: Final = (
    "Kundenbenutzer als CAB-Mitglieder erst nach Aktivieren von Kunden als Freigeber in den"
    " Freigabe-Einstellungen."
)
_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


# --- boards ----------------------------------------------------------------------------------


class CabIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: _Name
    description: str | None = Field(default=None, max_length=5000)
    rule: Rule = "majority"
    quorum: int | None = Field(default=None, ge=1, le=100)
    is_emergency: bool = False
    valid: bool = True

    @model_validator(mode="after")
    def _quorum(self) -> Self:
        if self.rule == "quorum" and self.quorum is None:
            raise ValueError("Für die Regel „Quorum“ muss eine Mindestanzahl angegeben werden.")
        if self.rule != "quorum" and self.quorum is not None:
            raise ValueError("Eine Mindestanzahl ist nur bei der Regel „Quorum“ erlaubt.")
        return self


class CabMemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_kind: MemberKind
    member_ref: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=190)
    ]


class ResolvedMember(BaseModel):
    """One CAB entry for display. ``count`` is the number of voters a group, role or
    Freigabegruppe stands for (``None`` for single entries and for resolvers, which only the
    engine expands at request time)."""

    member_kind: MemberKind
    member_ref: str
    label: str
    count: int | None = None
    #: the user / group / role / Freigabegruppe no longer exists or is invalid, or the customer
    #: table does not know the login
    missing: bool = False


class CabView(BaseModel):
    id: int
    name: str
    description: str | None
    rule: Rule
    quorum: int | None
    is_emergency: bool
    valid: bool
    members: list[ResolvedMember]
    created_at: datetime
    created_by: int
    updated_at: datetime
    updated_by: int
    #: the engine accepts customer users as approvers (``approvals.customer_approvers_enabled``)
    customer_members_allowed: bool = False


#: ``cab_invalid`` names why in ``reason`` (stable; the engine's English text stays in
#: ``message``): the quorum is larger than the members when only users / customer users are
#: members (the engine's countable case), another rule of the approval engine, or an entry the
#: engine's model refuses
CabInvalidReason = Literal["quorum_exceeds_members", "invalid_definition", "invalid_member"]


def _quorum_exceeds_members(
    rule: str, quorum: int | None, members: Sequence[tuple[str, str]]
) -> bool:
    """The engine's countable quorum check (``approvals.rules.validate_definition``)."""
    if rule != "quorum" or quorum is None or not members:
        return False
    if not all(kind in ("user", "customer_user") for kind, _ in members):
        return False
    return quorum > len(set(members))


def _rule_error(exc: ApprovalError, reason: CabInvalidReason) -> ChangeError:
    return ChangeError("cab_invalid", 422, reason=reason, message=exc.detail or exc.code)


def _members_definition(
    name: str, rule: str, quorum: int | None, members: Sequence[tuple[str, str]]
) -> ApprovalDefinition:
    return ApprovalDefinition.model_validate(
        {
            "steps": [
                {
                    "name": name,
                    "rule": rule,
                    "quorum": quorum if rule == "quorum" else None,
                    "approvers": [
                        ApproverEntry.model_validate({"kind": k, "ref": r}) for k, r in members
                    ],
                }
            ]
        }
    )


def check_definition(
    name: str, rule: str, quorum: int | None, members: Sequence[tuple[str, str]]
) -> None:
    """The engine's own rules on the step this CAB would request (pure). A CAB without members
    passes: it can be saved empty and filled later; the change refuses to use it while it is."""
    if not members:
        return
    try:
        validate_definition(_members_definition(name, rule, quorum, members))
    except ValidationError as exc:
        raise ChangeError(
            "cab_invalid", 422, reason="invalid_member", message=str(exc.errors()[0]["msg"])
        ) from exc
    except ApprovalError as exc:
        quorum_case = _quorum_exceeds_members(rule, quorum, members)
        raise _rule_error(
            exc, "quorum_exceeds_members" if quorum_case else "invalid_definition"
        ) from exc


async def _stored_members(session: AsyncSession, cab_id: int) -> list[tuple[str, str]]:
    rows = await session.execute(
        select(TiqoraItsmCabMember.member_kind, TiqoraItsmCabMember.member_ref)
        .where(TiqoraItsmCabMember.cab_id == cab_id)
        .order_by(TiqoraItsmCabMember.id)
    )
    return [(str(k), str(r)) for k, r in rows]


async def _existing_ids(session: AsyncSession, model: type, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    column = model.id  # type: ignore[attr-defined]
    stmt = select(column).where(column.in_(sorted(ids)), model.valid_id == 1)  # type: ignore[attr-defined]
    return {int(i) for i in (await session.execute(stmt)).scalars()}


async def _check_members(session: AsyncSession, members: Sequence[CabMemberIn]) -> None:
    """Shape of every entry and existence of the users, groups and roles."""
    if len(members) > MAX_MEMBERS:
        raise ChangeError("cab_too_many_members", 422, max=MAX_MEMBERS)
    seen: set[tuple[str, str]] = set()
    wanted: dict[str, set[int]] = {"user": set(), "group": set(), "role": set()}
    for m in members:
        key = (m.member_kind, m.member_ref)
        if key in seen:
            raise ChangeError("cab_member_duplicate", 422, member_kind=m.member_kind)
        seen.add(key)
        if m.member_kind in _ID_KINDS and not _ID.match(m.member_ref):
            raise ChangeError("cab_member_invalid", 422, member_kind=m.member_kind)
        if m.member_kind in wanted:
            wanted[m.member_kind].add(int(m.member_ref))
    for kind, model in (("user", Users), ("group", PermissionGroups), ("role", Roles)):
        if wanted[kind] - await _existing_ids(session, model, wanted[kind]):
            raise ChangeError(f"{kind}_not_found", 422)


async def customer_members_allowed(session: AsyncSession, settings: Settings | None = None) -> bool:
    """The engine's setting: customer users may approve at all (CX21)."""
    loaded = await load_approval_settings(session, settings or get_settings())
    return loaded.customer_approvers_enabled


def _yields_customers(member: CabMemberIn) -> bool:
    """A customer user, or a resolver that resolves to customers (e.g. ``ticket.customer_user``)."""
    if member.member_kind == "customer_user":
        return True
    if member.member_kind != "resolver":
        return False
    resolver = get_registry().get(APPROVER_RESOLVERS, member.member_ref)
    return resolver is not None and resolver.yields_customers


async def _check_engine_refs(
    session: AsyncSession, members: Sequence[CabMemberIn], settings: Settings | None
) -> None:
    """CX21 and the engine's reference check (N4); call after :func:`_check_members`."""
    if not members:
        return
    if any(_yields_customers(m) for m in members) and not (
        await customer_members_allowed(session, settings)
    ):
        raise ChangeError("cab_customer_members_disabled", 422, message=CUSTOMERS_DISABLED_TEXT)
    pairs = [(m.member_kind, m.member_ref) for m in members]
    check = await validate_definition_refs(
        session, _members_definition("CAB", "any", None, pairs), subject_types=[SUBJECT_TYPE]
    )
    for error in check.errors:
        if error.startswith("unknown_resolver:"):
            raise ChangeError("resolver_not_found", 422)
        raise ChangeError("cab_member_invalid", 422, message=error)
    if any(w.startswith("missing_list:") for w in check.warnings):
        raise ChangeError("list_not_found", 422)


async def _engine_warnings(session: AsyncSession, members: Sequence[tuple[str, str]]) -> set[str]:
    """``missing_*`` warnings of the engine's reference check for list and customer entries."""
    wanted = [(k, r) for k, r in members if k in ("list", "customer_user")]
    if not wanted:
        return set()
    try:
        definition = _members_definition("CAB", "any", None, wanted)
    except ValidationError:  # a row the editor would refuse: shown as it is
        return set()
    return set((await validate_definition_refs(session, definition)).warnings)


async def _display(
    session: AsyncSession, members: Sequence[tuple[str, str]]
) -> list[ResolvedMember]:
    warnings = await _engine_warnings(session, members)
    lists = (
        {r.id: r for r in await approval_lists.list_lists(session, include_invalid=True)}
        if any(k == "list" for k, _ in members)
        else {}
    )
    users: dict[int, str] = {}
    ids: dict[str, set[int]] = {"user": set(), "group": set(), "role": set()}
    for kind, ref in members:
        if kind in ids and _ID.match(ref):
            ids[kind].add(int(ref))
    if ids["user"]:
        rows = await session.execute(
            select(Users.id, Users.first_name, Users.last_name, Users.login).where(
                Users.id.in_(sorted(ids["user"])), Users.valid_id == 1
            )
        )
        users = {int(i): (f"{f} {ln}".strip() or str(lg)) for i, f, ln, lg in rows}
    groups: dict[int, str] = {}
    if ids["group"]:
        rows = await session.execute(
            select(PermissionGroups.id, PermissionGroups.name).where(
                PermissionGroups.id.in_(sorted(ids["group"])), PermissionGroups.valid_id == 1
            )
        )
        groups = {int(i): str(n) for i, n in rows}
    roles: dict[int, str] = {}
    role_counts: dict[int, int] = {}
    if ids["role"]:
        rows = await session.execute(
            select(Roles.id, Roles.name).where(
                Roles.id.in_(sorted(ids["role"])), Roles.valid_id == 1
            )
        )
        roles = {int(i): str(n) for i, n in rows}
        counted = await session.execute(
            select(RoleUser.role_id, func.count(RoleUser.user_id))
            .join(Users, Users.id == RoleUser.user_id)
            .where(RoleUser.role_id.in_(sorted(roles)), Users.valid_id == 1)
            .group_by(RoleUser.role_id)
        )
        role_counts = {int(r): int(n) for r, n in counted}
    engine = PermissionEngine(session)
    out: list[ResolvedMember] = []
    for kind, ref in members:
        k: MemberKind = kind  # type: ignore[assignment]
        num = int(ref) if _ID.match(ref) else 0
        if kind == "user":
            out.append(
                ResolvedMember(
                    member_kind=k,
                    member_ref=ref,
                    label=users.get(num, f"#{ref}"),
                    missing=num not in users,
                )
            )
        elif kind == "group":
            count = len(await engine.users_for_group_permission(num, "rw")) if num in groups else 0
            out.append(
                ResolvedMember(
                    member_kind=k,
                    member_ref=ref,
                    label=groups.get(num, f"#{ref}"),
                    count=count,
                    missing=num not in groups,
                )
            )
        elif kind == "role":
            out.append(
                ResolvedMember(
                    member_kind=k,
                    member_ref=ref,
                    label=roles.get(num, f"#{ref}"),
                    count=role_counts.get(num, 0) if num in roles else 0,
                    missing=num not in roles,
                )
            )
        elif kind == "list":
            found = lists.get(num)
            gone = found is None or not found.valid or f"missing_list:{ref}" in warnings
            out.append(
                ResolvedMember(
                    member_kind=k,
                    member_ref=ref,
                    label=found.name if found is not None else f"Freigabegruppe #{ref}",
                    count=0 if found is None or gone else len(found.member_ids),
                    missing=gone,
                )
            )
        elif kind == "customer_user":
            out.append(
                ResolvedMember(
                    member_kind=k,
                    member_ref=ref,
                    label=ref,
                    missing=f"missing_customer:{ref}" in warnings,
                )
            )
        else:  # resolver: its key (the engine expands it at request time)
            out.append(ResolvedMember(member_kind=k, member_ref=ref, label=ref))
    return out


async def resolved_members(session: AsyncSession, cab_id: int) -> list[ResolvedMember]:
    """The CAB's entries with names and counts, in entry order (display only)."""
    return await _display(session, await _stored_members(session, cab_id))


async def _view(
    session: AsyncSession, cab: TiqoraItsmCab, *, customers: bool | None = None
) -> CabView:
    if customers is None:
        customers = await customer_members_allowed(session)
    return CabView(
        id=cab.id,
        name=cab.name,
        description=cab.description,
        rule=cab.rule,  # type: ignore[arg-type]
        quorum=cab.quorum,
        is_emergency=cab.is_emergency,
        valid=cab.valid,
        members=await resolved_members(session, cab.id),
        created_at=cab.created_at,
        created_by=cab.created_by,
        updated_at=cab.updated_at,
        updated_by=cab.updated_by,
        customer_members_allowed=customers,
    )


async def _cab(session: AsyncSession, cab_id: int, *, lock: bool = False) -> TiqoraItsmCab:
    cab = await session.get(TiqoraItsmCab, cab_id, with_for_update=lock)
    if cab is None:
        raise ChangeError("cab_not_found", 404)
    return cab


async def list_cabs(session: AsyncSession, *, include_invalid: bool = True) -> list[CabView]:
    stmt = select(TiqoraItsmCab).order_by(TiqoraItsmCab.name, TiqoraItsmCab.id)
    if not include_invalid:
        stmt = stmt.where(TiqoraItsmCab.valid.is_(True))
    customers = await customer_members_allowed(session)
    return [
        await _view(session, c, customers=customers)
        for c in (await session.execute(stmt)).scalars()
    ]


async def get_cab(session: AsyncSession, cab_id: int) -> CabView:
    return await _view(session, await _cab(session, cab_id))


async def create_cab(session: AsyncSession, *, data: CabIn, user_id: int) -> CabView:
    """Admin. A new CAB has no members yet."""
    now = utc_now()
    cab = TiqoraItsmCab(
        name=data.name,
        description=data.description,
        rule=data.rule,
        quorum=data.quorum,
        is_emergency=data.is_emergency,
        valid=data.valid,
        created_at=now,
        created_by=user_id,
        updated_at=now,
        updated_by=user_id,
    )
    try:
        async with session.begin_nested():  # a concurrent create of the same name loses here
            session.add(cab)
    except IntegrityError as exc:
        raise ChangeError("cab_name_exists", 409) from exc
    return await _view(session, cab)


async def update_cab(session: AsyncSession, *, cab_id: int, data: CabIn, user_id: int) -> CabView:
    """Admin. Replaces name, rule and flags; the members stay (and must fit the new rule)."""
    cab = await _cab(session, cab_id)
    check_definition(data.name, data.rule, data.quorum, await _stored_members(session, cab_id))
    taken = await session.execute(
        select(TiqoraItsmCab.id).where(TiqoraItsmCab.name == data.name, TiqoraItsmCab.id != cab_id)
    )
    if taken.first() is not None:  # before touching the row: a refused update leaves it clean
        raise ChangeError("cab_name_exists", 409)
    cab.name = data.name
    cab.description = data.description
    cab.rule = data.rule
    cab.quorum = data.quorum
    cab.is_emergency = data.is_emergency
    cab.valid = data.valid
    cab.updated_at = utc_now()
    cab.updated_by = user_id
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise ChangeError("cab_name_exists", 409) from exc
    return await _view(session, cab)


async def delete_cab(session: AsyncSession, *, cab_id: int) -> None:
    """Admin. Refused (409 ``cab_has_meetings``) while meetings exist: mark the CAB invalid
    instead. Changes that named it lose the reference (``ON DELETE SET NULL``)."""
    cab = await _cab(session, cab_id)
    meetings = (
        await session.execute(
            select(func.count())
            .select_from(TiqoraItsmCabMeeting)
            .where(TiqoraItsmCabMeeting.cab_id == cab_id)
        )
    ).scalar_one()
    if meetings:
        raise ChangeError("cab_has_meetings", 409, meetings=int(meetings))
    await session.delete(cab)
    await session.flush()


async def set_members(
    session: AsyncSession,
    *,
    cab_id: int,
    members: Sequence[CabMemberIn],
    user_id: int,
    settings: Settings | None = None,
) -> CabView:
    """Admin. Replaces the member list (order kept). The result must be a definition the
    engine accepts with the CAB's rule and quorum, pointing at things that exist."""
    cab = await _cab(session, cab_id, lock=True)  # concurrent replaces take turns
    await _check_members(session, members)
    await _check_engine_refs(session, members, settings)
    check_definition(
        cab.name, cab.rule, cab.quorum, [(m.member_kind, m.member_ref) for m in members]
    )
    await session.execute(delete(TiqoraItsmCabMember).where(TiqoraItsmCabMember.cab_id == cab_id))
    for m in members:
        session.add(
            TiqoraItsmCabMember(cab_id=cab_id, member_kind=m.member_kind, member_ref=m.member_ref)
        )
    cab.updated_at = utc_now()
    cab.updated_by = user_id
    await session.flush()
    return await _view(session, cab)


# --- meetings and agenda ---------------------------------------------------------------------


class MeetingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cab_id: int
    title: _Name
    starts_at: datetime
    ends_at: datetime
    location: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _window(self) -> Self:
        if to_utc_naive(self.ends_at) <= to_utc_naive(self.starts_at):
            raise ValueError("Das Ende der Sitzung muss nach dem Beginn liegen.")
        return self


class MeetingUpdateIn(BaseModel):
    """Only the fields that are sent change; the CAB of a meeting stays."""

    model_config = ConfigDict(extra="forbid")

    title: _Name | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=200)
    status: MeetingStatus | None = None
    minutes: str | None = Field(default=None, max_length=MAX_TEXT)


class AgendaItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_ticket_id: int
    note: str | None = Field(default=None, max_length=5000)


class AgendaEntry(BaseModel):
    """One agenda line. For a change the caller cannot read only ``hidden`` and ``position``
    are set."""

    position: int
    hidden: bool = False
    change_ticket_id: int | None = None
    tn: str | None = None
    title: str | None = None
    change_type: str | None = None
    phase: str | None = None
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    note: str | None = None
    approval: ApprovalSnapshot | None = None


class MeetingView(BaseModel):
    id: int
    cab_id: int
    cab_name: str
    title: str
    starts_at: datetime
    ends_at: datetime
    location: str | None
    status: MeetingStatus
    minutes: str | None
    #: number of agenda lines (hidden ones included); the lines come with :func:`get_meeting`
    agenda_count: int
    agenda: list[AgendaEntry]
    created_at: datetime
    created_by: int
    updated_at: datetime
    updated_by: int


async def _require_manager(session: AsyncSession, settings: Settings, user_id: int) -> None:
    cfg = await load_change_settings(session, settings)
    if not await is_change_manager(session, user_id, cfg):
        raise ChangeError("forbidden", 403)


async def _meeting(
    session: AsyncSession, meeting_id: int, *, lock: bool = False
) -> TiqoraItsmCabMeeting:
    meeting = await session.get(TiqoraItsmCabMeeting, meeting_id, with_for_update=lock)
    if meeting is None:
        raise ChangeError("meeting_not_found", 404)
    return meeting


async def meeting_cab_id(session: AsyncSession, meeting_id: int) -> int:
    """The CAB of a meeting (404 ``meeting_not_found``); no agenda is loaded."""
    return int((await _meeting(session, meeting_id)).cab_id)


async def _cab_names(session: AsyncSession, cab_ids: set[int]) -> dict[int, str]:
    if not cab_ids:
        return {}
    rows = await session.execute(
        select(TiqoraItsmCab.id, TiqoraItsmCab.name).where(TiqoraItsmCab.id.in_(sorted(cab_ids)))
    )
    return {int(i): str(n) for i, n in rows}


async def _agenda_counts(session: AsyncSession, meeting_ids: Sequence[int]) -> dict[int, int]:
    if not meeting_ids:
        return {}
    rows = await session.execute(
        select(TiqoraItsmCabAgendaItem.meeting_id, func.count())
        .where(TiqoraItsmCabAgendaItem.meeting_id.in_(list(meeting_ids)))
        .group_by(TiqoraItsmCabAgendaItem.meeting_id)
    )
    return {int(m): int(n) for m, n in rows}


def _meeting_view(
    m: TiqoraItsmCabMeeting, cab_name: str, count: int, agenda: list[AgendaEntry]
) -> MeetingView:
    return MeetingView(
        id=m.id,
        cab_id=m.cab_id,
        cab_name=cab_name,
        title=m.title,
        starts_at=m.starts_at,
        ends_at=m.ends_at,
        location=m.location,
        status=m.status,  # type: ignore[arg-type]
        minutes=m.minutes,
        agenda_count=count,
        agenda=agenda,
        created_at=m.created_at,
        created_by=m.created_by,
        updated_at=m.updated_at,
        updated_by=m.updated_by,
    )


async def list_meetings(
    session: AsyncSession, *, start: datetime, end: datetime, user_id: int
) -> list[MeetingView]:
    """Meetings that overlap ``[start, end)``, earliest first, without their agenda lines."""
    _ = user_id  # meetings are visible to every agent; the lines are filtered in get_meeting
    lo, hi = to_utc_naive(start), to_utc_naive(end)
    rows = list(
        (
            await session.execute(
                select(TiqoraItsmCabMeeting)
                .where(TiqoraItsmCabMeeting.starts_at < hi, TiqoraItsmCabMeeting.ends_at > lo)
                .order_by(TiqoraItsmCabMeeting.starts_at, TiqoraItsmCabMeeting.id)
            )
        ).scalars()
    )
    names = await _cab_names(session, {m.cab_id for m in rows})
    counts = await _agenda_counts(session, [m.id for m in rows])
    return [_meeting_view(m, names.get(m.cab_id, ""), counts.get(m.id, 0), []) for m in rows]


async def get_meeting(session: AsyncSession, *, meeting_id: int, user_id: int) -> MeetingView:
    """The meeting with its agenda. Lines of changes the caller cannot read are ``hidden``."""
    meeting = await _meeting(session, meeting_id)
    items = list(
        (
            await session.execute(
                select(TiqoraItsmCabAgendaItem)
                .where(TiqoraItsmCabAgendaItem.meeting_id == meeting_id)
                .order_by(TiqoraItsmCabAgendaItem.position, TiqoraItsmCabAgendaItem.id)
            )
        ).scalars()
    )
    readable = await readable_tickets(session, [i.change_ticket_id for i in items], user_id=user_id)
    changes: dict[int, TiqoraItsmChange] = {}
    if readable:
        found = await session.execute(
            select(TiqoraItsmChange).where(TiqoraItsmChange.ticket_id.in_(sorted(readable)))
        )
        changes = {int(c.ticket_id): c for c in found.scalars()}
    port = get_approval_port()
    agenda: list[AgendaEntry] = []
    for item in items:
        facts = readable.get(item.change_ticket_id)
        change = changes.get(item.change_ticket_id)
        if facts is None or change is None:
            agenda.append(AgendaEntry(position=item.position, hidden=True))
            continue
        approval: ApprovalSnapshot | None = None
        if change.approval_id is not None:
            try:
                approval = await port.snapshot(session, approval_id=int(change.approval_id))
            except ChangeError:
                approval = None  # no approval engine registered: the line stays without it
        agenda.append(
            AgendaEntry(
                position=item.position,
                change_ticket_id=item.change_ticket_id,
                tn=facts.tn,
                title=facts.title,
                change_type=change.change_type,
                phase=change.phase,
                planned_start=change.planned_start,
                planned_end=change.planned_end,
                note=item.note,
                approval=approval,
            )
        )
    names = await _cab_names(session, {meeting.cab_id})
    return _meeting_view(meeting, names.get(meeting.cab_id, ""), len(items), agenda)


async def create_meeting(
    session: AsyncSession, settings: Settings, *, data: MeetingIn, user_id: int
) -> MeetingView:
    """Change manager. The CAB must exist and be valid."""
    await _require_manager(session, settings, user_id)
    cab = await session.get(TiqoraItsmCab, data.cab_id)
    if cab is None or not cab.valid:
        raise ChangeError("cab_not_found", 422)
    now = utc_now()
    meeting = TiqoraItsmCabMeeting(
        cab_id=cab.id,
        title=data.title,
        starts_at=to_utc_naive(data.starts_at),
        ends_at=to_utc_naive(data.ends_at),
        location=(data.location or "").strip() or None,
        status="planned",
        created_at=now,
        created_by=user_id,
        updated_at=now,
        updated_by=user_id,
    )
    session.add(meeting)
    await session.flush()
    return await get_meeting(session, meeting_id=meeting.id, user_id=user_id)


async def update_meeting(
    session: AsyncSession,
    settings: Settings,
    *,
    meeting_id: int,
    data: MeetingUpdateIn,
    user_id: int,
) -> MeetingView:
    """Change manager. Sent fields change (``location`` / ``minutes`` may be cleared with an
    empty string)."""
    await _require_manager(session, settings, user_id)
    meeting = await _meeting(session, meeting_id)
    sent = data.model_fields_set
    starts = to_utc_naive(data.starts_at) if data.starts_at is not None else meeting.starts_at
    ends = to_utc_naive(data.ends_at) if data.ends_at is not None else meeting.ends_at
    if ends <= starts:
        raise ChangeError("range_invalid", 422)
    if data.title is not None:
        meeting.title = data.title
    meeting.starts_at = starts
    meeting.ends_at = ends
    if "location" in sent:
        meeting.location = (data.location or "").strip() or None
    if data.status is not None:
        meeting.status = data.status
    if "minutes" in sent:
        meeting.minutes = data.minutes or None
    meeting.updated_at = utc_now()
    meeting.updated_by = user_id
    await session.flush()
    return await get_meeting(session, meeting_id=meeting_id, user_id=user_id)


async def set_agenda(
    session: AsyncSession,
    settings: Settings,
    *,
    meeting_id: int,
    items: Sequence[AgendaItemIn],
    user_id: int,
) -> MeetingView:
    """Change manager. Replaces the agenda of a planned meeting.

    Every change must be readable by the caller and, when it is new to the agenda, in one of
    :data:`AGENDA_PHASES` (422 ``change_not_found`` / ``change_not_open``; unreadable and missing
    changes answer alike). Lines the caller cannot read are kept behind the given ones.
    """
    await _require_manager(session, settings, user_id)
    meeting = await _meeting(session, meeting_id, lock=True)  # concurrent replaces take turns
    if meeting.status != "planned":
        raise ChangeError("meeting_not_planned", 409)
    if len(items) > MAX_AGENDA:
        raise ChangeError("agenda_too_long", 422, max=MAX_AGENDA)
    wanted = [i.change_ticket_id for i in items]
    if len(set(wanted)) != len(wanted):
        raise ChangeError("agenda_duplicate", 422)

    current = list(
        (
            await session.execute(
                select(TiqoraItsmCabAgendaItem)
                .where(TiqoraItsmCabAgendaItem.meeting_id == meeting_id)
                .order_by(TiqoraItsmCabAgendaItem.position, TiqoraItsmCabAgendaItem.id)
            )
        ).scalars()
    )
    on_agenda = {i.change_ticket_id for i in current}
    readable = await readable_tickets(
        session, [*wanted, *(i.change_ticket_id for i in current)], user_id=user_id
    )
    phases: dict[int, str] = {}
    if wanted:
        found = await session.execute(
            select(TiqoraItsmChange.ticket_id, TiqoraItsmChange.phase).where(
                TiqoraItsmChange.ticket_id.in_(sorted(set(wanted)))
            )
        )
        phases = {int(t): str(p) for t, p in found}
    for item in items:
        tid = item.change_ticket_id
        if tid not in readable or tid not in phases:
            raise ChangeError("change_not_found", 422, ticket_id=tid)
        if tid not in on_agenda and phases[tid] not in AGENDA_PHASES:
            raise ChangeError("change_not_open", 422, ticket_id=tid)

    kept_hidden = [
        (i.change_ticket_id, i.note) for i in current if i.change_ticket_id not in readable
    ]
    await session.execute(
        delete(TiqoraItsmCabAgendaItem).where(TiqoraItsmCabAgendaItem.meeting_id == meeting_id)
    )
    lines = [(i.change_ticket_id, (i.note or "").strip() or None) for i in items] + kept_hidden
    for position, (tid, note) in enumerate(lines, start=1):
        session.add(
            TiqoraItsmCabAgendaItem(
                meeting_id=meeting_id, change_ticket_id=tid, position=position, note=note
            )
        )
    meeting.updated_at = utc_now()
    meeting.updated_by = user_id
    await session.flush()
    return await get_meeting(session, meeting_id=meeting_id, user_id=user_id)


async def suggest_agenda(
    session: AsyncSession,
    settings: Settings,
    *,
    cab_id: int,
    user_id: int,
    meeting_id: int | None = None,
) -> list[ChangeListItem]:
    """Changes in ``approval`` that this (valid) CAB decides (its own, or by the default /
    emergency CAB setting), readable by the caller, planned start first. With ``meeting_id`` the
    changes already on that agenda are left out. The cap (:data:`MAX_SUGGESTIONS`, newest changes
    first) applies after the CAB filter, so other CABs' changes never push these out."""
    cab = await _cab(session, cab_id)
    if not cab.valid:
        raise ChangeError("cab_not_found", 422)
    cfg = await load_change_settings(session, settings)
    ch = TiqoraItsmChange
    by_setting = []
    if cfg.emergency_cab_id == cab_id:
        by_setting.append(ch.change_type == "emergency")
    if cfg.default_cab_id == cab_id:
        by_setting.append(true() if not cfg.emergency_cab_id else ch.change_type != "emergency")
    decides = ch.cab_id == cab_id
    if by_setting:
        decides = or_(decides, and_(ch.cab_id.is_(None), or_(*by_setting)))
    stmt = select(ch).where(ch.phase == "approval", decides)
    if meeting_id is not None:
        stmt = stmt.where(
            ch.ticket_id.not_in(
                select(TiqoraItsmCabAgendaItem.change_ticket_id).where(
                    TiqoraItsmCabAgendaItem.meeting_id == meeting_id
                )
            )
        )
    rows = list(
        (await session.execute(stmt.order_by(ch.ticket_id.desc()).limit(MAX_SUGGESTIONS))).scalars()
    )
    mine = [r for r in rows if effective_cab_id(r, cfg) == cab_id]
    items = await items_for_changes(session, [int(r.ticket_id) for r in mine], user_id=user_id)
    far = datetime.max
    return sorted(items, key=lambda i: (i.planned_start or far, i.ticket_id))
