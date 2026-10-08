"""API models for background jobs."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from tiqora.db.tiqora.models import TiqoraJob, TiqoraJobFile
from tiqora.domain.schemas import UtcDateTime
from tiqora.extensions import Registry
from tiqora.jobs.registry import JOB_HANDLERS


class JobFileOut(BaseModel):
    id: int
    name: str
    content_type: str
    size_bytes: int


class JobOut(BaseModel):
    id: int
    kind: str
    title_key: str | None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    progress_done: int
    progress_total: int | None
    progress_message: str | None
    cancel_requested: bool
    result: dict[str, Any] | None
    error: str | None
    files: list[JobFileOut]
    created_by: int | None
    created_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None


class JobAccepted(BaseModel):
    """202 body of every feature endpoint that starts a job."""

    job_id: int
    status: Literal["queued", "running"]


def _parse_result(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def job_out(job: TiqoraJob, files: list[TiqoraJobFile], registry: Registry) -> JobOut:
    handler = registry.get(JOB_HANDLERS, job.kind)
    return JobOut(
        id=job.id,
        kind=job.kind,
        title_key=handler.title_key if handler is not None else None,
        status=job.status,  # type: ignore[arg-type]
        progress_done=job.progress_done,
        progress_total=job.progress_total,
        progress_message=job.progress_message,
        cancel_requested=bool(job.cancel_requested),
        result=_parse_result(job.result),
        error=job.error,
        files=[
            JobFileOut(id=f.id, name=f.name, content_type=f.content_type, size_bytes=f.size_bytes)
            for f in files
        ],
        created_by=job.created_by,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
