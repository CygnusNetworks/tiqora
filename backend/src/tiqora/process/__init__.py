"""ProcessManagement (BPM) config loading, ticket-state resolution, and
execution engine.

Parses the ``pm_*`` legacy tables into typed config models
(:mod:`tiqora.process.config`), links them into an in-memory object graph
(:mod:`tiqora.process.graph`), resolves a ticket's current process/activity
from its Dynamic Field values (:mod:`tiqora.process.ticket_state`), and
executes processes — starting them, evaluating transition conditions,
dispatching TransitionActions, and driving Activity Dialog submission
(:mod:`tiqora.process.engine`).
"""

from __future__ import annotations

from tiqora.process.config import (
    ActivityConfig,
    ActivityDialogConfig,
    ActivityDialogFieldConfig,
    ProcessConfig,
    ProcessLayout,
    TransitionActionConfig,
    TransitionConditionBlock,
    TransitionConditionField,
    TransitionConfig,
    TransitionPathEntry,
)
from tiqora.process.engine import (
    ActivityDialogSubmitResult,
    evaluate_transition,
    execute_transition_action,
    get_ticket_attrs,
    start_process,
    submit_activity_dialog,
)
from tiqora.process.exceptions import (
    ActivityDialogNotAvailable,
    ActivityDialogNotFound,
    ProcessNotFound,
    ProcessPermissionDenied,
    RequiredFieldMissing,
    TicketAlreadyInProcess,
    TicketNotInProcess,
)
from tiqora.process.graph import (
    ActivityDialogNode,
    ActivityNode,
    ProcessGraph,
    ProcessRepository,
    ProcessSummary,
    TransitionActionNode,
    TransitionNode,
)
from tiqora.process.schemas import (
    ActivityDialogSummaryOut,
    ProcessStateOut,
    ProcessSummaryOut,
    TransitionSummaryOut,
)
from tiqora.process.ticket_state import (
    ACTIVITY_ID_DF_NAME,
    PROCESS_ID_DF_NAME,
    TicketProcessCandidates,
    TicketProcessState,
    get_ticket_process_candidates,
    get_ticket_process_state,
)

__all__ = [
    "ACTIVITY_ID_DF_NAME",
    "PROCESS_ID_DF_NAME",
    "ActivityConfig",
    "ActivityDialogConfig",
    "ActivityDialogFieldConfig",
    "ActivityDialogNode",
    "ActivityDialogNotAvailable",
    "ActivityDialogNotFound",
    "ActivityDialogSubmitResult",
    "ActivityDialogSummaryOut",
    "ActivityNode",
    "ProcessConfig",
    "ProcessGraph",
    "ProcessLayout",
    "ProcessNotFound",
    "ProcessPermissionDenied",
    "ProcessRepository",
    "ProcessStateOut",
    "ProcessSummary",
    "ProcessSummaryOut",
    "RequiredFieldMissing",
    "TicketAlreadyInProcess",
    "TicketNotInProcess",
    "TicketProcessCandidates",
    "TicketProcessState",
    "TransitionActionConfig",
    "TransitionActionNode",
    "TransitionConditionBlock",
    "TransitionConditionField",
    "TransitionConfig",
    "TransitionNode",
    "TransitionPathEntry",
    "TransitionSummaryOut",
    "evaluate_transition",
    "execute_transition_action",
    "get_ticket_attrs",
    "get_ticket_process_candidates",
    "get_ticket_process_state",
    "start_process",
    "submit_activity_dialog",
]
