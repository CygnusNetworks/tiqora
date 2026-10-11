"""PDF reports: HTML built with escaped text, rendered by WeasyPrint without network.

The document is assembled as one HTML string. Every dynamic string (report names,
queue and customer names, labels, info lines) goes through :func:`html.escape`,
the CSS is static, ``{{...}}`` stays literal. WeasyPrint gets ``base_url=None`` and
a ``url_fetcher`` that refuses everything, so even markup that slipped through
could neither read a file nor reach the network. Charts are inline SVG from
:mod:`tiqora.reporting.svg`.

WeasyPrint is imported lazily: the API process never needs Pango/HarfBuzz at
import time, only the worker that renders (and tests with the libs installed).
Rendering is CPU bound and synchronous; callers in async jobs run
:func:`write_report_pdf` in a thread (``asyncio.to_thread``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Final

from tiqora.reporting.definition import Visualization
from tiqora.reporting.export import OTHER_KEY
from tiqora.reporting.schemas import Cell, ReportResultOut
from tiqora.reporting.svg import chart_data, render_chart
from tiqora.reporting.texts import (
    DASH,
    ExportLabels,
    ReportTexts,
    _cell_label,
    format_measure,
    format_number,
)

#: rows over all sections of one document; further rows get the "pdf.truncated" note
MAX_TOTAL_ROWS: Final = 5000

#: private-use placeholders swapped for the CSS page counters
_PAGE: Final = "\ue000"
_PAGES: Final = "\ue001"
_CSS: Final = """
@page { size: A4; margin: 15mm 15mm 18mm 15mm; %(footer)s }
html { font-family: "DejaVu Sans", sans-serif; font-size: 9pt; color: #0d1b2e; }
body { margin: 0; }
h1 { font-size: 16pt; margin: 0 0 2mm 0; overflow-wrap: anywhere; }
h2 { font-size: 12pt; margin: 0 0 2mm 0; overflow-wrap: anywhere; break-after: avoid; }
p { margin: 0 0 2mm 0; overflow-wrap: anywhere; }
.meta { color: #66708a; font-size: 8pt; }
.info { color: #66708a; }
.section + .section { break-before: page; }
.chart { margin: 3mm 0; break-inside: avoid; }
.chart svg { width: 100%%; height: auto; }
table { width: 100%%; border-collapse: collapse; table-layout: fixed; margin-top: 2mm; }
th, td { padding: 1mm 1.5mm; border-bottom: 0.2mm solid #d9deea; overflow-wrap: anywhere;
         word-break: break-word; vertical-align: top; }
th { text-align: left; background: #eef1f7; font-weight: bold; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
td.num, th.num { text-align: right; }
tr.totals td { font-weight: bold; border-top: 0.4mm solid #66708a; }
.note { color: #66708a; font-style: italic; margin-top: 2mm; }
"""


class PdfUnavailable(RuntimeError):
    """WeasyPrint or its native libraries (Pango, HarfBuzz) cannot be loaded."""


@dataclass(frozen=True, slots=True)
class ReportSection:
    title: str
    result: ReportResultOut
    visualization: Visualization
    labels: ExportLabels
    #: "Zeitraum: …", parameter values
    info_lines: tuple[str, ...]
    row_limit: int


@dataclass(frozen=True, slots=True)
class PdfMeta:
    #: document title (schedule name or report title)
    title: str
    #: in the report time zone, for the footer
    generated_at_local: datetime
    #: "Erstellt mit den Berechtigungen von …" (scheduled runs only)
    owner_line: str | None


def _css_string(text: str) -> str:
    out = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("<", "\\3c ")
    return f'"{out}"'


def _footer_css(texts: ReportTexts, meta: PdfMeta) -> str:
    """``@bottom-right`` page counter and ``@bottom-left`` generation time."""
    template = texts.fixed("pdf.page", page=_PAGE, pages=_PAGES)
    parts: list[str] = []
    for piece in re.split(f"({_PAGE}|{_PAGES})", template):
        if piece == _PAGE:
            parts.append("counter(page)")
        elif piece == _PAGES:
            parts.append("counter(pages)")
        elif piece:
            parts.append(_css_string(piece))
    style = 'font-family: "DejaVu Sans", sans-serif; font-size: 7.5pt; color: #66708a;'
    return (
        f"@bottom-right {{ content: {' '.join(parts)}; {style} }} "
        f"@bottom-left {{ content: {_css_string(_generated_line(texts, meta))}; {style} }}"
    )


def _generated_line(texts: ReportTexts, meta: PdfMeta) -> str:
    at = meta.generated_at_local
    date = (
        f"{at.day:02d}.{at.month:02d}.{at.year}"
        if texts.locale == "de"
        else f"{at.month:02d}/{at.day:02d}/{at.year}"
    )
    return texts.fixed("pdf.generated", date=date, time=f"{at.hour:02d}:{at.minute:02d}")


def _dim_cell(section: ReportSection, texts: ReportTexts, index: int, value: Cell) -> str:
    if value is None:
        return DASH
    key = section.result.columns[index].key
    known = section.labels.value_labels.get(key, {}).get(str(value))
    if known is not None:
        return known
    return _cell_label(section.result, index, value, texts)


def _cell_text(section: ReportSection, texts: ReportTexts, index: int, value: Cell) -> str:
    column = section.result.columns[index]
    if column.role == "dimension":
        if str(value) == OTHER_KEY:
            return texts.t("reports.other")
        return _dim_cell(section, texts, index, value)
    return format_measure(value, column.type, texts.locale)


def _render_table(section: ReportSection, texts: ReportTexts, limit: int) -> str:
    result = section.result
    columns = result.columns
    first_dim = next((i for i, c in enumerate(columns) if c.role == "dimension"), None)
    totals = result.totals.grand if result.totals is not None else None
    lead = first_dim is None and totals is not None
    head: list[str] = []
    if lead:
        head.append("<th></th>")
    for c in columns:
        text = section.labels.column_labels.get(c.key) or texts.t(c.label_key, default=c.key)
        cls = ' class="num"' if c.role == "measure" else ""
        head.append(f"<th{cls}>{escape(text)}</th>")
    body: list[str] = []
    for row in result.rows[:limit]:
        cells = [] if not lead else ["<td></td>"]
        for i, (c, v) in enumerate(zip(columns, row, strict=False)):
            cls = ' class="num"' if c.role == "measure" else ""
            cells.append(f"<td{cls}>{escape(_cell_text(section, texts, i, v))}</td>")
        body.append(f"<tr>{''.join(cells)}</tr>")
    foot = ""
    if totals is not None:
        cells = [f"<td>{escape(section.labels.totals_label)}</td>"] if lead else []
        for i, (c, v) in enumerate(zip(columns, totals, strict=False)):
            if i == first_dim:
                cells.append(f"<td>{escape(section.labels.totals_label)}</td>")
            elif c.role == "measure" and v is not None:
                cells.append(f'<td class="num">{escape(_cell_text(section, texts, i, v))}</td>')
            else:
                cells.append("<td></td>")
        foot = f'<tr class="totals">{"".join(cells)}</tr>'
    return (
        f"<table><thead><tr>{''.join(head)}</tr></thead>"
        f"<tbody>{''.join(body)}{foot}</tbody></table>"
    )


def _render_section(section: ReportSection, texts: ReportTexts, budget: int) -> tuple[str, int]:
    """HTML of one section and the rows it used from ``budget``."""
    result = section.result
    parts = [f'<div class="section"><h2>{escape(section.title)}</h2>']
    parts.extend(f'<p class="info">{escape(line)}</p>' for line in section.info_lines)
    if not result.rows:
        parts.append(f'<p class="note">{escape(texts.fixed("pdf.empty"))}</p></div>')
        return "".join(parts), 0
    data = chart_data(result, section.visualization, section.labels, texts)
    svg = render_chart(section.visualization.type, data) if data is not None else None
    if svg is not None:
        parts.append(f'<div class="chart">{svg}</div>')
    limit = max(0, min(section.row_limit, budget))
    shown = min(len(result.rows), limit)
    parts.append(_render_table(section, texts, limit))
    if result.truncated or len(result.rows) > limit:
        n = format_number(shown, texts.locale, (0, 0))
        parts.append(f'<p class="note">{escape(texts.fixed("pdf.truncated", n=n))}</p>')
    parts.append("</div>")
    return "".join(parts), shown


def render_report_html(sections: Sequence[ReportSection], meta: PdfMeta, texts: ReportTexts) -> str:
    """The complete HTML document; all dynamic strings are escaped."""
    css = _CSS % {"footer": _footer_css(texts, meta)}
    body = [f"<h1>{escape(meta.title)}</h1>"]
    body.append(f'<p class="meta">{escape(_generated_line(texts, meta))}</p>')
    if meta.owner_line:
        body.append(f'<p class="meta">{escape(meta.owner_line)}</p>')
    budget = MAX_TOTAL_ROWS
    for section in sections:
        html_part, used = _render_section(section, texts, budget)
        budget -= used
        body.append(html_part)
    return (
        f'<!DOCTYPE html><html lang="{texts.locale}"><head><meta charset="utf-8">'
        f"<title>{escape(meta.title)}</title><style>{css}</style></head>"
        f"<body>{''.join(body)}</body></html>"
    )


def _refuse_fetch(url: str, *args: object, **kwargs: object) -> dict[str, Any]:
    """``url_fetcher`` of WeasyPrint: nothing is ever loaded (no http(s), file or data URL)."""
    raise ValueError(f"blocked: {url[:80]}")


def _fetcher() -> Any:
    """The refusing fetcher in the form the installed WeasyPrint expects.

    From 68 on WeasyPrint wants a ``URLFetcher`` instance (its error handling reads
    attributes of it, a bare function crashes the render); before that a callable.
    """
    try:
        from weasyprint.urls import URLFetcher
    except ImportError:
        return _refuse_fetch

    class _Refusing(URLFetcher):  # type: ignore[misc]
        def fetch(self, url: str, headers: Any = None) -> Any:
            return _refuse_fetch(url)

    return _Refusing()


def _render_pdf(html_text: str) -> bytes:
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise PdfUnavailable(f"WeasyPrint is not available: {exc}") from exc
    data: bytes = HTML(string=html_text, base_url=None, url_fetcher=_fetcher()).write_pdf()
    return data


def write_report_pdf(sections: Sequence[ReportSection], meta: PdfMeta, texts: ReportTexts) -> bytes:
    """Render the sections to PDF bytes (synchronous; call via ``asyncio.to_thread``)."""
    return _render_pdf(render_report_html(sections, meta, texts))
