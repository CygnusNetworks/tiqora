"""Approval sweeper (A12): deadlines, reminders, escalation, invalid approvers, link purge.

One tick runs five phases. Every item is handled in its own short transaction whose first
statement is ``SELECT … FOR UPDATE`` on the request (lock order: request, then steps/approvers/
tokens - the same as a vote); the candidate selection runs in a session of its own that is
closed before any item starts, so the state is re-checked after the lock. A failing item is
logged and rolled back, the tick goes on. ``approval_changed`` is published after each item's
commit (best-effort, to the agents whose inbox changed).

Timestamps are naive UTC (``now`` is injectable for tests and truncated to whole seconds, the
precision of a MariaDB ``DATETIME``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Final, Literal

import redis.asyncio as redis
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.approvals import audit, rules, service, tokens
from tiqora.approvals.contract import (
    EVENT_ESCALATED,
    Actor,
    ApprovalError,
    ResolveContext,
    StepSpec,
)
from tiqora.approvals.models import TiqoraApproval, TiqoraApprovalApprover, TiqoraApprovalStep
from tiqora.approvals.notify import enqueue_notification
from tiqora.approvals.resolve import _customer_rows, resolve_entries  # noqa: PLC2701
from tiqora.approvals.settings import ApprovalSettings, load_approval_settings
from tiqora.config import Settings
from tiqora.db.legacy.ticket import Ticket
from tiqora.db.legacy.user import Users
from tiqora.events.pubsub import publish_approval_changed
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry
from tiqora.jobs.service import utc_now

logger = structlog.get_logger(__name__)

_BATCH: Final = 200  # items per phase and tick
_CHUNK: Final = 500  # IN-list size
_LINK_RETENTION: Final = timedelta(days=30)
_SYSTEM = Actor.system()

type _Work[T] = Callable[[AsyncSession, TiqoraApproval, ApprovalSettings], Awaitable[T | None]]


def _chunks[T](items: Sequence[T]) -> list[Sequence[T]]:
    return [items[i : i + _CHUNK] for i in range(0, len(items), _CHUNK)]


async def _approver_state(
    session: AsyncSession, approval_id: int
) -> dict[int, tuple[str, int | None]]:
    rows = await service._locked_approvers(session, approval_id)  # noqa: SLF001
    return {a.id: (a.status, a.user_id) for a in rows}


async def _item[T](
    factory: async_sessionmaker[AsyncSession],
    approval_id: int,
    work: _Work[T],
    *,
    settings: Settings,
    redis_client: redis.Redis | None,
    pending_only: bool = True,
) -> T | None:
    """Runs *work* on the locked request in its own transaction; its result, or None.

    None when the request is gone or no longer pending, when *work* has nothing to do after the
    re-check, or when the item failed (logged, rolled back).
    """
    affected: set[int] = set()
    async with factory() as session:
        try:
            approval = await service._lock_approval(session, approval_id)  # noqa: SLF001
            if pending_only and approval.status != "pending":
                await session.rollback()
                return None
            asettings = await load_approval_settings(session, settings)
            before = await _approver_state(session, approval.id)
            result = await work(session, approval, asettings)
            if result is None:
                await session.rollback()
                return None
            after = await _approver_state(session, approval.id)
            await session.commit()
        except ApprovalError as exc:
            await session.rollback()
            logger.info("approval_sweep_item_skipped", approval_id=approval_id, code=exc.code)
            return None
        except Exception:
            await session.rollback()
            logger.exception("approval_sweep_item_failed", approval_id=approval_id)
            return None
    for approver_id, (status, user_id) in after.items():
        if user_id is not None and before.get(approver_id, (None, None))[0] != status:
            affected.add(user_id)
    if redis_client is not None and affected:
        await publish_approval_changed(redis_client, affected)
    return result


# --- 1. deadlines ---------------------------------------------------------------------------


async def _escalate(
    session: AsyncSession,
    approval: TiqoraApproval,
    step: TiqoraApprovalStep,
    spec: StepSpec,
    approvers: Sequence[TiqoraApprovalApprover],
    settings: ApprovalSettings,
    registry: Registry,
    now: datetime,
) -> list[int]:
    """Makes the escalation approvers decisive; returns their approver ids (new or upgraded).

    Never touches a principal who already voted in the step (F8), and ignores rows that are
    not pending any more. Same exclusions as at activation (requester, creator, invalid ...).
    """
    resolution = await resolve_entries(
        session,
        spec.escalation,
        ctx=ResolveContext(
            ticket_id=approval.ticket_id,
            subject_type=approval.subject_type,
            subject_ref=approval.subject_ref,
            approval_id=approval.id,
        ),
        excluded_principals=await service._excluded_keys(session, approval),  # noqa: SLF001
        allow_customers=service._allow_customers(approval, settings, registry),  # noqa: SLF001
        source_kind_override="escalation",
        registry=registry,
    )
    for principal, why in resolution.excluded:
        who: dict[str, object] = (
            {"user_id": principal.user_id} if principal.kind == "agent" else {"kind": "customer"}
        )
        await audit.record(
            session,
            approval=approval,
            action="approver_excluded",
            actor=_SYSTEM,
            step_id=step.id,
            detail={**who, "reason": why},
        )
    await service._audit_unresolved(session, approval, step, resolution, actor=_SYSTEM)  # noqa: SLF001
    voted = await service._voters_in_step(session, step.id)  # noqa: SLF001
    rows = {a.principal: a for a in approvers if a.step_id == step.id}
    decisive: list[int] = []
    for resolved in resolution.approvers:
        key = resolved.principal.key
        if key in voted:
            continue
        row = rows.get(key)
        if row is None:
            new_id = await service._add_approver(  # noqa: SLF001
                session, approval, step, resolved, actor=_SYSTEM, now=now
            )
            row = await session.get(TiqoraApprovalApprover, new_id)
            assert row is not None  # noqa: S101 - just flushed
            rows[key] = row
        elif row.status != "pending":
            continue
        row.decisive = True
        decisive.append(row.id)
    await session.flush()
    return decisive


async def _overdue(
    session: AsyncSession,
    approval: TiqoraApproval,
    settings: ApprovalSettings,
    *,
    step_id: int,
    now: datetime,
    registry: Registry,
) -> str | None:
    """One step past its deadline: notify, escalate or decide it by ``on_timeout``."""
    step = next(
        (s for s in await service._locked_steps(session, approval.id) if s.id == step_id),  # noqa: SLF001
        None,
    )
    if (
        step is None
        or step.status != "active"
        or step.overdue_at is not None
        or step.due_at is None
        or step.due_at > now
    ):
        return None
    spec = StepSpec.model_validate_json(step.spec)
    approvers = await service._locked_approvers(session, approval.id, step_ids=[step.id])  # noqa: SLF001
    pending = [a for a in approvers if a.status == "pending"]
    step.overdue_at = now

    if spec.on_timeout == "escalate":
        decisive = await _escalate(
            session, approval, step, spec, approvers, settings, registry, now
        )
        if decisive:
            await audit.record(
                session,
                approval=approval,
                action="escalated",
                actor=_SYSTEM,
                step_id=step.id,
                detail={"approvers": len(decisive)},
            )
            await audit.emit(session, approval, EVENT_ESCALATED, step_id=step.id)
            await enqueue_notification(
                session,
                approval_id=approval.id,
                kind="escalation",
                approver_ids=decisive,
                registry=registry,
            )
            return "escalated"
        # nobody to escalate to: the deadline is still reported, like on_timeout "notify"
    elif spec.on_timeout in ("reject", "approve"):
        outcome: Literal["approved", "rejected"] = (
            "rejected" if spec.on_timeout == "reject" else "approved"
        )
        await audit.record(
            session,
            approval=approval,
            action="timeout_decided",
            actor=_SYSTEM,
            step_id=step.id,
            detail={"decision": outcome},
        )
        await service._close_step(  # noqa: SLF001
            session,
            approval,
            step,
            outcome,
            "timeout",
            settings=settings,
            registry=registry,
            actor=_SYSTEM,
            now=now,
        )
        return "timeout_decided"

    await audit.record(
        session,
        approval=approval,
        action="overdue",
        actor=_SYSTEM,
        step_id=step.id,
        detail={"on_timeout": spec.on_timeout, "pending": len(pending)},
    )
    await enqueue_notification(
        session,
        approval_id=approval.id,
        kind="overdue",
        approver_ids=[a.id for a in pending],
        include_requester=approval.requester != "system",
        registry=registry,
    )
    return "overdue"


# --- 2. reminders ---------------------------------------------------------------------------


async def _remind(
    session: AsyncSession,
    approval: TiqoraApproval,
    settings: ApprovalSettings,
    *,
    step_id: int,
    now: datetime,
    registry: Registry,
) -> int | None:
    if settings.reminder_interval_hours <= 0:
        return None
    step = next(
        (s for s in await service._locked_steps(session, approval.id) if s.id == step_id),  # noqa: SLF001
        None,
    )
    if step is None or step.status != "active" or step.reminder_count >= settings.reminder_max:
        return None
    stamps = [t for t in (step.last_reminder_at, step.activated_at) if t is not None]
    since = max(stamps) if stamps else None
    if since is None or since + timedelta(hours=settings.reminder_interval_hours) > now:
        return None
    pending = [
        a.id
        for a in await service._locked_approvers(session, approval.id, step_ids=[step.id])  # noqa: SLF001
        if a.status == "pending"
    ]
    if not pending:
        return None
    step.last_reminder_at = now
    step.reminder_count += 1
    await audit.record(
        session,
        approval=approval,
        action="reminded",
        actor=_SYSTEM,
        step_id=step.id,
        detail={"count": step.reminder_count, "recipients": len(pending)},
    )
    await enqueue_notification(
        session, approval_id=approval.id, kind="reminder", approver_ids=pending, registry=registry
    )
    return len(pending)


# --- 3. invalid approvers -------------------------------------------------------------------


async def _invalid_principals(
    session: AsyncSession, rows: Sequence[tuple[str, int | None, str | None]]
) -> set[str]:
    """Principal keys among ``(key, user_id, customer_login)`` that can no longer approve.

    Agents whose ``users`` row is gone or not valid, customers whose ``customer_user`` row is
    gone or not valid. Looked up with IN lists (no join with the ``tiqora_*`` tables).
    """
    agents = sorted({uid for _, uid, _ in rows if uid is not None})
    valid_agents: set[int] = set()
    for chunk in _chunks(agents):
        found = await session.execute(
            select(Users.id).where(Users.id.in_(chunk), Users.valid_id == 1)
        )
        valid_agents.update(int(i) for i in found.scalars())
    logins = [login for _, _, login in rows if login is not None]
    customers = await _customer_rows(session, logins) if logins else {}
    invalid: set[str] = set()
    for key, uid, login in rows:
        if uid is not None and uid not in valid_agents:
            invalid.add(key)
        elif login is not None:
            found_row = customers.get(login.lower())
            if found_row is None or found_row[1] != 1:
                invalid.add(key)
    return invalid


async def _drop_invalid(
    session: AsyncSession,
    approval: TiqoraApproval,
    settings: ApprovalSettings,
    *,
    registry: Registry,
    now: datetime,
) -> int | None:
    """Removes pending approvers that became invalid and re-evaluates their steps."""
    steps = [
        s
        for s in await service._locked_steps(session, approval.id)  # noqa: SLF001
        if s.status == "active"
    ]
    approvers = await service._locked_approvers(session, approval.id)  # noqa: SLF001
    active = {s.id for s in steps}
    candidates = [a for a in approvers if a.status == "pending" and a.step_id in active]
    gone = await _invalid_principals(
        session, [(a.principal, a.user_id, a.customer_login) for a in candidates]
    )
    dropped = [a for a in candidates if a.principal in gone]
    if not dropped:
        return None
    for row in dropped:
        row.status = "removed"
    await session.flush()
    await tokens.revoke_links(session, approver_ids=[a.id for a in dropped])
    for row in dropped:
        await audit.record(
            session,
            approval=approval,
            action="approver_removed",
            actor=_SYSTEM,
            step_id=row.step_id,
            approver_id=row.id,
            detail={"reason": "invalid"},
        )
    touched = {a.step_id for a in dropped}
    for step in steps:
        if step.id not in touched or approval.status != "pending" or step.status != "active":
            continue
        spec = StepSpec.model_validate_json(step.spec)
        voters = await service._voters(  # noqa: SLF001
            session,
            step,
            await service._locked_approvers(session, approval.id),  # noqa: SLF001
        )
        outcome = rules.evaluate_step(step.rule, step.quorum, voters)  # type: ignore[arg-type]
        reason = "votes"
        if outcome == "pending" and not voters:
            outcome = "approved" if spec.on_no_approver == "approve" else "rejected"
            reason = "no_approvers"
        if outcome != "pending":
            await service._close_step(  # noqa: SLF001
                session,
                approval,
                step,
                outcome,
                reason,
                settings=settings,
                registry=registry,
                actor=_SYSTEM,
                now=now,
            )
    return len(dropped)


# --- candidate selection --------------------------------------------------------------------


async def _overdue_candidates(
    factory: async_sessionmaker[AsyncSession], now: datetime
) -> list[tuple[int, int]]:
    async with factory() as session:
        rows = await session.execute(
            select(TiqoraApprovalStep.id, TiqoraApprovalStep.approval_id)
            .where(
                TiqoraApprovalStep.status == "active",
                TiqoraApprovalStep.due_at.is_not(None),
                TiqoraApprovalStep.due_at <= now,
                TiqoraApprovalStep.overdue_at.is_(None),
            )
            .order_by(TiqoraApprovalStep.id)
            .limit(_BATCH)
        )
        return [(int(s), int(a)) for s, a in rows]


async def _reminder_candidates(
    factory: async_sessionmaker[AsyncSession], settings: ApprovalSettings, now: datetime
) -> list[tuple[int, int]]:
    if settings.reminder_interval_hours <= 0 or settings.reminder_max <= 0:
        return []
    cutoff = now - timedelta(hours=settings.reminder_interval_hours)
    async with factory() as session:
        rows = await session.execute(
            select(TiqoraApprovalStep.id, TiqoraApprovalStep.approval_id)
            .where(
                TiqoraApprovalStep.status == "active",
                TiqoraApprovalStep.reminder_count < settings.reminder_max,
                TiqoraApprovalStep.activated_at.is_not(None),
                TiqoraApprovalStep.activated_at <= cutoff,
                func.coalesce(TiqoraApprovalStep.last_reminder_at, TiqoraApprovalStep.activated_at)
                <= cutoff,
            )
            .order_by(TiqoraApprovalStep.id)
            .limit(_BATCH)
        )
        return [(int(s), int(a)) for s, a in rows]


async def _invalid_candidates(factory: async_sessionmaker[AsyncSession]) -> list[int]:
    """Approvals with a pending approver whose agent/customer user is gone or invalid."""
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    TiqoraApprovalApprover.approval_id,
                    TiqoraApprovalApprover.principal,
                    TiqoraApprovalApprover.user_id,
                    TiqoraApprovalApprover.customer_login,
                ).where(TiqoraApprovalApprover.status == "pending")
            )
        ).all()
        invalid = await _invalid_principals(session, [(p, u, c) for _, p, u, c in rows])
    return sorted({int(a) for a, p, _, _ in rows if p in invalid})[:_BATCH]


async def _orphan_candidates(factory: async_sessionmaker[AsyncSession]) -> list[int]:
    """Pending requests whose ticket row no longer exists (keyset-paged, IN lists)."""
    found: list[int] = []
    after = 0
    async with factory() as session:
        while len(found) < _BATCH:
            rows = (
                await session.execute(
                    select(TiqoraApproval.id, TiqoraApproval.ticket_id)
                    .where(TiqoraApproval.status == "pending", TiqoraApproval.id > after)
                    .order_by(TiqoraApproval.id)
                    .limit(_CHUNK)
                )
            ).all()
            if not rows:
                break
            after = int(rows[-1][0])
            ticket_ids = sorted({int(t) for _, t in rows})
            present = set(
                (await session.execute(select(Ticket.id).where(Ticket.id.in_(ticket_ids))))
                .scalars()
                .all()
            )
            found.extend(int(a) for a, t in rows if int(t) not in present)
    return found[:_BATCH]


# --- tick -----------------------------------------------------------------------------------


async def run_approvals_sweep(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime | None = None,
    redis_client: redis.Redis | None = None,
) -> dict[str, int]:
    """One sweep: cancel orphans, deadlines, reminders, invalid approvers, link purge.

    Returns ``overdue`` (steps only reported), ``escalated``, ``timeout_decided``, ``reminded``
    (steps reminded), ``removed`` (approver rows), ``cancelled`` and ``links_purged``.
    """
    moment = (now if now is not None else utc_now()).replace(microsecond=0)
    registry = get_registry()
    counts = {
        "overdue": 0,
        "escalated": 0,
        "timeout_decided": 0,
        "reminded": 0,
        "removed": 0,
        "cancelled": 0,
        "links_purged": 0,
    }
    async with session_factory() as session:
        asettings = await load_approval_settings(session, settings)

    # 4 first: later phases then never meet a request whose ticket is gone
    async def cancel(
        session: AsyncSession, approval: TiqoraApproval, loaded: ApprovalSettings
    ) -> bool | None:
        await service.cancel_approval(
            session,
            approval.id,
            actor=_SYSTEM,
            reason="ticket_deleted",
            check_permission=False,
            settings=settings,
            registry=registry,
        )
        return True

    for approval_id in await _orphan_candidates(session_factory):
        done = await _item(
            session_factory, approval_id, cancel, settings=settings, redis_client=redis_client
        )
        counts["cancelled"] += 1 if done else 0

    for step_id, approval_id in await _overdue_candidates(session_factory, moment):

        async def overdue(
            session: AsyncSession,
            approval: TiqoraApproval,
            loaded: ApprovalSettings,
            step_id: int = step_id,
        ) -> str | None:
            return await _overdue(
                session, approval, loaded, step_id=step_id, now=moment, registry=registry
            )

        tag = await _item(
            session_factory, approval_id, overdue, settings=settings, redis_client=redis_client
        )
        if tag is not None:
            counts[tag] += 1

    for step_id, approval_id in await _reminder_candidates(session_factory, asettings, moment):

        async def remind(
            session: AsyncSession,
            approval: TiqoraApproval,
            loaded: ApprovalSettings,
            step_id: int = step_id,
        ) -> int | None:
            return await _remind(
                session, approval, loaded, step_id=step_id, now=moment, registry=registry
            )

        sent = await _item(
            session_factory, approval_id, remind, settings=settings, redis_client=redis_client
        )
        counts["reminded"] += 1 if sent else 0

    for approval_id in await _invalid_candidates(session_factory):

        async def drop(
            session: AsyncSession, approval: TiqoraApproval, loaded: ApprovalSettings
        ) -> int | None:
            return await _drop_invalid(session, approval, loaded, registry=registry, now=moment)

        removed = await _item(
            session_factory, approval_id, drop, settings=settings, redis_client=redis_client
        )
        counts["removed"] += removed or 0

    async with session_factory() as session:
        counts["links_purged"] = await tokens.purge_links(
            session, older_than=moment - _LINK_RETENTION
        )
        await session.commit()
    return counts
