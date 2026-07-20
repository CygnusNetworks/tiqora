"""Pydantic v2 read-only DTOs for ProcessManagement (BPM).

Kept minimal for now — subtask 3 (REST API) will extend these with
request/pagination/detail shapes as needed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
