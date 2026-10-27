"""Admin API of the approval engine (A13 admin rows): overview, templates, approver lists and
delegations of any agent.

Mounted at ``/api/v1/admin/approvals`` (``AdminUser``, API-key area ``admin``). Cancelling a
request goes through the agent endpoint (an admin may cancel any request); an admin gets no
power to decide in place of somebody else.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Iterable
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import AppSettings, DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.approvals import delegation, detail, lists, templates
from tiqora.approvals.contract import (
    APPROVAL_SUBJECT_TYPES,
    Actor,
    ApprovalDefinition,
    ApprovalError,
)
from tiqora.approvals.models import (
    TiqoraApproval,
    TiqoraApprovalApprover,
    TiqoraApprovalDelegation,
    TiqoraApprovalStep,
)
from tiqora.approvals.schemas import (
    AdminDelegationIn,
    ApprovalAdminItemOut,
    ApprovalAdminPage,
    ApprovalListIn,
    ApprovalListMemberOut,
    ApprovalListOut,
    ApprovalStatusName,
    ApprovalTemplateIn,
    ApprovalTemplateOut,
    DefinitionCheckIn,
    DefinitionCheckOut,
    DelegationOut,
)
from tiqora.db.legacy.ticket import Ticket
from tiqora.events.pubsub import get_pubsub_redis, publish_approval_changed
from tiqora.extensions.core import get_registry
from tiqora.jobs.service import utc_now

router = APIRouter(prefix="/approvals", tags=["admin:approvals"])

_CHUNK = 500


def _http(err: ApprovalError) -> HTTPException:
    """``{"detail": {"code": ..., "detail": ...}}``: the frontend reads ``detail.code``."""
    return HTTPException(err.http_status, detail={"code": err.code, "detail": err.detail})


def _refuse(code: str, http_status: int, detail_text: str | None = None) -> NoReturn:
    raise _http(ApprovalError(code, http_status, detail_text))


async def _run[T](session: AsyncSession, operation: Awaitable[T]) -> T:
    """Awaits an engine call; an :class:`ApprovalError` rolls back and becomes an HTTP error."""
    try:
        return await operation
    except ApprovalError as err:
        await session.rollback()
        raise _http(err) from err


def _chunks(ids: list[int]) -> Iterable[list[int]]:
    for start in range(0, len(ids), _CHUNK):
        yield ids[start : start + _CHUNK]


# --- overview --------------------------------------------------------------------------------


@router.get("", response_model=ApprovalAdminPage)
async def get_overview(
    admin: AdminUser,
    session: DbSession,
    status_: Annotated[ApprovalStatusName | None, Query(alias="status")] = None,
    ticket_id: Annotated[int | None, Query(ge=1)] = None,
    subject_type: Annotated[str | None, Query(max_length=64)] = None,
    overdue: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ApprovalAdminPage:
    """Every approval request, newest first. ``overdue`` = an active step is past its deadline
    (``true``) or none is (``false``)."""
    _ = admin
    now = utc_now()
    conditions: list[Any] = []
    if status_ is not None:
        conditions.append(TiqoraApproval.status == status_)
    if ticket_id is not None:
        conditions.append(TiqoraApproval.ticket_id == ticket_id)
    if subject_type is not None:
        conditions.append(TiqoraApproval.subject_type == subject_type)
    if overdue is not None:
        late = select(TiqoraApprovalStep.approval_id).where(
            TiqoraApprovalStep.status == "active",
            (TiqoraApprovalStep.overdue_at.is_not(None))
            | (TiqoraApprovalStep.due_at.is_not(None) & (TiqoraApprovalStep.due_at < now)),
        )
        conditions.append(
            TiqoraApproval.id.in_(late) if overdue else TiqoraApproval.id.not_in(late)
        )
    total = (
        await session.execute(select(func.count()).select_from(TiqoraApproval).where(*conditions))
    ).scalar_one()
    approvals = list(
        (
            await session.execute(
                select(TiqoraApproval)
                .where(*conditions)
                .order_by(TiqoraApproval.id.desc())
                .limit(limit)
                .offset(offset)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    items = await _overview_items(session, approvals, now)
    await session.rollback()  # read-only request
    return ApprovalAdminPage(items=items, total=int(total), limit=limit, offset=offset)


async def _overview_items(
    session: AsyncSession, approvals: list[TiqoraApproval], now: Any
) -> list[ApprovalAdminItemOut]:
    ids = [a.id for a in approvals]
    # tiqora_* and Znuny rows are loaded separately and matched in Python (collation 1267)
    active: dict[int, list[TiqoraApprovalStep]] = defaultdict(list)
    open_rows: dict[int, int] = defaultdict(int)
    for chunk in _chunks(ids):
        steps = await session.execute(
            select(TiqoraApprovalStep)
            .where(TiqoraApprovalStep.approval_id.in_(chunk), TiqoraApprovalStep.status == "active")
            .order_by(TiqoraApprovalStep.position)
        )
        for step in steps.scalars():
            active[step.approval_id].append(step)
        counts = await session.execute(
            select(TiqoraApprovalApprover.approval_id, func.count())
            .join(TiqoraApprovalStep, TiqoraApprovalStep.id == TiqoraApprovalApprover.step_id)
            .where(
                TiqoraApprovalApprover.approval_id.in_(chunk),
                TiqoraApprovalApprover.status == "pending",
                TiqoraApprovalStep.status == "active",
            )
            .group_by(TiqoraApprovalApprover.approval_id)
        )
        for approval_id, count in counts:
            open_rows[int(approval_id)] = int(count)
    tickets: dict[int, tuple[str, str]] = {}
    ticket_ids = sorted({a.ticket_id for a in approvals})
    for chunk in _chunks(ticket_ids):
        rows = await session.execute(
            select(Ticket.id, Ticket.tn, Ticket.title).where(Ticket.id.in_(chunk))
        )
        for tid, number, title in rows:
            tickets[int(tid)] = (str(number), title)
    labels = await detail.principal_labels(session, [a.requester for a in approvals])
    return [
        ApprovalAdminItemOut(
            id=a.id,
            ticket_id=a.ticket_id,
            ticket_number=tickets.get(a.ticket_id, (None, None))[0],
            ticket_title=tickets.get(a.ticket_id, (None, None))[1],
            subject_type=a.subject_type,
            subject_ref=a.subject_ref,
            approval_key=a.approval_key,
            title=a.title,
            status=a.status,
            reason=a.reason,
            current_stage=a.current_stage,
            step_name=active[a.id][0].name if active[a.id] else None,
            open_approvers=open_rows[a.id],
            overdue=any(detail._step_overdue(s, now) for s in active[a.id]),  # noqa: SLF001
            requester_label=labels[a.requester],
            created_at=a.created_at,
            decided_at=a.decided_at,
            can_cancel=a.status == "pending",
        )
        for a in approvals
    ]


# --- templates -------------------------------------------------------------------------------


async def _template_out(session: AsyncSession, row: templates.TemplateRow) -> ApprovalTemplateOut:
    check = await templates.validate_definition_refs(
        session, row.definition, subject_types=row.subject_types
    )
    return ApprovalTemplateOut(
        id=row.id,
        template_key=row.key,
        name=row.name,
        description=row.description,
        subject_types=list(row.subject_types),
        definition=row.definition,
        origin=row.origin,
        valid=row.valid,
        warnings=check.warnings,
    )


@router.get("/templates", response_model=list[ApprovalTemplateOut])
async def get_templates(admin: AdminUser, session: DbSession) -> list[ApprovalTemplateOut]:
    """All templates, valid or not."""
    _ = admin
    rows = await templates.list_templates(session, include_invalid=True)
    out = [await _template_out(session, r) for r in rows]
    await session.rollback()
    return out


@router.post("/templates/validate", response_model=DefinitionCheckOut)
async def post_validate_template(
    body: DefinitionCheckIn, admin: AdminUser, session: DbSession
) -> DefinitionCheckOut:
    """Checks a definition without saving it: structure, resolver keys and references
    (``errors`` block saving) and approvers that no longer exist (``warnings``)."""
    _ = admin
    subject_types = [] if body.subject_type is None else [body.subject_type]
    if (
        body.subject_type is not None
        and get_registry().get(APPROVAL_SUBJECT_TYPES, body.subject_type) is None
    ):
        return DefinitionCheckOut(errors=[f"unknown_subject_type:{body.subject_type}"], warnings=[])
    try:
        definition = ApprovalDefinition.model_validate(body.definition)
    except ValidationError as exc:
        paths = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        return DefinitionCheckOut(
            errors=[f"invalid_definition:{p}" if p else "invalid_definition" for p in paths],
            warnings=[],
        )
    check = await templates.validate_definition_refs(
        session, definition, subject_types=subject_types
    )
    await session.rollback()
    return DefinitionCheckOut(errors=check.errors, warnings=check.warnings)


@router.get("/templates/{template_id:int}", response_model=ApprovalTemplateOut)
async def get_template(
    template_id: int, admin: AdminUser, session: DbSession
) -> ApprovalTemplateOut:
    _ = admin
    row = await _run(session, templates.get_template(session, template_id))
    out = await _template_out(session, row)
    await session.rollback()
    return out


@router.post("/templates", response_model=ApprovalTemplateOut, status_code=status.HTTP_201_CREATED)
async def post_template(
    body: ApprovalTemplateIn, admin: AdminUser, session: DbSession
) -> ApprovalTemplateOut:
    """Creates an admin template. ``unknown_subject_type`` / ``invalid_definition`` 422,
    ``template_key_taken`` 409."""
    row = await _run(
        session,
        templates.create_template(
            session,
            key=body.template_key,
            name=body.name,
            description=body.description,
            subject_types=body.subject_types,
            definition=body.definition,
            actor_id=admin.id,
        ),
    )
    if not body.valid:
        row = await _run(
            session, templates.update_template(session, row.id, actor_id=admin.id, valid=False)
        )
    out = await _template_out(session, row)
    await session.commit()
    return out


@router.put("/templates/{template_id:int}", response_model=ApprovalTemplateOut)
async def put_template(
    template_id: int, body: ApprovalTemplateIn, admin: AdminUser, session: DbSession
) -> ApprovalTemplateOut:
    """Replaces name, description, subject types, definition and validity. The key is
    immutable (consumers address templates by it): another one is 422 ``invalid_definition``."""
    current = await _run(session, templates.get_template(session, template_id))
    if body.template_key != current.key:
        await session.rollback()
        _refuse("invalid_definition", 422, "the template key cannot be changed")
    row = await _run(
        session,
        templates.update_template(
            session,
            template_id,
            actor_id=admin.id,
            name=body.name,
            description=body.description,
            subject_types=body.subject_types,
            definition=body.definition,
            valid=body.valid,
        ),
    )
    out = await _template_out(session, row)
    await session.commit()
    return out


@router.delete("/templates/{template_id:int}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: int, admin: AdminUser, session: DbSession) -> Response:
    """Running requests keep their snapshot."""
    _ = admin
    await _run(session, templates.delete_template(session, template_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- approver lists ----------------------------------------------------------------------------


async def _list_out(
    session: AsyncSession, rows: Iterable[lists.ApprovalListRow]
) -> list[ApprovalListOut]:
    rows = list(rows)
    labels = await detail.user_labels(session, [m for r in rows for m in r.member_ids])
    return [
        ApprovalListOut(
            id=r.id,
            name=r.name,
            description=r.description,
            valid=r.valid,
            member_ids=list(r.member_ids),
            members=[ApprovalListMemberOut(id=m, label=labels.get(m, "?")) for m in r.member_ids],
        )
        for r in rows
    ]


@router.get("/lists", response_model=list[ApprovalListOut])
async def get_lists(admin: AdminUser, session: DbSession) -> list[ApprovalListOut]:
    """All approver lists ("Freigabegruppen"), valid or not."""
    _ = admin
    out = await _list_out(session, await lists.list_lists(session, include_invalid=True))
    await session.rollback()
    return out


@router.get("/lists/{list_id:int}", response_model=ApprovalListOut)
async def get_list(list_id: int, admin: AdminUser, session: DbSession) -> ApprovalListOut:
    _ = admin
    row = await _run(session, lists.get_list(session, list_id))
    out = (await _list_out(session, [row]))[0]
    await session.rollback()
    return out


@router.post("/lists", response_model=ApprovalListOut, status_code=status.HTTP_201_CREATED)
async def post_list(body: ApprovalListIn, admin: AdminUser, session: DbSession) -> ApprovalListOut:
    """``invalid_member`` 422 for an unknown or invalid agent, ``list_name_taken`` 409."""
    row = await _run(
        session,
        lists.create_list(
            session,
            name=body.name,
            description=body.description,
            member_ids=body.member_ids,
            actor_id=admin.id,
        ),
    )
    if not body.valid:
        row = await _run(
            session, lists.update_list(session, row.id, actor_id=admin.id, valid=False)
        )
    out = (await _list_out(session, [row]))[0]
    await session.commit()
    return out


@router.put("/lists/{list_id:int}", response_model=ApprovalListOut)
async def put_list(
    list_id: int, body: ApprovalListIn, admin: AdminUser, session: DbSession
) -> ApprovalListOut:
    """Replaces name, description, members and validity."""
    row = await _run(
        session,
        lists.update_list(
            session,
            list_id,
            actor_id=admin.id,
            name=body.name,
            description=body.description,
            member_ids=body.member_ids,
            valid=body.valid,
        ),
    )
    out = (await _list_out(session, [row]))[0]
    await session.commit()
    return out


@router.delete("/lists/{list_id:int}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list(list_id: int, admin: AdminUser, session: DbSession) -> Response:
    """``list_in_use`` 409 while a template still names the list."""
    _ = admin
    await _run(session, lists.delete_list(session, list_id))
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- delegations -------------------------------------------------------------------------------


async def _delegation_out(
    session: AsyncSession, rows: Iterable[TiqoraApprovalDelegation]
) -> list[DelegationOut]:
    rows = list(rows)
    labels = await detail.user_labels(session, [r.substitute_user_id for r in rows])
    return [
        DelegationOut(
            id=r.id,
            user_id=r.user_id,
            substitute_user_id=r.substitute_user_id,
            substitute_label=labels.get(r.substitute_user_id, "?"),
            valid_from=r.valid_from,
            valid_until=r.valid_until,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/delegations", response_model=list[DelegationOut])
async def get_delegations(
    admin: AdminUser,
    session: DbSession,
    user_id: Annotated[int | None, Query(ge=1)] = None,
    include_ended: bool = False,
) -> list[DelegationOut]:
    """Delegations of any agent (optionally of one delegator), running and future ones."""
    _ = admin
    rows = await delegation.list_delegations(session, user_id=user_id, include_ended=include_ended)
    out = await _delegation_out(session, rows)
    await session.rollback()
    return out


@router.post("/delegations", response_model=DelegationOut, status_code=status.HTTP_201_CREATED)
async def post_delegation(
    body: AdminDelegationIn, admin: AdminUser, session: DbSession, settings: AppSettings
) -> DelegationOut:
    """Names a substitute for *another* agent (``user_id``), e.g. for sick leave."""
    row = await _run(
        session,
        delegation.create_delegation(
            session,
            user_id=body.user_id,
            substitute_user_id=body.substitute_user_id,
            valid_from=body.valid_from or utc_now(),
            valid_until=body.valid_until,
            created_by=admin.id,
        ),
    )
    out = (await _delegation_out(session, [row]))[0]
    await session.commit()
    await publish_approval_changed(
        get_pubsub_redis(settings), {row.user_id, row.substitute_user_id}
    )
    return out


@router.delete("/delegations/{delegation_id:int}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_delegation(
    delegation_id: int, admin: AdminUser, session: DbSession, settings: AppSettings
) -> Response:
    """Ends any delegation now (404 ``delegation_not_found`` for an unknown id)."""
    row = await session.get(TiqoraApprovalDelegation, delegation_id)
    if row is None:
        await session.rollback()
        _refuse("delegation_not_found", status.HTTP_404_NOT_FOUND)
    users = {row.user_id, row.substitute_user_id}
    await session.rollback()
    await _run(
        session, delegation.revoke_delegation(session, delegation_id, actor=Actor.agent(admin.id))
    )
    await session.commit()
    await publish_approval_changed(get_pubsub_redis(settings), users)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
