"""ProcessManagement (BPM) config loading and ticket-state resolution.

Read-only in this subtask: parses the ``pm_*`` legacy tables into typed
config models (:mod:`tiqora.process.config`), links them into an in-memory
object graph (:mod:`tiqora.process.graph`), and resolves a ticket's current
process/activity from its Dynamic Field values
(:mod:`tiqora.process.ticket_state`). Condition evaluation and transition
action dispatch (``process/engine.py``) are a later subtask.
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
    "ActivityDialogSummaryOut",
    "ActivityNode",
    "ProcessConfig",
    "ProcessGraph",
    "ProcessLayout",
    "ProcessRepository",
    "ProcessStateOut",
    "ProcessSummary",
    "ProcessSummaryOut",
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
    "get_ticket_process_candidates",
    "get_ticket_process_state",
]
