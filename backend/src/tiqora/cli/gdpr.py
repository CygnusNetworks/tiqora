"""``tiqora gdpr ...`` CLI: customer anonymization and retention policies."""

from __future__ import annotations

import argparse
import sys

from tiqora.config import get_settings
from tiqora.db.engine import get_session_factory


def add_gdpr_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("gdpr", help="GDPR tools: customer anonymization, retention policies")
    gdpr_sub = p.add_subparsers(dest="gdpr_command")

    anon_p = gdpr_sub.add_parser(
        "anonymize-customer",
        help="Scrub PII for one customer_user (or every customer_user under a customer_id)",
    )
    target = anon_p.add_mutually_exclusive_group(required=True)
    target.add_argument("--login", help="customer_user.login to anonymize")
    target.add_argument(
        "--customer-id", help="Anonymize every customer_user under this customer_id"
    )
    anon_p.add_argument("--seed", type=int, default=None, help="RNG seed for reproducible output")
    anon_p.add_argument(
        "--anonymize-company", action="store_true", help="Also scrub the customer_company name"
    )
    anon_p.add_argument(
        "--force-parallel",
        action="store_true",
        help=(
            "DANGEROUS: run even though schema-ownership is not active. "
            "Anonymizing during parallel operation can confuse a running Znuny."
        ),
    )
    anon_p.add_argument("--actor", default="cli", help="Audit-log actor identifier")
    anon_p.set_defaults(func=_cmd_anonymize_customer)

    report_p = gdpr_sub.add_parser(
        "retention-report", help="Dry-run: which tickets each retention rule would anonymize"
    )
    report_p.set_defaults(func=_cmd_retention_report)

    run_p = gdpr_sub.add_parser("retention-run", help="Apply all configured retention rules")
    run_p.add_argument(
        "--force-parallel",
        action="store_true",
        help=(
            "DANGEROUS: run even though schema-ownership is not active. "
            "Anonymizing during parallel operation can confuse a running Znuny."
        ),
    )
    run_p.add_argument("--actor", default="cli", help="Audit-log actor identifier")
    run_p.set_defaults(func=_cmd_retention_run)


async def _cmd_anonymize_customer(args: argparse.Namespace) -> int:
    from tiqora.gdpr.anonymize import CustomerNotFoundError, anonymize_customer
    from tiqora.gdpr.gate import GdprRefusedError

    settings = get_settings()
    factory = get_session_factory()
    try:
        result = await anonymize_customer(
            factory,
            settings,
            login=args.login,
            customer_id=args.customer_id,
            seed=args.seed,
            anonymize_company=args.anonymize_company,
            force_parallel=args.force_parallel,
            actor=args.actor,
        )
    except GdprRefusedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    except CustomerNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    for line in result.progress:
        print(line)  # noqa: T201
    print()  # noqa: T201
    print(result.render())  # noqa: T201
    return 0


async def _cmd_retention_report(args: argparse.Namespace) -> int:
    from tiqora.gdpr.retention import build_retention_report

    factory = get_session_factory()
    report = await build_retention_report(factory)
    print(report.render())  # noqa: T201
    return 0


async def _cmd_retention_run(args: argparse.Namespace) -> int:
    from tiqora.gdpr.gate import GdprRefusedError
    from tiqora.gdpr.retention import run_retention

    settings = get_settings()
    factory = get_session_factory()
    try:
        result = await run_retention(
            factory,
            settings,
            force_parallel=args.force_parallel,
            actor=args.actor,
        )
    except GdprRefusedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    for line in result.progress:
        print(line)  # noqa: T201
    print()  # noqa: T201
    print(result.render())  # noqa: T201
    return 0
