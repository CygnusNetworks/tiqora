"""Search index of the current CI version (D6): one ``tiqora_cmdb_ci_attr`` row per value."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.definition import INT_ID_TYPES, AttributeDef, parse_definition_json
from tiqora.db.tiqora.models import (
    TiqoraCmdbCi,
    TiqoraCmdbCiAttr,
    TiqoraCmdbCiVersion,
    TiqoraCmdbClass,
    TiqoraCmdbClassDefinition,
)

_TEXT_MAX: Final = 500
_KEY_MAX: Final = 191
#: ``value_num`` is ``Numeric(30, 6)``: 24 integer digits fit, anything larger is not indexed
_NUM_LIMIT: Final = Decimal(10) ** 24
_NUMERIC_TYPES: Final = frozenset({"integer", "decimal", "money"}) | INT_ID_TYPES
_BATCH: Final = 500


@dataclass(frozen=True, slots=True)
class IndexRow:
    attr_path: str
    position: int
    value_text: str
    value_key: str
    value_num: Decimal | None
    value_date: datetime | None


def _num(a: AttributeDef, v: Any) -> Decimal | None:
    if a.type not in _NUMERIC_TYPES or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return None
    if not d.is_finite() or abs(d) >= _NUM_LIMIT:
        return None
    return d


def _date(a: AttributeDef, v: Any) -> datetime | None:
    if a.type not in ("date", "datetime") or not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def build_index_rows(attrs: list[AttributeDef], values: Mapping[str, Any]) -> list[IndexRow]:
    """Rows for every **searchable** attribute path and entry, sorted by ``(path, position)``.

    ``position`` is the running 1-based index within the dotted path (``NIC.IPAddress`` counts
    across all NIC entries). Keys of ``values`` the definition does not know are ignored.
    """
    counters: dict[str, int] = {}
    rows: list[IndexRow] = []

    def walk(items: list[AttributeDef], vals: Mapping[str, Any], prefix: str) -> None:
        for a in items:
            entries = vals.get(a.key)
            if not isinstance(entries, list):
                continue
            path = f"{prefix}{a.key}"
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                v = entry.get("v")
                if a.searchable and v is not None and not isinstance(v, (dict, list)):
                    text_value = str(v)
                    counters[path] = counters.get(path, 0) + 1
                    rows.append(
                        IndexRow(
                            attr_path=path,
                            position=counters[path],
                            value_text=text_value[:_TEXT_MAX],
                            value_key=text_value.casefold()[:_KEY_MAX],
                            value_num=_num(a, v),
                            value_date=_date(a, v),
                        )
                    )
                sub = entry.get("s")
                if a.sub and isinstance(sub, Mapping):
                    walk(a.sub, sub, f"{path}.")

    walk(attrs, values, "")
    rows.sort(key=lambda r: (r.attr_path, r.position))
    return rows


async def write_index(
    session: AsyncSession, ci_id: int, attrs: list[AttributeDef], values: Mapping[str, Any]
) -> None:
    """Replace the index rows of one CI (``DELETE`` + bulk ``INSERT``)."""
    await session.execute(
        delete(TiqoraCmdbCiAttr)
        .where(TiqoraCmdbCiAttr.ci_id == ci_id)
        .execution_options(synchronize_session=False)
    )
    rows = build_index_rows(attrs, values)
    if rows:
        await session.execute(
            insert(TiqoraCmdbCiAttr),
            [
                {
                    "ci_id": ci_id,
                    "attr_path": r.attr_path,
                    "position": r.position,
                    "value_text": r.value_text,
                    "value_key": r.value_key,
                    "value_num": r.value_num,
                    "value_date": r.value_date,
                }
                for r in rows
            ],
        )


async def rebuild_index(session: AsyncSession, ci_ids: Sequence[int]) -> int:
    """Rebuild the index of the given CIs from their current version and their class's
    **current** definition (which is what a changed ``searchable`` flag needs). Returns the
    number of CIs rebuilt; unknown ids and CIs without a version are skipped."""
    done = 0
    definitions: dict[int, list[AttributeDef]] = {}
    ids = sorted({int(i) for i in ci_ids})
    for start in range(0, len(ids), _BATCH):
        chunk = ids[start : start + _BATCH]
        found = (
            await session.execute(
                select(
                    TiqoraCmdbCi.id, TiqoraCmdbCi.class_id, TiqoraCmdbCi.current_version_id
                ).where(TiqoraCmdbCi.id.in_(chunk))
            )
        ).all()
        version_ids = [int(r[2]) for r in found if r[2] is not None]
        docs: dict[int, str] = {}
        if version_ids:
            docs = {
                int(i): str(a)
                for i, a in (
                    await session.execute(
                        select(TiqoraCmdbCiVersion.id, TiqoraCmdbCiVersion.attributes).where(
                            TiqoraCmdbCiVersion.id.in_(version_ids)
                        )
                    )
                ).all()
            }
        for ci_id, class_id, version_id in found:
            if version_id is None or int(version_id) not in docs:
                continue
            class_id = int(class_id)
            if class_id not in definitions:
                definitions[class_id] = await _current_definition(session, class_id)
            values = json.loads(docs[int(version_id)])
            await write_index(session, int(ci_id), definitions[class_id], values)
            done += 1
    return done


async def _current_definition(session: AsyncSession, class_id: int) -> list[AttributeDef]:
    raw = (
        await session.execute(
            select(TiqoraCmdbClassDefinition.attributes)
            .join(
                TiqoraCmdbClass,
                TiqoraCmdbClass.current_definition_id == TiqoraCmdbClassDefinition.id,
            )
            .where(TiqoraCmdbClass.id == class_id)
        )
    ).scalar_one_or_none()
    return [] if raw is None else parse_definition_json(str(raw))


__all__ = ["IndexRow", "build_index_rows", "rebuild_index", "write_index"]
