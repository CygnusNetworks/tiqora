"""Admin reporting API: build status and the (re)build of the report data."""

from __future__ import annotations

from fastapi import APIRouter, status

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.jobs.schemas import JobAccepted
from tiqora.jobs.service import enqueue_job, find_active_job
from tiqora.reporting.backfill import REPORT_BACKFILL_JOB, ReportBackfillPayload
from tiqora.reporting.schemas import ReportBackfillIn, ReportingStatusOut
from tiqora.reporting.state import load_state, reporting_status

router = APIRouter(prefix="/reporting", tags=["admin:reporting"])

BACKFILL_DEDUPE_KEY = "report_backfill"


@router.get("/status", response_model=ReportingStatusOut)
async def get_reporting_status(_admin: AdminUser, session: DbSession) -> ReportingStatusOut:
    return await reporting_status(session)


@router.post("/backfill", status_code=status.HTTP_202_ACCEPTED, response_model=JobAccepted)
async def start_backfill(
    body: ReportBackfillIn, admin: AdminUser, session: DbSession
) -> JobAccepted:
    """Queue the (re)build of the report data; a second request joins the pending job.

    With ``restart`` the data counts as "not ready" again, unless a job is already
    active: that job owns the state and is joined as it is.
    """
    state = await load_state(session, for_update=True)
    active = await find_active_job(
        session, kind=REPORT_BACKFILL_JOB.kind, dedupe_key=BACKFILL_DEDUPE_KEY
    )
    job = active or await enqueue_job(
        session,
        kind=REPORT_BACKFILL_JOB.kind,
        payload=ReportBackfillPayload(**body.model_dump()),
        created_by=admin.id,
        dedupe_key=BACKFILL_DEDUPE_KEY,
    )
    state.backfill_job_id = job.id
    if active is None and body.restart:
        state.backfill_status = "pending"
    await session.commit()
    return JobAccepted(job_id=job.id, status="running" if job.status == "running" else "queued")
