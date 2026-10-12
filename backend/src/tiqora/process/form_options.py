"""Option refresh of a process dialog form (plan D7).

Znuny re-renders the dependent fields of a process dialog over AJAX (``AJAXUpdate``) with the
values entered so far. Tiqora asks the server again with those values: the Owner/Responsible
pickers follow the selected queue, the SLA list the selected service, and every ACL
``Properties`` block sees the entered attribute and dynamic field values.

The refresh goes through the same option builder as the form
(:func:`tiqora.process.dialog_form.collect_dialog_options`) and the same availability gates
(:func:`resolve_start_dialog` / :func:`resolve_ticket_dialog`), so it never offers more than the
form itself would.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.process.actor import ProcessActor
from tiqora.process.dialog_form import (
    collect_dialog_options,
    dialog_field_names,
    is_option_field,
    resolve_start_dialog,
    resolve_ticket_dialog,
    selectable_queue_ids,
    selection_from_values,
)
from tiqora.process.fields import field_config
from tiqora.process.graph import ActivityDialogNode, ProcessGraph, ProcessRepository
from tiqora.process.schemas import ProcessDialogOptionOut

if TYPE_CHECKING:
    from tiqora.domain.auth import AuthenticatedUser

__all__ = [
    "ProcessFormOptionsIn",
    "ProcessFormOptionsOut",
    "refresh_dialog_options",
    "refresh_start_options",
    "refresh_ticket_dialog_options",
]


class ProcessFormOptionsIn(BaseModel):
    #: The values entered so far (the ``field_values`` shape).
    values: dict[str, Any] = Field(default_factory=dict, max_length=200)
    #: Canonical names to refresh; empty = every select/multiselect field of the form.
    fields: list[str] = Field(default_factory=list, max_length=50)


class ProcessFormOptionsOut(BaseModel):
    options: dict[str, list[ProcessDialogOptionOut]]


async def refresh_dialog_options(
    session: AsyncSession,
    *,
    actor: ProcessActor,
    user: AuthenticatedUser | None,
    graph: ProcessGraph,
    dialog: ActivityDialogNode,
    ticket_id: int | None,
    values: Mapping[str, Any],
    fields: Sequence[str],
    activity_entity_id: str | None = None,
) -> dict[str, list[ProcessDialogOptionOut]]:
    """The option lists of *fields* (empty: all select/multiselect fields of the form) for the
    entered *values*. Names that are not option fields of the form are left out."""
    names, dynamic_fields = await dialog_field_names(
        session, actor=actor, dialog=dialog, ticket_id=ticket_id
    )
    option_names = [n for n in names if is_option_field(n, dynamic_fields)]
    requested = [n for n in option_names if not fields or n in fields]
    if not requested:
        return {}
    selection = selection_from_values(values)
    if selection.queue_id is not None and selection.queue_id not in await selectable_queue_ids(
        session, actor, user
    ):
        selection = replace(selection, queue_id=None)
    return await collect_dialog_options(
        session,
        actor=actor,
        user=user,
        names=requested,
        ticket_id=ticket_id,
        dialog_fields={name: field_config(dialog.config, name) for name in names},
        dynamic_fields=dynamic_fields,
        process_context={
            "ProcessEntityID": graph.entity_id,
            "ActivityEntityID": activity_entity_id or "",
            "ActivityDialogEntityID": dialog.entity_id,
        },
        selection=selection,
    )


async def refresh_start_options(
    session: AsyncSession,
    *,
    actor: ProcessActor,
    user: AuthenticatedUser | None,
    process_entity_id: str,
    ticket_id: int | None,
    body: ProcessFormOptionsIn,
    repository: ProcessRepository,
) -> ProcessFormOptionsOut:
    """Option refresh of a process start dialog; the start gate of the form applies."""
    target = await resolve_start_dialog(
        session,
        actor,
        process_entity_id=process_entity_id,
        ticket_id=ticket_id,
        repository=repository,
    )
    return ProcessFormOptionsOut(
        options=await refresh_dialog_options(
            session,
            actor=actor,
            user=user,
            graph=target.graph,
            dialog=target.dialog,
            ticket_id=ticket_id,
            values=body.values,
            fields=body.fields,
            activity_entity_id=target.activity_entity_id,
        )
    )


async def refresh_ticket_dialog_options(
    session: AsyncSession,
    *,
    actor: ProcessActor,
    user: AuthenticatedUser | None,
    ticket_id: int,
    activity_dialog_entity_id: str,
    body: ProcessFormOptionsIn,
    repository: ProcessRepository,
) -> ProcessFormOptionsOut:
    """Option refresh of a dialog of the ticket's current activity; the submit gate applies."""
    target = await resolve_ticket_dialog(
        session,
        actor,
        ticket_id=ticket_id,
        activity_dialog_entity_id=activity_dialog_entity_id,
        repository=repository,
    )
    return ProcessFormOptionsOut(
        options=await refresh_dialog_options(
            session,
            actor=actor,
            user=user,
            graph=target.graph,
            dialog=target.dialog,
            ticket_id=ticket_id,
            values=body.values,
            fields=body.fields,
            activity_entity_id=target.activity_entity_id,
        )
    )
