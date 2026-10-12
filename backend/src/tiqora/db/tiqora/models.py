"""SQLAlchemy models for tiqora_* tables (Alembic-managed)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, deferred, mapped_column

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
    # Comma-separated scope tokens: legacy read/write/mcp/* plus area RO/RW
    # (e.g. tickets:ro,kb:rw). NULL/empty = unrestricted (full agent privileges
    # of the linked user). See tiqora.domain.api_key_scopes.
    scopes: Mapped[str | None] = mapped_column(String(500), nullable=True)


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
    worker's ``outbox`` loop, which re-indexes affected tickets in
    Meilisearch and fans out to webhooks/SSE.

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

    Unlike Znuny we key drafts by ``article_id`` as well, because reply
    drafts autosave per article: one agent can have unsent replies open on
    several articles of the same ticket. Znuny instead tells its drafts
    apart by a user-supplied ``title`` — there they are explicit, manually
    named drafts, not an autosave.
    """

    __tablename__ = "tiqora_form_draft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Free-form action name (e.g. "AgentTicketNote", "AgentTicketCompose")
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    # Article the draft replies to. NULL = ticket-wide draft; NULLs count as
    # distinct in the unique constraint below, so any number of those may
    # coexist (the slot for Znuny-style named drafts).
    article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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

    __table_args__ = (
        Index("ix_tiqora_form_draft_ticket_user", "ticket_id", "user_id"),
        UniqueConstraint(
            "ticket_id",
            "user_id",
            "action",
            "article_id",
            name="uq_tiqora_form_draft_ticket_user_action_article",
        ),
    )


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


class TiqoraPasswordSetupToken(TiqoraBase):
    """One-time, expiring link that lets a new agent choose their own password.

    Replaces mailing a generated password in plaintext: the mail carries a
    token, the account is created with an unusable random hash, and the
    password only ever exists in the browser of the person setting it.

    Only the SHA-256 of the token is stored, so a database leak does not
    hand over working links. ``used`` is a timestamp rather than a flag to
    keep a minimal audit trail; rows are not deleted on redemption.

    ``used`` means *redeemed by the agent* and nothing else — a link the admin
    replaced is invalidated by setting ``expires`` to now, so the admin user
    list can report "invitation accepted" straight from this column.
    """

    __tablename__ = "tiqora_password_setup_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
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

    # autoincrement=False: this mirrors ``users.id`` and must never invent one.
    # SQLAlchemy defaults a single-column integer PK to ``autoincrement="auto"``,
    # which emits AUTO_INCREMENT on MariaDB and SERIAL on PostgreSQL — migration
    # 20260721_0014 creates a plain integer, so only metadata-built databases
    # ever diverged.
    user_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False, nullable=False
    )
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
    """Audit trail of PGP/S-MIME key-store mutations (never key material).

    PGP keys live in the gpg keyring, S/MIME certificates/keys in Znuny's
    ``SMIME::CertPath``/``SMIME::PrivatePath`` layout (see
    :mod:`tiqora.crypto.smime_store`). One row per mutation from the admin
    API or the ``tiqora crypto`` CLI: *who* did *what* to *which* key, when.
    """

    __tablename__ = "tiqora_crypto_key"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    # "pgp" | "smime"
    key_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # PGP: fingerprint. S/MIME: the store filename (<hash>.<n>).
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
    # import | delete | delete_secret | add_certificate | add_private |
    # delete_private | relation_add | relation_delete | migrate | rehash |
    # passphrase_set | passphrase_delete
    action: Mapped[str] = mapped_column(
        String(32), nullable=False, default="import", server_default="import"
    )
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (Index("ix_tiqora_crypto_key_type_identifier", "key_type", "identifier"),)


