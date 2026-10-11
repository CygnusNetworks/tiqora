"""Server-side report texts and number formats for unattended runs (PDF, scheduled mails).

The UI texts live in the frontend i18n files. The server needs a small, stable
subset of the ``reports`` namespace (column titles, enum values, the "Others"
bucket, ...), which ``--sync`` copies into ``texts_data/`` so the wheel carries
it. Only the allowlisted subtrees are bundled and compared, so unrelated UI copy
edits never turn backend CI red.

The formatting mirrors ``frontend/src/components/reports/reportFormat.ts``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Final, Literal

from tiqora.reporting.export import OTHER_KEY
from tiqora.reporting.schemas import (
    MAX_VALUE_LABELS_PER_COLUMN,
    Cell,
    ColumnType,
    ReportResultOut,
)
from tiqora.reporting.sql import Grain

TextLocale = Literal["de", "en"]

DASH: Final = "—"
#: Subtrees of the frontend ``reports`` namespace the server reads. ``builtin`` only
#: contributes ``builtin.<name>.title``.
BUNDLED_KEYS: Final = (
    "measures",
    "dimensions",
    "values",
    "sources",
    "other",
    "totals",
    "hourLabel",
    "weekLabel",
)
_PARAM = re.compile(r"\{\{(\w+)\}\}|\{(\w+)\}")


def text_locale(raw: str | None) -> TextLocale:
    """``de``, ``de_DE``, ``de-AT`` -> ``de``; everything else -> ``en``."""
    if raw and re.split(r"[-_]", raw.strip().lower(), maxsplit=1)[0] == "de":
        return "de"
    return "en"


def _substitute(template: str, params: dict[str, str | int]) -> str:
    """Replace ``{{name}}`` / ``{name}`` once; unknown names stay as written."""

    def repl(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return str(params[name]) if name in params else match.group(0)

    return _PARAM.sub(repl, template)


def _lookup(tree: Any, key: str) -> str | None:
    node = tree
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


@dataclass(frozen=True, slots=True)
class ReportTexts:
    locale: TextLocale

    def t(self, key: str, default: str | None = None, **params: str | int) -> str:
        """Bundle lookup (``reports.*``); ``de`` falls back to ``en``, then ``default``/``key``."""
        text = _lookup(_bundle(self.locale), key)
        if text is None and self.locale != "en":
            text = _lookup(_bundle("en"), key)
        if text is None:
            text = default if default is not None else key
        return _substitute(text, params)

    def fixed(self, key: str, **params: str | int) -> str:
        """Backend-only text from ``FIXED_TEXTS``."""
        template = FIXED_TEXTS[self.locale].get(key)
        if template is None:
            template = FIXED_TEXTS["en"].get(key, key)
        return _substitute(template, params)


@lru_cache(maxsize=2)
def _bundle(locale: TextLocale) -> dict[str, Any]:
    raw = resources.files("tiqora.reporting.texts_data").joinpath(f"reports.{locale}.json")
    data = json.loads(raw.read_text("utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=2)
def _texts(locale: TextLocale) -> ReportTexts:
    return ReportTexts(locale)


def load_texts(locale: str | None) -> ReportTexts:
    return _texts(text_locale(locale))


_DE_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
_DE_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
_EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

FIXED_TEXTS: Final[dict[TextLocale, dict[str, str]]] = {
    "de": {
        "pdf.generated": "Erstellt am {date} um {time}",
        "pdf.period": "Zeitraum: {start} – {end}",
        "pdf.truncated": (
            "Es werden nur die ersten {n} Zeilen gezeigt. "
            "Die vollständige Liste erhalten Sie als Excel-Datei."
        ),
        "pdf.empty": "Keine Daten im gewählten Zeitraum.",
        "pdf.page": "Seite {page} von {pages}",
        "pdf.owner": "Erstellt mit den Berechtigungen von {owner}.",
        "mail.default_subject": "Bericht: {{schedule}} ({{period}})",
        "mail.default_body": (
            "Guten Tag,\n\n"
            "anbei erhalten Sie den Bericht „{{schedule}}“ für den Zeitraum {{period}}.\n"
            "Die Datei finden Sie im Anhang dieser Nachricht."
        ),
        "mail.footer": (
            "Dieser Bericht wurde mit den Berechtigungen von {owner} erstellt. Zeitplan: {link}"
        ),
        "mail.link_fallback": "Berichte, Zeitpläne",
        "mail.failed_subject": "Zeitplan „{name}“: Bericht konnte nicht erstellt werden",
        "mail.failed_body": (
            "Guten Tag,\n\n"
            "der Bericht des Zeitplans „{name}“ konnte nicht erstellt oder versendet werden.\n"
            "Grund: {reason}\n\n"
            "Bitte prüfen Sie den Zeitplan: {link}"
        ),
        "mail.paused_body": (
            "Guten Tag,\n\n"
            "der Zeitplan „{name}“ wurde angehalten, weil mehrere Versuche in Folge "
            "fehlgeschlagen sind.\n"
            "Grund: {reason}\n\n"
            "Bitte prüfen Sie den Zeitplan und setzen Sie ihn danach fort: {link}"
        ),
        "recurrence.daily": "Täglich um {time}",
        "recurrence.workdays": "Montag bis Freitag um {time}",
        "recurrence.weekly": "Jeden {days} um {time}",
        "recurrence.monthly": "Monatlich am {day}. um {time}",
        "recurrence.yearly": "Jährlich am {day}. {month} um {time}",
        "recurrence.clamp": "(in kürzeren Monaten am letzten Tag)",
        "recurrence.and": "und",
        "quarter": "Q{q} {year}",
        **{f"month.{i}": name for i, name in enumerate(_DE_MONTHS, 1)},
        **{f"weekday.{i}": name for i, name in enumerate(_DE_WEEKDAYS, 1)},
    },
    "en": {
        "pdf.generated": "Generated on {date} at {time}",
        "pdf.period": "Period: {start} – {end}",
        "pdf.truncated": (
            "Only the first {n} rows are shown. You can get the complete list as an Excel file."
        ),
        "pdf.empty": "No data in the selected period.",
        "pdf.page": "Page {page} of {pages}",
        "pdf.owner": "Created with the permissions of {owner}.",
        "mail.default_subject": "Report: {{schedule}} ({{period}})",
        "mail.default_body": (
            "Hello,\n\n"
            "please find the report “{{schedule}}” for the period {{period}} attached.\n"
            "The file is attached to this message."
        ),
        "mail.footer": (
            "This report was created with the permissions of {owner}. Schedule: {link}"
        ),
        "mail.link_fallback": "Reports, Schedules",
        "mail.failed_subject": "Schedule “{name}”: the report could not be created",
        "mail.failed_body": (
            "Hello,\n\n"
            "the report of the schedule “{name}” could not be created or sent.\n"
            "Reason: {reason}\n\n"
            "Please check the schedule: {link}"
        ),
        "mail.paused_body": (
            "Hello,\n\n"
            "the schedule “{name}” was paused because several attempts in a row failed.\n"
            "Reason: {reason}\n\n"
            "Please check the schedule and resume it afterwards: {link}"
        ),
        "recurrence.daily": "Daily at {time}",
        "recurrence.workdays": "Monday to Friday at {time}",
        "recurrence.weekly": "Every {days} at {time}",
        "recurrence.monthly": "Monthly on day {day} at {time}",
        "recurrence.yearly": "Yearly on {month} {day} at {time}",
        "recurrence.clamp": "(on the last day in shorter months)",
        "recurrence.and": "and",
        "quarter": "Q{q} {year}",
        **{f"month.{i}": name for i, name in enumerate(_EN_MONTHS, 1)},
        **{f"weekday.{i}": name for i, name in enumerate(_EN_WEEKDAYS, 1)},
    },
}


def format_number(value: float, locale: TextLocale, digits: tuple[int, int]) -> str:
    """Like ``Intl.NumberFormat`` with min/max fraction digits (half away from zero)."""
    minimum, maximum = digits
    quantum = Decimal(1).scaleb(-maximum)
    rounded = Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP)
    text = f"{rounded:,.{maximum}f}"
    integer, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0").ljust(minimum, "0")
    if locale == "de":
        integer = integer.replace(",", ".")
        return f"{integer},{fraction}" if fraction else integer
    return f"{integer}.{fraction}" if fraction else integer


def _day_unit(locale: TextLocale) -> str:
    return "T" if locale == "de" else "d"


def _round1(n: float) -> float:
    return float(Decimal(repr(float(n))).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _format_minutes(minutes: float, locale: TextLocale) -> str:
    if abs(_round1(minutes)) < 60:
        return f"{format_number(minutes, locale, (0, 1))} min"
    hours = minutes / 60
    if abs(_round1(hours)) < 24:
        return f"{format_number(hours, locale, (0, 1))} h"
    return f"{format_number(minutes / 1440, locale, (0, 1))} {_day_unit(locale)}"


def format_measure(value: Cell, col_type: ColumnType, locale: TextLocale) -> str:
    """Measure cell by column type; ``None`` is an em dash."""
    if value is None:
        return DASH
    try:
        n = float(value)
    except ValueError:
        return str(value)
    if n != n or n in (float("inf"), float("-inf")):
        return str(value)
    if col_type == "percent":
        return f"{format_number(n * 100, locale, (0, 1))} %"
    if col_type == "minutes":
        return _format_minutes(n, locale)
    if col_type == "hours":
        return _format_minutes(n * 60, locale)
    if col_type == "days":
        return f"{format_number(n, locale, (0, 1))} {_day_unit(locale)}"
    if col_type == "units":
        return format_number(n, locale, (2, 2))
    if col_type == "number":
        return format_number(n, locale, (0, 2))
    if col_type == "count":
        return format_number(n, locale, (0, 0))
    return str(value)


def _parse_day(value: str) -> date | None:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def format_date_bucket(value: str, grain: Grain | None, texts: ReportTexts) -> str:
    """Mirror ``dateBucketLabel``: week "KW 41", month "Oktober 2026", quarter "Q4 2026", ..."""
    day = _parse_day(value)
    if day is None:
        return value
    if grain == "week":
        return texts.t("reports.weekLabel", week=day.isocalendar()[1])
    if grain == "month":
        return f"{texts.fixed(f'month.{day.month}')} {day.year}"
    if grain == "quarter":
        return texts.fixed("quarter", q=(day.month - 1) // 3 + 1, year=day.year)
    if grain == "year":
        return str(day.year)
    if texts.locale == "de":
        return f"{day.day:02d}.{day.month:02d}.{day.year}"
    return f"{day.month:02d}/{day.day:02d}/{day.year}"


@dataclass(frozen=True, slots=True)
class ExportLabels:
    column_labels: dict[str, str]
    value_labels: dict[str, dict[str, str]]
    totals_label: str


def _cell_label(result: ReportResultOut, index: int, value: Cell, texts: ReportTexts) -> str:
    column = result.columns[index]
    text = str(value)
    own = result.labels.get(column.key, {}).get(text)
    if own is not None:
        return own
    if text == OTHER_KEY:
        return texts.t("reports.other")
    if column.i18n_values:
        return texts.t(f"{column.i18n_values}.{text}", default=text)
    if column.type == "date":
        return format_date_bucket(text, column.grain, texts)
    if column.type == "hour":
        try:
            return texts.t("reports.hourLabel", hour=f"{int(float(text)):02d}")
        except ValueError:
            return text
    if column.type == "weekday":
        return texts.t(f"reports.values.weekday.{text}", default=text)
    return text


def server_export_labels(result: ReportResultOut, texts: ReportTexts) -> ExportLabels:
    """Mirror the frontend ``exportLabels``: headers and enum/id values for the export."""
    column_labels: dict[str, str] = {}
    value_labels: dict[str, dict[str, str]] = {}
    for idx, col in enumerate(result.columns):
        column_labels[col.key] = texts.t(col.label_key, default=col.key)
        if col.role != "dimension":
            continue
        mapping: dict[str, str] = {}
        for row in result.rows:
            cell = row[idx]
            if cell is None:
                continue
            text = str(cell)
            if text in mapping:
                continue
            if col.type == "date" and text != OTHER_KEY:
                continue
            if len(mapping) >= MAX_VALUE_LABELS_PER_COLUMN:
                break
            mapping[text] = _cell_label(result, idx, cell, texts)
        if mapping:
            value_labels[col.key] = mapping
    return ExportLabels(column_labels, value_labels, texts.t("reports.totals"))


def extract_subset(reports: dict[str, Any]) -> dict[str, Any]:
    """The allowlisted part of the frontend ``reports`` namespace."""
    subset: dict[str, Any] = {k: reports[k] for k in BUNDLED_KEYS if k in reports}
    builtin = reports.get("builtin")
    if isinstance(builtin, dict):
        subset["builtin"] = {
            name: {"title": entry["title"]}
            for name, entry in builtin.items()
            if isinstance(entry, dict) and "title" in entry
        }
    return subset


def _render(locale: str, frontend_dir: Path) -> str:
    source = json.loads((frontend_dir / f"{locale}.json").read_text("utf-8"))
    payload = {"reports": extract_subset(source["reports"])}
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m tiqora.reporting.texts")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sync", action="store_true", help="write the bundles")
    group.add_argument("--check", action="store_true", help="fail if the bundles are stale")
    parser.add_argument("--frontend-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    here = Path(__file__).resolve()
    frontend_dir = args.frontend_dir or here.parents[4] / "frontend" / "src" / "i18n" / "locales"
    target = here.parent / "texts_data"
    stale = False
    for locale in ("de", "en"):
        wanted = _render(locale, frontend_dir)
        path = target / f"reports.{locale}.json"
        if args.sync:
            path.write_text(wanted, encoding="utf-8")
        elif not path.exists() or path.read_text("utf-8") != wanted:
            print(f"stale: {path} (run: uv run python -m tiqora.reporting.texts --sync)")
            stale = True
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
