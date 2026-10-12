"""The CMDB mode gate: the CMDB is served only in ITSM mode ``tiqora`` (Mode B).

SEAM C2 -- replaced at merge by itsm1's resolver (plan Task 30). Until then the mode is read
from the stored ``itsm.mode`` setting; when that row is absent, Znuny's ITSM marker tables decide
(both present: ``znuny_readthrough``, else ``tiqora``). This module never writes the setting, so
a site migrates from Mode A to Mode B only through the importer.
"""

from __future__ import annotations

from typing import get_args

from sqlalchemy import inspect, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbError
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.itsm.mode import SETTING_MODE, ZNUNY_ITSM_MARKER_TABLES, ItsmMode

_MODES: frozenset[str] = frozenset(get_args(ItsmMode))


class CmdbUnavailable(CmdbError):
    """The Tiqora CMDB is not active (``off`` answers 404, ``znuny_readthrough`` 409)."""

    def __init__(self, mode: ItsmMode) -> None:
        super().__init__(f"cmdb unavailable in itsm mode {mode}")
        self.mode: ItsmMode = mode


def _znuny_itsm_installed(conn: Connection) -> bool:
    insp = inspect(conn)
    return all(insp.has_table(name) for name in ZNUNY_ITSM_MARKER_TABLES)


async def current_itsm_mode(session: AsyncSession) -> ItsmMode:
    """SEAM C2 -- replaced at merge by itsm1's resolver (plan Task 30)."""
    stored = (
        await session.execute(
            select(TiqoraSettings.value).where(TiqoraSettings.key == SETTING_MODE)
        )
    ).scalar_one_or_none()
    if stored is not None and stored.strip() in _MODES:
        mode: ItsmMode = stored.strip()  # type: ignore[assignment]
        return mode
    conn = await session.connection()
    installed = await conn.run_sync(_znuny_itsm_installed)
    return "znuny_readthrough" if installed else "tiqora"


async def require_tiqora_cmdb(session: AsyncSession) -> None:
    mode = await current_itsm_mode(session)
    if mode != "tiqora":
        raise CmdbUnavailable(mode)


__all__ = ["CmdbUnavailable", "current_itsm_mode", "require_tiqora_cmdb"]
