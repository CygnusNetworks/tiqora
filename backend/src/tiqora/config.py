"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for API, worker, and MCP processes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Core
    app_name: str = "Tiqora"
    environment: str = Field(default="development", validation_alias="TIQORA_ENV")
    debug: bool = Field(default=False, validation_alias="TIQORA_DEBUG")
    log_level: str = Field(default="INFO", validation_alias="TIQORA_LOG_LEVEL")
    secret_key: str = Field(
        default="change-me-in-production-use-openssl-rand",
        validation_alias="TIQORA_SECRET_KEY",
    )

    # Data stores
    database_url: str = Field(
        default="postgresql+asyncpg://tiqora:tiqora@localhost:5432/tiqora",
        validation_alias="DATABASE_URL",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias="REDIS_URL",
    )
    meili_url: str = Field(
        default="http://localhost:7700",
        validation_alias="MEILI_URL",
    )
    meili_master_key: str = Field(
        default="tiqora-dev-master-key",
        validation_alias="MEILI_MASTER_KEY",
    )
    meili_tickets_index: str = Field(
        default="tickets",
        validation_alias="MEILI_TICKETS_INDEX",
    )
    meili_kb_index: str = Field(
        default="kb",
        validation_alias="MEILI_KB_INDEX",
    )

    # HTTP
    cors_origins: str = Field(
        default="http://localhost:5173,http://localhost:8000",
        validation_alias="TIQORA_CORS_ORIGINS",
    )
    api_prefix: str = "/api/v1"

    # Sessions (Redis, opaque token, httpOnly cookie)
    session_cookie_name: str = Field(
        default="tiqora_session",
        validation_alias="TIQORA_SESSION_COOKIE",
    )
    session_ttl_seconds: int = Field(
        default=86400,
        validation_alias="TIQORA_SESSION_TTL",
    )
    session_cookie_secure: bool = Field(
        default=False,
        validation_alias="TIQORA_SESSION_COOKIE_SECURE",
    )
    session_cookie_samesite: str = Field(
        default="lax",
        validation_alias="TIQORA_SESSION_COOKIE_SAMESITE",
    )

    # Customer portal sessions (Redis, separate opaque token/cookie from agent
    # sessions — same store/TTL/secure/samesite knobs are reused for simplicity).
    customer_session_cookie_name: str = Field(
        default="tiqora_customer_session",
        validation_alias="TIQORA_CUSTOMER_SESSION_COOKIE",
    )

    # Znuny-write poller
    poller_interval_seconds: int = Field(
        default=15,
        validation_alias="TIQORA_POLLER_INTERVAL",
    )
    index_batch_size: int = Field(
        default=500,
        validation_alias="TIQORA_INDEX_BATCH_SIZE",
    )

    # Schema ownership (Phase 5) — must stay false during parallel operation
    schema_ownership: bool = Field(
        default=False,
        validation_alias="TIQORA_SCHEMA_OWNERSHIP",
    )

    # OIDC / SSO (Phase 3c). No auto-provisioning in v1: the mapped claim
    # must match an existing, valid `users.login` row or the login is rejected.
    oidc_enabled: bool = Field(default=False, validation_alias="TIQORA_OIDC_ENABLED")
    oidc_issuer: str = Field(default="", validation_alias="TIQORA_OIDC_ISSUER")
    oidc_client_id: str = Field(default="", validation_alias="TIQORA_OIDC_CLIENT_ID")
    oidc_client_secret: str = Field(default="", validation_alias="TIQORA_OIDC_CLIENT_SECRET")
    oidc_scopes: str = Field(default="openid profile email", validation_alias="TIQORA_OIDC_SCOPES")
    oidc_claim: str = Field(default="preferred_username", validation_alias="TIQORA_OIDC_CLAIM")
    oidc_redirect_uri: str = Field(default="", validation_alias="TIQORA_OIDC_REDIRECT_URI")

    # Kerberos / SPNEGO (Phase 3c). Off by default; requires the optional
    # `kerberos` extra (gssapi) and a keytab reachable via KRB5_KTNAME.
    spnego_enabled: bool = Field(default=False, validation_alias="TIQORA_SPNEGO_ENABLED")
    krb5_ktname: str = Field(default="", validation_alias="KRB5_KTNAME")

    # TOTP 2FA (Phase 3c)
    totp_pending_ttl_seconds: int = Field(default=300, validation_alias="TIQORA_TOTP_PENDING_TTL")
    totp_issuer: str = Field(default="Tiqora", validation_alias="TIQORA_TOTP_ISSUER")

    # Webhooks (Phase 3c)
    webhook_timeout_seconds: float = Field(default=10.0, validation_alias="TIQORA_WEBHOOK_TIMEOUT")
    webhook_max_attempts: int = Field(default=3, validation_alias="TIQORA_WEBHOOK_MAX_ATTEMPTS")

    # Postmaster (Phase 4a). The `daemon.postmaster.enabled` tiqora_settings
    # key (default OFF) is the actual takeover switch — see
    # docs/parallel-operation.md. This interval is only the poll cadence once
    # enabled, mirroring Znuny's Daemon::SchedulerCronTaskManager::Task###MailAccountFetch.
    postmaster_interval_seconds: int = Field(
        default=60, validation_alias="TIQORA_POSTMASTER_INTERVAL"
    )
    smtp_host: str = Field(default="localhost", validation_alias="TIQORA_SMTP_HOST")
    smtp_port: int = Field(default=25, validation_alias="TIQORA_SMTP_PORT")
    smtp_use_tls: bool = Field(default=False, validation_alias="TIQORA_SMTP_USE_TLS")
    smtp_user: str = Field(default="", validation_alias="TIQORA_SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="TIQORA_SMTP_PASSWORD")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def is_mysql(self) -> bool:
        return self.database_url.startswith("mysql")


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
