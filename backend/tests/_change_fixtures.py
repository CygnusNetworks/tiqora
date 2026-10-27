"""Fixtures of the change module tests: an in-memory approval port instead of the engine."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import (
    Actor,
    ApprovalDefinition,
    ApprovalStatus,
    Principal,
)
from tiqora.itsm.change.approval_port import (
    APPROVAL_KEY,
    ApprovalSnapshot,
    set_approval_port_for_tests,
)

_EPOCH = datetime(2026, 1, 1, 8, 0, 0)


@dataclass(frozen=True, slots=True)
class FakeRequest:
    ticket_id: int
    round: int
    title: str
    description: str
    definition: ApprovalDefinition
    actor: Actor
    requester: Principal
    allow_self_approval: bool


@dataclass
class FakeApprovalPort:
    """Deterministic ids (1, 2, ...); tests decide the outcome with ``decide``."""

    snapshots: dict[int, ApprovalSnapshot] = field(default_factory=dict)
    requests: list[FakeRequest] = field(default_factory=list)
    auto_decisions: list[tuple[int, int]] = field(default_factory=list)  # (ticket_id, round)
    cancels: list[tuple[int, str]] = field(default_factory=list)  # (approval_id, reason)
    #: when set, ``request`` answers decided at once with this reason (e.g. "no_approvers")
    reject_at_once: str | None = None
    _next_id: int = 1

    def _new(
        self, *, ticket_id: int, round: int, status: ApprovalStatus, reason: str | None
    ) -> ApprovalSnapshot:
        approval_id = self._next_id
        self._next_id += 1
        created = _EPOCH + timedelta(minutes=approval_id)
        snap = ApprovalSnapshot(
            approval_id=approval_id,
            ticket_id=ticket_id,
            approval_key=APPROVAL_KEY,
            status=status,
            reason=reason,
            current_stage=1 if status == "pending" else None,
            created_at=created,
            decided_at=None if status == "pending" else created,
            round=round,
        )
        self.snapshots[approval_id] = snap
        return snap

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
        self.requests.append(
            FakeRequest(
                ticket_id,
                round,
                title,
                description,
                definition,
                actor,
                requester,
                allow_self_approval,
            )
        )
        if self.reject_at_once is not None:
            return self._new(
                ticket_id=ticket_id, round=round, status="rejected", reason=self.reject_at_once
            )
        return self._new(ticket_id=ticket_id, round=round, status="pending", reason=None)

    async def auto_decision(
        self, session: AsyncSession, *, ticket_id: int, round: int, title: str, actor: Actor
    ) -> ApprovalSnapshot:
        self.auto_decisions.append((ticket_id, round))
        return self._new(ticket_id=ticket_id, round=round, status="approved", reason="auto")

    async def cancel(
        self, session: AsyncSession, *, approval_id: int, actor: Actor, reason: str
    ) -> None:
        snap = self.snapshots.get(approval_id)
        self.cancels.append((approval_id, reason))
        if snap is not None and snap.status == "pending":
            self.snapshots[approval_id] = replace(
                snap,
                status="cancelled",
                reason=reason,
                current_stage=None,
                decided_at=snap.created_at,
            )

    async def snapshot(self, session: AsyncSession, *, approval_id: int) -> ApprovalSnapshot | None:
        return self.snapshots.get(approval_id)

    def decide(
        self, approval_id: int, status: ApprovalStatus, reason: str | None = None
    ) -> ApprovalSnapshot:
        """Test helper: the engine reached a final status (pending ones only)."""
        snap = self.snapshots[approval_id]
        if snap.status != "pending":
            return snap
        decided = replace(
            snap, status=status, reason=reason, current_stage=None, decided_at=snap.created_at
        )
        self.snapshots[approval_id] = decided
        return decided


@pytest.fixture
def use_fake_approval_port() -> Iterator[FakeApprovalPort]:
    port = FakeApprovalPort()
    set_approval_port_for_tests(port)
    try:
        yield port
    finally:
        set_approval_port_for_tests(None)
