"""``tiqora report ...`` CLI: build and inspect the report data."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory


def add_report_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("report", help="Reporting: build and inspect the report data")
    report_sub = p.add_subparsers(dest="report_command")

    backfill_p = report_sub.add_parser(
        "backfill",
        help="Build facts and daily snapshots from the existing tickets (resumes a stopped run)",
    )
    backfill_p.add_argument(
        "--since",
        type=date.fromisoformat,
        default=None,
        metavar="YYYY-MM-DD",
        help="Start at the first ticket created on/after this date (reporting time zone)",
    )
    backfill_p.add_argument(
        "--restart", action="store_true", help="Ignore the stored cursor and start over"
    )
    backfill_p.add_argument(
        "--no-snapshots", action="store_true", help="Skip the daily open-ticket snapshots"
    )
    backfill_p.set_defaults(func=_cmd_backfill)

    status_p = report_sub.add_parser("status", help="Print the state of the report data as JSON")
    status_p.set_defaults(func=_cmd_status)


async def _cmd_backfill(args: argparse.Namespace) -> int:
    from tiqora.jobs.context import JobCancelled
    from tiqora.jobs.service import find_active_job, utc_now
    from tiqora.reporting.backfill import REPORT_BACKFILL_JOB, ReportBackfillPayload, run_backfill
    from tiqora.reporting.state import load_state

    factory = get_session_factory()
    async with factory() as session:
        active = await find_active_job(
            session, kind=REPORT_BACKFILL_JOB.kind, dedupe_key="report_backfill"
        )
    if active is not None:
        print(  # noqa: T201 — CLI output
            f"A backfill job (id {active.id}) is already {active.status}; "
            "cancel it first or wait for it.",
            file=sys.stderr,
        )
        return 1

    async with factory() as session:
        # a CLI run has no job: the worker tick must not resume it through a stale job id
        state = await load_state(session, for_update=True)
        state.backfill_job_id = None
        state.updated_at = utc_now()
        await session.commit()

    async def progress(done: int, total: int | None, message: str | None) -> None:
        print(f"{done}/{total} {message or ''}".rstrip())  # noqa: T201 — CLI output

    async def check_cancelled() -> None:
        return None

    payload = ReportBackfillPayload(
        since=args.since, restart=args.restart, snapshots=not args.no_snapshots
    )
    try:
        result = await run_backfill(
            factory,
            get_settings(),
            payload,
            progress=progress,
            check_cancelled=check_cancelled,
        )
    except JobCancelled:
        print("The backfill was taken over by another run.", file=sys.stderr)  # noqa: T201
        return 1
    print(result)  # noqa: T201 — CLI output
    return 0


async def _cmd_status(args: argparse.Namespace) -> int:
    from tiqora.reporting.state import facts_status

    async with get_session_factory()() as session:
        status = await facts_status(session)
    print(status.model_dump_json(indent=2))  # noqa: T201 — CLI output
    return 0