class TiqoraGdprAudit(TiqoraBase):
    """Audit trail for GDPR anonymization/retention runs (Phase 2c).

    One row per run of ``tiqora gdpr anonymize-customer`` /
    ``tiqora gdpr retention-run`` (and the worker's ``gdpr_retention`` daily
    loop). Never stores the anonymized values themselves — only who/what/when
    and row counts, so the audit log itself carries no PII.
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
    # "none" | "password" | "oauth2_token"
    auth_type: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    auth_user: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Fernet token; empty string means no password stored.
    auth_password: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Znuny SendmailModule::OAuth2TokenConfigName — name of oauth2_token_config.
    oauth2_token_config_name: Mapped[str] = mapped_column(String(250), nullable=False, default="")
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


class TiqoraStandardTemplateGroup(TiqoraBase):
    """Many-to-many standard_template <-> permission_group edit-ACL.

    A row grants edit rights on the template to members holding ``rw`` on the
    group. A template with **no** rows here (and no user grants) stays
    admin-only. Soft-joins ``standard_template.id`` / ``permission_groups.id``
    (no FK — parallel operation stays additive ``tiqora_*`` only).
    """

    __tablename__ = "tiqora_standard_template_group"

    standard_template_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    permission_group_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)

    __table_args__ = (Index("ix_tiqora_standard_template_group_group", "permission_group_id"),)


class TiqoraStandardTemplateUser(TiqoraBase):
    """Many-to-many standard_template <-> user edit-ACL (individual grants).

    A row grants edit rights on the template to that specific agent. Soft-joins
    ``standard_template.id`` / ``users.id`` (no FK).
    """

    __tablename__ = "tiqora_standard_template_user"

    standard_template_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)

    __table_args__ = (Index("ix_tiqora_standard_template_user_user", "user_id"),)


class TiqoraTelegramContact(TiqoraBase):
    """A Telegram chat, resolved (best-effort) to a customer identity.

    ``chat_id``/``telegram_user_id`` never change for a given Telegram user,
    but ``customer_user_login`` can — re-linking, portal account merges — so
    the resolved identity is cached here rather than looked up fresh on
    every inbound message.

    ``consent_time``/``consent_prompt_time`` back the DSGVO consent flow
    (Task 13): only chat_id + identity fields are ever stored before
    ``consent_time`` is set — no ticket/article is created for a contact
    until they accept the inline consent prompt.
    """

    __tablename__ = "tiqora_telegram_contact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_user_login: Mapped[str | None] = mapped_column(String(200), nullable=True)
    consent_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consent_prompt_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Telegram ``from.language_code`` (client UI language, e.g. "de", "en-US")
    # — fallback for the language of the bot's own texts (channels.telegram.texts).
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Set by ``/start`` (Task: Telegram-Chat-UX); marks the beginning of a
    # fresh dialog so ``_resolve_ticket`` stops reusing older per-chat
    # tickets that predate it. Explicitly set in code — deliberately no
    # ``server_default`` (see 20260814_0035, and the 0032/0034 lesson this
    # mirrors: ORM and migration must declare the identical default, and
    # "none at all" is the simplest way to keep that true).
    new_dialog_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    change_time: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (Index("ix_tiqora_telegram_contact_customer_login", "customer_user_login"),)


class TiqoraTelegramMessage(TiqoraBase):
    """Maps one Tiqora article to the Telegram message(s) it was sent/received as.

    One row per article, keyed by ``article_id`` (no FK, like
    ``TiqoraAiArticleOrigin`` — Znuny's ``article`` table lives outside
    ``tiqora_metadata``). ``direction`` is ``"out"`` for an agent-composed
    message sent to the customer's chat, ``"in"`` for a customer message
    stored as an article. ``chat_id``/``message_id`` identify the Telegram
    message so inbound button callbacks and edit/retract actions (later
    tasks) can look the row up either by article or by
    ``(chat_id, message_id)``.

    ``extra_message_ids`` holds a JSON list of message ids for additional
    attachment messages sent alongside the primary text message (bot uploads
    are one Telegram message per file). ``buttons_json`` records the inline
    keyboard actually sent (if any); ``answered_button``/``answered_at`` are
    set once a customer taps one. ``original_body`` preserves the
    pre-edit text when an agent edits a sent message (Task 6).
    """

    __tablename__ = "tiqora_telegram_message"

    article_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    extra_message_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_to_article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    buttons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered_button: Mapped[int | None] = mapped_column(Integer, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    original_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retracted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_tiqora_telegram_message_ticket_id", "ticket_id"),
        Index("ux_tiqora_telegram_message_chat_msg", "chat_id", "message_id", unique=True),
    )


class TiqoraQueueCustomerLink(TiqoraBase):
    """Per-queue external customer-management-tool link (ticket zoom).

    One row per queue configures the second header button next to the
    existing internal "Kunde" link. ``admin_url_template`` is optional —
    when set, agents with admin rights see it instead of ``url_template``;
    everyone else always sees ``url_template``. ``visibility="admins"``
    hides the button entirely for non-admins (no fallback to the normal
    template). Resolution (placeholder substitution) happens server-side in
    ``tiqora.domain.customer_link``. No FK on ``queue_id`` — same convention
    as ``tiqora_ai_queue_policy`` (Znuny's ``queue`` table lives outside
    ``tiqora_metadata``, validated at the application layer instead).
    """

    __tablename__ = "tiqora_queue_customer_link"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    url_template: Mapped[str] = mapped_column(String(1024), nullable=False)
    admin_url_template: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="all", server_default="all"
    )
    # {customer_user} strip rule: NULL/empty = login verbatim; e.g. "#" cuts
    # the login at its first "#" (site-specific contract disambiguator).
    login_suffix_separator: Mapped[str | None] = mapped_column(String(8), nullable=True)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class TiqoraFeatureGrant(TiqoraBase):
    """Who may use an agent feature that is not tied to a queue.

    One row grants *feature* (e.g. ``customer_directory``) to a single agent
    (``subject_type = "user"``), every member of a permission group
    (``"group"``) or every agent holding a role (``"role"``). Admins always
    have every feature; a feature with no rows is admin-only. Soft joins to
    ``users.id`` / ``permission_groups.id`` / ``roles.id`` (no FK — parallel
    operation stays additive ``tiqora_*`` only).
    """

    __tablename__ = "tiqora_feature_grant"

    feature: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), primary_key=True, nullable=False)
    subject_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)


class TiqoraCustomerFavorite(TiqoraBase):
    """A customer user an agent starred on the "Kunden" page.

    Personal: one row per agent and customer user, listed above the agent's
    recent and frequent customers. Soft joins to ``users.id`` and
    ``customer_user.login`` (no FK); GDPR erasure renames or drops the rows
    with the customer user.
    """

    __tablename__ = "tiqora_customer_favorite"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    customer_login: Mapped[str] = mapped_column(String(200), primary_key=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


#: Normalized avatar PNGs (256 px, typically 20-150 KiB): ``MEDIUMBLOB`` on
#: MariaDB — plain ``BLOB`` stops at 64 KiB — and ``BYTEA`` on PostgreSQL.
AVATAR_IMAGE_BLOB = LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql", "mariadb")


class TiqoraAvatar(TiqoraBase):
    """A picture an agent uploaded for a customer (person) or a company logo.

    ``kind = "email"``: ``subject_key`` is the SHA-256 hex of the customer's
    normalized e-mail address (the same key the read endpoint is called
    with, so the address itself never appears in a URL). ``kind = "domain"``:
    ``subject_key`` is the company's website domain without ``www.``.
    ``owner_ref`` names the customer login / company id the upload was made
    for (audit, GDPR erasure). ``data`` is always a normalized PNG.
    """

    __tablename__ = "tiqora_avatar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[bytes] = mapped_column(AVATAR_IMAGE_BLOB, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("kind", "subject_key", name="uq_tiqora_avatar_kind_subject"),
    )


#: Handler results and revision dumps are JSON up to 1 MB and more: plain ``TEXT`` stops at
#: 64 KiB on MariaDB.
MEDIUM_TEXT_VARIANT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")
#: Files a job produced (up to 200 MiB): ``LONGBLOB`` on MariaDB, ``BYTEA`` on PostgreSQL.
JOB_FILE_BLOB = LargeBinary().with_variant(mysql.LONGBLOB(), "mysql", "mariadb")


class TiqoraJob(TiqoraBase):
    """A background job (see :mod:`tiqora.jobs`). Timestamps are app-written naive UTC."""

    __tablename__ = "tiqora_job"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    result: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_done: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # claims so far; a retried / requeued job keeps counting
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # a queued job is not claimed before this moment (retry backoff)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # set by the handler right before an irreversible step (JobContext.checkpoint)
    checkpoint: Mapped[str | None] = mapped_column(String(50), nullable=True)

    __table_args__ = (
        Index("ix_tiqora_job_status_created", "status", "created_at"),
        Index("ix_tiqora_job_created_by", "created_by", "created_at"),
        Index("ix_tiqora_job_kind_dedupe", "kind", "dedupe_key"),
        Index("ix_tiqora_job_finished", "finished_at"),
    )


class TiqoraJobFile(TiqoraBase):
    """A file a job produced; ``job_id`` is a soft reference to ``tiqora_job.id``."""

    __tablename__ = "tiqora_job_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(JOB_FILE_BLOB, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_tiqora_job_file_job", "job_id"),)


#: Alias for the deployed-revision ``config`` column (one shared MEDIUMTEXT expression).
REVISION_CONFIG_TEXT = MEDIUM_TEXT_VARIANT


class TiqoraProcessRevision(TiqoraBase):
    """A deployed snapshot of the process configuration (canonical JSON dump).

    ``config`` is deferred: read it with an explicit ``select(Model.config)``.
    Timestamps are app-written naive UTC.
    """

    __tablename__ = "tiqora_process_revision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[str] = deferred(mapped_column(REVISION_CONFIG_TEXT, nullable=False))
    deployed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deployed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (Index("ix_tiqora_process_revision_checksum", "checksum"),)


class TiqoraAclRevision(TiqoraBase):
    """A deployed snapshot of the ACL configuration (same columns as the process one)."""

    __tablename__ = "tiqora_acl_revision"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[str] = deferred(mapped_column(REVISION_CONFIG_TEXT, nullable=False))
    deployed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    deployed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (Index("ix_tiqora_acl_revision_checksum", "checksum"),)


#: SAML IdP metadata documents can exceed 64 KiB: ``MEDIUMTEXT`` on MariaDB.
IDENTITY_METADATA_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


class TiqoraIdentityProvider(TiqoraBase):
    """A configured sign-in provider (OIDC, SAML or LDAP). Timestamps are app-written naive UTC.

    ``config`` holds the non-secret fields of the kind's config model as JSON;
    secrets live encrypted in ``secrets_enc``.
    """

    __tablename__ = "tiqora_identity_provider"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    audience: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False)
    button_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    button_icon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preset: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: JSON; may carry pasted SAML IdP metadata (MEDIUMTEXT on MariaDB).
    config: Mapped[str] = mapped_column(IDENTITY_METADATA_TEXT, nullable=False)
    secrets_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_by: Mapped[str] = mapped_column(String(16), nullable=False)
    require_sso_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attribute_map: Mapped[str] = mapped_column(Text, nullable=False)
    jit_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    jit_defaults: Mapped[str] = mapped_column(Text, nullable=False)
    role_rules: Mapped[str] = mapped_column(Text, nullable=False)
    sync_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    metadata_xml: Mapped[str | None] = mapped_column(IDENTITY_METADATA_TEXT, nullable=True)
    metadata_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    metadata_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    create_by: Mapped[int] = mapped_column(Integer, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    change_by: Mapped[int] = mapped_column(Integer, nullable=False)
    change_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("slug", name="uq_tiqora_identity_provider_slug"),
        Index("ix_tiqora_identity_provider_enabled", "enabled", "sort"),
    )


class TiqoraIdentityLink(TiqoraBase):
    """Binds an IdP subject to a local account. Compared by ``subject_hash`` (sha256 hex),
    never by the collated ``subject`` column; ``user_id`` is a soft reference."""

    __tablename__ = "tiqora_identity_link"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "tiqora_identity_provider.id",
            ondelete="CASCADE",
            name="fk_tiqora_identity_link_provider",
        ),
        nullable=False,
    )
    user_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_login: Mapped[str] = mapped_column(String(200), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "provider_id", "user_kind", "subject_hash", name="uq_tiqora_identity_link_subject"
        ),
        Index("ix_tiqora_identity_link_user", "user_kind", "user_id"),
    )


class TiqoraIdentityManagedAssignment(TiqoraBase):
    """A role/group assignment created by a provider's role rule (so sync may remove it).

    Agents only (no ``user_kind``): write through ``user_provisioning.record_managed_assignment``.
    """

    __tablename__ = "tiqora_identity_managed_assignment"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "tiqora_identity_provider.id",
            ondelete="CASCADE",
            name="fk_tiqora_identity_managed_provider",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    permission_key: Mapped[str] = mapped_column(String(20), nullable=False)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "user_id",
            "kind",
            "target_id",
            "permission_key",
            name="uq_tiqora_identity_managed",
        ),
        Index("ix_tiqora_identity_managed_user", "user_id"),
    )


class TiqoraCustomerTotp(TiqoraBase):
    """Per-customer TOTP 2FA enrollment (customer portal).

    Same shape as :class:`TiqoraUserTotp`; the database column is
    ``customer_user_id`` (``customer_user.id`` -- a different id space than
    ``users.id``) while the Python attribute stays ``user_id`` so
    ``TOTPService`` works on both models unchanged.
    """

    __tablename__ = "tiqora_customer_totp"

    user_id: Mapped[int] = mapped_column(
        "customer_user_id", Integer, primary_key=True, autoincrement=False, nullable=False
    )
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class TiqoraCustomerPasskey(TiqoraBase):
    """Per-customer WebAuthn passkey credential (portal second factor)."""

    __tablename__ = "tiqora_customer_passkey"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    user_id: Mapped[int] = mapped_column("customer_user_id", Integer, nullable=False)
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

    __table_args__ = (Index("ix_tiqora_customer_passkey_user", "customer_user_id"),)


class TiqoraReportTicketFact(TiqoraBase):
    """One row per ticket, fully recomputed whenever the ticket is touched.

    ``ticket_id`` mirrors ``ticket.id`` and must never be generated. Datetimes
    copied from Znuny stay as stored; ``built_at`` is app-written naive UTC.
    """

    __tablename__ = "tiqora_report_ticket_fact"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tn: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    queue_id_at_create: Mapped[int] = mapped_column(Integer, nullable=False)
    state_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    state_type: Mapped[str] = mapped_column(String(30), nullable=False)
    priority_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    type_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    service_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sla_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    responsible_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    customer_user: Mapped[str | None] = mapped_column(String(250), nullable=True)
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    merged: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_response_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_response_working_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_response_target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_response_breached: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    first_closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    solution_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solution_working_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solution_target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solution_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    solution_breached: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    update_target_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    update_breach_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    calendar_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    close_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    reopen_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    queue_move_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    queue_pingpong: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    owner_change_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    article_in_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    article_out_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    note_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    agent_touch_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    accounted_time: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal(0), server_default="0"
    )
    ai_triaged: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_triage_queue_applied: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_auto_replied: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_draft_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: "currently handed off" (the escalation marker is cleared again on resume)
    ai_handed_off: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    process_entity_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_event_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_history_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    fact_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_tiqora_report_fact_queue_created", "queue_id", "created_at"),
        Index("ix_tiqora_report_fact_created", "created_at"),
        Index("ix_tiqora_report_fact_closed", "closed_at"),
        Index("ix_tiqora_report_fact_customer_created", "customer_id", "created_at"),
        Index("ix_tiqora_report_fact_owner", "owner_id"),
        Index("ix_tiqora_report_fact_state_type", "state_type"),
        Index("ix_tiqora_report_fact_last_event", "last_event_at"),
        Index("ix_tiqora_report_fact_version", "fact_version"),
    )


class TiqoraReportTicketEvent(TiqoraBase):
    """Derived ticket event; deleted and re-inserted together with the fact."""

    __tablename__ = "tiqora_report_ticket_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    history_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    to_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    from_duration_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_tiqora_report_event_ticket", "ticket_id"),
        Index("ix_tiqora_report_event_kind_time", "kind", "occurred_at"),
        Index("ix_tiqora_report_event_actor_time", "actor_user_id", "occurred_at"),
        Index("ix_tiqora_report_event_queue_time", "queue_id", "occurred_at"),
    )


class TiqoraReportDailySnapshot(TiqoraBase):
    """End-of-day stock per queue / state type / priority in the reporting zone."""

    __tablename__ = "tiqora_report_daily_snapshot"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True, autoincrement=False)
    queue_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    state_type: Mapped[str] = mapped_column(String(30), primary_key=True)
    priority_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    open_count: Mapped[int] = mapped_column(Integer, nullable=False)
    age_0_1d: Mapped[int] = mapped_column(Integer, nullable=False)
    age_1_3d: Mapped[int] = mapped_column(Integer, nullable=False)
    age_3_7d: Mapped[int] = mapped_column(Integer, nullable=False)
    age_7_30d: Mapped[int] = mapped_column(Integer, nullable=False)
    age_30d_plus: Mapped[int] = mapped_column(Integer, nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_tiqora_report_snapshot_queue_date", "queue_id", "snapshot_date"),)


class TiqoraReportState(TiqoraBase):
    """Single row (``name = "facts"``): fact builder watermark, gaps and backfill cursor."""

    __tablename__ = "tiqora_report_state"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_history_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    #: JSON ``[[history_id, first_seen_epoch], ...]`` — thousands of gaps exceed ``TEXT``.
    gap_ids: Mapped[str | None] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=True)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_orphan_sweep_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    backfill_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    backfill_job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    backfill_start_ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    backfill_cursor_ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    backfill_target_ticket_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    backfill_since: Mapped[date | None] = mapped_column(Date, nullable=True)
    backfill_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: uuid of the run that owns the cursor; a writer of a replaced run must not touch the state
    backfill_run_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    backfill_finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_snapshot_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    snapshot_time_zone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TiqoraReportDirtyTicket(TiqoraBase):
    """Tickets whose facts must be rebuilt without a new history row."""

    __tablename__ = "tiqora_report_dirty_ticket"

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # Microseconds on MariaDB too: ``clear_dirty`` compares against a taken-at time and
    # whole-second values would let it delete a mark made in the same second.
    queued_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb"), nullable=False
    )


class TiqoraDashboard(TiqoraBase):
    """A home-page dashboard: personal, shared with groups, or a role template."""

    __tablename__ = "tiqora_dashboard"
    __table_args__ = (
        Index("ix_tiqora_dashboard_owner", "owner_user_id", "kind", "sort"),
        UniqueConstraint("owner_user_id", "source_key", name="uq_tiqora_dashboard_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Znuny users.id (no FK); NULL for templates.
    owner_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Tab order among the owner's dashboards; for templates the priority (low wins).
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # personal | template | shared
    kind: Mapped[str] = mapped_column(
        String(10), nullable=False, default="personal", server_default="personal"
    )
    # Templates only: viewers cannot change it.
    locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # 'core.default' | 'tpl:<template id>': makes the first-visit copy idempotent.
    source_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # No FK: the template may be deleted later.
    source_template_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_template_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Dashboard-wide filters; reserved, always '{}' in phase 1. JSON text.
    filters: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False, default="{}")
    # Optimistic lock, +1 per save.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TiqoraDashboardRole(TiqoraBase):
    """Roles a template dashboard applies to."""

    __tablename__ = "tiqora_dashboard_role"
    __table_args__ = (Index("ix_tiqora_dashboard_role_role", "role_id"),)

    dashboard_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_dashboard.id", name="fk_tiqora_dashboard_role_dashboard", ondelete="CASCADE"
        ),
        primary_key=True,
        autoincrement=False,
    )
    # Znuny roles.id (no FK).
    role_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class TiqoraDashboardGroup(TiqoraBase):
    """Groups a shared dashboard is shared with."""

    __tablename__ = "tiqora_dashboard_group"
    __table_args__ = (Index("ix_tiqora_dashboard_group_group", "group_id"),)

    dashboard_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_dashboard.id", name="fk_tiqora_dashboard_group_dashboard", ondelete="CASCADE"
        ),
        primary_key=True,
        autoincrement=False,
    )
    # Znuny permission_groups.id (no FK).
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)


class TiqoraDashboardWidget(TiqoraBase):
    """One widget instance on a dashboard; ``id`` is generated by the client."""

    __tablename__ = "tiqora_dashboard_widget"
    __table_args__ = (Index("ix_tiqora_dashboard_widget_dashboard", "dashboard_id", "position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dashboard_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_dashboard.id", name="fk_tiqora_dashboard_widget_dashboard", ondelete="CASCADE"
        ),
        nullable=False,
    )
    type_key: Mapped[str] = mapped_column(String(120), nullable=False)
    type_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    title_override: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # JSON text, at most 20 000 bytes (enforced by the service).
    settings: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False, default="{}")
    # {"lg": {x,y,w,h}, "md": {x,y,w,h}?}
    layout: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    # Phone order (index in the widget list).
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # True: this widget ignores the dashboard-wide filters.
    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # On a copy: id of the template widget it came from (no FK, the template may go away).
    template_widget_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # On a template widget: template version that first contained it (NULL = before phase 2).
    added_in_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TiqoraDashboardCounterSample(TiqoraBase):
    """A counter's latest value per user and UTC day (sparkline history)."""

    __tablename__ = "tiqora_dashboard_counter_sample"
    __table_args__ = (Index("ix_tiqora_dashboard_sample_day", "sample_day"),)

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    counter_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    sample_day: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    sampled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TiqoraPlugin(TiqoraBase):
    """One installed plugin (image-baked in phase 1): admin on/off switch and bookkeeping."""

    __tablename__ = "tiqora_plugin"

    id: Mapped[str] = mapped_column(String(190), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: version last seen in the image
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: "image" (later phases: "upload", "repository")
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    signature_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'unsigned'")
    )
    installed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    #: agent who last switched ``enabled`` (NULL = system)
    changed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TiqoraPluginRuntime(TiqoraBase):
    """Load state of a plugin per process role (``api`` / ``worker`` / ``mcp``)."""

    __tablename__ = "tiqora_plugin_runtime"

    plugin_id: Mapped[str] = mapped_column(String(190), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: German, at most 2000 characters, no traceback
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    boot_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TiqoraPluginHook(TiqoraBase):
    """Cursor and circuit breaker of an event subscriber or scheduled task."""

    __tablename__ = "tiqora_plugin_hook"

    #: ``event:<plugin_id>:<name>`` or ``task:<plugin_id>:<name>``
    hook_key: Mapped[str] = mapped_column(String(190), primary_key=True)
    plugin_id: Mapped[str] = mapped_column(String(190), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: events: last delivered ``ticket_history.id``
    cursor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: tasks: start of the last run
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: circuit breaker; NULL = running
    tripped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_tiqora_plugin_hook_plugin_id", "plugin_id"),)


class TiqoraReport(TiqoraBase):
    """A saved report definition (``definition`` is the ReportDefinition JSON).

    ``valid = false`` marks a soft-deleted report. ``owner_user_id`` / ``*_by`` mirror
    Znuny ``users.id`` without a foreign key. Timestamps are app-written naive UTC.
    """

    __tablename__ = "tiqora_report"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: private | groups | all
    visibility: Mapped[str] = mapped_column(
        String(10), nullable=False, default="private", server_default="private"
    )
    definition: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: optimistic lock, +1 per update
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    #: ``builtin.<name>`` or ``<id>`` of the report this one was copied from
    copied_from: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    changed_by: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_tiqora_report_owner", "owner_user_id", "valid"),
        Index("ix_tiqora_report_visibility", "visibility", "valid"),
    )


class TiqoraReportGroup(TiqoraBase):
    """Groups a ``groups``-visible report is shared with (``group_id`` has no FK)."""

    __tablename__ = "tiqora_report_group"

    report_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tiqora_report.id", ondelete="CASCADE", name="fk_tiqora_report_group_report"),
        primary_key=True,
        autoincrement=False,
    )
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    __table_args__ = (Index("ix_tiqora_report_group_group", "group_id"),)


