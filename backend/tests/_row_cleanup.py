"""Delete the rows a test module committed, so it also passes on its own.

``conftest._restore_db_between_modules`` repairs a leaking module, but under
``TIQORA_STRICT_DB_LEAKS=1`` (CI) a module that is not on
``db_leak_baseline.txt`` fails for leaking. Rather than growing that list,
new modules snapshot the highest id of every table they touch and delete
what appeared above it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from sqlalchemy import create_engine, text

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
    """Highest existing id per table, before the module writes anything."""
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            return {
                table: int(
                    conn.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table}")).scalar_one()
                )
                for table in tables
            }
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
                if table in snapshot:
                    conn.execute(text(f"DELETE FROM {table} WHERE id > :m"), {"m": snapshot[table]})
            for key in setting_keys:
                conn.execute(text("DELETE FROM tiqora_settings WHERE `key` = :k"), {"k": key})
    finally:
        engine.dispose()


def cleanup_module(
    sync_url: str, *, setting_keys: Sequence[str] = (), tables: Sequence[str] = DEFAULT_TABLES
) -> Iterator[None]:
    """Fixture body: snapshot before the module runs, delete after."""
    snapshot = snapshot_max_ids(sync_url, tables)
    yield
    delete_rows_above(sync_url, snapshot, setting_keys=setting_keys)
