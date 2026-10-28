"""Approver resolution (A6): approver entries -> a de-duplicated principal snapshot.

Unknown or invalid refs never raise (a deleted group must not break a running approval): a
principal that cannot approve lands in ``Resolution.excluded`` with a reason, an entry that names
nothing at all (missing group/role/list, unknown resolver) in ``Resolution.unresolved``.

Znuny tables and ``tiqora_approval_list*`` are never joined: keys are loaded from one side and
the other is queried with an ``IN`` list (chunks <= 500).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import (
    APPROVER_RESOLVERS,
    ApproverEntry,
    Principal,
    ResolveContext,
)
from tiqora.approvals.lists import list_member_ids
from tiqora.approvals.models import TiqoraApprovalList
from tiqora.db.legacy.customer import CustomerUser, CustomerUserCustomer
from tiqora.db.legacy.ticket import Ticket
from tiqora.db.legacy.user import PermissionGroups, Roles, RoleUser, Users
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry
from tiqora.permissions.engine import PermissionEngine

_CHUNK = 500
# Znuny's root@localhost: owner/responsible fallback, never a person who can approve.
_SYSTEM_USER_ID = 1


@dataclass(frozen=True, slots=True)
class ResolvedApprover:
    principal: Principal
    source_kind: str  # user|group|role|list|customer_user|resolver|escalation
    source_ref: str | None
    source_label: str | None


@dataclass(frozen=True, slots=True)
class Resolution:
    approvers: list[ResolvedApprover]  # de-duplicated by principal, first source wins
    # (principal, reason): self|invalid|system_user|outside_company|customers_not_allowed|unknown
    excluded: list[tuple[Principal, str]]
    # entries that name nothing (missing group/role/list, unknown resolver); reason "unknown"
    unresolved: list[tuple[ApproverEntry, str]] = field(default_factory=list)


def _chunks[T](items: Sequence[T]) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), _CHUNK):
        yield items[start : start + _CHUNK]


def _int_ref(ref: str) -> int | None:
    if ref.isascii() and ref.isdigit():
        return int(ref)
    return None


async def ticket_customer_id(session: AsyncSession, ticket_id: int) -> str | None:
    """The ticket's company (``ticket.customer_id``); None for a missing ticket or no company."""
    row = (await session.execute(select(Ticket.customer_id).where(Ticket.id == ticket_id))).first()
    return (row[0] or None) if row is not None else None


async def customer_rows(
    session: AsyncSession, logins: Sequence[str]
) -> dict[str, tuple[str, int, str]]:
    """``{lower(login): (login as stored, valid_id, customer_id)}`` for existing customer users.

    Matched through ``lower()`` so MariaDB (case-insensitive collation) and PostgreSQL agree; an
    exact-case match wins over a case variant.
    """
    wanted = sorted({login.lower() for login in logins})
    out: dict[str, tuple[str, int, str]] = {}
    for chunk in _chunks(wanted):
        rows = await session.execute(
            select(CustomerUser.login, CustomerUser.valid_id, CustomerUser.customer_id).where(
                func.lower(CustomerUser.login).in_(chunk)
            )
        )
        for login, valid_id, customer_id in rows:
            key = str(login).lower()
            if key not in out or str(login) in logins:
                out[key] = (str(login), int(valid_id), str(customer_id))
    return out


async def _companies_of(
    session: AsyncSession, rows: dict[str, tuple[str, int, str]], company: str
) -> dict[str, bool]:
    """``{stored login: True}`` for every customer whose own company or mapping is *company*."""
    allowed = {login: cid == company for login, _, cid in rows.values()}
    canonical = {key: login for key, (login, _, _) in rows.items()}
    for chunk in _chunks(sorted(canonical)):
        extra = await session.execute(
            select(CustomerUserCustomer.user_id).where(
                func.lower(CustomerUserCustomer.user_id).in_(chunk),
                CustomerUserCustomer.customer_id == company,
            )
        )
        for (user_id,) in extra:
            allowed[canonical[str(user_id).lower()]] = True
    return allowed


async def customer_in_ticket_company(session: AsyncSession, login: str, ticket_id: int) -> bool:
    """Whether customer user *login* (any letter case) belongs to the ticket's company.

    True when the login's own company or a ``customer_user_customer`` mapping equals
    ``ticket.customer_id``; a ticket without a company or an unknown login admits nobody.
    """
    company = await ticket_customer_id(session, ticket_id)
    if company is None:
        return False
    rows = await customer_rows(session, [login])
    if login.lower() not in rows:
        return False
    return (await _companies_of(session, rows, company))[rows[login.lower()][0]]


