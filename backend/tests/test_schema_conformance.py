"""Schema conformance: every legacy model matches the real Znuny 6.5 DDL.

Requires Docker (testcontainers). Marked ``db`` so CI can select it when the
daemon is available.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

# Import all models so they register on legacy_metadata
from tiqora.db.legacy import (  # noqa: F401
    Acl,
    Article,
    ArticleDataMime,
    ArticleDataMimeAttachment,
    ArticleDataMimePlain,
    ArticleFlag,
    ArticleSearchIndex,
    ArticleSenderType,
    AutoResponse,
    AutoResponseType,
    CommunicationChannel,
    CustomerCompany,
    CustomerPreferences,
    CustomerUser,
    CustomerUserCustomer,
    DynamicField,
    DynamicFieldObjIdName,
    DynamicFieldValue,
    FollowUpPossible,
    FormDraft,
    GenericAgentJobs,
    GiWebserviceConfig,
    GroupCustomer,
    GroupCustomerUser,
    GroupRole,
    GroupUser,
    LinkObject,
    LinkRelation,
    LinkType,
    MailQueue,
    Mention,
    PermissionGroups,
    PostmasterFilter,
    Queue,
    QueueAutoResponse,
    QueueStandardTemplate,
    Roles,
    RoleUser,
    Salutation,
    Service,
    ServiceCustomerUser,
    ServiceSla,
    Sessions,
    Signature,
    Sla,
    StandardAttachment,
    StandardTemplate,
    StandardTemplateAttachment,
    SysconfigDefault,
    SysconfigModified,
    SystemAddress,
    Ticket,
    TicketFlag,
    TicketHistory,
    TicketHistoryType,
    TicketIndex,
    TicketLockIndex,
    TicketLockType,
    TicketLoopProtection,
    TicketNumberCounter,
    TicketPriority,
    TicketState,
    TicketStateType,
    TicketType,
    TicketWatcher,
    TimeAccounting,
    UserPreferences,
    Users,
    Valid,
)
from tiqora.db.legacy.base import legacy_metadata

pytestmark = pytest.mark.db


def _category(sa_type: Any, dialect: str) -> str:
    """Map a SQLAlchemy / DBAPI type name into a coarse category."""
    name = type(sa_type).__name__.lower()
    raw = str(sa_type).lower()
    blob = f"{name} {raw}"
    if any(k in blob for k in ("int", "serial", "bigint", "smallint", "tinyint")):
        return "int"
    if any(k in blob for k in ("datetime", "timestamp", "date")):
        return "datetime"
    if any(k in blob for k in ("blob", "bytea", "binary", "varbinary", "largebinary")):
        return "binary"
    if any(k in blob for k in ("text", "clob", "json")):
        return "text"
    if any(k in blob for k in ("char", "varchar", "string", "enum")):
        # Large MySQL MEDIUMTEXT from VARCHAR(>64k) may appear as text — treat as text-ish
        return "string"
    if any(k in blob for k in ("numeric", "decimal", "float", "double", "real")):
        return "numeric"
    return "other"


def _model_category(col_type: Any) -> str:
    name = type(col_type).__name__
    if name in {"Integer", "BigInteger", "SmallInteger"}:
        return "int"
    if name == "DateTime":
        return "datetime"
    if name == "LargeBinary":
        return "binary"
    if name == "Text":
        return "text"
    if name == "String":
        # Models map huge VARCHAR to Text; String is normal string
        return "string"
    if name == "Numeric":
        return "numeric"
    return "other"


def _compatible(model_cat: str, db_cat: str) -> bool:
    """Allow Znuny dialect-specific type remappings.

    - string↔text: huge VARCHAR promoted to TEXT/MEDIUMTEXT
    - binary↔text: Znuny maps MySQL LONGBLOB → PostgreSQL TEXT for YAML/config
      blobs and large payloads (acl.config_*, sysconfig_*, attachments, …).
      Models keep ``LargeBinary`` for MySQL fidelity; PG stores the same bytes
      as text. Application code must round-trip both (see decode helpers).
    """
    if model_cat == db_cat:
        return True
    # string↔text (huge VARCHAR→TEXT) or binary↔text (MySQL LONGBLOB→PG TEXT)
    return {model_cat, db_cat} <= {"string", "text"} or {model_cat, db_cat} <= {
        "binary",
        "text",
    }


@pytest.fixture(scope="module")
def mariadb_engine(mariadb_znuny_url: str) -> Iterator[Engine]:
    engine = create_engine(mariadb_znuny_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def postgres_engine(postgres_znuny_url: str) -> Iterator[Engine]:
    # Sync driver URL from testcontainers is typically postgresql+psycopg2://
    engine = create_engine(postgres_znuny_url)
    yield engine
    engine.dispose()


@pytest.mark.parametrize("dialect", ["mariadb", "postgres"])
def test_all_mapped_tables_exist(
    dialect: str,
    request: pytest.FixtureRequest,
) -> None:
    engine: Engine = request.getfixturevalue(f"{dialect}_engine")
    insp = inspect(engine)
    db_tables = {t.lower() for t in insp.get_table_names()}
    missing = []
    for table in legacy_metadata.tables.values():
        if table.name.lower() not in db_tables:
            missing.append(table.name)
    assert not missing, f"Missing tables on {dialect}: {missing}"


@pytest.mark.parametrize("dialect", ["mariadb", "postgres"])
def test_mapped_columns_match_name_nullability_type_category(
    dialect: str,
    request: pytest.FixtureRequest,
) -> None:
    engine: Engine = request.getfixturevalue(f"{dialect}_engine")
    insp = inspect(engine)
    errors: list[str] = []

    for table_name, table in sorted(legacy_metadata.tables.items(), key=lambda x: x[0]):
        if table_name not in insp.get_table_names() and table_name.lower() not in {
            t.lower() for t in insp.get_table_names()
        }:
            errors.append(f"{table_name}: table missing")
            continue

        # Resolve actual table name case
        actual_names = {t.lower(): t for t in insp.get_table_names()}
        real_name = actual_names[table_name.lower()]
        db_cols = {c["name"].lower(): c for c in insp.get_columns(real_name)}

        for col in table.columns:
            key = col.name.lower()
            if key not in db_cols:
                errors.append(f"{table_name}.{col.name}: column missing in DB")
                continue
            db_col = db_cols[key]
            # Nullability
            model_nullable = col.nullable
            db_nullable = bool(db_col["nullable"])
            # Primary keys often force nullable=False even if reflected oddities
            if model_nullable != db_nullable and not (col.primary_key and not model_nullable):
                errors.append(
                    f"{table_name}.{col.name}: nullability model={model_nullable} db={db_nullable}"
                )
            model_cat = _model_category(col.type)
            db_cat = _category(db_col["type"], dialect)
            if not _compatible(model_cat, db_cat):
                errors.append(
                    f"{table_name}.{col.name}: type category model={model_cat} "
                    f"db={db_cat} (sa={col.type!r}, db={db_col['type']!r})"
                )

    assert not errors, "Schema mismatches:\n" + "\n".join(errors)


def test_legacy_metadata_is_separate_from_tiqora() -> None:
    """Legacy MetaData must not be the same object as any Alembic-owned base."""
    from sqlalchemy.orm import DeclarativeBase

    class _Probe(DeclarativeBase):
        pass

    assert legacy_metadata is not _Probe.metadata
    assert len(legacy_metadata.tables) >= 45


def test_expected_core_tables_registered() -> None:
    names = set(legacy_metadata.tables.keys())
    for required in (
        "ticket",
        "article",
        "queue",
        "users",
        "permission_groups",
        "group_user",
        "group_role",
        "sysconfig_default",
        "sysconfig_modified",
        "customer_user",
        "dynamic_field",
    ):
        assert required in names
