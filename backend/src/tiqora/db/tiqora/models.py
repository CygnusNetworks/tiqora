"""SQLAlchemy models for tiqora_* tables (Alembic-managed)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
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
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TiqoraSettings(TiqoraBase):
    """Key/value store for indexer watermarks and runtime flags."""

    __tablename__ = "tiqora_settings"

    key: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class TiqoraCacheInvalidation(TiqoraBase):
    """Cache invalidation queue consumed by the Znuny TiqoraSync addon.

    A row is either a ticket signal (``ticket_id`` set, ``cache_type`` NULL)
    or a Znuny CacheType cleanup signal (``cache_type`` set, ``ticket_id``
    NULL). The addon polls both.
    """

    __tablename__ = "tiqora_cache_invalidation"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
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


class TiqoraUserPasskey(TiqoraBase):
    """Per-agent WebAuthn passkey credential (alternative 2nd factor).

    One-to-many: an agent may register several authenticators. ``credential_id``
    is the base64url-encoded credential id (unique globally).
    """

    __tablename__ = "tiqora_user_passkey"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    credential_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transports: Mapped[str | None] = mapped_column(Text, nullable=True)
    aaguid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Passkey")
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TiqoraUserAuthConfig(TiqoraBase):
    """Per-agent auth policy: Kerberos SSO eligibility + 2FA enforcement.

    Soft-joins ``users.id`` (no FK) so parallel-operation stays additive
    ``tiqora_*`` only. Missing row means both flags default to false.
    """

    __tablename__ = "tiqora_user_auth_config"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    sso_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    enforce_2fa: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
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


class TiqoraMailOutbound(TiqoraBase):
    """Single-row outbound SMTP settings (admin UI + agent reply path).

    Mirrors Znuny SysConfig SendmailModule settings, stored in an additive
    ``tiqora_*`` table rather than env vars. ``auth_password`` is Fernet-
    encrypted at rest (see :mod:`tiqora.crypto.secret`); never log it.
    The logical singleton uses ``id = 1``.
    """

    __tablename__ = "tiqora_mail_outbound"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    # "none" | "starttls" | "ssl"
    security: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    # "none" | "password"
    auth_type: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    auth_user: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Fernet token; empty string means no password stored.
    auth_password: Mapped[str] = mapped_column(Text, nullable=False, default="")
    from_default: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    change_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    change_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TiqoraMailLog(TiqoraBase):
    """Inbound/outbound email communication log (Znuny Communication Log).

    One row per outbound agent-reply send attempt and per inbound
    fetch/process outcome. Logging is best-effort — write failures must
    never block mail processing or sending.
    """

    __tablename__ = "tiqora_mail_log"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    # "in" | "out"
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    # out: "queued" | "sent" | "failed"
    # in:  "received" | "filtered" | "failed"
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    from_addr: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    to_addr: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    cc_addr: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    queue: Mapped[str | None] = mapped_column(String(200), nullable=True)
    smtp_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_tiqora_mail_log_created_at", "created_at"),
        Index("ix_tiqora_mail_log_direction_status", "direction", "status"),
    )


class TiqoraQueueVariable(TiqoraBase):
    """Configurable per-queue (or global) placeholder variable.

    Resolved as ``<OTRS_QUEUE_{name}>`` / ``<TIQORA_QUEUE_{name}>``.
    ``queue_id IS NULL`` is a global default; a queue-specific row overrides
    the global for the same ``name``. Additive ``tiqora_*`` only — never
    alters Znuny ``queue`` columns.
    """

    __tablename__ = "tiqora_queue_variable"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    queue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    __table_args__ = (
        UniqueConstraint("queue_id", "name", name="uq_tiqora_queue_variable_queue_name"),
        Index("ix_tiqora_queue_variable_queue_id", "queue_id"),
    )


class TiqoraPlaceholderField(TiqoraBase):
    """Registry of customer_user / customer_company columns for the picker.

    Also drives the optional customer-field allow-list gate
    (``placeholder.customer_allowlist.enabled``). Additive ``tiqora_*`` only.
    """

    __tablename__ = "tiqora_placeholder_field"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    column_name: Mapped[str] = mapped_column(String(120), nullable=False)
    tag_name: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
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

    __table_args__ = (
        UniqueConstraint("source_table", "tag_name", name="uq_tiqora_placeholder_field_source_tag"),
    )


class TiqoraGdprJob(TiqoraBase):
    """One applied GDPR erasure job (anonymize or hard-delete) with backup window.

    ``selector`` / ``resolved_logins`` / ``counts`` are JSON text. Status is
    ``applied`` | ``rolled_back`` | ``purged``. Backups expire after 30 days
    (``backup_expires_at``); the daily purge worker deletes backup rows and
    flips status to ``purged``.
    """

    __tablename__ = "tiqora_gdpr_job"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    # "anonymize" | "delete"
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    # JSON of the ErasureSelector used for audit (may be empty if ids were explicit).
    selector: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON list of resolved customer_user.login values at apply time.
    resolved_logins: Mapped[str] = mapped_column(Text, nullable=False)
    # "applied" | "rolled_back" | "purged"
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # JSON counters, e.g. {"customer_user": 1, "article_data_mime": 4}
    counts: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    force_parallel: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    backup_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_tiqora_gdpr_job_status_expires", "status", "backup_expires_at"),)


class TiqoraGdprBackup(TiqoraBase):
    """Per-row snapshot taken before a GDPR erasure mutation.

    For ``mode=anonymize`` ``original_row`` holds only changed columns; for
    ``mode=delete`` it holds the full row so a hard-deleted master can be
    re-INSERTed on rollback. Binary values are base64-wrapped JSON objects.
    """

    __tablename__ = "tiqora_gdpr_backup"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # JSON object of primary-key column -> value.
    row_pk: Mapped[str] = mapped_column(Text, nullable=False)
    # JSON object of column -> original value (changed cols or full row).
    original_row: Mapped[str] = mapped_column(Text, nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_tiqora_gdpr_backup_job_id", "job_id"),
        Index("ix_tiqora_gdpr_backup_created", "created"),
    )
