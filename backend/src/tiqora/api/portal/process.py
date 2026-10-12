"""Portal ProcessManagement endpoints (CustomerInterface dialogs only).

Same ``pm_*`` tables and engine as agent ``/api/v1/process``, but:

* Auth is customer session (``CurrentCustomer``).
* Ticket access uses portal ownership scope (not agent queue perms).
* Activity dialogs are filtered to ``CustomerInterface`` (Znuny parity).
* Writes are attributed to system user id 1 (same as portal ticket create).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.portal.deps import CurrentCustomer, DbSession
from tiqora.db.engine import get_session_factory
from tiqora.domain.attachments_in import AttachmentInvalid, AttachmentTooLarge
from tiqora.domain.portal_ticket_service import (
    PORTAL_SYSTEM_USER_ID,
    PortalTicketAccessDenied,
    PortalTicketNotFound,
    customer_can_access_ticket,
)
from tiqora.domain.ticket_write_service import InvalidInput
from tiqora.domain.ticket_write_service import TicketNotFound as WriteTicketNotFound
from tiqora.process.actor import ProcessActor
from tiqora.process.availability import (
    available_dialogs,
    dialog_allows,
    list_startable_processes,
)
from tiqora.process.availability import (
    can_start as can_start_process,
)
from tiqora.process.config import ActivityDialogFieldConfig
from tiqora.process.dialog_form import build_start_form, build_ticket_dialog_form
from tiqora.process.dump import PROCESS_STATE_NAMES
from tiqora.process.engine import submit_activity_dialog
from tiqora.process.exceptions import (
    ActivityDialogNotAvailable,
    ActivityDialogNotFound,
    ProcessNotFound,
    ProcessNotRunning,
    ProcessNotStartable,
    ProcessPermissionDenied,
    RequiredFieldMissing,
    TicketAlreadyInProcess,
    TicketNotInProcess,
    UnresolvedFieldValue,
)
from tiqora.process.form_options import (
    ProcessFormOptionsIn,
    ProcessFormOptionsOut,
    refresh_start_options,
    refresh_ticket_dialog_options,
)
from tiqora.process.graph import ProcessRepository
from tiqora.process.process_ticket import create_process_ticket, enroll_ticket
from tiqora.process.schemas import (
    ActivityDialogDetailOut,
    ActivityDialogFieldOut,
    ActivityDialogSubmitIn,
    ActivityDialogSubmitOut,
    ActivityDialogSummaryOut,
    NewProcessTicketIn,
    NewProcessTicketOut,
    ProcessDialogFormOut,
    ProcessStartIn,
    ProcessSummaryOut,
    TicketProcessStateOut,
)
from tiqora.process.ticket_state import get_ticket_process_candidates
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/process", tags=["portal-process"])


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(
        exc, (ProcessNotFound, ActivityDialogNotFound, WriteTicketNotFound, PortalTicketNotFound)
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc) or "Not found")
    if isinstance(exc, (ProcessPermissionDenied, PortalTicketAccessDenied)):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(
        exc,
        (
            TicketAlreadyInProcess,
            TicketNotInProcess,
            ActivityDialogNotAvailable,
            ProcessNotStartable,
            ProcessNotRunning,
        ),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, AttachmentTooLarge):
        return HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc))
    if isinstance(exc, AttachmentInvalid):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, (RequiredFieldMissing, UnresolvedFieldValue, InvalidInput)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal error")


_MAPPED = (
    AttachmentTooLarge,
    AttachmentInvalid,
    WriteTicketNotFound,
    PortalTicketNotFound,
    PortalTicketAccessDenied,
    ProcessPermissionDenied,
    ProcessNotFound,
    ActivityDialogNotFound,
    ActivityDialogNotAvailable,
    ProcessNotStartable,
    ProcessNotRunning,
    TicketAlreadyInProcess,
    TicketNotInProcess,
    RequiredFieldMissing,
    UnresolvedFieldValue,
    InvalidInput,
)


def _actor(customer: CurrentCustomer) -> ProcessActor:
    return ProcessActor.customer(login=customer.login, customer_id=customer.customer_id)


def _field_out(field_cfg: ActivityDialogFieldConfig) -> ActivityDialogFieldOut:
    return ActivityDialogFieldOut(
        display=field_cfg.display,
        default_value=field_cfg.default_value,
        description_short=field_cfg.description_short,
        description_long=field_cfg.description_long,
        config=field_cfg.config,
    )


async def _assert_customer_ticket(
    session: AsyncSession, customer: CurrentCustomer, ticket_id: int
) -> None:
    ok = await customer_can_access_ticket(
        session,
        login=customer.login,
        customer_id=customer.customer_id,
        ticket_id=ticket_id,
    )
    if not ok:
        # Distinguish missing vs forbidden without leaking existence.
        from sqlalchemy import select

        from tiqora.db.legacy.ticket import Ticket

        exists = (
            await session.execute(select(Ticket.id).where(Ticket.id == ticket_id))
        ).scalar_one_or_none()
        if exists is None:
            raise PortalTicketNotFound(ticket_id)
        raise PortalTicketAccessDenied(ticket_id)


async def _ticket_process_state_out_customer(
    session: AsyncSession, ticket_id: int, actor: ProcessActor, repository: ProcessRepository
) -> TicketProcessStateOut:
    candidates = await get_ticket_process_candidates(session, ticket_id, repository=repository)
    if candidates is None:
        can_start = await can_start_process(
            session, actor, ticket_id=ticket_id, repository=repository
        )
        return TicketProcessStateOut(can_start=can_start)

    activity = candidates.process.activities.get(candidates.activity_entity_id)
    activity_name = activity.name if activity is not None else candidates.activity_entity_id
    dialogs = await available_dialogs(session, actor, ticket_id=ticket_id, candidates=candidates)
    return TicketProcessStateOut(
        process_entity_id=candidates.process.entity_id,
        process_name=candidates.process.name,
        activity_entity_id=candidates.activity_entity_id,
        activity_name=activity_name,
        available_dialogs=[
            ActivityDialogSummaryOut(
                entity_id=d.entity_id, name=d.name, description_short=d.config.description_short
            )
            for d in dialogs
        ],
        available_transitions_count=len(candidates.outgoing_transitions),
        process_state=PROCESS_STATE_NAMES.get(candidates.process.state_entity_id),
    )


@router.get("/", response_model=list[ProcessSummaryOut])
async def list_processes(
    customer: CurrentCustomer, session: DbSession, ticket_id: int | None = None
) -> list[ProcessSummaryOut]:
    """Processes the customer may start (Active, start dialog for the customer interface, ACLs).
    With ``ticket_id`` (enroll context) the ticket is access-checked and the Action ACL applies."""
    if ticket_id is not None:
        try:
            await _assert_customer_ticket(session, customer, ticket_id)
        except (PortalTicketNotFound, PortalTicketAccessDenied) as exc:
            raise _map_exc(exc) from exc
    summaries = await list_startable_processes(session, _actor(customer), ticket_id=ticket_id)
    return [ProcessSummaryOut.model_validate(s, from_attributes=True) for s in summaries]


@router.get("/{process_entity_id}/start-form", response_model=ProcessDialogFormOut)
async def get_start_form(
    process_entity_id: str,
    customer: CurrentCustomer,
    session: DbSession,
    ticket_id: int | None = None,
) -> ProcessDialogFormOut:
    """The start dialog of a process for the customer interface, built by the server."""
    repository = ProcessRepository(session)
    try:
        if ticket_id is not None:
            await _assert_customer_ticket(session, customer, ticket_id)
        return await build_start_form(
            session,
            actor=_actor(customer),
            user=None,
            process_entity_id=process_entity_id,
            ticket_id=ticket_id,
            repository=repository,
        )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc


@router.post("/{process_entity_id}/start-form/options", response_model=ProcessFormOptionsOut)
async def refresh_start_form_options(
    process_entity_id: str,
    body: ProcessFormOptionsIn,
    customer: CurrentCustomer,
    session: DbSession,
    ticket_id: int | None = None,
) -> ProcessFormOptionsOut:
    """The option lists of the start dialog for the values entered so far, with the customer
    principal. Same gates as ``GET .../start-form``."""
    try:
        if ticket_id is not None:
            await _assert_customer_ticket(session, customer, ticket_id)
        return await refresh_start_options(
            session,
            actor=_actor(customer),
            user=None,
            process_entity_id=process_entity_id,
            ticket_id=ticket_id,
            body=body,
            repository=ProcessRepository(session),
        )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc


@router.get("/activity-dialog/{activity_dialog_entity_id}", response_model=ActivityDialogDetailOut)
async def get_activity_dialog(
    activity_dialog_entity_id: str, customer: CurrentCustomer, session: DbSession
) -> ActivityDialogDetailOut:
    del customer
    repository = ProcessRepository(session)
    node = await repository.get_activity_dialog(activity_dialog_entity_id)
    if node is None:
        raise _map_exc(ActivityDialogNotFound(activity_dialog_entity_id))
    config = node.config
    if not dialog_allows(config, "CustomerInterface"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity dialog is not available on the customer interface",
        )
    return ActivityDialogDetailOut(
        entity_id=node.entity_id,
        name=node.name,
        description_short=config.description_short,
        description_long=config.description_long,
        field_order=config.field_order,
        fields={name: _field_out(cfg) for name, cfg in config.fields.items()},
        submit_advice_text=config.submit_advice_text,
        submit_button_text=config.submit_button_text,
    )


@router.get("/ticket/{ticket_id}/state", response_model=TicketProcessStateOut)
async def get_ticket_state(
    ticket_id: int, customer: CurrentCustomer, session: DbSession
) -> TicketProcessStateOut:
    try:
        await _assert_customer_ticket(session, customer, ticket_id)
    except (PortalTicketNotFound, PortalTicketAccessDenied) as exc:
        raise _map_exc(exc) from exc
    return await _ticket_process_state_out_customer(
        session, ticket_id, _actor(customer), ProcessRepository(session)
    )


@router.get(
    "/ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form",
    response_model=ProcessDialogFormOut,
)
async def get_ticket_dialog_form(
    ticket_id: int, activity_dialog_entity_id: str, customer: CurrentCustomer, session: DbSession
) -> ProcessDialogFormOut:
    try:
        await _assert_customer_ticket(session, customer, ticket_id)
        return await build_ticket_dialog_form(
            session,
            actor=_actor(customer),
            user=None,
            ticket_id=ticket_id,
            activity_dialog_entity_id=activity_dialog_entity_id,
            repository=ProcessRepository(session),
        )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc


@router.post(
    "/ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form/options",
    response_model=ProcessFormOptionsOut,
)
async def refresh_ticket_dialog_form_options(
    ticket_id: int,
    activity_dialog_entity_id: str,
    body: ProcessFormOptionsIn,
    customer: CurrentCustomer,
    session: DbSession,
) -> ProcessFormOptionsOut:
    """The option lists of a ticket dialog for the values entered so far (customer principal)."""
    try:
        await _assert_customer_ticket(session, customer, ticket_id)
        return await refresh_ticket_dialog_options(
            session,
            actor=_actor(customer),
            user=None,
            ticket_id=ticket_id,
            activity_dialog_entity_id=activity_dialog_entity_id,
            body=body,
            repository=ProcessRepository(session),
        )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc


@router.post("/tickets", response_model=NewProcessTicketOut, status_code=status.HTTP_201_CREATED)
async def create_new_process_ticket(
    body: NewProcessTicketIn, customer: CurrentCustomer, session: DbSession
) -> NewProcessTicketOut:
    """Create a ticket from the start dialog of a process (customer interface). The customer
    identity comes from the session: submitted ``CustomerUserID``/``CustomerID``/``Owner`` are
    ignored (the engine forces them from the actor, owner = the portal system user). A queue
    chosen in the dialog must be one of the customer's create queues."""
    sysconfig = SysConfig(session)
    actor = _actor(customer)
    repository = ProcessRepository(session)
    try:
        async with session.begin():
            result = await create_process_ticket(
                session,
                get_session_factory(),
                sysconfig,
                actor=actor,
                process_entity_id=body.process_entity_id,
                field_values=body.field_values,
                repository=repository,
            )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc
    state = await _ticket_process_state_out_customer(session, result.ticket_id, actor, repository)
    return NewProcessTicketOut(
        ticket_id=result.ticket_id,
        ticket_number=result.ticket_number,
        activity_changed=result.transition.activity_changed,
        unsupported_actions=result.transition.unsupported_actions,
        state=state,
    )


