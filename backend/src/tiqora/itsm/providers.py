"""CI provider registry (cross-plan; owner: ITSM core, design I3).

Declares the ``CI_PROVIDERS`` extension point and the factory item it holds — one factory per
ITSM mode. The ITSM core registers ``CiProviderFactory(mode="znuny_readthrough", …)``, the CMDB
plan ``CiProviderFactory(mode="tiqora", …)``, both in ``register_core``. The lookup functions
``get_ci_provider`` and ``tiqora_cmdb_empty`` are defined here (ITSM core).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.extensions import ExtensionPoint, get_registry
from tiqora.itsm.ci_provider import CiProvider
from tiqora.znuny.sysconfig import SysConfig


@dataclass(frozen=True, slots=True)
class CiProviderFactory:
    """Creates the CI provider of one ITSM mode."""

    mode: Literal["znuny_readthrough", "tiqora"]
    create: Callable[[SysConfig], CiProvider]


CI_PROVIDERS: Final[ExtensionPoint[CiProviderFactory]] = ExtensionPoint(
    key="itsm.ci_providers",
    item_key=lambda f: f.mode,
    description="CI data source per ITSM mode (Mode A: Znuny read-through, Mode B: Tiqora CMDB).",
    sdk_stable=False,
)


def get_ci_provider(
    mode: Literal["off", "znuny_readthrough", "tiqora"], sysconfig: SysConfig
) -> CiProvider | None:
    """The provider of ``mode``; ``None`` for ``off`` or while no factory is registered."""
    if mode == "off":
        return None
    factory = get_registry().get(CI_PROVIDERS, mode)
    return None if factory is None else factory.create(sysconfig)


async def tiqora_cmdb_empty(session: AsyncSession) -> bool:
    """``True`` while the Tiqora CMDB holds no CI (also while no Mode B provider is registered)."""
    provider = get_ci_provider("tiqora", SysConfig(session))
    return True if provider is None else await provider.is_empty(session)


__all__ = ["CI_PROVIDERS", "CiProviderFactory", "get_ci_provider", "tiqora_cmdb_empty"]
