"""Znuny S/MIME index tables (``smime_keys``, ``smime_signer_cert_relations``).

Written by Znuny's ``Kernel::System::Crypt::SMIME`` alongside the files in
``SMIME::CertPath``/``SMIME::PrivatePath``; Tiqora keeps them in sync so a
shared key store looks identical from both sides. ``smime_keys`` exists from
Znuny 6.4 on (optional on older schemas, see ``OPTIONAL_LEGACY_TABLES``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.legacy.base import LegacyBase
from tiqora.db.legacy.types import LegacyDateTime as DateTime


class SmimeKey(LegacyBase):
    """Znuny table `smime_keys` — one row per certificate (``cert``) / private key (``P``)."""

    __tablename__ = "smime_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(8), nullable=False)
    key_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    expiration_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(59), nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    create_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    change_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    create_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SmimeSignerCertRelation(LegacyBase):
    """Znuny table `smime_signer_cert_relations` — CA certs attached to a signer's signatures."""

    __tablename__ = "smime_signer_cert_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    cert_hash: Mapped[str] = mapped_column(String(8), nullable=False)
    cert_fingerprint: Mapped[str] = mapped_column(String(59), nullable=False)
    ca_hash: Mapped[str] = mapped_column(String(8), nullable=False)
    ca_fingerprint: Mapped[str] = mapped_column(String(59), nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
