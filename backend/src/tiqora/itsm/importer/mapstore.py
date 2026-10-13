"""Idempotency map (``tiqora_itsm_import_map``): which Tiqora row a Znuny record became.

``fingerprint`` hashes a source record so a re-run can tell unchanged from changed records.
``MapStore`` reads/writes the map; in ``read_only`` mode (dry run) writes only go to an
in-memory overlay that reads see as well.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraItsmImportMap

CHUNK = 500


def _plain(value: Any) -> Any:
    """Dataclasses -> dicts, bytes -> sha256 hex, recursively."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return hashlib.sha256(bytes(value)).hexdigest()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(v) for v in value]
        if isinstance(value, (set, frozenset)):
            items.sort(key=lambda x: json.dumps(x, sort_keys=True, default=str))
        return items
    return value


def fingerprint(record: Any) -> str:
    """sha256 over the canonical JSON of a dataclass or mapping (bytes fields hashed first)."""
    blob = json.dumps(_plain(record), sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MapEntry:
    target_id: int
    fingerprint: str


class MapStore:
    def __init__(self, session: AsyncSession, *, run_id: int, read_only: bool) -> None:
        self._session = session
        self._run_id = run_id
        self._read_only = read_only
        self._overlay: dict[tuple[str, str], MapEntry] = {}

    async def get(self, kind: str, source_id: str) -> MapEntry | None:
        hit = self._overlay.get((kind, source_id))
        if hit is not None:
            return hit
        row = (
            await self._session.execute(
                select(TiqoraItsmImportMap).where(
                    TiqoraItsmImportMap.source_kind == kind,
                    TiqoraItsmImportMap.source_id == source_id,
                )
            )
        ).scalar_one_or_none()
        return None if row is None else MapEntry(int(row.target_id), row.fingerprint)

    async def get_many(self, kind: str, source_ids: Sequence[str]) -> dict[str, MapEntry]:
        found: dict[str, MapEntry] = {}
        ids = list(dict.fromkeys(source_ids))
        for start in range(0, len(ids), CHUNK):
            part = ids[start : start + CHUNK]
            rows = (
                await self._session.execute(
                    select(TiqoraItsmImportMap).where(
                        TiqoraItsmImportMap.source_kind == kind,
                        TiqoraItsmImportMap.source_id.in_(part),
                    )
                )
            ).scalars()
            for row in rows:
                found[row.source_id] = MapEntry(int(row.target_id), row.fingerprint)
        for sid in ids:
            hit = self._overlay.get((kind, sid))
            if hit is not None:
                found[sid] = hit
        return found

    async def put(self, kind: str, source_id: str, target_id: int, fp: str) -> None:
        entry = MapEntry(target_id, fp)
        if self._read_only:
            self._overlay[(kind, source_id)] = entry
            return
        row = (
            await self._session.execute(
                select(TiqoraItsmImportMap).where(
                    TiqoraItsmImportMap.source_kind == kind,
                    TiqoraItsmImportMap.source_id == source_id,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC).replace(tzinfo=None)
        if row is None:
            self._session.add(
                TiqoraItsmImportMap(
                    source_kind=kind,
                    source_id=source_id,
                    target_id=target_id,
                    fingerprint=fp,
                    run_id=self._run_id,
                    updated_at=now,
                )
            )
        else:
            row.target_id = target_id
            row.fingerprint = fp
            row.run_id = self._run_id
            row.updated_at = now
        await self._session.flush()

    async def all_source_ids(self, kind: str) -> list[str]:
        rows = (
            await self._session.execute(
                select(TiqoraItsmImportMap.source_id).where(TiqoraItsmImportMap.source_kind == kind)
            )
        ).scalars()
        ids = set(rows)
        ids.update(sid for (k, sid) in self._overlay if k == kind)
        return sorted(ids)
