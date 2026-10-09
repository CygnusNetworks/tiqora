"""``tiqora report ...`` CLI: build and inspect the report data."""

from __future__ import annotations

import argparse
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
    from tiqora.reporting.backfill import ReportBackfillPayload, run_backfill

    async def progress(done: int, total: int | None, message: str | None) -> None:
        print(f"{done}/{total} {message or ''}".rstrip())  # noqa: T201 — CLI output

    async def check_cancelled() -> None:
        return None

    payload = ReportBackfillPayload(
        since=args.since, restart=args.restart, snapshots=not args.no_snapshots
    )
    result = await run_backfill(
        get_session_factory(),
        get_settings(),
        payload,
        progress=progress,
        check_cancelled=check_cancelled,
    )
    print(result)  # noqa: T201 — CLI output
    return 0


async def _cmd_status(args: argparse.Namespace) -> int:
    from tiqora.reporting.state import facts_status

    async with get_session_factory()() as session:
        status = await facts_status(session)
    print(status.model_dump_json(indent=2))  # noqa: T201 — CLI output
    return 0
