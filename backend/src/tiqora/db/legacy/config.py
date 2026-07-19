from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase


class Acl(LegacyBase):
    """Znuny table `acl`."""

    __tablename__ = "acl"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    comments: Mapped[str | None] = mapped_column(String(250), nullable=True)
    description: Mapped[str | None] = mapped_column(String(250), nullable=True)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    stop_after_match: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    config_match: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    config_change: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class Valid(LegacyBase):
    """Znuny table `valid`."""

    __tablename__ = "valid"

    id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class Sessions(LegacyBase):
    """Znuny table `sessions`."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    data_key: Mapped[str] = mapped_column(String(100), nullable=False)
    data_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    serialized: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class MailQueue(LegacyBase):
    """Znuny table `mail_queue`."""

    __tablename__ = "mail_queue"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    insert_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    sender: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recipient: Mapped[str] = mapped_column(Text, nullable=False)
    raw_message: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    due_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_smtp_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_smtp_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PostmasterFilter(LegacyBase):
    """Znuny table `postmaster_filter`."""

    __tablename__ = "postmaster_filter"

    f_name: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    f_stop: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    f_type: Mapped[str] = mapped_column(String(20), primary_key=True, nullable=False)
    f_key: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    f_value: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    f_not: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class GiWebserviceConfig(LegacyBase):
    """Znuny table `gi_webservice_config`."""

    __tablename__ = "gi_webservice_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    valid_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class GenericAgentJobs(LegacyBase):
    """Znuny table `generic_agent_jobs`."""

    __tablename__ = "generic_agent_jobs"

    job_name: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    job_key: Mapped[str] = mapped_column(String(255), primary_key=True, nullable=False)
    job_value: Mapped[str | None] = mapped_column(Text, nullable=True)


class SysconfigDefault(LegacyBase):
    """Znuny table `sysconfig_default`."""

    __tablename__ = "sysconfig_default"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    navigation: Mapped[str] = mapped_column(String(200), nullable=False)
    is_invisible: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_readonly: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_required: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_valid: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    has_configlevel: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_modification_possible: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_modification_active: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_preferences_group: Mapped[str | None] = mapped_column(String(250), nullable=True)
    xml_content_raw: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    xml_content_parsed: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    xml_filename: Mapped[str] = mapped_column(String(250), nullable=False)
    effective_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_dirty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    exclusive_lock_guid: Mapped[str] = mapped_column(String(32), nullable=False)
    exclusive_lock_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exclusive_lock_expiry_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)


class SysconfigModified(LegacyBase):
    """Znuny table `sysconfig_modified`."""

    __tablename__ = "sysconfig_modified"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    sysconfig_default_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_valid: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_modification_active: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    effective_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_dirty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    reset_to_default: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
