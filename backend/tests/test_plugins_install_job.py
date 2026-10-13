"""Plugin install job and endpoint (D9) against real databases (both dialects).

Band 62160-62199: no seeded ids are needed - the job works on the fixture plugin
``org.example.tables`` (short id ``tables``, table ``tqp_tables_note``), which this module
cleans up (rows, tables, jobs, restart requests). The child processes of the job
(``plugins verify|migrate --path``) run in-process through ``main([...])`` in a thread; the
one test marked "real subprocess" uses the production runner.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugins_state_helpers import to_async_url
from tests.plugins.package_builder import build_tqplugin, build_wheel, make_keypair
from tiqora.api.app import create_app
from tiqora.api.deps import get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.config import Settings, get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraJob,
    TiqoraJobFile,
    TiqoraPlugin,
    TiqoraPluginData,
    TiqoraPluginHook,
    TiqoraPluginPackage,
    TiqoraRestart,
)
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.jobs import get_job
from tiqora.jobs.registry import JOB_HANDLERS, lane_slots
from tiqora.jobs.runner import JobRunner
from tiqora.jobs.service import utc_now
from tiqora.main import main
from tiqora.plugins import install_jobs, restart
from tiqora.plugins.discovery import clear_discovery_cache
from tiqora.plugins.inspect import UNSIGNED_PROBLEM
from tiqora.plugins.runtime import activate_paths, reset_dir_status_cache, version_dir

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/plugins/packages"
TABLES = "org.example.tables"
PACKAGE = "tq_fixture_tables"
ROOT = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
KEY_USER = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="api_key"
)

_BAD_MIGRATION = b"""
from alembic import op

revision = "tables_0002"
down_revision = "tables_0001"


def upgrade() -> None:
    op.execute("UPDATE ticket SET title = 'x'")


def downgrade() -> None:
    pass
"""
_BROKEN_INIT = b"raise RuntimeError('kaputtes Paket')\n"


@dataclass
class Env:
    factory: async_sessionmaker[AsyncSession]
    db_url: str
    plugins_dir: Path
    tmp: Path
    private: Any
    public: str
    key_id: str

    def settings(self, **kw: Any) -> Settings:
        values: dict[str, Any] = {
            "environment": "test",
            "database_url": self.db_url,
            "plugins_dir": str(self.plugins_dir),
            "plugins_trusted_keys": self.public,
        }
        values.update(kw)
        return Settings(**values)

    def package(
        self, version: str = "1.0.0", *, sign: bool = True, **wheel_kw: Any
    ) -> tuple[bytes, str]:
        """A .tqplugin of the fixture plugin and its sha256."""
        import hashlib

        wheel = build_wheel(self.tmp, PACKAGE, plugin_id=TABLES, version=version, **wheel_kw)
        data = build_tqplugin(self.tmp, wheel, sign_with=self.private if sign else None)
        raw = data.read_bytes()
        return raw, hashlib.sha256(raw).hexdigest()


def _purge_modules() -> None:
    for name in [m for m in sys.modules if m == PACKAGE or m.startswith(PACKAGE + ".")]:
        del sys.modules[name]
    clear_discovery_cache()


async def _drop_tables(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        names = await s.run_sync(lambda c: inspect(c.connection()).get_table_names())
        mine = [n for n in names if n.startswith("tqp_tables_")]
        if not mine:
            return
        if s.get_bind().dialect.name == "mysql":
            await s.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for name in mine:
            await s.execute(text(f"DROP TABLE IF EXISTS {name}"))
        if s.get_bind().dialect.name == "mysql":
            await s.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        await s.commit()


async def _clean(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        ids = (
            (
                await s.execute(
                    select(TiqoraJob.id).where(
                        TiqoraJob.kind.in_(
                            (install_jobs.PLUGIN_INSTALL_KIND, install_jobs.PLUGIN_UNINSTALL_KIND)
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await s.execute(delete(TiqoraJobFile).where(TiqoraJobFile.job_id.in_(ids)))
            await s.execute(delete(TiqoraJob).where(TiqoraJob.id.in_(ids)))
        await s.execute(delete(TiqoraRestart).where(TiqoraRestart.reason.like("Erweiterung „%")))
        await s.execute(delete(TiqoraPluginHook).where(TiqoraPluginHook.plugin_id == TABLES))
        await s.execute(delete(TiqoraPluginPackage).where(TiqoraPluginPackage.plugin_id == TABLES))
        await s.execute(delete(TiqoraPlugin).where(TiqoraPlugin.id == TABLES))
        await s.execute(delete(TiqoraPluginData).where(TiqoraPluginData.short_id == "tables"))
        await s.commit()
    await _drop_tables(factory)


@pytest.fixture(name="env", params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env_fixture(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    db_url = to_async_url(request.getfixturevalue(request.param))
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _clean(sm)
    # the in-process "child processes" read the database from the environment
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.delenv("TIQORA_PLUGINS_SAFE_MODE", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(sys, "path", list(sys.path))
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    reset_dir_status_cache()
    _purge_modules()
    private, pub, kid = make_keypair()
    try:
        yield Env(sm, db_url, plugins_dir, tmp_path, private, pub, kid)
    finally:
        _purge_modules()
        reset_dir_status_cache()
        await _clean(sm)
        await engine.dispose()
        get_settings.cache_clear()


def _inprocess_child(args: list[str]) -> tuple[int, str]:
    """Run ``python -m tiqora.main <args>`` in this process (in a worker thread)."""
    saved_path = list(sys.path)
    _purge_modules()
    out = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(out):
            try:
                main(args)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
    finally:
        sys.path[:] = saved_path
        _purge_modules()
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    return code, "\n".join(lines[-20:])


@pytest.fixture
def children(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, str]]]:
    """Replace the subprocess runner by the in-process CLI; records ``(args, env)`` per call."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    async def fake(
        argv: Sequence[str],
        timeout: float,  # noqa: ASYNC109 - same signature as the real runner
        *,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, str]:
        assert list(argv[1:3]) == ["-m", "tiqora.main"]
        args = list(argv[3:])
        calls.append((args, dict(env or {})))
        return await asyncio.to_thread(_inprocess_child, args)

    monkeypatch.setattr(install_jobs, "_run_subprocess", fake)
    return calls


