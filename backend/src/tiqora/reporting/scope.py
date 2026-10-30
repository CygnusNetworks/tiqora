"""Viewer's CMDB class scope and availability of report sources.

``tiqora.cmdb.*`` is imported inside the functions: ``cmdb/transfer/*`` imports
``tiqora.reporting.export``, a module-level import here would close a cycle through the engine.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.extensions.registry import Registry
from tiqora.reporting.semantic import ReportSource, active_sources


async def load_cmdb_class_scope(session: AsyncSession, user_id: int) -> frozenset[int]:
    """Ids of the CI classes the user may read (admins: all), the rule of the CMDB list."""
    from tiqora.cmdb.access import load_access, readable_class_ids  # noqa: PLC0415 - import cycle

    return frozenset(await readable_class_ids(session, await load_access(session, user_id)))


async def source_available(session: AsyncSession, src: ReportSource) -> bool:
    """Whether ``src`` is offered now; a saved report on an unavailable source still runs."""
    if src.availability == "always":
        return True
    from tiqora.cmdb.gate import current_itsm_mode  # noqa: PLC0415 - import cycle

    return await current_itsm_mode(session) == "tiqora"


async def available_sources(session: AsyncSession, registry: Registry) -> list[ReportSource]:
    """The active sources that are offered now (the ITSM mode is read once per call)."""
    from tiqora.cmdb.gate import current_itsm_mode  # noqa: PLC0415 - import cycle

    mode = await current_itsm_mode(session)
    return [s for s in active_sources(registry) if s.availability == "always" or mode == "tiqora"]
