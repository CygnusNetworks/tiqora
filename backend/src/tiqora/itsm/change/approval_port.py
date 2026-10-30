"""The one seam between change management and the approval engine (``tiqora.approvals``).

Every approval call of this package goes through ``ChangeApprovalPort``: the transition service
stays engine-free and fast to test (the in-memory fake lives in ``tests/_change_fixtures.py``), and
the real adapter is this module (``EngineApprovalPort``, cross-plan contract X1 mapped to the
engine's names). ``get_approval_port()`` answers with it unless a test installed another port.

X1 in the engine's names: (a) request = ``service.request_approval`` (subject type
``itsm.change``, key ``change_cab``, the round as ``subject_ref``, superseding a pending older
round) and ``service.record_auto_decision`` (approved, reason ``auto``); (b) cancel =
``service.cancel_approval`` (engine reason ``cancelled``; the change keeps its own reason in its
event row: the engine stores no free text); (c) read = ``service.get_info``; (d) outcome = the
subject type's ``on_decided``, run by job ``core.approval_hook`` after the deciding commit
(``transitions.on_cab_decided``); (e) the voting UI is the engine's ``TicketApprovalsPanel``.
Every ``ApprovalError`` becomes ``ChangeError("approval_request_failed", 409)`` with the engine's
code under ``engine_code`` (ruling CX21; a detail key ``code`` would be shadowed).

Lock order (contract): the caller holds its change row; the engine then locks ``tiqora_approval``;
the caller writes the Znuny ticket row only after the port returned.

Also here, for the subject type: :func:`describe_change` (plain lines for approvers, ruling N2)
and the resolver ``itsm.service_owner`` (ruling F14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals import service
from tiqora.approvals.contract import (
    Actor,
    ApprovalDefinition,
    ApprovalError,
    ApprovalInfo,
    ApprovalStatus,
    ApproverResolver,
    Principal,
    ResolveContext,
)
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeService,
)
from tiqora.itsm.models import TiqoraItsmServiceMeta

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


#: the only cancel reason the engine takes from a consumer (it stores no free text)
_ENGINE_CANCEL_REASON: Final = "cancelled"


def _failed(exc: ApprovalError) -> ChangeError:
    return ChangeError(
        "approval_request_failed", 409, engine_code=exc.code, message=exc.detail or exc.code
    )


def _round(info: ApprovalInfo) -> int | None:
    """The change round of a set, or None when the set is no CAB approval of a change."""
    ref = info.subject_ref
    if info.subject_type != SUBJECT_TYPE or info.approval_key != APPROVAL_KEY or ref is None:
        return None
    return int(ref) if ref.isascii() and ref.isdigit() else None


def _snapshot(info: ApprovalInfo, round: int) -> ApprovalSnapshot:
    return ApprovalSnapshot.from_info(info, round=round)


class EngineApprovalPort:
    """``ChangeApprovalPort`` on the shared approval engine. Flushes, never commits."""

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
        try:
            info = await service.request_approval(
                session,
                ticket_id=ticket_id,
                subject_type=SUBJECT_TYPE,
                title=title,
                actor=actor,
                requester=requester,
                subject_ref=str(round),
                approval_key=APPROVAL_KEY,
                description=description or None,
                definition=definition,
                allow_self_approval=allow_self_approval,
            )
        except ApprovalError as exc:
            raise _failed(exc) from exc
        return _snapshot(info, round)

    async def auto_decision(
        self,
        session: AsyncSession,
        *,
        ticket_id: int,
        round: int,
        title: str,
        actor: Actor,
    ) -> ApprovalSnapshot:
        try:
            info = await service.record_auto_decision(
                session,
                ticket_id=ticket_id,
                subject_type=SUBJECT_TYPE,
                title=title,
                actor=actor,
                status="approved",
                reason="auto",
                subject_ref=str(round),
                approval_key=APPROVAL_KEY,
            )
        except ApprovalError as exc:
            raise _failed(exc) from exc
        return _snapshot(info, round)

    async def cancel(
        self, session: AsyncSession, *, approval_id: int, actor: Actor, reason: str
    ) -> None:
        _ = reason  # kept by the caller in its own event row
        try:
            await service.cancel_approval(
                session,
                approval_id,
                actor=actor,
                reason=_ENGINE_CANCEL_REASON,
                check_permission=False,
            )
        except ApprovalError as exc:
            if exc.code in ("approval_closed", "approval_not_found"):
                return  # decided already, or erased: nothing left to cancel
            raise _failed(exc) from exc

    async def snapshot(self, session: AsyncSession, *, approval_id: int) -> ApprovalSnapshot | None:
        try:
            info = await service.get_info(session, approval_id)
        except ApprovalError as exc:
            if exc.code == "approval_not_found":
                return None
            raise _failed(exc) from exc
        round = _round(info)
        return None if round is None else _snapshot(info, round)


_port: ChangeApprovalPort | None = None


def get_approval_port() -> ChangeApprovalPort:
    return _port if _port is not None else EngineApprovalPort()


def set_approval_port_for_tests(port: ChangeApprovalPort | None) -> None:
    global _port
    _port = port


# --- subject type helpers ----------------------------------------------------------------------

_TYPE_TEXT: Final = {"standard": "Standard", "normal": "Normal", "emergency": "Notfall"}
_RISK_TEXT: Final = {
    "low": "niedrig",
    "medium": "mittel",
    "high": "hoch",
    "very_high": "sehr hoch",
}


def _when(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


async def describe_change(session: AsyncSession, approval_id: int) -> list[str]:
    """Plain lines for approvers and mails (ruling N2): type, risk, window (UTC), affected
    services and CIs as counts. No names or logins: approvers may lack access to the ticket."""
    try:
        info = await service.get_info(session, approval_id)
    except ApprovalError:
        return []
    if _round(info) is None:
        return []
    row = await session.get(TiqoraItsmChange, info.ticket_id)
    if row is None:
        return []
    level = row.risk_override_level or row.risk_level
    if row.planned_start is not None and row.planned_end is not None:
        window = f"{_when(row.planned_start)} – {_when(row.planned_end)} (UTC)"
    else:
        window = "offen"
    rows = await session.execute(
        select(TiqoraItsmChangeImpact.kind, func.count())
        .where(TiqoraItsmChangeImpact.ticket_id == row.ticket_id)
        .group_by(TiqoraItsmChangeImpact.kind)
    )
    counts: dict[str, int] = {str(kind): int(n) for kind, n in rows}
    return [
        f"Change-Art: {_TYPE_TEXT.get(row.change_type, row.change_type)}",
        f"Risiko: {_RISK_TEXT.get(level or '', 'nicht bewertet')}",
        f"Geplant: {window}",
        f"Betroffene Services: {counts.get('service', 0)}",
        f"Betroffene CIs: {counts.get('ci', 0)}",
    ]


# Znuny's root@localhost: never a person who approves (as the engine's own resolvers)
_SYSTEM_USER_ID: Final = 1


async def _resolve_service_owners(session: AsyncSession, ctx: ResolveContext) -> list[Principal]:
    """Owners of the change ticket's service and of the change's extra services."""
    service_ids: set[int] = set()
    ticket_service = (
        await session.execute(
            text("SELECT service_id FROM ticket WHERE id = :t"), {"t": ctx.ticket_id}
        )
    ).scalar_one_or_none()
    if ticket_service:
        service_ids.add(int(ticket_service))
    extra = await session.execute(
        select(TiqoraItsmChangeService.service_id).where(
            TiqoraItsmChangeService.ticket_id == ctx.ticket_id
        )
    )
    service_ids.update(int(r[0]) for r in extra)
    if not service_ids:
        return []
    owners = await session.execute(
        select(TiqoraItsmServiceMeta.owner_user_id)
        .where(TiqoraItsmServiceMeta.service_id.in_(sorted(service_ids)))
        .distinct()
    )
    ids = sorted({int(o) for (o,) in owners if o is not None and int(o) > _SYSTEM_USER_ID})
    return [Principal.agent(i) for i in ids]


def service_owner_resolver() -> ApproverResolver:
    return ApproverResolver(
        key="itsm.service_owner",
        title_key="approvals.resolver.itsm_service_owner",
        resolve=_resolve_service_owners,
    )
