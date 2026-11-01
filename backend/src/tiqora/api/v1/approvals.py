"""Approval endpoints for agents (A13): inbox, decisions, forwarding, ticket approvals.

``/api/v1/approvals`` and ``/api/v1/tickets/{id}/approvals`` (API-key area ``tickets``). The
engine in :mod:`tiqora.approvals` never commits: every handler commits and then, best-effort,
tells the affected agents' browsers (``approval_changed``) that their inbox changed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Iterable
from typing import Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, CurrentUser, DbSession, get_redis
from tiqora.api.v1._approvals_common import (
    delegation_out as _delegation_out,
)
from tiqora.api.v1._approvals_common import (
    ensure_period_open,
    require_approvals_enabled,
    require_enabled_or_admin,
)
from tiqora.api.v1._approvals_common import http_error as _http
from tiqora.api.v1._approvals_common import refuse as _refuse
from tiqora.api.v1._approvals_common import run_engine as _run
from tiqora.approvals import (
    access,
    delegation,
    detail,
    inbox,
    lists,
    notify,
    rules,
    service,
    templates,
    tokens,
)
from tiqora.approvals.availability import frozen_subject_types
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    APPROVER_RESOLVERS,
    Actor,
    ApprovalError,
    Channel,
)
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalDelegation,
    TiqoraApprovalStep,
)
from tiqora.approvals.retry import commit_with_retry
from tiqora.approvals.schemas import (
    ApprovalDetailOut,
    ApprovalInboxPage,
    ApprovalListOptionOut,
    ApprovalRequestIn,
    ApprovalsStatusOut,
    ApprovalSummaryOut,
    ApprovalTemplateOptionOut,
    DecisionIn,
    DelegationIn,
    DelegationOut,
    ForwardIn,
    InboxCountOut,
    InboxItemOut,
    LinkDecideIn,
    LinkDecisionOut,
    LinkInspectIn,
    LinkInspectOut,
    ResolverOptionOut,
)
from tiqora.approvals.settings import approvals_enabled, load_approval_settings
from tiqora.db.legacy.ticket import Ticket
from tiqora.domain.ticket_access import TicketAccessDenied, require_ticket
from tiqora.events.pubsub import get_pubsub_redis, publish_approval_changed
from tiqora.extensions.core import get_registry
from tiqora.jobs.service import utc_now
from tiqora.permissions.engine import PermissionEngine
from tiqora.security.ratelimit import ApprovalLinkRateLimiter, client_ip

#: answers while the module is switched off, unlike ``router``
status_router = APIRouter(prefix="/approvals", tags=["approvals"])
#: reads the admin pages need while the module is off: open for admins then, refused for the others
admin_readable_router = APIRouter(
    prefix="/approvals", tags=["approvals"], dependencies=[Depends(require_enabled_or_admin)]
)
router = APIRouter(
    prefix="/approvals", tags=["approvals"], dependencies=[Depends(require_approvals_enabled)]
)
ticket_router = APIRouter(
    prefix="/tickets", tags=["approvals"], dependencies=[Depends(require_approvals_enabled)]
)
#: public: the link's secret is the credential (no session, no API-key scope, see CX12)
links_router = APIRouter(prefix="/approval-links", tags=["approvals"])


async def _run_on[T](
    session: AsyncSession, approval_id: int, actor: Actor, operation: Awaitable[T]
) -> T:
    """Like :func:`_run` for a call on one request: a refusal for a request the caller may not
    see is always 404 ``approval_not_found``, so 403/409 never reveal that it exists. The
    visibility check runs after the rollback, keeping the service's request lock the first read.
    """
    try:
        return await operation
    except ApprovalError as err:
        await session.rollback()
        if err.code != "approval_not_found":
            approval = (
                await session.execute(
                    select(TiqoraApproval).where(TiqoraApproval.id == approval_id)
                )
            ).scalar_one_or_none()
            if approval is None or not await access.can_view(session, approval, actor):
                await session.rollback()
                _refuse("approval_not_found", status.HTTP_404_NOT_FOUND)
        raise _http(err) from err


async def _raise(err: ApprovalError) -> NoReturn:
    raise err


def _actor(user: CurrentUser) -> Actor:
    return Actor.agent(user.id)


def _channel(user: CurrentUser) -> Channel:
    return "api" if user.auth_method == "api_key" else "ui"


async def _publish(settings: AppSettings, user_ids: Iterable[int]) -> None:
    """After the commit: the inbox of these agents changed (best-effort, never raises)."""
    ids = set(user_ids)
    if ids:
        await publish_approval_changed(get_pubsub_redis(settings), ids)


async def _affected_users(
    session: AsyncSession, approval_id: int, *, step_id: int | None = None
) -> set[int]:
    """Agents whose inbox may have changed: the request's approvers (any row status, so the
    previous and the new ones), their active substitutes and the requesting agent.
    """
    stmt = select(TiqoraApprovalApprover.user_id).where(
        TiqoraApprovalApprover.approval_id == approval_id,
        TiqoraApprovalApprover.user_id.is_not(None),
    )
    if step_id is not None:
        stmt = stmt.where(TiqoraApprovalApprover.step_id == step_id)
    users = {u for u in (await session.execute(stmt)).scalars() if u is not None}
    for substitutes in (await delegation.active_substitutes(session, users, at=utc_now())).values():
        users |= substitutes
    if step_id is None:
        requester = (
            await session.execute(
                select(TiqoraApproval.requester).where(TiqoraApproval.id == approval_id)
            )
        ).scalar_one_or_none()
        if requester is not None and str(requester).startswith("agent:"):
            users.add(int(str(requester).partition(":")[2]))
    return users


async def _viewable(session: AsyncSession, approval_id: int, actor: Actor) -> TiqoraApproval:
    """The request, or 404 ``approval_not_found`` (also for one the caller may not see)."""
    approval = (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if approval is None or not await access.can_view(session, approval, actor):
        _refuse("approval_not_found", status.HTTP_404_NOT_FOUND)
    if approval.subject_type in await frozen_subject_types(session) and not (
        await PermissionEngine(session).is_admin(actor.write_user_id)
    ):
        _refuse("subject_unavailable", status.HTTP_409_CONFLICT)
    return approval


async def _detail(session: AsyncSession, approval_id: int, actor: Actor) -> ApprovalDetailOut:
    return await detail.build_detail(session, await _viewable(session, approval_id, actor), actor)


# --- inbox -----------------------------------------------------------------------------------


@status_router.get("/status", response_model=ApprovalsStatusOut)
async def get_approvals_status(user: CurrentUser, session: DbSession) -> ApprovalsStatusOut:
    """Whether the module is switched on (the frontend hides everything otherwise)."""
    _ = user
    return ApprovalsStatusOut(enabled=await approvals_enabled(session))


@router.get("/inbox", response_model=ApprovalInboxPage)
async def get_inbox(
    user: CurrentUser,
    session: DbSession,
    state: Annotated[Literal["pending", "decided"], Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApprovalInboxPage:
    """ "Meine Freigaben": open rows of the caller (and of agents who delegated to them), or
    the rows they decided."""
    page = await inbox.inbox(session, _actor(user), state=state, limit=limit, offset=offset)
    labels = await detail.user_labels(
        session, [i.on_behalf_of for i in page.items if i.on_behalf_of is not None]
    )
    return ApprovalInboxPage(
        items=[
            InboxItemOut(
                approval_id=i.approval_id,
                approver_id=i.approver_id,
                ticket_id=i.ticket_id,
                ticket_number=i.ticket_number,
                ticket_title=i.ticket_title,
                title=i.title,
                subject_type=i.subject_type,
                step_name=i.step_name,
                rule=i.rule,
                required=i.required,
                approved_count=i.approved_count,
                due_at=i.due_at,
                overdue=i.overdue,
                on_behalf_of_label=(
                    None if i.on_behalf_of is None else labels.get(i.on_behalf_of, "?")
                ),
                status=i.status,
                decided_at=i.decided_at,
                my_decision=i.my_decision,
                can_vote=i.can_vote,
                blocked_reason=i.blocked_reason,
            )
            for i in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/inbox/count", response_model=InboxCountOut)
async def get_inbox_count(user: CurrentUser, session: DbSession) -> InboxCountOut:
    """Open rows the caller can still vote on - the sidebar badge."""
    return InboxCountOut(pending=await inbox.pending_count(session, _actor(user)))


# --- pickers ---------------------------------------------------------------------------------


@router.get("/templates", response_model=list[ApprovalTemplateOptionOut])
async def get_templates(
    user: CurrentUser,
    session: DbSession,
    subject_type: Annotated[str | None, Query(max_length=64)] = None,
) -> list[ApprovalTemplateOptionOut]:
    """Valid approval templates (for "Freigabe anfordern"), optionally for one subject type."""
    _ = user
    rows = await templates.list_templates(session, subject_type=subject_type)
    return [
        ApprovalTemplateOptionOut(
            key=r.key,
            name=r.name,
            description=r.description,
            subject_types=list(r.subject_types),
        )
        for r in rows
    ]


@admin_readable_router.get("/lists", response_model=list[ApprovalListOptionOut])
async def get_lists(user: CurrentUser, session: DbSession) -> list[ApprovalListOptionOut]:
    """Valid approval groups ("Freigabegruppe, z. B. CAB") for the approver picker."""
    _ = user
    return [
        ApprovalListOptionOut(id=r.id, name=r.name, member_count=len(r.member_ids))
        for r in await lists.list_lists(session)
    ]


@admin_readable_router.get("/resolvers", response_model=list[ResolverOptionOut])
async def get_resolvers(
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
    subject_type: Annotated[str | None, Query(max_length=64)] = None,
) -> list[ResolverOptionOut]:
    """Dynamic approver sources. Those that name customers only appear where customers may
    approve (the setting is on and the subject type allows them)."""
    _ = user
    registry = get_registry()
    customers = (await load_approval_settings(session, settings)).customer_approvers_enabled
    if customers and subject_type is not None:
        subject = registry.get(APPROVAL_SUBJECT_TYPES, subject_type)
        customers = subject is not None and subject.allow_customer_approvers
    return [
        ResolverOptionOut(key=r.key, title_key=r.title_key, yields_customers=r.yields_customers)
        for r in sorted(registry.items(APPROVER_RESOLVERS), key=lambda r: r.key)
        if customers or not r.yields_customers
    ]


# --- delegations -----------------------------------------------------------------------------


@router.get("/delegations", response_model=list[DelegationOut])
async def get_delegations(user: CurrentUser, session: DbSession) -> list[DelegationOut]:
    """The caller's own delegations ("Vertretung"), running and future ones."""
    return await _delegation_out(
        session, await delegation.list_delegations(session, user_id=user.id)
    )