class TiqoraSavedSearch(TiqoraBase):
    """A saved ticket search (``criteria`` is the wave-0 filter JSON)."""

    __tablename__ = "tiqora_saved_search"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    criteria: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    include_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    sort_by: Mapped[str] = mapped_column(
        String(20), nullable=False, default="age", server_default="age"
    )
    sort_order: Mapped[str] = mapped_column(
        String(4), nullable=False, default="desc", server_default="desc"
    )
    #: tiqora | znuny
    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="tiqora", server_default="tiqora"
    )
    #: Znuny search-profile name (re-import key); NULL never collides in the unique index
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_tiqora_saved_search_owner", "owner_user_id"),
        UniqueConstraint(
            "owner_user_id", "source", "source_name", name="uq_tiqora_saved_search_source"
        ),
    )


class TiqoraSavedSearchGroup(TiqoraBase):
    __tablename__ = "tiqora_saved_search_group"

    saved_search_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_saved_search.id", ondelete="CASCADE", name="fk_tiqora_saved_search_group_search"
        ),
        primary_key=True,
        autoincrement=False,
    )
    group_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)

    __table_args__ = (Index("ix_tiqora_saved_search_group_group", "group_id"),)


class TiqoraSavedSearchPref(TiqoraBase):
    """Per viewer: sidebar entry, count badge and order of a saved search."""

    __tablename__ = "tiqora_saved_search_pref"

    saved_search_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_saved_search.id", ondelete="CASCADE", name="fk_tiqora_saved_search_pref_search"
        ),
        primary_key=True,
        autoincrement=False,
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    show_in_sidebar: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    show_count: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (Index("ix_tiqora_saved_search_pref_user", "user_id", "position"),)


