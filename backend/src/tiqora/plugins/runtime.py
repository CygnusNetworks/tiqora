"""Runtime plugin directory: materialising packages, activating them on ``sys.path`` (D5).

Filesystem part only. Layout below ``settings.plugins_dir``::

    <plugin_id>/<version>-<sha256[:12]>/site/...   unpacked wheel (purelib layout)
    <plugin_id>/<version>-<sha256[:12]>/COMPLETE   written last; content = full sha256
    .tmp-<uuid>/                                   extraction in progress (renamed into place)
    .probe-<uuid>                                  write probe (created and deleted)
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import re
import shutil
import sys
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tiqora.config import Settings
from tiqora.plugins.discovery import ENTRY_POINT_GROUP, clear_discovery_cache
from tiqora.plugins.package import PackageError, read_tqplugin, unpack_wheel

log = structlog.get_logger(__name__)

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


def runtime_dir_problem(settings: Settings) -> str | None:
    """German reason why nothing can be installed in this process, or ``None``."""
    status = plugins_dir_status(settings)
    if not status.usable or not status.writable:
        return status.reason or "Installation zur Laufzeit ist nicht möglich"
    return None


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
    if added:
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


async def gc_runtime_dirs_at_boot(
    settings: Settings, factory: async_sessionmaker[AsyncSession]
) -> int:
    """F8/D9: at boot, remove the version dirs nobody needs any more.

    Kept: the current and the previously installed package of every installed runtime plugin
    (``keep_shas``), every ``id@sha`` a live process reported (``tiqora_process``) and what
    this process has on ``sys.path``. Nothing is removed while a plugins-lane job is queued
    or running: an install may have unpacked a directory its plugin row does not name yet.
    Best effort: any error is logged and ignored, the boot goes on.
    """
    from tiqora.plugins import packages_store, restart

    if not settings.plugins_dir or not plugins_dir_status(settings).writable:
        return 0
    try:
        async with factory() as session:
            if await restart.plugin_job_active(session):
                await session.rollback()
                log.info("plugin_runtime_gc_skipped", reason="plugin job active")
                return 0
            keep = await packages_store.keep_shas(session)
            live = await restart.live_loaded_packages(session, now=restart.utcnow())
            await session.rollback()
        wanted: dict[str, set[str]] = {pid: set(shas) for pid, shas in keep.items()}
        for pid, shas in live.items():
            wanted.setdefault(pid, set()).update(shas)
        for entry in restart.loaded_packages_from_sys_path(settings.plugins_dir):
            pid, _, sha = entry.partition("@")
            wanted.setdefault(pid, set()).add(sha)
        removed = await asyncio.to_thread(
            gc_runtime_dirs, settings, {pid: sorted(shas) for pid, shas in wanted.items()}
        )
    except Exception as exc:  # noqa: BLE001 - cleanup must never stop a boot
        log.warning("plugin_runtime_gc_failed", error=f"{type(exc).__name__}: {exc}"[:300])
        return 0
    if removed:
        log.info("plugin_runtime_gc", removed=removed)
    return removed


# --- activation (D5, F1) --------------------------------------------------------------------

MISSING_PACKAGE_TEXT: Final = "Paketdateien fehlen – bitte erneut installieren"
NEXT_START_TEXT: Final = "Paketdateien werden beim nächsten Start bereitgestellt"


@dataclass(frozen=True, slots=True)
class RuntimeActivation:
    site_dirs: dict[str, Path] = field(default_factory=dict)  # plugin_id -> active site dir
    problems: dict[str, str] = field(default_factory=dict)  # plugin_id -> German problem


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def is_runtime_entry_point(ep: EntryPoint, settings: Settings) -> bool:
    """F1: an entry point whose distribution lives below ``plugins_dir`` is a runtime plugin."""
    dist = ep.dist
    if dist is None:
        return False
    try:
        location = Path(str(dist.locate_file(""))).resolve()
        root = Path(settings.plugins_dir).resolve()
    except (OSError, ValueError):
        return False
    return _is_below(location, root)


def image_plugin_ids(settings: Settings) -> set[str]:
    """Ids of ``tiqora.plugins`` entry points shipped outside the runtime plugins directory."""
    return {
        ep.name
        for ep in entry_points(group=ENTRY_POINT_GROUP)
        if not is_runtime_entry_point(ep, settings)
    }


def has_active_site_dir(settings: Settings, plugin_id: str) -> bool:
    """Whether ``sys.path`` already holds a site dir of this plugin (one id, one dir)."""
    roots = {str(Path(settings.plugins_dir) / plugin_id)}
    with contextlib.suppress(OSError):
        roots.add(str(Path(settings.plugins_dir).resolve() / plugin_id))
    return any(entry.startswith(root + os.sep) for entry in sys.path for root in roots)


async def activate_runtime_plugins(
    settings: Settings,
    factory: async_sessionmaker[AsyncSession],
    *,
    materialize: bool = True,
    status_only: bool = False,
) -> RuntimeActivation:
    async with factory() as session:
        return await activate_runtime_plugins_in(
            session, settings, materialize=materialize, status_only=status_only
        )


async def activate_runtime_plugins_in(
    session: AsyncSession,
    settings: Settings,
    *,
    materialize: bool = True,
    status_only: bool = False,
) -> RuntimeActivation:
    """Put the site dirs of all installed runtime packages on ``sys.path`` (no plugin import).

    ``materialize=False`` never unpacks (a missing dir is reported). ``status_only`` is for an
    already booted process (admin list, boot retry, uninstall job): problems are reported,
    nothing is written to the database and an id that already has a site dir on ``sys.path``
    keeps it (never a second dir for one id). Missing plugin tables give an empty result.
    """
    from tiqora.plugins import packages_store
    from tiqora.plugins.bootstrap import is_missing_table_error

    try:
        rows = await packages_store.installed_runtime_rows(session)
    except Exception as exc:
        if is_missing_table_error(exc):
            await session.rollback()  # PostgreSQL: the failed statement aborted the transaction
            return RuntimeActivation()
        raise
    if not rows:
        return RuntimeActivation()
    images = image_plugin_ids(settings)  # decided by location, before any path is added
    status = plugins_dir_status(settings)
    site_dirs: dict[str, Path] = {}
    problems: dict[str, str] = {}
    shadowed = False
    for row in rows:
        sha = row.package_sha256
        if sha is None:
            continue
        if row.id in images:  # Assumption 5: the image wins
            if not status_only:  # CLI status runs stay quiet (stdout carries --json)
                log.warning("plugin_runtime_package_shadowed", plugin=row.id)
                row.source = "image"
                row.package_sha256 = None
                row.repository_id = None
                shadowed = True
            continue
        if status_only and has_active_site_dir(settings, row.id):
            continue
        try:
            vdir = version_dir(settings, row.id, row.version, sha)
        except PackageError as exc:
            problems[row.id] = str(exc)
            continue
        if _is_complete(vdir, sha):
            site_dirs[row.id] = vdir / "site"
            continue
        if not status.writable:
            problems[row.id] = f"Paketdateien fehlen: {status.reason}"
            continue
        if not materialize:
            problems[row.id] = NEXT_START_TEXT
            continue
        data = await packages_store.get_package_data(session, sha)
        if data is None:
            problems[row.id] = MISSING_PACKAGE_TEXT
            continue
        try:
            site_dirs[row.id] = await asyncio.to_thread(
                materialize_package, settings, row.id, row.version, sha, data
            )
        except (PackageError, OSError) as exc:
            log.warning("plugin_runtime_materialize_failed", plugin=row.id, error=str(exc)[:300])
            problems[row.id] = f"Paketdateien konnten nicht bereitgestellt werden: {exc}"[:500]
    if shadowed:
        await session.commit()
    activate_paths(site_dirs.values())
    return RuntimeActivation(site_dirs, problems)


materialize_package = materialize


async def activate_best_effort(
    settings: Settings,
    *,
    timeout: float = 5.0,  # noqa: ASYNC109 - bounds the whole database round trip
    materialize: bool = True,
    status_only: bool = False,
) -> RuntimeActivation:
    """CLI helper: activate installed runtime packages; any failure means "image plugins only"."""
    from tiqora.plugins.migrations import plugin_session_factory

    async def run() -> RuntimeActivation:
        async with plugin_session_factory(settings.database_url) as factory:
            return await activate_runtime_plugins(
                settings, factory, materialize=materialize, status_only=status_only
            )

    try:
        return await asyncio.wait_for(run(), timeout)
    except Exception:  # noqa: BLE001 - no database, no tables, timeout; CLI stdout stays clean
        return RuntimeActivation()
