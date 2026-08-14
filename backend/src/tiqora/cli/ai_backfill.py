"""``tiqora ai ...`` CLI: AI subsystem maintenance."""

from __future__ import annotations

import argparse

from tiqora.db.engine import get_session_factory


def add_ai_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("ai", help="AI subsystem maintenance")
    ai_sub = p.add_subparsers(dest="ai_command")

    backfill_p = ai_sub.add_parser(
        "backfill-tool-trace",
        help=(
            "Reconstruct tool_trace_json/run_id on tiqora_ai_article_origin rows "
            "that predate the tool-trace feature, from tiqora_ai_audit_log"
        ),
    )
    backfill_p.add_argument(
        "--dry-run", action="store_true", help="Print the intended writes without applying them"
    )
    backfill_p.add_argument(
        "--ticket-id", type=int, default=None, help="Limit to one ticket (default: all tickets)"
    )
    backfill_p.set_defaults(func=_cmd_backfill_tool_trace)


async def _cmd_backfill_tool_trace(args: argparse.Namespace) -> int:
    from tiqora.ai.backfill_tool_trace import run_backfill

    factory = get_session_factory()
    async with factory() as session:
        result = await run_backfill(session, dry_run=args.dry_run, ticket_id=args.ticket_id)
        if not args.dry_run:
            await session.commit()
    print(result.render())  # noqa: T201
    return 0
