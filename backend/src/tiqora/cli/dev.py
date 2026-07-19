"""``tiqora dev ...`` CLI: seed and anonymize helpers for local/dev databases."""

from __future__ import annotations

import argparse
import sys

from tiqora.db.engine import get_engine, get_session_factory


def add_dev_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("dev", help="Local/dev-only database helpers: seed, anonymize")
    dev_sub = p.add_subparsers(dest="dev_command")

    seed_p = dev_sub.add_parser(
        "seed",
        help="Seed fake customers/tickets/articles through TicketWriteService",
    )
    seed_p.add_argument("--customers", type=int, default=10, help="Number of customer companies")
    seed_p.add_argument("--tickets", type=int, default=20, help="Number of tickets")
    seed_p.add_argument(
        "--seed", type=int, default=None, help="RNG seed for reproducible fake data"
    )
    seed_p.add_argument(
        "--database-url",
        default=None,
        help="Target DB URL (defaults to the configured DATABASE_URL)",
    )
    seed_p.add_argument(
        "--agent-user-id",
        type=int,
        default=1,
        help="Acting agent user id used to create tickets/articles (default: 1, root@localhost)",
    )
    seed_p.set_defaults(func=_cmd_seed)

    anon_p = dev_sub.add_parser(
        "anonymize",
        help=(
            "Scrub PII (names/emails/bodies) in a restored dump copy. "
            "NEVER run against a live/production database."
        ),
    )
    anon_p.add_argument(
        "--database-url",
        required=True,
        help=(
            "Target DB URL — REQUIRED. Must point at a restored dump copy; "
            "this command never falls back to the configured DATABASE_URL."
        ),
    )
    anon_p.add_argument(
        "--seed", type=int, default=None, help="RNG seed for reproducible anonymization"
    )
    anon_p.add_argument("--batch-size", type=int, default=500, help="Rows updated per batch")
    anon_p.set_defaults(func=_cmd_anonymize)


async def _cmd_seed(args: argparse.Namespace) -> int:
    from tiqora.domain.dev_seed import SeedError, seed_database

    engine = get_engine(args.database_url)
    factory = get_session_factory(engine)
    try:
        result = await seed_database(
            factory,
            customers=args.customers,
            tickets=args.tickets,
            seed=args.seed,
            agent_user_id=args.agent_user_id,
        )
    except SeedError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    print(  # noqa: T201
        f"Seeded {result.customers_created} customer companies "
        f"({result.customer_users_created} customer users), "
        f"{result.tickets_created} tickets, {result.articles_created} articles."
    )
    return 0


async def _cmd_anonymize(args: argparse.Namespace) -> int:
    from tiqora.domain.dev_anonymize import anonymize_database

    engine = get_engine(args.database_url)
    factory = get_session_factory(engine)
    result = await anonymize_database(factory, seed=args.seed, batch_size=args.batch_size)
    for line in result.progress:
        print(line)  # noqa: T201
    print()  # noqa: T201
    print(result.render())  # noqa: T201
    return 0
