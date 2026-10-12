"""Names for synchronous before-hooks (``registry.before``): veto a write or enrich a ticket."""

from __future__ import annotations

from tiqora.plugins.sync_hooks import (
    ArticleOp,
    DynamicFieldsOp,
    HookContext,
    OperationVetoed,
    QueueMoveOp,
    StateChangeOp,
    SyncHandler,
    SyncOperation,
    TicketCreateOp,
    TicketCreatePatch,
)

__all__ = [
    "ArticleOp",
    "DynamicFieldsOp",
    "HookContext",
    "OperationVetoed",
    "QueueMoveOp",
    "StateChangeOp",
    "SyncHandler",
    "SyncOperation",
    "TicketCreateOp",
    "TicketCreatePatch",
]
