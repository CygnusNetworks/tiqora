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
from tiqora.db.engine import get_session_factory
from tiqora.domain.attachments_in import AttachmentInvalid, AttachmentTooLarge
from tiqora.domain.ticket_write_service import (
    InvalidInput,
    _ticket_must_exist,  # noqa: PLC2701 -- deliberate reuse
)
from tiqora.domain.ticket_write_service import TicketNotFound as WriteTicketNotFound
from tiqora.permissions.engine import PermissionEngine
from tiqora.plugins.process_points import abort_on_action_timeout
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
    PluginActionTimedOut,
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
from tiqora.process.path import load_process_path
from tiqora.process.process_ticket import create_process_ticket, enroll_ticket
from tiqora.process.schemas import (
    ActivityDialogDetailOut,
    ActivityDialogFieldOut,
    ActivityDialogRefOut,
    ActivityDialogSubmitIn,
    ActivityDialogSubmitOut,
    ActivityDialogSummaryOut,
    NewProcessTicketIn,
    NewProcessTicketOut,
    ProcessActivityOut,
    ProcessDetailOut,
    ProcessDialogFormOut,
    ProcessPathOut,
    ProcessStartIn,
    ProcessSummaryOut,
    SkippedActionOut,
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


# Every engine/write-service error that maps to a defined HTTP status above.
# Both mutating endpoints catch this single tuple so a newly-raised process
# exception can never be handled by one endpoint but escape another as a 500.
_MAPPED_ERRORS: tuple[type[Exception], ...] = (
    AttachmentTooLarge,
    AttachmentInvalid,
    WriteTicketNotFound,
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


async def _ticket_process_state_out(
    session: AsyncSession, ticket_id: int, actor: ProcessActor, repository: ProcessRepository
) -> TicketProcessStateOut:
    """Re-fetch and shape a ticket's current process state — used by the
    ``GET .../state``, ``POST .../start``, and ``POST .../submit`` endpoints.
    The dialogs and ``can_start`` honour state, interface and ACLs (availability.py).
    """
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


# ── Processes ────────────────────────────────────────────────────────────


@router.get("/", response_model=list[ProcessSummaryOut])
async def list_processes(
    user: CurrentUser, session: DbSession, ticket_id: int | None = None
) -> list[ProcessSummaryOut]:
    """Processes the agent may start (Active, start dialog for the agent interface, ACLs).
    With ``ticket_id`` (enroll context) the ticket is access-checked and the Action ACL applies."""
    if ticket_id is not None:
        try:
            await _assert_ticket_permission(
                session, ticket_id=ticket_id, user_id=user.id, permission="ro"
            )
        except (WriteTicketNotFound, ProcessPermissionDenied) as exc:
            raise _map_exc(exc) from exc
    summaries = await list_startable_processes(
        session, ProcessActor.agent(user.id), ticket_id=ticket_id
    )
    return [ProcessSummaryOut.model_validate(s, from_attributes=True) for s in summaries]


@router.get("/{process_entity_id}/start-form", response_model=ProcessDialogFormOut)
async def get_start_form(
    process_entity_id: str, user: CurrentUser, session: DbSession, ticket_id: int | None = None
) -> ProcessDialogFormOut:
    """The start dialog of a process, built by the server: visible fields in order with their
    kind and ACL-filtered options. With ``ticket_id`` (enroll context) the ticket-bound gates
    of :func:`assert_can_start` apply."""
    repository = ProcessRepository(session)
    try:
        if ticket_id is not None:
            await _assert_ticket_permission(
                session, ticket_id=ticket_id, user_id=user.id, permission="ro"
            )
        return await build_start_form(
            session,
            actor=ProcessActor.agent(user.id),
            user=user,
            process_entity_id=process_entity_id,
            ticket_id=ticket_id,
            repository=repository,
        )
    except _MAPPED_ERRORS as exc:
        raise _map_exc(exc) from exc


@router.post("/{process_entity_id}/start-form/options", response_model=ProcessFormOptionsOut)
async def refresh_start_form_options(
    process_entity_id: str,
    body: ProcessFormOptionsIn,
    user: CurrentUser,
    session: DbSession,
    ticket_id: int | None = None,
) -> ProcessFormOptionsOut:
    """The option lists of the start dialog for the values entered so far (Owner follows Queue,
    SLA follows Service, ACLs see the entered values). Same gates as ``GET .../start-form``."""
    try:
        if ticket_id is not None:
            await _assert_ticket_permission(
                session, ticket_id=ticket_id, user_id=user.id, permission="ro"
            )
        return await refresh_start_options(
            session,
            actor=ProcessActor.agent(user.id),
            user=user,
            process_entity_id=process_entity_id,
            ticket_id=ticket_id,
            body=body,
            repository=ProcessRepository(session),
        )
    except _MAPPED_ERRORS as exc:
        raise _map_exc(exc) from exc


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
    node = await repository.get_activity_dialog(activity_dialog_entity_id)
    if node is None:
        raise _map_exc(ActivityDialogNotFound(activity_dialog_entity_id))

    config = node.config
    # ActivityDialogGet with Interface => AgentInterface returns nothing for other dialogs.
    if not dialog_allows(config, "AgentInterface"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity dialog is not available on the agent interface",
        )
    return ActivityDialogDetailOut(
        entity_id=node.entity_id,
        name=node.name,
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
    return await _ticket_process_state_out(
        session, ticket_id, ProcessActor.agent(user.id), ProcessRepository(session)
    )


@router.get("/ticket/{ticket_id}/path", response_model=ProcessPathOut)
async def get_ticket_path(ticket_id: int, user: CurrentUser, session: DbSession) -> ProcessPathOut:
    """The activities the ticket has been through so far, oldest first (empty outside a process)."""
    try:
        await _assert_ticket_permission(
            session, ticket_id=ticket_id, user_id=user.id, permission="ro"
        )
    except (WriteTicketNotFound, ProcessPermissionDenied) as exc:
        raise _map_exc(exc) from exc
    return await load_process_path(session, ticket_id)


@router.get(
    "/ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form",
    response_model=ProcessDialogFormOut,
)
async def get_ticket_dialog_form(
    ticket_id: int, activity_dialog_entity_id: str, user: CurrentUser, session: DbSession
) -> ProcessDialogFormOut:
    """A dialog of the ticket's current activity as a server-built form; 403 when the dialog is
    hidden for this agent (interface, Permission, ACLs), like the submit."""
    try:
        await _assert_ticket_permission(
            session, ticket_id=ticket_id, user_id=user.id, permission="ro"
        )
        return await build_ticket_dialog_form(
            session,
            actor=ProcessActor.agent(user.id),
            user=user,
            ticket_id=ticket_id,
            activity_dialog_entity_id=activity_dialog_entity_id,
            repository=ProcessRepository(session),
        )
    except _MAPPED_ERRORS as exc:
        raise _map_exc(exc) from exc


@router.post(
    "/ticket/{ticket_id}/dialog/{activity_dialog_entity_id}/form/options",
    response_model=ProcessFormOptionsOut,
)
async def refresh_ticket_dialog_form_options(
    ticket_id: int,
    activity_dialog_entity_id: str,
    body: ProcessFormOptionsIn,
    user: CurrentUser,
    session: DbSession,
) -> ProcessFormOptionsOut:
    """The option lists of a ticket dialog for the values entered so far; 403 when the dialog is
    hidden for this agent, like ``GET .../form``."""
    try:
        await _assert_ticket_permission(
            session, ticket_id=ticket_id, user_id=user.id, permission="ro"
        )
        return await refresh_ticket_dialog_options(
            session,
            actor=ProcessActor.agent(user.id),
            user=user,
            ticket_id=ticket_id,
            activity_dialog_entity_id=activity_dialog_entity_id,
            body=body,
            repository=ProcessRepository(session),
        )
    except _MAPPED_ERRORS as exc:
        raise _map_exc(exc) from exc


@router.post("/tickets", response_model=NewProcessTicketOut, status_code=status.HTTP_201_CREATED)
async def create_new_process_ticket(
    body: NewProcessTicketIn, user: CurrentUser, session: DbSession
) -> NewProcessTicketOut:
    """Create a ticket from the start dialog of a process. Needs ``create`` on the resolved
    queue (403), a startable process (409) and the start dialog's mandatory fields (400)."""
    sysconfig = SysConfig(session)
    actor = ProcessActor.agent(user.id)
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
    except PluginActionTimedOut as exc:
        raise await abort_on_action_timeout(session, exc) from exc
    except _MAPPED_ERRORS as exc:
        raise _map_exc(exc) from exc
    state = await _ticket_process_state_out(session, result.ticket_id, actor, repository)
    return NewProcessTicketOut(
        ticket_id=result.ticket_id,
        ticket_number=result.ticket_number,
        activity_changed=result.transition.activity_changed,
        unsupported_actions=result.transition.unsupported_actions,
        skipped_actions=[
            SkippedActionOut.model_validate(a) for a in result.transition.skipped_actions
        ],
        state=state,
    )


@router.post("/ticket/{ticket_id}/start", response_model=TicketProcessStateOut)
async def start_ticket_process(
    ticket_id: int, body: ProcessStartIn, user: CurrentUser, session: DbSession
) -> TicketProcessStateOut:
    sysconfig = SysConfig(session)
    actor = ProcessActor.agent(user.id)
    repository = ProcessRepository(session)  # one revision for the whole request
    try:
        async with session.begin():
            await _assert_ticket_permission(
                session, ticket_id=ticket_id, user_id=user.id, permission="rw"
            )
            # assert_can_start, the start dialog's fields and the first transitions.
            await enroll_ticket(
                session,
                sysconfig,
                actor=actor,
                ticket_id=ticket_id,
                process_entity_id=body.process_entity_id,
                field_values=body.field_values,
                repository=repository,
            )
    except PluginActionTimedOut as exc:
        raise await abort_on_action_timeout(session, exc) from exc
    except _MAPPED_ERRORS as exc:
        raise _map_exc(exc) from exc
    return await _ticket_process_state_out(session, ticket_id, actor, repository)


@router.post("/ticket/{ticket_id}/submit", response_model=ActivityDialogSubmitOut)
async def submit_ticket_activity_dialog(
    ticket_id: int, body: ActivityDialogSubmitIn, user: CurrentUser, session: DbSession
) -> ActivityDialogSubmitOut:
    """Submit an activity dialog for *ticket_id*.

    Requires ``rw`` on the ticket's queue as a coarse gate — the dialog's own
    (finer-grained) ``Permission`` config, the interface and the ACLs are enforced again
    inside ``engine.submit_activity_dialog`` (``availability.assert_can_submit``).
    """
    sysconfig = SysConfig(session)
    field_values: dict[str, Any] = body.field_values
    actor = ProcessActor.agent(user.id)
    repository = ProcessRepository(session)
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
                actor=actor,
                repository=repository,
            )
    except PluginActionTimedOut as exc:
        raise await abort_on_action_timeout(session, exc) from exc
    except _MAPPED_ERRORS as exc:
        raise _map_exc(exc) from exc

    state = await _ticket_process_state_out(session, ticket_id, actor, repository)
    return ActivityDialogSubmitOut(
        activity_changed=result.activity_changed,
        new_activity_entity_id=result.new_activity_entity_id,
        transition_entity_id=result.transition_entity_id,
        unsupported_actions=result.unsupported_actions,
        skipped_actions=[SkippedActionOut.model_validate(a) for a in result.skipped_actions],
        state=state,
    )
