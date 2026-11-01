"""Customer portal: approvals the logged-in customer is an approver of (A13 portal rows).

``/api/portal/approvals``. Customers only ever see requests they hold an approver row on, and only
while they are still inside the ticket's company (A7b, re-checked on every read). Everything else
is 404 ``approval_not_found``, the same body as a request that does not exist. A request shows its
own step and rule, the customer's own row and vote; never other approvers or other comments.
The whole router is 404 unless customer approvers and portal approvals are switched on.
"""

from __future__ import annotations

from typing import Annotated, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.portal.deps import CurrentCustomer
from tiqora.approvals import access, inbox, notify, service
from tiqora.approvals.availability import frozen_subject_types
from tiqora.approvals.contract import Actor, ApprovalError, Principal
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalStep,
    TiqoraApprovalVote,
)
from tiqora.approvals.resolve import canonical_principal, customer_in_ticket_company
from tiqora.approvals.retry import commit_with_retry
from tiqora.approvals.schemas import (
    PortalApprovalItemOut,
    PortalApprovalOut,
    PortalApprovalPage,
    PortalDecisionIn,
)
from tiqora.approvals.settings import load_approval_settings
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.domain.portal_gate import portal_enabled
from tiqora.events.pubsub import get_pubsub_redis, publish_approval_changed


def _not_found() -> NoReturn:
    raise HTTPException(
        status.HTTP_404_NOT_FOUND, detail={"code": "approval_not_found", "detail": None}
    )


