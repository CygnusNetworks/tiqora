"""Plugin migrations on real databases (both dialects), D6.

Sync tests on purpose: the plugin Alembic environment runs its own event loop.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from tests.plugins.helpers import install_fixture_plugin
from tiqora.plugins.discovery import DiscoveredPlugin, discovered_plugins
from tiqora.plugins.migrations import (
    PluginMigrationError,
    StatementGuard,
    downgrade_plugin_to_base,
    plugin_migration_head,
    upgrade_plugin,
)

pytestmark = pytest.mark.db

_BAD = """
import sqlalchemy as sa
from alembic import op

revision = "tables_0002"
down_revision = "tables_0001"


def upgrade() -> None:
    op.create_table("tqp_tables_ok", sa.Column("id", sa.Integer(), primary_key=True))
    op.execute("UPDATE ticket SET title = 'x'")


def downgrade() -> None:
    op.drop_table("tqp_tables_ok")
"""

_PLAIN = """
import sqlalchemy as sa
from alembic import op

revision = "tables_0002"
down_revision = "tables_0001"


def upgrade() -> None:
    op.create_table("zz_plain", sa.Column("id", sa.Integer(), primary_key=True))


def downgrade() -> None:
    op.drop_table("zz_plain")
"""

_SECOND_BASE = """
revision = "tables_other"
down_revision = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""


def _async_url(url: str) -> str:
    return (
        url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _drop_test_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for name in inspect(engine).get_table_names():
            if name.startswith(("tqp_tables_", "tqp_hello_")) or name == "zz_plain":
                conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
        if engine.dialect.name == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.execute(text("UPDATE ticket SET title = title WHERE 1 = 0"))


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def db(request: pytest.FixtureRequest) -> Iterator[tuple[Engine, str]]:
    sync_url = request.getfixturevalue(request.param)
    engine = create_engine(sync_url)
    _drop_test_tables(engine)
    try:
        yield engine, _async_url(sync_url)
    finally:
        _drop_test_tables(engine)
        engine.dispose()


def _discover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package: str,
    *,
    plugin_id: str,
    extra: dict[str, str] | None = None,
) -> DiscoveredPlugin:
    pkg_dir = install_fixture_plugin(tmp_path, monkeypatch, package, plugin_id=plugin_id)
    for name, body in (extra or {}).items():
        (pkg_dir / "migrations" / name).write_text(body)
    found = [p for p in discovered_plugins() if p.plugin_id == plugin_id]
    assert len(found) == 1 and found[0].problem is None, found
    return found[0]


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _version(engine: Engine, short_id: str = "tables") -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT version_num FROM tqp_{short_id}_alembic_version")
        ).scalar_one_or_none()


def _core_versions(engine: Engine) -> list[str]:
    if "tiqora_alembic_version" not in _tables(engine):
        return []
    with engine.connect() as conn:
        return sorted(
            str(r[0]) for r in conn.execute(text("SELECT version_num FROM tiqora_alembic_version"))
        )


def _titled_x(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM ticket WHERE title = 'x'")).scalar_one())


def test_upgrade_creates_prefixed_tables_and_own_version_table(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    core_before = _core_versions(engine)
    plugin = _discover(tmp_path, monkeypatch, "tq_fixture_tables", plugin_id="org.example.tables")
    assert plugin_migration_head(plugin) == "tables_0001"
    assert upgrade_plugin(plugin, database_url=url) == "tables_0001"
    names = _tables(engine)
    assert {"tqp_tables_note", "tqp_tables_alembic_version"} <= names
    assert _version(engine) == "tables_0001"
    assert _core_versions(engine) == core_before  # the core version table never sees it
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tqp_tables_note (body) VALUES ('hallo')"))


def test_upgrade_is_idempotent(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    plugin = _discover(tmp_path, monkeypatch, "tq_fixture_tables", plugin_id="org.example.tables")
    assert upgrade_plugin(plugin, database_url=url) == "tables_0001"
    assert upgrade_plugin(plugin, database_url=url) == "tables_0001"
    assert _version(engine) == "tables_0001"


def test_downgrade_to_base_removes_the_tables(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    plugin = _discover(tmp_path, monkeypatch, "tq_fixture_tables", plugin_id="org.example.tables")
    upgrade_plugin(plugin, database_url=url)
    downgrade_plugin_to_base(plugin, database_url=url)
    assert "tqp_tables_note" not in _tables(engine)
    assert _version(engine) is None


def test_guard_blocks_before_execution(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    plugin = _discover(
        tmp_path,
        monkeypatch,
        "tq_fixture_tables",
        plugin_id="org.example.tables",
        extra={"0002_bad.py": _BAD},
    )
    with pytest.raises(PluginMigrationError, match=r"„tables_0002“.*nicht erlaubt"):
        upgrade_plugin(plugin, database_url=url)
    assert _titled_x(engine) == 0


def _upgrade_then_add_bad(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> DiscoveredPlugin:
    plugin = _discover(tmp_path, monkeypatch, "tq_fixture_tables", plugin_id="org.example.tables")
    assert upgrade_plugin(plugin, database_url=url) == "tables_0001"
    assert plugin.package_dir is not None
    (plugin.package_dir / "migrations" / "0002_bad.py").write_text(_BAD)
    return plugin


def test_failed_run_rolls_back_on_postgres(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    if engine.dialect.name != "postgresql":
        pytest.skip("transactional DDL is PostgreSQL only")
    plugin = _upgrade_then_add_bad(url, tmp_path, monkeypatch)
    with pytest.raises(PluginMigrationError, match="nicht erlaubt"):
        upgrade_plugin(plugin, database_url=url)
    assert "tqp_tables_ok" not in _tables(engine)
    assert "tqp_tables_note" in _tables(engine)
    assert _version(engine) == "tables_0001"
    assert _titled_x(engine) == 0


def test_failed_run_reported_on_mariadb(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    if engine.dialect.name != "mysql":
        pytest.skip("DDL autocommit is MariaDB only")
    plugin = _upgrade_then_add_bad(url, tmp_path, monkeypatch)
    with pytest.raises(PluginMigrationError, match="nicht erlaubt"):
        upgrade_plugin(plugin, database_url=url)
    assert "tqp_tables_ok" in _tables(engine)  # DDL committed implicitly before the bad statement
    assert _version(engine) == "tables_0001"
    assert _titled_x(engine) == 0


def test_post_run_diff_catches_unprefixed_table(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    monkeypatch.setattr(StatementGuard, "check", lambda self, statement: None)
    plugin = _discover(
        tmp_path,
        monkeypatch,
        "tq_fixture_tables",
        plugin_id="org.example.tables",
        extra={"0002_plain.py": _PLAIN},
    )
    with pytest.raises(PluginMigrationError, match=r"Tabelle zz_plain "):
        upgrade_plugin(plugin, database_url=url)


def test_plugin_without_migrations_returns_none(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    plugin = _discover(tmp_path, monkeypatch, "tq_fixture_hello", plugin_id="org.example.hello")
    assert plugin_migration_head(plugin) is None
    assert upgrade_plugin(plugin, database_url=url) is None
    assert not any(n.endswith("_alembic_version") and n.startswith("tqp_") for n in _tables(engine))


def test_two_heads_rejected(
    db: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, url = db
    plugin = _discover(
        tmp_path,
        monkeypatch,
        "tq_fixture_tables",
        plugin_id="org.example.tables",
        extra={"0002_other.py": _SECOND_BASE},
    )
    with pytest.raises(PluginMigrationError, match="mehrere Enden"):
        upgrade_plugin(plugin, database_url=url)
    assert "tqp_tables_note" not in _tables(engine)
