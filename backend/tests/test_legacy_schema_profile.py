"""Unit + DB tests for multi-version OTRS/Znuny schema profile detection.

Pure classification tests need no database. Detection against the real Znuny
6.5 fixture is marked ``db`` and exercises INFORMATION_SCHEMA on MariaDB.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.db.legacy.profile import (
    SchemaProfileId,
    UnsupportedLegacySchemaError,
    apply_legacy_schema_profile,
    classify_schema,
    detect_legacy_schema_profile_sync,
    ensure_legacy_schema_supported,
    get_legacy_schema_profile,
    groups_table_name,
    profile_for_id,
    reset_legacy_schema_profile,
)

# ---------------------------------------------------------------------------
# Pure classification
# ---------------------------------------------------------------------------

_CORE = {
    "ticket",
    "article",
    "users",
    "ticket_history",
    "sessions",
    "ticket_number_counter",
}


def test_classify_otrs_znuny_6_0() -> None:
    profile = classify_schema(
        table_names=_CORE | {"groups", "mail_account", "group_user", "ticket_state"},
        mail_account_columns={"id", "login", "pw", "host"},
        ticket_state_columns={"id", "name", "type_id", "valid_id"},
        group_user_columns={"user_id", "group_id", "permission_key"},
        dialect="mysql",
    )
    assert profile.profile_id is SchemaProfileId.OTRS_ZNUNY_6_0
    assert profile.groups_table == "groups"
    assert profile.mail_account_has_oauth is False
    assert profile.state_priority_has_color is False
    assert profile.is_supported


def test_classify_znuny_6_1() -> None:
    profile = classify_schema(
        table_names=_CORE | {"permission_groups", "mail_account", "group_user", "ticket_state"},
        mail_account_columns={"id", "login", "pw", "host"},
        ticket_state_columns={"id", "name"},
        group_user_columns={"user_id", "group_id", "permission_key"},
    )
    assert profile.profile_id is SchemaProfileId.ZNUNY_6_1
    assert profile.groups_table == "permission_groups"
    assert profile.mail_account_has_oauth is False


def test_classify_znuny_6_2() -> None:
    profile = classify_schema(
        table_names=_CORE
        | {
            "permission_groups",
            "mail_account",
            "group_user",
            "ticket_state",
            "acl_ticket_attribute_relations",
        },
        mail_account_columns={"id", "login", "pw", "host"},
        ticket_state_columns={"id", "name"},
        group_user_columns={"user_id", "group_id", "permission_key"},
    )
    assert profile.profile_id is SchemaProfileId.ZNUNY_6_2


def test_classify_znuny_6_3() -> None:
    profile = classify_schema(
        table_names=_CORE
        | {
            "permission_groups",
            "mail_account",
            "oauth2_token",
            "group_user",
            "ticket_state",
        },
        mail_account_columns={"id", "authentication_type", "oauth2_token_config_id"},
        ticket_state_columns={"id", "name"},
        group_user_columns={"user_id", "group_id", "permission_key"},
    )
    assert profile.profile_id is SchemaProfileId.ZNUNY_6_3
    assert profile.mail_account_has_oauth is True


def test_classify_znuny_6_5() -> None:
    profile = classify_schema(
        table_names=_CORE
        | {
            "permission_groups",
            "mail_account",
            "mention",
            "smime_keys",
            "group_user",
            "ticket_state",
        },
        mail_account_columns={"id", "authentication_type", "oauth2_token_config_id"},
        ticket_state_columns={"id", "name"},
        group_user_columns={"user_id", "group_id", "permission_key"},
    )
    assert profile.profile_id is SchemaProfileId.ZNUNY_6_5
    assert profile.label == "Znuny 6.5"


def test_classify_znuny_7_0() -> None:
    profile = classify_schema(
        table_names=_CORE
        | {
            "permission_groups",
            "mail_account",
            "mention",
            "activity",
            "group_user",
            "ticket_state",
        },
        mail_account_columns={"id", "authentication_type"},
        ticket_state_columns={"id", "name", "color"},
        group_user_columns={"user_id", "group_id", "permission_key"},
    )
    assert profile.profile_id is SchemaProfileId.ZNUNY_7_0
    assert profile.state_priority_has_color is True
    assert profile.junction_tables_have_surrogate_id is False


def test_classify_znuny_7_1() -> None:
    profile = classify_schema(
        table_names=_CORE
        | {
            "permission_groups",
            "mail_account",
            "mention",
            "activity",
            "group_user",
            "ticket_state",
        },
        mail_account_columns={"id", "authentication_type"},
        ticket_state_columns={"id", "name", "color"},
        group_user_columns={"id", "user_id", "group_id", "permission_key"},
    )
    assert profile.profile_id is SchemaProfileId.ZNUNY_7_1
    assert profile.junction_tables_have_surrogate_id is True


def test_classify_znuny_7_3() -> None:
    profile = classify_schema(
        table_names=_CORE
        | {
            "permission_groups",
            "mail_account",
            "mention",
            "activity",
            "article_color",
            "sendmail_config",
            "group_user",
            "ticket_state",
        },
        mail_account_columns={"id", "authentication_type"},
        ticket_state_columns={"id", "name", "color"},
        group_user_columns={"id", "user_id", "group_id", "permission_key"},
    )
    assert profile.profile_id is SchemaProfileId.ZNUNY_7_3
    assert profile.label == "Znuny 7.3"


def test_classify_unknown_missing_core() -> None:
    profile = classify_schema(
        table_names={"users", "sessions"},
        mail_account_columns=set(),
        ticket_state_columns=set(),
        group_user_columns=set(),
    )
    assert profile.profile_id is SchemaProfileId.UNKNOWN
    assert profile.known is False
    assert profile.is_supported is False


def test_profile_for_id_override() -> None:
    profile = profile_for_id("znuny-6.5", dialect="mysql")
    assert profile.profile_id is SchemaProfileId.ZNUNY_6_5
    assert profile.source == "override"
    assert profile.groups_table == "permission_groups"
    assert profile.mail_account_has_oauth is True


def test_profile_for_id_6_0_groups() -> None:
    profile = profile_for_id(SchemaProfileId.OTRS_ZNUNY_6_0)
    assert profile.groups_table == "groups"
    assert profile.mail_account_has_oauth is False


def test_profile_for_id_rejects_letter_tiers() -> None:
    with pytest.raises(ValueError, match="Unknown legacy schema"):
        profile_for_id("E")
    with pytest.raises(ValueError, match="Unknown legacy schema"):
        profile_for_id("A")


def test_profile_for_id_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Unknown legacy schema"):
        profile_for_id("znuny-9.9")


def test_groups_table_name_fallback_and_cache() -> None:
    reset_legacy_schema_profile()
    assert groups_table_name() == "permission_groups"
    apply_legacy_schema_profile(profile_for_id(SchemaProfileId.OTRS_ZNUNY_6_0))
    assert groups_table_name() == "groups"
    assert get_legacy_schema_profile() is not None
    reset_legacy_schema_profile()


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_refuses_unknown_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_legacy_schema_profile()

    async def _fake_detect(_session: object) -> object:
        return classify_schema(
            table_names={"users"},
            mail_account_columns=set(),
            ticket_state_columns=set(),
            group_user_columns=set(),
        )

    monkeypatch.setattr(
        "tiqora.db.legacy.profile.detect_legacy_schema_profile",
        _fake_detect,
    )

    class _Sess:
        pass

    with pytest.raises(UnsupportedLegacySchemaError):
        await ensure_legacy_schema_supported(_Sess())  # type: ignore[arg-type]

    reset_legacy_schema_profile()


@pytest.mark.asyncio
async def test_ensure_allows_unknown_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_legacy_schema_profile()

    async def _fake_detect(_session: object) -> object:
        return classify_schema(
            table_names={"users"},
            mail_account_columns=set(),
            ticket_state_columns=set(),
            group_user_columns=set(),
        )

    monkeypatch.setattr(
        "tiqora.db.legacy.profile.detect_legacy_schema_profile",
        _fake_detect,
    )

    class _Sess:
        pass

    profile = await ensure_legacy_schema_supported(_Sess(), allow_unknown=True)  # type: ignore[arg-type]
    assert profile.profile_id is SchemaProfileId.UNKNOWN
    reset_legacy_schema_profile()


@pytest.mark.asyncio
async def test_ensure_honours_profile_override() -> None:
    reset_legacy_schema_profile()

    class _Sess:
        pass

    profile = await ensure_legacy_schema_supported(
        _Sess(),  # type: ignore[arg-type]
        profile_override="znuny-6.3",
        dialect_hint="mysql",
    )
    assert profile.profile_id is SchemaProfileId.ZNUNY_6_3
    assert profile.source == "override"
    assert profile.mail_account_has_oauth is True
    reset_legacy_schema_profile()


def test_is_db_unavailable_classifies_connection_vs_logic_errors() -> None:
    from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError

    from tiqora.db.legacy.profile import is_db_unavailable

    assert is_db_unavailable(ConnectionRefusedError("connection refused"))
    assert is_db_unavailable(TimeoutError("connect timed out"))
    assert is_db_unavailable(OSError("Network is unreachable"))

    # SQLAlchemy InterfaceError → unavailable
    assert is_db_unavailable(InterfaceError("statement", {}, Exception("closed")))

    # OperationalError with connect-time wording
    assert is_db_unavailable(
        OperationalError("can't connect to MySQL server on '127.0.0.1'", {}, None)
    )

    # ProgrammingError / logic bugs must NOT soft-skip
    prog = ProgrammingError("(1146, \"Table 'x.y' doesn't exist\")", {}, None)
    assert not is_db_unavailable(prog)
    assert not is_db_unavailable(ValueError("unexpected marker set"))
    assert not is_db_unavailable(RuntimeError("detection bug"))


def test_default_color_for_write_only_on_7x() -> None:
    from tiqora.db.legacy.profile import (
        DEFAULT_STATE_PRIORITY_COLOR,
        SchemaProfileId,
        apply_legacy_schema_profile,
        default_color_for_write,
        profile_for_id,
        reset_legacy_schema_profile,
    )

    reset_legacy_schema_profile()
    assert default_color_for_write() is None

    apply_legacy_schema_profile(profile_for_id(SchemaProfileId.ZNUNY_6_5))
    assert default_color_for_write() is None

    apply_legacy_schema_profile(profile_for_id(SchemaProfileId.ZNUNY_7_0))
    assert default_color_for_write() == DEFAULT_STATE_PRIORITY_COLOR == "#FFFFFF"

    apply_legacy_schema_profile(profile_for_id(SchemaProfileId.ZNUNY_7_3))
    assert default_color_for_write() == "#FFFFFF"
    reset_legacy_schema_profile()


# ---------------------------------------------------------------------------
# Live detection against Znuny 6.5 MariaDB fixture
# ---------------------------------------------------------------------------


@pytest.mark.db
def test_detect_live_znuny_6_5_mariadb(mariadb_znuny_url: str) -> None:
    reset_legacy_schema_profile()
    engine = create_engine(mariadb_znuny_url)
    try:
        with engine.connect() as conn:
            profile = detect_legacy_schema_profile_sync(conn)
    finally:
        engine.dispose()

    assert profile.known is True
    assert profile.is_supported
    assert profile.groups_table == "permission_groups"
    assert profile.mail_account_has_oauth is True
    assert profile.state_priority_has_color is False
    assert profile.profile_id is SchemaProfileId.ZNUNY_6_5
    assert profile.dialect == "mysql"


@pytest.mark.db
@pytest.mark.asyncio
async def test_admin_system_includes_legacy_schema(mariadb_znuny_url: str) -> None:
    """GET system-info aggregate exposes the detected schema on DbStatusOut."""
    from sqlalchemy import text
    from sqlalchemy.engine import create_engine as create_sync_engine

    from tiqora.api.v1.admin import system as admin_system
    from tiqora.config import Settings
    from tiqora.db.tiqora.base import TiqoraBase
    from tiqora.domain.auth import AuthenticatedUser

    reset_legacy_schema_profile()
    sync_url = mariadb_znuny_url
    engine_sync = create_sync_engine(sync_url)
    with engine_sync.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_settings"))
    engine_sync.dispose()

    async_url = mariadb_znuny_url.replace("mysql+pymysql://", "mysql+aiomysql://")
    cfg = Settings(database_url=async_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    class _DeadRedis:
        async def ping(self) -> bool:
            raise ConnectionError("no redis")

        async def info(self) -> dict[str, str]:
            raise ConnectionError("no redis")

    try:
        async with factory() as session:
            out = await admin_system.get_system_info(
                admin=AuthenticatedUser(
                    id=1,
                    login="root@localhost",
                    first_name="Admin",
                    last_name="Znuny",
                    auth_method="session",
                ),
                session=session,
                cfg=cfg,
                redis_client=_DeadRedis(),  # type: ignore[arg-type]
            )
            legacy = out.datastores.database.legacy_schema
            assert legacy is not None
            assert legacy.known is True
            assert legacy.supported is True
            assert legacy.groups_table == "permission_groups"
            assert legacy.profile_id == "znuny-6.5"
            assert "Znuny" in legacy.label
    finally:
        await engine.dispose()
        reset_legacy_schema_profile()
