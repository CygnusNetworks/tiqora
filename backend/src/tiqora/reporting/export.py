"""XLSX and CSV writers for report results (pure, no database)."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import chain, islice
from math import isfinite
from typing import Final

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from tiqora.reporting.schemas import Cell, ColumnType, ReportResultOut

OTHER_KEY: Final = "__other__"
OTHER_LABEL: Final = "Weitere"
_WIDTH_SCAN_ROWS: Final = 500
_ILLEGAL_XML: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEET_FORBIDDEN: Final = re.compile(r"[\[\]:*?/\\]")
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@", "\t", "\r")
_NUMBER_FORMATS: Final[dict[str, str]] = {
    "count": "0",
    "id": "0",
    "hour": "0",
    "weekday": "0",
    "number": "0.00",
    "hours": "0.00",
    "days": "0.00",
    "units": "0.00",
    "minutes": "0",
    "percent": "0.0%",
    "date": "yyyy-mm-dd",
}
_HEADER_FILL: Final = PatternFill("solid", fgColor="EDF0F7")


@dataclass(frozen=True, slots=True)
class XlsxColumn:
    header: str
    type: ColumnType
    width: float | None = None


@dataclass(frozen=True, slots=True)
class XlsxSheet:
    #: sanitised: []:*?/\ removed, at most 31 chars, unique per workbook
    name: str
    columns: Sequence[XlsxColumn]
    rows: Iterable[Sequence[Cell | date]]
    totals: Sequence[Cell] | None = None
    totals_label: str = "Summe"
    #: column index that receives ``totals_label`` in the totals row
    totals_label_column: int = 0
    #: written to an "Info" sheet when non-empty
    info_lines: Sequence[str] = ()


def _sheet_name(raw: str, used: set[str]) -> str:
    base = _SHEET_FORBIDDEN.sub("", raw).strip().strip("'") or "Sheet"
    name = base[:31].rstrip(" '") or "Sheet"
    if name.lower() == "history":  # reserved by Excel
        name = "History_"
    base = name
    n = 2
    while name.lower() in used:
        suffix = f" ({n})"
        name = base[: 31 - len(suffix)].rstrip(" '") + suffix
        n += 1
    used.add(name.lower())
    return name


def _text(value: str) -> str:
    return _ILLEGAL_XML.sub("", value)


def _to_date(value: object) -> datetime | None:
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            parsed = (
                datetime.fromisoformat(value)
                if len(value) > 10
                else datetime.combine(date.fromisoformat(value), datetime.min.time())
            )
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:  # openpyxl cannot store aware datetimes
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _make_cell(
    ws: object, value: object, col_type: ColumnType, *, bold: bool = False
) -> WriteOnlyCell:
    cell = WriteOnlyCell(ws)
    if value is None or (isinstance(value, float) and not isfinite(value)):
        cell.value = None  # NaN/inf have no spreadsheet representation
    elif col_type == "date" and (parsed := _to_date(value)) is not None:
        cell.value = parsed
        has_time = parsed.time() != datetime.min.time()
        cell.number_format = "yyyy-mm-dd hh:mm" if has_time else _NUMBER_FORMATS["date"]
    elif isinstance(value, bool):
        cell.value = value
    elif isinstance(value, int | float):
        cell.value = value
        cell.number_format = _NUMBER_FORMATS.get(col_type, "General")
    else:
        cell.value = _text(str(value))
        # never let a text starting with "=" become a formula
        cell.data_type = "s"
    if bold:
        cell.font = Font(bold=True)
    return cell


def _display_len(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, datetime | date):
        return 10
    return len(str(value))


def write_xlsx_workbook(sheets: Sequence[XlsxSheet]) -> bytes:
    wb = Workbook(write_only=True)
    used: set[str] = set()
    info: list[str] = []
    for sheet in sheets:
        ws = wb.create_sheet(_sheet_name(sheet.name, used))
        rows_iter = iter(sheet.rows)
        head = list(islice(rows_iter, _WIDTH_SCAN_ROWS))
        scan = list(head)
        if sheet.totals is not None:
            scan.append(sheet.totals)
        for idx, col in enumerate(sheet.columns):
            if col.width is not None:
                width = col.width
            else:
                longest = max(
                    [len(col.header), *(_display_len(r[idx]) for r in scan if idx < len(r))]
                )
                width = min(60.0, max(10.0, longest + 2.0))
            ws.column_dimensions[get_column_letter(idx + 1)].width = width
        ws.freeze_panes = "A2"
        header: list[WriteOnlyCell] = []
        for col in sheet.columns:
            cell = _make_cell(ws, col.header, "string", bold=True)
            cell.fill = _HEADER_FILL
            header.append(cell)
        ws.append(header)
        for row in chain(head, rows_iter):
            ws.append([_make_cell(ws, v, c.type) for v, c in zip(row, sheet.columns, strict=False)])
        if sheet.totals is not None:
            totals = list(sheet.totals)
            if 0 <= sheet.totals_label_column < len(totals):
                totals[sheet.totals_label_column] = sheet.totals_label
            ws.append(
                [
                    _make_cell(ws, v, c.type, bold=True)
                    for v, c in zip(totals, sheet.columns, strict=False)
                ]
            )
        info.extend(sheet.info_lines)
    if info:
        ws = wb.create_sheet(_sheet_name("Info", used))
        ws.column_dimensions["A"].width = min(60.0, max(10.0, max(len(x) for x in info) + 2.0))
        for line in info:
            ws.append([_make_cell(ws, line, "string")])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _dimension_label(
    result: ReportResultOut,
    key: str,
    value: Cell,
    value_labels: Mapping[str, Mapping[str, str]],
) -> Cell:
    if value is None:
        return None
    text = str(value)
    own = value_labels.get(key, {})
    if text in own:
        return own[text]
    if text == OTHER_KEY:
        return OTHER_LABEL
    return result.labels.get(key, {}).get(text, value)


def _labelled_rows(
    result: ReportResultOut, value_labels: Mapping[str, Mapping[str, str]]
) -> list[list[Cell]]:
    out: list[list[Cell]] = []
    for row in result.rows:
        cells: list[Cell] = []
        for col, v in zip(result.columns, row, strict=False):
            # date values stay ISO (real date cells) unless a label maps them
            keep = col.role != "dimension" or (
                col.type == "date"
                and str(v) != OTHER_KEY
                and str(v)
                not in {**result.labels.get(col.key, {}), **value_labels.get(col.key, {})}
            )
            cells.append(v if keep else _dimension_label(result, col.key, v, value_labels))
        out.append(cells)
    return out


def _first_dimension(result: ReportResultOut) -> int | None:
    return next((i for i, c in enumerate(result.columns) if c.role == "dimension"), None)


def report_sheet(
    result: ReportResultOut,
    *,
    title: str,
    column_labels: Mapping[str, str],
    value_labels: Mapping[str, Mapping[str, str]],
    totals_label: str,
    info_lines: Sequence[str],
) -> XlsxSheet:
    columns = [XlsxColumn(column_labels.get(c.key, c.key), c.type) for c in result.columns]
    rows = _labelled_rows(result, value_labels)
    totals = result.totals.grand if result.totals is not None else None
    label_col = _first_dimension(result)
    if label_col is None and totals is not None:
        # measure-only report: a leading, empty-headed column carries the totals label
        columns.insert(0, XlsxColumn("", "string"))
        rows = [[None, *r] for r in rows]
        totals = [None, *totals]
        label_col = 0
    return XlsxSheet(
        name=title,
        columns=columns,
        rows=rows,
        totals=totals,
        totals_label=totals_label,
        totals_label_column=label_col or 0,
        info_lines=info_lines,
    )


def _csv_cell(value: Cell) -> str:
    if value is None or (isinstance(value, float) and not isfinite(value)):
        return ""
    text = str(value)
    # intentionally also prefixes numeric-looking strings ("-5", "+49 ..."): text stays text
    if isinstance(value, str) and text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def write_report_csv(
    result: ReportResultOut,
    *,
    column_labels: Mapping[str, str],
    value_labels: Mapping[str, Mapping[str, str]],
    totals_label: str,
) -> bytes:
    sheet = report_sheet(
        result,
        title="",
        column_labels=column_labels,
        value_labels=value_labels,
        totals_label=totals_label,
        info_lines=(),
    )
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow([_csv_cell(c.header) for c in sheet.columns])
    for row in sheet.rows:
        writer.writerow([_csv_cell(v) for v in row])  # type: ignore[arg-type]
    if sheet.totals is not None:
        grand = list(sheet.totals)
        if 0 <= sheet.totals_label_column < len(grand):
            grand[sheet.totals_label_column] = totals_label
        writer.writerow([_csv_cell(v) for v in grand])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