def _app(env: Env, *, user: AuthenticatedUser = ROOT, **settings: Any) -> Any:
    async def _db() -> Any:
        async with env.factory() as session:
            yield session

    app = create_app(env.settings(**settings))
    app.state.start_restart_watch = False
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_admin_user] = lambda: user
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _upload(env: Env, data: bytes) -> str:
    async with _client(_app(env)) as client:
        r = await client.post(BASE, files={"file": ("a.tqplugin", data, "application/zip")})
    assert r.status_code in (200, 201), r.text
    return str(r.json()["sha256"])


async def _post_install(env: Env, sha: str, *, user: AuthenticatedUser = ROOT, **body: Any) -> Any:
    async with _client(_app(env, user=user)) as client:
        return await client.post(f"{BASE}/{sha}/install", json=body or {})


async def _run_jobs(env: Env, **settings: Any) -> None:
    reg = Registry()
    register_core(reg)
    runner = JobRunner(
        session_factory=env.factory,
        settings=env.settings(**settings),
        registry=reg,
        worker_id="t:1",
    )
    await runner.run_once()
    await runner.wait_idle()


async def _job(env: Env, job_id: int) -> TiqoraJob:
    async with env.factory() as s:
        job = await get_job(s, job_id)
    assert job is not None
    return job


async def _install(env: Env, data: bytes, **body: Any) -> TiqoraJob:
    """Upload, POST install, run the job; returns the finished job."""
    sha = await _upload(env, data)
    r = await _post_install(env, sha, **body)
    assert r.status_code == 202, r.text
    await _run_jobs(env)
    return await _job(env, r.json()["job_id"])


async def _plugin_row(env: Env) -> TiqoraPlugin | None:
    async with env.factory() as s:
        return (
            await s.execute(select(TiqoraPlugin).where(TiqoraPlugin.id == TABLES))
        ).scalar_one_or_none()


async def _tables(env: Env) -> set[str]:
    async with env.factory() as s:
        names = await s.run_sync(lambda c: inspect(c.connection()).get_table_names())
    return {n for n in names if n.startswith("tqp_tables_")}


