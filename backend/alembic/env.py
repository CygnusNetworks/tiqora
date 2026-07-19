"""Alembic environment — migrates tiqora_* schema; never Znuny tables by default."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from tiqora.config import get_settings
from tiqora.db.engine import _normalize_url, get_engine
from tiqora.db.tiqora import tiqora_metadata

target_metadata = tiqora_metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", _normalize_url(settings.database_url))


async def _db_ownership_marker_set() -> bool:
    """Check the ``tiqora_settings`` DB marker (second half of the gate).

    Best-effort: if ``tiqora_settings`` does not exist yet (fresh install,
    ``versions_tiqora`` not yet applied), treat the marker as unset rather
    than failing the whole migration run.
    """
    from sqlalchemy import select

    from tiqora.db.tiqora.models import TiqoraSettings
    from tiqora.domain.ownership import KEY_OWNERSHIP_ENABLED, VALUE_ENABLED

    engine = get_engine(settings.database_url)
    try:
        async with engine.connect() as conn:

            def _query(sync_conn: object) -> str | None:
                from sqlalchemy.orm import Session

                with Session(bind=sync_conn) as sess:  # type: ignore[arg-type]
                    return sess.execute(
                        select(TiqoraSettings.value).where(
                            TiqoraSettings.key == KEY_OWNERSHIP_ENABLED
                        )
                    ).scalar_one_or_none()

            value = await conn.run_sync(_query)
    except Exception:  # noqa: BLE001 — table missing / DB unreachable at this stage
        return False
    return value == VALUE_ENABLED


def _script_locations() -> list[str]:
    """Return the active version-location list for this run.

    Gate: ``versions_owned`` is only appended when **both**
    ``TIQORA_SCHEMA_OWNERSHIP=1`` (env/config) *and* the ``tiqora_settings``
    DB marker ``schema.ownership=enabled`` are present (see
    ``tiqora.domain.ownership``). Otherwise the owned migrations stay
    entirely invisible to Alembic — not just unapplied.
    """
    base = ["alembic/versions_tiqora"]
    if not settings.schema_ownership:
        return base
    if not asyncio.run(_db_ownership_marker_set()):
        return base
    return [*base, "alembic/versions_owned"]


config.set_main_option("version_locations", os.pathsep.join(_script_locations()))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=_include_name,
        version_table="tiqora_alembic_version",
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_name=_include_name,
        version_table="tiqora_alembic_version",
    )

    with context.begin_transaction():
        context.run_migrations()


def _include_name(name: str | None, type_: str, parent_names: dict) -> bool:  # type: ignore[type-arg]
    """Only touch tiqora_* objects; never alter Znuny tables."""
    if type_ == "table" and name is not None:
        return name.startswith("tiqora_") or name == "tiqora_alembic_version"
    return True


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
