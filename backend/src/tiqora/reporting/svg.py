"""Server-side SVG charts for PDF reports (pure, no database, no external references).

The geometry mirrors the frontend charts in simplified, static form: axis at
zero, gridlines, rotated category labels when crowded, a donut hole, a seven
step heatmap ramp. Every text is escaped with ``html.escape``; every number the
reader sees goes through ``ChartData.value_format``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import Final

from tiqora.reporting.definition import Visualization, VisualizationType
from tiqora.reporting.export import OTHER_KEY
from tiqora.reporting.schemas import Cell, ReportResultOut
from tiqora.reporting.texts import (
    ExportLabels,
    ReportTexts,
    _cell_label,
    format_measure,
)

#: tokens.css light ``--color-series-1..6``
SERIES_COLORS: Final = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7")
#: tokens.css light ``--color-muted``
MUTED_COLOR: Final = "#66708a"
MAX_CATEGORIES: Final = 60

_LABEL_FONT: Final = "DejaVu Sans, sans-serif"
_NUMBER_FONT: Final = "DejaVu Sans Mono, monospace"
_GRID_COLOR: Final = "#d9deea"
_TEXT_COLOR: Final = "#0d1b2e"
_FONT_SIZE: Final = 9
_CHAR_WIDTH: Final = 0.62 * _FONT_SIZE
_HEATMAP_STEPS: Final = 7
_DONUT_HOLE: Final = 0.58
_LEGEND_ROW: Final = 18
_ChartKinds = frozenset({"bar", "stacked_bar", "line", "area", "donut", "heatmap", "kpi"})


@dataclass(frozen=True, slots=True)
class ChartSeries:
    key: str
    label: str
    values: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class ChartData:
    #: labels, already formatted
    categories: tuple[str, ...]
    #: the ``__other__`` series is drawn in ``MUTED_COLOR`` and never takes a slot
    series: tuple[ChartSeries, ...]
    value_format: Callable[[float], str]


def _num(value: Cell) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _label(
    result: ReportResultOut, labels: ExportLabels, index: int, value: Cell, texts: ReportTexts
) -> str:
    if value is None:
        return "—"
    column = result.columns[index]
    known = labels.value_labels.get(column.key, {}).get(str(value))
    return known if known is not None else _cell_label(result, index, value, texts)


def chart_data(
    result: ReportResultOut,
    viz: Visualization,
    labels: ExportLabels,
    texts: ReportTexts,
) -> ChartData | None:
    """Pivot the rows like the frontend ``toSeries``; ``None`` when there is nothing to plot."""
    if viz.type not in _ChartKinds:
        return None
    dims = [i for i, c in enumerate(result.columns) if c.role == "dimension"]
    measures = [i for i, c in enumerate(result.columns) if c.role == "measure"]
    if not measures or not dims or not result.rows:
        return None
    plotted = [measures[m] for m in (viz.chart_measures or []) if 0 <= m < len(measures)]
    plotted = plotted or measures
    series_idx = (
        dims[viz.series_dimension]
        if viz.series_dimension is not None and 0 <= viz.series_dimension < len(dims)
        else None
    )
    category_idx = next((i for i in dims if i != series_idx), None)
    if category_idx is None:
        return None

    def cat_key(row: list[Cell]) -> str:
        return str(row[category_idx])

    category_keys: list[str] = []
    position: dict[str, int] = {}
    category_cells: dict[str, Cell] = {}
    for row in result.rows:
        key = cat_key(row)
        if key not in position:
            position[key] = len(category_keys)
            category_keys.append(key)
            category_cells[key] = row[category_idx]
    categories = tuple(
        _label(result, labels, category_idx, category_cells[k], texts) for k in category_keys
    )

    series: list[ChartSeries] = []
    if series_idx is not None:
        measure = plotted[0]
        buckets: dict[str, list[float | None]] = {}
        cells: dict[str, Cell] = {}
        for row in result.rows:
            sk = str(row[series_idx])
            arr = buckets.get(sk)
            if arr is None:
                arr = [None] * len(category_keys)
                buckets[sk] = arr
                cells[sk] = row[series_idx]
            value = _num(row[measure])
            at = position[cat_key(row)]
            if value is not None:
                arr[at] = (arr[at] or 0.0) + value
        series = [
            ChartSeries(sk, _label(result, labels, series_idx, cells[sk], texts), tuple(arr))
            for sk, arr in buckets.items()
        ]
        value_type = result.columns[measure].type
    else:
        for mi in plotted:
            arr2: list[float | None] = [None] * len(category_keys)
            for row in result.rows:
                value = _num(row[mi])
                if value is not None:
                    at = position[cat_key(row)]
                    arr2[at] = (arr2[at] or 0.0) + value
            column = result.columns[mi]
            series.append(
                ChartSeries(
                    column.key,
                    labels.column_labels.get(column.key)
                    or texts.t(column.label_key, default=column.key),
                    tuple(arr2),
                )
            )
        value_type = result.columns[plotted[0]].type
    if not series or not categories or all(v is None for s in series for v in s.values):
        return None
    locale = texts.locale
    return ChartData(
        categories=categories,
        series=tuple(series),
        value_format=lambda v: format_measure(v, value_type, locale),
    )


# ---------------------------------------------------------------- helpers


def _n(value: float) -> str:
    """Coordinate with one decimal and no negative zero."""
    text = f"{value + 0.0:.1f}"
    return "0.0" if text == "-0.0" else text


def _esc(text: str) -> str:
    return escape(text, quote=True)


def _clip(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max(1, max_chars - 1)] + "…"


def _sort_key(key: str) -> tuple[int, float, str]:
    try:
        return (0, float(key), key)
    except ValueError:
        return (1, 0.0, key)


def _colors(keys: list[str]) -> dict[str, str]:
    """Slot per key by ascending key (ids numerically); ``__other__`` and the 7th+ are muted."""
    ranked = sorted((k for k in dict.fromkeys(keys) if k != OTHER_KEY), key=_sort_key)
    out = {
        k: (SERIES_COLORS[i] if i < len(SERIES_COLORS) else MUTED_COLOR)
        for i, k in enumerate(ranked)
    }
    out[OTHER_KEY] = MUTED_COLOR
    return out


def _text(
    x: float,
    y: float,
    content: str,
    *,
    anchor: str = "start",
    size: int = _FONT_SIZE,
    fill: str = _TEXT_COLOR,
    number: bool = False,
    weight: str | None = None,
    rotate: float | None = None,
) -> str:
    font = _NUMBER_FONT if number else _LABEL_FONT
    transform = f' transform="rotate({_n(rotate)} {_n(x)} {_n(y)})"' if rotate else ""
    bold = f' font-weight="{weight}"' if weight else ""
    return (
        f'<text x="{_n(x)}" y="{_n(y)}" text-anchor="{anchor}" font-family="{font}" '
        f'font-size="{size}" fill="{fill}"{bold}{transform}>{_esc(content)}</text>'
    )


def _svg(width: int, height: int, body: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img">' + "".join(body) + "</svg>"
    )


def _trim(data: ChartData) -> tuple[ChartData, int]:
    extra = len(data.categories) - MAX_CATEGORIES
    if extra <= 0:
        return data, 0
    trimmed = ChartData(
        categories=data.categories[:MAX_CATEGORIES],
        series=tuple(ChartSeries(s.key, s.label, s.values[:MAX_CATEGORIES]) for s in data.series),
        value_format=data.value_format,
    )
    return trimmed, extra


def _legend(entries: list[tuple[str, str]], width: int, y: float) -> list[str]:
    out: list[str] = []
    x = 8.0
    for color, label in entries:
        shown = _clip(label, 28)
        advance = 14 + len(shown) * _CHAR_WIDTH + 14
        if x + advance > width and x > 8.0:
            out.append(_text(x, y + 9, "…", fill=MUTED_COLOR))
            break
        out.append(
            f'<rect x="{_n(x)}" y="{_n(y)}" width="9.0" height="9.0" rx="2.0" fill="{color}"/>'
        )
        out.append(_text(x + 13, y + 8, shown))
        x += advance
    return out


def _nice_step(span: float, intervals: int = 4) -> float:
    raw = span / intervals
    magnitude = 10.0 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * magnitude:
            return factor * magnitude
    return 10 * magnitude


def _axis(lo: float, hi: float) -> tuple[float, float, list[float]]:
    lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi - lo <= 0:
        hi = lo + 1.0
    step = _nice_step(hi - lo)
    start = math.floor(lo / step + 1e-9) * step
    end = math.ceil(hi / step - 1e-9) * step
    ticks: list[float] = []
    value = start
    while value <= end + step * 1e-6 and len(ticks) < 12:
        ticks.append(round(value, 10))
        value += step
    return start, end, ticks


@dataclass(frozen=True, slots=True)
class _Plot:
    left: float
    top: float
    right: float
    bottom: float
    lo: float
    hi: float

    def y(self, value: float) -> float:
        return self.bottom - (value - self.lo) / (self.hi - self.lo) * (self.bottom - self.top)


def _frame(
    data: ChartData,
    width: int,
    height: int,
    lo: float,
    hi: float,
    extra: int,
    entries: list[tuple[str, str]],
) -> tuple[_Plot, list[str], float, bool, int]:
    """Axis, gridlines, category labels, legend: plot, parts, slot width, rotated, height."""
    lo, hi, ticks = _axis(lo, hi)
    tick_text = [data.value_format(t) for t in ticks]
    left = 12 + max((len(t) for t in tick_text), default=1) * _CHAR_WIDTH
    n = len(data.categories)
    right = width - 12.0
    slot = (right - left) / n
    longest = max(len(c) for c in data.categories)
    rotated = slot < longest * _CHAR_WIDTH + 4 and slot < 90
    label_room = min(longest, 14) * _CHAR_WIDTH * 0.75 + 12 if rotated else 16
    legend_h = _LEGEND_ROW if entries else 0
    top = 14.0
    bottom = height - label_room - legend_h - 6
    plot = _Plot(left, top, right, bottom, lo, hi)
    parts: list[str] = []
    for tick, label in zip(ticks, tick_text, strict=True):
        y = plot.y(tick)
        zero = abs(tick) < 1e-12
        parts.append(
            f'<line x1="{_n(left)}" y1="{_n(y)}" x2="{_n(right)}" y2="{_n(y)}" '
            f'stroke="{MUTED_COLOR if zero else _GRID_COLOR}" '
            f'stroke-width="{1.0 if zero else 0.5}"/>'
        )
        parts.append(_text(left - 4, y + 3, label, anchor="end", fill=MUTED_COLOR, number=True))
    stride = max(1, math.ceil((_FONT_SIZE + 3) / slot)) if rotated else 1
    for i, category in enumerate(data.categories):
        if i % stride:
            continue
        cx = left + slot * (i + 0.5)
        if rotated:
            parts.append(
                _text(
                    cx, bottom + 10, _clip(category, 14), anchor="end", fill=MUTED_COLOR, rotate=-45
                )
            )
        else:
            parts.append(
                _text(
                    cx,
                    bottom + 12,
                    _clip(category, max(2, int(slot / _CHAR_WIDTH))),
                    anchor="middle",
                    fill=MUTED_COLOR,
                )
            )
    if extra:
        parts.append(_text(right, 9, f"+{extra}", anchor="end", fill=MUTED_COLOR, number=True))
    if entries:
        parts.extend(_legend(entries, width, height - legend_h + 3))
    return plot, parts, slot, rotated, height


def _entries(data: ChartData, colors: dict[str, str]) -> list[tuple[str, str]]:
    return [(colors[s.key], s.label) for s in data.series]


# ---------------------------------------------------------------- charts


def _bars(data: ChartData, width: int, height: int, extra: int, *, stacked: bool) -> str:
    colors = _colors([s.key for s in data.series])
    if stacked:
        pos = [0.0] * len(data.categories)
        neg = [0.0] * len(data.categories)
        for s in data.series:
            for i, v in enumerate(s.values):
                if v is not None:
                    if v >= 0:
                        pos[i] += v
                    else:
                        neg[i] += v
        lo, hi = min(neg, default=0.0), max(pos, default=0.0)
    else:
        flat = [v for s in data.series for v in s.values if v is not None]
        lo, hi = min(flat, default=0.0), max(flat, default=0.0)
    plot, parts, slot, _, _ = _frame(data, width, height, lo, hi, extra, _entries(data, colors))
    inner = slot * 0.7
    zero = plot.y(0.0)
    count = 1 if stacked else len(data.series)
    bar_w = inner / count
    base_pos = [0.0] * len(data.categories)
    base_neg = [0.0] * len(data.categories)
    for si, s in enumerate(data.series):
        for i, v in enumerate(s.values):
            if v is None:
                continue
            x0 = plot.left + slot * i + (slot - inner) / 2
            if stacked:
                if v >= 0:
                    a, b = base_pos[i], base_pos[i] + v
                    base_pos[i] = b
                else:
                    a, b = base_neg[i], base_neg[i] + v
                    base_neg[i] = b
                y_top, y_bot = plot.y(max(a, b)), plot.y(min(a, b))
                x = x0
            else:
                y_top, y_bot = (plot.y(v), zero) if v >= 0 else (zero, plot.y(v))
                x = x0 + bar_w * si
            h = max(y_bot - y_top, 0.5)
            parts.append(
                f'<rect x="{_n(x)}" y="{_n(y_top)}" width="{_n(max(bar_w - 1, 1))}" '
                f'height="{_n(h)}" '
                f'fill="{colors[s.key]}"/>'
            )
    return _svg(width, height, parts)


def _lines(data: ChartData, width: int, height: int, extra: int, *, area: bool) -> str:
    colors = _colors([s.key for s in data.series])
    flat = [v for s in data.series for v in s.values if v is not None]
    plot, parts, slot, _, _ = _frame(
        data,
        width,
        height,
        min(flat, default=0.0),
        max(flat, default=0.0),
        extra,
        _entries(data, colors),
    )
    zero = plot.y(0.0)
    for s in data.series:
        color = colors[s.key]
        segments: list[list[tuple[float, float]]] = [[]]
        for i, v in enumerate(s.values):
            if v is None:
                if segments[-1]:
                    segments.append([])
                continue
            segments[-1].append((plot.left + slot * (i + 0.5), plot.y(v)))
        for seg in (g for g in segments if g):
            points = " ".join(f"{_n(x)},{_n(y)}" for x, y in seg)
            if area and len(seg) > 1:
                outline = f"{_n(seg[0][0])},{_n(zero)} {points} {_n(seg[-1][0])},{_n(zero)}"
                parts.append(f'<polygon points="{outline}" fill="{color}" fill-opacity="0.18"/>')
            if len(seg) > 1:
                parts.append(
                    f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.6" '
                    'stroke-linejoin="round" stroke-linecap="round"/>'
                )
            if len(seg) <= 40:
                for x, y in seg:
                    parts.append(f'<circle cx="{_n(x)}" cy="{_n(y)}" r="2.0" fill="{color}"/>')
    return _svg(width, height, parts)


def _donut(data: ChartData, width: int, height: int, extra: int) -> str:
    totals = [
        sum(v for s in data.series if (v := s.values[i]) is not None and v > 0)
        for i in range(len(data.categories))
    ]
    total = sum(totals)
    parts: list[str] = []
    if extra:
        parts.append(_text(width - 12, 9, f"+{extra}", anchor="end", fill=MUTED_COLOR, number=True))
    radius = max(min(height / 2 - 12, width / 4), 20.0)
    cx, cy = radius + 20, height / 2
    inner = radius * _DONUT_HOLE
    if total <= 0:
        parts.append(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(radius)}" fill="{_GRID_COLOR}"/>')
        parts.append(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(inner)}" fill="#ffffff"/>')
        return _svg(width, height, parts)
    angle = -math.pi / 2
    shown = [i for i, t in enumerate(totals) if t > 0]
    for rank, i in enumerate(shown):
        sweep = totals[i] / total * 2 * math.pi
        color = SERIES_COLORS[rank] if rank < len(SERIES_COLORS) else MUTED_COLOR
        if len(shown) == 1:
            parts.append(
                f'<path d="M{_n(cx - radius)},{_n(cy)} '
                f"a{_n(radius)},{_n(radius)} 0 1 0 {_n(2 * radius)},0 "
                f"a{_n(radius)},{_n(radius)} 0 1 0 {_n(-2 * radius)},0 Z "
                f"M{_n(cx - inner)},{_n(cy)} a{_n(inner)},{_n(inner)} 0 1 1 {_n(2 * inner)},0 "
                f'a{_n(inner)},{_n(inner)} 0 1 1 {_n(-2 * inner)},0 Z" '
                f'fill="{color}" fill-rule="evenodd"/>'
            )
        else:
            end = angle + sweep
            large = 1 if sweep > math.pi else 0
            x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
            x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
            x3, y3 = cx + inner * math.cos(end), cy + inner * math.sin(end)
            x4, y4 = cx + inner * math.cos(angle), cy + inner * math.sin(angle)
            parts.append(
                f'<path d="M{_n(x1)},{_n(y1)} '
                f"A{_n(radius)},{_n(radius)} 0 {large} 1 {_n(x2)},{_n(y2)} "
                f'L{_n(x3)},{_n(y3)} A{_n(inner)},{_n(inner)} 0 {large} 0 {_n(x4)},{_n(y4)} Z" '
                f'fill="{color}" stroke="#ffffff" stroke-width="1.0"/>'
            )
        angle += sweep
    parts.append(
        _text(
            cx,
            cy + 4,
            data.value_format(total),
            anchor="middle",
            size=11,
            number=True,
            weight="bold",
        )
    )
    # legend column: swatch, label, share
    x0 = cx + radius + 28
    row_h = 15.0
    fit = max(1, int((height - 16) // row_h))
    y = 14.0
    for rank, i in enumerate(shown[:fit]):
        color = SERIES_COLORS[rank] if rank < len(SERIES_COLORS) else MUTED_COLOR
        share = f"{totals[i] / total * 100:.1f} %"
        parts.append(
            f'<rect x="{_n(x0)}" y="{_n(y - 8)}" width="9.0" height="9.0" rx="2.0" fill="{color}"/>'
        )
        parts.append(_text(x0 + 13, y, _clip(data.categories[i], 30)))
        parts.append(
            _text(
                width - 12,
                y,
                f"{data.value_format(totals[i])}  {share}",
                anchor="end",
                fill=MUTED_COLOR,
                number=True,
            )
        )
        y += row_h
    if len(shown) > fit:
        parts.append(_text(x0 + 13, y, f"+{len(shown) - fit}", fill=MUTED_COLOR, number=True))
    return _svg(width, height, parts)


def _heatmap(data: ChartData, width: int, height: int, extra: int) -> str:
    flat = [v for s in data.series for v in s.values if v is not None]
    lo, hi = min(flat, default=0.0), max(flat, default=0.0)
    label_w = min(max(len(s.label) for s in data.series), 22) * _CHAR_WIDTH + 12
    top = 14.0
    n_cat = len(data.categories)
    n_ser = len(data.series)
    grid_w = width - label_w - 12
    cell_w = grid_w / n_cat
    head_room = 30.0
    cell_h = max(min((height - top - head_room) / n_ser, 28.0), 10.0)
    parts: list[str] = []
    if extra:
        parts.append(_text(width - 12, 9, f"+{extra}", anchor="end", fill=MUTED_COLOR, number=True))
    stride = max(1, math.ceil((_FONT_SIZE + 3) / cell_w))
    show_values = cell_w >= 30 and cell_h >= 12
    for ri, s in enumerate(data.series):
        y = top + ri * cell_h
        parts.append(_text(label_w - 6, y + cell_h / 2 + 3, _clip(s.label, 22), anchor="end"))
        for ci, v in enumerate(s.values):
            x = label_w + ci * cell_w
            if v is None:
                parts.append(
                    f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(cell_w - 1)}" '
                    f'height="{_n(cell_h - 1)}" fill="{_GRID_COLOR}" fill-opacity="0.35"/>'
                )
                continue
            tone = 0.0 if hi <= lo else (v - lo) / (hi - lo)
            step = min(_HEATMAP_STEPS - 1, int(tone * _HEATMAP_STEPS))
            opacity = 0.12 + step * (0.88 / (_HEATMAP_STEPS - 1))
            parts.append(
                f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(cell_w - 1)}" height="{_n(cell_h - 1)}" '
                f'fill="{SERIES_COLORS[0]}" fill-opacity="{opacity:.2f}"/>'
            )
            if show_values:
                ink = _TEXT_COLOR if step <= 3 else "#ffffff"
                parts.append(
                    _text(
                        x + cell_w / 2,
                        y + cell_h / 2 + 3,
                        _clip(data.value_format(v), int(cell_w / _CHAR_WIDTH)),
                        anchor="middle",
                        fill=ink,
                        number=True,
                    )
                )
    base = top + n_ser * cell_h + 12
    for ci, category in enumerate(data.categories):
        if ci % stride:
            continue
        parts.append(
            _text(
                label_w + ci * cell_w + cell_w / 2,
                base,
                _clip(category, max(2, int(cell_w * stride / _CHAR_WIDTH))),
                anchor="middle",
                fill=MUTED_COLOR,
            )
        )
    return _svg(width, height, parts)


def _kpi(data: ChartData, width: int, height: int) -> str:
    colors = _colors([s.key for s in data.series])
    shown = data.series[:6]
    per_row = min(len(shown), 3)
    rows = math.ceil(len(shown) / per_row)
    cell_w = width / per_row
    cell_h = height / rows
    parts: list[str] = []
    for i, s in enumerate(shown):
        cx = (i % per_row + 0.5) * cell_w
        cy = (i // per_row) * cell_h
        value = next((v for v in s.values if v is not None), None)
        text = "—" if value is None else data.value_format(value)
        size = 28 if len(text) <= 10 else 20
        parts.append(
            _text(
                cx,
                cy + cell_h / 2 + 6,
                _clip(text, int(cell_w / (size * 0.62))),
                anchor="middle",
                size=size,
                number=True,
                fill=colors[s.key],
                weight="bold",
            )
        )
        parts.append(
            _text(
                cx,
                cy + cell_h / 2 + 24,
                _clip(s.label, int(cell_w / _CHAR_WIDTH)),
                anchor="middle",
                fill=MUTED_COLOR,
            )
        )
    return _svg(width, height, parts)


def render_chart(
    kind: VisualizationType, data: ChartData, *, width: int = 680, height: int = 260
) -> str | None:
    """SVG for ``kind``; ``None`` for ``table``/``pivot`` or when nothing can be drawn."""
    if kind not in _ChartKinds or not data.categories or not data.series:
        return None
    data, extra = _trim(data)
    if kind == "bar":
        return _bars(data, width, height, extra, stacked=False)
    if kind == "stacked_bar":
        return _bars(data, width, height, extra, stacked=True)
    if kind == "line":
        return _lines(data, width, height, extra, area=False)
    if kind == "area":
        return _lines(data, width, height, extra, area=True)
    if kind == "donut":
        return _donut(data, width, height, extra)
    if kind == "heatmap":
        return _heatmap(data, width, height, extra)
    return _kpi(data, width, height)
