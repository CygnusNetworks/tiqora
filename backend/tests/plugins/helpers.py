"""Install a fixture plugin as a real (pip-less) distribution for importlib.metadata."""

from __future__ import annotations

import importlib
import shutil
import sys
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _toml_value(v: Any) -> str:
    if isinstance(v, str):
        return '"' + v.replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def apply_manifest_overrides(path: Path, overrides: Mapping[str, Any]) -> None:
    """Rewrite a fixture ``tiqora-plugin.toml`` with top-level keys / tables replaced."""
    data = tomllib.loads(path.read_text())
    top = {k: v for k, v in data.items() if not isinstance(v, dict)}
    top.update({k: v for k, v in overrides.items() if not isinstance(v, dict)})
    tables = {k: v for k, v in data.items() if isinstance(v, dict)}
    tables.update({k: v for k, v in overrides.items() if isinstance(v, dict)})
    lines = [f"{k} = {_toml_value(v)}" for k, v in top.items()]
    for name, table in tables.items():
        lines.append(f"[{name}]")
        lines.extend(f'"{k}" = {_toml_value(v)}' for k, v in table.items())
    path.write_text("\n".join(lines) + "\n")


def install_fixture_plugin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package: str = "tq_fixture_hello",
    *,
    plugin_id: str = "org.example.hello",
    version: str = "1.0.0",
    dist_name: str | None = None,
    entry: str | None = None,
    drop: Sequence[str] = (),
    manifest_overrides: Mapping[str, Any] | None = None,
    requires_dist: Sequence[str] = (),
) -> Path:
    """Copy fixtures/<package> to tmp_path/site/, write a .dist-info, put it on sys.path."""
    site = tmp_path / "site"
    pkg_dir = site / package
    shutil.copytree(FIXTURES / package, pkg_dir, ignore=shutil.ignore_patterns("__pycache__"))
    for rel in drop:
        (pkg_dir / rel).unlink()
    if manifest_overrides:
        apply_manifest_overrides(pkg_dir / "tiqora-plugin.toml", manifest_overrides)
    dist = dist_name or package.replace("_", "-")
    info = site / f"{dist.replace('-', '_')}-{version}.dist-info"
    info.mkdir()
    meta = ["Metadata-Version: 2.1", f"Name: {dist}", f"Version: {version}"]
    meta += [f"Requires-Dist: {r}" for r in requires_dist]
    (info / "METADATA").write_text("\n".join(meta) + "\n")
    (info / "entry_points.txt").write_text(
        f"[tiqora.plugins]\n{plugin_id} = {entry or package + ':plugin'}\n"
    )
    files = [p.relative_to(site).as_posix() for p in sorted(pkg_dir.rglob("*")) if p.is_file()]
    files += [f"{info.name}/METADATA", f"{info.name}/entry_points.txt", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{f},,\n" for f in files))
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    from tiqora.plugins.discovery import clear_discovery_cache

    clear_discovery_cache()
    return pkg_dir


Installer = Callable[..., Path]


@pytest.fixture
def fixture_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Installer]:
    installed: list[str] = []

    def install(package: str = "tq_fixture_hello", **kw: Any) -> Path:
        installed.append(package)
        sub = tmp_path / f"p{len(installed)}"
        sub.mkdir()
        return install_fixture_plugin(sub, monkeypatch, package, **kw)

    yield install
    for package in installed:
        for name in [m for m in sys.modules if m == package or m.startswith(package + ".")]:
            del sys.modules[name]
    from tiqora.plugins.discovery import clear_discovery_cache

    clear_discovery_cache()
