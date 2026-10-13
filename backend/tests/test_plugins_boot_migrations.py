"""Plugin migrations at startup (both dialects): D6 "Boot", F3, F5.

Sync tests on purpose: the CLI runs its own event loops and one subprocess per plugin.
The core schema is created once per dialect; every test starts with no plugin rows and no
plugin tables.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from tests._schema_reset import reset_tiqora_schema
from tests.plugins.helpers import install_fixture_plugin
from tiqora.cli import migrate as migrate_cli
from tiqora.cli.migrate import add_migrate_subparser, run_migrate
from tiqora.config import get_settings
from tiqora.main import main
from tiqora.plugins import migrations as migrations_mod
from tiqora.plugins.discovery import DiscoveredPlugin, clear_discovery_cache, discovered_plugins
from tiqora.plugins.migrations import SAFE_MODE_MIGRATIONS_MESSAGE, run_boot_migrations

pytestmark = pytest.mark.db

CORE_HEAD = "20261010_0082"
TABLES_ID = "org.example.tables"
BAD_ID = "org.example.badmig"
T0 = datetime(2026, 10, 10, 8, 0)

_EXITING = """
import sys

revision = "tables_0002"
down_revision = "tables_0001"


def upgrade() -> None:
    sys.exit(3)


def downgrade() -> None:
    pass
"""

_SLEEPING = """
import time

revision = "tables_0002"
down_revision = "tables_0001"


def upgrade() -> None:
    time.sleep(60)


def downgrade() -> None:
    pass
"""

_GUARD_VIOLATION = """
from alembic import op

revision = "tables_0002"
down_revision = "tables_0001"


def upgrade() -> None:
    op.execute("UPDATE ticket SET title = 'x'")


def downgrade() -> None:
    pass
"""


def _async_url(sync_url: str) -> str:
    return (
        sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        .replace("mysql+pymysql://", "mysql+aiomysql://")
        .replace("postgresql://", "postgresql+asyncpg://")
    )


def _migrate_args(*argv: str) -> Any:
    import argparse

    parser = argparse.ArgumentParser()
    add_migrate_subparser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["migrate", *argv])


def _drop_plugin_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for name in inspect(engine).get_table_names():
            if name.startswith(("tqp_tables_", "tqp_badmig_")):
                conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
        if engine.dialect.name == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))


@pytest.fixture(scope="module", params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> Iterator[str]:
    """The dialect's database with the full core schema, migrated once per module."""
    url: str = request.getfixturevalue(request.param)
    reset_tiqora_schema(url)
    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "TIQORA_SCHEMA_OWNERSHIP")}
    os.environ["DATABASE_URL"] = _async_url(url)
    os.environ.pop("TIQORA_SCHEMA_OWNERSHIP", None)
    get_settings.cache_clear()
    try:
        assert run_migrate(_migrate_args("upgrade")) == 0
        yield url
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        engine = create_engine(url)
        _drop_plugin_tables(engine)
        with engine.begin() as conn:  # rows the core migrations seeded (strict leak check)
            for table in ("tiqora_plugin", "tiqora_alembic_version", "tiqora_report_state"):
                conn.execute(text(f"DELETE FROM {table}"))
        engine.dispose()


