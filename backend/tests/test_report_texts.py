"""Server-side report texts and number formats (pure, no database)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import pytest

from tiqora.reporting.schemas import (
    Cell,
    ColumnType,
    FactsStatusOut,
    ReportResultOut,
    ResultColumnOut,
)
from tiqora.reporting.texts import (
    BUNDLED_KEYS,
    FIXED_TEXTS,
    _main,
    extract_subset,
    format_date_bucket,
    format_measure,
    load_texts,
    server_export_labels,
    text_locale,
)

REPO = Path(__file__).resolve().parents[2]
LOCALES = REPO / "frontend/src/i18n/locales"


def _bundled(loc: str) -> dict[str, Any]:
    raw = resources.files("tiqora.reporting.texts_data").joinpath(f"reports.{loc}.json")
    return json.loads(raw.read_text("utf-8"))


def col(
    key: str,
    role: str,
    col_type: ColumnType,
    *,
    i18n_values: str | None = None,
    grain: Any = None,
) -> ResultColumnOut:
    return ResultColumnOut(
        key=key,
        role=role,  # type: ignore[arg-type]
        type=col_type,
        label_key=f"reports.{'measures' if role == 'measure' else 'dimensions'}.{key}",
        grain=grain,
        i18n_values=i18n_values,
    )


def make_result(
    columns: list[ResultColumnOut],
    rows: list[list[Cell]],
    labels: dict[str, dict[str, str]] | None = None,
) -> ReportResultOut:
    return ReportResultOut(
        report_key=None,
        columns=columns,
        rows=rows,
        labels=labels or {},
        totals=None,
        previous=None,
        row_count=len(rows),
        truncated=False,
        time_window=None,
        generated_at=datetime(2026, 10, 11, tzinfo=UTC),
        cached=False,
        facts=FactsStatusOut(
            ready=True,
            as_of=None,
            backfill_status="done",
            backfill_job_id=None,
            backfill_progress=None,
        ),
    )


@pytest.mark.parametrize("loc", ["de", "en"])
def test_bundle_in_sync_with_frontend(loc: str) -> None:
    frontend = json.loads((LOCALES / f"{loc}.json").read_text("utf-8"))["reports"]
    bundled = _bundled(loc)
    assert bundled == {"reports": extract_subset(frontend)}, (
        "run: uv run python -m tiqora.reporting.texts --sync"
    )
    subset = bundled["reports"]
    assert set(BUNDLED_KEYS) <= set(subset), "an allowlisted subtree is missing in the frontend"
    assert subset["builtin"]["volume"] == {"title": frontend["builtin"]["volume"]["title"]}
    assert "description" not in subset["builtin"]["volume"]
    assert "title" not in subset  # unrelated UI copy stays out of the bundle


def test_check_entry_point_passes_and_detects_staleness(tmp_path: Path) -> None:
    assert _main(["--check"]) == 0
    (tmp_path / "de.json").write_text(
        json.dumps({"reports": {"other": "Anders", "totals": "x"}}), encoding="utf-8"
    )
    (tmp_path / "en.json").write_text(json.dumps({"reports": {}}), encoding="utf-8")
    assert _main(["--check", "--frontend-dir", str(tmp_path)]) == 1


def test_text_locale() -> None:
    assert [text_locale(x) for x in ("de", "de_DE", "de-AT", "en", "fr", None, "")] == [
        "de",
        "de",
        "de",
        "en",
        "en",
        "en",
        "en",
    ]


def test_lookup_params_and_fallback() -> None:
    de = load_texts("de")
    assert de.t("reports.totals") == "Summe"
    assert de.t("reports.hourLabel", hour="07") == "07 Uhr"
    assert de.t("reports.nope.missing", default="x") == "x"
    assert de.t("reports.nope.missing") == "reports.nope.missing"
    assert de.t("reports.hourLabel", hour="{{hour}}").count("{{hour}}") == 1  # no recursion
    assert load_texts("fr").locale == "en"
    assert load_texts("en").t("reports.weekLabel", week=7) == "Wk 7"


def test_fixed_texts_are_complete_and_sie_form() -> None:
    assert set(FIXED_TEXTS["de"]) == set(FIXED_TEXTS["en"])
    de = load_texts("de")
    assert de.fixed("pdf.page", page=2, pages=5) == "Seite 2 von 5"
    assert de.fixed("month.10") == "Oktober"
    assert de.fixed("weekday.7") == "Sonntag"
    assert "Sie" in de.fixed("pdf.truncated", n=50)
    assert de.fixed("mail.default_body").startswith("Guten Tag")
    assert " du " not in " ".join(FIXED_TEXTS["de"].values()).lower()
    # unknown placeholders stay for the caller (schedule/period are filled later)
    assert "{{schedule}}" in de.fixed("mail.default_subject")


@pytest.mark.parametrize(
    ("value", "col_type", "de", "en"),
    [
        (1234.5, "number", "1.234,5", "1,234.5"),
        (0.1234, "percent", "12,3 %", "12.3 %"),
        (0.125, "percent", "12,5 %", "12.5 %"),
        (45, "minutes", "45 min", "45 min"),
        (59.96, "minutes", "1 h", "1 h"),
        (1439.9, "minutes", "1 T", "1 d"),
        (3000, "minutes", "2,1 T", "2.1 d"),
        (210, "minutes", "3,5 h", "3.5 h"),
        (2, "hours", "2 h", "2 h"),
        (0.99, "hours", "59,4 min", "59.4 min"),
        (2.5, "units", "2,50", "2.50"),
        (14.5, "units", "14,50", "14.50"),
        (1234, "count", "1.234", "1,234"),
        (1234567, "count", "1.234.567", "1,234,567"),
        (3, "days", "3 T", "3 d"),
        (None, "count", "—", "—"),
    ],
)
def test_format_measure_matches_frontend(
    value: Cell, col_type: ColumnType, de: str, en: str
) -> None:
    assert format_measure(value, col_type, "de") == de
    assert format_measure(value, col_type, "en") == en


def test_date_buckets() -> None:
    de, en = load_texts("de"), load_texts("en")
    assert format_date_bucket("2026-10-05", "week", de) == "KW 41"
    assert format_date_bucket("2026-10-05", "week", en) == "Wk 41"
    assert format_date_bucket("2026-10-01", "month", de) == "Oktober 2026"
    assert format_date_bucket("2026-10-01", "month", en) == "October 2026"
    assert format_date_bucket("2026-10-01", "quarter", de) == "Q4 2026"
    assert format_date_bucket("2026-10-01", "year", de) == "2026"
    assert format_date_bucket("2026-10-11", "day", de) == "11.10.2026"
    assert format_date_bucket("2026-10-11", "day", en) == "10/11/2026"
    assert format_date_bucket("2026-10-11", None, de) == "11.10.2026"
    assert format_date_bucket("garbage", "day", de) == "garbage"


def test_server_export_labels() -> None:
    result = make_result(
        columns=[
            col("state_type", "dimension", "enum", i18n_values="reports.values.state_type"),
            col("queue", "dimension", "id"),
            col("tickets", "measure", "count"),
        ],
        rows=[["open", 4, 3], ["__other__", 5, 1]],
        labels={"queue": {"4": "rep52 Queue"}},
    )
    de = load_texts("de")
    labels = server_export_labels(result, de)
    assert labels.column_labels["tickets"] == de.t("reports.measures.tickets")
    assert labels.column_labels["tickets"] != "reports.measures.tickets"
    assert labels.value_labels["state_type"]["open"] == de.t("reports.values.state_type.open")
    assert labels.value_labels["state_type"]["open"] != "open"
    assert labels.value_labels["state_type"]["__other__"] == "Weitere"
    assert labels.value_labels["queue"]["4"] == "rep52 Queue"
    assert labels.value_labels["queue"]["5"] == "5"
    assert labels.totals_label == "Summe"
    assert "tickets" not in labels.value_labels  # measures carry no value labels
    assert server_export_labels(result, load_texts("en")).totals_label == "Total"


def test_export_labels_hour_weekday_and_dates() -> None:
    result = make_result(
        columns=[
            col("created_hour", "dimension", "hour"),
            col("created_weekday", "dimension", "weekday"),
            col("created", "dimension", "date", grain="day"),
            col("tickets", "measure", "count"),
        ],
        rows=[[7, 1, "2026-10-05", 2], ["__other__", 7, "__other__", 1]],
    )
    labels = server_export_labels(result, load_texts("de"))
    assert labels.value_labels["created_hour"]["7"] == "07 Uhr"
    assert labels.value_labels["created_weekday"]["1"] == "Montag"
    # real dates stay out (XLSX keeps them), only the "Weitere" bucket is labelled
    assert labels.value_labels["created"] == {"__other__": "Weitere"}
