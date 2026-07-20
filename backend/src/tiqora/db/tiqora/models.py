"""SQLAlchemy models for tiqora_* tables (Alembic-managed)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from tiqora.db.tiqora.base import TiqoraBase


class TiqoraApiKey(TiqoraBase):
    """API key for bearer authentication (``Authorization: Bearer``)."""

    __tablename__ = "tiqora_api_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class TiqoraSettings(TiqoraBase):
    """Key/value store for indexer watermarks and runtime flags."""

    __tablename__ = "tiqora_settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class TiqoraCacheInvalidation(TiqoraBase):
    """Ticket cache invalidation queue consumed by the Znuny TiqoraSync addon."""

    __tablename__ = "tiqora_cache_invalidation"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_tiqora_cache_inv_id", "id"),)


class TiqoraEventOutbox(TiqoraBase):
    """Transactional outbox for ticket/article events.

    Written in the same transaction as all write operations; drained by the
    taskiq worker which re-indexes affected tickets in Meilisearch and may
    fan out to webhooks in Phase 3.

    Event names match Znuny-style event identifiers (TicketCreate,
    ArticleCreate, TicketStateUpdate, TicketQueueUpdate, …) so that the
    event log is directly comparable against Znuny's event history for
    golden-master validation.
    """

    __tablename__ = "tiqora_event_outbox"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    processed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    __table_args__ = (Index("ix_tiqora_event_outbox_processed", "processed", "id"),)


class TiqoraFormDraft(TiqoraBase):
    """Tiqora-owned form draft storage (JSON content).

    We intentionally DO NOT write to Znuny's ``form_draft`` table because:
    1. Znuny's ``form_draft.content`` is stored as Perl Storable binary blobs
       (``Storable::freeze``), which we cannot read or write from Python.
    2. Writing invalid Storable data would corrupt Znuny's draft UI.
    3. After cutover (Phase 5) we own the table; until then we keep draft
       data in this separate table and surface it only via the Tiqora API.
    """

    __tablename__ = "tiqora_form_draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Free-form action name (e.g. "AgentTicketNote", "AgentTicketCompose")
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # JSON-encoded draft content (subject, body, to, cc, …)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    changed: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_tiqora_form_draft_ticket_user", "ticket_id", "user_id"),)


class TiqoraUserTotp(TiqoraBase):
    """Per-agent TOTP 2FA enrollment (Phase 3c).

    ``secret`` is stored Fernet-encrypted (using ``settings.secret_key``),
    never in plaintext.
    """

    __tablename__ = "tiqora_user_totp"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class TiqoraCryptoKey(TiqoraBase):
    """Bookkeeping record for an imported PGP/S/MIME key (Phase 2c).

    Not the key material itself: PGP private/public key material lives in
    the gpg keyring at ``TIQORA_CRYPTO_GNUPG_HOME``; S/MIME cert/key material
    lives as files under the configured cert/private directories (see
    ``tiqora.crypto.keystore``). This table only records *who imported what,
    when* — an audit trail for `tiqora crypto pgp-import` /
    `tiqora crypto smime-register`.
    """

    __tablename__ = "tiqora_crypto_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    # "pgp" | "smime"
    key_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # PGP: fingerprint. S/MIME: the email the cert/key pair is filed under.
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "sign" | "encrypt" | "both"
    purpose: Mapped[str] = mapped_column(String(20), nullable=False, default="both")
    has_private_key: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_tiqora_crypto_key_type_identifier", "key_type", "identifier"),)


class TiqoraGdprAudit(TiqoraBase):
    """Audit trail for GDPR anonymization/retention runs (Phase 2c).

    One row per run of ``tiqora gdpr anonymize-customer`` /
    ``tiqora gdpr retention-run`` (and the retention taskiq worker task).
    Never stores the anonymized values themselves — only who/what/when and
    row counts, so the audit log itself carries no PII.
    """

    __tablename__ = "tiqora_gdpr_audit"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    # "anonymize_customer" | "retention_run" | "retention_dry_run"
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    # Login/customer_id for anonymize_customer; rule description for retention.
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    # "cli" | "worker" | an operator identifier passed through by the caller.
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    # JSON-encoded counters, e.g. {"customer_user": 1, "article_data_mime": 4}
    counts: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    force_parallel: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_tiqora_gdpr_audit_action_created", "action", "created"),)


class TiqoraWebhook(TiqoraBase):
    """Admin-configured outbound webhook subscription (Phase 3c).

    ``events`` is a JSON-encoded array of event-type strings (matching
    ``tiqora_event_outbox.event_type``); an empty/``["*"]`` list means "all
    events". Deliveries are HMAC-SHA256 signed with ``secret``.
    """

    __tablename__ = "tiqora_webhook"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    events: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    changed: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
