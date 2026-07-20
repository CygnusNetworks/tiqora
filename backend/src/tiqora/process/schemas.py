"""Pydantic v2 DTOs for ProcessManagement (BPM).

Subtask 1's read-only summary/state DTOs live at the top; subtask 3 (REST
API, ``tiqora.api.v1.process``) adds the request/detail/submit shapes below
them — kept here rather than inline in the router module, matching the
calendar module's schemas/service separation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProcessSummaryOut(BaseModel):
    """One row from :meth:`tiqora.process.graph.ProcessRepository.list_processes`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: str
    name: str
    state_entity_id: str


class ActivityDialogSummaryOut(BaseModel):
    """A candidate activity dialog available at a ticket's current activity."""

    model_config = ConfigDict(from_attributes=True)

    entity_id: str
    name: str
    description_short: str


class TransitionSummaryOut(BaseModel):
    """A candidate outgoing transition from a ticket's current activity.

    No condition evaluation is performed — see
    :mod:`tiqora.process.ticket_state`. Whether this transition can actually
    fire is subtask 2's job.
    """

    model_config = ConfigDict(from_attributes=True)

    entity_id: str
    name: str
    target_activity_entity_id: str


class ProcessStateOut(BaseModel):
    """A ticket's current process/activity plus available dialogs/transitions."""

    process_entity_id: str
    process_name: str
    activity_entity_id: str
    activity_name: str
    available_activity_dialogs: list[ActivityDialogSummaryOut]
    available_transitions: list[TransitionSummaryOut]


# ---------------------------------------------------------------------------
# Subtask 3 (REST API) DTOs
# ---------------------------------------------------------------------------


class ActivityDialogRefOut(BaseModel):
    """A dialog reference within :class:`ProcessDetailOut` — entity id + name
    only, no field-level detail (that is ``ActivityDialogDetailOut``, fetched
    per-dialog by the frontend as needed)."""

    entity_id: str
    name: str


class ProcessActivityOut(BaseModel):
    """One activity within :class:`ProcessDetailOut`."""

    entity_id: str
    name: str
    activity_dialogs: list[ActivityDialogRefOut]


class ProcessDetailOut(BaseModel):
    """A single process's full graph: activities and their dialogs.

    Transition/condition internals are intentionally not exposed here — the
    frontend does not need them to render a process, and Tiqora does not
    leak internal BPM routing logic to clients (see ``TicketProcessStateOut``).
    """

    id: int
    entity_id: str
    name: str
    state_entity_id: str
    start_activity_entity_id: str | None
    activities: list[ProcessActivityOut]


class TicketProcessStateOut(BaseModel):
    """A ticket's current process/activity position, for the REST layer.

    All fields are ``None``/empty when the ticket is not part of any
    process — that is a normal state (HTTP 200), not an error; see
    ``GET /api/v1/process/ticket/{ticket_id}/state``.

    ``available_transitions_count`` is informational only (how many outgoing
    transitions the current activity has) — the transitions' own
    conditions/actions are not exposed to avoid leaking internal BPM routing
    logic to the client.
    """

    process_entity_id: str | None = None
    process_name: str | None = None
    activity_entity_id: str | None = None
    activity_name: str | None = None
    available_dialogs: list[ActivityDialogSummaryOut] = Field(default_factory=list)
    available_transitions_count: int = 0


class ActivityDialogFieldOut(BaseModel):
    """One field definition within :class:`ActivityDialogDetailOut`, for the
    frontend to build a dynamic form."""

    display: str
    default_value: Any = None
    description_short: str
    description_long: str
    config: dict[str, Any]


class ActivityDialogDetailOut(BaseModel):
    """Full field definitions for one activity dialog."""

    entity_id: str
    name: str
    description_short: str
    description_long: str
    field_order: list[str]
    fields: dict[str, ActivityDialogFieldOut]
    submit_advice_text: str
    submit_button_text: str


class ProcessStartIn(BaseModel):
    """Body of ``POST /api/v1/process/ticket/{ticket_id}/start``."""

    process_entity_id: str


class ActivityDialogSubmitIn(BaseModel):
    """Body of ``POST /api/v1/process/ticket/{ticket_id}/submit``."""

    activity_dialog_entity_id: str
    field_values: dict[str, Any] = Field(default_factory=dict)


class ActivityDialogSubmitOut(BaseModel):
    """Outcome of submitting an activity dialog, mirroring
    :class:`tiqora.process.engine.ActivityDialogSubmitResult`, plus the
    freshly re-fetched ticket process state so the frontend can immediately
    re-render without a second round-trip."""

    activity_changed: bool
    new_activity_entity_id: str | None
    transition_entity_id: str | None
    unsupported_actions: list[str]
    state: TicketProcessStateOut
