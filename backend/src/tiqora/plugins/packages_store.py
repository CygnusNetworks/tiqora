"""Plugin packages (``.tqplugin``) kept in the database, both dialects.

Only ``tiqora_*`` tables are touched, and never joined with each other: the keys are
loaded first and then used in IN lists.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraPlugin, TiqoraPluginPackage
from tiqora.plugins.inspect import PackageReport

RUNTIME_SOURCES = ("upload", "repository")

PACKET_TOO_LARGE_TEXT = (
    "Paket zu groß für die Datenbank-Einstellung max_allowed_packet; "
    "bitte erhöhen oder TIQORA_PLUGINS_MAX_PACKAGE_MB senken"
)
_PACKET_ERRORS = (1153, 2013)  # ER_NET_PACKET_TOO_LARGE, CR_SERVER_LOST (aiomysql)


class PackageTooLargeForDatabase(Exception):
    """The blob exceeds the MariaDB ``max_allowed_packet``; ``str()`` is the German text."""

    def __init__(self) -> None:
        super().__init__(PACKET_TOO_LARGE_TEXT)


def _is_packet_error(exc: DBAPIError) -> bool:
    args: tuple[object, ...] = tuple(getattr(exc.orig, "args", ()))
    return bool(args) and args[0] in _PACKET_ERRORS


async def store_package(
    session: AsyncSession,
    *,
    data: bytes,
    report: PackageReport,
    manifest_text: str,
    source: str,
    repository_id: int | None,
    uploaded_by: int | None,
    now: datetime,
) -> TiqoraPluginPackage:
    """Insert the package, or refresh the report of the row that already has this hash.

    Raises ``PackageTooLargeForDatabase`` when MariaDB rejects the blob (1153/2013). After
    2013 the connection is gone: the caller must roll back the session.
    """
    if source not in RUNTIME_SOURCES:
        raise ValueError(f"unknown package source {source!r}")
    existing = (
        await session.execute(
            select(TiqoraPluginPackage).where(TiqoraPluginPackage.sha256 == report.sha256)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.report = report.model_dump_json()
        existing.signature_status = report.trust.value
        existing.key_id = report.key_id
        await session.flush()
        return existing
    row = TiqoraPluginPackage(
        sha256=report.sha256,
        plugin_id=report.plugin_id,
        version=report.version,
        size_bytes=len(data),
        data=data,
        manifest=manifest_text,
        report=report.model_dump_json(),
        signature_status=report.trust.value,
        key_id=report.key_id,
        source=source,
        repository_id=repository_id,
        uploaded_by=uploaded_by,
        uploaded_at=now,
        installed_at=None,
    )
    session.add(row)
    try:
        await session.flush()
    except DBAPIError as exc:
        if session.bind.dialect.name in ("mysql", "mariadb") and _is_packet_error(exc):
            raise PackageTooLargeForDatabase from exc
        raise
    return row


async def get_package(session: AsyncSession, sha256: str) -> TiqoraPluginPackage | None:
    """The row without its blob (``data`` stays deferred)."""
    return (
        await session.execute(
            select(TiqoraPluginPackage).where(TiqoraPluginPackage.sha256 == sha256)
        )
    ).scalar_one_or_none()


async def get_package_data(session: AsyncSession, sha256: str) -> bytes | None:
    """Only the blob column."""
    return (
        await session.execute(
            select(TiqoraPluginPackage.data).where(TiqoraPluginPackage.sha256 == sha256)
        )
    ).scalar_one_or_none()


async def _referenced(session: AsyncSession, shas: list[str]) -> set[str]:
    if not shas:
        return set()
    rows = await session.execute(
        select(TiqoraPlugin.package_sha256).where(TiqoraPlugin.package_sha256.in_(shas))
    )
    return {r for r in rows.scalars() if r is not None}


async def delete_package(session: AsyncSession, sha256: str) -> bool:
    """Delete a package; False when it is installed (referenced) or does not exist."""
    if await _referenced(session, [sha256]):
        return False
    result = await session.execute(
        delete(TiqoraPluginPackage).where(TiqoraPluginPackage.sha256 == sha256)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def purge_stale_packages(session: AsyncSession, *, older_than: datetime) -> int:
    """Remove packages that were never installed, are older than the cutoff, unreferenced."""
    candidates = list(
        (
            await session.execute(
                select(TiqoraPluginPackage.sha256).where(
                    TiqoraPluginPackage.installed_at.is_(None),
                    TiqoraPluginPackage.uploaded_at < older_than,
                )
            )
        ).scalars()
    )
    referenced = await _referenced(session, candidates)
    doomed = [s for s in candidates if s not in referenced]
    if not doomed:
        return 0
    result = await session.execute(
        delete(TiqoraPluginPackage).where(TiqoraPluginPackage.sha256.in_(doomed))
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def installed_runtime_rows(session: AsyncSession) -> list[TiqoraPlugin]:
    """Plugins installed from a package (not from the image)."""
    return list(
        (
            await session.execute(
                select(TiqoraPlugin)
                .where(
                    TiqoraPlugin.source.in_(RUNTIME_SOURCES),
                    TiqoraPlugin.package_sha256.is_not(None),
                )
                .order_by(TiqoraPlugin.id)
            )
        ).scalars()
    )


async def mark_package_installed(session: AsyncSession, sha256: str, *, now: datetime) -> None:
    await session.execute(
        update(TiqoraPluginPackage)
        .where(TiqoraPluginPackage.sha256 == sha256)
        .values(installed_at=now)
    )


async def unmark_packages(session: AsyncSession, plugin_id: str) -> None:
    """After an uninstall: all packages of the plugin count as never installed."""
    await session.execute(
        update(TiqoraPluginPackage)
        .where(TiqoraPluginPackage.plugin_id == plugin_id)
        .values(installed_at=None)
    )


async def keep_shas(session: AsyncSession) -> dict[str, list[str]]:
    """plugin_id -> [current sha, newest previously installed sha] (for the dir GC)."""
    rows = await installed_runtime_rows(session)
    if not rows:
        return {}
    current = {r.id: r.package_sha256 for r in rows if r.package_sha256}
    previous = (
        await session.execute(
            select(
                TiqoraPluginPackage.plugin_id,
                TiqoraPluginPackage.sha256,
                TiqoraPluginPackage.installed_at,
            )
            .where(
                TiqoraPluginPackage.plugin_id.in_(list(current)),
                TiqoraPluginPackage.installed_at.is_not(None),
            )
            .order_by(TiqoraPluginPackage.installed_at.desc(), TiqoraPluginPackage.sha256)
        )
    ).all()
    out: dict[str, list[str]] = defaultdict(list)
    for pid, sha in current.items():
        out[pid].append(sha)
    seen_prev: set[str] = set()
    for pid, sha, _at in previous:
        if sha != current[pid] and pid not in seen_prev:
            out[pid].append(sha)
            seen_prev.add(pid)
    return dict(out)
