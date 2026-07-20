"""Domain exceptions for ProcessManagement (BPM) execution.

Kept separate from :mod:`tiqora.process.engine` so callers (subtask 3's REST
layer, subtask 5's tests) can import the exception set without pulling in the
full execution engine.
"""

from __future__ import annotations


class ProcessNotFound(Exception):
    """The referenced ``pm_process.entity_id`` does not exist (or has no
    ``StartActivity``, which makes it unusable to start a process instance)."""


class ActivityDialogNotFound(Exception):
    """The referenced ``pm_activity_dialog.entity_id`` does not exist at all."""


class ActivityDialogNotAvailable(Exception):
    """The activity dialog exists but is not offered by the ticket's current
    activity (i.e. it is not listed in that activity's ``ActivityDialog``
    config) — submitting it would not be possible via Znuny's own
    AgentTicketProcess UI either."""


class TicketNotInProcess(Exception):
    """The ticket has no (or an inconsistent/dangling) process/activity
    Dynamic Field state — see :func:`tiqora.process.ticket_state.get_ticket_process_state`."""


class TicketAlreadyInProcess(Exception):
    """``start_process`` was called for a ticket that already has process
    Dynamic Field values set. Znuny's AgentTicketProcess frontend does not
    offer starting a second process on a ticket already in one; Tiqora
    enforces the same restriction at the engine layer as a deliberate,
    documented simplification (see ``process/engine.py`` module docstring)."""


class RequiredFieldMissing(Exception):
    """An activity-dialog-required field was missing/empty in a
    ``submit_activity_dialog`` call, OR a dispatched TransitionAction was
    missing a Config key it requires to run (the same exception is reused
    for both — both are "caller supplied incomplete input" errors)."""


class ProcessPermissionDenied(Exception):
    """The acting user lacks the activity dialog's configured ``Permission``
    on the ticket's queue."""