class TiqoraChatConfig(TiqoraBase):
    """One embeddable web-chat widget configuration (public key, topics, look, limits).

    All structured columns hold JSON as ``TEXT`` (house style); the typed shape is
    ``tiqora.webchat.models.ChatConfigValues``.
    """

    __tablename__ = "tiqora_chat_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_key: Mapped[str] = mapped_column(String(40), nullable=False)
    valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    allowed_origins: Mapped[str] = mapped_column(Text, nullable=False)
    topics: Mapped[str] = mapped_column(Text, nullable=False)
    greeting: Mapped[str | None] = mapped_column(String(500), nullable=True)
    privacy_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    offline_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="form", server_default="form"
    )
    business_calendar: Mapped[str | None] = mapped_column(String(10), nullable=True)
    wait_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=180, server_default="180"
    )
    prechat_fields: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    upload_policy: Mapped[str] = mapped_column(Text, nullable=False)
    theme: Mapped[str] = mapped_column(Text, nullable=False)
    rate_limits: Mapped[str] = mapped_column(Text, nullable=False)
    default_customer_user: Mapped[str | None] = mapped_column(String(200), nullable=True)
    show_agent_names: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    changed: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    changed_by: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ux_tiqora_chat_config_name", "name", unique=True),
        Index("ux_tiqora_chat_config_key", "public_key", unique=True),
    )


