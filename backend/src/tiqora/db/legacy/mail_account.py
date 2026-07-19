from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase


class MailAccount(LegacyBase):
    """Znuny table `mail_account`.

    ``pw`` is stored **plaintext** by Znuny (``Kernel::System::MailAccount``
    inserts/reads it verbatim — no XOR/base64 obfuscation, unlike some other
    OTRS-family credential columns). Tiqora reads it the same way.
    """

    __tablename__ = "mail_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    login: Mapped[str] = mapped_column(String(200), nullable=False)
    pw: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(200), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    trusted: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    imap_folder: Mapped[str | None] = mapped_column(String(250), nullable=True)
    authentication_type: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default="password"
    )
    oauth2_token_config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
