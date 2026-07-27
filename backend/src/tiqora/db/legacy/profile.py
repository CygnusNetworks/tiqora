"""Runtime detection of the OTRS/Znuny legacy schema profile.

Tiqora shares a Znuny/OTRS database in parallel-operation mode. Fresh-install
DDL evolves across **6.0–7.3** (table renames, optional columns/tables). Rather
than guessing from a version string, we probe ``INFORMATION_SCHEMA`` and map
the live shape onto a small, dialect-agnostic :class:`LegacySchemaProfile`.

Profile IDs are **version keys** (e.g. ``znuny-6.5``, ``otrs-znuny-6.0``), not
letter tiers — new peers can be added without renumbering. Detection is pure;
adapters (groups table name, optional mail OAuth columns, state/priority color
defaults) are applied via :func:`apply_legacy_schema_profile`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

logger = structlog.get_logger(__name__)

# Connect-time / transport failure substrings for :func:`is_db_unavailable`.
_CONNECTION_FAILURE_MARKERS = re.compile(
    r"(?i)"
    r"can'?t connect|could not connect|connection refused|connection reset|"
    r"timed? ?out|name or service not known|nodename nor servname|"
    r"no such host|server closed the connection|is the server running|"
    r"network is unreachable|connection aborted|actively refused|"
    r"no route to host|temporary failure in name resolution|"
    r"connection does not exist|connect call failed|failed to connect"
)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class SchemaProfileId(StrEnum):
    """Stable version-keyed schema profile identifier.

    Values are used in env overrides (``TIQORA_LEGACY_SCHEMA_PROFILE``), the
    admin System-Info API, fixture directory names, and logs. Add new members
    when supporting additional peer releases — never recycle old IDs.
    """

    OTRS_ZNUNY_6_0 = "otrs-znuny-6.0"
    ZNUNY_6_1 = "znuny-6.1"
    ZNUNY_6_2 = "znuny-6.2"
    ZNUNY_6_3 = "znuny-6.3"
    ZNUNY_6_4 = "znuny-6.4"
    ZNUNY_6_5 = "znuny-6.5"
    ZNUNY_7_0 = "znuny-7.0"
    ZNUNY_7_1 = "znuny-7.1"
    ZNUNY_7_2 = "znuny-7.2"
    ZNUNY_7_3 = "znuny-7.3"
    UNKNOWN = "unknown"


#: Human-readable peer product labels (admin UI / logs).
PROFILE_LABELS: dict[SchemaProfileId, str] = {
    SchemaProfileId.OTRS_ZNUNY_6_0: "OTRS/Znuny 6.0",
    SchemaProfileId.ZNUNY_6_1: "Znuny 6.1",
    SchemaProfileId.ZNUNY_6_2: "Znuny 6.2",
    SchemaProfileId.ZNUNY_6_3: "Znuny 6.3",
    SchemaProfileId.ZNUNY_6_4: "Znuny 6.4",
    SchemaProfileId.ZNUNY_6_5: "Znuny 6.5",
    SchemaProfileId.ZNUNY_7_0: "Znuny 7.0",
    SchemaProfileId.ZNUNY_7_1: "Znuny 7.1",
    SchemaProfileId.ZNUNY_7_2: "Znuny 7.2",
    SchemaProfileId.ZNUNY_7_3: "Znuny 7.3",
    SchemaProfileId.UNKNOWN: "unknown / unsupported",
}

#: Profiles Tiqora formally supports. Only ``unknown`` is refused without override.
SUPPORTED_PROFILES: frozenset[SchemaProfileId] = frozenset(
    {
        SchemaProfileId.OTRS_ZNUNY_6_0,
        SchemaProfileId.ZNUNY_6_1,
        SchemaProfileId.ZNUNY_6_2,
        SchemaProfileId.ZNUNY_6_3,
        SchemaProfileId.ZNUNY_6_4,
        SchemaProfileId.ZNUNY_6_5,
        SchemaProfileId.ZNUNY_7_0,
        SchemaProfileId.ZNUNY_7_1,
        SchemaProfileId.ZNUNY_7_2,
        SchemaProfileId.ZNUNY_7_3,
    }
)

#: Release Layer-A matrix anchors (CI tags / default ``schema_matrix``).
RELEASE_SCHEMA_PROFILES: tuple[SchemaProfileId, ...] = (
    SchemaProfileId.OTRS_ZNUNY_6_0,
    SchemaProfileId.ZNUNY_6_3,
    SchemaProfileId.ZNUNY_6_5,
    SchemaProfileId.ZNUNY_7_0,
    SchemaProfileId.ZNUNY_7_3,
)

#: Full fixture set (nightly / ``SCHEMA_MATRIX_FULL=1``).
ALL_SCHEMA_PROFILES: tuple[SchemaProfileId, ...] = (
    SchemaProfileId.OTRS_ZNUNY_6_0,
    SchemaProfileId.ZNUNY_6_1,
    SchemaProfileId.ZNUNY_6_2,
    SchemaProfileId.ZNUNY_6_3,
    SchemaProfileId.ZNUNY_6_4,
    SchemaProfileId.ZNUNY_6_5,
    SchemaProfileId.ZNUNY_7_0,
    SchemaProfileId.ZNUNY_7_1,
    SchemaProfileId.ZNUNY_7_2,
    SchemaProfileId.ZNUNY_7_3,
)

#: Model tables that are absent on older peer tiers (skip conformance if missing).
OPTIONAL_LEGACY_TABLES: frozenset[str] = frozenset(
    {
        "mention",
        "smime_keys",
        "oauth2_token",
        "oauth2_token_config",
        "calendar_appointment_plugin",
        "acl_ticket_attribute_relations",
        "activity",
        "article_color",
        "pm_process_preferences",
        "translation",
        "sendmail_config",
        "cloud_service_config",
    }
)

#: Model columns present only from certain profiles onward.
OPTIONAL_LEGACY_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("mail_account", "authentication_type"),
        ("mail_account", "oauth2_token_config_id"),
    }
)

_CORE_TABLES: frozenset[str] = frozenset(
    {"ticket", "article", "users", "ticket_history", "sessions", "ticket_number_counter"}
)

_KNOWN_PROFILE_VALUES: frozenset[str] = frozenset(
    p.value for p in SchemaProfileId if p is not SchemaProfileId.UNKNOWN
)


class UnsupportedLegacySchemaError(RuntimeError):
    """Raised when the live DB schema is not a known OTRS/Znuny profile.

    Process start must abort unless the operator sets
    ``TIQORA_ALLOW_UNKNOWN_LEGACY_SCHEMA=1``.
    """


@dataclass(frozen=True, slots=True)
class LegacySchemaProfile:
    """Dialect-agnostic snapshot of the connected legacy schema."""

    profile_id: SchemaProfileId
    groups_table: str  # "groups" | "permission_groups"
    mail_account_has_oauth: bool
    state_priority_has_color: bool
    junction_tables_have_surrogate_id: bool
    tables: frozenset[str] = field(default_factory=frozenset)
    dialect: str = "unknown"  # "mysql" | "postgresql" | "unknown"
    source: str = "detected"  # "detected" | "override" | "builtin"
    known: bool = True

    @property
    def label(self) -> str:
        return PROFILE_LABELS.get(self.profile_id, PROFILE_LABELS[SchemaProfileId.UNKNOWN])

    @property
    def is_supported(self) -> bool:
        return self.known and self.profile_id in SUPPORTED_PROFILES

    def has_table(self, name: str) -> bool:
        return name in self.tables

    def to_public_dict(self) -> dict[str, Any]:
        """JSON-serialisable summary for the admin System-Info API."""
        return {
            "profile_id": self.profile_id.value,
            "label": self.label,
            "known": self.known,
            "supported": self.is_supported,
            "groups_table": self.groups_table,
            "mail_account_has_oauth": self.mail_account_has_oauth,
            "state_priority_has_color": self.state_priority_has_color,
            "junction_tables_have_surrogate_id": self.junction_tables_have_surrogate_id,
            "dialect": self.dialect,
            "source": self.source,
        }


# Process-wide cache (filled at startup / first detect).
_cached_profile: LegacySchemaProfile | None = None
_adapters_applied: bool = False


def get_legacy_schema_profile() -> LegacySchemaProfile | None:
    """Return the cached profile, or ``None`` if detection has not run yet."""
    return _cached_profile


def set_legacy_schema_profile(profile: LegacySchemaProfile | None) -> None:
    """Test helper / explicit cache setter."""
    global _cached_profile
    _cached_profile = profile


def reset_legacy_schema_profile() -> None:
    """Clear the cache and restore default ORM bindings (tests)."""
    global _cached_profile, _adapters_applied
    _cached_profile = None
    _adapters_applied = False
    # Restore groups table name to the 6.5 baseline so later tests see a
    # clean slate even if a previous test applied otrs-znuny-6.0.
    try:
        from typing import cast

        from sqlalchemy import Table

        from tiqora.db.legacy.user import PermissionGroups

        table = cast(Table, PermissionGroups.__table__)
        if table.name != "permission_groups":
            table.name = "permission_groups"
            PermissionGroups.__tablename__ = "permission_groups"
    except Exception as exc:  # noqa: BLE001 — import / metadata edge in teardown
        logger.warning("legacy_schema_profile_reset_partial", error=str(exc))


def groups_table_name() -> str:
    """Resolved groups table name for raw SQL / helpers.

    Falls back to ``permission_groups`` (Znuny 6.1+) when no profile is loaded
    yet — matches the historical baseline and test fixtures.
    """
    profile = _cached_profile
    if profile is None:
        return "permission_groups"
    return profile.groups_table


def quote_ident(name: str, *, dialect: str) -> str:
    """Quote a trusted SQL identifier for MySQL/MariaDB or PostgreSQL.

    *name* must come from our code / profile (never user input).
    """
    if not name.replace("_", "").isalnum():
        raise ValueError(f"refusing to quote unsafe identifier: {name!r}")
    if dialect in {"mysql", "mariadb"}:
        return f"`{name}`"
    return f'"{name}"'


def groups_table_sql(*, dialect: str) -> str:
    """Quoted groups table identifier for embedding in trusted SQL strings."""
    return quote_ident(groups_table_name(), dialect=dialect)


def parse_profile_id(value: str | SchemaProfileId) -> SchemaProfileId:
    """Parse a profile override string; never accepts letter tiers."""
    if isinstance(value, SchemaProfileId):
        return value
    raw = value.strip()
    try:
        return SchemaProfileId(raw)
    except ValueError as exc:
        raise ValueError(
            f"Unknown legacy schema profile override {raw!r}; "
            f"expected one of {sorted(_KNOWN_PROFILE_VALUES)}"
        ) from exc


# ---------------------------------------------------------------------------
# Classification (pure — no I/O)
# ---------------------------------------------------------------------------


def classify_schema(
    *,
    table_names: set[str] | frozenset[str],
    mail_account_columns: set[str] | frozenset[str],
    ticket_state_columns: set[str] | frozenset[str],
    group_user_columns: set[str] | frozenset[str],
    dialect: str = "unknown",
    source: str = "detected",
) -> LegacySchemaProfile:
    """Map raw presence flags onto a :class:`LegacySchemaProfile`.

    Classification prefers the *newest* matching profile so upgraded installs
    that still carry leftover tables land on the newer id when decisive markers
    (color, junction id, …) exist.

    Ambiguity: 6.4 and 6.5 share the markers we probe → classified as
    ``znuny-6.5`` (primary baseline).
    """
    tables = frozenset(table_names)
    if not _CORE_TABLES.issubset(tables):
        missing = sorted(_CORE_TABLES - tables)
        logger.warning("legacy_schema_missing_core_tables", missing=missing)
        return LegacySchemaProfile(
            profile_id=SchemaProfileId.UNKNOWN,
            groups_table="permission_groups",
            mail_account_has_oauth=False,
            state_priority_has_color=False,
            junction_tables_have_surrogate_id=False,
            tables=tables,
            dialect=dialect,
            source=source,
            known=False,
        )

    has_groups = "groups" in tables
    has_permission_groups = "permission_groups" in tables
    has_oauth = "authentication_type" in mail_account_columns
    has_color = "color" in ticket_state_columns
    has_junction_id = "id" in group_user_columns
    has_mention = "mention" in tables
    has_smime = "smime_keys" in tables
    has_article_color = "article_color" in tables
    has_sendmail = "sendmail_config" in tables
    has_activity = "activity" in tables
    has_acl_rel = "acl_ticket_attribute_relations" in tables

    if has_groups and not has_permission_groups:
        groups_table = "groups"
    elif has_permission_groups:
        groups_table = "permission_groups"
    elif has_groups:
        groups_table = "groups"
    else:
        return LegacySchemaProfile(
            profile_id=SchemaProfileId.UNKNOWN,
            groups_table="permission_groups",
            mail_account_has_oauth=has_oauth,
            state_priority_has_color=has_color,
            junction_tables_have_surrogate_id=has_junction_id,
            tables=tables,
            dialect=dialect,
            source=source,
            known=False,
        )

    # Walk newest → oldest using decisive markers.
    if has_sendmail:
        profile_id = SchemaProfileId.ZNUNY_7_3
    elif has_article_color:
        profile_id = SchemaProfileId.ZNUNY_7_2
    elif has_color and has_junction_id:
        profile_id = SchemaProfileId.ZNUNY_7_1
    elif has_color or has_activity:
        profile_id = SchemaProfileId.ZNUNY_7_0
    elif has_mention or has_smime:
        # 6.4 ≈ 6.5 for our markers — prefer the primary baseline.
        profile_id = SchemaProfileId.ZNUNY_6_5
    elif has_oauth:
        profile_id = SchemaProfileId.ZNUNY_6_3
    elif groups_table == "permission_groups" and has_acl_rel:
        profile_id = SchemaProfileId.ZNUNY_6_2
    elif groups_table == "permission_groups":
        profile_id = SchemaProfileId.ZNUNY_6_1
    elif groups_table == "groups":
        profile_id = SchemaProfileId.OTRS_ZNUNY_6_0
    else:
        profile_id = SchemaProfileId.UNKNOWN

    known = profile_id is not SchemaProfileId.UNKNOWN
    return LegacySchemaProfile(
        profile_id=profile_id,
        groups_table=groups_table,
        mail_account_has_oauth=has_oauth,
        state_priority_has_color=has_color,
        junction_tables_have_surrogate_id=has_junction_id,
        tables=tables,
        dialect=dialect,
        source=source,
        known=known,
    )


def profile_for_id(
    profile_id: SchemaProfileId | str,
    *,
    dialect: str = "unknown",
    source: str = "override",
) -> LegacySchemaProfile:
    """Build a synthetic profile for a known id (env override / tests).

    Table set is a representative *superset marker set*, not a full DDL inventory.
    """
    profile_id = parse_profile_id(profile_id)

    if profile_id is SchemaProfileId.UNKNOWN:
        return LegacySchemaProfile(
            profile_id=SchemaProfileId.UNKNOWN,
            groups_table="permission_groups",
            mail_account_has_oauth=False,
            state_priority_has_color=False,
            junction_tables_have_surrogate_id=False,
            tables=frozenset(),
            dialect=dialect,
            source=source,
            known=False,
        )

    groups_table = "groups" if profile_id is SchemaProfileId.OTRS_ZNUNY_6_0 else "permission_groups"
    mail_oauth = profile_id in {
        SchemaProfileId.ZNUNY_6_3,
        SchemaProfileId.ZNUNY_6_4,
        SchemaProfileId.ZNUNY_6_5,
        SchemaProfileId.ZNUNY_7_0,
        SchemaProfileId.ZNUNY_7_1,
        SchemaProfileId.ZNUNY_7_2,
        SchemaProfileId.ZNUNY_7_3,
    }
    has_color = profile_id in {
        SchemaProfileId.ZNUNY_7_0,
        SchemaProfileId.ZNUNY_7_1,
        SchemaProfileId.ZNUNY_7_2,
        SchemaProfileId.ZNUNY_7_3,
    }
    has_junction = profile_id in {
        SchemaProfileId.ZNUNY_7_1,
        SchemaProfileId.ZNUNY_7_2,
        SchemaProfileId.ZNUNY_7_3,
    }

    tables: set[str] = set(_CORE_TABLES)
    tables.add(groups_table)
    if profile_id is not SchemaProfileId.OTRS_ZNUNY_6_0:
        tables.add("permission_groups")
    if profile_id in {
        SchemaProfileId.ZNUNY_6_2,
        SchemaProfileId.ZNUNY_6_3,
        SchemaProfileId.ZNUNY_6_4,
        SchemaProfileId.ZNUNY_6_5,
        SchemaProfileId.ZNUNY_7_0,
        SchemaProfileId.ZNUNY_7_1,
        SchemaProfileId.ZNUNY_7_2,
        SchemaProfileId.ZNUNY_7_3,
    }:
        tables.add("acl_ticket_attribute_relations")
    if mail_oauth:
        tables.update({"oauth2_token", "oauth2_token_config", "calendar_appointment_plugin"})
    if profile_id in {
        SchemaProfileId.ZNUNY_6_4,
        SchemaProfileId.ZNUNY_6_5,
        SchemaProfileId.ZNUNY_7_0,
        SchemaProfileId.ZNUNY_7_1,
        SchemaProfileId.ZNUNY_7_2,
        SchemaProfileId.ZNUNY_7_3,
    }:
        tables.update({"mention", "smime_keys"})
    if has_color:
        tables.add("activity")
    if profile_id in {SchemaProfileId.ZNUNY_7_2, SchemaProfileId.ZNUNY_7_3}:
        tables.update({"article_color", "pm_process_preferences", "translation"})
    if profile_id is SchemaProfileId.ZNUNY_7_3:
        tables.add("sendmail_config")

    return LegacySchemaProfile(
        profile_id=profile_id,
        groups_table=groups_table,
        mail_account_has_oauth=mail_oauth,
        state_priority_has_color=has_color,
        junction_tables_have_surrogate_id=has_junction,
        tables=frozenset(tables),
        dialect=dialect,
        source=source,
        known=True,
    )


# ---------------------------------------------------------------------------
# Live detection (I/O)
# ---------------------------------------------------------------------------


def _dialect_name(bind: Connection | AsyncConnection | AsyncEngine | AsyncSession) -> str:
    try:
        name = bind.dialect.name  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return "unknown"
    if name in {"mysql", "mariadb"}:
        return "mysql"
    if name in {"postgresql", "postgres"}:
        return "postgresql"
    return str(name)


def _sync_list_tables(conn: Connection) -> set[str]:
    dialect = conn.dialect.name
    if dialect in {"mysql", "mariadb"}:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()")
        )
    else:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        )
    return {str(r[0]) for r in rows}


def _sync_list_columns(conn: Connection, table: str) -> set[str]:
    dialect = conn.dialect.name
    if dialect in {"mysql", "mariadb"}:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t"
            ),
            {"t": table},
        )
    else:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :t"
            ),
            {"t": table},
        )
    return {str(r[0]) for r in rows}


def detect_legacy_schema_profile_sync(conn: Connection) -> LegacySchemaProfile:
    """Detect the schema profile using a sync SQLAlchemy connection."""
    dialect = _dialect_name(conn)
    tables = _sync_list_tables(conn)
    mail_cols = _sync_list_columns(conn, "mail_account") if "mail_account" in tables else set()
    state_cols = _sync_list_columns(conn, "ticket_state") if "ticket_state" in tables else set()
    gu_cols = _sync_list_columns(conn, "group_user") if "group_user" in tables else set()
    return classify_schema(
        table_names=tables,
        mail_account_columns=mail_cols,
        ticket_state_columns=state_cols,
        group_user_columns=gu_cols,
        dialect=dialect,
        source="detected",
    )


async def detect_legacy_schema_profile(
    session: AsyncSession | AsyncConnection,
) -> LegacySchemaProfile:
    """Detect the schema profile using an async session or connection."""
    if isinstance(session, AsyncSession):
        bind = await session.connection()
    else:
        bind = session

    def _probe(sync_conn: Connection) -> LegacySchemaProfile:
        return detect_legacy_schema_profile_sync(sync_conn)

    return await bind.run_sync(_probe)


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def apply_legacy_schema_profile(profile: LegacySchemaProfile) -> None:
    """Cache *profile* and adapt ORM bindings (groups table name).

    Design note: rebinding ``PermissionGroups.__table__.name`` is a
    **process-global** SQLAlchemy metadata mutation. That is intentional and
    safe for the production model (exactly once at API/worker startup, single
    process). Tests must call :func:`reset_legacy_schema_profile` between
    cases. Do not rely on thread-local rebinding for in-process parallelism.
    """
    global _cached_profile, _adapters_applied
    _cached_profile = profile

    from typing import cast

    from sqlalchemy import Table

    from tiqora.db.legacy.user import PermissionGroups

    table = cast(Table, PermissionGroups.__table__)
    if table.name != profile.groups_table:
        logger.info(
            "legacy_groups_table_rebind",
            from_name=table.name,
            to_name=profile.groups_table,
            profile_id=profile.profile_id.value,
        )
        table.name = profile.groups_table
        PermissionGroups.__tablename__ = profile.groups_table

    # Mail OAuth: use mail_account_load_options() — do not mutate ORM mapping.
    # Color 7.0+: default_color_for_write() on state/priority creates.

    _adapters_applied = True
    logger.info(
        "legacy_schema_profile_applied",
        profile_id=profile.profile_id.value,
        label=profile.label,
        groups_table=profile.groups_table,
        mail_oauth=profile.mail_account_has_oauth,
        color=profile.state_priority_has_color,
        junction_id=profile.junction_tables_have_surrogate_id,
        source=profile.source,
    )


def mail_account_load_options() -> list[Any]:
    """Loader options that skip OAuth columns on 6.0–6.2 (absent in DDL)."""
    from sqlalchemy.orm import load_only

    from tiqora.db.legacy.mail_account import MailAccount

    profile = _cached_profile
    if profile is None or profile.mail_account_has_oauth:
        return []
    return [
        load_only(
            MailAccount.id,
            MailAccount.login,
            MailAccount.pw,
            MailAccount.host,
            MailAccount.account_type,
            MailAccount.queue_id,
            MailAccount.trusted,
            MailAccount.imap_folder,
            MailAccount.comments,
            MailAccount.valid_id,
            MailAccount.create_time,
            MailAccount.create_by,
            MailAccount.change_time,
            MailAccount.change_by,
        )
    ]


# Znuny 7.0+ requires ticket_state.color / ticket_priority.color (NOT NULL).
# Admin create APIs do not yet accept a colour from the client; use a fixed
# neutral white so inserts succeed. Operators can recolour in Znuny/Tiqora UI
# later. Intentional minimal support — not a product default palette.
DEFAULT_STATE_PRIORITY_COLOR = "#FFFFFF"


def default_color_for_write() -> str | None:
    """Return a color default when the live schema requires it, else ``None``.

    On 7.0+ profiles returns :data:`DEFAULT_STATE_PRIORITY_COLOR` (``#FFFFFF``)
    so admin state/priority creates satisfy the NOT NULL column without a
    client-supplied colour field. On pre-7.0 returns ``None`` (ORM path).
    """
    profile = _cached_profile
    if profile is not None and profile.state_priority_has_color:
        return DEFAULT_STATE_PRIORITY_COLOR
    return None


async def insert_row_with_color(
    session: AsyncSession,
    *,
    table_name: str,
    values: dict[str, Any],
) -> int:
    """INSERT into a legacy table that has a ``color`` column (Znuny 7.0+).

    The 6.5 ORM models omit ``color``; this Core path keeps admin routes free of
    duplicated dialekt-SQL. Requires a bound session — no silent MySQL fallback
    when the dialect cannot be determined.
    """
    from sqlalchemy import Integer, column, insert, table

    bind = session.get_bind()
    if bind is None:
        raise RuntimeError(f"session has no bind; cannot INSERT into {table_name!r} with color")
    dialect = bind.dialect.name
    if not dialect:
        raise RuntimeError(
            f"session bind has empty dialect name; refusing MySQL LAST_INSERT_ID "
            f"guess for {table_name!r}"
        )

    cols = [column("id", Integer)]
    cols.extend(column(k) for k in values if k != "id")
    tbl = table(table_name, *cols)
    stmt = insert(tbl).values(**values)

    if dialect in {"postgresql", "postgres"}:
        result = await session.execute(stmt.returning(tbl.c.id))
        return int(result.scalar_one())

    result = await session.execute(stmt)
    # Async Result typing omits inserted_primary_key; present on Core inserts.
    pk = getattr(result, "inserted_primary_key", None)
    if pk and pk[0] is not None:
        return int(pk[0])
    # MariaDB/MySQL: Core usually fills inserted_primary_key; last resort.
    rid = (await session.execute(text("SELECT LAST_INSERT_ID()"))).scalar_one()
    return int(rid)


# ---------------------------------------------------------------------------
# Startup gate
# ---------------------------------------------------------------------------


def is_db_unavailable(exc: BaseException) -> bool:
    """True when *exc* means the peer DB is unreachable (soft-skip the gate).

    Connection failures (dev without a stack) must not hard-fail startup.
    Detection bugs against a **reachable** DB (bad SQL, permission denied on
    ``information_schema``, unexpected types) must **not** match — callers
    re-raise those so the process does not boot with a silent wrong profile.
    """
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

    if isinstance(exc, InterfaceError):
        return True

    if isinstance(exc, OperationalError):
        # OperationalError is also used for some server-side failures; only
        # treat connect-time / transport failures as "unavailable".
        if getattr(exc, "connection_invalidated", False):
            return True
        msg = str(exc).lower()
        if _CONNECTION_FAILURE_MARKERS.search(msg):
            return True
        # Unwrapped driver error on connect (pymysql/asyncpg/psycopg).
        orig = getattr(exc, "orig", None)
        if orig is not None and _CONNECTION_FAILURE_MARKERS.search(str(orig).lower()):
            return True
        # Bare "can't connect" style without ProgrammingError subclass.
        if orig is not None and type(orig).__name__ in {
            "OperationalError",  # pymysql / psycopg
            "InterfaceError",
            "Error",  # asyncpg.exceptions.ConnectionDoesNotExistError parent
        }:
            return _CONNECTION_FAILURE_MARKERS.search(str(orig).lower()) is not None
        return False

    if isinstance(exc, DBAPIError) and getattr(exc, "connection_invalidated", False):
        return True

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    for attr in ("orig", "__cause__", "__context__"):
        inner = getattr(exc, attr, None)
        if isinstance(inner, BaseException) and inner is not exc and is_db_unavailable(inner):
            return True
    return False


async def ensure_legacy_schema_supported(
    session: AsyncSession,
    *,
    allow_unknown: bool = False,
    profile_override: str = "",
    dialect_hint: str = "unknown",
) -> LegacySchemaProfile:
    """Detect (or override) the schema profile, apply adapters, enforce gate.

    Parameters
    ----------
    allow_unknown:
        When ``True`` (``TIQORA_ALLOW_UNKNOWN_LEGACY_SCHEMA=1``), an unknown
        profile is logged and allowed to proceed instead of raising.
    profile_override:
        When non-empty (``TIQORA_LEGACY_SCHEMA_PROFILE``), skip live detection
        and use the named version id (e.g. ``znuny-6.5``). Letter tiers are
        not accepted.
    """
    if profile_override.strip():
        profile = profile_for_id(
            profile_override.strip(),
            dialect=dialect_hint,
            source="override",
        )
        logger.info(
            "legacy_schema_profile_override",
            profile_id=profile.profile_id.value,
            label=profile.label,
        )
    else:
        profile = await detect_legacy_schema_profile(session)

    apply_legacy_schema_profile(profile)

    if not profile.is_supported:
        known = ", ".join(sorted(_KNOWN_PROFILE_VALUES))
        msg = (
            f"Unsupported or unrecognised OTRS/Znuny database schema "
            f"(profile_id={profile.profile_id.value}, label={profile.label!r}, "
            f"groups_table={profile.groups_table!r}). "
            f"Tiqora supports profile ids: {known}. "
            f"Upgrade the peer to a supported version, or set "
            f"TIQORA_ALLOW_UNKNOWN_LEGACY_SCHEMA=1 to start anyway "
            f"(unsupported — may 500 on missing tables/columns), or set "
            f"TIQORA_LEGACY_SCHEMA_PROFILE=<profile_id> to force a known profile."
        )
        if allow_unknown:
            logger.error("legacy_schema_unknown_allowed", message=msg, **profile.to_public_dict())
        else:
            logger.error("legacy_schema_unknown_refused", message=msg, **profile.to_public_dict())
            raise UnsupportedLegacySchemaError(msg)

    return profile