async def _restart_rows(env: Env) -> list[TiqoraRestart]:
    async with env.factory() as s:
        return list(
            (
                await s.execute(
                    select(TiqoraRestart).where(TiqoraRestart.reason.like("Erweiterung „%"))
                )
            )
            .scalars()
            .all()
        )


def test_install_job_kind_is_a_plugins_lane_kind() -> None:
    # restart.py spells the kinds out (import cycle); they must stay the install_jobs constants
    assert set(restart.PLUGIN_JOB_KINDS) == {
        install_jobs.PLUGIN_INSTALL_KIND,
        install_jobs.PLUGIN_UNINSTALL_KIND,
        install_jobs.PLUGIN_FETCH_KIND,
        install_jobs.PLUGIN_REPOSITORY_REFRESH_KIND,
    }
    assert len(restart.PLUGIN_JOB_KINDS) == 4
    reg = Registry()
    register_core(reg)
    handler = reg.get(JOB_HANDLERS, "core.plugin_install")
    assert handler is not None
    assert handler.lane == "plugins" and lane_slots("plugins", 4) == 1
    assert (handler.max_attempts, handler.requeue_if_interrupted, handler.result_private) == (
        1,
        False,
        False,
    )
    assert handler.timeout_seconds == 1500
    assert handler.title_key == "jobs.kinds.core.plugin_install"


async def test_install_new_plugin_end_to_end(env: Env, children: list[Any]) -> None:
    data, sha = env.package()
    job = await _install(env, data, enable=True, restart=True)
    assert job.status == "succeeded", job.error
    result = json.loads(job.result or "{}")
    row = await _plugin_row(env)
    assert row is not None
    assert (row.source, row.version, row.package_sha256) == ("upload", "1.0.0", sha)
    assert (row.signature_status, row.enabled, row.installed_by) == ("local", True, 1)
    assert (row.migrated_version, row.migrated_head, row.migration_error) == (
        "1.0.0",
        "tables_0001",
        None,
    )
    assert row.key_id == env.key_id and row.short_id == "tables"
    assert "tqp_tables_note" in await _tables(env)
    # the unpacked package is on the volume
    assert (version_dir(env.settings(), TABLES, "1.0.0", sha) / "site" / PACKAGE).is_dir()
    restarts = await _restart_rows(env)
    assert (
        len(restarts) == 1
        and restarts[0].reason == "Erweiterung „Tabellen-Erweiterung“ 1.0.0 installiert"
    )
    assert result == {
        "plugin_id": TABLES,
        "version": "1.0.0",
        "action": "install",
        "restart_generation": restarts[0].generation,
        "migrated_head": "tables_0001",
    }
    async with env.factory() as s:
        stored = await s.get(TiqoraPluginPackage, sha)
    assert stored is not None and stored.installed_at is not None
    # the checks ran on the unpacked site, one after the other
    assert [c[0][1] for c in children] == ["verify", "migrate"]
    assert "--path" in children[0][0] and "--only" in children[0][0]


