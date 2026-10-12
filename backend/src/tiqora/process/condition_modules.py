"""``Type: Module`` transition conditions: a registry of validation modules.

Znuny loads ``Kernel::System::ProcessManagement::TransitionValidation::<Name>`` for a
condition field of ``Type: Module`` (``Transition.pm``). Perl code cannot run here, so every
supported module is re-implemented in Python and registered on :data:`CONDITION_MODULES`
under the exact Perl module path used in ``Match``. Unknown modules never match.

Sources (``znuny-6.5.22/Kernel/System/ProcessManagement/TransitionValidation``):
``ValidateDemo.pm`` accepts a ticket only when its queue is ``Raw``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from tiqora.extensions.registry import ExtensionPoint, Registry

ConditionDataValue = str | list[str] | None
ConditionModuleFn = Callable[[Mapping[str, ConditionDataValue], str], bool]
"""``(condition data, field name) -> match``."""

ZNUNY_VALIDATION_PREFIX = "Kernel::System::ProcessManagement::TransitionValidation::"

__all__ = [
    "CONDITION_MODULES",
    "ConditionModule",
    "ConditionModuleFn",
    "core_condition_modules",
    "normalize_module_match",
    "resolve_condition_module",
]


@dataclass(frozen=True, slots=True)
class ConditionModule:
    match: str
    """The Perl module path exactly as in ``Match``."""
    validate: ConditionModuleFn
    tiqora_only: bool = False
    """``Tiqora::...`` names, not known to Znuny."""


CONDITION_MODULES: ExtensionPoint[ConditionModule] = ExtensionPoint(
    key="core.process.condition_modules",
    item_key=lambda m: m.match,
    description="Transition condition validation modules (Type: Module)",
)


def _validate_demo(data: Mapping[str, ConditionDataValue], field: str) -> bool:
    """``ValidateDemo.pm``: matches exactly when the ticket's queue is ``Raw``."""
    return data.get("Queue") == "Raw"


def core_condition_modules() -> list[ConditionModule]:
    return [ConditionModule(ZNUNY_VALIDATION_PREFIX + "ValidateDemo", _validate_demo)]


_PM_SUFFIX = re.compile(r"\.pm\Z")


def normalize_module_match(match: str) -> str:
    """Strip surrounding whitespace and a trailing ``.pm`` (Znuny's POD example has one)."""
    return _PM_SUFFIX.sub("", match.strip()).strip()


def resolve_condition_module(
    match: object, registry: Registry | None = None
) -> ConditionModule | None:
    """The module registered for *match*, or ``None`` (also for non-string values)."""
    if not isinstance(match, str):
        return None
    if registry is None:
        from tiqora.extensions.core import get_registry

        registry = get_registry()
    return registry.get(CONDITION_MODULES, normalize_module_match(match))
