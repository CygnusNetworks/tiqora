"""Request and response models of the report-schedule API (plan D11).

The run models are named ``ScheduleRun*``: ``ReportRunOut`` already exists in
``reporting.schemas`` (the body of ``POST /reports/run``) and would collide in the OpenAPI
document.
"""

from __future__ import annotations

import re
from email.utils import parseaddr
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tiqora.domain.schemas import UtcDateTime
from tiqora.jobs.schemas import JobAccepted
from tiqora.reporting.definition import TimeRange
from tiqora.reporting.recurrence import Recurrence
from tiqora.reporting.schemas import ParameterValue, ReportOwnerOut
from tiqora.reporting.sql import Grain

_CONTROL: Final = re.compile(r"[\r\n\t]+")
_FORBIDDEN_IN_ADDRESS: Final = re.compile(r"[\s,;<>\"()\[\]\\]")


def clean_line(value: str) -> str:
    """One-line free text: control characters become a space (they would break mail headers)."""
    return _CONTROL.sub(" ", value).strip()


def normalise_address(value: str) -> str | None:
    """Lower-cased single addr-spec, or None when ``value`` is anything else."""
    candidate = value.strip().lower()
    if not candidate or _FORBIDDEN_IN_ADDRESS.search(candidate):
        return None
    if candidate.count("@") != 1 or parseaddr(candidate) != ("", candidate):
        return None
    local, domain = candidate.split("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    if ".." in domain or not candidate.isascii():
        return None
    return candidate


class ScheduleItemOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_range: TimeRange | None = None
    grain: Grain | None = None
    parameters: dict[Annotated[str, Field(max_length=40)], ParameterValue] = Field(
        default_factory=dict, max_length=10
    )


class ScheduleItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_key: str = Field(min_length=1, max_length=120)
    overrides: ScheduleItemOverrides = Field(default_factory=ScheduleItemOverrides)


class ScheduleRecipientsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[int] = Field(default_factory=list, max_length=50)
    group_ids: list[int] = Field(default_factory=list, max_length=20)
    role_ids: list[int] = Field(default_factory=list, max_length=20)
    emails: list[Annotated[str, Field(max_length=250)]] = Field(default_factory=list, max_length=50)

    @field_validator("emails")
    @classmethod
    def _check_emails(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for raw in values:
            address = normalise_address(raw)
            if address is None:
                raise ValueError(f"Keine gültige E-Mail-Adresse: {raw.strip()[:80]}")
            if address not in out:
                out.append(address)
        return out


class ReportScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    items: list[ScheduleItemIn] = Field(min_length=1, max_length=10)
    format: Literal["pdf", "xlsx", "csv"] = "pdf"
    recurrence: Recurrence
    #: None -> the owner's zone
    time_zone: str | None = Field(None, max_length=64)
    language: Literal["de", "en"] = "de"
    subject: str = Field("", max_length=250)
    body: str = Field("", max_length=10_000)
    only_if_nonempty: bool = False
    active: bool = True
    recipients: ScheduleRecipientsIn

    @field_validator("name", "subject")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return clean_line(value)


class ReportScheduleUpdateIn(ReportScheduleIn):
    version: int = Field(ge=1)


class ScheduleItemOut(BaseModel):
    report_key: str
    #: saved report name
    title: str | None
    #: built-in i18n key
    title_key: str | None
    #: resolves for the owner right now
    available: bool
    #: German, when not available
    problem: str | None
    overrides: ScheduleItemOverrides


class ScheduleItemSummaryOut(BaseModel):
    """The list shows titles only (no per-item rights check, see the list route)."""

    report_key: str
    title: str | None
    title_key: str | None


class ScheduleRecipientOut(BaseModel):
    kind: Literal["user", "group", "role", "email"]
    id: int | None
    email: str | None
    #: "Erika Beispiel", group / role name, the address
    label: str
    #: "Keine E-Mail-Adresse hinterlegt.", "Ungültig.", "Externe Empfänger sind ausgeschaltet."
    problem: str | None


class ReportScheduleOut(BaseModel):
    id: int
    name: str
    owner: ReportOwnerOut
    is_owner: bool
    can_edit: bool
    items: list[ScheduleItemOut]
    format: Literal["pdf", "xlsx", "csv"]
    #: None only when the stored JSON can no longer be read (``recurrence_text`` says so)
    recurrence: Recurrence | None
    recurrence_text: str
    time_zone: str
    language: Literal["de", "en"]
    subject: str
    body: str
    only_if_nonempty: bool
    active: bool
    paused_reason: str | None
    next_run_at: UtcDateTime | None
    last_run_at: UtcDateTime | None
    last_status: Literal["succeeded", "partial", "failed", "skipped"] | None
    failure_count: int
    recipients: list[ScheduleRecipientOut]
    version: int
    changed_at: UtcDateTime


class ReportScheduleListItemOut(BaseModel):
    """Lightweight row of the list (no availability checks, no recipient views)."""

    id: int
    name: str
    owner: ReportOwnerOut
    is_owner: bool
    can_edit: bool
    items: list[ScheduleItemSummaryOut]
    format: Literal["pdf", "xlsx", "csv"]
    recurrence_text: str
    active: bool
    paused_reason: str | None
    next_run_at: UtcDateTime | None
    last_run_at: UtcDateTime | None
    last_status: Literal["succeeded", "partial", "failed", "skipped"] | None
    failure_count: int
    version: int


class RecurrencePreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recurrence: Recurrence
    time_zone: str | None = Field(None, max_length=64)


class RecurrencePreviewOut(BaseModel):
    #: the next five occurrences
    next_runs: list[UtcDateTime]
    text: str


class ScheduleRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: "Testversand an mich"
    owner_only: bool = True


class ScheduleRunAccepted(BaseModel):
    run_id: int
    job: JobAccepted


class ScheduleRunFileOut(BaseModel):
    id: int
    name: str
    content_type: str
    size_bytes: int


class ScheduleRunOut(BaseModel):
    """One delivery of a schedule (run history). ``detail`` / ``error`` hold no addresses."""

    id: int
    schedule_id: int | None
    schedule_name: str
    run_kind: Literal["schedule", "manual"]
    owner_only: bool
    status: Literal["queued", "running", "succeeded", "partial", "failed", "skipped"]
    due_at: UtcDateTime | None
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    row_count: int | None
    recipient_count: int | None
    detail: str | None
    error: str | None
    job_id: int | None
    files: list[ScheduleRunFileOut]
