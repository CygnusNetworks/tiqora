"""Load Znuny base schema SQL (installer order) into MySQL/MariaDB or PostgreSQL.

Statement splitting handles Znuny seed quirks (embedded ``;``, MySQL ``\\;``,
``--`` inside string literals). Loaders are idempotent on re-run for common
"already exists" errors.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

Dialect = Literal["mysql", "postgresql"]

# Installer order is mandatory (circular FKs users↔valid).
_SCHEMA_FILES: dict[Dialect, tuple[str, str, str]] = {
    "mysql": (
        "schema.mysql.sql",
        "initial_insert.mysql.sql",
        "schema-post.mysql.sql",
    ),
    "postgresql": (
        "schema.postgresql.sql",
        "initial_insert.postgresql.sql",
        "schema-post.postgresql.sql",
    ),
}


def schema_dir() -> Path:
    """Return the filesystem path to the shipped Znuny base-schema SQL files."""
    # Prefer package data under tiqora.bootstrap (works for editable + wheel).
    # schema/ is data-only (no __init__.py); locate via the parent package.
    root = resources.files("tiqora.bootstrap").joinpath("schema")
    return Path(str(root))


def detect_dialect(database_url: str) -> Dialect:
    """Infer SQL dialect from a SQLAlchemy (or plain) database URL."""
    lower = database_url.lower()
    if lower.startswith(("mysql", "mariadb")):
        return "mysql"
    if lower.startswith(("postgresql", "postgres")):
        return "postgresql"
    raise ValueError(
        f"Unsupported database URL dialect for bootstrap (need mysql/mariadb "
        f"or postgresql): {database_url!r}"
    )


def split_sql_statements(sql: str, *, mysql_backslash_escapes: bool = False) -> list[str]:
    """Split SQL on semicolons outside quotes / dollar-quotes.

    Znuny seed data embeds multi-line string literals that contain both
    semicolons (``text/plain; charset=utf-8``), MySQL ``\\;`` URL escapes, and
    ``--`` comment-like lines (signature ASCII art). Naive split/comment-stripping
    corrupts those inserts.

    Parameters
    ----------
    mysql_backslash_escapes:
        When True, treat ``\\X`` inside single-quoted strings as an escaped pair
        (MySQL/MariaDB default). PostgreSQL with standard_conforming_strings keeps
        backslash as a literal, so leave this False for PG.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False
    in_dollar = False
    dollar_tag = ""
    while i < n:
        if not in_single and not in_double and not in_dollar:
            m = re.match(r"\$([A-Za-z0-9_]*)\$", sql[i:])
            if m:
                in_dollar = True
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if in_dollar:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                in_dollar = False
                dollar_tag = ""
                continue
            buf.append(sql[i])
            i += 1
            continue
        ch = sql[i]
        # MySQL backslash escapes inside single-quoted strings (\' \; \\ …)
        if in_single and mysql_backslash_escapes and ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(sql[i + 1])
            i += 2
            continue
        if not in_double and ch == "'":
            # SQL standard escaped quote: ''
            if in_single and i + 1 < n and sql[i + 1] == "'":
                buf.append("''")
                i += 2
                continue
            in_single = not in_single
            buf.append(ch)
            i += 1
            continue
        if not in_single and ch == '"':
            in_double = not in_double
            buf.append(ch)
            i += 1
            continue
        if not in_single and not in_double and ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def load_sql_mysql(container_url: str, sql_path: Path) -> None:
    """Execute a SQL file against a MySQL/MariaDB database."""
    import pymysql
    from pymysql.constants import CLIENT

    url = container_url
    for prefix in ("mysql+pymysql://", "mysql+aiomysql://", "mariadb+pymysql://"):
        if url.startswith(prefix):
            url = "mysql://" + url[len(prefix) :]
            break
    parsed = urlparse(url)
    conn = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=parsed.username or "root",
        password=parsed.password or "",
        database=(parsed.path or "/test").lstrip("/") or "test",
        autocommit=True,
        charset="utf8mb4",
        client_flag=CLIENT.MULTI_STATEMENTS,
    )
    try:
        # Do not strip ``--`` lines: Znuny seed strings contain signature art with ``--``.
        # MySQL/MariaDB understand ``#`` / ``--`` comments outside of string literals.
        sql = sql_path.read_text(encoding="utf-8", errors="replace")
        with conn.cursor() as cur:
            for stmt in split_sql_statements(sql, mysql_backslash_escapes=True):
                if not stmt.strip():
                    continue
                try:
                    cur.execute(stmt)
                    while cur.nextset():
                        pass
                except pymysql.err.Error as exc:
                    # Ignore "already exists" / "unknown key" style noise on re-runs
                    if getattr(exc, "args", None) and exc.args[0] in {1050, 1061, 1091}:
                        continue
                    raise
    finally:
        conn.close()


def load_sql_postgres(dsn: str, sql_path: Path) -> None:
    """Execute a SQL file against a PostgreSQL database."""
    import psycopg2

    url = dsn
    for prefix in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix) :]
            break
    conn = psycopg2.connect(url)
    conn.autocommit = True
    try:
        sql = sql_path.read_text(encoding="utf-8", errors="replace")
        with conn.cursor() as cur:
            for stmt in split_sql_statements(sql, mysql_backslash_escapes=False):
                if not stmt.strip():
                    continue
                try:
                    cur.execute(stmt)
                except psycopg2.Error as exc:
                    # duplicate_table / duplicate_object
                    if exc.pgcode in {"42P07", "42710"}:
                        conn.rollback()
                        continue
                    raise
    finally:
        conn.close()


def load_base_schema(database_url: str, *, dialect: Dialect | None = None) -> None:
    """Load Znuny base schema in installer order for *database_url*.

    Order: ``schema`` → ``initial_insert`` → ``schema-post``. Idempotent for
    common re-run errors (duplicate table/object).
    """
    dia = dialect or detect_dialect(database_url)
    root = schema_dir()
    names = _SCHEMA_FILES[dia]
    loader = load_sql_mysql if dia == "mysql" else load_sql_postgres
    for name in names:
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"Missing base schema file: {path}")
        loader(database_url, path)
