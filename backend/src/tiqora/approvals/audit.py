"""Audit rows, outbox events and ticket history lines of the approval engine (A9).

Everything written here carries ids, keys and statuses only - never names, e-mail addresses,
titles or comments (those stay in the vote/approver rows that GDPR erasure handles). Functions
flush, never commit.
"""

from __future__ import annotations

import json
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import Actor
from tiqora.approvals.models import AUDIT_ACTIONS, TiqoraApproval, TiqoraApprovalEvent
from tiqora.approvals.settings import ApprovalSettings
from tiqora.domain.ticket_write_service import _emit_event
from tiqora.jobs.service import utc_now
from tiqora.znuny.history import history_add

#: the only keys an outbox payload may carry
PAYLOAD_KEYS: Final = frozenset(
    {
        "approval_id",
        "approval_key",
        "subject_type",
        "subject_ref",
        "status",
        "step_id",
        "decision",
        "channel",
        "reason",
    }
)
_HISTORY_VERBS: Final = frozenset({"requested", "voted", "decided", "cancelled"})


async def record(
    session: AsyncSession,
    *,
    approval: TiqoraApproval,
    action: str,
    actor: Actor,
    step_id: int | None = None,
    approver_id: int | None = None,
    channel: str | None = None,
    detail: dict[str, Any] | None = None,
    job_id: int | None = None,
) -> TiqoraApprovalEvent:
    """Appends one audit row (``detail`` is stored as JSON: ids, keys and statuses only)."""
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"unknown audit action: {action!r}")
    row = TiqoraApprovalEvent(
        approval_id=approval.id,
        ticket_id=approval.ticket_id,
        at=utc_now(),
        action=action,
        actor=actor.key,
        step_id=step_id,
        approver_id=approver_id,
        channel=channel,
        job_id=job_id,
        detail=json.dumps(detail, sort_keys=True) if detail else None,
    )
    session.add(row)
    await session.flush()
    return row


async def emit(
    session: AsyncSession, approval: TiqoraApproval, event_name: str, **payload: Any
) -> None:
    """Writes one outbox event (webhooks + SSE through the existing drain).

    The payload always has the request's identity and status; *payload* adds ``step_id``,
    ``decision``, ``channel`` or ``reason``. Skipped for a request whose ticket was deleted
    (nothing to fan out to).
    """
    unknown = set(payload) - PAYLOAD_KEYS
    if unknown:
        raise ValueError(f"outbox payload keys not allowed: {sorted(unknown)}")
    if approval.reason == "ticket_deleted":
        return
    body: dict[str, Any] = {
        "approval_id": approval.id,
        "approval_key": approval.approval_key,
        "subject_type": approval.subject_type,
        "subject_ref": approval.subject_ref,
        "status": approval.status,
    }
    body.update({k: v for k, v in payload.items() if v is not None})
    await _emit_event(session, event_name, approval.ticket_id, body)


async def _ticket_exists(session: AsyncSession, ticket_id: int) -> bool:
    row = await session.execute(text("SELECT id FROM ticket WHERE id = :t"), {"t": ticket_id})
    return row.first() is not None


async def history(
    session: AsyncSession,
    approval: TiqoraApproval,
    verb: str,
    *,
    actor: Actor,
    extra: str | None = None,
    settings: ApprovalSettings,
) -> None:
    """Adds a ``Misc`` ticket history line (Znuny shows the first ``%%`` value verbatim).

    ``Approval #42 (default): requested``, ``Approval #42: voted approved``,
    ``Approval #42: decided rejected (timeout)``, ``Approval #42: cancelled``. Skipped when
    ``history_entries`` is off or the ticket row is gone (``history_add`` raises on that).
    """
    if verb not in _HISTORY_VERBS:
        raise ValueError(f"unknown history verb: {verb!r}")
    if not settings.history_entries or not await _ticket_exists(session, approval.ticket_id):
        return
    if verb == "requested":
        name = f"Approval #{approval.id} ({approval.approval_key}): requested"
    else:
        name = f"Approval #{approval.id}: {verb}" + (f" {extra}" if extra else "")
    await history_add(
        session,
        ticket_id=approval.ticket_id,
        history_type="Misc",
        name=name,
        user_id=actor.write_user_id,
    )
