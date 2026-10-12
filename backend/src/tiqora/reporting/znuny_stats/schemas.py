"""API models of the Znuny statistics import (plan D7)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tiqora.reporting.definition import ReportDefinition
from tiqora.reporting.znuny_stats.xml_hash import MAX_FILE_BYTES

#: base64 of MAX_FILE_BYTES (ruling: 1 MB, so the limit holds behind the proxy)
MAX_FILE_BASE64: int = (MAX_FILE_BYTES + 2) // 3 * 4


class ImportIssueOut(BaseModel):
    element: str
    value: str
    reason: str
    severity: Literal["dropped", "approximated"]


class StatPreviewOut(BaseModel):
    import_key: str
    stat_id: int | None
    stat_number: str | None
    title: str
    description: str
    object_label: str
    target: Literal["report", "saved_search", "none"]
    issues: list[ImportIssueOut]
    definition: ReportDefinition | None
    already_imported: Literal["report", "saved_search"] | None
    existing_id: int | None


class StatLoadErrorOut(BaseModel):
    """``stat_id`` is None in the aggregate entry non-admins get (no ids of unreadable stats)."""

    stat_id: int | None
    message: str


class ZnunyStatsPreviewOut(BaseModel):
    stats: list[StatPreviewOut]
    errors: list[StatLoadErrorOut]
    truncated: bool


class ZnunyStatsFileIn(BaseModel):
    content_base64: str = Field(min_length=1, max_length=MAX_FILE_BASE64)


class ZnunyStatsApplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stat_ids: list[int] = Field(default_factory=list, max_length=200)
    file_content_base64: str | None = Field(None, max_length=MAX_FILE_BASE64)


class ImportedStatOut(BaseModel):
    import_key: str
    title: str
    target: Literal["report", "saved_search"]
    id: int
    action: Literal["created", "updated"]
    issues: list[ImportIssueOut]


class ZnunyStatsApplyOut(BaseModel):
    imported: list[ImportedStatOut]
    skipped: list[StatPreviewOut]