class TiqoraChatSession(TiqoraBase):
    """One chat conversation; ``id`` is ``uuid4().hex`` and doubles as the public handle.

    No foreign keys: ``ticket_id`` points into Znuny's ``ticket`` table.
    ``token_hash`` is the SHA-256 of the visitor secret (empty after the retention purge).
    """

    __tablename__ = "tiqora_chat_session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, nullable=False)
    config_id: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    queue_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    end_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    visitor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    visitor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    visitor_email: Mapped[str | None] = mapped_column(String(250), nullable=True)
    customer_user_login: Mapped[str | None] = mapped_column(String(200), nullable=True)
    assigned_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    visitor_last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    page_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_tiqora_chat_session_config", "config_id"),
        Index("ix_tiqora_chat_session_status", "status", "config_id"),
        Index("ix_tiqora_chat_session_ticket", "ticket_id"),
        Index("ix_tiqora_chat_session_assigned", "assigned_user_id", "status"),
        Index("ix_tiqora_chat_session_visitor", "visitor_id"),
    )


class TiqoraChatMessage(TiqoraBase):
    """One transcript line of a chat session, ordered by the per-session ``seq``."""

    __tablename__ = "tiqora_chat_message"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    sender: Mapped[str] = mapped_column(String(10), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True)
    article_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        # NULL client_id values never collide on MariaDB or PostgreSQL
        Index("ux_tiqora_chat_message_seq", "session_id", "seq", unique=True),
        Index("ux_tiqora_chat_message_client", "session_id", "client_id", unique=True),
        Index("ix_tiqora_chat_message_ticket", "ticket_id"),
        Index("ix_tiqora_chat_message_article", "article_id"),
    )


