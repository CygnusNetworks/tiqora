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
from tiqora.domain.portal_ticket_service import (
    PORTAL_SYSTEM_USER_ID,
    PortalTicketAccessDenied,
    PortalTicketNotFound,
    customer_can_access_ticket,
)
from tiqora.domain.ticket_write_service import InvalidInput
from tiqora.domain.ticket_write_service import TicketNotFound as WriteTicketNotFound
from tiqora.process.config import ActivityDialogConfig, ActivityDialogFieldConfig
from tiqora.process.engine import start_process, submit_activity_dialog
from tiqora.process.exceptions import (
    ActivityDialogNotAvailable,
    ActivityDialogNotFound,
    ProcessNotFound,
    ProcessPermissionDenied,
    RequiredFieldMissing,
    TicketAlreadyInProcess,
    TicketNotInProcess,
    UnresolvedFieldValue,
)
from tiqora.process.graph import ProcessRepository
from tiqora.process.schemas import (
    ActivityDialogDetailOut,
    ActivityDialogFieldOut,
    ActivityDialogSubmitIn,
    ActivityDialogSubmitOut,
    ActivityDialogSummaryOut,
    ProcessStartIn,
    ProcessSummaryOut,
    TicketProcessStateOut,
)
from tiqora.process.ticket_state import get_ticket_process_candidates
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/process", tags=["portal-process"])

_CUSTOMER_INTERFACE = "CustomerInterface"


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(
        exc, (ProcessNotFound, ActivityDialogNotFound, WriteTicketNotFound, PortalTicketNotFound)
    ):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc) or "Not found")
    if isinstance(exc, (ProcessPermissionDenied, PortalTicketAccessDenied)):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if isinstance(exc, (TicketAlreadyInProcess, TicketNotInProcess, ActivityDialogNotAvailable)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (RequiredFieldMissing, UnresolvedFieldValue, InvalidInput)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal error")


_MAPPED = (
    WriteTicketNotFound,
    PortalTicketNotFound,
    PortalTicketAccessDenied,
    ProcessPermissionDenied,
    ProcessNotFound,
    ActivityDialogNotFound,
    ActivityDialogNotAvailable,
    TicketAlreadyInProcess,
    TicketNotInProcess,
    RequiredFieldMissing,
    UnresolvedFieldValue,
    InvalidInput,
)


def _dialog_allows_customer(config: ActivityDialogConfig) -> bool:
    """Znuny: dialog is customer-usable when Interface lists CustomerInterface.

    Empty Interface is treated as agent-only (conservative; Znuny dialogs
    always set Interface explicitly for customer flows).
    """
    interfaces = [str(i) for i in (config.interface or [])]
    return _CUSTOMER_INTERFACE in interfaces


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
    session: AsyncSession, ticket_id: int
) -> TicketProcessStateOut:
    candidates = await get_ticket_process_candidates(session, ticket_id)
    if candidates is None:
        return TicketProcessStateOut()

    activity = candidates.process.activities.get(candidates.activity_entity_id)
    activity_name = activity.name if activity is not None else candidates.activity_entity_id

    customer_dialogs: list[ActivityDialogSummaryOut] = []
    for d in candidates.activity_dialogs:
        if _dialog_allows_customer(d.config):
            customer_dialogs.append(
                ActivityDialogSummaryOut(
                    entity_id=d.entity_id,
                    name=d.name,
                    description_short=d.config.description_short,
                )
            )

    return TicketProcessStateOut(
        process_entity_id=candidates.process.entity_id,
        process_name=candidates.process.name,
        activity_entity_id=candidates.activity_entity_id,
        activity_name=activity_name,
        available_dialogs=customer_dialogs,
        available_transitions_count=len(candidates.outgoing_transitions),
    )


@router.get("/", response_model=list[ProcessSummaryOut])
async def list_processes(customer: CurrentCustomer, session: DbSession) -> list[ProcessSummaryOut]:
    """List processes a customer may start (all known processes; start is gated separately)."""
    del customer
    repository = ProcessRepository(session)
    summaries = await repository.list_processes()
    return [ProcessSummaryOut.model_validate(s, from_attributes=True) for s in summaries]


@router.get("/activity-dialog/{activity_dialog_entity_id}", response_model=ActivityDialogDetailOut)
async def get_activity_dialog(
    activity_dialog_entity_id: str, customer: CurrentCustomer, session: DbSession
) -> ActivityDialogDetailOut:
    del customer
    repository = ProcessRepository(session)
    row = await repository.get_activity_dialog(activity_dialog_entity_id)
    if row is None:
        raise _map_exc(ActivityDialogNotFound(activity_dialog_entity_id))
    config = ActivityDialogConfig.from_yaml(row.config)
    if not _dialog_allows_customer(config):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity dialog is not available on the customer interface",
        )
    return ActivityDialogDetailOut(
        entity_id=row.entity_id,
        name=row.name,
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
    return await _ticket_process_state_out_customer(session, ticket_id)


@router.post("/ticket/{ticket_id}/start", response_model=TicketProcessStateOut)
async def start_ticket_process(
    ticket_id: int, body: ProcessStartIn, customer: CurrentCustomer, session: DbSession
) -> TicketProcessStateOut:
    sysconfig = SysConfig(session)
    try:
        async with session.begin():
            await _assert_customer_ticket(session, customer, ticket_id)
            await start_process(
                session,
                ticket_id=ticket_id,
                process_entity_id=body.process_entity_id,
                user_id=PORTAL_SYSTEM_USER_ID,
                sysconfig=sysconfig,
            )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc
    return await _ticket_process_state_out_customer(session, ticket_id)


@router.post("/ticket/{ticket_id}/submit", response_model=ActivityDialogSubmitOut)
async def submit_ticket_activity_dialog(
    ticket_id: int, body: ActivityDialogSubmitIn, customer: CurrentCustomer, session: DbSession
) -> ActivityDialogSubmitOut:
    sysconfig = SysConfig(session)
    field_values: dict[str, Any] = body.field_values
    # Ensure dialog is customer-facing before engine runs.
    repository = ProcessRepository(session)
    row = await repository.get_activity_dialog(body.activity_dialog_entity_id)
    if row is None:
        raise _map_exc(ActivityDialogNotFound(body.activity_dialog_entity_id))
    cfg = ActivityDialogConfig.from_yaml(row.config)
    if not _dialog_allows_customer(cfg):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity dialog is not available on the customer interface",
        )
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
            )
    except _MAPPED as exc:
        raise _map_exc(exc) from exc

    state = await _ticket_process_state_out_customer(session, ticket_id)
    return ActivityDialogSubmitOut(
        activity_changed=result.activity_changed,
        new_activity_entity_id=result.new_activity_entity_id,
        transition_entity_id=result.transition_entity_id,
        unsupported_actions=result.unsupported_actions,
        state=state,
    )
