"""Baking plugins into a Tiqora image: ``plugins.txt`` parsing, Dockerfile, constraints, verify.

Baked-only is the trust boundary of phase 1 (no runtime pip), so ``plugins.txt`` is the supply
chain: only pinned names, built wheels and at most one internal index are accepted.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import Distribution, EntryPoint, distributions, entry_points
from pathlib import Path
from typing import Final

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from tiqora.plugins.discovery import ENTRY_POINT_GROUP, discover_plugins

IMAGE_REPOSITORY: Final = "ghcr.io/cygnusnetworks/tiqora"
CONTAINER_DIR: Final = "/tmp/tiqora-plugins"  # noqa: S108 — path inside the build container
_WHEEL_DIR: Final = f"{CONTAINER_DIR}/wheels"
_HASH_RE: Final = re.compile(r"^--hash=sha256:[0-9a-f]{64}$")
_VCS_RE: Final = re.compile(r"(?:^|[\s@])(?:git|hg|svn|bzr)\+", re.IGNORECASE)
_CORE_REQUIREMENTS: Final = frozenset({"tiqora", "tiqora-sdk"})


class BakeError(ValueError):
    """``plugins.txt`` or the bake request is unusable (message is German)."""


@dataclass(frozen=True, slots=True)
class PluginsTxt:
    lines: list[str] = field(default_factory=list)  # as written to the image's plugins.txt
    wheels: list[Path] = field(default_factory=list)  # local wheels to copy next to the Dockerfile


def _split_hashes(text: str, where: str) -> tuple[str, list[str]]:
    """Separate trailing ``--hash=sha256:…`` options from the requirement."""
    tokens = text.split()
    hashes: list[str] = []
    while tokens and tokens[-1].startswith("--"):
        option = tokens.pop()
        if not _HASH_RE.fullmatch(option):
            raise BakeError(
                f"{where}: erlaubt ist nur --hash=sha256:<64 Hex-Zeichen>, nicht „{option}“"
            )
        hashes.insert(0, option)
    return " ".join(tokens), hashes


def parse_plugins_txt(path: Path) -> PluginsTxt:
    lines: list[str] = []
    wheels: list[Path] = []
    index_seen = False
    for number, raw in enumerate(path.read_text("utf-8").splitlines(), start=1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        where = f"Zeile {number}"
        if text.startswith("-"):
            name, _, value = text.partition(" ")
            if name == "--extra-index-url":
                raise BakeError(
                    f"{where}: --extra-index-url ist nicht erlaubt (Verwechslungsgefahr mit "
                    "öffentlichen Paketen); bitte genau einen internen --index-url nutzen"
                )
            if name == "--index-url":
                url = value.strip()
                if not url or len(url.split()) != 1:
                    raise BakeError(
                        f"{where}: --index-url erwartet genau eine Adresse und sonst nichts"
                    )
                if not url.startswith("https://"):
                    raise BakeError(f"{where}: --index-url muss mit https:// beginnen")
                if index_seen:
                    raise BakeError(f"{where}: --index-url darf nur einmal vorkommen")
                index_seen = True
                lines.append(f"--index-url {url}")
                continue
            if name.startswith("--hash"):
                raise BakeError(f"{where}: --hash gehört ans Ende einer Paketzeile")
            raise BakeError(f"{where}: Option „{name}“ ist in plugins.txt nicht erlaubt")
        if _VCS_RE.search(text):
            raise BakeError(
                f"{where}: Git-Quellen sind nicht erlaubt, bitte ein gebautes Wheel angeben"
            )
        requirement, hashes = _split_hashes(text, where)
        suffix = "".join(f" {h}" for h in hashes)
        if "@" not in requirement and requirement.endswith(".whl"):
            if "://" in requirement:
                raise BakeError(f"{where}: Adresse bitte als „name @ https://…“ schreiben")
            wheel = (path.parent / requirement).resolve()
            if not wheel.is_file():
                raise BakeError(f"{where}: Wheel-Datei {requirement} nicht gefunden")
            if any(w.name == wheel.name and w != wheel for w in wheels):
                raise BakeError(f"{where}: Wheel-Name {wheel.name} kommt mehrfach vor")
            if wheel not in wheels:
                wheels.append(wheel)
            lines.append(f"{_WHEEL_DIR}/{wheel.name}{suffix}")
            continue
        if requirement.startswith((".", "/", "~")):
            raise BakeError(f"{where}: lokale Pfade müssen auf ein gebautes Wheel (.whl) zeigen")
        try:
            parsed = Requirement(requirement)
        except InvalidRequirement as exc:
            raise BakeError(f"{where}: ungültige Paketangabe „{requirement}“ ({exc})") from None
        if parsed.url is not None and not parsed.url.startswith("https://"):
            raise BakeError(f"{where}: Adressen von Wheels müssen mit https:// beginnen")
        lines.append(requirement + suffix)
    return PluginsTxt(lines=lines, wheels=wheels)


def render_dockerfile(base_image: str, *, has_wheels: bool) -> str:
    parts = [
        "# Generated by `tiqora plugins bake` — edit plugins.txt and run it again "
        "instead of editing this file.\n",
        f"FROM {base_image}\n",
        "USER root\n",
        f"COPY plugins.txt {CONTAINER_DIR}/plugins.txt\n",
    ]
    if has_wheels:
        parts.append(f"COPY wheels/ {_WHEEL_DIR}/\n")
    parts += [
        "RUN /app/.venv/bin/python -m ensurepip --upgrade \\\n",
        " && /app/.venv/bin/python -m pip install --no-cache-dir \\\n",
        "      --constraint /app/backend/core-constraints.txt \\\n",
        f"      -r {CONTAINER_DIR}/plugins.txt \\\n",
        " && /app/.venv/bin/python -m pip uninstall -y pip \\\n",
        f" && rm -rf {CONTAINER_DIR}\n",
        "USER tiqora\n",
        "RUN python -m tiqora.main plugins verify --import --strict\n",
    ]
    return "".join(parts)


def default_base_image(env: Mapping[str, str] = os.environ) -> str | None:
    from tiqora.plugins.compat import release_core_version

    version = release_core_version(env)
    return f"{IMAGE_REPOSITORY}:{version}" if version is not None else None


_IMAGE_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]*$")


def bake(plugins_txt: Path, output: Path, *, base_image: str | None) -> None:
    parsed = parse_plugins_txt(plugins_txt)
    if base_image is not None and not _IMAGE_RE.fullmatch(base_image):
        raise BakeError(f"--base-image „{base_image}“ ist keine gültige Image-Angabe")
    image = base_image or default_base_image()
    if image is None:
        raise BakeError(
            "Keine Release-Version bekannt (TIQORA_VERSION fehlt oder ist kein Release): "
            "bitte --base-image angeben"
        )
    output.mkdir(parents=True, exist_ok=True)
    if parsed.wheels:
        (output / "wheels").mkdir(exist_ok=True)
        for wheel in parsed.wheels:
            shutil.copyfile(wheel, output / "wheels" / wheel.name)
    (output / "plugins.txt").write_text("".join(f"{line}\n" for line in parsed.lines), "utf-8")
    (output / "Dockerfile").write_text(
        render_dockerfile(image, has_wheels=bool(parsed.wheels)), "utf-8"
    )


def core_constraints(dists: Iterable[Distribution] | None = None) -> list[str]:
    """``Name==version`` per installed distribution (first wins), sorted by name."""
    seen: dict[str, str] = {}
    for dist in distributions() if dists is None else dists:
        name = dist.metadata["Name"]
        if name and name not in seen:
            seen[name] = dist.version
    return [f"{name}=={seen[name]}" for name in sorted(seen)]


def _plugin_entry_point(
    plugin_id: str, value: str, candidates: Iterable[EntryPoint] | None = None
) -> EntryPoint | None:
    """The entry point of ``plugin_id``; ``candidates`` replaces the process-wide lookup."""
    found = (
        entry_points(group=ENTRY_POINT_GROUP, name=plugin_id)
        if candidates is None
        else [ep for ep in candidates if ep.name == plugin_id]
    )
    for ep in found:
        if ep.value == value:
            return ep
    return None


def _core_requirements(ep: EntryPoint) -> list[str]:
    found: list[str] = []
    for raw in (ep.dist.requires if ep.dist is not None else None) or []:
        try:
            req = Requirement(raw)
        except InvalidRequirement:
            continue
        if canonicalize_name(req.name) in _CORE_REQUIREMENTS:
            found.append(req.name)
    return found


async def verify_installed_plugins(
    *,
    import_check: bool,
    strict: bool,
    timeout: float,  # noqa: ASYNC109  (mirrors run_in_daemon_thread)
    extra_paths: Sequence[Path] = (),
    only: str | None = None,
) -> list[str]:
    """One German line per problem, prefixed with the plugin id; empty when all is well.

    ``extra_paths`` (site directories of unpacked packages) replaces the process-wide entry
    point lookup: only the distributions in those directories are checked (the caller puts
    them on ``sys.path`` when ``import_check`` is set). ``only`` restricts the check to one
    plugin id; an id that is not there is a problem of its own.
    """
    from tiqora.plugins.compat import check_compat, release_core_version
    from tiqora.plugins.loader import run_in_daemon_thread

    problems: list[str] = []
    core = release_core_version()
    candidates: list[EntryPoint] | None = None
    if extra_paths:
        candidates = [
            ep
            for dist in distributions(path=[str(p) for p in extra_paths])
            for ep in dist.entry_points
            if ep.group == ENTRY_POINT_GROUP
        ]
        found = discover_plugins(entry_points_=candidates)
    else:
        found = discover_plugins()
    if only is not None:
        found = [d for d in found if d.plugin_id == only]
        if not found:
            return [f"Erweiterung {only} nicht im Paket gefunden"]
    for plugin in found:
        pid = plugin.plugin_id
        if plugin.problem is not None:
            problems.append(f"{pid}: {plugin.problem}")
            continue
        manifest = plugin.manifest
        if manifest is not None:
            compat = check_compat(manifest, core=core)
            if compat is not None:
                problems.append(f"{pid}: {compat}")
            has_web = plugin.web_manifest is not None and bool(plugin.web_manifest.contributions)
            needs_i18n = (
                has_web or "core.settings_sections" in manifest.capabilities.extension_points
            )
            if needs_i18n and "en" not in plugin.locales:
                problems.append(f"{pid}: i18n/en.json fehlt")
        ep = _plugin_entry_point(pid, plugin.entry, candidates)
        if ep is None:
            problems.append(f"{pid}: Einstiegspunkt nicht gefunden")
            continue
        if strict:
            for name in _core_requirements(ep):
                problems.append(
                    f"{pid}: verlangt „{name}“ als Abhängigkeit; Tiqora bringt das selbst mit "
                    "(Plugin-Paket darf es nicht festlegen)"
                )
        if import_check:
            try:
                await run_in_daemon_thread(ep.load, timeout=timeout, label=pid)
            except Exception as exc:  # incl. PluginLoadTimeout, RegistrationRejected
                problems.append(f"{pid}: Import fehlgeschlagen: {exc}")
    return problems
