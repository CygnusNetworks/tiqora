"""The one seam between change management and the approval engine (``tiqora.approvals``).

Every approval call of this package goes through ``ChangeApprovalPort``: the transition service
stays engine-free and fast to test (the in-memory fake lives in ``tests/_change_fixtures.py``), and
the real adapter is one module (Task 17). Until it registers itself, ``get_approval_port()``
answers with ``UnavailableApprovalPort``.

The outcome comes back the other way: ``tiqora.approvals`` runs ``on_decided`` in job
``core.approval_hook`` after the deciding commit; Task 17 registers the listener that calls
``transitions.on_approval_outcome``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import (
    Actor,
    ApprovalDefinition,
    ApprovalInfo,
    ApprovalStatus,
    Principal,
)
from tiqora.itsm.change.errors import ChangeError

APPROVAL_KEY: Final = "change_cab"
SUBJECT_TYPE: Final = "itsm.change"


@dataclass(frozen=True, slots=True)
class ApprovalSnapshot:
    """``ApprovalInfo`` plus the change's approval round (``int(subject_ref)``)."""

    approval_id: int
    ticket_id: int
    approval_key: str
    status: ApprovalStatus
    reason: str | None
    current_stage: int | None
    created_at: datetime
    decided_at: datetime | None
    round: int
    #: None until the approval engine exposes vote counts
    votes_total: int | None = None
    votes_approved: int | None = None
    votes_rejected: int | None = None

    @classmethod
    def from_info(cls, info: ApprovalInfo, *, round: int) -> ApprovalSnapshot:
        return cls(
            approval_id=info.approval_id,
            ticket_id=info.ticket_id,
            approval_key=info.approval_key,
            status=info.status,
            reason=info.reason,
            current_stage=info.current_stage,
            created_at=info.created_at,
            decided_at=info.decided_at,
            round=round,
        )


class ChangeApprovalPort(Protocol):
    async def request(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        round: int,
        title: str,
        description: str,
        definition: ApprovalDefinition,
        actor: Actor,
        requester: Principal,
        allow_self_approval: bool,
    ) -> ApprovalSnapshot:
        """Open the CAB approval of ``round`` (pending, or decided at once, e.g. no approvers)."""
        ...

    async def auto_decision(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        round: int,
        title: str,
        actor: Actor,
    ) -> ApprovalSnapshot:
        """Leave an approved trail without voting (template or low risk): reason ``"auto"``."""
        ...

    async def cancel(
        self, session: AsyncSession, *, approval_id: int, actor: Actor, reason: str
    ) -> None:
        """Cancel a pending approval; an already decided one stays as it is (no error)."""
        ...

    async def snapshot(self, session: AsyncSession, *, approval_id: int) -> ApprovalSnapshot | None:
        """None when the set does not exist (any more: GDPR may have erased it)."""
        ...


class UnavailableApprovalPort:
    """Used while no real adapter is registered: every call is a clear 409."""

    @staticmethod
    def _unavailable() -> ChangeError:
        return ChangeError("approvals_unavailable", 409)

    async def request(self, session: AsyncSession, **_: object) -> ApprovalSnapshot:
        raise self._unavailable()

    async def auto_decision(self, session: AsyncSession, **_: object) -> ApprovalSnapshot:
        raise self._unavailable()

    async def cancel(self, session: AsyncSession, **_: object) -> None:
        raise self._unavailable()

    async def snapshot(self, session: AsyncSession, **_: object) -> ApprovalSnapshot | None:
        raise self._unavailable()


_port: ChangeApprovalPort | None = None


def get_approval_port() -> ChangeApprovalPort:
    return _port if _port is not None else UnavailableApprovalPort()


def set_approval_port_for_tests(port: ChangeApprovalPort | None) -> None:
    global _port
    _port = port
