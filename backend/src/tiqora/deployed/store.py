"""Store for deployed process/ACL revisions (``tiqora_*_revision``).

The newest revision per kind is the active one. ``load_active_revision`` costs one
small SELECT (newest id + checksum, no ``config``) and reads ``config`` again only when
that pair changed. Every call parses its own copy of the cached JSON, so a caller that
mutates the dump cannot change what the next request runs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraAclRevision, TiqoraProcessRevision
from tiqora.jobs.service import utc_now
from tiqora.process.dump import canonical_json, dump_checksum, dump_entity_count

RevisionKind = Literal["process", "acl"]
RevisionSource = Literal["initial", "znuny", "tiqora"]

_MODELS: dict[str, type[TiqoraProcessRevision] | type[TiqoraAclRevision]] = {
    "process": TiqoraProcessRevision,
    "acl": TiqoraAclRevision,
}


@dataclass(frozen=True, slots=True)
class RevisionInfo:
    id: int
    kind: RevisionKind
    source: str
    checksum: str
    entity_count: int
    deployed_at: datetime  # naive UTC
    deployed_by: int | None


@dataclass(frozen=True, slots=True)
class LoadedRevision:
    info: RevisionInfo
    dump: Mapping[str, Any]  # parsed JSON, a fresh copy per load_active_revision call


@dataclass(frozen=True, slots=True)
class _CachedRevision:
    info: RevisionInfo
    config: str  # the stored canonical JSON


_cache: dict[str, _CachedRevision] = {}


def clear_revision_cache() -> None:
    _cache.clear()


def _model(kind: RevisionKind) -> type[TiqoraProcessRevision] | type[TiqoraAclRevision]:
    return _MODELS[kind]


async def latest_revision_info(session: AsyncSession, kind: RevisionKind) -> RevisionInfo | None:
    model = _model(kind)
    # Explicit column list: ``config`` is deferred and must not be touched here.
    row = (
        await session.execute(
            select(
                model.id,
                model.source,
                model.checksum,
                model.entity_count,
                model.deployed_at,
                model.deployed_by,
            )
            .order_by(model.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return RevisionInfo(
        id=row.id,
        kind=kind,
        source=row.source,
        checksum=row.checksum,
        entity_count=row.entity_count,
        deployed_at=row.deployed_at,
        deployed_by=row.deployed_by,
    )


async def load_active_revision(session: AsyncSession, kind: RevisionKind) -> LoadedRevision | None:
    info = await latest_revision_info(session, kind)
    if info is None:
        _cache.pop(kind, None)
        return None
    cached = _cache.get(kind)
    # Id and checksum: ids alone could repeat after a database was recreated.
    if cached is None or cached.info.id != info.id or cached.info.checksum != info.checksum:
        model = _model(kind)
        raw = (await session.execute(select(model.config).where(model.id == info.id))).scalar_one()
        cached = _CachedRevision(info=info, config=raw)
        _cache[kind] = cached
    # A copy per call: the dump's nested dicts end up in EntityRecord.data / ACL rules, and a
    # shared object mutated by one caller would silently change the deployed revision.
    return LoadedRevision(info=cached.info, dump=json.loads(cached.config))


async def record_revision(
    session: AsyncSession,
    kind: RevisionKind,
    *,
    dump: Mapping[str, Any],
    source: RevisionSource,
    deployed_by: int | None,
) -> RevisionInfo:
    """Insert a revision (flush only; the caller commits)."""
    model = _model(kind)
    row = model(
        source=source,
        checksum=dump_checksum(dump),
        entity_count=dump_entity_count(dump),
        config=canonical_json(dump),
        deployed_at=utc_now().replace(microsecond=0),  # MariaDB DATETIME has no fraction
        deployed_by=deployed_by,
    )
    session.add(row)
    await session.flush()
    return RevisionInfo(
        id=row.id,
        kind=kind,
        source=row.source,
        checksum=row.checksum,
        entity_count=row.entity_count,
        deployed_at=row.deployed_at,
        deployed_by=row.deployed_by,
    )
