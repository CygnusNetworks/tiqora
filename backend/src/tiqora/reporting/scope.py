"""Viewer's CMDB class scope and availability of report sources.

``tiqora.cmdb.*`` is imported inside the functions: ``cmdb/transfer/*`` imports
``tiqora.reporting.export``, a module-level import here would close a cycle through the engine.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.extensions.registry import Registry
from tiqora.reporting.catalogue import BuiltinReport
from tiqora.reporting.semantic import ReportSource, active_sources


async def load_cmdb_class_scope(session: AsyncSession, user_id: int) -> frozenset[int]:
    """Ids of the CI classes the user may read (admins: all), the rule of the CMDB list."""
    from tiqora.cmdb.access import load_access, readable_class_ids  # noqa: PLC0415 - import cycle

    return frozenset(await readable_class_ids(session, await load_access(session, user_id)))


async def available_sources(session: AsyncSession, registry: Registry) -> list[ReportSource]:
    """The active sources that are offered now (the ITSM mode is read once per call)."""
    from tiqora.cmdb.gate import current_itsm_mode  # noqa: PLC0415 - import cycle
    from tiqora.itsm.change.gate import change_switched_off  # noqa: PLC0415 - import cycle

    mode = await current_itsm_mode(session)
    change_on = await change_switched_off(session) is None

    def offered(source: ReportSource) -> bool:
        if source.availability == "always":
            return True
        return mode == "tiqora" and (source.availability != "itsm_change" or change_on)

    return [s for s in active_sources(registry) if offered(s)]


async def switched_off_message(
    session: AsyncSession, source: ReportSource, builtin: BuiltinReport | None
) -> str | None:
    """Why this report may not run on a dashboard tile or in a schedule, ``None`` when it may.

    Only a switch counts: ITSM mode ``off`` and the change module off. A report on a Mode B source
    in Mode A keeps running and shows no rows.
    """
    from tiqora.cmdb.gate import current_itsm_mode  # noqa: PLC0415 - import cycle
    from tiqora.itsm.change.gate import change_switched_off  # noqa: PLC0415 - import cycle
    from tiqora.reporting.catalogue_itsm import CHANGE_ONLY_BUILTINS  # noqa: PLC0415

    is_itsm = source.availability != "always" or (
        builtin is not None and builtin.category == "itsm"
    )
    if is_itsm and await current_itsm_mode(session) == "off":
        return "ITSM ist ausgeschaltet."
    about_changes = source.availability == "itsm_change" or (
        builtin is not None and builtin.key in CHANGE_ONLY_BUILTINS
    )
    if about_changes and await change_switched_off(session) is not None:
        return "Change-Management ist ausgeschaltet."
    return None
