"""GenericInterface debug log store.

Writes ``tiqora_gi_debug_entry`` / ``tiqora_gi_debug_content`` (Tiqora never writes Znuny's
``gi_debugger_*`` tables) and reads both sides for the debugger view. The two sides are never
joined (collation clash on MariaDB, error 1267): each is queried on its own and merged here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

import structlog
from sqlalchemy import bindparam, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.db.tiqora.models import TiqoraGiDebugContent, TiqoraGiDebugEntry
from tiqora.gi.config import DEBUG_LEVELS
from tiqora.jobs.service import utc_now

logger = structlog.get_logger(__name__)

DebugSource = Literal["tiqora", "znuny"]

_BATCH: Final = 1000
# Total order of sources inside one timestamp (keyset pagination).
_SOURCE_ORDER: Final = {"tiqora": 0, "znuny": 1}


@dataclass(frozen=True, slots=True)
class DebugEntryRow:
    source: DebugSource
    id: int
    communication_id: str
    communication_type: str
    remote_ip: str | None
    webservice_id: int
    create_time: datetime
    name: str | None
    ticket_id: int | None
    max_level: str
    can_resend: bool


@dataclass(frozen=True, slots=True)
class DebugContentRow:
    id: int
    debug_level: str
    subject: str
    content: str | None
    create_time: datetime


class DbDebugSink:
    """Implements :class:`tiqora.gi.debugger.DebugSink`.

    Every ``add`` runs in its OWN short session and commits: Znuny's ``LogAdd`` sits outside
    the business transaction, so a rolled-back ticket write still leaves its debugger trail.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def add(
        self,
        *,
        communication_id: str,
        communication_type: str,
        webservice_id: int,
        remote_ip: str | None,
        name: str | None,
        ticket_id: int | None,
        debug_level: str,
        summary: str,
        content: str,
        replay: Mapping[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        async with self._factory() as session:
            entry_id = await self._entry_id(session, communication_id)
            if entry_id is None:
                entry = TiqoraGiDebugEntry(
                    communication_id=communication_id,
                    communication_type=communication_type[:50],
                    remote_ip=remote_ip[:50] if remote_ip else None,
                    webservice_id=webservice_id,
                    create_time=now,
                    name=name[:200] if name else None,
                    ticket_id=ticket_id,
                    replay=(
                        json.dumps(replay, sort_keys=True, ensure_ascii=False, default=str)
                        if replay is not None
                        else None
                    ),
                )
                session.add(entry)
                try:
                    await session.flush()
                    entry_id = int(entry.id)
                except IntegrityError:
                    # A concurrent add created it first (unique communication_id).
                    await session.rollback()
                    entry_id = await self._entry_id(session, communication_id)
                    if entry_id is None:
                        raise
            elif ticket_id is not None:
                # The ticket id often becomes known after the first line (ticket create).
                await session.execute(
                    text(
                        "UPDATE tiqora_gi_debug_entry SET ticket_id = :t"
                        " WHERE id = :i AND ticket_id IS NULL"
                    ),
                    {"t": ticket_id, "i": entry_id},
                )
            session.add(
                TiqoraGiDebugContent(
                    entry_id=entry_id,
                    debug_level=debug_level[:50],
                    subject=summary[:255],
                    content=content.encode("utf-8"),
                    create_time=now,
                )
            )
            await session.commit()

    @staticmethod
    async def _entry_id(session: AsyncSession, communication_id: str) -> int | None:
        found = (
            await session.execute(
                select(TiqoraGiDebugEntry.id).where(
                    TiqoraGiDebugEntry.communication_id == communication_id
                )
            )
        ).scalar_one_or_none()
        return int(found) if found is not None else None


def _rank(level: str) -> int:
    return DEBUG_LEVELS.get(level, 0)


async def _max_levels(session: AsyncSession, table: str, fk: str, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            text(
                f"SELECT {fk} AS entry_id, debug_level FROM {table}"  # noqa: S608 - fixed names
                f" WHERE {fk} IN :ids GROUP BY {fk}, debug_level"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        )
    ).all()
    best: dict[int, str] = {}
    for entry_id, level in rows:
        key = int(entry_id)
        if key not in best or _rank(level) > _rank(best[key]):
            best[key] = level
    return best


def _keyset(
    side: DebugSource, before: tuple[datetime, str, int] | None
) -> tuple[str, dict[str, Any]]:
    """SQL fragment + params selecting rows strictly older than *before* for one side.

    Rows sort by ``(create_time, source, id)`` descending; *before* is the last row already
    delivered.
    """
    if before is None:
        return "", {}
    b_time, b_source, b_id = before
    mine, theirs = _SOURCE_ORDER[side], _SOURCE_ORDER.get(b_source, 0)
    if mine < theirs:
        return " AND create_time <= :b_time", {"b_time": b_time}
    if mine > theirs:
        return " AND create_time < :b_time", {"b_time": b_time}
    return (
        " AND (create_time < :b_time OR (create_time = :b_time AND id < :b_id))",
        {"b_time": b_time, "b_id": b_id},
    )


async def _search_side(
    session: AsyncSession,
    *,
    side: DebugSource,
    webservice_id: int,
    communication_type: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    remote_ip: str | None,
    limit: int,
    before: tuple[datetime, str, int] | None,
) -> list[DebugEntryRow]:
    entry_table = "tiqora_gi_debug_entry" if side == "tiqora" else "gi_debugger_entry"
    content_table = "tiqora_gi_debug_content" if side == "tiqora" else "gi_debugger_entry_content"
    fk = "entry_id" if side == "tiqora" else "gi_debugger_entry_id"
    extra = ", name, ticket_id, replay IS NOT NULL AS has_replay" if side == "tiqora" else ""
    where = "webservice_id = :ws"
    params: dict[str, Any] = {"ws": webservice_id, "lim": limit + 1}
    if communication_type:
        where += " AND communication_type = :ctype"
        params["ctype"] = communication_type
    if created_after is not None:
        where += " AND create_time >= :after"
        params["after"] = created_after
    if created_before is not None:
        where += " AND create_time <= :until"
        params["until"] = created_before
    if remote_ip:
        where += " AND remote_ip = :rip"
        params["rip"] = remote_ip
    frag, kparams = _keyset(side, before)
    params.update(kparams)
    rows = (
        await session.execute(
            text(
                "SELECT id, communication_id, communication_type, remote_ip, webservice_id,"  # noqa: S608
                f" create_time{extra} FROM {entry_table} WHERE {where}{frag}"
                " ORDER BY create_time DESC, id DESC LIMIT :lim"
            ),
            params,
        )
    ).all()
    rows = rows[: limit + 1]
    levels = await _max_levels(session, content_table, fk, [int(r.id) for r in rows])
    out: list[DebugEntryRow] = []
    for r in rows:
        is_tiqora = side == "tiqora"
        out.append(
            DebugEntryRow(
                source=side,
                id=int(r.id),
                communication_id=r.communication_id,
                communication_type=r.communication_type,
                remote_ip=r.remote_ip,
                webservice_id=int(r.webservice_id),
                create_time=r.create_time,
                name=r.name if is_tiqora else None,
                ticket_id=(int(r.ticket_id) if r.ticket_id is not None else None)
                if is_tiqora
                else None,
                max_level=levels.get(int(r.id), "debug"),
                can_resend=bool(is_tiqora and r.communication_type == "Requester" and r.has_replay),
            )
        )
    return out


async def search_entries(
    session: AsyncSession,
    *,
    webservice_id: int,
    communication_type: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    remote_ip: str | None = None,
    source: DebugSource | None = None,
    limit: int = 50,
    before: tuple[datetime, str, int] | None = None,
) -> list[DebugEntryRow]:
    """Newest *limit* entries of both stores, newest first.

    Page on with ``before = (create_time, source, id)`` of the last row delivered.
    """
    merged: list[DebugEntryRow] = []
    for side in ("tiqora", "znuny"):
        if source is not None and source != side:
            continue
        merged.extend(
            await _search_side(
                session,
                side=side,
                webservice_id=webservice_id,
                communication_type=communication_type,
                created_after=created_after,
                created_before=created_before,
                remote_ip=remote_ip,
                limit=limit,
                before=before,
            )
        )
    merged.sort(key=lambda e: (e.create_time, _SOURCE_ORDER[e.source], e.id), reverse=True)
    return merged[:limit]


def _decode(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    return bytes(raw).decode("utf-8", errors="replace")


async def get_entry(
    session: AsyncSession, *, source: DebugSource, entry_id: int, webservice_id: int
) -> tuple[DebugEntryRow, list[DebugContentRow]] | None:
    """One entry with its lines (ordered by id); ``None`` when it belongs to another webservice."""
    entry_table = "tiqora_gi_debug_entry" if source == "tiqora" else "gi_debugger_entry"
    content_table = "tiqora_gi_debug_content" if source == "tiqora" else "gi_debugger_entry_content"
    fk = "entry_id" if source == "tiqora" else "gi_debugger_entry_id"
    extra = ", name, ticket_id, replay IS NOT NULL AS has_replay" if source == "tiqora" else ""
    head = (
        await session.execute(
            text(
                "SELECT id, communication_id, communication_type, remote_ip, webservice_id,"  # noqa: S608
                f" create_time{extra} FROM {entry_table} WHERE id = :i AND webservice_id = :ws"
            ),
            {"i": entry_id, "ws": webservice_id},
        )
    ).first()
    if head is None:
        return None
    lines = (
        await session.execute(
            text(
                f"SELECT id, debug_level, subject, content, create_time FROM {content_table}"  # noqa: S608
                f" WHERE {fk} = :i ORDER BY id"
            ),
            {"i": entry_id},
        )
    ).all()
    contents = [
        DebugContentRow(
            id=int(c.id),
            debug_level=c.debug_level,
            subject=c.subject,
            content=_decode(c.content),
            create_time=c.create_time,
        )
        for c in lines
    ]
    top = "debug"
    for c in contents:
        if _rank(c.debug_level) > _rank(top):
            top = c.debug_level
    is_tiqora = source == "tiqora"
    row = DebugEntryRow(
        source=source,
        id=int(head.id),
        communication_id=head.communication_id,
        communication_type=head.communication_type,
        remote_ip=head.remote_ip,
        webservice_id=int(head.webservice_id),
        create_time=head.create_time,
        name=head.name if is_tiqora else None,
        ticket_id=(int(head.ticket_id) if head.ticket_id is not None else None)
        if is_tiqora
        else None,
        max_level=top,
        can_resend=bool(is_tiqora and head.communication_type == "Requester" and head.has_replay),
    )
    return row, contents


async def get_replay(
    session: AsyncSession, *, entry_id: int, webservice_id: int
) -> dict[str, Any] | None:
    """The stored requester replay (``invoker``, ``data``, ``asynchronous``) of a Tiqora entry."""
    raw = (
        await session.execute(
            select(TiqoraGiDebugEntry.replay).where(
                TiqoraGiDebugEntry.id == entry_id,
                TiqoraGiDebugEntry.webservice_id == webservice_id,
            )
        )
    ).scalar_one_or_none()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _delete_entries(session: AsyncSession, entry_ids: list[int]) -> None:
    await session.execute(
        delete(TiqoraGiDebugContent).where(TiqoraGiDebugContent.entry_id.in_(entry_ids))
    )
    await session.execute(delete(TiqoraGiDebugEntry).where(TiqoraGiDebugEntry.id.in_(entry_ids)))


async def purge_debug_entries(session: AsyncSession, *, older_than: datetime) -> int:
    """Delete Tiqora entries (and their lines) created before *older_than*; commits per batch.

    Never touches Znuny's ``gi_debugger_*`` tables.
    """
    total = 0
    while True:
        ids = list(
            (
                await session.execute(
                    select(TiqoraGiDebugEntry.id)
                    .where(TiqoraGiDebugEntry.create_time < older_than)
                    .order_by(TiqoraGiDebugEntry.id)
                    .limit(_BATCH)
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            break
        await _delete_entries(session, [int(i) for i in ids])
        await session.commit()
        total += len(ids)
    return total


async def delete_debug_entries_for_tickets(session: AsyncSession, ticket_ids: Sequence[int]) -> int:
    """GDPR: delete every Tiqora entry that belongs to one of *ticket_ids* (no commit).

    Irreversible and not part of the erasure backup. Provider rows without a ``ticket_id`` are
    only covered by the retention cleanup.
    """
    ids = sorted({int(t) for t in ticket_ids})
    total = 0
    for start in range(0, len(ids), _BATCH):
        chunk = ids[start : start + _BATCH]
        entry_ids = [
            int(i)
            for i in (
                await session.execute(
                    select(TiqoraGiDebugEntry.id).where(TiqoraGiDebugEntry.ticket_id.in_(chunk))
                )
            )
            .scalars()
            .all()
        ]
        if entry_ids:
            await _delete_entries(session, entry_ids)
            total += len(entry_ids)
    return total


async def count_entries(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count(TiqoraGiDebugEntry.id)))).scalar_one())
