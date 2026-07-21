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

    # Frontend SPA served by the api container itself (single image ships
    # backend + UI). Disable (TIQORA_SERVE_FRONTEND=0) to front with a
    # separate static host. Dir is where the Dockerfile copies the vite build.
    serve_frontend: bool = Field(default=True, validation_alias="TIQORA_SERVE_FRONTEND")
    frontend_dist_dir: str = Field(
        default="/app/frontend/dist",
        validation_alias="TIQORA_FRONTEND_DIST_DIR",
    )

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

    # LDAP/AD agent auth (Phase 3c). Bind-search-bind against a directory,
    # mirroring Kernel::System::Auth::LDAP. No auto-provisioning in v1: the
    # LDAP UID must match an existing, valid `users.login` row or the login
    # is rejected. Tried as a fallback when local password auth fails.
    ldap_enabled: bool = Field(default=False, validation_alias="TIQORA_LDAP_ENABLED")
    ldap_host: str = Field(default="", validation_alias="TIQORA_LDAP_HOST")
    ldap_port: int = Field(default=389, validation_alias="TIQORA_LDAP_PORT")
    ldap_use_ssl: bool = Field(default=False, validation_alias="TIQORA_LDAP_USE_SSL")
    ldap_use_starttls: bool = Field(default=False, validation_alias="TIQORA_LDAP_USE_STARTTLS")
    ldap_base_dn: str = Field(default="", validation_alias="TIQORA_LDAP_BASE_DN")
    ldap_bind_dn: str = Field(default="", validation_alias="TIQORA_LDAP_BIND_DN")
    ldap_bind_password: str = Field(default="", validation_alias="TIQORA_LDAP_BIND_PASSWORD")
    ldap_uid_attr: str = Field(default="uid", validation_alias="TIQORA_LDAP_UID_ATTR")
    ldap_always_filter: str = Field(default="", validation_alias="TIQORA_LDAP_ALWAYS_FILTER")
    # Optional group-membership gate (Kernel::System::Auth::LDAP's
    # GroupDN/AccessAttr/UserAttr): if ldap_group_dn is set, the user's DN
    # (or UID, when ldap_user_attr != "DN") must appear in ldap_access_attr
    # under ldap_group_dn or the login is rejected.
    ldap_group_dn: str = Field(default="", validation_alias="TIQORA_LDAP_GROUP_DN")
    ldap_access_attr: str = Field(default="memberUid", validation_alias="TIQORA_LDAP_ACCESS_ATTR")
    ldap_user_attr: str = Field(default="DN", validation_alias="TIQORA_LDAP_USER_ATTR")

    # LDAP/AD customer (portal) auth (Phase 3c), mirroring
    # Kernel::System::CustomerAuth::LDAP. Same no-auto-provisioning rule:
    # the LDAP UID must match an existing, valid `customer_user.login`.
    customer_ldap_enabled: bool = Field(
        default=False, validation_alias="TIQORA_CUSTOMER_LDAP_ENABLED"
    )
    customer_ldap_host: str = Field(default="", validation_alias="TIQORA_CUSTOMER_LDAP_HOST")
    customer_ldap_port: int = Field(default=389, validation_alias="TIQORA_CUSTOMER_LDAP_PORT")
    customer_ldap_use_ssl: bool = Field(
        default=False, validation_alias="TIQORA_CUSTOMER_LDAP_USE_SSL"
    )
    customer_ldap_use_starttls: bool = Field(
        default=False, validation_alias="TIQORA_CUSTOMER_LDAP_USE_STARTTLS"
    )
    customer_ldap_base_dn: str = Field(default="", validation_alias="TIQORA_CUSTOMER_LDAP_BASE_DN")
    customer_ldap_bind_dn: str = Field(default="", validation_alias="TIQORA_CUSTOMER_LDAP_BIND_DN")
    customer_ldap_bind_password: str = Field(
        default="", validation_alias="TIQORA_CUSTOMER_LDAP_BIND_PASSWORD"
    )
    customer_ldap_uid_attr: str = Field(
        default="uid", validation_alias="TIQORA_CUSTOMER_LDAP_UID_ATTR"
    )
    customer_ldap_always_filter: str = Field(
        default="", validation_alias="TIQORA_CUSTOMER_LDAP_ALWAYS_FILTER"
    )
    customer_ldap_group_dn: str = Field(
        default="", validation_alias="TIQORA_CUSTOMER_LDAP_GROUP_DN"
    )
    customer_ldap_access_attr: str = Field(
        default="memberUid", validation_alias="TIQORA_CUSTOMER_LDAP_ACCESS_ATTR"
    )
    customer_ldap_user_attr: str = Field(
        default="DN", validation_alias="TIQORA_CUSTOMER_LDAP_USER_ATTR"
    )

    # TOTP 2FA (Phase 3c)
    totp_pending_ttl_seconds: int = Field(default=300, validation_alias="TIQORA_TOTP_PENDING_TTL")
    totp_issuer: str = Field(default="Tiqora", validation_alias="TIQORA_TOTP_ISSUER")

    # WebAuthn passkeys as an alternative 2nd factor (Phase 3c). Disabled unless
    # both rp_id and origin are set — endpoints 404 and AuthMethodsOut.webauthn
    # is false when unset (ships flag-off).
    webauthn_rp_id: str = Field(default="", validation_alias="TIQORA_WEBAUTHN_RP_ID")
    webauthn_rp_name: str = Field(default="Tiqora", validation_alias="TIQORA_WEBAUTHN_RP_NAME")
    webauthn_origin: str = Field(default="", validation_alias="TIQORA_WEBAUTHN_ORIGIN")

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
    # Outbound SMTP. Disabled by default so agent email replies store the
    # article without 502ing when no relay is configured (prod often has no
    # TIQORA_SMTP_HOST). Set TIQORA_SMTP_ENABLED=1 once a real relay is wired.
    smtp_enabled: bool = Field(default=False, validation_alias="TIQORA_SMTP_ENABLED")
    smtp_host: str = Field(default="localhost", validation_alias="TIQORA_SMTP_HOST")
    smtp_port: int = Field(default=25, validation_alias="TIQORA_SMTP_PORT")
    smtp_use_tls: bool = Field(default=False, validation_alias="TIQORA_SMTP_USE_TLS")
    smtp_user: str = Field(default="", validation_alias="TIQORA_SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="TIQORA_SMTP_PASSWORD")

    # Phase 4b daemon takeovers. Each ``daemon.<name>.enabled`` tiqora_settings
    # key (default OFF) is the actual takeover switch — see
    # docs/parallel-operation.md. These intervals are only the poll cadence
    # once enabled.
    escalation_interval_seconds: int = Field(
        default=300, validation_alias="TIQORA_ESCALATION_INTERVAL"
    )
    notifications_interval_seconds: int = Field(
        default=60, validation_alias="TIQORA_NOTIFICATIONS_INTERVAL"
    )
    generic_agent_interval_seconds: int = Field(
        default=60, validation_alias="TIQORA_GENERIC_AGENT_INTERVAL"
    )

    # PGP / S/MIME (Phase 2c). Both OFF by default and require their
    # respective external tool (gpg / openssl) — see docs/crypto.md. When
    # enabled, inbound postmaster mail is checked for PGP/S/MIME
    # encryption/signatures (decrypt + verify, best-effort — a failure never
    # blocks delivery) and the compat GenericInterface's TicketCreate
    # EmailSecurity params are honored for outbound email articles.
    crypto_pgp_enabled: bool = Field(default=False, validation_alias="TIQORA_CRYPTO_PGP_ENABLED")
    crypto_pgp_gnupghome: str = Field(default="", validation_alias="TIQORA_CRYPTO_PGP_GNUPGHOME")
    crypto_smime_enabled: bool = Field(
        default=False, validation_alias="TIQORA_CRYPTO_SMIME_ENABLED"
    )
    # Tiqora-owned simplification of Znuny's SMIME::CertPath/SMIME::PrivatePath
    # (flat <email>.crt / <email>.key directories — see tiqora.crypto.keystore).
    crypto_smime_cert_dir: str = Field(default="", validation_alias="TIQORA_CRYPTO_SMIME_CERT_DIR")
    crypto_smime_private_dir: str = Field(
        default="", validation_alias="TIQORA_CRYPTO_SMIME_PRIVATE_DIR"
    )
    crypto_openssl_bin: str = Field(default="openssl", validation_alias="TIQORA_CRYPTO_OPENSSL_BIN")

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
