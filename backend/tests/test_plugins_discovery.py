from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from tiqora.plugins.discovery import (
    clear_discovery_cache,
    discover_plugins,
    discovered_plugins,
    plugin_asset_path,
)

Installer = Callable[..., Path]


def test_discovers_installed_fixture_without_importing(fixture_plugins: Installer) -> None:
    fixture_plugins()
    (p,) = [d for d in discover_plugins() if d.plugin_id == "org.example.hello"]
    assert p.problem is None
    assert p.manifest is not None and p.manifest.short_id == "hello"
    assert p.web_manifest is not None and len(p.web_manifest.contributions) == 6
    assert p.locales == ("de", "en")
    assert p.dist_name == "tq-fixture-hello" and p.dist_version == "1.0.0"
    assert "tq_fixture_hello" not in sys.modules


def test_entry_mismatch_is_problem(fixture_plugins: Installer) -> None:
    fixture_plugins(entry="tq_fixture_hello.plugin:plugin")
    (p,) = discover_plugins()
    assert p.problem is not None and "entry" in p.problem


def test_version_mismatch_is_problem(fixture_plugins: Installer) -> None:
    fixture_plugins(version="2.0.0")
    (p,) = discover_plugins()
    assert p.problem is not None and "2.0.0" in p.problem and "1.0.0" in p.problem


def test_missing_web_entry_is_problem(fixture_plugins: Installer) -> None:
    fixture_plugins(drop=["web/index.js"])
    (p,) = discover_plugins()
    assert p.problem is not None and "web/index.js" in p.problem


def test_missing_manifest_is_problem(fixture_plugins: Installer) -> None:
    fixture_plugins(drop=["tiqora-plugin.toml"])
    (p,) = discover_plugins()
    assert p.manifest is None and p.problem is not None and "tiqora-plugin.toml" in p.problem


def test_duplicate_ids_flag_both(fixture_plugins: Installer) -> None:
    fixture_plugins(dist_name="tq-fixture-hello")
    fixture_plugins(dist_name="tq-fixture-hello-copy")
    found = [d for d in discover_plugins() if d.plugin_id == "org.example.hello"]
    assert len(found) == 2 and all(d.problem and "mehrfach" in d.problem for d in found)


def test_cache_and_clear(fixture_plugins: Installer) -> None:
    assert all(d.plugin_id != "org.example.hello" for d in discovered_plugins())
    fixture_plugins()
    clear_discovery_cache()
    assert any(d.plugin_id == "org.example.hello" for d in discovered_plugins())


@pytest.mark.parametrize(
    "rel",
    [
        "web/../tiqora-plugin.toml",
        "/etc/passwd",
        "i18n/../plugin.py",
        "plugin.py",
        "web\\index.js",
        "i18n/de.txt",
        "web/missing.js",
    ],
)
def test_asset_path_rejects(fixture_plugins: Installer, rel: str) -> None:
    fixture_plugins()
    (p,) = discover_plugins()
    assert plugin_asset_path(p, rel) is None


def test_asset_path_resolves(fixture_plugins: Installer) -> None:
    pkg = fixture_plugins()
    (p,) = discover_plugins()
    assert plugin_asset_path(p, "web/index.js") == (pkg / "web" / "index.js").resolve()
    assert plugin_asset_path(p, "i18n/de.json") == (pkg / "i18n" / "de.json").resolve()


def test_exception_in_one_distribution_becomes_problem() -> None:
    class Boom:
        name = "org.example.boom"
        value = "boom:plugin"

        @property
        def dist(self) -> object:
            raise PermissionError("kein Zugriff")

    class NoDist:
        name = "org.example.nodist"
        value = "nodist:plugin"
        dist = None

    found = discover_plugins([Boom(), NoDist()])  # type: ignore[list-item]
    by_id = {d.plugin_id: d for d in found}
    assert "PermissionError" in (by_id["org.example.boom"].problem or "")
    assert by_id["org.example.nodist"].problem == "Paket ohne Metadaten"
