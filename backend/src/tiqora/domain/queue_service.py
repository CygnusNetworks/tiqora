"""Read-only queue tree with permission-filtered ticket counts."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.queue import Queue
from tiqora.db.legacy.ticket import Ticket, TicketLockType, TicketState, TicketStateType
from tiqora.domain.schemas import QueueCounts, QueueNode
from tiqora.permissions.engine import PermissionEngine

# State types considered "open" for queue views (Znuny viewable defaults).
OPEN_STATE_TYPES = frozenset({"new", "open", "pending reminder", "pending auto"})


class QueueService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._perms = PermissionEngine(session)

    async def allowed_queue_ids(self, user_id: int, perm: str = "ro") -> set[int]:
        group_ids = await self._perms.groups_for_permission(user_id, perm)
        if not group_ids:
            return set()
        result = await self._session.execute(
            select(Queue.id).where(Queue.group_id.in_(group_ids), Queue.valid_id == 1)
        )
        return set(result.scalars().all())

    async def list_queues(self, user_id: int) -> list[QueueNode]:
        """Return permission-filtered queue tree with open/locked counts."""
        allowed = await self.allowed_queue_ids(user_id, "ro")
        if not allowed:
            return []

        q_result = await self._session.execute(
            select(Queue).where(Queue.id.in_(allowed)).order_by(Queue.name)
        )
        queues = list(q_result.scalars().all())

        # Open (viewable) state ids
        open_states = await self._session.execute(
            select(TicketState.id)
            .join(TicketStateType, TicketStateType.id == TicketState.type_id)
            .where(TicketStateType.name.in_(OPEN_STATE_TYPES), TicketState.valid_id == 1)
        )
        open_state_ids = set(open_states.scalars().all())

        # "new" state ids — subset of open_state_ids, broken out for the
        # per-queue "N neu" badge (issue: new tickets were invisible under
        # the default Offen filter; the queue tree should surface them too).
        new_states = await self._session.execute(
            select(TicketState.id)
            .join(TicketStateType, TicketStateType.id == TicketState.type_id)
            .where(TicketStateType.name == "new", TicketState.valid_id == 1)
        )
        new_state_ids = set(new_states.scalars().all())

        # Lock type ids
        lock_rows = await self._session.execute(select(TicketLockType.id, TicketLockType.name))
        lock_map = {name: lid for lid, name in lock_rows.all()}
        locked_ids = {lid for name, lid in lock_map.items() if name in {"lock", "tmp_lock"}}
        unlock_id = lock_map.get("unlock")

        counts_by_queue: dict[int, QueueCounts] = defaultdict(QueueCounts)
        if open_state_ids:
            # open total per queue
            open_q = await self._session.execute(
                select(Ticket.queue_id, func.count())
                .where(
                    Ticket.queue_id.in_(allowed),
                    Ticket.ticket_state_id.in_(open_state_ids),
                    Ticket.archive_flag == 0,
                )
                .group_by(Ticket.queue_id)
            )
            for qid, cnt in open_q.all():
                counts_by_queue[qid].open = int(cnt)
                counts_by_queue[qid].total = int(cnt)

            if new_state_ids:
                new_q = await self._session.execute(
                    select(Ticket.queue_id, func.count())
                    .where(
                        Ticket.queue_id.in_(allowed),
                        Ticket.ticket_state_id.in_(new_state_ids),
                        Ticket.archive_flag == 0,
                    )
                    .group_by(Ticket.queue_id)
                )
                for qid, cnt in new_q.all():
                    counts_by_queue[qid].new = int(cnt)

            if locked_ids:
                locked_q = await self._session.execute(
                    select(Ticket.queue_id, func.count())
                    .where(
                        Ticket.queue_id.in_(allowed),
                        Ticket.ticket_state_id.in_(open_state_ids),
                        Ticket.ticket_lock_id.in_(locked_ids),
                        Ticket.archive_flag == 0,
                    )
                    .group_by(Ticket.queue_id)
                )
                for qid, cnt in locked_q.all():
                    counts_by_queue[qid].locked = int(cnt)

            if unlock_id is not None:
                unlocked_q = await self._session.execute(
                    select(Ticket.queue_id, func.count())
                    .where(
                        Ticket.queue_id.in_(allowed),
                        Ticket.ticket_state_id.in_(open_state_ids),
                        Ticket.ticket_lock_id == unlock_id,
                        Ticket.archive_flag == 0,
                    )
                    .group_by(Ticket.queue_id)
                )
                for qid, cnt in unlocked_q.all():
                    counts_by_queue[qid].unlocked = int(cnt)

        # Build tree from name hierarchy (Znuny uses ``Parent::Child``)
        nodes: dict[int, QueueNode] = {}
        by_name: dict[str, QueueNode] = {}
        for q in queues:
            parent_name = None
            if "::" in q.name:
                parent_name = q.name.rsplit("::", 1)[0]
            node = QueueNode(
                id=q.id,
                name=q.name,
                group_id=q.group_id,
                parent_name=parent_name,
                valid=q.valid_id == 1,
                counts=counts_by_queue.get(q.id, QueueCounts()),
            )
            nodes[q.id] = node
            by_name[q.name] = node

        roots: list[QueueNode] = []
        for node in nodes.values():
            if node.parent_name and node.parent_name in by_name:
                by_name[node.parent_name].children.append(node)
            else:
                roots.append(node)

        def sort_tree(items: list[QueueNode]) -> list[QueueNode]:
            items.sort(key=lambda n: n.name)
            for n in items:
                n.children = sort_tree(n.children)
            return items

        return sort_tree(roots)


def age_seconds(create_time: datetime, now: datetime | None = None) -> int:
    ref = now or datetime.now(UTC)
    ct = create_time
    if ct.tzinfo is None:
        ct = ct.replace(tzinfo=UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    return max(0, int((ref - ct).total_seconds()))