class TiqoraChatAgentState(TiqoraBase):
    """Per-agent chat availability; ``user_id`` mirrors ``users.id`` (never generated)."""

    __tablename__ = "tiqora_chat_agent_state"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    topics: Mapped[str] = mapped_column(Text, nullable=False)
    max_concurrent: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default="3"
    )
    changed: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TiqoraReportSchedule(TiqoraBase):
    """A scheduled report delivery; runs with the owner's permissions (no FK on the owner)."""

    __tablename__ = "tiqora_report_schedule"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: pdf | xlsx | csv
    format: Mapped[str] = mapped_column(
        String(4), nullable=False, default="pdf", server_default="pdf"
    )
    #: Recurrence JSON
    recurrence: Mapped[str] = mapped_column(Text, nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    #: de | en
    language: Mapped[str] = mapped_column(
        String(5), nullable=False, default="de", server_default="de"
    )
    #: '' = default subject of the language
    subject: Mapped[str] = mapped_column(String(250), nullable=False)
    #: '' = default body
    body: Mapped[str] = mapped_column(MEDIUM_TEXT_VARIANT, nullable=False)
    only_if_nonempty: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    #: German; set by the auto-pause
    paused_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: naive UTC; NULL while inactive
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: succeeded | partial | failed | skipped
    last_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    #: consecutive failed scheduled runs
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: optimistic lock, +1 per update
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_tiqora_report_schedule_due", "active", "next_run_at"),
        Index("ix_tiqora_report_schedule_owner", "owner_user_id"),
    )