async def customer_tickets_in_company(
    session: AsyncSession, login: str, ticket_ids: Sequence[int]
) -> set[int]:
    """The subset of *ticket_ids* whose company customer user *login* belongs to.

    One lookup of the customer's companies (own plus ``customer_user_customer`` mappings), then
    ``ticket.customer_id`` for the tickets in IN-list chunks. Same rule as
    :func:`customer_in_ticket_company`; tickets without a company are never included.
    """
    rows = await customer_rows(session, [login])
    found = rows.get(login.lower())
    if found is None or not ticket_ids:
        return set()
    companies = {found[2]}
    mapped = await session.execute(
        select(CustomerUserCustomer.customer_id).where(
            func.lower(CustomerUserCustomer.user_id) == login.lower()
        )
    )
    companies.update(str(c) for (c,) in mapped)
    allowed: set[int] = set()
    for chunk in _chunks(sorted(set(ticket_ids))):
        got = await session.execute(
            select(Ticket.id, Ticket.customer_id).where(Ticket.id.in_(chunk))
        )
        allowed.update(int(i) for i, company in got if company and str(company) in companies)
    return allowed


async def canonical_principal(session: AsyncSession, principal: Principal) -> Principal:
    """A customer principal with the login spelled as stored in ``customer_user``.

    Unknown logins and agents come back unchanged. Used wherever principal keys are compared
    (requester self-exclusion), because ``customer:Kunde1`` and ``customer:kunde1`` are one person.
    """
    if principal.login is None:
        return principal
    rows = await customer_rows(session, [principal.login])
    found = rows.get(principal.login.lower())
    return Principal.customer(found[0]) if found is not None else principal


@dataclass(frozen=True, slots=True)
class _Candidate:
    principal: Principal
    source_kind: str
    source_ref: str | None
    source_label: str | None


async def _group_names(session: AsyncSession, group_ids: set[int]) -> dict[int, str]:
    """Names of the valid groups among *group_ids*."""
    out: dict[int, str] = {}
    for chunk in _chunks(sorted(group_ids)):
        rows = await session.execute(
            select(PermissionGroups.id, PermissionGroups.name).where(
                PermissionGroups.id.in_(chunk), PermissionGroups.valid_id == 1
            )
        )
        out.update({int(i): n for i, n in rows})
    return out


async def _role_members(
    session: AsyncSession, role_ids: set[int]
) -> dict[int, tuple[str, set[int]]]:
    """``{role_id: (name, member user ids)}`` of the valid roles (members not yet validated)."""
    out: dict[int, tuple[str, set[int]]] = {}
    for chunk in _chunks(sorted(role_ids)):
        rows = await session.execute(
            select(Roles.id, Roles.name).where(Roles.id.in_(chunk), Roles.valid_id == 1)
        )
        for rid, name in rows:
            out[int(rid)] = (name, set())
        members = await session.execute(
            select(RoleUser.role_id, RoleUser.user_id).where(RoleUser.role_id.in_(chunk))
        )
        for rid, uid in members:
            if int(rid) in out:
                out[int(rid)][1].add(int(uid))
    return out


async def _list_names(session: AsyncSession, list_ids: set[int]) -> dict[int, str]:
    """Names of the valid approver lists among *list_ids*."""
    out: dict[int, str] = {}
    for chunk in _chunks(sorted(list_ids)):
        rows = await session.execute(
            select(TiqoraApprovalList.id, TiqoraApprovalList.name).where(
                TiqoraApprovalList.id.in_(chunk), TiqoraApprovalList.valid.is_(True)
            )
        )
        out.update({int(i): n for i, n in rows})
    return out


async def _collect(
    session: AsyncSession,
    entries: Sequence[ApproverEntry],
    *,
    ctx: ResolveContext,
    source_kind_override: str | None,
    registry: Registry,
) -> tuple[list[_Candidate], list[tuple[ApproverEntry, str]]]:
    refs: dict[str, set[int]] = {"group": set(), "role": set(), "list": set()}
    for entry in entries:
        number = _int_ref(entry.ref)
        if entry.kind in refs and number is not None:
            refs[entry.kind].add(number)
    groups = await _group_names(session, refs["group"])
    roles = await _role_members(session, refs["role"])
    lists = await _list_names(session, refs["list"])
    list_members = await list_member_ids(session, sorted(lists)) if lists else {}
    engine = PermissionEngine(session)

    candidates: list[_Candidate] = []
    unresolved: list[tuple[ApproverEntry, str]] = []

    def add(principal: Principal, kind: str, entry: ApproverEntry, label: str | None) -> None:
        candidates.append(_Candidate(principal, source_kind_override or kind, entry.ref, label))

    for entry in entries:
        number = _int_ref(entry.ref)
        if entry.kind == "user":
            if number is None or number < 1:
                unresolved.append((entry, "unknown"))
            else:
                add(Principal.agent(number), "user", entry, None)
        elif entry.kind == "group":
            if number is None or number not in groups:
                unresolved.append((entry, "unknown"))
                continue
            for uid in sorted(await engine.users_for_group_permission(number, "rw")):
                add(Principal.agent(uid), "group", entry, groups[number])
        elif entry.kind == "role":
            if number is None or number not in roles:
                unresolved.append((entry, "unknown"))
                continue
            name, member_ids = roles[number]
            for uid in sorted(member_ids):
                add(Principal.agent(uid), "role", entry, name)
        elif entry.kind == "list":
            if number is None or number not in lists:
                unresolved.append((entry, "unknown"))
                continue
            for uid in list_members.get(number, ()):
                add(Principal.agent(uid), "list", entry, lists[number])
        elif entry.kind == "customer_user":
            add(Principal.customer(entry.ref), "customer_user", entry, None)
        else:  # resolver
            resolver = registry.get(APPROVER_RESOLVERS, entry.ref)
            if resolver is None:
                unresolved.append((entry, "unknown"))
                continue
            # No catch: a failing resolver (DB error, plugin bug) must fail the request instead of
            # emptying the step, which on_no_approver="approve" would turn into an approval.
            principals = await resolver.resolve(session, ctx)
            for principal in principals:
                if not isinstance(principal, Principal):
                    raise TypeError(
                        f"approver resolver {entry.ref} returned {type(principal).__name__},"
                        " expected Principal"
                    )
                add(principal, "resolver", entry, None)
    return candidates, unresolved


