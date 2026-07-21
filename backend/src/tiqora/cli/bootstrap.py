"""``tiqora bootstrap`` — greenfield install on an empty database.

Loads the Znuny base schema (if needed), applies the tiqora Alembic chain,
sets the admin password + admin-group membership, and optionally seeds
dev data.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from urllib.parse import urlparse

from alembic import command

from tiqora.bootstrap.schema_loader import detect_dialect, load_base_schema
from tiqora.config import get_settings
from tiqora.znuny.password import hash_password


def add_bootstrap_subparser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = sub.add_parser(
        "bootstrap",
        help=(
            "Greenfield install: load Znuny base schema (if empty), "
            "run tiqora migrations, set admin password, optional --seed"
        ),
    )
    p.add_argument(
        "--database-url",
        default=None,
        help="Target DB URL (defaults to the configured DATABASE_URL)",
    )
    p.add_argument(
        "--admin-password",
        required=True,
        help="Password for the admin login (hashed with Znuny BCRYPT scheme)",
    )
    p.add_argument(
        "--admin-login",
        default="root@localhost",
        help="Admin user login to update (default: root@localhost from base seed)",
    )
    p.add_argument(
        "--skip-schema",
        action="store_true",
        help="Skip Znuny base-schema load (only migrate + admin + optional seed)",
    )
    p.add_argument(
        "--seed",
        action="store_true",
        help="After bootstrap, seed fake customers/tickets (requires faker)",
    )
    p.add_argument(
        "--customers",
        type=int,
        default=10,
        help="With --seed: number of customer companies (default: 10)",
    )
    p.add_argument(
        "--tickets",
        type=int,
        default=20,
        help="With --seed: number of tickets (default: 20)",
    )
    p.add_argument(
        "--seed-value",
        type=int,
        default=None,
        dest="seed_value",
        help="With --seed: RNG seed for reproducible fake data",
    )
    p.set_defaults(func=run_bootstrap)


def _sync_url(url: str) -> str:
    """Normalize to a sync driver URL for pymysql / psycopg2.

    Replacements are prefix-based (not naive substring) so that e.g.
    ``aiomysql`` does not re-match ``mysql://`` inside the driver name.
    """
    replacements = (
        ("postgresql+asyncpg://", "postgresql+psycopg2://"),
        ("mysql+aiomysql://", "mysql+pymysql://"),
        ("mariadb+aiomysql://", "mysql+pymysql://"),
        ("mariadb+pymysql://", "mysql+pymysql://"),
        ("mariadb://", "mysql+pymysql://"),
        ("postgresql://", "postgresql+psycopg2://"),
        ("postgres://", "postgresql+psycopg2://"),
        ("mysql://", "mysql+pymysql://"),
    )
    for old, new in replacements:
        if url.startswith(old):
            return new + url[len(old) :]
    return url


def _async_url(url: str) -> str:
    """Normalize to an asyncio driver URL for asyncpg / aiomysql."""
    replacements = (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mariadb+pymysql://", "mysql+aiomysql://"),
        ("mariadb://", "mysql+aiomysql://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("postgres://", "postgresql+asyncpg://"),
        ("mysql://", "mysql+aiomysql://"),
    )
    for old, new in replacements:
        if url.startswith(old):
            return new + url[len(old) :]
    return url


def _users_table_populated(database_url: str) -> bool:
    """Return True if the ``users`` table exists and has at least one row."""
    dialect = detect_dialect(database_url)
    sync = _sync_url(database_url)
    if dialect == "mysql":
        return _mysql_users_populated(sync)
    return _pg_users_populated(sync)


def _mysql_users_populated(url: str) -> bool:
    import pymysql

    raw = url
    for prefix in ("mysql+pymysql://", "mysql+aiomysql://", "mariadb+pymysql://"):
        if raw.startswith(prefix):
            raw = "mysql://" + raw[len(prefix) :]
            break
    parsed = urlparse(raw)
    conn = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=parsed.username or "root",
        password=parsed.password or "",
        database=(parsed.path or "/test").lstrip("/") or "test",
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'users'"
            )
            row = cur.fetchone()
            if not row or row[0] == 0:
                return False
            cur.execute("SELECT COUNT(*) FROM users")
            count_row = cur.fetchone()
            return bool(count_row and count_row[0] > 0)
    finally:
        conn.close()


def _pg_users_populated(url: str) -> bool:
    import psycopg2

    raw = url
    for prefix in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
        if raw.startswith(prefix):
            raw = "postgresql://" + raw[len(prefix) :]
            break
    conn = psycopg2.connect(raw)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables "
                "  WHERE table_schema = 'public' AND table_name = 'users'"
                ")"
            )
            exists = cur.fetchone()
            if not exists or not exists[0]:
                return False
            cur.execute("SELECT COUNT(*) FROM users")
            count_row = cur.fetchone()
            return bool(count_row and count_row[0] > 0)
    finally:
        conn.close()


def _run_migrations(database_url: str) -> None:
    """Apply tiqora Alembic chain (versions_tiqora only — same as migrate upgrade)."""
    from tiqora.cli.migrate import build_alembic_config

    # env.py reads get_settings().database_url; point it at the target.
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _async_url(database_url)
    get_settings.cache_clear()
    try:
        # Ownership OFF for greenfield: never pull in versions_owned.
        old_flag = os.environ.get("TIQORA_SCHEMA_OWNERSHIP")
        os.environ.pop("TIQORA_SCHEMA_OWNERSHIP", None)
        get_settings.cache_clear()
        try:
            cfg = build_alembic_config(include_owned=False)
            print("Running migrations (tiqora only) -> head")  # noqa: T201
            command.upgrade(cfg, "head")
        finally:
            if old_flag is not None:
                os.environ["TIQORA_SCHEMA_OWNERSHIP"] = old_flag
            get_settings.cache_clear()
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url
        get_settings.cache_clear()


def _set_admin_password(database_url: str, login: str, password: str) -> int:
    """Hash *password* and UPDATE users.pw for *login*; ensure admin group rw."""
    dialect = detect_dialect(database_url)
    sync = _sync_url(database_url)
    pw_hash = hash_password(password)
    now = datetime.now(UTC).replace(tzinfo=None)
    if dialect == "mysql":
        return _set_admin_mysql(sync, login, pw_hash, now)
    return _set_admin_pg(sync, login, pw_hash, now)


def _set_admin_mysql(url: str, login: str, pw_hash: str, now: datetime) -> int:
    import pymysql

    raw = url
    for prefix in ("mysql+pymysql://", "mysql+aiomysql://", "mariadb+pymysql://"):
        if raw.startswith(prefix):
            raw = "mysql://" + raw[len(prefix) :]
            break
    parsed = urlparse(raw)
    conn = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=parsed.username or "root",
        password=parsed.password or "",
        database=(parsed.path or "/test").lstrip("/") or "test",
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE login = %s", (login,))
            row = cur.fetchone()
            if not row:
                print(  # noqa: T201
                    f"ERROR: admin login {login!r} not found in users table",
                    file=sys.stderr,
                )
                return 1
            user_id = int(row[0])
            cur.execute(
                "UPDATE users SET pw = %s, change_time = %s, change_by = %s WHERE id = %s",
                (pw_hash, now, user_id, user_id),
            )
            cur.execute(
                "SELECT id FROM permission_groups WHERE name = %s LIMIT 1",
                ("admin",),
            )
            g_row = cur.fetchone()
            if not g_row:
                print(  # noqa: T201
                    "ERROR: permission_groups row name='admin' not found",
                    file=sys.stderr,
                )
                return 1
            group_id = int(g_row[0])
            cur.execute(
                "SELECT 1 FROM group_user "
                "WHERE user_id = %s AND group_id = %s AND permission_key = %s",
                (user_id, group_id, "rw"),
            )
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO group_user "
                    "(user_id, group_id, permission_key, create_by, create_time, "
                    "change_by, change_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (user_id, group_id, "rw", user_id, now, user_id, now),
                )
            print(f"Admin password set for login={login!r} (user id {user_id})")  # noqa: T201
            return 0
    finally:
        conn.close()


def _set_admin_pg(url: str, login: str, pw_hash: str, now: datetime) -> int:
    import psycopg2

    raw = url
    for prefix in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
        if raw.startswith(prefix):
            raw = "postgresql://" + raw[len(prefix) :]
            break
    conn = psycopg2.connect(raw)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE login = %s", (login,))
            row = cur.fetchone()
            if not row:
                print(  # noqa: T201
                    f"ERROR: admin login {login!r} not found in users table",
                    file=sys.stderr,
                )
                return 1
            user_id = int(row[0])
            cur.execute(
                "UPDATE users SET pw = %s, change_time = %s, change_by = %s WHERE id = %s",
                (pw_hash, now, user_id, user_id),
            )
            cur.execute(
                "SELECT id FROM permission_groups WHERE name = %s LIMIT 1",
                ("admin",),
            )
            g_row = cur.fetchone()
            if not g_row:
                print(  # noqa: T201
                    "ERROR: permission_groups row name='admin' not found",
                    file=sys.stderr,
                )
                return 1
            group_id = int(g_row[0])
            cur.execute(
                "SELECT 1 FROM group_user "
                "WHERE user_id = %s AND group_id = %s AND permission_key = %s",
                (user_id, group_id, "rw"),
            )
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO group_user "
                    "(user_id, group_id, permission_key, create_by, create_time, "
                    "change_by, change_time) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (user_id, group_id, "rw", user_id, now, user_id, now),
                )
            print(f"Admin password set for login={login!r} (user id {user_id})")  # noqa: T201
            return 0
    finally:
        conn.close()


async def _run_seed(
    database_url: str,
    *,
    customers: int,
    tickets: int,
    seed: int | None,
    agent_user_id: int,
) -> int:
    from tiqora.db.engine import get_engine, get_session_factory
    from tiqora.domain.dev_seed import SeedError, seed_database

    engine = get_engine(_async_url(database_url))
    factory = get_session_factory(engine)
    try:
        result = await seed_database(
            factory,
            customers=customers,
            tickets=tickets,
            seed=seed,
            agent_user_id=agent_user_id,
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


def run_bootstrap(args: argparse.Namespace) -> int:
    """Entry point for ``tiqora bootstrap`` (sync; may open asyncio for --seed)."""
    settings = get_settings()
    database_url = args.database_url or settings.database_url
    if not database_url:
        print(  # noqa: T201
            "ERROR: no database URL (set --database-url or DATABASE_URL)",
            file=sys.stderr,
        )
        return 2

    try:
        dialect = detect_dialect(database_url)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)  # noqa: T201
        return 2

    print(f"Bootstrap target dialect={dialect}")  # noqa: T201

    # 1. Base schema (unless skipped or already populated)
    if args.skip_schema:
        print("Skipping base-schema load (--skip-schema)")  # noqa: T201
    elif _users_table_populated(database_url):
        print(  # noqa: T201
            "WARNING: database not empty (users table has rows); skipping base-schema load"
        )
    else:
        print("Loading Znuny base schema (schema → initial_insert → schema-post)…")  # noqa: T201
        load_base_schema(database_url, dialect=dialect)
        print("Base schema loaded.")  # noqa: T201

    # 2. Tiqora migrations
    _run_migrations(database_url)

    # 3. Admin password + admin group
    rc = _set_admin_password(database_url, args.admin_login, args.admin_password)
    if rc != 0:
        return rc

    # 4. Optional seed
    if args.seed:
        rc = asyncio.run(
            _run_seed(
                database_url,
                customers=args.customers,
                tickets=args.tickets,
                seed=args.seed_value,
                agent_user_id=1,
            )
        )
        if rc != 0:
            return rc

    print()  # noqa: T201
    print("Bootstrap complete.")  # noqa: T201
    print(f"  Login:    {args.admin_login}")  # noqa: T201
    print("  Password: (the --admin-password you provided)")  # noqa: T201
    print("  Open the Tiqora UI (default: http://localhost:8000/) and sign in.")  # noqa: T201
    return 0
