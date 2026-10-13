"""``tiqora itsm`` CLI (Task 13; band 56320-56329).

The CLI enqueues the import job and waits for the worker; here the wait tick drains the queue
through the real job runner, so the same handler runs as in production.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests import _gi_mail_jobs
from tests._cmdb_helpers import to_async_url
from tests._itsm_import_fixtures import sample_source
from tests._itsm_import_job_helpers import enqueue_import, wipe
from tiqora.cli import itsm as cli_itsm
from tiqora.db.tiqora.models import TiqoraItsmImportRun
from tiqora.itsm.importer import facades
from tiqora.itsm.importer.jobs import IMPORT_JOB_KIND
from tiqora.main import main

pytestmark = pytest.mark.db


@dataclass
class _World:
    sync_url: str

    def factory(self) -> async_sessionmaker[AsyncSession]:
        # NullPool: the CLI runs each command in its own event loop (asyncio.run)
        engine = create_async_engine(to_async_url(self.sync_url), poolclass=NullPool)
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[_World]:
    sync_url: str = request.getfixturevalue(request.param)
    wipe(sync_url)
    w = _World(sync_url)
    monkeypatch.setattr(cli_itsm, "get_session_factory", w.factory)
    monkeypatch.setattr(_gi_mail_jobs, "GI_MAIL_KIND", IMPORT_JOB_KIND)

    async def drain() -> None:
        await _gi_mail_jobs.drain_with_factory(w.factory())

    monkeypatch.setattr(cli_itsm, "_wait_tick", drain)
    try:
        yield w
    finally:
        facades.source_override = None
        wipe(sync_url)


def _run_cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert isinstance(exc.value.code, int)
    return exc.value.code


def test_import_dry_run_prints_json_with_steps(
    world: _World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    assert _run_cli(["itsm", "import", "--dry-run", "--page-size", "3", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "dry_run" and out["status"] == "done"
    assert out["summary"]["steps"]["cis_pass2"]["created"] > 0
    assert out["job_id"] is not None


def test_import_prints_step_table(
    world: _World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    assert _run_cli(["itsm", "import"]) == 0
    out = capsys.readouterr().out
    assert "Status: done" in out and "cis_pass2" in out and "issues:" in out


def test_failed_import_exits_1_with_message(
    world: _World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source(profile=None))
    assert _run_cli(["itsm", "import"]) == 1
    out = capsys.readouterr().out
    assert "Status: failed" in out
    assert "In der Znuny-Datenbank wurde kein ITSM gefunden." in out


def test_import_refused_while_a_job_is_active(
    world: _World, capsys: pytest.CaptureFixture[str]
) -> None:
    asyncio.run(enqueue_import(world.factory(), dry_run=False, user_id=56320))
    assert _run_cli(["itsm", "import", "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "läuft bereits" in captured.err
    assert captured.out == ""


def test_import_refused_after_switch(world: _World, capsys: pytest.CaptureFixture[str]) -> None:
    engine = create_engine(world.sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tiqora_settings (`key`, value) VALUES ('itsm.switch', '{}')"
                    if conn.dialect.name == "mysql"
                    else "INSERT INTO tiqora_settings (key, value) VALUES ('itsm.switch', '{}')"
                )
            )
    finally:
        engine.dispose()
    assert _run_cli(["itsm", "import"]) == 1
    assert "bereits in Tiqora geführt" in capsys.readouterr().err


def test_status_prints_mode_and_last_run(
    world: _World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    assert _run_cli(["itsm", "status", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)
    assert before["last_run"] is None and before["active_job_id"] is None
    assert before["switched"] is False and before["mode"]
    assert _run_cli(["itsm", "import", "--dry-run"]) == 0
    capsys.readouterr()
    assert _run_cli(["itsm", "status"]) == 0
    text_out = capsys.readouterr().out
    assert f"mode: {before['mode']}" in text_out
    assert "dry_run done" in text_out

    async def last() -> TiqoraItsmImportRun:
        async with world.factory()() as s:
            return (
                (
                    await s.execute(
                        select(TiqoraItsmImportRun).order_by(TiqoraItsmImportRun.id.desc())
                    )
                )
                .scalars()
                .first()
            )  # type: ignore[return-value]

    assert asyncio.run(last()).started_by == 1


def test_itsm_without_subcommand_prints_help_and_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_cli(["itsm"]) == 2
    assert "import" in capsys.readouterr().out
