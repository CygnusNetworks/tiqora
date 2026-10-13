"""Import steps (catalog, classes, CIs, links, ...): each is `async def run_*(ctx: StepContext)`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.itsm.importer.mapstore import MapStore
from tiqora.itsm.importer.ports import CmdbWritePort, Ports
from tiqora.itsm.importer.report import ImportReport


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
