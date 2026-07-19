"""Declarative base for tiqora_* tables managed by Alembic."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

tiqora_metadata = MetaData()


class TiqoraBase(DeclarativeBase):
    """Base for Tiqora-owned tables (never Znuny schema)."""

    metadata = tiqora_metadata
