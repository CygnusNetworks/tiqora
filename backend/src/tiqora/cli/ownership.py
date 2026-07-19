"""``tiqora ownership ...`` CLI: schema-ownership gate and orphan report (Phase 5)."""

from __future__ import annotations

import argparse
import sys

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory
from tiqora.domain.ownership import (
    REQUIRED_CONFIRM_PHRASE,
    OwnershipConfirmError,
    OwnershipPreflightError,
    enable_ownership,
    get_ownership_state,
)


def add_ownership_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("ownership", help="Schema-ownership gate (Phase 5 cutover)")
    op_sub = p.add_subparsers(dest="ownership_command")

    status_p = op_sub.add_parser("status", help="Show schema-ownership gate state")
    status_p.set_defaults(func=_cmd_status)

    enable_p = op_sub.add_parser(
        "enable", help="Set the DB marker after preflight checks (env flag set separately)"
    )
    enable_p.add_argument(
        "--confirm",
        required=True,
        help=f'Must be exactly: "{REQUIRED_CONFIRM_PHRASE}"',
    )
    enable_p.add_argument(
        "--force",
        action="store_true",
        help="Skip preflight checks (DANGEROUS — only for verified-safe cutovers)",
    )
    enable_p.add_argument("--history-watermark-minutes", type=int, default=15)
    enable_p.add_argument("--session-watermark-minutes", type=int, default=15)
    enable_p.set_defaults(func=_cmd_enable)

    orphan_p = op_sub.add_parser(
        "orphan-report", help="Read-only report of dangling FK references (no cleanup)"
    )
    orphan_p.set_defaults(func=_cmd_orphan_report)


async def _cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        state = await get_ownership_state(session, settings)
    print("Schema-ownership gate status")  # noqa: T201
    print("=============================")  # noqa: T201
    print(f"env flag  (TIQORA_SCHEMA_OWNERSHIP): {'set' if state.env_flag else 'unset'}")  # noqa: T201
    print(f"DB marker (tiqora_settings key):     {'enabled' if state.db_marker else 'unset'}")  # noqa: T201
    if state.enabled_at:
        print(f"DB marker enabled_at:                 {state.enabled_at}")  # noqa: T201
    chain_status = "YES" if state.active else "no (both gates required)"
    print(f"versions_owned chain active:          {chain_status}")  # noqa: T201
    return 0


async def _cmd_enable(args: argparse.Namespace) -> int:
    factory = get_session_factory()
    async with factory() as session:
        try:
            report = await enable_ownership(
                session,
                confirm=args.confirm,
                force=args.force,
                history_watermark_minutes=args.history_watermark_minutes,
                session_watermark_minutes=args.session_watermark_minutes,
            )
        except OwnershipConfirmError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)  # noqa: T201
            return 2
        except OwnershipPreflightError as exc:
            print(exc.report.render(), file=sys.stderr)  # noqa: T201
            print(  # noqa: T201
                "\nREFUSED: preflight checks failed. Re-run with --force to override "
                "(only if you have independently verified Znuny is shut down).",
                file=sys.stderr,
            )
            return 1
    print(report.render())  # noqa: T201
    print(  # noqa: T201
        "\nDB marker set (tiqora_settings: schema.ownership=enabled). "
        "Now set TIQORA_SCHEMA_OWNERSHIP=1 in the environment and restart Tiqora "
        "processes to activate the versions_owned Alembic chain."
    )
    return 0


async def _cmd_orphan_report(args: argparse.Namespace) -> int:
    from tiqora.domain.orphan_report import build_orphan_report

    factory = get_session_factory()
    async with factory() as session:
        rows = await build_orphan_report(session)
    width = max(len(r.relation) for r in rows) + 2
    print(f"{'relation'.ljust(width)}{'orphan_count'}")  # noqa: T201
    print("-" * (width + 12))  # noqa: T201
    for row in rows:
        print(f"{row.relation.ljust(width)}{row.orphan_count}")  # noqa: T201
    total = sum(r.orphan_count for r in rows)
    print(f"\nTotal dangling references: {total}")  # noqa: T201
    if total:
        print(  # noqa: T201
            "This is a READ-ONLY report — no cleanup is performed. See docs/cutover.md."
        )
    return 0
