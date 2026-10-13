"""Import report: per-step counters and the issue list (summary JSON for the run row, CSV file)."""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from tiqora.reporting.export import FORMULA_PREFIXES

CSV_HEADER = ("Schritt", "Code", "Objekt", "Meldung")


@dataclass(frozen=True, slots=True)
class ImportIssue:
    step: str
    code: str
    object_ref: str
    message: str


@dataclass(slots=True)
class StepCounts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    problems: int = 0


_COUNT_FIELDS = frozenset(f.name for f in fields(StepCounts))


def _csv_cell(value: str) -> str:
    # issue messages carry Znuny-controlled text that admins open in Excel (F16)
    return "'" + value if value.startswith(FORMULA_PREFIXES) else value


@dataclass
class ImportReport:
    steps: dict[str, StepCounts] = field(default_factory=dict)
    issues: list[ImportIssue] = field(default_factory=list)

    def start_step(self, name: str) -> StepCounts:
        """Make the step appear in the report even when it counts nothing."""
        return self.steps.setdefault(name, StepCounts())

    def count(self, step: str, field_name: str, n: int = 1) -> None:
        if field_name not in _COUNT_FIELDS:
            raise ValueError(f"unknown counter {field_name!r}")
        counts = self.start_step(step)
        setattr(counts, field_name, getattr(counts, field_name) + n)

    def issue(self, step: str, code: str, object_ref: str, message: str) -> None:
        self.start_step(step)
        self.issues.append(ImportIssue(step, code, object_ref, message))

    def summary(self, max_issues: int = 200) -> dict[str, Any]:
        return {
            "steps": {name: asdict(c) for name, c in self.steps.items()},
            "issues": [asdict(i) for i in self.issues[:max_issues]],
            "issue_count": len(self.issues),
        }

    def to_csv(self) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        writer.writerow(CSV_HEADER)
        for i in self.issues:
            writer.writerow(
                [
                    _csv_cell(i.step),
                    _csv_cell(i.code),
                    _csv_cell(i.object_ref),
                    _csv_cell(i.message),
                ]
            )
        return buf.getvalue().encode("utf-8-sig")
