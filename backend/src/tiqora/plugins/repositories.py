"""Package sources: the key ring built from verified repository indexes (D8).

Only ``build_key_ring`` exists so far; configuration, fetching and refresh follow in later tasks.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraPluginRepository
from tiqora.plugins.inspect import PackageReport
from tiqora.plugins.repo_index import IndexFormatError, parse_index, publisher_keys
from tiqora.plugins.signing import (
    OFFICIAL_KEYS,
    KeyRing,
    PublisherKey,
    SignatureError,
    TrustLevel,
    local_keys,
    parse_public_key,
)

log = structlog.get_logger(__name__)


async def build_key_ring_named(
    session: AsyncSession, settings: Settings
) -> tuple[KeyRing, dict[int, str]]:
    """The key ring plus ``repository id -> name`` of the repositories that contributed keys.

    Publisher keys come only from enabled repositories whose last accepted index verified against
    the repository's own key (D7); an unverified index never gives a key any weight.
    """
    official = {}
    for kid, text in OFFICIAL_KEYS.items():
        try:
            official[kid] = parse_public_key(text)
        except SignatureError:  # pragma: no cover - the constant is ours
            log.warning("plugin_official_key_unreadable", key_id=kid)
    rows = (
        (
            await session.execute(
                select(TiqoraPluginRepository)
                .where(
                    TiqoraPluginRepository.enabled.is_(True),
                    TiqoraPluginRepository.index_verified.is_(True),
                    TiqoraPluginRepository.index_json.is_not(None),
                )
                .order_by(TiqoraPluginRepository.id)
            )
        )
        .scalars()
        .all()
    )
    publishers: list[PublisherKey] = []
    names: dict[int, str] = {}
    for row in rows:
        try:
            index = parse_index((row.index_json or "").encode())
            keys = publisher_keys(index, row.id)
        except (IndexFormatError, SignatureError, ValueError) as exc:
            log.warning(
                "plugin_repository_index_unreadable", repository=row.id, error=str(exc)[:200]
            )
            continue
        publishers += keys
        names[row.id] = row.name
    ring = KeyRing(official=official, local=local_keys(settings), publishers=publishers)
    return ring, names


async def build_key_ring(session: AsyncSession, settings: Settings) -> KeyRing:
    return (await build_key_ring_named(session, settings))[0]


def with_repository_signer(
    report: PackageReport, ring: KeyRing, names: dict[int, str]
) -> PackageReport:
    """F10: an upload signed with a publisher key names the repository that vouches for it."""
    if report.trust is not TrustLevel.VERIFIED_PUBLISHER or report.key_id is None:
        return report
    repo_ids = sorted(p.repository_id for p in ring.publishers if p.key_id == report.key_id)
    name = next((names[r] for r in repo_ids if r in names), None)
    if name is None or report.signer is None:
        return report
    return report.model_copy(update={"signer": f"{report.signer} (über Paketquelle {name})"})
