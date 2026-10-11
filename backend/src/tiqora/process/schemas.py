"""Pydantic v2 DTOs for ProcessManagement (BPM).

Subtask 1's read-only summary/state DTOs live at the top; subtask 3 (REST
API, ``tiqora.api.v1.process``) adds the request/detail/submit shapes below
them — kept here rather than inline in the router module, matching the
calendar module's schemas/service separation.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProcessSummaryOut(BaseModel):
    """One row from :meth:`tiqora.process.graph.ProcessRepository.list_processes`."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: str
    name: str
    state_entity_id: str
    state_name: str = ""


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
    process_state: str | None = None  # "Active" | "Inactive" | "FadeAway"
    can_start: bool = False  # not in a process, the Action ACL allows it, >= 1 startable process


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
    """Body of ``POST /api/v1/process/ticket/{ticket_id}/start`` (enroll through the start
    dialog: ``field_values`` are the dialog's fields, like a dialog submit)."""

    process_entity_id: str
    field_values: dict[str, Any] = Field(default_factory=dict)


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


class NewProcessTicketIn(BaseModel):
    """Body of ``POST /api/v1/process/tickets``: start dialog values of a new process ticket."""

    process_entity_id: str
    field_values: dict[str, Any] = Field(default_factory=dict)


class NewProcessTicketOut(BaseModel):
    """The created process ticket and the outcome of its first transition pass."""

    ticket_id: int
    ticket_number: str
    activity_changed: bool
    unsupported_actions: list[str]
    state: TicketProcessStateOut


# ---------------------------------------------------------------------------
# Server-built activity dialog form (D9)
# ---------------------------------------------------------------------------

FieldKind = Literal[
    "text",
    "textarea",
    "select",
    "multiselect",
    "boolean",
    "date",
    "datetime",
    "article",
    "pending_time",
    "customer",
    "unsupported",
]


class ProcessDialogOptionOut(BaseModel):
    value: str
    label: str


class ProcessDialogFieldOut(BaseModel):
    name: str  # canonical name, e.g. "Queue", "DynamicField_Region", "Article"
    kind: FieldKind
    label: str  # DF label; ticket attributes: the canonical name (the frontend translates)
    required: bool
    description_short: str = ""
    description_long: str = ""
    default: Any = None
    options: list[ProcessDialogOptionOut] = Field(default_factory=list)
    #: kind "article" only: booked time. "0" = no time field (also whenever
    #: ``Ticket::Frontend::AccountTime`` is off or on the customer interface), "1" = optional,
    #: "2" = required (``AgentTicketProcess.pm:3067-3082``).
    time_units: Literal["0", "1", "2"] = "0"
    #: kind "article": the submit may carry files in ``field_values.ArticleAttachments``
    #: (a list of ``{filename, content_type, content_base64}``, at most 10).
    uploads: bool = False


class ProcessDialogFormOut(BaseModel):
    """One activity dialog as the server wants it rendered: visible fields in order, with
    their kind and the ACL-filtered options."""

    process_entity_id: str
    activity_dialog_entity_id: str
    name: str
    description_short: str
    description_long: str
    submit_button_text: str
    submit_advice_text: str
    fields: list[ProcessDialogFieldOut]
