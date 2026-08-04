"""Znuny ``oauth2_token_config`` / ``oauth2_token`` tables (mail OAuth, 6.3+).

These are legacy tables shared with Znuny in parallel operation — never managed
by Alembic. Config JSON matches Znuny's ``ContentJSON`` shape
(``Kernel::System::OAuth2TokenConfig``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.types import LegacyDateTime as DateTime


class OAuth2TokenConfig(LegacyBase):
    """Znuny table ``oauth2_token_config``.

    ``dbcrud_uuid`` exists on Znuny 6.5+ only and is intentionally unmapped so
    the same ORM works against 6.3 fixtures/installs (column absent there).
    """

    __tablename__ = "oauth2_token_config"
    __table_args__ = (UniqueConstraint("name", name="oauth2_token_config_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    # JSON blob (Znuny ContentJSON); MEDIUMTEXT on MySQL, TEXT on PG.
    config: Mapped[str] = mapped_column(Text, nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class OAuth2Token(LegacyBase):
    """Znuny table ``oauth2_token`` — one row per token config (UNIQUE).

    ``dbcrud_uuid`` (6.5+) is unmapped — see :class:`OAuth2TokenConfig`.
    """

    __tablename__ = "oauth2_token"
    __table_args__ = (UniqueConstraint("token_config_id", name="oauth2_token_config_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    token_config_id: Mapped[int] = mapped_column(Integer, nullable=False)
    authorization_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expiration_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_expiration_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
