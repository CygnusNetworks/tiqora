"""Read-only access to Znuny statistics in ``xml_storage`` (no JOIN with tiqora_* tables)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.reporting.znuny_stats.model import ZnunyStat, stat_from_xml_hash
from tiqora.reporting.znuny_stats.xml_hash import XmlHashError, build_xml_hash

MAX_PREVIEW_STATS: Final = 500
_CHUNK: Final = 200


@dataclass(frozen=True, slots=True)
class StatLoadError:
    stat_id: int
    message: str  # German


async def list_stat_ids(session: AsyncSession) -> list[int]:
    """Numeric ``xml_key`` values of all stored statistics, sorted, capped at MAX_PREVIEW_STATS."""
    result = await session.execute(
        text("SELECT DISTINCT xml_key FROM xml_storage WHERE xml_type = 'Stats'")
    )
    ids = sorted({int(k) for (k,) in result.all() if str(k).isdigit()})
    return ids[:MAX_PREVIEW_STATS]


async def load_stats(
    session: AsyncSession, stat_ids: Sequence[int]
) -> tuple[list[ZnunyStat], list[StatLoadError]]:
    """Load and parse the given statistics; a broken one becomes a StatLoadError."""
    ids = sorted(set(stat_ids))
    stmt = text(
        "SELECT xml_key, xml_content_key, xml_content_value FROM xml_storage "
        "WHERE xml_type = 'Stats' AND xml_key IN :keys ORDER BY xml_key, xml_content_key"
    ).bindparams(bindparam("keys", expanding=True))
    rows_by_id: dict[int, list[tuple[str, str | None]]] = defaultdict(list)
    for start in range(0, len(ids), _CHUNK):
        chunk = [str(i) for i in ids[start : start + _CHUNK]]
        for key, content_key, value in (await session.execute(stmt, {"keys": chunk})).all():
            if str(key).isdigit():
                rows_by_id[int(key)].append((content_key, value))
    stats: list[ZnunyStat] = []
    errors: list[StatLoadError] = []
    for stat_id in ids:
        rows = rows_by_id.get(stat_id)
        if not rows:
            errors.append(StatLoadError(stat_id, "Die Statistik wurde nicht gefunden."))
            continue
        try:
            xml_hash = build_xml_hash(rows)
            stats.append(stat_from_xml_hash(xml_hash, origin="db", stat_id=stat_id))
        except XmlHashError as exc:
            errors.append(StatLoadError(stat_id, exc.message))
    return stats, errors
