"""API models of the reporting foundation (status and backfill; reports follow later)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from tiqora.domain.schemas import UtcDateTime


class FactsStatusOut(BaseModel):
    """Whether the derived report data is usable, and how far the first build is."""

    ready: bool
    as_of: UtcDateTime | None
    backfill_status: Literal["pending", "running", "done"]
    backfill_job_id: int | None
    #: 0..1 while the backfill runs, otherwise ``None``
    backfill_progress: float | None


class ReportingStatusOut(FactsStatusOut):
    """Admin view: the status plus the bookkeeping behind it."""

    last_history_id: int
    history_lag: int
    gap_count: int
    dirty_count: int
    last_snapshot_date: date | None
    snapshot_time_zone: str | None
    fact_rows: int
    event_rows: int


class ReportBackfillIn(BaseModel):
    since: date | None = None
    restart: bool = True
    snapshots: bool = True
