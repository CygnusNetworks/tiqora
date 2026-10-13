"""Runtime plugin directory: materialising packages, activating them on ``sys.path`` (D5).

Filesystem part only. Layout below ``settings.plugins_dir``::

    <plugin_id>/<version>-<sha256[:12]>/site/...   unpacked wheel (purelib layout)
    <plugin_id>/<version>-<sha256[:12]>/COMPLETE   written last; content = full sha256
    .tmp-<uuid>/                                   extraction in progress (renamed into place)
    .probe-<uuid>                                  write probe (created and deleted)
"""

from __future__ import annotations

import contextlib
import importlib
import os
import re
import shutil
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tiqora.config import Settings
from tiqora.plugins.discovery import clear_discovery_cache
from tiqora.plugins.package import PackageError, read_tqplugin, unpack_wheel

_STATUS_TTL: Final = 60.0
_TMP_MAX_AGE: Final = 3600.0
_SAFE_PART: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")
_SHA_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_VERSION_DIR_RE: Final = re.compile(r"^.+-[0-9a-f]{12}$")

_status_cache: dict[tuple[str, bool], tuple[float, DirStatus]] = {}


@dataclass(frozen=True, slots=True)
class DirStatus:
    usable: bool
    writable: bool
    reason: str | None  # German


def reset_dir_status_cache() -> None:
    _status_cache.clear()


def _probe(root: Path) -> bool:
    probe = root / f".probe-{uuid.uuid4().hex}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        probe.unlink()
    except OSError:
        return False
    return True


def plugins_dir_status(settings: Settings) -> DirStatus:
    """Whether runtime install is possible here; cached per directory for 60 s."""
    key = (settings.plugins_dir, settings.plugins_runtime_install)
    now = time.monotonic()
    hit = _status_cache.get(key)
    if hit is not None and now - hit[0] < _STATUS_TTL:
        return hit[1]
    root = Path(settings.plugins_dir)
    if not settings.plugins_runtime_install:
        status = DirStatus(
            False,
            False,
            "Installation zur Laufzeit ist abgeschaltet (TIQORA_PLUGINS_RUNTIME_INSTALL)",
        )
    elif not root.exists():
        status = DirStatus(False, False, f"Verzeichnis {root} existiert nicht")
    elif not root.is_dir():
        status = DirStatus(False, False, f"{root} ist kein Verzeichnis")
    elif _probe(root):
        status = DirStatus(True, True, None)
    else:
        status = DirStatus(True, False, f"Verzeichnis {root} ist schreibgeschützt")
    _status_cache[key] = (now, status)
    return status


def _safe(part: str, what: str) -> str:
    if not _SAFE_PART.match(part) or ".." in part:
        raise PackageError(f"Unzulässiger Wert für {what}")
    return part


def version_dir(settings: Settings, plugin_id: str, version: str, sha256: str) -> Path:
    _safe(plugin_id, "die Erweiterungs-Kennung")
    _safe(version, "die Version")
    if not _SHA_RE.match(sha256):
        raise PackageError("Unzulässige Prüfsumme")
    return Path(settings.plugins_dir) / plugin_id / f"{version}-{sha256[:12]}"


def _is_complete(vdir: Path, sha256: str) -> bool:
    try:
        return (vdir / "COMPLETE").read_text() == sha256 and (vdir / "site").is_dir()
    except OSError:
        return False


def _discard(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def materialize(settings: Settings, plugin_id: str, version: str, sha256: str, data: bytes) -> Path:
    """Unpack ``data`` (a .tqplugin) into its version directory; returns the ``site`` dir.

    Idempotent. The disk is never trusted: a directory without a matching ``COMPLETE`` is
    replaced, and the package is re-read and re-validated before anything is written.
    """
    final = version_dir(settings, plugin_id, version, sha256)
    if _is_complete(final, sha256):
        return final / "site"
    pkg = read_tqplugin(data, max_bytes=settings.plugins_max_package_mb * 1024 * 1024)
    if pkg.sha256 != sha256 or pkg.manifest.id != plugin_id or pkg.manifest.version != version:
        raise PackageError("Das Paket passt nicht zu den angegebenen Daten")
    root = Path(settings.plugins_dir)
    tmp = root / f".tmp-{uuid.uuid4().hex}"
    try:
        tmp.mkdir(mode=0o755)
        unpack_wheel(pkg.wheel_bytes, tmp / "site")
        (tmp / "COMPLETE").write_text(sha256)
        final.parent.mkdir(mode=0o755, exist_ok=True)
        if _is_complete(final, sha256):  # a concurrent winner finished while we unpacked
            return final / "site"
        if final.exists():  # incomplete leftovers (or a stale dir): move aside, then delete
            aside = root / f".tmp-{uuid.uuid4().hex}"
            try:
                os.replace(final, aside)
            except OSError:
                pass
            else:
                _discard(aside)
        try:
            os.replace(tmp, final)
        except OSError:
            if not _is_complete(final, sha256):
                raise PackageError("Das Paket konnte nicht installiert werden") from None
            # a concurrent installer won the race; keep its directory
    finally:
        if tmp.exists():
            _discard(tmp)
    return final / "site"


def activate_paths(site_dirs: Iterable[Path]) -> list[Path]:
    """Append site dirs to ``sys.path`` (never insert: a plugin can't shadow core modules)."""
    added: list[Path] = []
    for site in site_dirs:
        entry = str(site)
        if entry not in sys.path:
            sys.path.append(entry)
            added.append(site)
    importlib.invalidate_caches()
    clear_discovery_cache()
    return added


def gc_runtime_dirs(settings: Settings, keep: Mapping[str, Sequence[str]]) -> int:
    """Remove version dirs not named in ``keep`` (plugin_id -> sha256s), dirs of unknown ids
    and ``.tmp-*``/``.probe-*`` older than 1 h. Only when the directory is writable."""
    if not plugins_dir_status(settings).writable:
        return 0
    root = Path(settings.plugins_dir)
    removed = 0
    now = time.time()
    for entry in sorted(root.iterdir()):
        if entry.name.startswith((".tmp-", ".probe-")):
            try:
                old = now - entry.stat().st_mtime > _TMP_MAX_AGE
            except OSError:
                continue
            if old:
                if entry.is_dir():
                    _discard(entry)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
            continue
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        wanted = {sha[:12] for sha in keep.get(entry.name, ())}
        for child in sorted(entry.iterdir()):
            if not child.is_dir() or not _VERSION_DIR_RE.match(child.name):
                continue
            if child.name.rsplit("-", 1)[1] in wanted:
                continue
            _discard(child)
            removed += 1
        with contextlib.suppress(OSError):
            entry.rmdir()  # only succeeds when empty
    return removed