async def test_children_get_the_database_and_no_safe_mode(
    env: Env, children: list[tuple[list[str], dict[str, str]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIQORA_PLUGINS_SAFE_MODE", "1")
    data, _sha = env.package()
    job = await _install(env, data, restart=False)
    assert job.status == "succeeded", job.error
    for _args, child_env in children:
        assert child_env["DATABASE_URL"] == env.db_url
        assert "TIQORA_PLUGINS_SAFE_MODE" not in child_env


async def test_install_without_restart(env: Env, children: list[Any]) -> None:
    data, _sha = env.package()
    job = await _install(env, data, enable=False, restart=False)
    assert job.status == "succeeded", job.error
    assert json.loads(job.result or "{}")["restart_generation"] is None
    assert await _restart_rows(env) == []
    row = await _plugin_row(env)
    assert row is not None and row.enabled is False  # "enable" applies to a new row


async def test_install_refused_when_dir_unusable(env: Env, tmp_path: Path) -> None:
    data, _sha = env.package()
    missing = str(tmp_path / "gibt-es-nicht")
    async with _client(_app(env, plugins_dir=missing)) as client:
        up = await client.post(BASE, files={"file": ("a.tqplugin", data, "application/zip")})
        sha = up.json()["sha256"]
        r = await client.post(f"{BASE}/{sha}/install", json={})
    assert r.status_code == 409
    assert "existiert nicht" in r.json()["detail"]
    async with env.factory() as s:
        kinds = (await s.execute(select(TiqoraJob.kind))).scalars().all()
    assert install_jobs.PLUGIN_INSTALL_KIND not in kinds


async def test_install_refused_when_not_installable(env: Env) -> None:
    data, _sha = env.package(sign=False)
    sha = await _upload(env, data)
    r = await _post_install(env, sha)
    assert r.status_code == 409
    assert UNSIGNED_PROBLEM in r.json()["detail"]
    assert await _plugin_row(env) is None


async def test_install_unknown_package_404(env: Env) -> None:
    r = await _post_install(env, "0" * 64)
    assert r.status_code == 404


async def test_install_needs_a_browser_session(env: Env) -> None:
    data, _sha = env.package()
    sha = await _upload(env, data)
    r = await _post_install(env, sha, user=KEY_USER)
    assert r.status_code == 403
    assert r.json()["detail"] == "Nur in der Weboberfläche möglich"


async def test_second_install_for_same_plugin_conflicts(env: Env) -> None:
    data, _sha = env.package()
    sha = await _upload(env, data)
    first = await _post_install(env, sha)
    second = await _post_install(env, sha)
    assert first.status_code == 202, first.text
    assert second.status_code == 409
    assert (
        second.json()["detail"]
        == "Für diese Erweiterung läuft bereits eine Installation oder Deinstallation"
    )


async def test_active_uninstall_blocks_install(env: Env) -> None:
    data, _sha = env.package()
    sha = await _upload(env, data)
    async with env.factory() as s:
        s.add(
            TiqoraJob(
                kind=install_jobs.PLUGIN_UNINSTALL_KIND,
                status="queued",
                payload="{}",
                progress_done=0,
                cancel_requested=False,
                dedupe_key=install_jobs.dedupe_key(TABLES),
                created_by=1,
                created_at=utc_now(),
            )
        )
        await s.commit()
    r = await _post_install(env, sha)
    assert r.status_code == 409


async def test_job_for_a_vanished_package_fails_cleanly(env: Env, children: list[Any]) -> None:
    async with env.factory() as s:
        from tiqora.jobs import enqueue_job

        job = await enqueue_job(
            s,
            kind=install_jobs.PLUGIN_INSTALL_KIND,
            payload=install_jobs.PluginInstallPayload(package_sha256="f" * 64),
            created_by=1,
        )
        await s.commit()
        job_id = job.id
    await _run_jobs(env)
    done = await _job(env, job_id)
    assert done.status == "failed"
    assert done.error == "Paket nicht mehr vorhanden – bitte erneut hochladen"
    assert children == []


async def test_verify_failure_keeps_old_version(env: Env, children: list[Any]) -> None:
    good, good_sha = env.package("1.0.0")
    first = await _install(env, good, restart=False)
    assert first.status == "succeeded", first.error
    broken, _ = env.package("1.1.0", extra_members={f"{PACKAGE}/__init__.py": _BROKEN_INIT})
    job = await _install(env, broken, restart=False)
    assert job.status == "failed"
    assert (job.error or "").startswith("Prüfung fehlgeschlagen: ")
    assert "kaputtes Paket" in (job.error or "")
    row = await _plugin_row(env)
    assert row is not None
    assert (row.version, row.package_sha256, row.migrated_version) == ("1.0.0", good_sha, "1.0.0")
    # the migration child never ran for the broken package
    assert [c[0][1] for c in children] == ["verify", "migrate", "verify"]


async def test_verify_failure_on_a_fresh_install_leaves_nothing(
    env: Env, children: list[Any]
) -> None:
    broken, _ = env.package(extra_members={f"{PACKAGE}/__init__.py": _BROKEN_INIT})
    job = await _install(env, broken)
    assert job.status == "failed" and (job.error or "").startswith("Prüfung fehlgeschlagen")
    assert await _plugin_row(env) is None
    assert await _tables(env) == set()
    assert await _restart_rows(env) == []


async def test_migration_failure_keeps_old_version(env: Env, children: list[Any]) -> None:
    good, good_sha = env.package("1.0.0")
    assert (await _install(env, good, restart=False)).status == "succeeded"
    bad, _ = env.package(
        "1.1.0", extra_members={f"{PACKAGE}/migrations/0002_bad.py": _BAD_MIGRATION}
    )
    job = await _install(env, bad, restart=False)
    assert job.status == "failed"
    assert (job.error or "").startswith("Datenbank-Änderungen fehlgeschlagen: ")
    row = await _plugin_row(env)
    assert row is not None
    assert (row.version, row.package_sha256) == ("1.0.0", good_sha)
    # the failed attempt is not written onto the healthy installed version: the load gate
    # and the boot migrations must keep treating 1.0.0 as fine
    assert row.migration_error is None
    assert row.migrated_version == "1.0.0"
    assert await _restart_rows(env) == []


async def test_failed_fresh_install_can_be_retried_with_a_fixed_package(
    env: Env, children: list[Any]
) -> None:
    bad, _ = env.package(
        "1.0.0", extra_members={f"{PACKAGE}/migrations/0002_bad.py": _BAD_MIGRATION}
    )
    job = await _install(env, bad, restart=False)
    assert job.status == "failed"
    assert await _plugin_row(env) is None
    # the failed run owns the short id while tables are left (MariaDB keeps the DDL);
    # PostgreSQL rolled them back, so the claim was given back
    async with env.factory() as s:
        owner = await s.get(TiqoraPluginData, "tables")
    if await _tables(env):
        assert owner is not None and owner.plugin_id == TABLES
    else:
        assert owner is None
    fixed, _ = env.package("1.0.1")
    job = await _install(env, fixed, restart=False)
    assert job.status == "succeeded", job.error
    row = await _plugin_row(env)
    assert row is not None and row.version == "1.0.1" and row.migration_error is None
    async with env.factory() as s:
        assert await s.get(TiqoraPluginData, "tables") is None


async def test_failed_fresh_install_without_tables_gives_the_short_id_back(
    env: Env, children: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 15 M8: the migrate child died before creating a table -> no claim is left."""
    inprocess = install_jobs._run_subprocess

    async def migrate_crashes(
        argv: Sequence[str],
        timeout: float,  # noqa: ASYNC109 - same signature as the real runner
        *,
        env: Mapping[str, str] | None = None,
    ) -> tuple[int, str]:
        if "migrate" in argv:
            return 1, "Speicherzugriffsfehler"
        return await inprocess(argv, timeout, env=env)

    monkeypatch.setattr(install_jobs, "_run_subprocess", migrate_crashes)
    data, _ = env.package("1.0.0")
    job = await _install(env, data, restart=False)
    assert job.status == "failed"
    assert (job.error or "").startswith("Datenbank-Änderungen fehlgeschlagen")
    assert await _plugin_row(env) is None and await _tables(env) == set()
    async with env.factory() as s:
        assert await s.get(TiqoraPluginData, "tables") is None


async def test_update_keeps_enabled_and_hook_cursors(env: Env, children: list[Any]) -> None:
    first = await _install(env, env.package("1.0.0")[0], enable=False, restart=False)
    assert first.status == "succeeded", first.error
    async with env.factory() as s:
        s.add(
            TiqoraPluginHook(
                hook_key=f"event:{TABLES}:note",
                plugin_id=TABLES,
                kind="event",
                cursor=42,
                created_at=utc_now().replace(microsecond=0),
            )
        )
        await s.commit()
    data, sha = env.package("1.1.0")
    job = await _install(env, data, enable=True, restart=False)
    assert job.status == "succeeded", job.error
    assert json.loads(job.result or "{}")["action"] == "update"
    row = await _plugin_row(env)
    assert row is not None
    assert (row.version, row.package_sha256, row.enabled) == ("1.1.0", sha, False)
    async with env.factory() as s:
        hook = await s.get(TiqoraPluginHook, f"event:{TABLES}:note")
    assert hook is not None and hook.cursor == 42


async def test_trust_rechecked_in_job(env: Env, children: list[Any]) -> None:
    data, _sha = env.package()
    sha = await _upload(env, data)  # the key is trusted now
    r = await _post_install(env, sha)
    assert r.status_code == 202
    await _run_jobs(env, plugins_trusted_keys="")  # ... and no longer when the job runs
    job = await _job(env, r.json()["job_id"])
    assert job.status == "failed"
    assert UNSIGNED_PROBLEM in (job.error or "")
    assert children == []
    assert await _plugin_row(env) is None


async def test_install_adopts_kept_data_of_the_same_plugin(env: Env, children: list[Any]) -> None:
    async with env.factory() as s:
        s.add(
            TiqoraPluginData(
                short_id="tables", plugin_id=TABLES, kept_at=utc_now().replace(microsecond=0)
            )
        )
        await s.commit()
    job = await _install(env, env.package()[0], restart=False)
    assert job.status == "succeeded", job.error
    async with env.factory() as s:
        assert await s.get(TiqoraPluginData, "tables") is None


async def test_update_checks_the_new_code_not_the_installed_one(
    env: Env, children: list[Any]
) -> None:
    # 1.0.0 is installed and its site dir is what activation would have on sys.path
    good, good_sha = env.package("1.0.0")
    assert (await _install(env, good, restart=False)).status == "succeeded"
    activate_paths([version_dir(env.settings(), TABLES, "1.0.0", good_sha) / "site"])
    clear_discovery_cache()
    broken, _ = env.package("1.1.0", extra_members={f"{PACKAGE}/__init__.py": _BROKEN_INIT})
    job = await _install(env, broken, restart=False)
    assert job.status == "failed"
    assert "kaputtes Paket" in (job.error or "")  # the child imported 1.1.0, not 1.0.0


async def test_update_of_a_broken_installed_version_succeeds_with_good_code(
    env: Env, children: list[Any]
) -> None:
    broken, broken_sha = env.package(
        "1.0.0", extra_members={f"{PACKAGE}/__init__.py": _BROKEN_INIT}
    )
    # the broken 1.0.0 is "installed": materialised, on sys.path, with its row
    from tiqora.plugins.runtime import materialize

    site = materialize(env.settings(), TABLES, "1.0.0", broken_sha, broken)
    activate_paths([site])
    clear_discovery_cache()
    now = utc_now().replace(microsecond=0)
    async with env.factory() as s:
        s.add(
            TiqoraPlugin(
                id=TABLES,
                name="Tabellen-Erweiterung",
                version="1.0.0",
                enabled=False,
                source="upload",
                signature_status="local",
                installed_at=now,
                updated_at=now,
                short_id="tables",
                package_sha256=broken_sha,
            )
        )
        await s.commit()
    good, good_sha = env.package("1.1.0")
    job = await _install(env, good, restart=False)
    assert job.status == "succeeded", job.error
    row = await _plugin_row(env)
    assert row is not None and (row.version, row.package_sha256) == ("1.1.0", good_sha)


async def test_real_subprocess_install(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """The production runner: two real ``python -m tiqora.main`` children."""
    # the children inherit the test process's import path through PYTHONPATH
    monkeypatch.setenv("TIQORA_ENVIRONMENT", "test")
    data, sha = env.package()
    job = await _install(env, data, restart=False)
    assert job.status == "succeeded", job.error
    row = await _plugin_row(env)
    assert row is not None and (row.version, row.migrated_head) == ("1.0.0", "tables_0001")
    assert "tqp_tables_note" in await _tables(env)
    assert sha == row.package_sha256


async def test_subprocess_runner_kills_on_timeout_and_reports_exit(
    tmp_path: Path,
) -> None:
    code, out = await install_jobs._run_subprocess(
        [sys.executable, "-c", "import time; time.sleep(30)"], 0.5
    )
    assert code == install_jobs.TIMEOUT_EXIT_CODE and "länger als 0 Sekunden" in out
    code, out = await install_jobs._run_subprocess(
        [sys.executable, "-c", "import sys; print('a'); print('b', file=sys.stderr); sys.exit(3)"],
        20,
    )
    assert code == 3 and set(out.split()) == {"a", "b"}