class TiqoraReportScheduleItem(TiqoraBase):
    """One report of a schedule's bundle, in order (position 0..9)."""

    __tablename__ = "tiqora_report_schedule_item"

    schedule_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_report_schedule.id",
            ondelete="CASCADE",
            name="fk_tiqora_report_schedule_item_schedule",
        ),
        primary_key=True,
        autoincrement=False,
    )
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    #: ``builtin.<name>`` or ``<saved id>``
    report_key: Mapped[str] = mapped_column(String(120), nullable=False)
    #: ScheduleItemOverrides JSON
    overrides: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_tiqora_report_schedule_item_key", "report_key"),)


class TiqoraReportScheduleRecipient(TiqoraBase):
    """Recipient of a schedule: agent, group, role or external e-mail address."""

    __tablename__ = "tiqora_report_schedule_recipient"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_report_schedule.id",
            ondelete="CASCADE",
            name="fk_tiqora_report_schedule_recipient_schedule",
        ),
        nullable=False,
    )
    #: user | group | role | email
    kind: Mapped[str] = mapped_column(String(5), nullable=False)
    #: users.id / permission_groups.id / roles.id (kind != email)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: kind = email; stored lower-case
    email: Mapped[str | None] = mapped_column(String(250), nullable=True)

    __table_args__ = (
        Index("ix_tiqora_report_schedule_recipient_schedule", "schedule_id"),
        Index("ix_tiqora_report_schedule_recipient_email", "email"),
    )


