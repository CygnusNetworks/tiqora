"""HTTP tests for ``/api/v1/admin/itsm/import`` (both dialects; band 56370-56399).

The jobs are never run here (no worker): the endpoints only create the run row and enqueue.
Users 56370-56399 and their runs are removed again; rows of user 1 and the importer band are
wiped by ``tests._itsm_import_job_helpers.wipe``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests._cmdb_helpers import to_async_url
from tests._itsm_import_fixtures import sample_source
from tests._itsm_import_job_helpers import wipe
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.cmdb.gate import reset_mode_cache
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraItsmImportRun, TiqoraJob, TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.importer import facades
from tiqora.itsm.importer.issue_codes import ISSUE_CODES
from tiqora.itsm.importer.jobs import ItsmImportPayload
from tiqora.itsm.importer.switch import MODE_KEY, READONLY_KEY, SWITCH_KEY
from tiqora.jobs.service import enqueue_job, utc_now

pytestmark = pytest.mark.db

BASE = "/api/v1/admin/itsm/import"
ADMIN = AuthenticatedUser(
    id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
)
AGENT = AuthenticatedUser(
    id=56370, login="itim56.api", first_name="Ina", last_name="Agent", auth_method="session"
)
NAMED_USER = 56371
BAND_LO, BAND_HI = 56370, 56399
NO_ITSM = "In der Datenbank wurde kein Znuny ITSM gefunden."


@dataclass
class _Api:
    client: AsyncClient
    factory: async_sessionmaker[AsyncSession]
    sync_url: str


def _clean_band(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tiqora_itsm_import_run WHERE started_by BETWEEN :a AND :b"),
                {"a": BAND_LO, "b": BAND_HI},
            )
            conn.execute(
                text("DELETE FROM tiqora_job WHERE created_by BETWEEN :a AND :b"),
                {"a": BAND_LO, "b": BAND_HI},
            )
            conn.execute(
                text("DELETE FROM users WHERE id BETWEEN :a AND :b"), {"a": BAND_LO, "b": BAND_HI}
            )
    finally:
        engine.dispose()


def _seed_named_user(sync_url: str) -> None:
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 'itim56.named', 'x', 'Nora', 'Namensgeber', 1,"
                    " '2026-01-01 00:00:00', 1, '2026-01-01 00:00:00', 1)"
                ),
                {"id": NAMED_USER},
            )
    finally:
        engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> AsyncIterator[str]:
    url: str = request.getfixturevalue(request.param)
    wipe(url)
    _clean_band(url)
    reset_mode_cache()
    yield url  # type: ignore[misc]
    facades.source_override = None
    facades.znuny_present_override = None
    reset_mode_cache()
    _clean_band(url)
    wipe(url)


async def _api(url: str, *, admin: bool) -> AsyncIterator[_Api]:
    engine = create_async_engine(to_async_url(url), poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    if admin:
        app.dependency_overrides[get_admin_user] = lambda: ADMIN
    else:  # a plain agent: the real admin check runs against the database
        app.dependency_overrides[get_current_user] = lambda: AGENT
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield _Api(c, factory, url)
    await engine.dispose()


@pytest.fixture
async def admin(sync_url: str) -> AsyncIterator[_Api]:
    async for api in _api(sync_url, admin=True):
        yield api


@pytest.fixture
async def agent(sync_url: str) -> AsyncIterator[_Api]:
    async for api in _api(sync_url, admin=False):
        yield api


async def _put(api: _Api, key: str, value: str) -> None:
    async with api.factory() as s:
        row = await s.get(TiqoraSettings, key)
        if row is None:
            s.add(TiqoraSettings(key=key, value=value))
        else:
            row.value = value
        await s.commit()


async def _get(api: _Api, key: str) -> str | None:
    async with api.factory() as s:
        row = await s.get(TiqoraSettings, key)
        return row.value if row is not None else None


async def _mode_a(api: _Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facades, "source_override", sample_source())
    monkeypatch.setattr(facades, "znuny_present_override", True)
    await _put(api, MODE_KEY, "znuny_readthrough")
    reset_mode_cache()


async def _add_run(
    api: _Api,
    *,
    kind: str = "import",
    status: str = "done",
    user_id: int = 1,
    age_minutes: int = 0,
    summary: dict[str, object] | None = None,
) -> int:
    async with api.factory() as s:
        run = TiqoraItsmImportRun(
            kind=kind,
            status=status,
            job_id=None,
            started_by=user_id,
            started_at=utc_now() - timedelta(minutes=age_minutes),
            finished_at=utc_now() - timedelta(minutes=age_minutes),
            profile=None,
            summary=json.dumps(summary or {}),
            message=None,
        )
        s.add(run)
        await s.commit()
        return run.id


async def _jobs(api: _Api) -> list[TiqoraJob]:
    async with api.factory() as s:
        rows = await s.execute(
            select(TiqoraJob).where(TiqoraJob.kind.in_(["core.itsm_import", "core.itsm_switch"]))
        )
        return list(rows.scalars())


async def test_non_admin_is_forbidden_on_every_route(agent: _Api) -> None:
    c = agent.client
    responses = [
        await c.get(f"{BASE}/status"),
        await c.post(f"{BASE}/runs", json={"dry_run": True}),
        await c.get(f"{BASE}/runs"),
        await c.get(f"{BASE}/runs/1"),
        await c.post(f"{BASE}/switch", json={"confirm": "UMSTELLEN", "addon_installed": True}),
        await c.post(f"{BASE}/rollback", json={"confirm": "ZURÜCK"}),
        await c.post(f"{BASE}/release-lock"),
    ]
    assert [r.status_code for r in responses] == [403] * 7
    assert await _jobs(agent) == []


async def test_status_without_znuny_itsm(admin: _Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facades, "znuny_present_override", False)
    body = (await admin.client.get(f"{BASE}/status")).json()
    assert body["znuny_itsm_present"] is False
    assert body["can_import"] is False and body["can_switch"] is False
    assert body["blocked_reason"] == NO_ITSM
    assert body["switched"] is None and body["last_run"] is None
    assert body["znuny_counts"] is None and body["znuny_changes_since_switch"] is None


async def test_status_on_a_database_without_itsm_packages(admin: _Api) -> None:
    """No override: the real detection finds no ITSM packages in the Znuny test schema."""
    body = (await admin.client.get(f"{BASE}/status")).json()
    assert body["znuny_itsm_present"] is False and body["can_import"] is False
    assert body["blocked_reason"] == NO_ITSM


async def test_status_on_an_itsm_core_only_site(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R6: ITSMCore without the CMDB package has a profile and imports (no CI tables to count)."""
    await _mode_a(admin, monkeypatch)
    monkeypatch.setattr(facades, "source_override", sample_source(cmdb_installed=False))
    r = await admin.client.get(f"{BASE}/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["znuny_itsm_present"] is True and body["profile"] == "itsm-6"
    assert body["can_import"] is True and body["blocked_reason"] is None
    assert body["znuny_counts"] == {"cis": 0, "classes": 0, "changes": 3, "workorders": 5}


async def test_status_in_mode_a_reports_counts_and_last_run(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    run_id = await _add_run(
        admin,
        summary={
            "steps": {"cis_pass2": {"created": 10, "updated": 0, "unchanged": 0}},
            "issues": [
                {"step": "links", "code": "unknown_link_type", "object_ref": "1>2", "message": "m"}
            ],
            "issue_count": 1,
        },
    )
    body = (await admin.client.get(f"{BASE}/status")).json()
    assert body["znuny_itsm_present"] is True and body["mode"] == "znuny_readthrough"
    assert body["can_import"] is True and body["can_switch"] is True
    assert body["blocked_reason"] is None and body["readonly_flag"] is False
    assert body["znuny_counts"]["cis"] == 10 and body["znuny_counts"]["classes"] == 2
    last = body["last_run"]
    assert last["id"] == run_id and last["status"] == "done"
    assert last["steps"]["cis_pass2"]["created"] == 10
    assert last["issue_count"] == 1
    assert last["issues"][0]["explanation"] == ISSUE_CODES["unknown_link_type"]


async def test_status_flags_a_stuck_readonly_lock(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    await _put(admin, READONLY_KEY, "1")
    body = (await admin.client.get(f"{BASE}/status")).json()
    assert body["readonly_flag"] is True and body["can_import"] is False
    assert "Sperre" in body["blocked_reason"]


async def test_dry_run_is_queued_without_running(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    resp = await admin.client.post(f"{BASE}/runs", json={"dry_run": True})
    assert resp.status_code == 202
    accepted = resp.json()
    assert accepted["status"] == "queued"
    jobs = await _jobs(admin)
    assert [(j.id, j.kind, j.status) for j in jobs] == [
        (accepted["job_id"], "core.itsm_import", "queued")
    ]
    assert json.loads(jobs[0].payload)["dry_run"] is True
    runs = (await admin.client.get(f"{BASE}/runs")).json()
    assert len(runs) == 1
    assert runs[0]["kind"] == "dry_run" and runs[0]["status"] == "running"
    assert runs[0]["job_id"] == accepted["job_id"] and runs[0]["issue_count"] == 0
    assert json.loads(jobs[0].payload)["run_id"] == runs[0]["id"]
    # nothing was imported: the job has not run
    assert (await admin.client.get(f"{BASE}/status")).json()["active_job_id"] == accepted["job_id"]


async def test_second_start_while_active_is_a_conflict(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    assert (await admin.client.post(f"{BASE}/runs", json={"dry_run": True})).status_code == 202
    second = await admin.client.post(f"{BASE}/runs", json={"dry_run": False})
    assert second.status_code == 409
    assert "läuft bereits" in second.json()["detail"]
    assert len(await _jobs(admin)) == 1
    assert len((await admin.client.get(f"{BASE}/runs")).json()) == 1  # no orphan run row
    blocked = await admin.client.post(
        f"{BASE}/switch", json={"confirm": "UMSTELLEN", "addon_installed": True}
    )
    assert blocked.status_code == 409


async def test_start_without_znuny_itsm_is_a_conflict(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facades, "znuny_present_override", False)
    resp = await admin.client.post(f"{BASE}/runs", json={"dry_run": True})
    assert resp.status_code == 409 and resp.json()["detail"] == NO_ITSM
    assert await _jobs(admin) == []


async def test_import_after_the_switch_is_a_conflict(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    await _put(admin, MODE_KEY, "tiqora")
    await _put(
        admin,
        SWITCH_KEY,
        json.dumps({"switched_at": "2026-10-12T08:00:00", "by": 1, "run_id": 1}),
    )
    reset_mode_cache()
    resp = await admin.client.post(f"{BASE}/runs", json={"dry_run": False})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Die ITSM-Daten werden bereits in Tiqora geführt."
    status = (await admin.client.get(f"{BASE}/status")).json()
    assert status["switched"]["by"] == 1 and status["switched"]["run_id"] == 1
    assert status["switched"]["switched_at"].startswith("2026-10-12T08:00:00")
    assert status["znuny_changes_since_switch"] == 0


async def test_switch_with_wrong_confirmation_is_rejected(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    await _add_run(admin)
    resp = await admin.client.post(
        f"{BASE}/switch", json={"confirm": "falsch", "addon_installed": True}
    )
    assert resp.status_code == 400
    assert "UMSTELLEN" in resp.json()["detail"]
    assert await _jobs(admin) == []


async def test_switch_preconditions_give_409_with_the_blocked_message(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    body = {"confirm": "UMSTELLEN", "addon_installed": True}
    no_import = await admin.client.post(f"{BASE}/switch", json=body)
    assert no_import.status_code == 409
    assert "erfolgreichen Import" in no_import.json()["detail"]

    await _add_run(admin)
    no_stop = await admin.client.post(
        f"{BASE}/switch", json={"confirm": "UMSTELLEN", "addon_installed": False}
    )
    assert no_stop.status_code == 409 and "bearbeitet" in no_stop.json()["detail"]
    assert await _jobs(admin) == []


async def test_switch_is_queued_when_the_preconditions_hold(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    await _add_run(admin)
    resp = await admin.client.post(
        f"{BASE}/switch",
        json={"confirm": "UMSTELLEN", "addon_installed": False, "edits_stopped": True},
    )
    assert resp.status_code == 202
    jobs = await _jobs(admin)
    assert [(j.kind, j.status) for j in jobs] == [("core.itsm_switch", "queued")]
    payload = json.loads(jobs[0].payload)
    assert payload["addon_installed"] is False and payload["edits_stopped"] is True
    assert payload["without_ci_data"] is False
    runs = (await admin.client.get(f"{BASE}/runs")).json()
    assert runs[0]["kind"] == "switch" and runs[0]["status"] == "running"
    assert await _get(admin, READONLY_KEY) in (None, "0")  # only the job sets the flag


async def test_switch_without_ci_data_needs_no_done_import(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    monkeypatch.setattr(facades, "source_override", sample_source(profile=None))
    resp = await admin.client.post(
        f"{BASE}/switch",
        json={"confirm": "UMSTELLEN", "addon_installed": True, "without_ci_data": True},
    )
    assert resp.status_code == 202
    assert json.loads((await _jobs(admin))[0].payload)["without_ci_data"] is True


async def test_switch_without_ci_data_refused_on_a_readable_release(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Server-side guard: the option skips the import, so only an unreadable release may use it."""
    await _mode_a(admin, monkeypatch)
    await _add_run(admin)  # a done import: everything else would let the switch start
    resp = await admin.client.post(
        f"{BASE}/switch",
        json={"confirm": "UMSTELLEN", "addon_installed": True, "without_ci_data": True},
    )
    assert resp.status_code == 409
    assert "ohne CI-Daten" in resp.json()["detail"]
    assert await _jobs(admin) == []
    assert await _get(admin, READONLY_KEY) in (None, "0")


async def test_running_run_of_an_ended_job_is_shown_settled(admin: _Api) -> None:
    """A queued job cancelled before a worker claimed it never runs the handler."""
    gone = await _add_run(admin, status="running", user_id=NAMED_USER)
    cancelled = await _add_run(admin, status="running", user_id=NAMED_USER, age_minutes=1)
    live = await _add_run(admin, status="running", user_id=NAMED_USER, age_minutes=2)
    async with admin.factory() as s:
        job_ids = []
        for job_status in ("cancelled", "running"):
            job = await enqueue_job(
                s,
                kind="core.itsm_import",
                payload=ItsmImportPayload(user_id=NAMED_USER, dry_run=True, run_id=0),
                created_by=NAMED_USER,
            )
            job.status = job_status
            job_ids.append(job.id)
        await s.execute(
            update(TiqoraItsmImportRun)
            .where(TiqoraItsmImportRun.id == gone)
            .values(job_id=max(job_ids) + 1000)  # purged job
        )
        for run_id, job_id in ((cancelled, job_ids[0]), (live, job_ids[1])):
            await s.execute(
                update(TiqoraItsmImportRun)
                .where(TiqoraItsmImportRun.id == run_id)
                .values(job_id=job_id)
            )
        await s.commit()
    runs = {r["id"]: r for r in (await admin.client.get(f"{BASE}/runs")).json()}
    assert runs[gone]["status"] == "failed" and "nicht abgeschlossen" in runs[gone]["message"]
    assert runs[cancelled]["status"] == "cancelled"
    assert runs[live]["status"] == "running" and runs[live]["message"] is None
    one = (await admin.client.get(f"{BASE}/runs/{cancelled}")).json()
    assert one["status"] == "cancelled"
    status = (await admin.client.get(f"{BASE}/status")).json()
    assert status["last_run"]["id"] == gone and status["last_run"]["status"] == "failed"


async def test_runs_list_newest_first_with_started_by_name(admin: _Api, sync_url: str) -> None:
    _seed_named_user(sync_url)
    old = await _add_run(admin, kind="dry_run", user_id=NAMED_USER, age_minutes=30)
    new = await _add_run(admin, kind="import", user_id=1, age_minutes=1)
    runs = (await admin.client.get(f"{BASE}/runs")).json()
    assert [r["id"] for r in runs] == [new, old]
    assert runs[1]["started_by_name"] == "Nora Namensgeber"
    assert runs[0]["started_by_name"] is not None
    assert (await admin.client.get(f"{BASE}/runs", params={"limit": 1})).json()[0]["id"] == new
    assert (await admin.client.get(f"{BASE}/runs", params={"limit": 0})).status_code == 422
    one = await admin.client.get(f"{BASE}/runs/{old}")
    assert one.status_code == 200 and one.json()["started_by_name"] == "Nora Namensgeber"
    assert (await admin.client.get(f"{BASE}/runs/999999999")).status_code == 404


async def test_rollback_when_not_switched_is_a_conflict(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    resp = await admin.client.post(f"{BASE}/rollback", json={"confirm": "ZURÜCK"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Die ITSM-Daten wurden nicht auf Tiqora umgestellt."
    wrong = await admin.client.post(f"{BASE}/rollback", json={"confirm": "x"})
    assert wrong.status_code == 400


async def test_rollback_after_the_switch_returns_the_status(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    await _put(admin, MODE_KEY, "tiqora")
    await _put(admin, READONLY_KEY, "1")
    await _put(
        admin,
        SWITCH_KEY,
        json.dumps({"switched_at": "2026-10-12T08:00:00", "by": 1, "run_id": 1}),
    )
    reset_mode_cache()
    resp = await admin.client.post(f"{BASE}/rollback", json={"confirm": "ZURÜCK"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["switched"] is None and body["mode"] == "znuny_readthrough"
    assert body["readonly_flag"] is False and body["can_import"] is True
    assert body["last_run"]["kind"] == "rollback"
    assert await _get(admin, SWITCH_KEY) is None


async def test_release_lock_clears_a_stuck_flag_but_not_during_a_job(
    admin: _Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _mode_a(admin, monkeypatch)
    await _put(admin, READONLY_KEY, "1")
    released = await admin.client.post(f"{BASE}/release-lock")
    assert released.status_code == 200
    assert released.json()["readonly_flag"] is False and released.json()["can_import"] is True
    assert await _get(admin, READONLY_KEY) == "0"

    assert (await admin.client.post(f"{BASE}/runs", json={"dry_run": True})).status_code == 202
    await _put(admin, READONLY_KEY, "1")
    busy = await admin.client.post(f"{BASE}/release-lock")
    assert busy.status_code == 409
    assert await _get(admin, READONLY_KEY) == "1"
