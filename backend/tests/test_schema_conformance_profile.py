"""Profile-aware schema conformance (Layer A).

Unlike :mod:`test_schema_conformance` (always Znuny 6.5 baseline models vs the
default fixture), this module loads each multi-version DDL fixture and checks:

1. Detected ``profile_id`` matches the fixture directory.
2. Every **required** model column exists on the live table (optional mail
   OAuth columns and optional tables are skipped when absent on that tier).
3. Critical-path tables always exist.

Opt-in: ``SCHEMA_MATRIX=1`` (same gate as the matrix suite).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.types import TypeDecorator

from tiqora.bootstrap.schema_loader import load_legacy_schema
from tiqora.db.legacy.base import legacy_metadata
from tiqora.db.legacy.profile import (
    ALL_SCHEMA_PROFILES,
    OPTIONAL_LEGACY_COLUMNS,
    OPTIONAL_LEGACY_TABLES,
    RELEASE_SCHEMA_PROFILES,
    SchemaProfileId,
    apply_legacy_schema_profile,
    detect_legacy_schema_profile_sync,
    reset_legacy_schema_profile,
)

pytestmark = pytest.mark.schema_matrix

_CORE_REQUIRED_TABLES = frozenset(
    {
        "ticket",
        "article",
        "users",
        "ticket_history",
        "sessions",
        "ticket_number_counter",
        "queue",
        "group_user",
        "group_role",
        "customer_user",
        "dynamic_field",
    }
)


def _matrix_profiles() -> tuple[str, ...]:
    if os.environ.get("SCHEMA_MATRIX_FULL") == "1":
        return tuple(p.value for p in ALL_SCHEMA_PROFILES)
    return tuple(p.value for p in RELEASE_SCHEMA_PROFILES)


def _model_category(col_type: Any) -> str:
    if isinstance(col_type, TypeDecorator):
        col_type = col_type.impl_instance
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
        return "string"
    if name == "Numeric":
        return "numeric"
    return "other"


def _db_category(sa_type: Any) -> str:
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
        return "string"
    if any(k in blob for k in ("numeric", "decimal", "float", "double", "real")):
        return "numeric"
    return "other"


def _compatible(model_cat: str, db_cat: str) -> bool:
    if model_cat == db_cat:
        return True
    return {model_cat, db_cat} <= {"string", "text"} or {model_cat, db_cat} <= {
        "binary",
        "text",
    }


def _wipe_mysql(url: str) -> None:
    from urllib.parse import urlparse

    import pymysql

    parsed = urlparse(
        url.replace("mysql+pymysql://", "mysql://").replace("mysql+aiomysql://", "mysql://")
    )
    db = (parsed.path or "/test").lstrip("/") or "test"
    conn = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=parsed.username or "root",
        password=parsed.password or "",
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{db}`")
            cur.execute(f"CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        conn.close()


def _wipe_postgres(url: str) -> None:
    import psycopg2

    dsn = url
    for prefix in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
        if dsn.startswith(prefix):
            dsn = "postgresql://" + dsn[len(prefix) :]
            break
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
    finally:
        conn.close()


@pytest.fixture(scope="module")
def conf_mariadb_url() -> Generator[str, None, None]:
    from tests.conftest import docker_available

    if not docker_available():
        pytest.skip("Docker not available")
    from testcontainers.mysql import MySqlContainer

    with MySqlContainer("mariadb:10.11", dialect="pymysql") as mysql:
        yield mysql.get_connection_url()


@pytest.fixture(scope="module")
def conf_postgres_url() -> Generator[str, None, None]:
    from tests.conftest import docker_available

    if not docker_available():
        pytest.skip("Docker not available")
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg.get_connection_url()


def _assert_profile_conformance(sync_url: str, profile_id: str, dialect: str) -> None:
    reset_legacy_schema_profile()
    if dialect == "mysql":
        _wipe_mysql(sync_url)
    else:
        _wipe_postgres(sync_url)
    load_legacy_schema(sync_url, profile_id, dialect=dialect)  # type: ignore[arg-type]

    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            detected = detect_legacy_schema_profile_sync(conn)
        apply_legacy_schema_profile(detected)

        # 6.4 fixtures are detected as znuny-6.5 (shared markers).
        if profile_id == "znuny-6.4":
            assert detected.profile_id is SchemaProfileId.ZNUNY_6_5
        else:
            assert detected.profile_id.value == profile_id, (
                f"fixture {profile_id} detected as {detected.profile_id.value}"
            )

        insp = inspect(engine)
        db_tables = {t.lower(): t for t in insp.get_table_names()}
        errors: list[str] = []

        for required in _CORE_REQUIRED_TABLES:
            if required not in db_tables:
                errors.append(f"core table missing: {required}")

        # Groups table: either groups or permission_groups
        if detected.groups_table.lower() not in db_tables:
            errors.append(f"groups table missing: {detected.groups_table}")

        for _key, table in sorted(legacy_metadata.tables.items(), key=lambda x: x[0]):
            # Physical name after rebind (e.g. groups instead of permission_groups)
            physical = table.name
            meta_key = _key  # original metadata key
            if physical.lower() not in db_tables:
                # Optional tables may be absent
                if meta_key in OPTIONAL_LEGACY_TABLES or physical in OPTIONAL_LEGACY_TABLES:
                    continue
                # permission_groups key may rebind to groups
                if meta_key == "permission_groups" and detected.groups_table.lower() in db_tables:
                    physical = detected.groups_table
                else:
                    errors.append(f"{meta_key}: table missing (physical={physical})")
                    continue

            real_name = db_tables[physical.lower()]
            db_cols = {c["name"].lower(): c for c in insp.get_columns(real_name)}

            for col in table.columns:
                col_key = col.name.lower()
                is_optional_col = (meta_key, col.name) in OPTIONAL_LEGACY_COLUMNS or (
                    physical,
                    col.name,
                ) in OPTIONAL_LEGACY_COLUMNS
                # mail_account OAuth columns: only required when the profile has them
                if is_optional_col and col.name in {
                    "authentication_type",
                    "oauth2_token_config_id",
                }:
                    if not detected.mail_account_has_oauth:
                        continue
                elif is_optional_col and col_key not in db_cols:
                    # Other optional columns: skip when absent on this tier
                    continue
                if col_key not in db_cols:
                    errors.append(f"{physical}.{col.name}: column missing in DB")
                    continue
                db_col = db_cols[col_key]
                model_cat = _model_category(col.type)
                db_cat = _db_category(db_col["type"])
                if not _compatible(model_cat, db_cat):
                    errors.append(f"{physical}.{col.name}: type model={model_cat} db={db_cat}")

            # 7.0+: color present in DB even though not on baseline models
            if (
                physical in {"ticket_state", "ticket_priority"}
                and detected.state_priority_has_color
                and "color" not in db_cols
            ):
                errors.append(f"{physical}.color: expected on 7.0+ schema")

        assert not errors, f"{profile_id}/{dialect} mismatches:\n" + "\n".join(errors)
    finally:
        engine.dispose()
        reset_legacy_schema_profile()


@pytest.mark.parametrize("profile_id", _matrix_profiles())
def test_conformance_mariadb(profile_id: str, conf_mariadb_url: str) -> None:
    _assert_profile_conformance(conf_mariadb_url, profile_id, "mysql")


@pytest.mark.parametrize("profile_id", _matrix_profiles())
def test_conformance_postgres(profile_id: str, conf_postgres_url: str) -> None:
    _assert_profile_conformance(conf_postgres_url, profile_id, "postgresql")
