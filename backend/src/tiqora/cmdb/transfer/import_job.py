"""CSV/XLSX import of CIs as a background job (D9).

Pass 1 reads every row, resolves names with one IN-list query per type, matches the
identifiers, validates each row against the class definition and builds a ``RowPlan``.
A dry run stops here. A real run writes the plans in batches of 200 rows per transaction
through :class:`tiqora.cmdb.service.CiService` (``recompute=False``) and recomputes the
incident states of the touched CIs once at the end. The report (``report.csv``) lists every
data row; texts are German and name the offending cell value.

Decisions worth knowing (D9, F11, F13, F27):

* ``identifiers`` are alternatives: the first one with a non-empty cell identifies the row.
* A number that does not exist is an error (a rerun must not create the CI a second time); an
  empty identifier creates a new CI when ``create_missing`` is set.
* Updates use ``attributes_patch`` (unknown stored keys are carried forward untouched).
* New CIs get the deployment state ``production`` and the incident state ``operational`` when
  the file has no state column or the cell is empty.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.cmdb.access import CmdbAccess, load_access, require_class_access
from tiqora.cmdb.catalog import CatalogService
from tiqora.cmdb.classes import ClassService
from tiqora.cmdb.definition import AttributeDef, iter_attribute_paths
from tiqora.cmdb.errors import CmdbError, CmdbForbidden, CmdbNotFound
from tiqora.cmdb.propagation import recompute_from
from tiqora.cmdb.schemas import MappingRowIn
from tiqora.cmdb.seed import DEPLOYMENT_STATE, INCIDENT_STATE
from tiqora.cmdb.service import CiService, InvalidValues, system_access
from tiqora.cmdb.settings import CmdbSettings, load_cmdb_settings
from tiqora.cmdb.transfer.mapping import (
    ColumnTarget,
    ImportOptions,
    TargetError,
    format_target,
    parse_target,
)
from tiqora.cmdb.transfer.readers import Cell, ReadError, read_rows
from tiqora.cmdb.values import (
    ValueIssue,
    canonical_json,
    check_references,
    collect_reference_paths,
    normalize_values,
)
from tiqora.db.legacy.queue import Queue, Service, Sla
from tiqora.db.legacy.ticket import TicketPriority, TicketState, TicketType
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import (
    TiqoraCmdbCi,
    TiqoraCmdbCiAttr,
    TiqoraCmdbCiVersion,
)
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JobHandler
from tiqora.jobs.service import get_job_file, list_job_files

INPUT_PREFIX: Final = "input-"
REPORT_NAME: Final = "report.csv"
WRITE_BATCH: Final = 200
MAX_ROWS: Final = 200_000
_CHUNK: Final = 500
_NAME_MAX: Final = 250
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
DEFAULT_DEPLOYMENT_KEY: Final = "production"
DEFAULT_INCIDENT_KEY: Final = "operational"
NOT_WRITTEN: Final = "nicht geschrieben (Abbruch)"

Status = Literal["created", "updated", "unchanged", "error", "skipped"]


class CmdbImportPayload(BaseModel):
    user_id: int
    class_id: int
    file_format: Literal["csv", "xlsx"]
    mapping: list[MappingRowIn] = Field(max_length=500)
    options: ImportOptions
    dry_run: bool
    filename: str = Field(max_length=255)
    time_zone: str = Field("UTC", max_length=64)


# --- mapping ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MappingProblem:
    #: 1-based position in the submitted mapping list
    row: int
    message: str


@dataclass(frozen=True, slots=True)
class ResolvedColumn:
    index: int  #: 0-based position in the file
    header: str
    target: ColumnTarget
    row: int  #: 1-based position in the mapping list


def default_mapping(header: Sequence[str]) -> list[MappingRowIn]:
    """Columns whose header is itself a valid target (the export's headers) map to it."""
    out: list[MappingRowIn] = []
    for h in header:
        try:
            parse_target(h)
        except TargetError:
            out.append(MappingRowIn(column=h, target="ignore"))
        else:
            out.append(MappingRowIn(column=h, target=h))
    return out


def resolve_columns(
    header: Sequence[str], mapping: Sequence[MappingRowIn]
) -> tuple[list[ResolvedColumn], list[MappingProblem]]:
    columns: list[ResolvedColumn] = []
    problems: list[MappingProblem] = []
    for n, row in enumerate(mapping, start=1):
        try:
            target = parse_target(row.target)
        except TargetError as exc:
            problems.append(MappingProblem(n, str(exc)))
            continue
        if isinstance(row.column, int):
            index = row.column - 1
            if not 0 <= index < len(header):
                problems.append(MappingProblem(n, f"Die Spalte {row.column} gibt es nicht."))
                continue
        else:
            wanted = row.column.strip()
            found = [i for i, h in enumerate(header) if h == wanted]
            if not found:
                found = [i for i, h in enumerate(header) if h.casefold() == wanted.casefold()]
            if not found:
                problems.append(MappingProblem(n, f"Die Spalte „{wanted}“ gibt es nicht."))
                continue
            index = found[0]
        if target.kind != "ignore":
            columns.append(ResolvedColumn(index, header[index], target, n))
    return columns, problems


def check_mapping(
    columns: Sequence[ResolvedColumn], attrs: list[AttributeDef], options: ImportOptions
) -> list[MappingProblem]:
    """Rules that need the class definition: known attributes, positions, identifiers."""
    problems: list[MappingProblem] = []
    by_path = dict(iter_attribute_paths(attrs))
    seen: dict[str, int] = {}
    for col in columns:
        t = col.target
        text = format_target(t)
        if text in seen:
            problems.append(
                MappingProblem(col.row, f"„{text}“ ist schon der Spalte {seen[text]} zugeordnet.")
            )
        seen[text] = col.row
        if t.kind != "attr":
            continue
        a = by_path.get(t.dotted)
        if a is None:
            problems.append(MappingProblem(col.row, f"Das Attribut „{t.dotted}“ gibt es nicht."))
            continue
        if a.type in ("group", "attachment"):
            problems.append(
                MappingProblem(col.row, f"Das Attribut „{t.dotted}“ lässt sich nicht importieren.")
            )
        walk = attrs
        for key, pos in t.path:
            node = next((x for x in walk if x.key == key), None)
            if node is not None and pos > node.count_max:
                problems.append(
                    MappingProblem(
                        col.row, f"„{node.name}“ hat höchstens {node.count_max} Einträge."
                    )
                )
            walk = [] if node is None else node.sub
    mapped = {format_target(c.target) for c in columns}
    for ident in options.identifiers:
        try:
            it = parse_target(ident)
        except TargetError as exc:
            problems.append(MappingProblem(0, f"Kennung „{ident}“: {exc}"))
            continue
        if it.kind not in ("number", "name", "attr"):
            problems.append(MappingProblem(0, f"„{ident}“ taugt nicht als Kennung."))
            continue
        if format_target(it) not in mapped:
            problems.append(
                MappingProblem(0, f"Die Kennung „{ident}“ ist keiner Spalte zugeordnet.")
            )
            continue
        if it.kind == "attr":
            a = by_path.get(it.dotted)
            if a is not None and not a.searchable:
                problems.append(
                    MappingProblem(0, f"Das Attribut „{it.dotted}“ ist nicht durchsuchbar.")
                )
    if options.create_missing and "name" not in mapped:
        problems.append(
            MappingProblem(0, "Für neue CIs muss eine Spalte dem Namen zugeordnet sein.")
        )
    return problems


# --- cell conversion ---------------------------------------------------------------------------


class CellProblem(Exception):
    """A cell that cannot be turned into a value; the message is shown in the report."""


_NAME_COLUMNS: Final[dict[str, Any]] = {
    "service": Service.name,
    "sla": Sla.name,
    "queue": Queue.name,
    "priority": TicketPriority.name,
    "ticket_type": TicketType.name,
    "state": TicketState.name,
    "agent": Users.login,
}
_NAME_LABELS: Final[dict[str, str]] = {
    "service": "Service",
    "sla": "SLA",
    "queue": "Queue",
    "priority": "Priorität",
    "ticket_type": "Ticket-Typ",
    "state": "Ticket-Status",
    "agent": "Mitarbeiter",
}
_FORMAT_PHRASES: Final[dict[str, str]] = {
    "ip": "Ungültige IP-Adresse",
    "mac": "Ungültige MAC-Adresse",
    "email": "Ungültige E-Mail-Adresse",
    "url": "Ungültige Adresse (URL)",
    "date": "Ungültiges Datum",
    "datetime": "Ungültiger Zeitpunkt",
    "integer": "Ungültige Zahl",
    "decimal": "Ungültige Zahl",
    "money": "Ungültiger Betrag",
}
_ISO_DATETIME: Final = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$")
_DE_DATETIME: Final = re.compile(
    r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$"
)
_ISO_DATE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def cell_text(cell: Cell) -> str:
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell.strip()
    if isinstance(cell, datetime):
        return cell.isoformat(sep=" ", timespec="seconds")
    if isinstance(cell, float):
        return str(int(cell)) if cell.is_integer() else repr(cell)
    return str(cell)


def _local_datetime(text: str) -> datetime | None:
    """A date or date-time spelled ``YYYY-MM-DD[ HH:MM[:SS]]`` or ``DD.MM.YYYY[ HH:MM[:SS]]``."""
    try:
        if _ISO_DATE.match(text):
            return datetime.strptime(text, "%Y-%m-%d")
        m = _ISO_DATETIME.match(text)
        if m:
            y, mo, d, h, mi, s = m.groups()
            return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
        m = _DE_DATETIME.match(text)
        if m:
            d, mo, y, h, mi, s = m.groups()
            return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    return None


@dataclass
class _Lookups:
    tz: tzinfo
    names: dict[str, dict[str, int]] = field(default_factory=dict)  # type -> folded name -> id
    catalog: dict[str, dict[str, int]] = field(default_factory=dict)  # catalog class -> ...
    ci_numbers: dict[str, int] = field(default_factory=dict)

    def to_utc(self, naive: datetime) -> str:
        local = naive.replace(tzinfo=self.tz)
        return local.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


def convert_cell(a: AttributeDef, cell: Cell, env: _Lookups) -> Any:
    """The raw value ``normalize_values`` expects for a non-empty ``cell``.

    Date-times are converted to canonical UTC here: ``CiService`` normalises again in UTC.
    """
    t = a.type
    if t == "date":
        if isinstance(cell, datetime):
            return cell.date().isoformat()
        text = cell_text(cell)
        parsed = _local_datetime(text)
        return parsed.date().isoformat() if parsed is not None else text
    if t == "datetime":
        if isinstance(cell, datetime):
            return env.to_utc(cell)
        text = cell_text(cell)
        parsed = _local_datetime(text)
        return env.to_utc(parsed) if parsed is not None else text
    text = cell_text(cell)
    if t == "integer":
        if isinstance(cell, float) and cell.is_integer():
            return int(cell)
        return cell if isinstance(cell, int) else text
    if t in ("decimal", "money"):
        if isinstance(cell, float):
            try:
                return format(Decimal(repr(cell)), "f")
            except InvalidOperation:
                return text
        if "," in text and "." not in text:
            return text.replace(",", ".")
        return text
    if t == "catalog":
        cls = a.options.catalog_class or ""
        found = env.catalog.get(cls, {}).get(text.casefold())
        if found is None:
            raise CellProblem(f"Unbekannter Eintrag „{text}“ in der Liste „{cls}“")
        return found
    if t == "ci_ref":
        found = env.ci_numbers.get(text)
        if found is None:
            raise CellProblem(f"Unbekanntes CI „{text}“")
        return found
    if t in _NAME_COLUMNS:
        found = env.names.get(t, {}).get(text.casefold())
        if found is None:
            raise CellProblem(f"{_NAME_LABELS[t]} „{text}“ gibt es nicht")
        return found
    if t in ("group", "attachment"):
        raise CellProblem("Dieser Attributtyp lässt sich nicht importieren")
    return text


# --- row planning -----------------------------------------------------------------------------


@dataclass(slots=True)
class _CiInfo:
    id: int
    number: str
    class_id: int
    name: str
    deployment_state_id: int
    incident_state_id: int
    values: dict[str, Any]


@dataclass(slots=True)
class RowPlan:
    row_no: int
    status: Status = "skipped"
    ci_id: int | None = None
    number: str = ""
    messages: list[str] = field(default_factory=list)
    create: bool = False
    name: str | None = None
    deployment_state_id: int | None = None
    incident_state_id: int | None = None
    #: create: the full value document; update: the normalised entries of the touched keys
    values: dict[str, Any] = field(default_factory=dict)
    state_touched: bool = False


def _guard(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _chunks(items: Sequence[Any], size: int = _CHUNK) -> list[Sequence[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _entry_path(target: ColumnTarget) -> str:
    return ".".join(f"{k}[{i}]" for k, i in target.path)


def _apply_leaf(values: dict[str, Any], path: tuple[tuple[str, int], ...], value: Any) -> None:
    """Set ``values[key][pos-1]['v']`` (and below ``['s']``) to ``value``; pads with empties."""
    key, pos = path[0]
    entries = values.setdefault(key, [])
    while len(entries) < pos:
        entries.append({"v": None})
    entry = entries[pos - 1]
    if len(path) == 1:
        entry["v"] = value
        return
    sub = entry.setdefault("s", {})
    _apply_leaf(sub, path[1:], value)


class _Importer:
    def __init__(
        self,
        session: AsyncSession,
        payload: CmdbImportPayload,
        attrs: list[AttributeDef],
        columns: list[ResolvedColumn],
    ) -> None:
        self.s = session
        self.p = payload
        self.o = payload.options
        self.attrs = attrs
        self.by_path = dict(iter_attribute_paths(attrs))
        self.columns = columns
        try:
            tz: tzinfo = UTC if payload.time_zone.upper() == "UTC" else ZoneInfo(payload.time_zone)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            tz = UTC
        self.env = _Lookups(tz=tz)
        self.dep_ids: dict[str, int] = {}
        self.inc_ids: dict[str, int] = {}
        self.default_dep = 0
        self.default_inc = 0

    # lookups ---------------------------------------------------------------------------------

    async def load_catalogs(self) -> None:
        cat = CatalogService(self.s)
        for cls, store in ((DEPLOYMENT_STATE, self.dep_ids), (INCIDENT_STATE, self.inc_ids)):
            for item in await cat.items(cls):
                store.setdefault(item.name.casefold(), item.id)
                store.setdefault(item.item_key.casefold(), item.id)
        self.default_dep = await _default_state(cat, DEPLOYMENT_STATE, DEFAULT_DEPLOYMENT_KEY)
        self.default_inc = await _default_state(cat, INCIDENT_STATE, DEFAULT_INCIDENT_KEY)
        wanted = {
            a.options.catalog_class
            for a in self.by_path.values()
            if a.type == "catalog" and a.options.catalog_class
        }
        for name in sorted(c for c in wanted if c):
            store = self.env.catalog.setdefault(name, {})
            for item in await cat.items(name):
                store.setdefault(item.name.casefold(), item.id)
                store.setdefault(item.item_key.casefold(), item.id)

    async def load_names(self, rows: Sequence[tuple[int, list[Cell]]]) -> None:
        """One IN-list query (chunked) per reference type for every distinct cell text."""
        need: dict[str, set[str]] = {}
        numbers: set[str] = set()
        for col in self.columns:
            if col.target.kind != "attr":
                continue
            a = self.by_path.get(col.target.dotted)
            if a is None or (a.type not in _NAME_COLUMNS and a.type != "ci_ref"):
                continue
            for _, cells in rows:
                text = cell_text(cells[col.index]) if col.index < len(cells) else ""
                if not text:
                    continue
                if a.type == "ci_ref":
                    numbers.add(text)
                else:
                    need.setdefault(a.type, set()).add(text)
        for t, texts in need.items():
            column = _NAME_COLUMNS[t]
            store = self.env.names.setdefault(t, {})
            folded = sorted({x.casefold() for x in texts})
            for part in _chunks(folded):
                res = await self.s.execute(
                    select(column, _id_column(t)).where(func.lower(column).in_(list(part)))
                )
                for name, ident in res.all():
                    store.setdefault(str(name).casefold(), int(ident))
        for part in _chunks(sorted(numbers)):
            res = await self.s.execute(
                select(TiqoraCmdbCi.number, TiqoraCmdbCi.id).where(
                    TiqoraCmdbCi.number.in_(list(part))
                )
            )
            for number, ident in res.all():
                self.env.ci_numbers[str(number)] = int(ident)

    # identifier matching --------------------------------------------------------------------

    def _identifier_value(self, ident: ColumnTarget, cells: list[Cell]) -> str | None:
        """Text of the identifier cell (normalised for attributes), ``None`` when empty."""
        want = format_target(ident)
        col = next((c for c in self.columns if format_target(c.target) == want), None)
        if col is None or col.index >= len(cells):
            return None
        raw = cells[col.index]
        if cell_text(raw) == "":
            return None
        if ident.kind != "attr":
            return cell_text(raw)
        a = self.by_path[ident.dotted].model_copy(
            update={"sub": [], "required": False, "count_min": 0, "count_max": 1}
        )
        try:
            value = convert_cell(a, raw, self.env)
        except CellProblem as exc:
            raise CellProblem(f"Ungültige Kennung „{cell_text(raw)}“: {exc}") from exc
        normal, issues = normalize_values([a], {a.key: [{"v": value}]})
        if issues or not normal.get(a.key):
            raise CellProblem(f"Ungültige Kennung „{cell_text(raw)}“")
        return str(normal[a.key][0]["v"])

    async def match(
        self, parsed: list[tuple[int, list[Cell], tuple[str, ColumnTarget, str] | None]]
    ) -> dict[tuple[str, str], list[_CiInfo]]:
        """Existing CIs per identifier ``(kind, key)``; kind is ``number``, ``name`` or
        ``attr:<path>``. Names and attribute values are matched case-insensitively."""
        by_number: set[str] = set()
        by_name: set[str] = set()
        by_attr: dict[str, set[str]] = {}
        for _, _, ident in parsed:
            if ident is None:
                continue
            kind, target, value = ident
            if kind == "number":
                by_number.add(value)
            elif kind == "name":
                by_name.add(value)
            else:
                by_attr.setdefault(target.dotted, set()).add(ident_key(kind, value))
        ci_ids: set[int] = set()
        for part in _chunks(sorted(by_number)):
            res = await self.s.execute(
                select(TiqoraCmdbCi.id).where(TiqoraCmdbCi.number.in_(list(part)))
            )
            ci_ids.update(int(i) for i in res.scalars().all())
        for part in _chunks(sorted(by_name)):
            res = await self.s.execute(
                select(TiqoraCmdbCi.id).where(
                    TiqoraCmdbCi.class_id == self.p.class_id, TiqoraCmdbCi.name.in_(list(part))
                )
            )
            ci_ids.update(int(i) for i in res.scalars().all())
        attr_hits: list[tuple[str, str, int]] = []
        for dotted, keys in by_attr.items():
            for part in _chunks(sorted(keys)):
                res = await self.s.execute(
                    select(TiqoraCmdbCiAttr.ci_id, TiqoraCmdbCiAttr.value_key)
                    .where(
                        TiqoraCmdbCiAttr.attr_path == dotted,
                        TiqoraCmdbCiAttr.value_key.in_(list(part)),
                    )
                    .distinct()
                )
                for ident_id, key in res.all():
                    ci_ids.add(int(ident_id))
                    attr_hits.append((dotted, str(key), int(ident_id)))
        infos = await self._load_infos(sorted(ci_ids))
        found: dict[tuple[str, str], list[_CiInfo]] = {}
        for info in infos.values():
            found.setdefault(("number", info.number), []).append(info)
            if info.class_id == self.p.class_id:
                found.setdefault(("name", info.name.casefold()), []).append(info)
        for dotted, key, ident_id in attr_hits:
            hit = infos.get(ident_id)
            if hit is not None and hit.class_id == self.p.class_id:
                bucket = found.setdefault((f"attr:{dotted}", key), [])
                if hit not in bucket:
                    bucket.append(hit)
        return found

    async def _load_infos(self, ci_ids: list[int]) -> dict[int, _CiInfo]:
        out: dict[int, _CiInfo] = {}
        for part in _chunks(ci_ids):
            rows = (
                await self.s.execute(
                    select(
                        TiqoraCmdbCi.id,
                        TiqoraCmdbCi.number,
                        TiqoraCmdbCi.class_id,
                        TiqoraCmdbCi.name,
                        TiqoraCmdbCi.deployment_state_id,
                        TiqoraCmdbCi.incident_state_id,
                        TiqoraCmdbCi.current_version_id,
                    ).where(TiqoraCmdbCi.id.in_(list(part)))
                )
            ).all()
            version_ids = [int(r[6]) for r in rows if r[6] is not None]
            documents: dict[int, str] = {}
            for vpart in _chunks(version_ids):
                for vid, doc in (
                    await self.s.execute(
                        select(TiqoraCmdbCiVersion.id, TiqoraCmdbCiVersion.attributes).where(
                            TiqoraCmdbCiVersion.id.in_(list(vpart))
                        )
                    )
                ).all():
                    documents[int(vid)] = str(doc)
            for ident_id, number, class_id, name, dep, inc, vid in rows:
                out[int(ident_id)] = _CiInfo(
                    id=int(ident_id),
                    number=str(number),
                    class_id=int(class_id),
                    name=str(name),
                    deployment_state_id=int(dep),
                    incident_state_id=int(inc),
                    values=json.loads(documents[int(vid)]) if vid is not None else {},
                )
        return out


def ident_key(kind: str, value: str) -> str:
    """Comparison key of an identifier value (numbers exact, the rest casefolded)."""
    if kind == "number":
        return value
    folded = value.casefold()
    return folded if kind == "name" else folded[:191]


async def _default_state(cat: CatalogService, catalog_class: str, item_key: str) -> int:
    items = await cat.items(catalog_class)
    for item in items:
        if item.item_key == item_key:
            return item.id
    if items:
        return items[0].id
    raise JobFailed("Die Auswahlliste für Verwendungs- oder Vorfallsstatus ist leer.")


def _id_column(t: str) -> Any:
    return {
        "service": Service.id,
        "sla": Sla.id,
        "queue": Queue.id,
        "priority": TicketPriority.id,
        "ticket_type": TicketType.id,
        "state": TicketState.id,
        "agent": Users.id,
    }[t]


# --- the run ------------------------------------------------------------------------------------


def _report(plans: Sequence[RowPlan]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(["row", "status", "ci_number", "message"])
    for p in plans:
        writer.writerow([p.row_no, p.status, _guard(p.number), _guard("; ".join(p.messages))])
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def _issue_text(issue: ValueIssue, cells: Mapping[str, tuple[AttributeDef, str]]) -> str:
    found = cells.get(issue.path)
    if found is None:
        return f"{issue.path}: {issue.message}"
    a, text = found
    phrase = _FORMAT_PHRASES.get(a.type)
    if phrase is not None and issue.code in ("bad_format", "bad_type"):
        return f"{phrase} „{text}“"
    return f"{a.name}: {issue.message} (Wert „{text}“)"


def _count(plans: Sequence[RowPlan]) -> dict[str, int]:
    out = {"created": 0, "updated": 0, "unchanged": 0, "errors": 0, "skipped": 0}
    for p in plans:
        out["errors" if p.status == "error" else p.status] += 1
    return out


async def run_cmdb_import(ctx: JobContext, payload: CmdbImportPayload) -> dict[str, Any]:
    opts = payload.options
    async with ctx.session_factory() as session:
        access = await load_access(session, payload.user_id)
        try:
            cls = await require_class_access(session, access, payload.class_id, "rw")
        except (CmdbNotFound, CmdbForbidden) as exc:
            raise JobFailed("Für diese Klasse fehlt die Berechtigung zum Importieren.") from exc
        if not cls.valid:
            raise JobFailed("Die Klasse ist nicht mehr gültig.")
        definition = await ClassService(session).current_definition(payload.class_id)
        settings = await load_cmdb_settings(session)
        attrs = definition.attributes

        files = (await list_job_files(session, [ctx.job_id])).get(ctx.job_id, [])
        meta = next((f for f in files if f.name.startswith(INPUT_PREFIX)), None)
        loaded = None if meta is None else await get_job_file(session, ctx.job_id, int(meta.id))
        if loaded is None:
            raise JobFailed("Die hochgeladene Datei fehlt.")
        data = bytes(loaded.data)

        try:
            header, stream = read_rows(data, file_format=payload.file_format, options=opts)
            rows: list[tuple[int, list[Cell]]] = []
            for n, cells in enumerate(stream, start=2 if opts.header_row else 1):
                if len(rows) >= MAX_ROWS:
                    raise JobFailed(f"Die Datei hat mehr als {MAX_ROWS} Zeilen.")
                rows.append((n, cells))
        except ReadError as exc:
            raise JobFailed(str(exc)) from exc

        columns, problems = resolve_columns(header, payload.mapping)
        problems += check_mapping(columns, attrs, opts)
        if problems:
            raise JobFailed(
                "Die Zuordnung ist ungültig: " + "; ".join(p.message for p in problems[:5])
            )

        imp = _Importer(session, payload, attrs, columns)
        await imp.load_catalogs()
        await ctx.progress(0, len(rows), "Datei wird geprüft …")
        plans = await plan_rows(imp, rows, ctx)
        counts = _count(plans)

        if payload.dry_run:
            await ctx.add_file(REPORT_NAME, "text/csv; charset=utf-8", _report(plans))
            return {**counts, "dry_run": True}

        if counts["errors"] and opts.on_error == "abort":
            for p in plans:
                if p.status != "error":
                    p.status = "skipped"
                    p.messages = [NOT_WRITTEN]
            await ctx.add_file(REPORT_NAME, "text/csv; charset=utf-8", _report(plans))
            raise JobFailed(
                f"Die Datei enthält {counts['errors']} fehlerhafte Zeile(n). "
                "Es wurde nichts geschrieben. Einzelheiten stehen im Bericht."
            )

    aborted = await write_plans(ctx, payload, plans, settings)
    await ctx.add_file(REPORT_NAME, "text/csv; charset=utf-8", _report(plans))
    if aborted:
        raise JobFailed(aborted)
    await _recompute(ctx, plans, settings)
    return {**_count(plans), "dry_run": False}


@dataclass(slots=True)
class _Check:
    plan: RowPlan
    attrs: list[AttributeDef]
    values: dict[str, Any]
    cells: dict[str, tuple[AttributeDef, str]]
    old: dict[str, Any]


async def plan_rows(
    imp: _Importer, rows: list[tuple[int, list[Cell]]], ctx: JobContext
) -> list[RowPlan]:
    o = imp.o
    await imp.load_names(rows)
    id_targets = [parse_target(i) for i in o.identifiers]

    # identifier of every row: the first alternative with a non-empty cell
    parsed: list[tuple[int, list[Cell], tuple[str, ColumnTarget, str] | None]] = []
    ident_errors: dict[int, str] = {}
    for n, cells in rows:
        if all(cell_text(c) == "" for c in cells):
            continue  # blank line: no report row either
        chosen: tuple[str, ColumnTarget, str] | None = None
        for id_target in id_targets:
            try:
                value = imp._identifier_value(id_target, cells)
            except CellProblem as exc:
                ident_errors[n] = str(exc)
                break
            if value is not None:
                chosen = (
                    id_target.kind if id_target.kind != "attr" else f"attr:{id_target.dotted}",
                    id_target,
                    value,
                )
                break
        parsed.append((n, cells, chosen))

    found = await imp.match(parsed)
    plans: list[RowPlan] = []
    seen: dict[tuple[str, str], int] = {}
    checks: list[_Check] = []
    total = len(parsed)
    for done, (n, cells, ident) in enumerate(parsed, start=1):
        plan = RowPlan(row_no=n)
        plans.append(plan)
        if n in ident_errors:
            plan.status = "error"
            plan.messages.append(ident_errors[n])
            continue
        info: _CiInfo | None = None
        if ident is not None:
            kind, _target, value = ident
            key = (kind, ident_key(kind, value))
            if key in seen:
                plan.status = "error"
                plan.messages.append(
                    f"Die Kennung „{value}“ kommt in der Datei mehrfach vor "
                    f"(zuerst in Zeile {seen[key]})"
                )
                continue
            seen[key] = n
            matches = found.get(key, [])
            if kind == "number":
                if not matches:
                    plan.status = "error"
                    plan.messages.append(f"Die Nummer „{value}“ gibt es nicht")
                    continue
                if matches[0].class_id != imp.p.class_id:
                    plan.status = "error"
                    plan.messages.append(f"Die Nummer „{value}“ gehört zu einer anderen Klasse")
                    continue
            if len(matches) > 1:
                plan.status = "error"
                plan.messages.append(f"Mehrere CIs passen zur Kennung „{value}“")
                continue
            info = matches[0] if matches else None
            if info is None and not o.create_missing:
                plan.status = "error"
                plan.messages.append(f"Kein CI zur Kennung „{value}“ gefunden")
                continue
        elif not o.create_missing:
            plan.status = "error"
            plan.messages.append("Die Zeile hat keine Kennung")
            continue
        check = _plan_row(imp, plan, cells, info)
        if check is not None:
            checks.append(check)
        if done % 2000 == 0:
            await ctx.progress(done, total, "Datei wird geprüft …")

    await _check_references(imp, checks)
    for plan in plans:
        if plan.messages and plan.status != "error":
            plan.status = "error"
    return plans


async def _check_references(imp: _Importer, checks: list[_Check]) -> None:
    """One ``check_references`` call for every distinct (path, value) of the whole file.
    References a CI already stored before are not re-checked (it may have vanished since)."""
    verdicts: dict[tuple[str, int | str], str | None] = {}
    pending: dict[str, dict[int | str, str]] = {}
    labels: dict[str, tuple[str, int | str]] = {}
    for c in checks:
        old = collect_reference_paths(c.attrs, c.old)
        for path, vals in collect_reference_paths(c.attrs, c.values).items():
            for value in vals:
                if value in old.get(path, {}) or (path, value) in verdicts:
                    continue
                verdicts[(path, value)] = None
                label = f"{path}|{value}"
                labels[label] = (path, value)
                pending.setdefault(path, {})[value] = label
    if pending:
        for issue in await check_references(
            imp.s, pending, attrs_by_path=imp.by_path, ci_id=None, user_id=imp.p.user_id
        ):
            pair = labels.get(issue.path)
            if pair is not None:
                verdicts[pair] = issue.message
    for c in checks:
        if c.plan.status == "error":
            continue
        for path, vals in collect_reference_paths(c.attrs, c.values).items():
            for value, entry in vals.items():
                message = verdicts.get((path, value))
                if message is not None:
                    a = imp.by_path[path]
                    text = c.cells.get(entry, (a, str(value)))[1]
                    c.plan.messages.append(f"{a.name}: {message} (Wert „{text}“)")


def _plan_row(
    imp: _Importer, plan: RowPlan, cells: list[Cell], info: _CiInfo | None
) -> _Check | None:
    o = imp.o
    problems: list[str] = []
    name_text = ""
    dep: int | None = None
    inc: int | None = None
    edits: list[tuple[ColumnTarget, Cell, AttributeDef]] = []
    for col in imp.columns:
        cell = cells[col.index] if col.index < len(cells) else None
        text = cell_text(cell)
        t = col.target
        if t.kind == "name":
            name_text = text
        elif t.kind == "deployment_state" and text:
            dep = imp.dep_ids.get(text.casefold())
            if dep is None:
                problems.append(f"Unbekannter Verwendungsstatus „{text}“")
        elif t.kind == "incident_state" and text:
            inc = imp.inc_ids.get(text.casefold())
            if inc is None:
                problems.append(f"Unbekannter Vorfallsstatus „{text}“")
        elif t.kind == "attr":
            edits.append((t, cell, imp.by_path[t.dotted]))

    create = info is None
    if len(name_text) > _NAME_MAX:
        problems.append(f"Der Name ist zu lang (höchstens {_NAME_MAX} Zeichen)")
    if create and not name_text:
        problems.append("Der Name fehlt")

    cell_index: dict[str, tuple[AttributeDef, str]] = {}
    old: dict[str, Any] = {} if info is None else info.values
    base: dict[str, Any] = copy.deepcopy(old)
    touched: set[str] = set()
    for t, cell, a in edits:
        text = cell_text(cell)
        value: Any = None
        if text == "":
            if create or o.empty_cells == "keep":
                continue
        else:
            try:
                value = convert_cell(a, cell, imp.env)
            except CellProblem as exc:
                problems.append(str(exc))
                continue
            cell_index[_entry_path(t)] = (a, text)
        touched.add(t.path[0][0])
        _apply_leaf(base, t.path, value)

    subset = imp.attrs if create else [a for a in imp.attrs if a.key in touched]
    raw = base if create else {k: base[k] for k in touched}
    normalised, issues = normalize_values(subset, raw)
    problems.extend(_issue_text(i, cell_index) for i in issues)
    if problems:
        plan.status = "error"
        plan.messages.extend(problems)
        return None

    plan.create = create
    if info is None:
        plan.name = name_text
        plan.deployment_state_id = dep if dep is not None else imp.default_dep
        plan.incident_state_id = inc if inc is not None else imp.default_inc
        plan.values = dict(normalised)
        plan.status = "created"
        return _Check(plan, subset, dict(normalised), cell_index, {})

    plan.ci_id = info.id
    plan.number = info.number
    plan.name = name_text if name_text and name_text != info.name else None
    plan.deployment_state_id = dep if dep is not None and dep != info.deployment_state_id else None
    plan.incident_state_id = inc if inc is not None and inc != info.incident_state_id else None
    plan.state_touched = plan.incident_state_id is not None
    changed = {
        k: normalised.get(k, [])
        for k in touched
        if canonical_json({k: normalised.get(k, [])}) != canonical_json({k: old.get(k, [])})
    }
    plan.values = changed
    nothing = (
        plan.name is None
        and plan.deployment_state_id is None
        and plan.incident_state_id is None
        and not changed
    )
    plan.status = "unchanged" if nothing else "updated"
    return _Check(plan, subset, dict(normalised), cell_index, old)


async def write_plans(
    ctx: JobContext,
    payload: CmdbImportPayload,
    plans: list[RowPlan],
    settings: CmdbSettings,
) -> str | None:
    """Write created/updated plans, 200 rows per transaction. Returns an abort message."""
    writable = [p for p in plans if p.status in ("created", "updated")]
    access = system_access(payload.user_id)
    note = f"Import {payload.filename}"[:500]
    abort = payload.options.on_error == "abort"
    for start in range(0, len(writable), WRITE_BATCH):
        batch = writable[start : start + WRITE_BATCH]
        failed: RowPlan | None = None
        async with ctx.session_factory() as session:
            svc = CiService(session, settings=settings)
            for plan in batch:
                try:
                    async with session.begin_nested():
                        await _write_one(svc, access, payload, plan, note)
                except (CmdbError, InvalidValues) as exc:
                    plan.status = "error"
                    plan.messages.append(_error_text(exc))
                    if abort:
                        failed = plan
                        break
            if failed is None:
                await session.commit()
            else:
                await session.rollback()
        if failed is not None:
            for p in writable[start:]:
                if p is not failed:
                    p.status = "skipped"
                    p.messages = [NOT_WRITTEN]
                    if p.create:
                        p.ci_id, p.number = None, ""
            return (
                f"Zeile {failed.row_no} konnte nicht geschrieben werden. "
                "Der Import wurde abgebrochen; vorherige Blöcke sind gespeichert."
            )
        await ctx.progress(start + len(batch), len(writable), "CIs werden geschrieben …")
    return None


async def _write_one(
    svc: CiService, access: CmdbAccess, payload: CmdbImportPayload, plan: RowPlan, note: str
) -> None:
    if plan.create:
        rec = await svc.create(
            access,
            class_id=payload.class_id,
            name=plan.name or "",
            deployment_state_id=plan.deployment_state_id or 0,
            incident_state_id=plan.incident_state_id or 0,
            attributes=plan.values,
            change_note=note,
            recompute=False,
            enforce_permissions=False,
        )
        plan.ci_id, plan.number, plan.state_touched = rec.id, rec.number, True
        return
    res = await svc.update(
        access,
        plan.ci_id or 0,
        expected_lock_version=None,
        name=plan.name,
        deployment_state_id=plan.deployment_state_id,
        incident_state_id=plan.incident_state_id,
        attributes_patch=plan.values or None,
        change_note=note,
        recompute=False,
        enforce_permissions=False,
    )
    if not res.changed:
        plan.status = "unchanged"


def _error_text(exc: Exception) -> str:
    if isinstance(exc, InvalidValues):
        return "; ".join(f"{i.path}: {i.message}" for i in exc.issues)
    return str(exc) or "Nicht gespeichert"


async def _recompute(ctx: JobContext, plans: Sequence[RowPlan], settings: CmdbSettings) -> None:
    ids = sorted(
        {
            p.ci_id
            for p in plans
            if p.state_touched and p.ci_id is not None and p.status in ("created", "updated")
        }
    )
    for part in _chunks(ids, 1000):
        async with ctx.session_factory() as session:
            await recompute_from(session, [int(i) for i in part], settings=settings)
            await session.commit()


CMDB_IMPORT_JOB = JobHandler(
    kind="core.cmdb_import",
    payload_model=CmdbImportPayload,
    run=run_cmdb_import,
    title_key="jobs.kinds.core.cmdb_import",
    timeout_seconds=3600,
    result_private=True,
)

__all__ = [
    "CMDB_IMPORT_JOB",
    "INPUT_PREFIX",
    "CmdbImportPayload",
    "MappingProblem",
    "ResolvedColumn",
    "RowPlan",
    "check_mapping",
    "convert_cell",
    "default_mapping",
    "resolve_columns",
    "run_cmdb_import",
]
