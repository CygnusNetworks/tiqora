"""Load ticket data for the fact builder and write fact + event rows.

Pure computation lives in :mod:`tiqora.reporting.facts` and
:mod:`tiqora.reporting.history_events`; this module only does the I/O. Joins between
Znuny tables use integer keys only; ``tiqora_*`` tables are reached by integer ids
loaded first (prod MariaDB mixes collations, so no string JOIN across the two worlds).
"""

from __future__ import annotations

import asyncio
import dataclasses
import weakref
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.ai.models import (
    DRAFT_STATUS_ACCEPTED,
    SOURCE_AUTO,
    TiqoraAiArticleOrigin,
    TiqoraAiDraft,
    TiqoraAiTicketState,
    TiqoraAiTriage,
)
from tiqora.db.legacy.article import Article, ArticleSenderType, CommunicationChannel
from tiqora.db.legacy.dynamic_field import DynamicField, DynamicFieldValue
from tiqora.db.legacy.misc import TimeAccounting
from tiqora.db.legacy.ticket import (
    Ticket,
    TicketHistory,
    TicketHistoryType,
    TicketState,
    TicketStateType,
)
from tiqora.db.tiqora.models import TiqoraReportTicketEvent, TiqoraReportTicketFact
from tiqora.jobs.service import utc_now
from tiqora.process.ticket_state import PROCESS_ID_DF_NAME
from tiqora.reporting.facts import AiFlags, FactInputs, compute_fact
from tiqora.reporting.history_events import ArticleRow, HistoryRow, TicketRow, derive_events
from tiqora.reporting.working_time import CalendarResolver
from tiqora.znuny.sysconfig import SysConfig

FACT_CHUNK_SIZE: Final = 200
_IN_CHUNK: Final = 1000
_ORIGIN_MANUAL_ACCEPT: Final = "manual_accept"  # human accepted an AI draft

_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def fact_write_lock() -> asyncio.Lock:
    """The fact-write lock of the running event loop.

    Serialises fact writes of the tick loop and the backfill job inside one worker
    process. One lock per loop: a module-level ``Lock`` raises "bound to a different
    event loop" when a second loop (every pytest-asyncio test) contends for it.
    """
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


@dataclass(frozen=True, slots=True)
class RebuildResult:
    built: int
    deleted: int  # fact rows removed for tickets that no longer exist


