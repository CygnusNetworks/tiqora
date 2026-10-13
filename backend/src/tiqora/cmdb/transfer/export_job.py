"""CSV/XLSX export of the CIs of one class as a background job (D9).

The header uses the import syntax (``attr:NIC[2].IPAddress``), so the exported file re-imports
with :func:`tiqora.cmdb.transfer.mapping.default_mapping_for_header` and identifier ``number``.
References are written the way the import reads them: CI number, name of a catalog entry,
service, SLA, queue, priority, ticket type or ticket state, login of an agent, customer login
or customer id. Date-times are canonical UTC. ``group`` and ``attachment`` attributes have no
column of their own (a group only carries its sub-attributes).

The permission is evaluated when the job runs, not when it was queued. Columns are built from the
entries that exist in the result (two passes over the CIs), at most ``ENTRY_CAP`` entries per
path level; ``result["entries_cut"]`` tells that more entries existed. Formula-looking cells get
the same guard as the ticket export (F13), the import strips it again.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import load_access, require_class_access
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.definition import AttributeDef, iter_attribute_paths
from tiqora.cmdb.errors import CmdbForbidden, CmdbNotFound
from tiqora.cmdb.schemas import CiSearchCriteria
from tiqora.cmdb.search import ci_search_statement
from tiqora.cmdb.transfer.mapping import ColumnTarget, format_target
from tiqora.cmdb.values import collect_references
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraCmdbCatalog,
    TiqoraCmdbCi,
    TiqoraCmdbCiVersion,
)
from tiqora.filters import Filter, FilterError
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.reporting.export import FORMULA_PREFIXES

ENTRY_CAP: Final = 10
MAX_ROWS: Final = 200_000
PROGRESS_EVERY_ROWS: Final = 500
_CHUNK: Final = 500
_INT32: Final = 2**31 - 1
FIXED_HEADER: Final = ("number", "name", "class", "deployment_state", "incident_state")
_NAME_COLUMNS: Final[dict[str, Any]] = {
    "service": (Service.id, Service.name),
    "sla": (Sla.id, Sla.name),
    "queue": (Queue.id, Queue.name),
    "priority": (TicketPriority.id, TicketPriority.name),
    "ticket_type": (TicketType.id, TicketType.name),
    "state": (TicketState.id, TicketState.name),
    "agent": (Users.id, Users.login),
}
_CONTENT_TYPES: Final = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

PathKey = tuple[tuple[str, int], ...]


class CmdbExportPayload(BaseModel):
    user_id: int
    class_id: int
    filter: Filter | None = None
    q: str | None = None
    file_format: Literal["csv", "xlsx"] = "csv"


@dataclass(slots=True)
class _Row:
    number: str
    name: str
    deployment_id: int
    incident_id: int
    values: dict[str, Any]


@dataclass(slots=True)
class _Names:
    names: dict[tuple[str, int], str] = field(default_factory=dict)
    cis: dict[int, str] = field(default_factory=dict)


def _guard(text: str) -> str:
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _chunks(items: Sequence[Any], size: int = _CHUNK) -> Iterator[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _is_filled(entry: Any) -> bool:
    return isinstance(entry, Mapping) and entry.get("v") not in (None, "")


def observe_paths(
    attrs: list[AttributeDef],
    values: Mapping[str, Any],
    out: set[PathKey],
    *,
    prefix: PathKey = (),
) -> bool:
    """Add the column paths of ``values`` to ``out``; True when entries beyond the cap exist."""
    cut = False
    for a in attrs:
        if a.type == "attachment":
            continue
        entries = values.get(a.key)
        if not isinstance(entries, list):
            continue
        if len(entries) > ENTRY_CAP:
            cut = True
        for idx, entry in enumerate(entries[:ENTRY_CAP], start=1):
            if not isinstance(entry, Mapping):
                continue
            path = (*prefix, (a.key, idx))
            if a.type != "group" and _is_filled(entry):
                out.add(path)
            sub = entry.get("s")
            if a.sub and isinstance(sub, Mapping):
                cut = observe_paths(a.sub, sub, out, prefix=path) or cut
    return cut


def sort_paths(attrs: list[AttributeDef], paths: set[PathKey]) -> list[PathKey]:
    """Definition order, then position: NIC, NIC.IPAddress, NIC.IPAddress[2], NIC[2], ..."""
    order = {dotted: n for n, (dotted, _a) in enumerate(iter_attribute_paths(attrs))}

    def key(path: PathKey) -> tuple[tuple[int, int], ...]:
        return tuple(
            (order.get(".".join(k for k, _ in path[: level + 1]), 0), pos)
            for level, (_k, pos) in enumerate(path)
        )

    return sorted(paths, key=key)


def cell_for(a: AttributeDef, entry: Mapping[str, Any], names: _Names) -> str:
    value = entry.get("v")
    if value is None or isinstance(value, bool):
        return ""
    if a.type == "ci_ref":
        return names.cis.get(value, "") if isinstance(value, int) else ""
    if a.type == "catalog" or a.type in _NAME_COLUMNS:
        return names.names.get((a.type, value), "") if isinstance(value, int) else ""
    return str(value)


def _entry_at(
    attrs: list[AttributeDef], values: Mapping[str, Any], path: PathKey
) -> tuple[AttributeDef, Mapping[str, Any]] | None:
    level_attrs, level_values = attrs, values
    found: tuple[AttributeDef, Mapping[str, Any]] | None = None
    for key, pos in path:
        a = next((x for x in level_attrs if x.key == key), None)
        entries = level_values.get(key)
        if a is None or not isinstance(entries, list) or pos > len(entries):
            return None
        entry = entries[pos - 1]
        if not isinstance(entry, Mapping):
            return None
        found = (a, entry)
        sub = entry.get("s")
        level_attrs, level_values = a.sub, sub if isinstance(sub, Mapping) else {}
    return found


async def _load_names(
    session: AsyncSession, attrs: list[AttributeDef], rows: Sequence[_Row]
) -> _Names:
    out = _Names()
    refs: dict[str, set[int | str]] = {}
    for r in rows:
        for t, found in collect_references(attrs, r.values).items():
            refs.setdefault(t, set()).update(found)
    for t, values in refs.items():
        ints = sorted(
            v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0
        )
        if t in _NAME_COLUMNS:
            id_col, name_col = _NAME_COLUMNS[t]
            for part in _chunks([i for i in ints if i <= _INT32]):
                for i, name in (
                    await session.execute(select(id_col, name_col).where(id_col.in_(part)))
                ).all():
                    out.names[(t, int(i))] = str(name)
        elif t == "catalog":
            for part in _chunks([i for i in ints if i <= _INT32]):
                for i, name in (
                    await session.execute(
                        select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.name).where(
                            TiqoraCmdbCatalog.id.in_(part)
                        )
                    )
                ).all():
                    out.names[(t, int(i))] = str(name)
        elif t == "ci_ref":
            for part in _chunks(ints):
                for i, number in (
                    await session.execute(
                        select(TiqoraCmdbCi.id, TiqoraCmdbCi.number).where(
                            TiqoraCmdbCi.id.in_(part)
                        )
                    )
                ).all():
                    out.cis[int(i)] = str(number)
    return out


async def _state_names(session: AsyncSession) -> dict[int, str]:
    rows = (await session.execute(select(TiqoraCmdbCatalog.id, TiqoraCmdbCatalog.name))).all()
    return {int(i): str(n) for i, n in rows}


async def _load_rows(session: AsyncSession, ids: Sequence[int]) -> list[_Row]:
    stmt = (
        select(
            TiqoraCmdbCi.id,
            TiqoraCmdbCi.number,
            TiqoraCmdbCi.name,
            TiqoraCmdbCi.deployment_state_id,
            TiqoraCmdbCi.incident_state_id,
            TiqoraCmdbCiVersion.attributes,
        )
        .outerjoin(TiqoraCmdbCiVersion, TiqoraCmdbCiVersion.id == TiqoraCmdbCi.current_version_id)
        .where(TiqoraCmdbCi.id.in_(list(ids)))
        .order_by(TiqoraCmdbCi.id)
    )
    out: list[_Row] = []
    for _id, number, name, dep, inc, raw in (await session.execute(stmt)).all():
        try:
            values = json.loads(raw) if raw else {}
        except ValueError:
            values = {}
        out.append(
            _Row(
                str(number),
                str(name),
                int(dep),
                int(inc),
                values if isinstance(values, dict) else {},
            )
        )
    return out


class _Sink:
    """Collects rows into CSV (``;``, BOM, like the ticket export) or a write-only workbook."""

    def __init__(self, file_format: Literal["csv", "xlsx"]) -> None:
        self.format = file_format
        self._buf = io.BytesIO()
        self._text = io.StringIO()
        self._csv = csv.writer(self._text, delimiter=";", lineterminator="\r\n")
        self._wb = Workbook(write_only=True) if file_format == "xlsx" else None
        self._ws = None if self._wb is None else self._wb.create_sheet("CMDB")
        if file_format == "csv":
            self._buf.write(b"\xef\xbb\xbf")

    def write(self, cells: Sequence[str]) -> None:
        if self._ws is not None:
            self._ws.append(list(cells))
            return
        self._csv.writerow(cells)
        self._buf.write(self._text.getvalue().encode("utf-8"))
        self._text.seek(0)
        self._text.truncate()

    def finish(self) -> bytes:
        if self._wb is not None:
            self._wb.save(self._buf)
        return self._buf.getvalue()


async def run_cmdb_export(ctx: JobContext, payload: CmdbExportPayload) -> dict[str, Any]:
    async with ctx.session_factory() as session:
        access = await load_access(session, payload.user_id)
        try:
            cls = await require_class_access(session, access, payload.class_id, "ro")
        except (CmdbNotFound, CmdbForbidden) as exc:
            raise JobFailed("Für diese Klasse besteht keine Berechtigung zum Exportieren.") from exc
        attrs = (await ClassService(session).current_definition(payload.class_id)).attributes
        try:
            stmt = await ci_search_statement(
                session,
                access,
                CiSearchCriteria(class_ids=[payload.class_id], filter=payload.filter, q=payload.q),
            )
        except FilterError as exc:
            raise JobFailed(f"Der Filter ist nicht mehr gültig: {exc.message}") from exc
        ids: list[int] = []
        if stmt is not None:
            ids = [
                int(i)
                for i in (
                    await session.execute(
                        stmt.with_only_columns(TiqoraCmdbCi.id, maintain_column_froms=True)
                        .order_by(TiqoraCmdbCi.id)
                        .limit(MAX_ROWS + 1)
                    )
                ).scalars()
            ]
        if len(ids) > MAX_ROWS:
            raise JobFailed(
                f"Der Export hat mehr als {MAX_ROWS} Zeilen. Bitte schränken Sie den Filter ein."
            )
        total = len(ids)
        await ctx.progress(0, total, "CIs werden gelesen …")

        # pass 1: which attribute columns exist
        observed: set[PathKey] = set()
        cut = False
        for part in _chunks(ids):
            for row in await _load_rows(session, part):
                cut = observe_paths(attrs, row.values, observed) or cut
        paths = sort_paths(attrs, observed)
        states = await _state_names(session)

        header = [*FIXED_HEADER, *(format_target(ColumnTarget("attr", p)) for p in paths)]
        sink = _Sink(payload.file_format)
        sink.write(header)

        # pass 2: the rows
        done = 0
        for part in _chunks(ids):
            rows = await _load_rows(session, part)
            names = await _load_names(session, attrs, rows)
            for row in rows:
                cells = [
                    row.number,
                    row.name,
                    cls.name,
                    states.get(row.deployment_id, ""),
                    states.get(row.incident_id, ""),
                ]
                for p in paths:
                    hit = _entry_at(attrs, row.values, p)
                    cells.append("" if hit is None else cell_for(hit[0], hit[1], names))
                sink.write([_guard(c) for c in cells])
                done += 1
                if done % PROGRESS_EVERY_ROWS == 0:
                    await ctx.progress(done, total, "CIs werden gelesen …")
    await ctx.progress(done, done, "Datei wird gespeichert …")
    name = f"cmdb-{cls.class_key}.{payload.file_format}"
    await ctx.add_file(name, _CONTENT_TYPES[payload.file_format], sink.finish())
    return {"rows": done, "columns": len(header), "entries_cut": cut}


CMDB_EXPORT_JOB = JobHandler(
    kind="core.cmdb_export",
    payload_model=CmdbExportPayload,
    run=run_cmdb_export,
    title_key="jobs.kinds.core.cmdb_export",
    timeout_seconds=3600,
    result_private=True,
)

__all__ = [
    "CMDB_EXPORT_JOB",
    "ENTRY_CAP",
    "CmdbExportPayload",
    "observe_paths",
    "run_cmdb_export",
    "sort_paths",
]
