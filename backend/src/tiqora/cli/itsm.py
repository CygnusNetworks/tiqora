"""``tiqora itsm ...`` CLI: Znuny ITSM import and status.

``import`` does not run the import itself: it records the run, enqueues the same job the admin
UI uses and waits for the worker (one code path, one lock; a second start is refused while a
job is active or after the switch).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from tiqora.db.engine import get_session_factory

ROOT_USER_ID = 1  # Znuny's root@localhost; CLI runs have no logged-in agent
POLL_SECONDS = 2.0


def add_itsm_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("itsm", help="ITSM: import Znuny CMDB data, show the state")
    itsm_sub = p.add_subparsers(dest="itsm_command")

    import_p = itsm_sub.add_parser(
        "import",
        help="Import Znuny ITSM data into Tiqora (needs a running worker; waits for it)",
    )
    import_p.add_argument(
        "--dry-run", action="store_true", help="Only count what would be imported"
    )
    import_p.add_argument("--page-size", type=int, default=500, help="CIs per page (default 500)")
    import_p.add_argument("--json", action="store_true", help="Print the run summary as JSON")
    import_p.set_defaults(func=_cmd_import)

    status_p = itsm_sub.add_parser("status", help="Print the ITSM mode and the last import run")
    status_p.add_argument("--json", action="store_true", help="Print as JSON")
    status_p.set_defaults(func=_cmd_status)


def _err(message: str) -> None:
    print(message, file=sys.stderr)  # noqa: T201 — CLI output


async def _wait_tick() -> None:
    """One pause between two polls of the job (tests replace it)."""
    await asyncio.sleep(POLL_SECONDS)


def _run_dict(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "job_id": run.job_id,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "message": run.message,
        "summary": json.loads(run.summary or "{}"),
    }


def _print_steps(summary: dict[str, Any]) -> None:
    for name, counts in summary.get("steps", {}).items():
        print(  # noqa: T201 — CLI output
            f"{name:20} " + " ".join(f"{k}={v}" for k, v in counts.items())
        )
    print(f"issues: {summary.get('issue_count', 0)}")  # noqa: T201 — CLI output


async def _cmd_import(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from tiqora.db.tiqora.models import TiqoraItsmImportRun, TiqoraJob
    from tiqora.itsm.importer.facades import get_ports
    from tiqora.itsm.importer.jobs import (
        ALREADY_SWITCHED,
        IMPORT_DEDUPE_KEY,
        IMPORT_JOB_KIND,
        ImportBusy,
        ItsmImportPayload,
        start_run,
    )
    from tiqora.jobs.service import TERMINAL_STATUSES, enqueue_job

    factory = get_session_factory()
    async with factory() as session:
        if await get_ports(session).mode.is_switched():
            _err(ALREADY_SWITCHED)
            return 1
        try:
            run = await start_run(
                session, kind="dry_run" if args.dry_run else "import", user_id=ROOT_USER_ID
            )
        except ImportBusy as exc:
            await session.rollback()
            _err(f"{exc} Warten Sie, bis der Lauf beendet ist, oder brechen Sie ihn ab.")
            return 1
        job = await enqueue_job(
            session,
            kind=IMPORT_JOB_KIND,
            payload=ItsmImportPayload(
                user_id=ROOT_USER_ID,
                dry_run=bool(args.dry_run),
                run_id=run.id,
                page_size=max(1, args.page_size),
            ),
            created_by=ROOT_USER_ID,
            dedupe_key=IMPORT_DEDUPE_KEY,
        )
        run.job_id = job.id
        run_id, job_id = run.id, job.id
        await session.commit()

    print(f"Import-Lauf {run_id} (Job {job_id}) wartet auf den Worker …", file=sys.stderr)  # noqa: T201
    while True:
        async with factory() as session:
            status = (
                await session.execute(select(TiqoraJob.status).where(TiqoraJob.id == job_id))
            ).scalar_one_or_none()
        if status is None or status in TERMINAL_STATUSES:
            break
        await _wait_tick()

    async with factory() as session:
        run_row = await session.get(TiqoraItsmImportRun, run_id)
    assert run_row is not None
    result = _run_dict(run_row)
    if args.json:
        print(json.dumps(result, indent=2))  # noqa: T201 — CLI output
    else:
        print(f"Status: {result['status']}")  # noqa: T201 — CLI output
        if result["message"]:
            print(result["message"])  # noqa: T201 — CLI output
        _print_steps(result["summary"])
    return 0 if result["status"] == "done" else 1


async def _cmd_status(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from tiqora.db.tiqora.models import TiqoraItsmImportRun
    from tiqora.itsm.importer.jobs import IMPORT_DEDUPE_KEY, IMPORT_JOB_KIND, SWITCH_JOB_KIND
    from tiqora.itsm.mode import get_itsm_mode, is_switched
    from tiqora.jobs.service import find_active_job

    async with get_session_factory()() as session:
        state = await get_itsm_mode(session, fresh=True)
        switched = await is_switched(session)
        last = (
            (
                await session.execute(
                    select(TiqoraItsmImportRun).order_by(TiqoraItsmImportRun.id.desc()).limit(1)
                )
            )
            .scalars()
            .first()
        )
        active = None
        for kind in (IMPORT_JOB_KIND, SWITCH_JOB_KIND):
            active = active or await find_active_job(
                session, kind=kind, dedupe_key=IMPORT_DEDUPE_KEY
            )
    data: dict[str, Any] = {
        "mode": state.mode,
        "switched": switched,
        "active_job_id": active.id if active is not None else None,
        "last_run": _run_dict(last) if last is not None else None,
    }
    if args.json:
        print(json.dumps(data, indent=2))  # noqa: T201 — CLI output
        return 0
    print(f"mode: {data['mode']}  switched: {switched}")  # noqa: T201 — CLI output
    if active is not None:
        print(f"active job: {active.id} ({active.status})")  # noqa: T201 — CLI output
    if last is None:
        print("last run: none")  # noqa: T201 — CLI output
    else:
        print(  # noqa: T201 — CLI output
            f"last run: #{last.id} {last.kind} {last.status} {last.started_at.isoformat()}"
        )
    return 0