@router.post("/ticket/{ticket_id}/start", response_model=TicketProcessStateOut)
async def start_ticket_process(
    ticket_id: int, body: ProcessStartIn, customer: CurrentCustomer, session: DbSession
) -> TicketProcessStateOut:
    """Enroll an existing ticket through the process start dialog (assert_can_start, the
    dialog's mandatory fields and the first transitions run in ``enroll_ticket``)."""
    sysconfig = SysConfig(session)
    actor = _actor(customer)
    repository = ProcessRepository(session)  # one revision for the whole request
    try:
        async with session.begin():
            await _assert_customer_ticket(session, customer, ticket_id)
            await enroll_ticket(
                session,
                sysconfig,
                actor=actor,
                ticket_id=ticket_id,
                process_entity_id=body.process_entity_id,
                field_values=body.field_values,
                repository=repository,
            )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc
    return await _ticket_process_state_out_customer(session, ticket_id, actor, repository)


@router.post("/ticket/{ticket_id}/submit", response_model=ActivityDialogSubmitOut)
async def submit_ticket_activity_dialog(
    ticket_id: int, body: ActivityDialogSubmitIn, customer: CurrentCustomer, session: DbSession
) -> ActivityDialogSubmitOut:
    sysconfig = SysConfig(session)
    field_values: dict[str, Any] = body.field_values
    actor = _actor(customer)
    repository = ProcessRepository(session)
    try:
        async with session.begin():
            await _assert_customer_ticket(session, customer, ticket_id)
            result = await submit_activity_dialog(
                session,
                ticket_id=ticket_id,
                activity_dialog_entity_id=body.activity_dialog_entity_id,
                field_values=field_values,
                user_id=PORTAL_SYSTEM_USER_ID,
                sysconfig=sysconfig,
                actor=actor,
                repository=repository,
            )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc

    state = await _ticket_process_state_out_customer(session, ticket_id, actor, repository)
    return ActivityDialogSubmitOut(
        activity_changed=result.activity_changed,
        new_activity_entity_id=result.new_activity_entity_id,
        transition_entity_id=result.transition_entity_id,
        unsupported_actions=result.unsupported_actions,
        state=state,
    )
