"""What of Znuny's ITSM is installed in the database (read-only, no JOIN to ``tiqora_*``)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.znuny_adapter.profiles import select_profile

CACHE_SECONDS: Final = 60.0

_CMDB_TABLES: Final = ("configitem", "configitem_version", "configitem_definition")
_CHANGE_TABLES: Final = ("change_item",)


@dataclass(frozen=True, slots=True)
class ZnunyItsmInfo:
    packages: Mapping[str, str]  # install_status "installed": name -> version
    core_installed: bool  # all of ZNUNY_ITSM_MARKER_TABLES present (the one predicate)
    cmdb_installed: bool  # configitem + configitem_version + configitem_definition present
    change_installed: bool  # change_item present (survey only; unused in phase 1)
    profile: str | None  # select_profile(...) name, None when unknown
    unsupported_reason: str | None  # e.g. "ITSMConfigurationManagement 7.4.0 not surveyed"


_cache: dict[str, tuple[float, ZnunyItsmInfo]] = {}


def clear_detect_cache() -> None:
    _cache.clear()


def _cache_key(session: AsyncSession) -> str:
    return session.get_bind().engine.url.render_as_string(hide_password=True)


def _table_flags(sync_conn: Connection) -> tuple[set[str], bool]:
    from tiqora.itsm.mode import ZNUNY_ITSM_MARKER_TABLES  # mode imports this module

    names = set(inspect(sync_conn).get_table_names())
    wanted = set(ZNUNY_ITSM_MARKER_TABLES) | set(_CMDB_TABLES) | set(_CHANGE_TABLES)
    return names & wanted, "package_repository" in names


async def detect_znuny_itsm(session: AsyncSession, *, use_cache: bool = True) -> ZnunyItsmInfo:
    """Inspect the Znuny database. Cached per process for 60 s (``use_cache=False`` bypasses)."""
    from tiqora.itsm.mode import ZNUNY_ITSM_MARKER_TABLES  # mode imports this module

    key = _cache_key(session)
    now = time.monotonic()
    if use_cache:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_SECONDS:
            return hit[1]

    conn = await session.connection()
    present, has_repo = await conn.run_sync(_table_flags)
    core = all(t in present for t in ZNUNY_ITSM_MARKER_TABLES)
    cmdb = all(t in present for t in _CMDB_TABLES)
    change = all(t in present for t in _CHANGE_TABLES)

    packages: dict[str, str] = {}
    if has_repo:
        rows = await session.execute(
            text(
                "SELECT name, version FROM package_repository"
                " WHERE install_status = 'installed' ORDER BY id"
            )
        )
        for name, version in rows.all():
            packages[str(name)] = str(version)

    profile_name: str | None = None
    reason: str | None = None
    if core:
        profile, reason = select_profile(packages)
        profile_name = profile.name if profile is not None else None

    info = ZnunyItsmInfo(
        packages=MappingProxyType(packages),
        core_installed=core,
        cmdb_installed=cmdb,
        change_installed=change,
        profile=profile_name,
        unsupported_reason=reason,
    )
    _cache[key] = (now, info)
    return info