async def resolve_entries(
    session: AsyncSession,
    entries: Sequence[ApproverEntry],
    *,
    ctx: ResolveContext,
    excluded_principals: frozenset[str],
    allow_customers: bool,
    source_kind_override: str | None = None,
    registry: Registry | None = None,
) -> Resolution:
    """Resolves *entries* in order; the first source of a principal wins."""
    candidates, unresolved = await _collect(
        session,
        entries,
        ctx=ctx,
        source_kind_override=source_kind_override,
        registry=registry or get_registry(),
    )
    agent_ids = sorted({c.principal.user_id for c in candidates if c.principal.user_id is not None})
    agent_valid: dict[int, int] = {}
    for chunk in _chunks(agent_ids):
        rows = await session.execute(select(Users.id, Users.valid_id).where(Users.id.in_(chunk)))
        agent_valid.update({int(i): int(v) for i, v in rows})
    customer_valid: dict[str, int] = {}
    in_company: dict[str, bool] = {}
    if allow_customers:
        logins = [c.principal.login for c in candidates if c.principal.login is not None]
        crows = await customer_rows(session, logins)
        # canonical keys: the login as stored, whatever case the entry / resolver spelled
        candidates = [_canonical(c, crows) for c in candidates]
        customer_valid = {login: valid for login, valid, _ in crows.values()}
        company = await ticket_customer_id(session, ctx.ticket_id) if crows else None
        in_company = await _companies_of(session, crows, company) if company is not None else {}

    approvers: list[ResolvedApprover] = []
    excluded: list[tuple[Principal, str]] = []
    seen: set[str] = set()
    for cand in candidates:
        principal = cand.principal
        if principal.key in seen:
            continue
        seen.add(principal.key)
        reason = _exclusion(
            principal,
            excluded_principals=excluded_principals,
            allow_customers=allow_customers,
            agent_valid=agent_valid,
            customer_valid=customer_valid,
            in_company=in_company,
        )
        if reason is not None:
            excluded.append((principal, reason))
        else:
            approvers.append(
                ResolvedApprover(principal, cand.source_kind, cand.source_ref, cand.source_label)
            )
    return Resolution(approvers=approvers, excluded=excluded, unresolved=unresolved)


def _canonical(cand: _Candidate, rows: dict[str, tuple[str, int, str]]) -> _Candidate:
    login = cand.principal.login
    if login is None or login.lower() not in rows:
        return cand
    stored = rows[login.lower()][0]
    return _Candidate(
        Principal.customer(stored), cand.source_kind, cand.source_ref, cand.source_label
    )


def _exclusion(
    principal: Principal,
    *,
    excluded_principals: frozenset[str],
    allow_customers: bool,
    agent_valid: dict[int, int],
    customer_valid: dict[str, int],
    in_company: dict[str, bool],
) -> str | None:
    if principal.kind == "agent":
        uid = principal.user_id
        if uid is None:
            raise ValueError("agent principal without user_id")
        if uid == _SYSTEM_USER_ID:
            return "system_user"
        if uid not in agent_valid:
            return "unknown"
        if agent_valid[uid] != 1:
            return "invalid"
    else:
        login = principal.login
        if login is None:
            raise ValueError("customer principal without login")
        if not allow_customers:
            return "customers_not_allowed"
        if login not in customer_valid:
            return "unknown"
        if customer_valid[login] != 1:
            return "invalid"
    if principal.key in excluded_principals:
        return "self"
    if principal.kind == "customer" and not in_company.get(principal.login or "", False):
        return "outside_company"
    return None
