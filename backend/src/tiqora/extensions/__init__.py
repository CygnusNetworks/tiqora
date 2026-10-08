"""Internal extension registry (see :mod:`tiqora.extensions.registry`)."""

from __future__ import annotations

from tiqora.extensions.core import get_registry, register_core, set_registry_for_tests
from tiqora.extensions.registry import (
    DuplicateExtensionError,
    ExtensionPoint,
    Registration,
    Registry,
    RegistryFrozenError,
)

__all__ = [
    "DuplicateExtensionError",
    "ExtensionPoint",
    "Registration",
    "Registry",
    "RegistryFrozenError",
    "get_registry",
    "register_core",
    "set_registry_for_tests",
]
