"""Delete the rows a test module committed, so it also passes on its own.

``conftest._restore_db_between_modules`` repairs a leaking module, but under
``TIQORA_STRICT_DB_LEAKS=1`` (CI) a module that is not on
``db_leak_baseline.txt`` fails for leaking. Rather than growing that list,
new modules snapshot the highest id of every table they touch and delete
what appeared above it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from sqlalchemy import create_engine, delete, text
from sqlalchemy.exc import DatabaseError

from tiqora.db.tiqora.models import TiqoraSettings

# Child rows first: ticket_history and the article payload tables reference
# ticket/article, so the order here is the delete order.
DEFAULT_TABLES: tuple[str, ...] = (
    "tiqora_mail_log",
    "tiqora_event_outbox",
    "tiqora_cache_invalidation",
    "ticket_history",
    "article_data_mime_attachment",
    "article_data_mime_plain",
    "article_data_mime",
    "article",
    "ticket",
    "ticket_number_counter",
    "sla",
    "queue",
    "sysconfig_default",
)


def snapshot_max_ids(sync_url: str, tables: Sequence[str] = DEFAULT_TABLES) -> dict[str, int]:
    """Highest existing id per table, before the module writes anything.

    Tables the module creates itself (the additive ``tiqora_*`` set) may not
    exist yet at module-setup time — a module that runs first in a fresh
    container is exactly that case. Those are skipped: anything the module
    goes on to create it also owns entirely, so the restore has nothing older
    to preserve.
    """
    engine = create_engine(sync_url)
    snapshot: dict[str, int] = {}
    try:
        for table in tables:
            try:
                with engine.begin() as conn:
                    snapshot[table] = int(
                        conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table}")).scalar_one()
                    )
            except DatabaseError:
                continue
        return snapshot
    finally:
        engine.dispose()


def delete_rows_above(
    sync_url: str, snapshot: dict[str, int], *, setting_keys: Sequence[str] = ()
) -> None:
    """Delete every row the module added, plus the named settings keys."""
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            for table in DEFAULT_TABLES:
                if table not in snapshot:
                    continue
                try:
                    conn.execute(text(f"DELETE FROM {table} WHERE id > :m"), {"m": snapshot[table]})
                except DatabaseError:
                    continue
            if setting_keys:
                # Via the model, not raw SQL: ``key`` is reserved in MySQL and
                # would need backticks that PostgreSQL rejects.
                conn.execute(delete(TiqoraSettings).where(TiqoraSettings.key.in_(setting_keys)))
    finally:
        engine.dispose()


def cleanup_module(
    sync_url: str, *, setting_keys: Sequence[str] = (), tables: Sequence[str] = DEFAULT_TABLES
) -> Iterator[None]:
    """Fixture body: snapshot before the module runs, delete after."""
    snapshot = snapshot_max_ids(sync_url, tables)
    yield
    delete_rows_above(sync_url, snapshot, setting_keys=setting_keys)
