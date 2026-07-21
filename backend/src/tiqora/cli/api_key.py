"""``tiqora api-key ...`` CLI: issue / list / revoke / delete Bearer API keys."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from sqlalchemy import select

from tiqora.db.engine import get_session_factory
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraApiKey
from tiqora.domain.auth import generate_api_key, hash_api_key


def add_api_key_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser("api-key", help="API key lifecycle: create, list, revoke, delete")
    key_sub = p.add_subparsers(dest="api_key_command")

    create_p = key_sub.add_parser("create", help="Issue a new API key for a user")
    create_p.add_argument("--user", type=int, required=True, help="Target users.id")
    create_p.add_argument("--name", required=True, help="Human-readable key name")
    create_p.add_argument(
        "--expires",
        default=None,
        help="Optional hard expiry as ISO-8601 (e.g. 2027-01-01T00:00:00)",
    )
    create_p.set_defaults(func=_cmd_create)

    list_p = key_sub.add_parser("list", help="List API keys (hashes never shown)")
    list_p.add_argument(
        "--all",
        action="store_true",
        help="Include revoked (valid=false) keys; default shows valid only",
    )
    list_p.set_defaults(func=_cmd_list)

    revoke_p = key_sub.add_parser("revoke", help="Soft-revoke an API key (valid=false)")
    revoke_p.add_argument("id", type=int, help="tiqora_api_key.id")
    revoke_p.set_defaults(func=_cmd_revoke)

    delete_p = key_sub.add_parser("delete", help="Hard-delete an API key row")
    delete_p.add_argument("id", type=int, help="tiqora_api_key.id")
    delete_p.set_defaults(func=_cmd_delete)


def _parse_expires(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(f"ERROR: invalid --expires ISO-8601 value: {raw!r}") from exc
    if value.tzinfo is not None:
        from datetime import UTC

        return value.astimezone(UTC).replace(tzinfo=None)
    return value


async def _cmd_create(args: argparse.Namespace) -> int:
    expires_at = _parse_expires(args.expires)
    factory = get_session_factory()
    async with factory() as session:
        user = (
            await session.execute(select(Users).where(Users.id == args.user, Users.valid_id == 1))
        ).scalar_one_or_none()
        if user is None:
            print(  # noqa: T201
                f"ERROR: target user {args.user} not found or invalid",
                file=sys.stderr,
            )
            return 2
        raw = generate_api_key()
        row = TiqoraApiKey(
            name=args.name,
            key_hash=hash_api_key(raw),
            user_id=args.user,
            valid=True,
            created=datetime.utcnow(),  # noqa: DTZ003 — naive UTC matches DB columns
            expires_at=expires_at,
            created_by=None,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        print(f"Created API key id={row.id} name={row.name!r} user_id={row.user_id}")  # noqa: T201
        print(raw)  # noqa: T201
        print(  # noqa: T201
            "NOTE: This plaintext key will not be shown again. Store it securely.",
            file=sys.stderr,
        )
    return 0


async def _cmd_list(args: argparse.Namespace) -> int:
    factory = get_session_factory()
    async with factory() as session:
        stmt = select(TiqoraApiKey).order_by(TiqoraApiKey.created.desc())
        if not args.all:
            stmt = stmt.where(TiqoraApiKey.valid.is_(True))
        rows = (await session.execute(stmt)).scalars().all()
        if not rows:
            print("(no API keys)")  # noqa: T201
            return 0
        print(  # noqa: T201
            f"{'id':>6}  {'valid':5}  {'user_id':>8}  {'created':19}  "
            f"{'last_used_at':19}  {'expires_at':19}  name"
        )
        for row in rows:
            print(  # noqa: T201
                f"{row.id:6d}  {str(row.valid):5}  {row.user_id:8d}  "
                f"{_fmt_dt(row.created):19}  {_fmt_dt(row.last_used_at):19}  "
                f"{_fmt_dt(row.expires_at):19}  {row.name}"
            )
    return 0


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.isoformat(sep=" ", timespec="seconds")


async def _cmd_revoke(args: argparse.Namespace) -> int:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(TiqoraApiKey, args.id)
        if row is None:
            print(f"ERROR: API key id={args.id} not found", file=sys.stderr)  # noqa: T201
            return 2
        row.valid = False
        await session.commit()
        print(f"Revoked API key id={args.id}")  # noqa: T201
    return 0


async def _cmd_delete(args: argparse.Namespace) -> int:
    factory = get_session_factory()
    async with factory() as session:
        row = await session.get(TiqoraApiKey, args.id)
        if row is None:
            print(f"ERROR: API key id={args.id} not found", file=sys.stderr)  # noqa: T201
            return 2
        await session.delete(row)
        await session.commit()
        print(f"Deleted API key id={args.id}")  # noqa: T201
    return 0
