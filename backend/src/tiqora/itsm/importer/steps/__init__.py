"""Import steps (catalog, classes, CIs, links, ...): each is `async def run_*(ctx: StepContext)`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.itsm.importer.mapstore import MapStore
from tiqora.itsm.importer.ports import CmdbWritePort, Ports
from tiqora.itsm.importer.report import ImportReport

#: map kind of the CIs that still need cmdb's ``finish_import`` (search index, class counter,
#: derived incident states). Written in the same transaction as the versions or links that made
#: the CI unfinished, dropped in the transaction that finished it, so a run that ends early
#: (cancel, error, worker restart) leaves them for the next run. ``source_id`` and ``target_id``
#: are the Tiqora CI id.
KIND_FINISH = "finish"


@dataclass
class StepContext:
    session_factory: async_sessionmaker[AsyncSession]
    ports_for: Callable[[AsyncSession], Ports]
    run_id: int
    dry_run: bool
    page_size: int
    report: ImportReport
    progress: Callable[[int, int | None, str], Awaitable[None]]
    check_cancelled: Callable[[], Awaitable[None]]
    user_id: int
    writer_for: Callable[[AsyncSession], CmdbWritePort]
    mapstore_for: Callable[[AsyncSession], MapStore]
    touched_ci_ids: set[int] = field(default_factory=set)


async def mark_unfinished(ctx: StepContext, store: MapStore, ci_ids: set[int]) -> None:
    """Remember CIs for ``incident_states`` (real runs only; flush only)."""
    if ctx.dry_run:
        return
    for ci_id in sorted(i for i in ci_ids if i > 0):
        ctx.touched_ci_ids.add(ci_id)
        await store.put(KIND_FINISH, str(ci_id), ci_id, "")
