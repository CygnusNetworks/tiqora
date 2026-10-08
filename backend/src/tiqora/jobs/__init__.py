"""Background jobs (see :mod:`tiqora.jobs.settings` for the admin settings)."""

from __future__ import annotations

from tiqora.jobs.context import JobCancelled, JobContext, JobFailed
from tiqora.jobs.registry import JOB_HANDLERS, JobHandler
from tiqora.jobs.service import (
    ACTIVE_STATUSES,
    MAX_RESULT_CHARS,
    TERMINAL_STATUSES,
    JobNotCancellable,
    UnknownJobKind,
    can_view_job,
    enqueue_job,
    get_job,
    get_job_file,
    list_job_files,
    list_jobs,
    purge_finished_jobs,
    request_cancel,
    utc_now,
)

__all__ = [
    "ACTIVE_STATUSES",
    "JOB_HANDLERS",
    "MAX_RESULT_CHARS",
    "TERMINAL_STATUSES",
    "JobCancelled",
    "JobContext",
    "JobFailed",
    "JobHandler",
    "JobNotCancellable",
    "UnknownJobKind",
    "can_view_job",
    "enqueue_job",
    "get_job",
    "get_job_file",
    "list_job_files",
    "list_jobs",
    "purge_finished_jobs",
    "request_cancel",
    "utc_now",
]
