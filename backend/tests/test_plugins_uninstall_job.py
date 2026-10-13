"""Plugin uninstall job and endpoint (D9) against real databases (both dialects).

Band 62200-62239: like ``test_plugins_install_job`` no seeded ids are needed; the fixture
plugin ``org.example.tables`` (short id ``tables``) is installed through the real install job,
and everything it leaves behind (rows, ``tqp_tables_*`` tables, decoys, settings) is removed
here. The installer's environment and helpers are reused.
"""

from __future__ import annotations

import base64
import gzip
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.orm import undefer

from tests.test_plugins_install_job import (  # noqa: F401
    BASE,
    KEY_USER,
    PACKAGE,
    ROOT,
    TABLES,
    Env,
    _app,
    _client,
    _install,
    _job,
    _plugin_row,
    _restart_rows,
    _run_jobs,
    _tables,
)
from tests.test_plugins_install_job import (
    children as _children_fixture,
)
from tests.test_plugins_install_job import (
    env_fixture as _env_fixture,
)
from tiqora.db.tiqora.models import (
    TiqoraJob,
    TiqoraJobFile,
    TiqoraPlugin,
    TiqoraPluginData,
    TiqoraPluginHook,
    TiqoraPluginPackage,
    TiqoraPluginRuntime,
    TiqoraSettings,
)
from tiqora.jobs.service import utc_now
from tiqora.plugins import install_jobs, migrations, runtime
from tiqora.plugins.install_jobs import IN_IMAGE_MESSAGE

pytestmark = pytest.mark.db

# the installer test module's fixtures, re-exported under their pytest names
env_fixture = _env_fixture
children = _children_fixture

PLUGINS = "/api/v1/admin/plugins"
SETTING = f"plugins.{TABLES}.greeting"
DECOY_SETTING = "plugins.org.example.tablesx.greeting"  # another plugin's key, same text start
DECOY_TABLES = ("tqp_tablesx_keep", "tiqora_x_keep")
BLOB_TABLE = "tqp_tables_blob"

_KIND_MIGRATION = b"""
import sqlalchemy as sa
from alembic import op

revision = "tables_0002"
down_revision = "tables_0001"


def upgrade() -> None:
    op.execute("CREATE TYPE tqp_tables_kind AS ENUM ('a', 'b')")
    op.add_column(
        "tqp_tables_note",
        sa.Column(
            "kind",
            sa.dialects.postgresql.ENUM("a", "b", name="tqp_tables_kind", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tqp_tables_note", "kind")
    op.execute("DROP TYPE tqp_tables_kind")
"""


async def _drop_extra(env: Env) -> None:
    async with env.factory() as s:
        for name in (*DECOY_TABLES, BLOB_TABLE):
            await s.execute(text(f"DROP TABLE IF EXISTS {name}"))
        await s.execute(
            delete(TiqoraSettings).where(TiqoraSettings.key.in_((SETTING, DECOY_SETTING)))
        )
        await s.execute(delete(TiqoraPluginRuntime).where(TiqoraPluginRuntime.plugin_id == TABLES))
        await s.commit()


@pytest.fixture(autouse=True)
async def _extra_cleanup(env: Env) -> AsyncIterator[None]:
    await _drop_extra(env)
    yield
    await _drop_extra(env)


async def _install_with_data(env: Env, **kw: Any) -> None:
    """The fixture plugin, installed, with two notes, a setting, a hook and a runtime row."""
    job = await _install(env, env.package(**kw)[0], restart=False)
    assert job.status == "succeeded", job.error
    async with env.factory() as s:
        await s.execute(
            text("INSERT INTO tqp_tables_note (body) VALUES (:a), (:b)"),
            {"a": "eins", "b": "zwei"},
        )
        s.add(TiqoraSettings(key=SETTING, value="Hallo"))
        s.add(TiqoraSettings(key=DECOY_SETTING, value="bleibt"))
        s.add(
            TiqoraPluginHook(
                hook_key=f"event:{TABLES}:note",
                plugin_id=TABLES,
                kind="event",
                cursor=7,
                created_at=utc_now().replace(microsecond=0),
            )
        )
        s.add(
            TiqoraPluginRuntime(
                plugin_id=TABLES,
                role="api",
                state="loaded",
                version="1.0.0",
                boot_attempts=0,
                updated_at=utc_now().replace(microsecond=0),
            )
        )
        await s.commit()


