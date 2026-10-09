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
    """The acting user may not use the dialog or start here: missing ``Permission`` on the
    ticket's queue (agent or customer group check), wrong interface, or an ACL (HTTP 403)."""


class UnresolvedFieldValue(Exception):
    """A submitted activity-dialog field value (Queue/State/Priority/Owner/
    Responsible) could not be resolved to an id — neither by name lookup nor
    as a literal numeric id. Previously this was silently ignored (the field
    change was skipped with no error), which let a typo'd value fail to
    apply without any feedback to the caller."""


class ProcessNotStartable(Exception):
    """The process cannot be started here: it is not Active, its start dialog is not offered
    on this interface, or an ACL (Process / Action) hides it (HTTP 409)."""


class ProcessNotRunning(Exception):
    """The ticket's process is Inactive, so none of its dialogs may be submitted (HTTP 409).
    FadeAway processes keep running; only new starts are refused."""