@pytest.fixture
def engine(sync_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    monkeypatch.setenv("DATABASE_URL", _async_url(sync_url))
    for key in ("TIQORA_PLUGINS_SAFE_MODE", "TIQORA_PLUGINS_DISABLED", "TIQORA_VERSION"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TIQORA_PLUGINS_MIGRATE_TIMEOUT_SECONDS", "120")
    get_settings.cache_clear()
    clear_discovery_cache()
    eng = create_engine(sync_url)
    _clean(eng)
    try:
        yield eng
    finally:
        _clean(eng)
        eng.dispose()
        get_settings.cache_clear()
        clear_discovery_cache()


def _clean(engine: Engine) -> None:
    _drop_plugin_tables(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tiqora_plugin WHERE id LIKE 'org.example.%'"))


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _seed(
    engine: Engine,
    plugin_id: str,
    *,
    version: str = "1.0.0",
    migrated_version: str | None = None,
    error: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_plugin (id, name, version, enabled, source, signature_status, "
                "installed_at, updated_at, migrated_version, migration_error) "
                "VALUES (:id, :id, :v, :en, 'image', 'unsigned', :t, :t, :mv, :err)"
            ),
            {
                "id": plugin_id,
                "v": version,
                "en": True,
                "t": T0,
                "mv": migrated_version,
                "err": error,
            },
        )


def _row(engine: Engine, plugin_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        found = conn.execute(
            text("SELECT * FROM tiqora_plugin WHERE id = :id"), {"id": plugin_id}
        ).mappings()
        return dict(found.one())


def _core_version(engine: Engine) -> str:
    with engine.connect() as conn:
        return str(
            conn.execute(text("SELECT version_num FROM tiqora_alembic_version")).scalar_one()
        )


def _install_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, extra: dict[str, str] | None = None
) -> Path:
    pkg = install_fixture_plugin(tmp_path, monkeypatch, "tq_fixture_tables", plugin_id=TABLES_ID)
    for name, body in (extra or {}).items():
        (pkg / "migrations" / name).write_text(body)
    return pkg


def _plugin(plugin_id: str) -> DiscoveredPlugin:
    (found,) = [p for p in discovered_plugins() if p.plugin_id == plugin_id]
    assert found.problem is None, found
    return found


def test_migrate_upgrade_runs_plugin_after_core(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_tables(tmp_path, monkeypatch)
    _seed(engine, TABLES_ID)
    assert run_migrate(_migrate_args("upgrade")) == 0
    assert "tqp_tables_note" in _tables(engine)
    row = _row(engine, TABLES_ID)
    assert (row["migrated_version"], row["migrated_head"], row["migration_error"]) == (
        "1.0.0",
        "tables_0001",
        None,
    )
    assert row["migrated_at"] is not None
    assert f"Erweiterung {TABLES_ID}: Datenbank aktuell (1.0.0)" in capsys.readouterr().out
    assert _core_version(engine) == CORE_HEAD


def test_upgrade_creates_the_row_of_a_plugin_seen_for_the_first_time(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch)
    assert run_migrate(_migrate_args("upgrade")) == 0
    row = _row(engine, TABLES_ID)
    assert (row["short_id"], row["migrated_version"], row["enabled"]) == ("tables", "1.0.0", True)
    assert "tqp_tables_note" in _tables(engine)


def test_failed_plugin_migration_does_not_stop_others(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "bad").mkdir()
    install_fixture_plugin(tmp_path / "bad", monkeypatch, "tq_fixture_badmig", plugin_id=BAD_ID)
    _install_tables(tmp_path, monkeypatch)
    _seed(engine, TABLES_ID)
    _seed(engine, BAD_ID)
    assert run_migrate(_migrate_args("upgrade")) == 0
    good, bad = _row(engine, TABLES_ID), _row(engine, BAD_ID)
    assert (good["migrated_version"], good["migration_error"]) == ("1.0.0", None)
    assert bad["migrated_version"] is None
    assert bad["migration_error"].startswith("Datenbank-Änderung")
    assert "nicht erlaubt" in bad["migration_error"]
    assert "tqp_tables_note" in _tables(engine)


def test_core_upgrade_ignores_plugin_version_tables(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = _install_tables(tmp_path, monkeypatch).parent
    _seed(engine, TABLES_ID)
    assert run_migrate(_migrate_args("upgrade")) == 0
    assert "tqp_tables_alembic_version" in _tables(engine)
    # uninstalled with "keep data": the package is gone, its tables stay
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(site)])
    clear_discovery_cache()
    assert TABLES_ID not in {p.plugin_id for p in discovered_plugins()}
    assert run_migrate(_migrate_args("upgrade")) == 0
    assert _core_version(engine) == CORE_HEAD
    assert "tqp_tables_alembic_version" in _tables(engine)


def test_skips_when_already_migrated(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        migrations_mod,
        "_run_one_in_subprocess",
        lambda plugin_id, **kw: calls.append(plugin_id),
    )
    plugin = _plugin(TABLES_ID)
    url = os.environ["DATABASE_URL"]
    assert (
        run_boot_migrations(database_url=url, plugins=[plugin], rows={TABLES_ID: ("1.0.0", None)})
        == {}
    )
    assert calls == []
    # an older migrated version does need a run
    assert run_boot_migrations(
        database_url=url, plugins=[plugin], rows={TABLES_ID: ("0.9.0", None)}
    ) == {TABLES_ID: None}
    assert calls == [TABLES_ID]


def test_error_for_the_current_version_is_not_retried_at_boot(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        migrations_mod, "_run_one_in_subprocess", lambda plugin_id, **kw: calls.append(plugin_id)
    )
    # recorded for 1.0.0 (row.version == 1.0.0): no retry
    _seed(engine, TABLES_ID, version="1.0.0", error="Datenbank-Änderung „x“ fehlgeschlagen")
    assert run_migrate(_migrate_args("upgrade")) == 0
    assert calls == []
    assert _row(engine, TABLES_ID)["migration_error"] == "Datenbank-Änderung „x“ fehlgeschlagen"
    # recorded for the version the image had before (row.version 0.9.0): one new attempt
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tiqora_plugin SET version = '0.9.0' WHERE id = :i"), {"i": TABLES_ID}
        )
    assert run_migrate(_migrate_args("upgrade")) == 0
    assert calls == [TABLES_ID]


def test_incompatible_or_invalid_plugin_not_migrated(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a").mkdir()
    install_fixture_plugin(
        tmp_path / "a",
        monkeypatch,
        "tq_fixture_tables",
        plugin_id=TABLES_ID,
        manifest_overrides={"sdk": ">=9,<10"},
    )
    (tmp_path / "b").mkdir()
    install_fixture_plugin(
        tmp_path / "b",
        monkeypatch,
        "tq_fixture_badmig",
        plugin_id=BAD_ID,
        drop=["tiqora-plugin.toml"],
    )
    calls: list[str] = []
    monkeypatch.setattr(
        migrations_mod, "_run_one_in_subprocess", lambda plugin_id, **kw: calls.append(plugin_id)
    )
    plugins = [p for p in discovered_plugins() if p.plugin_id in {TABLES_ID, BAD_ID}]
    assert {p.plugin_id for p in plugins} == {TABLES_ID, BAD_ID}
    assert any(p.problem for p in plugins if p.plugin_id == BAD_ID)
    assert run_boot_migrations(database_url="x", plugins=plugins, rows={}) == {}
    assert calls == []
    assert "tqp_tables_note" not in _tables(engine)


def test_safe_mode_runs_no_plugin_migration(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _install_tables(tmp_path, monkeypatch)
    _seed(engine, TABLES_ID)
    monkeypatch.setenv("TIQORA_PLUGINS_SAFE_MODE", "1")
    get_settings.cache_clear()
    assert run_migrate(_migrate_args("upgrade")) == 0
    assert SAFE_MODE_MIGRATIONS_MESSAGE in capsys.readouterr().out
    assert "tqp_tables_note" not in _tables(engine)
    assert _row(engine, TABLES_ID)["migrated_version"] is None


def test_env_disabled_plugin_is_not_migrated(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch)
    _seed(engine, TABLES_ID)
    monkeypatch.setenv("TIQORA_PLUGINS_DISABLED", TABLES_ID)
    get_settings.cache_clear()
    assert run_migrate(_migrate_args("upgrade")) == 0
    assert "tqp_tables_note" not in _tables(engine)
    assert _row(engine, TABLES_ID)["migrated_version"] is None


def test_plugin_calling_sys_exit_is_recorded_and_upgrade_still_exits_zero(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch, extra={"0002_exit.py": _EXITING})
    _seed(engine, TABLES_ID)
    assert run_migrate(_migrate_args("upgrade")) == 0
    row = _row(engine, TABLES_ID)
    assert row["migrated_version"] is None
    assert row["migration_error"].startswith("Datenbank-Änderung")
    assert "Exit-Code 3" in row["migration_error"]
    assert _core_version(engine) == CORE_HEAD


def test_hanging_plugin_migration_is_cut_off_by_the_timeout(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch, extra={"0002_sleep.py": _SLEEPING})
    _seed(engine, TABLES_ID)
    monkeypatch.setenv("TIQORA_PLUGINS_MIGRATE_TIMEOUT_SECONDS", "2")
    get_settings.cache_clear()
    assert run_migrate(_migrate_args("upgrade")) == 0
    row = _row(engine, TABLES_ID)
    assert row["migrated_version"] is None
    assert "länger als 2 Sekunden" in row["migration_error"]


def test_plugins_migrate_cli_with_path(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = _install_tables(tmp_path, monkeypatch).parent
    # the in-process CLI puts the site first on sys.path; restore afterwards
    monkeypatch.setattr(sys, "path", list(sys.path))
    _seed(engine, TABLES_ID)
    with pytest.raises(SystemExit) as done:
        main(["plugins", "migrate", TABLES_ID, "--path", str(site)])
    assert done.value.code == 0
    row = _row(engine, TABLES_ID)
    assert (row["migrated_version"], row["migrated_head"], row["migration_error"]) == (
        "1.0.0",
        "tables_0001",
        None,
    )
    assert "tqp_tables_note" in _tables(engine)
    assert sys.path[0] == str(site.resolve())
    assert capsys.readouterr().out.startswith(f"Erweiterung {TABLES_ID}: Datenbank aktuell")


def test_plugins_migrate_cli_reports_a_bad_migration(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    site = _install_tables(tmp_path, monkeypatch, extra={"0002_bad.py": _GUARD_VIOLATION}).parent
    monkeypatch.setattr(sys, "path", list(sys.path))
    _seed(engine, TABLES_ID)
    with pytest.raises(SystemExit) as done:
        main(["plugins", "migrate", TABLES_ID, "--path", str(site)])
    assert done.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("Datenbank-Änderung „tables_0002“ fehlgeschlagen")
    row = _row(engine, TABLES_ID)
    assert row["migration_error"] == err.strip()
    assert row["migrated_version"] is None


def test_plugins_migrate_cli_unknown_id(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit) as done:
        main(["plugins", "migrate", "org.example.nothing", "--path", str(tmp_path / "empty")])
    assert done.value.code == 1
    assert "nicht gefunden" in capsys.readouterr().err


def test_migrate_upgrade_to_other_revision_skips_plugins(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch)
    monkeypatch.setattr(
        migrate_cli,
        "_migrate_plugins",
        lambda: pytest.fail("plugins must only migrate with the default 'heads' target"),
    )
    assert run_migrate(_migrate_args("upgrade", CORE_HEAD)) == 0


_DUPLICATE_TABLE = """
import sqlalchemy as sa
from alembic import op

revision = "tables_0002"
down_revision = "tables_0001"


def upgrade() -> None:
    op.create_table("tqp_tables_note", sa.Column("id", sa.Integer, primary_key=True))


def downgrade() -> None:
    pass
"""


def test_real_db_error_is_recorded_as_one_line_naming_the_revision(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch, extra={"0002_dup.py": _DUPLICATE_TABLE})
    _seed(engine, TABLES_ID)
    assert run_migrate(_migrate_args("upgrade")) == 0
    error = _row(engine, TABLES_ID)["migration_error"]
    assert error.startswith("Datenbank-Änderung „tables_0002“ fehlgeschlagen")
    assert "Background on this error" in error  # still there, but as part of one line
    assert "\n" not in error


def test_successful_child_record_is_not_rewritten_by_the_parent(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_tables(tmp_path, monkeypatch)
    _seed(engine, TABLES_ID)

    def boom(_plugin: object) -> str:
        raise AssertionError("plugin revision modules must not load in the entrypoint process")

    monkeypatch.setattr("tiqora.plugins.migrations.plugin_migration_head", boom)
    assert run_migrate(_migrate_args("upgrade")) == 0
    row = _row(engine, TABLES_ID)
    assert (row["migrated_version"], row["migrated_head"]) == ("1.0.0", "tables_0001")


def test_unexpected_exit_one_gets_the_generic_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from tiqora.plugins import migrations

    done = subprocess.CompletedProcess([], 1, "", "Traceback ...\nValueError: kaputt\n")
    monkeypatch.setattr(migrations.subprocess, "run", lambda *a, **k: done)
    error = migrations._run_one_in_subprocess("x.y", database_url="sqlite://", timeout=5)
    assert error == "Datenbank-Änderung abgebrochen (Exit-Code 1): ValueError: kaputt"
