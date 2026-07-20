from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.types import LegacyDateTime as DateTime


class Article(LegacyBase):
    """Znuny table `article`."""

    __tablename__ = "article"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    article_sender_type_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    communication_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_visible_for_customer: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    search_index_needs_rebuild: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1"
    )
    insert_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class ArticleFlag(LegacyBase):
    """Znuny table `article_flag`."""

    __tablename__ = "article_flag"

    article_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    article_key: Mapped[str] = mapped_column(String(50), primary_key=True, nullable=False)
    article_value: Mapped[str | None] = mapped_column(String(50), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)


class ArticleSenderType(LegacyBase):
    """Znuny table `article_sender_type`."""

    __tablename__ = "article_sender_type"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class ArticleDataMime(LegacyBase):
    """Znuny table `article_data_mime`."""

    __tablename__ = "article_data_mime"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    article_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    a_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_cc: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_bcc: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_message_id_md5: Mapped[str | None] = mapped_column(String(32), nullable=True)
    a_in_reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_references: Mapped[str | None] = mapped_column(Text, nullable=True)
    a_content_type: Mapped[str | None] = mapped_column(String(250), nullable=True)
    a_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    incoming_time: Mapped[int] = mapped_column(Integer, nullable=False)
    content_path: Mapped[str | None] = mapped_column(String(250), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class ArticleDataMimePlain(LegacyBase):
    """Znuny table `article_data_mime_plain`."""

    __tablename__ = "article_data_mime_plain"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    article_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class ArticleDataMimeAttachment(LegacyBase):
    """Znuny table `article_data_mime_attachment`."""

    __tablename__ = "article_data_mime_attachment"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    article_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(250), nullable=True)
    content_size: Mapped[str | None] = mapped_column(String(30), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(450), nullable=True)
    content_id: Mapped[str | None] = mapped_column(String(250), nullable=True)
    content_alternative: Mapped[str | None] = mapped_column(String(50), nullable=True)
    disposition: Mapped[str | None] = mapped_column(String(15), nullable=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class ArticleSearchIndex(LegacyBase):
    """Znuny table `article_search_index`."""

    __tablename__ = "article_search_index"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    article_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    article_key: Mapped[str] = mapped_column(String(200), nullable=False)
    article_value: Mapped[str | None] = mapped_column(Text, nullable=True)


class CommunicationChannel(LegacyBase):
    """Znuny table `communication_channel`."""

    __tablename__ = "communication_channel"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    module: Mapped[str] = mapped_column(String(200), nullable=False)
    package_name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
