"""Server-side SVG charts (pure, no database)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.test_report_texts import col, make_result
from tiqora.reporting.definition import Visualization
from tiqora.reporting.svg import (
    MAX_CATEGORIES,
    MUTED_COLOR,
    SERIES_COLORS,
    ChartData,
    ChartSeries,
    chart_data,
    render_chart,
)
from tiqora.reporting.texts import load_texts, server_export_labels

REPO = Path(__file__).resolve().parents[2]


def test_series_colors_match_tokens() -> None:
    css = (REPO / "frontend/src/themes/tokens.css").read_text("utf-8")
    light = css.split('[data-theme="dark"]')[0]
    for i, color in enumerate(SERIES_COLORS, start=1):
        assert f"--color-series-{i}: {color};" in light
    assert f"--color-muted: {MUTED_COLOR};" in light


def _data(n_series: int = 2, n_cat: int = 3) -> ChartData:
    return ChartData(
        categories=tuple(f"K{i}" for i in range(n_cat)),
        series=tuple(
            ChartSeries(
                key=str(s),
                label=f"S{s}",
                values=tuple(float(s + c) for c in range(n_cat)),
            )
            for s in range(n_series)
        ),
        value_format=lambda v: f"{v:.0f}",
    )


@pytest.mark.parametrize("kind", ["bar", "stacked_bar", "line", "area", "donut", "heatmap", "kpi"])
def test_every_kind_renders_valid_svg(kind: str) -> None:
    svg = render_chart(kind, _data())  # type: ignore[arg-type]
    assert svg is not None
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and root.get("viewBox")
    assert "href" not in svg and "http" not in svg.replace("http://www.w3.org/2000/svg", "")


def test_table_and_pivot_have_no_chart() -> None:
    assert render_chart("table", _data()) is None and render_chart("pivot", _data()) is None


def test_labels_are_escaped() -> None:
    data = ChartData(
        categories=("<script>alert(1)</script>",),
        series=(ChartSeries("a", "A & <b>", (1.0,)),),
        value_format=str,
    )
    svg = render_chart("bar", data)
    assert svg is not None
    assert "<script>" not in svg and "&lt;script&gt;" in svg and "A &amp; &lt;b&gt;" in svg


def test_more_than_six_series_fold_to_muted() -> None:
    svg = render_chart("line", _data(n_series=8))
    assert svg is not None
    assert svg.count(MUTED_COLOR) >= 2 and all(c in svg for c in SERIES_COLORS)


def test_other_series_is_muted_not_a_slot() -> None:
    data = ChartData(
        categories=("a",),
        series=(
            ChartSeries("__other__", "Weitere", (2.0,)),
            ChartSeries("1", "Q1", (1.0,)),
        ),
        value_format=str,
    )
    svg = render_chart("bar", data)
    assert svg is not None
    assert MUTED_COLOR in svg and SERIES_COLORS[0] in svg
    assert SERIES_COLORS[1] not in svg


def test_too_many_categories_note() -> None:
    svg = render_chart("bar", _data(n_cat=MAX_CATEGORIES + 5))
    assert svg is not None
    assert "+5" in svg and f"K{MAX_CATEGORIES}<" not in svg and "K0<" in svg


def test_none_values_and_negative_values() -> None:
    data = ChartData(
        categories=("a", "b"),
        series=(ChartSeries("1", "Q", (None, -3.0)),),
        value_format=str,
    )
    for kind in ("bar", "stacked_bar", "line", "area", "donut", "heatmap", "kpi"):
        svg = render_chart(kind, data)  # type: ignore[arg-type]
        assert svg is not None
        ET.fromstring(svg)


def test_values_go_through_value_format() -> None:
    data = ChartData(
        categories=("a",),
        series=(ChartSeries("1", "Q", (1234.0,)),),
        value_format=lambda v: f"<{v:.0f} Tickets>",
    )
    svg = render_chart("kpi", data)
    assert svg is not None and "&lt;1234 Tickets&gt;" in svg


def test_chart_data_from_result() -> None:
    result = make_result(
        [
            col("created", "dimension", "date", grain="month"),
            col("queue", "dimension", "id"),
            col("tickets", "measure", "count"),
        ],
        [
            ["2026-09-01", "1", 1200],
            ["2026-09-01", "2", 5],
            ["2026-10-01", "1", 7],
            ["2026-10-01", "__other__", 3],
        ],
        labels={"queue": {"1": "Support", "2": "Vertrieb"}},
    )
    texts = load_texts("de")
    labels = server_export_labels(result, texts)
    data = chart_data(result, Visualization(type="line", series_dimension=1), labels, texts)
    assert data is not None
    assert data.categories == ("September 2026", "Oktober 2026")
    assert [(s.key, s.label) for s in data.series] == [
        ("1", "Support"),
        ("2", "Vertrieb"),
        ("__other__", "Weitere"),
    ]
    assert data.series[0].values == (1200.0, 7.0)
    assert data.series[1].values == (5.0, None)
    assert data.value_format(1200.0) == "1.200"
    svg = render_chart("line", data)
    assert svg is not None and "Support" in svg and "September 2026" in svg

    # no series dimension: one series per measure; table has no chart
    plain = chart_data(result, Visualization(type="bar"), labels, texts)
    assert plain is not None and len(plain.series) == 1 and plain.series[0].values[0] == 1205.0
    assert chart_data(result, Visualization(type="table"), labels, texts) is None