class TiqoraReportRun(TiqoraBase):
    """One delivery (bundle); outlives a deleted schedule until retention (no FK)."""

    __tablename__ = "tiqora_report_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    schedule_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: name at run time
    schedule_name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: schedule | manual
    run_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    #: manual "only to me"
    owner_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    #: the occurrence (schedule runs)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: queued | running | succeeded | partial | failed | skipped
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    #: JSON list at run time
    report_keys: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recipient_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: German lines; never e-mail addresses
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: German
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_tiqora_report_run_schedule", "schedule_id", "id"),
        Index("ix_tiqora_report_run_finished", "finished_at"),
        Index("ix_tiqora_report_run_owner", "owner_user_id"),
    )


class TiqoraReportRunFile(TiqoraBase):
    """An attachment generated for a run."""

    __tablename__ = "tiqora_report_run_file"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_report_run.id", ondelete="CASCADE", name="fk_tiqora_report_run_file_run"
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data: Mapped[bytes] = deferred(mapped_column(JOB_FILE_BLOB, nullable=False))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (Index("ix_tiqora_report_run_file_run", "run_id"),)


class TiqoraDashboardKioskToken(TiqoraBase):
    """A wallboard link: only the sha256 of the secret is stored; data runs as ``created_by``."""

    __tablename__ = "tiqora_dashboard_kiosk_token"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_tiqora_dashboard_kiosk_token_hash"),
        Index("ix_tiqora_dashboard_kiosk_token_creator", "created_by"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # sha256 hex of the secret
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # last 4 characters, shown in lists
    token_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Znuny users.id (no FK)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    # 15..900 (enforced by the service)
    rotate_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60, server_default="60"
    )
    show_details: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class TiqoraDashboardKioskItem(TiqoraBase):
    """A dashboard of a wallboard link, in rotation order."""

    __tablename__ = "tiqora_dashboard_kiosk_item"
    __table_args__ = (Index("ix_tiqora_dashboard_kiosk_item_dashboard", "dashboard_id"),)

    token_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_dashboard_kiosk_token.id",
            name="fk_tiqora_dashboard_kiosk_item_token",
            ondelete="CASCADE",
        ),
        primary_key=True,
        autoincrement=False,
    )
    dashboard_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "tiqora_dashboard.id",
            name="fk_tiqora_dashboard_kiosk_item_dashboard",
            ondelete="CASCADE",
        ),
        primary_key=True,
        autoincrement=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
