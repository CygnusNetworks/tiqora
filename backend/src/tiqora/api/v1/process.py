"""ProcessManagement (BPM) REST API.

Pure translation/orchestration layer over ``tiqora.process.engine`` and
``tiqora.process.graph``/``tiqora.process.ticket_state`` — no business logic
lives here. Follows the exact conventions of ``tiqora.api.v1.calendar``:
``_map_exc()`` translates ``tiqora.process.exceptions.*`` (plus the ticket
existence/permission errors reused from ``tiqora.domain.ticket_write_service``)
to HTTP status codes, and each mutating call wraps its engine call in
``async with session.begin():`` — the same commit convention ``calendar.py``
uses (``DbSession``/``get_db`` itself never commits, see ``api/deps.py``).

Permission model
-----------------
The engine functions in ``process/engine.py`` do not gate ticket read/write
access themselves (only ``submit_activity_dialog`` enforces the activity
dialog's own fine-grained ``Permission`` config, when set). This router adds
the coarse ticket-queue gate on top, mirroring the check
``tiqora.domain.ticket_service.TicketService._assert_ticket_ro`` performs for
``GET /api/v1/tickets/{id}`` (and the ``rw`` gate
``tiqora.domain.ticket_write_service.TicketWriteService._assert_rw`` performs
for ticket mutation endpoints) — reusing ``_ticket_must_exist`` plus
``PermissionEngine.check`` directly, the same pair of primitives those
private helpers are themselves built on (already reused across module
boundaries elsewhere in ``tiqora.process``, e.g. ``engine.py``'s imports from
``ticket_write_service``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.domain.ticket_write_service import TicketNotFound as WriteTicketNotFound
from tiqora.domain.ticket_write_service import (
    _ticket_must_exist,  # noqa: PLC2701 -- deliberate reuse
)
from tiqora.permissions.engine import PermissionEngine
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
)
from tiqora.process.graph import ProcessRepository
from tiqora.process.schemas import (
    ActivityDialogDetailOut,
    ActivityDialogFieldOut,
    ActivityDialogRefOut,
    ActivityDialogSubmitIn,
    ActivityDialogSubmitOut,
    ActivityDialogSummaryOut,
    ProcessActivityOut,
    ProcessDetailOut,
    ProcessStartIn,
    ProcessSummaryOut,
    TicketProcessStateOut,
)
from tiqora.process.ticket_state import get_ticket_process_candidates
from tiqora.znuny.sysconfig import SysConfig

router = APIRouter(prefix="/process", tags=["process"])


def _map_exc(exc: Exception) -> HTTPException:
    if isinstance(exc, (ProcessNotFound, ActivityDialogNotFound, WriteTicketNotFound)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ProcessPermissionDenied):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, (TicketAlreadyInProcess, TicketNotInProcess, ActivityDialogNotAvailable)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, RequiredFieldMissing):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal error")


async def _assert_ticket_permission(
    session: AsyncSession, *, ticket_id: int, user_id: int, permission: str
) -> None:
    """Ticket-existence + coarse queue-permission gate shared by every
    ticket-scoped endpoint below. Raises ``WriteTicketNotFound`` (-> 404) or
    ``ProcessPermissionDenied`` (-> 403) via ``_map_exc``.
    """
    t = await _ticket_must_exist(session, ticket_id)
    perms = PermissionEngine(session)
    if not await perms.check(user_id, int(t["queue_id"]), permission):
        raise ProcessPermissionDenied(
            f"user {user_id} lacks {permission!r} on ticket {ticket_id}'s queue"
        )


def _dialog_field_out(field_cfg: ActivityDialogFieldConfig) -> ActivityDialogFieldOut:
    return ActivityDialogFieldOut(
        display=field_cfg.display,
        default_value=field_cfg.default_value,
        description_short=field_cfg.description_short,
        description_long=field_cfg.description_long,
        config=field_cfg.config,
    )


async def _ticket_process_state_out(session: AsyncSession, ticket_id: int) -> TicketProcessStateOut:
    """Re-fetch and shape a ticket's current process state — used by the
    ``GET .../state``, ``POST .../start``, and ``POST .../submit`` endpoints.
    """
    candidates = await get_ticket_process_candidates(session, ticket_id)
    if candidates is None:
        return TicketProcessStateOut()

    activity = candidates.process.activities.get(candidates.activity_entity_id)
    activity_name = activity.name if activity is not None else candidates.activity_entity_id
    return TicketProcessStateOut(
        process_entity_id=candidates.process.entity_id,
        process_name=candidates.process.name,
        activity_entity_id=candidates.activity_entity_id,
        activity_name=activity_name,
        available_dialogs=[
            ActivityDialogSummaryOut(
                entity_id=d.entity_id, name=d.name, description_short=d.config.description_short
            )
            for d in candidates.activity_dialogs
        ],
        available_transitions_count=len(candidates.outgoing_transitions),
    )


# ── Processes ────────────────────────────────────────────────────────────


@router.get("/", response_model=list[ProcessSummaryOut])
async def list_processes(user: CurrentUser, session: DbSession) -> list[ProcessSummaryOut]:
    del user  # any authenticated agent may list processes
    repository = ProcessRepository(session)
    summaries = await repository.list_processes()
    return [ProcessSummaryOut.model_validate(s, from_attributes=True) for s in summaries]


@router.get("/{process_entity_id}", response_model=ProcessDetailOut)
async def get_process(
    process_entity_id: str, user: CurrentUser, session: DbSession
) -> ProcessDetailOut:
    del user
    repository = ProcessRepository(session)
    graph = await repository.get_process(process_entity_id)
    if graph is None:
        raise _map_exc(ProcessNotFound(process_entity_id))
    return ProcessDetailOut(
        id=graph.id,
        entity_id=graph.entity_id,
        name=graph.name,
        state_entity_id=graph.state_entity_id,
        start_activity_entity_id=graph.config.start_activity,
        activities=[
            ProcessActivityOut(
                entity_id=activity.entity_id,
                name=activity.name,
                activity_dialogs=[
                    ActivityDialogRefOut(entity_id=d.entity_id, name=d.name)
                    for d in activity.activity_dialogs
                ],
            )
            for activity in graph.activities.values()
        ],
    )


# ── Activity dialogs ─────────────────────────────────────────────────────


@router.get("/activity-dialog/{activity_dialog_entity_id}", response_model=ActivityDialogDetailOut)
async def get_activity_dialog(
    activity_dialog_entity_id: str, user: CurrentUser, session: DbSession
) -> ActivityDialogDetailOut:
    del user
    repository = ProcessRepository(session)
    row = await repository.get_activity_dialog(activity_dialog_entity_id)
    if row is None:
        raise _map_exc(ActivityDialogNotFound(activity_dialog_entity_id))

    config = ActivityDialogConfig.from_yaml(row.config)
    return ActivityDialogDetailOut(
        entity_id=row.entity_id,
        name=row.name,
        description_short=config.description_short,
        description_long=config.description_long,
        field_order=config.field_order,
        fields={name: _dialog_field_out(cfg) for name, cfg in config.fields.items()},
        submit_advice_text=config.submit_advice_text,
        submit_button_text=config.submit_button_text,
    )


# ── Ticket process state + actions ──────────────────────────────────────


@router.get("/ticket/{ticket_id}/state", response_model=TicketProcessStateOut)
async def get_ticket_state(
    ticket_id: int, user: CurrentUser, session: DbSession
) -> TicketProcessStateOut:
    """A ticket's current process/activity position.

    Returns an all-``None``/empty-fields ``TicketProcessStateOut`` (HTTP 200)
    if the ticket is not part of any process — that is a normal state, not
    an error. A 404 is only raised if the ticket itself does not exist.
    """
    try:
        await _assert_ticket_permission(
            session, ticket_id=ticket_id, user_id=user.id, permission="ro"
        )
    except (WriteTicketNotFound, ProcessPermissionDenied) as exc:
        raise _map_exc(exc) from exc
    return await _ticket_process_state_out(session, ticket_id)


@router.post("/ticket/{ticket_id}/start", response_model=TicketProcessStateOut)
async def start_ticket_process(
    ticket_id: int, body: ProcessStartIn, user: CurrentUser, session: DbSession
) -> TicketProcessStateOut:
    sysconfig = SysConfig(session)
    try:
        async with session.begin():
            await _assert_ticket_permission(
                session, ticket_id=ticket_id, user_id=user.id, permission="rw"
            )
            await start_process(
                session,
                ticket_id=ticket_id,
                process_entity_id=body.process_entity_id,
                user_id=user.id,
                sysconfig=sysconfig,
            )
    except (
        WriteTicketNotFound,
        ProcessPermissionDenied,
        ProcessNotFound,
        TicketAlreadyInProcess,
    ) as exc:
        raise _map_exc(exc) from exc
    return await _ticket_process_state_out(session, ticket_id)


@router.post("/ticket/{ticket_id}/submit", response_model=ActivityDialogSubmitOut)
async def submit_ticket_activity_dialog(
    ticket_id: int, body: ActivityDialogSubmitIn, user: CurrentUser, session: DbSession
) -> ActivityDialogSubmitOut:
    """Submit an activity dialog for *ticket_id*.

    Requires ``rw`` on the ticket's queue as a coarse gate — the dialog's own
    (finer-grained) ``Permission`` config, if set, is enforced again inside
    ``engine.submit_activity_dialog`` itself.
    """
    sysconfig = SysConfig(session)
    field_values: dict[str, Any] = body.field_values
    try:
        async with session.begin():
            await _assert_ticket_permission(
                session, ticket_id=ticket_id, user_id=user.id, permission="rw"
            )
            result = await submit_activity_dialog(
                session,
                ticket_id=ticket_id,
                activity_dialog_entity_id=body.activity_dialog_entity_id,
                field_values=field_values,
                user_id=user.id,
                sysconfig=sysconfig,
            )
    except (
        WriteTicketNotFound,
        ProcessPermissionDenied,
        ProcessNotFound,
        TicketNotInProcess,
        ActivityDialogNotFound,
        ActivityDialogNotAvailable,
        RequiredFieldMissing,
    ) as exc:
        raise _map_exc(exc) from exc

    state = await _ticket_process_state_out(session, ticket_id)
    return ActivityDialogSubmitOut(
        activity_changed=result.activity_changed,
        new_activity_entity_id=result.new_activity_entity_id,
        transition_entity_id=result.transition_entity_id,
        unsupported_actions=result.unsupported_actions,
        state=state,
    )
