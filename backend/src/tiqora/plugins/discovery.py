"""Find installed plugins without importing any plugin code.

Plugins are ordinary distributions with an entry point in group ``tiqora.plugins``
(name = plugin id). Everything here reads package metadata and files only.
"""

from __future__ import annotations

import dataclasses
import functools
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import Distribution, EntryPoint, PackagePath, entry_points
from pathlib import Path
from typing import Final

import structlog
from packaging.version import InvalidVersion, Version

from tiqora.plugins.manifest import (
    ManifestError,
    PluginManifest,
    WebManifest,
    parse_manifest,
    parse_web_manifest,
)

log = structlog.get_logger(__name__)

ENTRY_POINT_GROUP: Final = "tiqora.plugins"
MANIFEST_NAME: Final = "tiqora-plugin.toml"

_WEB_ASSET_RE: Final = re.compile(r"^web/[A-Za-z0-9_./-]+$")
_I18N_ASSET_RE: Final = re.compile(r"^i18n/[a-z]{2}(_[A-Z]{2})?\.json$")
_LOCALE_RE: Final = re.compile(r"^[a-z]{2}(_[A-Z]{2})?$")


@dataclass(frozen=True, slots=True)
class DiscoveredPlugin:
    plugin_id: str  # entry point name
    entry: str  # entry point value "module:attr"
    dist_name: str
    dist_version: str
    package_dir: Path | None  # directory that holds tiqora-plugin.toml (None if not found)
    manifest: PluginManifest | None
    web_manifest: WebManifest | None
    locales: tuple[str, ...]  # from i18n/*.json, sorted
    problem: str | None  # German reason when the package is unusable


def _manifest_path(dist: Distribution, entry: str) -> PackagePath | None:
    candidates = [f for f in (dist.files or []) if f.name == MANIFEST_NAME and len(f.parts) == 2]
    if len(candidates) > 1:
        top = entry.split(":", 1)[0].split(".", 1)[0]
        candidates = [f for f in candidates if f.parts[0] == top]
    return candidates[0] if len(candidates) == 1 else None


def _inspect(ep: EntryPoint) -> DiscoveredPlugin:
    plugin_id, entry = ep.name, ep.value
    dist = ep.dist
    if dist is None:
        return DiscoveredPlugin(
            plugin_id, entry, "", "", None, None, None, (), "Paket ohne Metadaten"
        )
    dist_name = str(dist.metadata["Name"] or "")
    dist_version = dist.version

    def result(
        problem: str | None,
        package_dir: Path | None = None,
        manifest: PluginManifest | None = None,
        web: WebManifest | None = None,
        locales: tuple[str, ...] = (),
    ) -> DiscoveredPlugin:
        return DiscoveredPlugin(
            plugin_id, entry, dist_name, dist_version, package_dir, manifest, web, locales, problem
        )

    rel = _manifest_path(dist, entry)
    if rel is None:
        return result(f"{MANIFEST_NAME} fehlt im Paket")
    located = Path(str(dist.locate_file(rel)))
    package_dir = located.parent.resolve()
    if not located.is_file():
        return result(f"{MANIFEST_NAME} fehlt im Paket", package_dir)
    try:
        manifest = parse_manifest(located.read_text("utf-8"))
    except ManifestError as exc:
        return result(str(exc), package_dir)

    i18n_dir = package_dir / "i18n"
    locales = tuple(sorted(p.stem for p in i18n_dir.glob("*.json") if _LOCALE_RE.fullmatch(p.stem)))

    def bad(problem: str) -> DiscoveredPlugin:
        return result(problem, package_dir, manifest, None, locales)

    if manifest.id != plugin_id:
        return bad(
            f"Kennung im Manifest ({manifest.id}) passt nicht zum Einstiegspunkt ({plugin_id})"
        )
    if manifest.entry != entry:
        return bad(f"entry im Manifest ({manifest.entry}) passt nicht zum Einstiegspunkt ({entry})")
    try:
        same_version = Version(manifest.version) == Version(dist_version)
    except InvalidVersion:
        same_version = False
    if not same_version:
        return bad(
            f"Version im Manifest ({manifest.version}) passt nicht zum Paket ({dist_version})"
        )

    web: WebManifest | None = None
    web_path = package_dir / "web" / "manifest.json"
    if web_path.is_file():
        try:
            web = parse_web_manifest(web_path.read_bytes(), manifest.capabilities)
        except ManifestError as exc:
            return bad(str(exc))
    elif manifest.capabilities.frontend_slots:
        return bad("web/manifest.json fehlt")
    if web is not None:
        for name in (web.entry, *web.styles):
            if not (package_dir / "web" / name).is_file():
                return result(
                    f"web/{name} fehlt im Paket (beim Bauen verloren? "
                    "siehe Anleitung „Dateien im Wheel“)",
                    package_dir,
                    manifest,
                    web,
                    locales,
                )
    return result(None, package_dir, manifest, web, locales)


def _flag_duplicates(found: list[DiscoveredPlugin]) -> list[DiscoveredPlugin]:
    ids = Counter(d.plugin_id for d in found)
    shorts = Counter(d.manifest.short_id for d in found if d.manifest is not None)
    out: list[DiscoveredPlugin] = []
    for d in found:
        problem = d.problem
        if problem is None and ids[d.plugin_id] > 1:
            problem = f"Kennung {d.plugin_id} ist mehrfach installiert"
        elif problem is None and d.manifest is not None and shorts[d.manifest.short_id] > 1:
            problem = f"Kurzname {d.manifest.short_id} ist mehrfach installiert"
        out.append(dataclasses.replace(d, problem=problem) if problem != d.problem else d)
    return out


def discover_plugins(entry_points_: Iterable[EntryPoint] | None = None) -> list[DiscoveredPlugin]:
    """Inspect every ``tiqora.plugins`` entry point; never imports plugin code."""
    eps = entry_points(group=ENTRY_POINT_GROUP) if entry_points_ is None else entry_points_
    found: list[DiscoveredPlugin] = []
    for ep in eps:
        try:
            found.append(_inspect(ep))
        except Exception as exc:  # one broken distribution must not hide the others
            log.warning("plugin_discovery_failed", plugin=ep.name, error=repr(exc))
            found.append(
                DiscoveredPlugin(
                    ep.name,
                    ep.value,
                    "",
                    "",
                    None,
                    None,
                    None,
                    (),
                    f"Erweiterung konnte nicht gelesen werden: {type(exc).__name__}: {exc}",
                )
            )
    return sorted(_flag_duplicates(found), key=lambda d: d.plugin_id)


@functools.cache
def discovered_plugins() -> tuple[DiscoveredPlugin, ...]:
    return tuple(discover_plugins())


def clear_discovery_cache() -> None:
    discovered_plugins.cache_clear()


def plugin_asset_path(plugin: DiscoveredPlugin, rel: str) -> Path | None:
    """Resolve ``web/…`` or ``i18n/<lang>.json`` inside the package, else None."""
    if plugin.package_dir is None or "\\" in rel or ".." in rel.split("/"):
        return None
    if not (_WEB_ASSET_RE.fullmatch(rel) or _I18N_ASSET_RE.fullmatch(rel)):
        return None
    candidate = (plugin.package_dir / rel).resolve()
    if not candidate.is_file() or not candidate.is_relative_to(plugin.package_dir):
        return None
    return candidate