def _chunks[T](items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def load_fact_inputs(
    session: AsyncSession,
    ticket_ids: Sequence[int],
    *,
    resolver: CalendarResolver,
    now: datetime,
) -> dict[int, FactInputs]:
    """Inputs for every given ticket that exists (missing ids are absent from the result)."""
    ids = sorted({int(i) for i in ticket_ids})
    if not ids:
        return {}
    out: dict[int, FactInputs] = {}
    state_types = {
        int(sid): str(name)
        for sid, name in (
            await session.execute(
                select(TicketState.id, TicketStateType.name).join(
                    TicketStateType, TicketStateType.id == TicketState.type_id
                )
            )
        ).all()
    }
    storage_tz = await resolver.storage_time_zone()
    process_field_id = (
        await session.execute(
            select(DynamicField.id).where(
                DynamicField.name == PROCESS_ID_DF_NAME, DynamicField.object_type == "Ticket"
            )
        )
    ).scalar_one_or_none()

    for chunk in _chunks(ids, _IN_CHUNK):
        chunk_ids = list(chunk)
        tickets = {
            t.id: TicketRow(
                id=t.id,
                tn=t.tn,
                create_time=t.create_time,
                create_by=t.create_by,
                queue_id=t.queue_id,
                state_id=t.ticket_state_id,
                priority_id=t.ticket_priority_id,
                type_id=t.type_id,
                service_id=t.service_id,
                sla_id=t.sla_id,
                user_id=t.user_id,
                responsible_user_id=t.responsible_user_id,
                customer_id=t.customer_id,
                customer_user_id=t.customer_user_id,
                archive_flag=t.archive_flag,
            )
            for t in (await session.execute(select(Ticket).where(Ticket.id.in_(chunk_ids))))
            .scalars()
            .all()
        }
        if not tickets:
            continue
        found = sorted(tickets)

        history: dict[int, list[HistoryRow]] = defaultdict(list)
        for h, type_name in (
            await session.execute(
                select(TicketHistory, TicketHistoryType.name)
                .join(TicketHistoryType, TicketHistoryType.id == TicketHistory.history_type_id)
                .where(TicketHistory.ticket_id.in_(found))
                .order_by(TicketHistory.id)
            )
        ).all():
            history[h.ticket_id].append(
                HistoryRow(
                    id=h.id,
                    history_type=str(type_name),
                    name=h.name,
                    article_id=h.article_id,
                    queue_id=h.queue_id,
                    state_id=h.state_id,
                    priority_id=h.priority_id,
                    owner_id=h.owner_id,
                    type_id=h.type_id,
                    create_time=h.create_time,
                    create_by=h.create_by,
                    change_time=h.change_time,
                )
            )

        articles: dict[int, list[ArticleRow]] = defaultdict(list)
        article_ticket: dict[int, int] = {}
        for a, sender, channel in (
            await session.execute(
                select(Article, ArticleSenderType.name, CommunicationChannel.name)
                .join(ArticleSenderType, ArticleSenderType.id == Article.article_sender_type_id)
                .join(
                    CommunicationChannel,
                    CommunicationChannel.id == Article.communication_channel_id,
                )
                .where(Article.ticket_id.in_(found))
                .order_by(Article.create_time, Article.id)
            )
        ).all():
            article_ticket[a.id] = a.ticket_id
            articles[a.ticket_id].append(
                ArticleRow(
                    id=a.id,
                    sender_type=str(sender),
                    visible=bool(a.is_visible_for_customer),
                    channel=str(channel),
                    create_time=a.create_time,
                    create_by=a.create_by,
                )
            )

        accounted: dict[int, Decimal] = {
            int(tid): Decimal(total)
            for tid, total in (
                await session.execute(
                    select(TimeAccounting.ticket_id, func.sum(TimeAccounting.time_unit))
                    .where(TimeAccounting.ticket_id.in_(found))
                    .group_by(TimeAccounting.ticket_id)
                )
            ).all()
            if total is not None
        }

        triaged: dict[int, bool] = {
            int(tid): bool(applied)
            for tid, applied in (
                await session.execute(
                    select(TiqoraAiTriage.ticket_id, TiqoraAiTriage.queue_applied).where(
                        TiqoraAiTriage.ticket_id.in_(found)
                    )
                )
            ).all()
        }
        auto_replied: set[int] = set()
        accepted_by_origin: set[int] = set()
        article_ids = list(article_ticket)
        for article_chunk in _chunks(article_ids, _IN_CHUNK):
            for aid, source in (
                await session.execute(
                    select(TiqoraAiArticleOrigin.article_id, TiqoraAiArticleOrigin.source).where(
                        TiqoraAiArticleOrigin.article_id.in_(list(article_chunk)),
                        TiqoraAiArticleOrigin.source.in_([SOURCE_AUTO, _ORIGIN_MANUAL_ACCEPT]),
                    )
                )
            ).all():
                if source == SOURCE_AUTO:
                    auto_replied.add(article_ticket[int(aid)])
                else:
                    accepted_by_origin.add(article_ticket[int(aid)])
        draft_used = {
            int(tid)
            for tid in (
                await session.execute(
                    select(TiqoraAiDraft.ticket_id)
                    .where(
                        TiqoraAiDraft.ticket_id.in_(found),
                        TiqoraAiDraft.status == DRAFT_STATUS_ACCEPTED,
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        }
        handed_off = {
            int(tid)
            for tid in (
                await session.execute(
                    select(TiqoraAiTicketState.ticket_id).where(
                        TiqoraAiTicketState.ticket_id.in_(found),
                        TiqoraAiTicketState.ai_escalated_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        }

        process_ids: dict[int, str] = {}
        if process_field_id is not None:
            process_ids = {
                int(oid): str(value)
                for oid, value in (
                    await session.execute(
                        select(DynamicFieldValue.object_id, DynamicFieldValue.value_text).where(
                            DynamicFieldValue.field_id == process_field_id,
                            DynamicFieldValue.object_id.in_(found),
                        )
                    )
                ).all()
                if value
            }

        for tid in found:
            ticket = tickets[tid]
            targets = await resolver.targets(ticket.sla_id, ticket.queue_id)
            out[tid] = FactInputs(
                ticket=ticket,
                history=history.get(tid, []),
                articles=articles.get(tid, []),
                events=derive_events(
                    ticket, history.get(tid, []), articles.get(tid, []), state_types
                ),
                state_types=state_types,
                accounted_time=accounted.get(tid, Decimal("0.00")),
                targets=targets,
                calendar=await resolver.calendar(targets.calendar_name),
                storage_tz=storage_tz,
                ai=AiFlags(
                    triaged=tid in triaged,
                    triage_queue_applied=triaged.get(tid, False),
                    auto_replied=tid in auto_replied,
                    draft_used=tid in draft_used or tid in accepted_by_origin,
                    handed_off=tid in handed_off,
                ),
                process_entity_id=process_ids.get(tid),
                now=now,
            )
    return out


async def delete_ticket_facts(session: AsyncSession, ticket_ids: Sequence[int]) -> None:
    """Remove events and facts of the tickets; flush only."""
    ids = sorted({int(i) for i in ticket_ids})
    for chunk in _chunks(ids, _IN_CHUNK):
        await session.execute(
            delete(TiqoraReportTicketEvent).where(TiqoraReportTicketEvent.ticket_id.in_(chunk))
        )
        await session.execute(
            delete(TiqoraReportTicketFact).where(TiqoraReportTicketFact.ticket_id.in_(chunk))
        )
    await session.flush()


async def rebuild_ticket_facts(
    session: AsyncSession,
    ticket_ids: Iterable[int],
    *,
    sysconfig: SysConfig | None = None,
    now: datetime | None = None,
) -> RebuildResult:
    """Recompute facts and events of the tickets; flush only, never commits.

    Tickets that no longer exist lose their fact and event rows.
    """
    ids = sorted({int(i) for i in ticket_ids})
    stamp = now if now is not None else utc_now()
    resolver = CalendarResolver(session, sysconfig if sysconfig is not None else SysConfig(session))
    built = deleted = 0
    for chunk in _chunks(ids, FACT_CHUNK_SIZE):
        chunk_ids = list(chunk)
        inputs = await load_fact_inputs(session, chunk_ids, resolver=resolver, now=stamp)
        had_fact = set(
            (
                await session.execute(
                    select(TiqoraReportTicketFact.ticket_id).where(
                        TiqoraReportTicketFact.ticket_id.in_(chunk_ids)
                    )
                )
            )
            .scalars()
            .all()
        )
        await delete_ticket_facts(session, chunk_ids)
        if inputs:
            await session.execute(
                insert(TiqoraReportTicketFact),
                [compute_fact(inp).as_insert_dict() for inp in inputs.values()],
            )
            events = [dataclasses.asdict(e) for inp in inputs.values() for e in inp.events]
            if events:
                await session.execute(insert(TiqoraReportTicketEvent), events)
        await session.flush()
        built += len(inputs)
        deleted += len(had_fact - inputs.keys())
    return RebuildResult(built=built, deleted=deleted)


async def rebuild_and_commit(
    factory: async_sessionmaker[AsyncSession],
    ticket_ids: Iterable[int],
    *,
    now: datetime | None = None,
    attempts: int = 3,
) -> RebuildResult:
    """Rebuild in chunks, each in its own session and transaction under the write lock.

    A chunk that hits an integrity error or a deadlock/lock-wait timeout (a concurrent
    writer of the same tickets) is rolled back and retried up to *attempts* times, then
    the error is raised.
    """
    ids = sorted({int(i) for i in ticket_ids})
    built = deleted = 0
    for chunk in _chunks(ids, FACT_CHUNK_SIZE):
        for attempt in range(1, attempts + 1):
            try:
                async with fact_write_lock(), factory() as session:
                    # leaving the session context without commit rolls back
                    res = await rebuild_ticket_facts(session, chunk, now=now)
                    await session.commit()
            except (IntegrityError, OperationalError):
                if attempt == attempts:
                    raise
                await asyncio.sleep(0.2 * attempt)
            else:
                built += res.built
                deleted += res.deleted
                break
    return RebuildResult(built=built, deleted=deleted)