async def require_portal_approvals(session: DbSession, settings: AppSettings) -> None:
    """404 unless customers may approve, portal approvals are on and the portal itself is on;
    ``approvals_disabled`` while the module is switched off."""
    cfg = await load_approval_settings(session, settings)
    if not cfg.enabled:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "approvals_disabled", "detail": None}
        )
    if not (
        cfg.customer_approvers_enabled
        and cfg.portal_approvals_enabled
        and await portal_enabled(session, settings)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(
    prefix="/approvals",
    tags=["portal-approvals"],
    dependencies=[Depends(require_portal_approvals)],
)


async def _key(session: AsyncSession, customer: AuthenticatedCustomer) -> str:
    return (await canonical_principal(session, Principal.customer(customer.login))).key


def _item(item: inbox.InboxItem) -> PortalApprovalItemOut:
    return PortalApprovalItemOut(
        id=item.approval_id,
        title=item.title,
        ticket_number=item.ticket_number,
        ticket_title=item.ticket_title,
        status=item.status,
        step_name=item.step_name,
        due_at=item.due_at,
        my_status=item.approver_status,
        my_decision=item.my_decision,
        decided_at=item.decided_at,
        can_decide=item.can_vote,
    )


@router.get("", response_model=PortalApprovalPage)
async def list_approvals(
    customer: CurrentCustomer,
    session: DbSession,
    state: Annotated[Literal["pending", "decided"], Query()] = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PortalApprovalPage:
    """The customer's open ("pending") or already decided requests, inside their company."""
    page = await inbox.customer_page(
        session, Actor.customer(customer.login), state=state, limit=limit, offset=offset
    )
    items, total = page.items, page.total
    await session.rollback()  # read only: end the transaction
    return PortalApprovalPage(
        items=[_item(i) for i in items], total=total, limit=limit, offset=offset
    )


async def _own_row(
    session: AsyncSession, approval_id: int, key: str
) -> (
    tuple[TiqoraApproval, TiqoraApprovalApprover, TiqoraApprovalStep, TiqoraApprovalVote | None]
    | None
):
    """The request and the customer's most relevant row on it (open one first), or None."""
    approval = (
        await session.execute(
            select(TiqoraApproval)
            .where(TiqoraApproval.id == approval_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if approval is None or approval.subject_type in await frozen_subject_types(session):
        return None
    result = await session.execute(
        select(TiqoraApprovalApprover, TiqoraApprovalStep)
        .join(TiqoraApprovalStep, TiqoraApprovalStep.id == TiqoraApprovalApprover.step_id)
        .where(
            TiqoraApprovalApprover.approval_id == approval_id,
            TiqoraApprovalApprover.principal == key,
        )
        .order_by(TiqoraApprovalApprover.id)
        .execution_options(populate_existing=True)
    )
    pairs = list(result)
    if not pairs:
        return None
    open_pairs = [p for p in pairs if p[0].status == "pending" and p[1].status == "active"]
    row, step = open_pairs[0] if open_pairs else pairs[-1]
    vote = (
        await session.execute(
            select(TiqoraApprovalVote).where(TiqoraApprovalVote.approver_id == row.id)
        )
    ).scalar_one_or_none()
    return approval, row, step, vote


async def _out(
    session: AsyncSession,
    customer: AuthenticatedCustomer,
    approval_id: int,
    *,
    require_company: bool,
) -> PortalApprovalOut:
    key = await _key(session, customer)
    found = await _own_row(session, approval_id, key)
    if found is None:
        _not_found()
    approval, row, step, vote = found
    if require_company and not await customer_in_ticket_company(
        session, customer.login, approval.ticket_id
    ):
        _not_found()
    open_now = approval.status == "pending" and step.status == "active" and row.status == "pending"
    item = await inbox.row_item(session, key, row, step, approval, vote, pending=open_now)
    return PortalApprovalOut(
        id=approval.id,
        title=approval.title,
        description=approval.description,
        describe_lines=await notify.describe_lines(session, approval),
        ticket_number=item.ticket_number,
        ticket_title=item.ticket_title,
        status=approval.status,
        step_name=step.name,
        rule_text_key=f"approvals.rule.{step.rule}",
        required=item.required,
        approved_count=item.approved_count,
        due_at=step.due_at,
        my_status=row.status,
        my_decision=item.my_decision,
        can_decide=item.can_vote,
    )


@router.get("/{approval_id:int}", response_model=PortalApprovalOut)
async def get_approval(
    approval_id: int, customer: CurrentCustomer, session: DbSession
) -> PortalApprovalOut:
    """One request the customer approves. 404 for any other, also outside their company."""
    out = await _out(session, customer, approval_id, require_company=True)
    await session.rollback()
    return out


@router.post("/{approval_id:int}/decision", response_model=PortalApprovalOut)
async def post_decision(
    approval_id: int,
    body: PortalDecisionIn,
    customer: CurrentCustomer,
    session: DbSession,
    settings: AppSettings,
) -> PortalApprovalOut:
    """Votes "Freigeben" / "Ablehnen" on the customer's own row (channel ``portal``).

    A request the customer holds no row on is 404. Once the ticket left the customer's company
    the engine refuses with 403 ``customer_approver_outside_company``; with customer approvers
    switched off in the meantime, 403 ``forbidden``.
    """
    actor = Actor.customer(customer.login)
    await session.rollback()  # the request lock must be the first read (F1(a))
    try:
        result = await commit_with_retry(
            session,
            lambda: service.decide(
                session,
                approval_id,
                actor=actor,
                decision=body.decision,
                channel="portal",
                comment=body.comment,
                settings=settings,
            ),
        )
    except ApprovalError as err:
        await session.rollback()
        approval = (
            await session.execute(select(TiqoraApproval).where(TiqoraApproval.id == approval_id))
        ).scalar_one_or_none()
        visible = approval is not None and await access.can_view(session, approval, actor)
        in_company = approval is not None and await customer_in_ticket_company(
            session, customer.login, approval.ticket_id
        )
        await session.rollback()
        if not visible:
            _not_found()
        refusal = err
        if not in_company:  # whatever the request's state: closed must not look different
            refusal = ApprovalError("customer_approver_outside_company", 403)
        raise HTTPException(
            refusal.http_status, detail={"code": refusal.code, "detail": refusal.detail}
        ) from err
    await publish_approval_changed(get_pubsub_redis(settings), result.affected_user_ids)
    out = await _out(session, customer, approval_id, require_company=False)
    await session.rollback()
    return out