async def _uninstall(
    env: Env, plugin_id: str = TABLES, *, user: Any = ROOT, settings: Any = None, **body: Any
) -> Any:
    async with _client(_app(env, user=user, **(settings or {}))) as client:
        return await client.post(f"{PLUGINS}/{plugin_id}/uninstall", json=body)


async def _run_uninstall(env: Env, *, settings: Any = None, **body: Any) -> TiqoraJob:
    r = await _uninstall(env, settings=settings, **body)
    assert r.status_code == 202, r.text
    await _run_jobs(env, **(settings or {}))
    return await _job(env, r.json()["job_id"])


async def _count(env: Env, table: str) -> int:
    async with env.factory() as s:
        return int((await s.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one())


async def _all_tables(env: Env) -> set[str]:
    async with env.factory() as s:
        return set(await s.run_sync(lambda c: inspect(c.connection()).get_table_names()))


async def _files(env: Env, job_id: int) -> list[TiqoraJobFile]:
    async with env.factory() as s:
        return list(
            (
                await s.execute(
                    select(TiqoraJobFile)
                    .where(TiqoraJobFile.job_id == job_id)
                    .options(undefer(TiqoraJobFile.data))
                )
            )
            .scalars()
            .all()
        )


async def _make_decoys(env: Env) -> None:
    async with env.factory() as s:
        for name in DECOY_TABLES:
            await s.execute(text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
        await s.commit()


def test_uninstall_job_is_registered_like_install() -> None:
    from tiqora.extensions.core import register_core
    from tiqora.extensions.registry import Registry
    from tiqora.jobs.registry import JOB_HANDLERS

    reg = Registry()
    register_core(reg)
    handler = reg.get(JOB_HANDLERS, "core.plugin_uninstall")
    assert handler is not None
    assert (handler.lane, handler.max_attempts, handler.requeue_if_interrupted) == (
        "plugins",
        1,
        False,
    )
    assert (handler.result_private, handler.timeout_seconds) == (False, 1500)
    assert handler.title_key == "jobs.kinds.core.plugin_uninstall"


async def test_uninstall_keep_data(env: Env, children: list[Any]) -> None:
    await _install_with_data(env)
    job = await _run_uninstall(env, delete_data=False, restart=True)
    assert job.status == "succeeded", job.error
    result = json.loads(job.result or "{}")
    assert await _plugin_row(env) is None
    async with env.factory() as s:
        assert (await s.get(TiqoraPluginRuntime, (TABLES, "api"))) is None
        stored = (
            (
                await s.execute(
                    select(TiqoraPluginPackage).where(TiqoraPluginPackage.plugin_id == TABLES)
                )
            )
            .scalars()
            .all()
        )
        owner = await s.get(TiqoraPluginData, "tables")
        setting = await s.get(TiqoraSettings, SETTING)
        hook = await s.get(TiqoraPluginHook, f"event:{TABLES}:note")
    assert stored and all(p.installed_at is None for p in stored)
    assert owner is not None and owner.plugin_id == TABLES  # F2: the data has an owner
    assert setting is not None and setting.value == "Hallo"  # settings stay with the data
    assert hook is not None
    assert await _count(env, "tqp_tables_note") == 2
    assert {"tqp_tables_note", "tqp_tables_alembic_version"} <= await _tables(env)
    restarts = await _restart_rows(env)
    assert [r.reason for r in restarts] == ["Erweiterung „Tabellen-Erweiterung“ entfernt"]
    assert result == {
        "plugin_id": TABLES,
        "deleted_tables": [],
        "export_file_id": None,
        "restart_generation": restarts[0].generation,
    }
    assert await _files(env, job.id) == []


async def test_keep_data_then_reinstall_adopts_the_data(env: Env, children: list[Any]) -> None:
    await _install_with_data(env)
    assert (await _run_uninstall(env, delete_data=False, restart=False)).status == "succeeded"
    job = await _install(env, env.package("1.0.1")[0], restart=False)
    assert job.status == "succeeded", job.error
    assert await _count(env, "tqp_tables_note") == 2
    async with env.factory() as s:
        assert await s.get(TiqoraPluginData, "tables") is None


async def test_uninstall_without_restart(env: Env, children: list[Any]) -> None:
    await _install_with_data(env)
    job = await _run_uninstall(env, delete_data=False, restart=False)
    assert job.status == "succeeded", job.error
    assert json.loads(job.result or "{}")["restart_generation"] is None
    assert await _restart_rows(env) == []


async def test_delete_data_exports_then_drops_only_prefix(env: Env, children: list[Any]) -> None:
    await _install_with_data(env)
    await _make_decoys(env)
    async with env.factory() as s:
        meta = sa.MetaData()
        blob = sa.Table(
            BLOB_TABLE,
            meta,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("data", sa.LargeBinary()),
        )
        await s.run_sync(lambda c: meta.create_all(c.connection()))
        await s.execute(blob.insert().values(id=1, data=b"\x00\xff\x10"))
        await s.commit()
    job = await _run_uninstall(env, delete_data=True, restart=False)
    assert job.status == "succeeded", job.error
    tables = await _all_tables(env)
    assert not {t for t in tables if t.startswith("tqp_tables_")}  # incl. the version table
    assert set(DECOY_TABLES) <= tables
    assert await _plugin_row(env) is None
    async with env.factory() as s:
        assert await s.get(TiqoraSettings, SETTING) is None
        decoy = await s.get(TiqoraSettings, DECOY_SETTING)
        assert decoy is not None and decoy.value == "bleibt"
        assert await s.get(TiqoraPluginHook, f"event:{TABLES}:note") is None
        assert await s.get(TiqoraPluginData, "tables") is None
    files = await _files(env, job.id)
    assert len(files) == 1
    assert files[0].name.startswith(f"{TABLES}-daten-") and files[0].name.endswith(".jsonl.gz")
    assert files[0].content_type == "application/gzip"
    lines = [json.loads(x) for x in gzip.decompress(files[0].data).decode().splitlines()]
    header, rows = lines[0], lines[1:]
    assert header["plugin_id"] == TABLES and header["short_id"] == "tables"
    assert header["exported_at"]
    assert header["tables"] == sorted(
        ["tqp_tables_alembic_version", "tqp_tables_blob", "tqp_tables_note"]
    )
    notes = [r["row"]["body"] for r in rows if r["table"] == "tqp_tables_note"]
    assert sorted(notes) == ["eins", "zwei"]
    blobs = [r["row"] for r in rows if r["table"] == BLOB_TABLE]
    assert [base64.b64decode(b["data"]) for b in blobs] == [b"\x00\xff\x10"]
    assert [
        r["row"]["version_num"] for r in rows if r["table"] == "tqp_tables_alembic_version"
    ] == ["tables_0001"]
    result = json.loads(job.result or "{}")
    assert result["export_file_id"] == files[0].id
    assert result["deleted_tables"] == header["tables"]


async def test_delete_data_too_large_refuses(
    env: Env, children: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _install_with_data(env)
    monkeypatch.setattr(migrations, "EXPORT_MAX_BYTES", 10)
    job = await _run_uninstall(env, delete_data=True, restart=False)
    assert job.status == "failed"
    assert job.error == (
        "Die Daten der Erweiterung sind größer als 200 MB. Bitte sichern Sie die Datenbank "
        "und entfernen Sie die Tabellen tqp_tables_* von Hand."
    )
    assert await _count(env, "tqp_tables_note") == 2
    assert await _plugin_row(env) is not None
    assert await _files(env, job.id) == []
    assert await _restart_rows(env) == []


async def test_image_plugin_in_image_refused(
    env: Env, children: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _install_with_data(env)
    monkeypatch.setattr(runtime, "image_plugin_ids", lambda settings: {TABLES})
    r = await _uninstall(env, delete_data=True)
    assert r.status_code == 409
    assert r.json()["detail"] == IN_IMAGE_MESSAGE
    # a job queued before the image changed fails the same way and touches nothing
    async with env.factory() as s:
        from tiqora.jobs import enqueue_job

        queued = await enqueue_job(
            s,
            kind=install_jobs.PLUGIN_UNINSTALL_KIND,
            payload=install_jobs.PluginUninstallPayload(plugin_id=TABLES, delete_data=True),
            created_by=1,
        )
        await s.commit()
        job_id = queued.id
    await _run_jobs(env)
    done = await _job(env, job_id)
    assert (done.status, done.error) == ("failed", IN_IMAGE_MESSAGE)
    assert await _plugin_row(env) is not None
    assert await _count(env, "tqp_tables_note") == 2


async def test_image_plugin_gone_can_delete_data(env: Env) -> None:
    now = utc_now().replace(microsecond=0)
    async with env.factory() as s:
        s.add(
            TiqoraPlugin(
                id=TABLES,
                name="Tabellen-Erweiterung",
                version="1.0.0",
                enabled=True,
                source="image",
                signature_status="local",
                installed_at=now,
                updated_at=now,
                short_id="tables",
            )
        )
        await s.execute(
            text("CREATE TABLE tqp_tables_note (id INTEGER PRIMARY KEY, body VARCHAR(200))")
        )
        await s.execute(text("INSERT INTO tqp_tables_note (id, body) VALUES (1, 'alt')"))
        await s.commit()
    job = await _run_uninstall(env, delete_data=True, restart=False)
    assert job.status == "succeeded", job.error
    assert await _plugin_row(env) is None
    assert await _tables(env) == set()
    assert json.loads(job.result or "{}")["deleted_tables"] == ["tqp_tables_note"]


async def test_delete_data_without_short_id_fails(env: Env) -> None:
    now = utc_now().replace(microsecond=0)
    async with env.factory() as s:
        s.add(
            TiqoraPlugin(
                id=TABLES,
                name="Tabellen-Erweiterung",
                version="1.0.0",
                enabled=True,
                source="image",
                signature_status="local",
                installed_at=now,
                updated_at=now,
                short_id=None,
            )
        )
        await s.commit()
    job = await _run_uninstall(env, delete_data=True, restart=False)
    assert job.status == "failed"
    assert job.error == "Kurzname unbekannt – Daten können nicht zugeordnet werden"
    assert await _plugin_row(env) is not None


async def test_uninstall_in_safe_mode(env: Env, children: list[Any]) -> None:
    await _install_with_data(env)
    job = await _run_uninstall(
        env, settings={"plugins_safe_mode": True}, delete_data=True, restart=False
    )
    assert job.status == "succeeded", job.error
    assert await _plugin_row(env) is None
    assert await _tables(env) == set()


async def test_uninstall_endpoint_guards(env: Env, children: list[Any]) -> None:
    unknown = await _uninstall(env, "org.example.nirgends")
    assert unknown.status_code == 404
    await _install_with_data(env)
    forbidden = await _uninstall(env, user=KEY_USER)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "Nur in der Weboberfläche möglich"
    first = await _uninstall(env)
    second = await _uninstall(env)
    assert first.status_code == 202, first.text
    assert second.status_code == 409
    assert (
        second.json()["detail"]
        == "Für diese Erweiterung läuft bereits eine Installation oder Deinstallation"
    )
    assert await _plugin_row(env) is not None  # nothing happens before the job runs


async def test_active_uninstall_blocks_a_new_install_request(env: Env, children: list[Any]) -> None:
    await _install_with_data(env)
    assert (await _uninstall(env)).status_code == 202
    data, _sha = env.package("1.1.0")
    async with _client(_app(env)) as client:
        up = await client.post(BASE, files={"file": ("a.tqplugin", data, "application/zip")})
        r = await client.post(f"{BASE}/{up.json()['sha256']}/install", json={})
    assert r.status_code == 409


async def test_plugin_tables_rejects_a_bad_short_id(env: Env) -> None:
    async with env.factory() as s:
        for bad in ("x;drop", "Tables", "a", ""):
            with pytest.raises(migrations.PluginMigrationError):
                await migrations.plugin_tables(s, bad)
            with pytest.raises(migrations.PluginMigrationError):
                await migrations.drop_plugin_data(s, bad)


async def test_postgres_enum_types_dropped(env: Env, children: list[Any]) -> None:
    if not env.db_url.startswith("postgresql"):
        pytest.skip("PostgreSQL only: enum types")
    job = await _install(
        env,
        env.package(extra_members={f"{PACKAGE}/migrations/0002_kind.py": _KIND_MIGRATION})[0],
        restart=False,
    )
    assert job.status == "succeeded", job.error

    async def types() -> list[str]:
        async with env.factory() as s:
            return list(
                (
                    await s.execute(
                        text("SELECT typname FROM pg_type WHERE typname LIKE 'tqp\\_tables\\_%'")
                    )
                )
                .scalars()
                .all()
            )

    assert "tqp_tables_kind" in await types()
    done = await _run_uninstall(env, delete_data=True, restart=False)
    assert done.status == "succeeded", done.error
    assert await _tables(env) == set()
    assert "tqp_tables_kind" not in await types()
