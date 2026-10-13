"""The CMDB mode gate: the CMDB is served only in ITSM mode ``tiqora`` (Mode B).

The mode comes from the ITSM core's resolver :func:`tiqora.itsm.mode.get_itsm_mode`: the stored
``itsm.mode`` setting, or, while that row is absent, the auto default from Znuny's ITSM marker
tables (both present: ``znuny_readthrough``, else ``tiqora``). The table check behind it is
:func:`tiqora.itsm.znuny_adapter.detect.detect_znuny_itsm`, cached per database for 60 s, so a
gated request does not inspect the schema itself. Nothing here writes the setting: a site moves
from Mode A to Mode B only through the importer.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.errors import CmdbError
from tiqora.itsm.mode import ItsmMode, get_itsm_mode
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache


def reset_mode_cache() -> None:
    """Forget the cached Znuny ITSM detection (tests that create or drop the marker tables)."""
    clear_detect_cache()


class CmdbUnavailable(CmdbError):
    """The Tiqora CMDB is not active (``off`` answers 404, ``znuny_readthrough`` 409)."""

    def __init__(self, mode: ItsmMode) -> None:
        super().__init__(f"cmdb unavailable in itsm mode {mode}")
        self.mode: ItsmMode = mode


async def current_itsm_mode(session: AsyncSession) -> ItsmMode:
    """The ITSM mode as the ITSM core resolves it (one mode source for both modes)."""
    return (await get_itsm_mode(session)).mode


async def require_tiqora_cmdb(session: AsyncSession) -> None:
    mode = await current_itsm_mode(session)
    if mode != "tiqora":
        raise CmdbUnavailable(mode)


__all__ = ["CmdbUnavailable", "current_itsm_mode", "require_tiqora_cmdb", "reset_mode_cache"]