@router.post("/delegations", response_model=DelegationOut, status_code=status.HTTP_201_CREATED)
async def post_delegation(
    body: DelegationIn, user: CurrentUser, session: DbSession, settings: AppSettings
) -> DelegationOut:
    """The caller names a substitute for a period; the substitute may then decide in their
    place (one vote per person and step, never on the caller's own requests)."""
    ensure_period_open(body.valid_until)
    row = await _run(
        session,
        delegation.create_delegation(
            session,
            user_id=user.id,
            substitute_user_id=body.substitute_user_id,
            valid_from=body.valid_from or utc_now(),
            valid_until=body.valid_until,
            created_by=user.id,
        ),
    )
    await session.commit()
    await _publish(settings, {row.user_id, row.substitute_user_id})
    return (await _delegation_out(session, [row]))[0]


@router.delete("/delegations/{delegation_id:int}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_delegation(
    delegation_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> Response:
    """Ends one of the caller's delegations now. A foreign one is 404 (an admin may end any)."""
    row = await session.get(TiqoraApprovalDelegation, delegation_id)
    if row is None or (
        row.user_id != user.id and not await PermissionEngine(session).is_admin(user.id)
    ):
        _refuse("delegation_not_found", status.HTTP_404_NOT_FOUND)
    users = {row.user_id, row.substitute_user_id}
    await session.rollback()
    await _run(session, delegation.revoke_delegation(session, delegation_id, actor=_actor(user)))
    await session.commit()
    await _publish(settings, users)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- one request -----------------------------------------------------------------------------


@admin_readable_router.get("/{approval_id:int}", response_model=ApprovalDetailOut)
async def get_approval(
    approval_id: int, user: CurrentUser, session: DbSession
) -> ApprovalDetailOut:
    """Steps, approvers, votes, audit trail and what the caller may do. 404 unless the caller
    may see the request (admin, ``ro`` on the ticket, requester, approver or their delegate).
    No ticket articles are part of it."""
    return await _detail(session, approval_id, _actor(user))


@router.post("/{approval_id:int}/decision", response_model=ApprovalDetailOut)
async def post_decision(
    approval_id: int,
    body: DecisionIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ApprovalDetailOut:
    """Votes "Freigeben" / "Ablehnen". The first decision of a step with rule "einer" counts."""
    actor = _actor(user)
    await session.rollback()  # the request lock must be the first read (F1(a))
    result = await _run_on(
        session,
        approval_id,
        actor,
        commit_with_retry(
            session,
            lambda: service.decide(
                session,
                approval_id,
                actor=actor,
                decision=body.decision,
                channel=_channel(user),
                comment=body.comment,
                approver_id=body.approver_id,
                settings=settings,
            ),
        ),
    )
    await _publish(settings, result.affected_user_ids)
    return await _detail(session, approval_id, actor)


@router.post(
    "/{approval_id:int}/approvers/{approver_id:int}/forward", response_model=ApprovalDetailOut
)
async def post_forward(
    approval_id: int,
    approver_id: int,
    body: ForwardIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ApprovalDetailOut:
    """Hands the caller's own open row to another agent."""
    actor = _actor(user)
    await session.rollback()
    owner = (
        await session.execute(
            select(TiqoraApprovalApprover.approval_id, TiqoraApprovalApprover.step_id).where(
                TiqoraApprovalApprover.id == approver_id
            )
        )
    ).first()
    await session.rollback()  # back to a clean transaction for the locking service call
    if owner is None or int(owner[0]) != approval_id:
        await _run_on(
            session,
            approval_id,
            actor,
            _raise(ApprovalError("not_an_approver", status.HTTP_403_FORBIDDEN)),
        )
    await _run_on(
        session,
        approval_id,
        actor,
        commit_with_retry(
            session,
            lambda: service.forward(
                session, approval_id, approver_id, actor=actor, to_user_id=body.user_id
            ),
        ),
    )
    await _publish(settings, await _affected_users(session, approval_id, step_id=int(owner[1])))
    return await _detail(session, approval_id, actor)


@router.post("/{approval_id:int}/cancel", response_model=ApprovalDetailOut)
async def post_cancel(
    approval_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ApprovalDetailOut:
    """Withdraws an open request ("Zurückziehen"): requester, creator, ``rw`` on the ticket
    or an admin. There is no note: the audit trail never stores free text."""
    actor = _actor(user)
    await session.rollback()
    await _run_on(
        session,
        approval_id,
        actor,
        service.cancel_approval(session, approval_id, actor=actor, settings=settings),
    )
    await session.commit()
    await _publish(settings, await _affected_users(session, approval_id))
    return await _detail(session, approval_id, actor)


@router.post("/{approval_id:int}/refresh-approvers", response_model=ApprovalDetailOut)
async def post_refresh_approvers(
    approval_id: int, user: CurrentUser, session: DbSession, settings: AppSettings
) -> ApprovalDetailOut:
    """ "Freigeber neu ermitteln": re-resolves the approvers of the open steps."""
    actor = _actor(user)
    await session.rollback()
    await _run_on(
        session,
        approval_id,
        actor,
        service.refresh_approvers(session, approval_id, actor=actor, settings=settings),
    )
    await session.commit()
    await _publish(settings, await _affected_users(session, approval_id))
    return await _detail(session, approval_id, actor)


# --- on a ticket -----------------------------------------------------------------------------


async def _require_ticket(
    session: AsyncSession, user: CurrentUser, ticket_id: int, *, write: bool = False
) -> None:
    """``ro`` on the ticket's queue, else 404 ``ticket_not_found``; with *write* also ``rw``,
    else 403 ``forbidden``. An admin passes like :mod:`tiqora.approvals.access` lets them
    (``can_view`` / ``can_request``): the ticket only has to exist."""
    if await PermissionEngine(session).is_admin(user.id):
        found = await session.execute(select(Ticket.id).where(Ticket.id == ticket_id))
        if found.first() is None:
            _refuse("ticket_not_found", status.HTTP_404_NOT_FOUND)
        return
    try:
        await require_ticket(session, ticket_id, user_id=user.id, write=write)
    except TicketAccessDenied as exc:
        _refuse(exc.code, exc.http_status)


@ticket_router.get("/{ticket_id}/approvals", response_model=list[ApprovalSummaryOut])
async def list_ticket_approvals(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ApprovalSummaryOut]:
    """Approval requests of the ticket, newest first, paged. Needs ``ro`` on the ticket (or
    admin)."""
    await _require_ticket(session, user, ticket_id)
    rows = (
        await session.execute(
            select(TiqoraApproval)
            .where(
                TiqoraApproval.ticket_id == ticket_id,
                TiqoraApproval.subject_type.not_in(await frozen_subject_types(session)),
            )
            .order_by(TiqoraApproval.id.desc())
            .limit(limit)
            .offset(offset)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return await detail.build_summaries(session, list(rows), _actor(user))


@ticket_router.post(
    "/{ticket_id}/approvals", response_model=ApprovalDetailOut, status_code=status.HTTP_201_CREATED
)
async def request_ticket_approval(
    ticket_id: int,
    body: ApprovalRequestIn,
    user: CurrentUser,
    session: DbSession,
    settings: AppSettings,
) -> ApprovalDetailOut:
    """ "Freigabe anfordern" on the ticket (subject ``ticket``): a template or an own
    definition. Needs ``rw`` on the ticket (or admin). An own definition may not approve when
    nobody can decide or when the deadline passes: those outcomes are for admin templates (F9)."""
    await _require_ticket(session, user, ticket_id, write=True)
    subject = get_registry().get(APPROVAL_SUBJECT_TYPES, "ticket")
    if subject is None or not subject.agent_requestable:
        _refuse("subject_not_requestable", 422)
    if body.definition is not None and any(
        s.on_no_approver != "reject" or s.on_timeout == "approve" for s in body.definition.steps
    ):
        _refuse(
            "invalid_definition",
            422,
            "an own definition cannot approve automatically",
        )
    actor = _actor(user)
    info = await _run(
        session,
        service.request_approval(
            session,
            ticket_id=ticket_id,
            subject_type="ticket",
            title=body.title,
            description=body.description,
            approval_key=body.approval_key,
            definition=body.definition,
            template_key=body.template_key,
            actor=actor,
            settings=settings,
        ),
    )
    await session.commit()
    await _publish(settings, await _affected_users(session, info.approval_id))
    return await _detail(session, info.approval_id, actor)


# --- public decision links ---------------------------------------------------------------------
# Only POST routes with the secret in the body: a GET (link scanner, prefetch, preview) never
# reaches the engine, and no URL, access log or referrer ever carries a token. The page behind
# the mail link (``/approve#t=…``) keeps the secret in the fragment, which browsers never send.


async def _link_gate(
    request: Request, session: AsyncSession, settings: AppSettings, redis: Any, token: str
) -> tuple[ApprovalLinkRateLimiter, str]:
    """Per-IP lock before any database work (429), then the module switch (409
    ``approvals_disabled``, before the token: nothing is counted against the link or the IP), then
    the per-link budget (429).
    """
    limiter = ApprovalLinkRateLimiter(redis, settings)
    ip = client_ip(request, settings.trusted_proxies)
    decision = await limiter.check_ip(ip)
    if decision.allowed:
        enabled = await approvals_enabled(session)
        await session.rollback()  # the link code opens its own transaction
        if not enabled:
            _refuse("approvals_disabled", status.HTTP_409_CONFLICT)
        decision = await limiter.check_token(tokens.link_digest(token))
    if not decision.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "detail": None},
            headers={"Retry-After": str(max(1, decision.retry_after))},
        )
    return limiter, ip


async def _link_view(session: AsyncSession, target: tokens.LinkTarget) -> LinkInspectOut:
    approval = (
        await session.execute(select(TiqoraApproval).where(TiqoraApproval.id == target.approval_id))
    ).scalar_one()
    approver = (
        await session.execute(
            select(TiqoraApprovalApprover).where(TiqoraApprovalApprover.id == target.approver_id)
        )
    ).scalar_one()
    step = (
        await session.execute(
            select(TiqoraApprovalStep).where(TiqoraApprovalStep.id == approver.step_id)
        )
    ).scalar_one()
    labels = await detail.principal_labels(session, [approver.principal])
    number, ticket_title = await detail.load_ticket(session, approval.ticket_id)
    required = None
    if step.rule in ("quorum", "majority"):
        voters, _ = (await inbox.step_progress(session, [step.id]))[step.id]
        required = rules.required_votes(step.rule, step.quorum, voters)  # type: ignore[arg-type]
    return LinkInspectOut(
        valid=True,
        approver_name=labels[approver.principal],
        title=approval.title,
        description=approval.description,
        ticket_number=number,
        ticket_title=ticket_title,
        step_name=step.name,
        rule=step.rule,
        required=required,
        due_at=step.due_at,
        describe_lines=await notify.describe_lines(session, approval),
    )


@links_router.post("/inspect", response_model=LinkInspectOut)
async def post_link_inspect(
    body: LinkInspectIn,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    redis: Annotated[Any, Depends(get_redis)],
) -> LinkInspectOut:
    """What a decision link stands for, without using it (the confirm page reads this).

    A bad link is a normal answer (200, ``valid`` false); only an unknown one counts towards the
    IP lock (a revoked, used or expired link is no guess). A
    customer approver who could not vote through the link (customer approvers off, the ticket
    left their company) gets the vote's 403 instead, without anything about the request.
    """
    limiter, ip = await _link_gate(request, session, settings, redis, body.token)
    try:
        target = await tokens.inspect_link(session, body.token, settings=settings)
        out = await _link_view(session, target)
    except ApprovalError as err:
        await session.rollback()
        if not err.code.startswith("link_"):
            raise _http(err) from err
        if isinstance(err, tokens.UnknownLinkError):  # a known (dead) link is no guess
            await limiter.record_auth_failure(ip)
        return LinkInspectOut(valid=False, code=err.code)
    await session.rollback()  # read only: end the transaction
    return out


@links_router.post("/decide", response_model=LinkDecisionOut)
async def post_link_decide(
    body: LinkDecideIn,
    request: Request,
    session: DbSession,
    settings: AppSettings,
    redis: Annotated[Any, Depends(get_redis)],
) -> LinkDecisionOut:
    """Casts the vote of the link's approver row, once (channel ``email``). 410 ``link_used``
    for a link that was already used, ``link_expired`` for one past its expiry, ``link_invalid``
    for any other dead or unknown link."""
    limiter, ip = await _link_gate(request, session, settings, redis, body.token)
    try:
        result = await commit_with_retry(
            session,
            lambda: tokens.redeem_link(
                session, body.token, decision=body.decision, comment=body.comment, settings=settings
            ),
        )
    except ApprovalError as err:
        await session.rollback()  # nothing is committed after a refusal (used_at included)
        if not err.code.startswith("link_"):
            raise _http(err) from err
        if isinstance(err, tokens.UnknownLinkError):
            await limiter.record_auth_failure(ip)
        raise _http(err) from err
    await _publish(settings, result.affected_user_ids)
    return LinkDecisionOut(decision=body.decision, status=result.info.status)
