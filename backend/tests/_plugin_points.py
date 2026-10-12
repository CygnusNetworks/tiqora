"""Boot the ``org.example.points`` fixture plugin into a frozen registry (D1.5)."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.plugins.helpers import install_fixture_plugin
from tiqora.extensions.core import register_core, set_registry_for_tests
from tiqora.extensions.registry import Registry
from tiqora.plugins.context import make_plugin_context
from tiqora.plugins.discovery import clear_discovery_cache, discover_plugins
from tiqora.plugins.loader import commit_registrar, import_and_register
from tiqora.plugins.state import PluginLoadResult, PluginRole, RuntimeState, set_loaded_plugins

PLUGIN_ID = "org.example.points"
PACKAGE = "tq_fixture_points"


def boot_points_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    role: PluginRole = "api",
    manifest_overrides: Mapping[str, Any] | None = None,
) -> Registry:
    """Install, register and freeze the fixture; ``conftest`` undoes the global state afterwards.

    ``role`` is the process role the caller pretends to be; it does not change loading yet.
    """
    _ = role
    teardown_points_plugin()
    install_fixture_plugin(
        tmp_path,
        monkeypatch,
        PACKAGE,
        plugin_id=PLUGIN_ID,
        manifest_overrides=manifest_overrides,
    )
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == PLUGIN_ID]
    registry = Registry()
    register_core(registry)
    registrar = import_and_register(discovered, make_plugin_context(discovered))
    contributions = tuple(commit_registrar(registry, registrar))
    registry.freeze()
    set_registry_for_tests(registry)
    set_loaded_plugins(
        [
            PluginLoadResult(
                PLUGIN_ID, discovered.dist_version, RuntimeState.ACTIVE, None, contributions
            )
        ]
    )
    points_behaviour().reset()
    return registry


def points_behaviour() -> ModuleType:
    return importlib.import_module(f"{PACKAGE}.behaviour")


def teardown_points_plugin() -> None:
    set_registry_for_tests(None)
    set_loaded_plugins([])
    for name in [m for m in sys.modules if m == PACKAGE or m.startswith(PACKAGE + ".")]:
        if name.endswith(".behaviour"):
            sys.modules[name].reset()
        del sys.modules[name]
    clear_discovery_cache()
