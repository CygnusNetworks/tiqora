"""CI provider registry (cross-plan; owner: ITSM core, design I3).

Declares the ``CI_PROVIDERS`` extension point and the factory item it holds — one factory per
ITSM mode. The ITSM core registers ``CiProviderFactory(mode="znuny_readthrough", …)``, the CMDB
plan ``CiProviderFactory(mode="tiqora", …)``, both in ``register_core``. The lookup functions
(``get_ci_provider``, ``tiqora_cmdb_empty``) belong to the ITSM core and are added there.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from tiqora.extensions import ExtensionPoint
from tiqora.itsm.ci_provider import CiProvider

if TYPE_CHECKING:
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

__all__ = ["CI_PROVIDERS", "CiProviderFactory"]
