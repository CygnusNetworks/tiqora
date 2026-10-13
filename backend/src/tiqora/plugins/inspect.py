"""The package report: single source for the install dialog, upload, install job and CLI.

``inspect_package`` is pure and synchronous (CPU work only: callers run it in a thread, F4).
Package content problems become German ``problems`` of the report; only a package that cannot be
read at all raises ``PackageError``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel

from tiqora.config import Settings
from tiqora.plugins.compat import check_compat, release_core_version
from tiqora.plugins.install_checks import (
    collision_problems,
    migration_tables,
    prefix_problems,
    requirement_problems,
    static_warnings,
)
from tiqora.plugins.package import read_tqplugin
from tiqora.plugins.repo_index import safe_url
from tiqora.plugins.signing import KeyRing, TrustLevel, resolve_trust

UNSIGNED_PROBLEM = (
    "Paket ist nicht signiert (oder mit einem unbekannten Schlüssel). "
    "Unsignierte Pakete sind nur mit TIQORA_PLUGINS_ALLOW_UNSIGNED=1 erlaubt."
)
BAKE_HINT = "Bitte als eigenes Image einbauen (tiqora plugins bake)."


class CapabilityLine(BaseModel):
    kind: Literal["extension_point", "outbound_host", "frontend_slot", "table"]
    value: str


class PackageReport(BaseModel):
    sha256: str
    size_bytes: int
    plugin_id: str
    short_id: str
    name: str
    version: str
    publisher: str
    publisher_id: str | None
    description: str
    license: str | None
    homepage: str | None
    support: str | None
    categories: list[str]
    changelog: str | None
    trust: TrustLevel
    key_id: str | None
    signer: str | None
    capabilities: list[CapabilityLine]
    problems: list[str]
    warnings: list[str]
    installed_version: str | None
    action: Literal["install", "update", "reinstall", "downgrade", "blocked_image"]
    installable: bool


@dataclass(frozen=True, slots=True)
class InstalledPlugin:
    plugin_id: str
    version: str
    source: str  # "image" | "upload" | "repository"
    package_sha256: str | None
    top_level: tuple[str, ...]


def _requires_problems(
    requires: Mapping[str, str],
    installed: Mapping[str, InstalledPlugin],
    image_plugin_ids: frozenset[str],
) -> list[str]:
    out: list[str] = []
    for plugin_id, spec in sorted(requires.items()):
        have = installed.get(plugin_id)
        if have is None:
            if plugin_id not in image_plugin_ids:
                out.append(f"benötigt die Erweiterung „{plugin_id}“, die nicht installiert ist")
            continue
        try:
            ok = SpecifierSet(spec).contains(Version(have.version), prereleases=True)
        except InvalidVersion:
            ok = False
        if not ok:
            out.append(
                f"benötigt die Erweiterung „{plugin_id}“ in Version {spec}, "
                f"installiert ist {have.version}"
            )
    return out


def inspect_package(
    data: bytes,
    *,
    settings: Settings,
    ring: KeyRing,
    installed: Mapping[str, InstalledPlugin],
    image_plugin_ids: frozenset[str],
    short_id_owners: Mapping[str, str] | None = None,
) -> PackageReport:
    """Everything an admin needs to decide about ``data``.

    ``short_id_owners`` maps a short id to the plugin id that owns it (installed, discovered or
    holding kept data, F2); the caller builds it from the database. Raises ``PackageError`` when
    the file is not a readable ``.tqplugin``.
    """
    pkg = read_tqplugin(data, max_bytes=settings.plugins_max_package_bytes)
    manifest = pkg.manifest
    wheel = pkg.wheel
    problems: list[str] = []
    warnings: list[str] = []

    inst = installed.get(manifest.id)

    # 1. compatibility with this Tiqora / SDK
    compat = check_compat(manifest, core=release_core_version())
    if compat:
        problems.append(compat)

    # 2. Python dependencies the image does not bring
    req = requirement_problems(wheel.requires_dist)
    problems += req
    if req:
        problems.append(BAKE_HINT)
    if wheel.entry_value != manifest.entry:
        problems.append(
            f"Einstiegspunkt im Wheel ({wheel.entry_value}) stimmt nicht mit dem Manifest "
            f"({manifest.entry}) überein"
        )
    problems += _requires_problems(manifest.requires, installed, image_plugin_ids)

    # 3. module name collisions. The plugin's own runtime copy is exempt, but only for names the
    # installed record says it already owns (an uploaded wheel cannot name an image dist).
    own_dist = None
    if (
        inst is not None
        and inst.source != "image"
        and manifest.id not in image_plugin_ids
        and set(wheel.top_level) <= set(inst.top_level)
    ):
        own_dist = wheel.dist_name
    problems += collision_problems(list(wheel.top_level), own_dist=own_dist)

    # 4. blocking static checks and warnings
    problems += prefix_problems(wheel.files, manifest.short_id)
    warnings += static_warnings(wheel.files, short_id=manifest.short_id)
    if wheel.files_truncated:
        warnings.append(
            "Das Paket enthält sehr viel Quelltext; die statische Prüfung wurde gekürzt"
        )
    owner = (short_id_owners or {}).get(manifest.short_id)
    if owner is not None and owner != manifest.id:
        problems.append(f"Kurzname „{manifest.short_id}“ wird schon von {owner} verwendet")

    # 5. trust
    trust = resolve_trust(
        pkg.signature,
        pkg.checksums,
        plugin_id=manifest.id,
        manifest_publisher_id=manifest.publisher_id,
        ring=ring,
    )
    if trust.level is TrustLevel.INVALID:
        problems.append(trust.problem or "Signatur ungültig")
    elif trust.level is TrustLevel.UNSIGNED:
        if not settings.plugins_allow_unsigned:
            problems.append(UNSIGNED_PROBLEM)
        elif trust.problem:
            warnings.append(trust.problem)

    # 6. image plugin and version relation
    action: Literal["install", "update", "reinstall", "downgrade", "blocked_image"] = "install"
    if manifest.id in image_plugin_ids:
        action = "blocked_image"
        problems.append(
            f"Erweiterung „{manifest.id}“ ist im Image eingebaut; "
            "Aktualisierung über ein neues Image"
        )
    elif inst is not None:
        try:
            new, old = Version(manifest.version), Version(inst.version)
        except InvalidVersion:
            action = "update"
        else:
            if new == old:
                action = "reinstall"
            elif new > old:
                action = "update"
            else:
                action = "downgrade"
                problems.append(
                    f"Version {manifest.version} ist älter als die installierte "
                    f"Version {inst.version}; Downgrades sind nicht erlaubt"
                )

    capabilities = [
        CapabilityLine(kind="extension_point", value=v)
        for v in manifest.capabilities.extension_points
    ]
    capabilities += [
        CapabilityLine(kind="outbound_host", value=v) for v in manifest.capabilities.outbound_hosts
    ]
    capabilities += [
        CapabilityLine(kind="frontend_slot", value=v) for v in manifest.capabilities.frontend_slots
    ]
    capabilities += [CapabilityLine(kind="table", value=v) for v in migration_tables(wheel.files)]

    return PackageReport(
        sha256=pkg.sha256,
        size_bytes=pkg.size_bytes,
        plugin_id=manifest.id,
        short_id=manifest.short_id,
        name=manifest.name,
        version=manifest.version,
        publisher=manifest.publisher,
        publisher_id=manifest.publisher_id,
        description=manifest.description,
        license=manifest.license,
        homepage=safe_url(manifest.homepage),
        support=safe_url(manifest.support, mailto=True),
        categories=list(manifest.categories),
        changelog=pkg.changelog,
        trust=trust.level,
        key_id=trust.key_id,
        signer=trust.signer,
        capabilities=capabilities,
        problems=problems,
        warnings=warnings,
        installed_version=inst.version if inst is not None else None,
        action=action,
        installable=not problems,
    )
