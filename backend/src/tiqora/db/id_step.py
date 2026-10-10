"""Auto-increment lattice of the database connection (MariaDB/MySQL Galera multi-writer setups)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def autoincrement_step(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    """``(auto_increment_increment, offset)`` of the MariaDB/MySQL connection, else ``(1, 1)``."""
    async with factory() as session:
        if session.get_bind().dialect.name not in ("mysql", "mariadb"):
            return 1, 1
        row = (
            await session.execute(
                text("SELECT @@auto_increment_increment, @@auto_increment_offset")
            )
        ).one()
    return max(int(row[0]), 1), int(row[1])
