"""Legacy Znuny schema metadata — never managed by Alembic."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

legacy_metadata = MetaData()


class LegacyBase(DeclarativeBase):
    """Declarative base for Znuny 6.5 tables (parallel-operation compatible)."""

    metadata = legacy_metadata
