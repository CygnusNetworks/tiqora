"""API models of ``/api/v1/admin/itsm/import`` (plan D8)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from tiqora.db.tiqora.models import TiqoraItsmImportRun
from tiqora.domain.schemas import UtcDateTime
from tiqora.itsm.importer.issue_codes import ISSUE_CODES
from tiqora.itsm.importer.switch import ImportStatus


class ItsmImportStepCountsOut(BaseModel):
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    problems: int = 0


class ItsmImportIssueOut(BaseModel):
    step: str
    code: str
    object_ref: str
    message: str
    #: German explanation of the code (``ISSUE_CODES``); empty for an unknown code
    explanation: str


class ItsmImportRunOut(BaseModel):
    id: int
    kind: Literal["dry_run", "import", "switch", "rollback"]
    status: Literal["running", "done", "failed", "cancelled"]
    started_at: UtcDateTime
    finished_at: UtcDateTime | None
    started_by_name: str | None
    job_id: int | None
    profile: str | None
    message: str | None
    steps: dict[str, ItsmImportStepCountsOut]
    #: the first issues of the run (the full list is in the CSV file of the job)
    issues: list[ItsmImportIssueOut]
    issue_count: int


class ItsmImportSwitchInfoOut(BaseModel):
    switched_at: UtcDateTime | None
    by: int | None
    run_id: int | None


class ItsmImportZnunyCountsOut(BaseModel):
    cis: int
    classes: int
    changes: int
    workorders: int


class ItsmImportStatusOut(BaseModel):
    mode: str
    znuny_itsm_present: bool
    profile: str | None
    switched: ItsmImportSwitchInfoOut | None
    readonly_flag: bool
    last_run: ItsmImportRunOut | None
    active_job_id: int | None
    can_import: bool
    can_switch: bool
    blocked_reason: str | None
    znuny_counts: ItsmImportZnunyCountsOut | None
    znuny_changes_since_switch: int | None


class ItsmImportStartIn(BaseModel):
    dry_run: bool


class ItsmImportSwitchIn(BaseModel):
    confirm: str = Field(max_length=50)
    addon_installed: bool
    edits_stopped: bool = False
    #: "Umstellen ohne CI-Daten": Znuny ITSM release the importer cannot read (itsm1 F19)
    without_ci_data: bool = False


class ItsmImportRollbackIn(BaseModel):
    confirm: str = Field(max_length=50)


def _summary(run: TiqoraItsmImportRun) -> dict[str, Any]:
    try:
        raw = json.loads(run.summary or "{}")
    except ValueError:
        return {}
    return raw if isinstance(raw, dict) else {}


def run_out(run: TiqoraItsmImportRun, names: dict[int, str]) -> ItsmImportRunOut:
    summary = _summary(run)
    steps = {
        str(name): ItsmImportStepCountsOut(
            **{k: int(v) for k, v in counts.items() if k in _COUNT_KEYS}
        )
        for name, counts in (summary.get("steps") or {}).items()
        if isinstance(counts, dict)
    }
    issues = [
        ItsmImportIssueOut(
            step=str(i.get("step", "")),
            code=str(i.get("code", "")),
            object_ref=str(i.get("object_ref", "")),
            message=str(i.get("message", "")),
            explanation=ISSUE_CODES.get(str(i.get("code", "")), ""),
        )
        for i in summary.get("issues") or []
        if isinstance(i, dict)
    ]
    count = summary.get("issue_count")
    return ItsmImportRunOut(
        id=run.id,
        kind=run.kind,  # type: ignore[arg-type]
        status=run.status,  # type: ignore[arg-type]
        started_at=run.started_at,
        finished_at=run.finished_at,
        started_by_name=names.get(run.started_by),
        job_id=run.job_id,
        profile=run.profile,
        message=run.message,
        steps=steps,
        issues=issues,
        issue_count=count if isinstance(count, int) else len(issues),
    )


_COUNT_KEYS = frozenset(ItsmImportStepCountsOut.model_fields)


def status_out(status: ImportStatus, names: dict[int, str]) -> ItsmImportStatusOut:
    switched = (
        ItsmImportSwitchInfoOut(
            switched_at=status.switched.switched_at,
            by=status.switched.by,
            run_id=status.switched.run_id,
        )
        if status.switched is not None
        else None
    )
    counts = (
        ItsmImportZnunyCountsOut(**status.znuny_counts) if status.znuny_counts is not None else None
    )
    return ItsmImportStatusOut(
        mode=status.mode,
        znuny_itsm_present=status.znuny_itsm_present,
        profile=status.profile,
        switched=switched,
        readonly_flag=status.readonly_flag,
        last_run=run_out(status.last_run, names) if status.last_run is not None else None,
        active_job_id=status.active_job_id,
        can_import=status.can_import,
        can_switch=status.can_switch,
        blocked_reason=status.blocked_reason,
        znuny_counts=counts,
        znuny_changes_since_switch=status.znuny_changes_since_switch,
    )
