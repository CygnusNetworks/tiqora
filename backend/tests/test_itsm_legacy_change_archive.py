"""The legacy change archive (Task 19): importer step, standalone job, archive source, GDPR.

Both dialects x both profiles (``itsm_db_url``). Every test runs in one outer transaction that
every session of the factory joins with a savepoint (a ``commit`` releases the savepoint only), so
the archive, the job, the routes and the erasure commit as in production and nothing is left
behind. The Znuny world is ``seed_legacy_world`` (band 946060-946099); this module's own rows use
947600-947699: Tiqora CI ids of the import map, run ids, a change template, history rows and a
customer company.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from tests._itsm_adapter_helpers import count_statements, seed_customer
from tests._itsm_db import ITSM_PARAMS, to_async_url
from tests._itsm_fake_provider import OPERATIONAL, FakeCiProvider
from tests._itsm_fixtures import seed_change_history
from tests._itsm_legacy_helpers import (
    CUSTOMER,
    FIRMWARE,
    GONE_CI,
    GROUP_A,
    MAIL,
    MANAGER,
    MEMBER,
    SERVICE,
    TICKET_A,
    TICKET_B,
    UPTIME,
    LegacyWorld,
    at,
    change_checksums,
    grant,
    group_id,
    seed_legacy_world,
)
from tests._itsm_mode_helpers import make_factory, plain_url, store
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraItsmImportMap, TiqoraJob, TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry, set_registry_for_tests
from tiqora.gdpr.erasure import rollback_job, run_erasure
from tiqora.itsm.change.legacy import reader
from tiqora.itsm.change.legacy.archive import (
    ARCHIVE_JOB_KIND,
    KIND_LEGACY_CHANGE,
    MODE_B_ONLY,
    NO_CHANGE_TABLES,
    ArchiveReport,
    LegacyArchivePayload,
    archive_blocker,
    archive_changes,
    change_from_data,
    run_legacy_archive_job,
    with_ci_refs,
)
from tiqora.itsm.change.legacy.sources import ArchiveSource, get_legacy_source, visible_links
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmLegacyChange,
    TiqoraItsmLegacyChangeRef,
)
from tiqora.itsm.ci_provider import CiRef
from tiqora.itsm.importer.mapstore import MapStore
from tiqora.itsm.importer.report import ImportReport
from tiqora.itsm.importer.steps import StepContext
from tiqora.itsm.importer.steps.cis import KIND_CI
from tiqora.itsm.importer.steps.delegated import STEP_CHANGES, run_changes
from tiqora.itsm.mode import SETTING_MODE
from tiqora.itsm.znuny_adapter.detect import clear_detect_cache
from tiqora.jobs.context import JobContext, JobFailed
from tiqora.jobs.registry import JOB_HANDLERS

pytestmark = pytest.mark.db

SETTINGS = Settings(environment="test")
ADMIN = 1
#: Tiqora CI ids the import map points the seeded Znuny CIs at
TIQORA_CI_VISIBLE, TIQORA_CI_HIDDEN = 947600, 947601
#: map run ids of the CI rows / of the archive runs
RUN_CIS, RUN_ARCHIVE = 947610, 947611
TEMPLATE_ID = 947620
NOTIFY_HISTORY, OTHER_NOTIFY_HISTORY = 947630, 947631
OTHER_ADDRESS = "chg94-other@example.org"
ARCHIVED = {MAIL, FIRMWARE, UPTIME}
_WRITE = re.compile(r"^(?:insert into|update|delete from) ([^\s(]+)")


async def _noop(*_args: Any) -> None:
    return None


@dataclass
class World:
    factory: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    seeded: LegacyWorld
    dialect: str
    profile: str


@asynccontextmanager
async def archive_world(
    request: pytest.FixtureRequest, dialect: str, profile: str, *, mode: str | None = "tiqora"
) -> AsyncIterator[World]:
    """The seeded Znuny changes on an ITSM database, the ITSM mode stored as ``mode``."""
    url = request.getfixturevalue("itsm_db_url")(dialect, profile)
    clear_detect_cache()
    set_registry_for_tests(None)  # the real registry (job handlers, CI providers)
    engine = create_async_engine(to_async_url(url), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            outer = await conn.begin()
            try:
                factory = async_sessionmaker(
                    conn,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                async with factory() as s:
                    seeded = await seed_legacy_world(s, profile)
                    if mode is not None:
                        s.add(TiqoraSettings(key=SETTING_MODE, value=mode))
                    await s.commit()
                yield World(factory, engine, seeded, dialect, profile)
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()
        set_registry_for_tests(None)
        clear_detect_cache()


async def _map_ci(w: World, znuny_id: int, tiqora_id: int) -> None:
    """What the CI steps of the importer leave behind for one CI."""
    async with w.factory() as s:
        await MapStore(s, run_id=RUN_CIS, read_only=False).put(
            KIND_CI, str(znuny_id), tiqora_id, "fp"
        )
        await s.commit()


async def _archive(w: World, *, dry_run: bool = False, page_size: int = 2) -> ArchiveReport:
    return await archive_changes(
        w.factory,
        run_id=RUN_ARCHIVE,
        dry_run=dry_run,
        page_size=page_size,
        progress=_noop,
        check_cancelled=_noop,
        report=ArchiveReport(),
    )


async def _rows(w: World) -> dict[int, TiqoraItsmLegacyChange]:
    async with w.factory() as s:
        found = (await s.execute(select(TiqoraItsmLegacyChange))).scalars().all()
    return {int(r.znuny_change_id): r for r in found}


async def _refs(w: World, znuny_id: int) -> set[tuple[str, str, int]]:
    async with w.factory() as s:
        row_id = (
            await s.execute(
                select(TiqoraItsmLegacyChange.id).where(
                    TiqoraItsmLegacyChange.znuny_change_id == znuny_id
                )
            )
        ).scalar_one()
        rows = await s.execute(
            select(
                TiqoraItsmLegacyChangeRef.ref_kind,
                TiqoraItsmLegacyChangeRef.ref_key,
                TiqoraItsmLegacyChangeRef.workorder_number,
            ).where(TiqoraItsmLegacyChangeRef.legacy_change_id == row_id)
        )
        return {(str(k), str(v), int(n)) for k, v, n in rows.all()}


async def _live(w: World, znuny_id: int) -> Any:
    async with w.factory() as s:
        return (await reader.read_changes(s, [znuny_id]))[0]


def _counts(report: ArchiveReport) -> tuple[int, int, int, int]:
    return report.created, report.updated, report.unchanged, report.skipped


# --- archive ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_archive_imports_changes_and_refs(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        ci = w.seeded
        await _map_ci(w, ci.ci_visible, TIQORA_CI_VISIBLE)
        async with w.factory() as s:
            changes_before = (
                await s.execute(select(func.count()).select_from(TiqoraItsmChange))
            ).scalar_one()

        report = await _archive(w)

        assert _counts(report) == (3, 0, 0, 0)
        rows = await _rows(w)
        assert set(rows) == ARCHIVED
        mail = rows[MAIL]
        assert (mail.change_number, mail.title, mail.state) == (
            "2026110194000070",
            "chg94 Mail relay replacement",
            "in progress",
        )
        assert (mail.impact, mail.priority) == ("4 high", "4 high")
        assert (mail.manager_login, mail.builder_login) == ("chg94.lmanager", "chg94.lagent")
        # the change window from the work orders (S2): the review has no planned times, so
        # neither planned start nor end; the earliest actual start counts, the end is open
        assert (mail.planned_start, mail.planned_end) == (None, None)
        assert (mail.actual_start, mail.actual_end) == (at(2, 8, 5), None)
        assert (mail.workorder_count, mail.import_run_id) == (3, RUN_ARCHIVE)
        assert rows[FIRMWARE].planned_start is None and rows[FIRMWARE].workorder_count == 0
        # CI refs: imported CI as tiqora:<id>, the others by their Znuny id
        assert await _refs(w, MAIL) == {
            ("ticket", str(TICKET_A), 0),
            ("ticket", str(TICKET_B), 0),
            ("ticket", str(TICKET_B), 2),
            ("ci", f"tiqora:{TIQORA_CI_VISIBLE}", 1),
            ("ci", f"znuny:{ci.ci_hidden}", 1),
            ("ci", f"znuny:{GONE_CI}", 1),
            ("ci", f"znuny:{GONE_CI}", 2),
            ("service", str(SERVICE), 2),
            ("customer_user", CUSTOMER, 0),
        }
        assert await _refs(w, UPTIME) == {("ticket", str(TICKET_B), 1)}
        assert await _refs(w, FIRMWARE) == set()
        # data is the reader's change with the stored CI keys, and round-trips exactly
        expected = with_ci_refs(await _live(w, MAIL), {str(ci.ci_visible): TIQORA_CI_VISIBLE})
        assert change_from_data(json.loads(mail.data)) == expected
        assert json.loads(mail.data)["v"] == 1
        # Znuny's "not set" sentinel never lands in the archive (NULL / null)
        for row in rows.values():
            assert "9999-01-01" not in json.dumps(
                {k: v for k, v in json.loads(row.data).items() if k != "history"}
            )
        # the idempotency map: one row per change, pointing at its archive row
        async with w.factory() as s:
            mapped = {
                m.source_id: (m.target_id, m.fingerprint, m.run_id)
                for m in (
                    await s.execute(
                        select(TiqoraItsmImportMap).where(
                            TiqoraItsmImportMap.source_kind == KIND_LEGACY_CHANGE
                        )
                    )
                ).scalars()
            }
            changes_after = (
                await s.execute(select(func.count()).select_from(TiqoraItsmChange))
            ).scalar_one()
        assert mapped == {str(z): (r.id, r.fingerprint, RUN_ARCHIVE) for z, r in rows.items()}
        assert changes_after == changes_before  # no Tiqora change record is written
        # templates, conditions and notification rules stay in Znuny (the package ships rules)
        assert set(report.not_archived) <= {"templates", "conditions", "notifications"}
        assert report.not_archived["notifications"] > 0


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_archive_rerun_unchanged(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        assert _counts(await _archive(w)) == (3, 0, 0, 0)
        first = {z: (r.id, r.data, r.imported_at) for z, r in (await _rows(w)).items()}

        assert _counts(await _archive(w, page_size=500)) == (0, 0, 3, 0)
        assert {z: (r.id, r.data, r.imported_at) for z, r in (await _rows(w)).items()} == first

        # a CI imported later: the unchanged change gets its tiqora: ref on the next run
        await _map_ci(w, w.seeded.ci_hidden, TIQORA_CI_HIDDEN)
        assert _counts(await _archive(w)) == (0, 1, 2, 0)
        refs = await _refs(w, MAIL)
        assert ("ci", f"tiqora:{TIQORA_CI_HIDDEN}", 1) in refs
        assert ("ci", f"znuny:{w.seeded.ci_hidden}", 1) not in refs
        assert (await _rows(w))[MAIL].id == first[MAIL][0]  # updated in place


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_archive_detects_changed_change(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        await _archive(w)
        before = await _rows(w)
        async with w.factory() as s:
            await s.execute(
                text("UPDATE change_item SET title = :t, change_time = :c WHERE id = :i"),
                {"t": "chg94 Firmware update v2", "c": at(6, 8), "i": FIRMWARE},
            )
            await s.execute(
                text("DELETE FROM change_cab WHERE change_id = :i AND customer_user_id = :c"),
                {"i": MAIL, "c": CUSTOMER},
            )
            await s.commit()

        assert _counts(await _archive(w)) == (0, 2, 1, 0)
        after = await _rows(w)
        assert after[FIRMWARE].title == "chg94 Firmware update v2"
        assert after[FIRMWARE].znuny_changed_at == at(6, 8)
        assert after[FIRMWARE].fingerprint != before[FIRMWARE].fingerprint
        assert after[UPTIME].data == before[UPTIME].data
        # the lookup rows are rebuilt: the customer left the CAB
        assert ("customer_user", CUSTOMER, 0) not in await _refs(w, MAIL)
        assert ("ticket", str(TICKET_A), 0) in await _refs(w, MAIL)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_dry_run_writes_nothing(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        report = await _archive(w, dry_run=True)
        assert _counts(report) == (3, 0, 0, 0)
        assert await _rows(w) == {}
        async with w.factory() as s:
            mapped = (
                await s.execute(
                    select(func.count())
                    .select_from(TiqoraItsmImportMap)
                    .where(TiqoraItsmImportMap.source_kind == KIND_LEGACY_CHANGE)
                )
            ).scalar_one()
            refs = (
                await s.execute(select(func.count()).select_from(TiqoraItsmLegacyChangeRef))
            ).scalar_one()
        assert (mapped, refs) == (0, 0)

        # after a real run a changed change is "updated" in a dry run, and stays as it was
        await _archive(w)
        before = {z: r.data for z, r in (await _rows(w)).items()}
        async with w.factory() as s:
            await s.execute(
                text("UPDATE change_item SET title = :t WHERE id = :i"),
                {"t": "chg94 dry", "i": UPTIME},
            )
            await s.commit()
        assert _counts(await _archive(w, dry_run=True)) == (0, 1, 2, 0)
        assert {z: r.data for z, r in (await _rows(w)).items()} == before


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_archive_writes_no_znuny_change_table(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            before = await change_checksums(s)
        with count_statements(w.engine) as seen:
            await _archive(w)
            await _archive(w)  # the unchanged path too
        async with w.factory() as s:
            assert await change_checksums(s) == before
        targets = [
            m.group(1).strip('"`')
            for st in seen
            if (m := _WRITE.match(" ".join(st.split()).lower())) is not None
        ]
        assert "tiqora_itsm_legacy_change" in targets  # the archive did write
        assert all(t.startswith("tiqora_") for t in targets), targets


# --- importer step ---------------------------------------------------------------------------


def _step_ctx(w: World, *, run_id: int, dry_run: bool = False) -> StepContext:
    return StepContext(
        session_factory=w.factory,
        ports_for=lambda _s: pytest.fail("the change step needs no ports"),
        run_id=run_id,
        dry_run=dry_run,
        page_size=500,
        report=ImportReport(),
        progress=_noop,
        check_cancelled=_noop,
        user_id=ADMIN,
        writer_for=lambda _s: pytest.fail("the change step writes no CI"),
        mapstore_for=lambda s: MapStore(s, run_id=run_id, read_only=dry_run),
    )


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_importer_step_reports_counts(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile, mode="znuny_readthrough") as w:
        async with w.factory() as s:
            type_id = (
                await s.execute(
                    text("SELECT id FROM change_template_type WHERE name = 'ITSMChange'")
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO change_template (id, name, comments, content, type_id,"
                    " valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:i, 'chg94 template', NULL, :c, :t, 1, :n, 1, :n, 1)"
                ),
                # Data::Dumper text: counted, never read (S4)
                {
                    "i": TEMPLATE_ID,
                    "c": "$VAR1 = { 'ChangeAdd' => {} };",
                    "t": type_id,
                    "n": at(1, 8),
                },
            )
            await s.commit()

        ctx = _step_ctx(w, run_id=RUN_ARCHIVE)
        await run_changes(ctx)
        counts = ctx.report.steps[STEP_CHANGES]
        assert (counts.created, counts.updated, counts.unchanged, counts.skipped) == (3, 0, 0, 0)
        issues = [i for i in ctx.report.issues if i.step == STEP_CHANGES]
        assert {i.code for i in issues} == {"not_archived"}
        by_kind = {i.object_ref: i.message for i in issues}
        assert by_kind["templates"].startswith("1 Change-Vorlagen in Znuny werden nicht übernommen")
        assert "notifications" in by_kind
        assert "kommt mit Phase 3" not in " ".join(by_kind.values())

        again = _step_ctx(w, run_id=RUN_ARCHIVE + 1)
        await run_changes(again)
        counts = again.report.steps[STEP_CHANGES]
        assert (counts.created, counts.updated, counts.unchanged) == (0, 0, 3)

        dry = _step_ctx(w, run_id=RUN_ARCHIVE + 2, dry_run=True)
        await run_changes(dry)
        assert dry.report.steps[STEP_CHANGES].unchanged == 3


# --- standalone job + admin API --------------------------------------------------------------


def _user(uid: int) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=uid, login=f"chg94.{uid}", first_name="Erika", last_name="Beispiel",
        auth_method="session",
    )  # fmt: skip


@asynccontextmanager
async def _client(w: World, uid: int) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        async with w.factory() as session:
            yield session

    app = create_app(SETTINGS)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: _user(uid)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _set_mode(w: World, mode: str) -> None:
    async with w.factory() as s:
        row = await s.get(TiqoraSettings, SETTING_MODE)
        assert row is not None
        row.value = mode
        await s.commit()
    clear_detect_cache()


def _job_ctx(w: World, job_id: int) -> JobContext:
    return JobContext(
        job_id=job_id,
        kind=ARCHIVE_JOB_KIND,
        created_by=ADMIN,
        settings=SETTINGS,
        session_factory=w.factory,
    )


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_standalone_job_only_in_mode_b(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    handler = get_registry().get(JOB_HANDLERS, ARCHIVE_JOB_KIND)
    assert handler is not None
    assert (handler.lane, handler.timeout_seconds, handler.max_attempts) == (
        "itsm_import",
        7200,
        2,
    )
    assert handler.requeue_if_interrupted
    assert handler.title_key == "jobs.kinds.core.itsm_legacy_change_archive"
    path = "/api/v1/admin/itsm/change/legacy-archive"

    async with archive_world(request, dialect, profile, mode="znuny_readthrough") as w:
        async with _client(w, ADMIN) as client:
            res = await client.post(path, json={})
            assert res.status_code == 409, res.text
            assert res.json()["detail"]["code"] == "legacy_archive_mode_b_only"
            status = (await client.get(path)).json()
            assert (status["can_start"], status["blocked_code"], status["archived"]) == (
                False,
                "legacy_archive_mode_b_only",
                0,
            )
        # the handler checks again (a job queued before the mode changed)
        with pytest.raises(JobFailed) as failed:
            await run_legacy_archive_job(_job_ctx(w, 0), LegacyArchivePayload(user_id=ADMIN))
        assert failed.value.message == MODE_B_ONLY.message
        assert await _rows(w) == {}

        await _set_mode(w, "tiqora")
        async with _client(w, MEMBER) as client:
            assert (await client.post(path, json={})).status_code == 403  # admins only
        async with _client(w, ADMIN) as client:
            res = await client.post(path, json={})
            assert res.status_code == 202, res.text
            job_id = res.json()["job_id"]
            assert res.json()["status"] == "queued"
            again = await client.post(path, json={"dry_run": True})
            assert again.json()["job_id"] == job_id  # one active archive job
            assert (await client.get(path)).json()["active_job_id"] == job_id

        async with w.factory() as s:
            job = await s.get(TiqoraJob, job_id)
            assert job is not None and job.kind == ARCHIVE_JOB_KIND
            payload = LegacyArchivePayload.model_validate_json(job.payload)
        result = await run_legacy_archive_job(_job_ctx(w, job_id), payload)
        assert result["created"] == 3 and result["dry_run"] is False
        assert set(await _rows(w)) == ARCHIVED
        assert {r.import_run_id for r in (await _rows(w)).values()} == {None}

        async with _client(w, ADMIN) as client:
            status = (await client.get(path)).json()
        assert (status["can_start"], status["blocked_code"], status["archived"]) == (True, None, 3)
        assert status["last_archived_at"] is not None

    # Mode B on a Znuny without Change Management (``plain_url`` cleans the settings up)
    url = plain_url(request, "mariadb_znuny_url" if dialect == "mysql" else "postgres_znuny_url")
    store(url, SETTING_MODE, "tiqora")
    engine, plain = make_factory(url)
    try:
        async with plain() as s:
            assert await archive_blocker(s) == NO_CHANGE_TABLES
    finally:
        await engine.dispose()


# --- archive source --------------------------------------------------------------------------


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_archive_source_search_get(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        ci = w.seeded
        await _map_ci(w, ci.ci_visible, TIQORA_CI_VISIBLE)
        async with w.factory() as s:
            assert await get_legacy_source(s, SETTINGS) is None  # Mode B, nothing archived yet
        await _archive(w)
        expected_mail = with_ci_refs(await _live(w, MAIL), {str(ci.ci_visible): TIQORA_CI_VISIBLE})

        async with w.factory() as s:
            source = await get_legacy_source(s, SETTINGS)
            assert isinstance(source, ArchiveSource) and source.kind == "archive"

            async def ids(*, limit: int = 25, offset: int = 0, **kw: Any) -> tuple[list[int], int]:
                params: dict[str, Any] = {"text": None, "ticket_id": None, "ci_ref": None, **kw}
                items, total = await source.search(s, limit=limit, offset=offset, **params)
                return [i.znuny_change_id for i in items], total

            assert await ids() == ([UPTIME, FIRMWARE, MAIL], 3)  # newest change first
            assert await ids(limit=1, offset=1) == ([FIRMWARE], 3)
            assert await ids(text="RELAY") == ([MAIL], 1)
            assert await ids(text="100%") == ([UPTIME], 1)  # % is literal
            assert await ids(text="2026110194000071") == ([FIRMWARE], 1)
            assert await ids(ticket_id=TICKET_B) == ([UPTIME, MAIL], 2)  # also via a work order
            assert await ids(ticket_id=TICKET_A, text="firmware") == ([], 0)
            assert await ids(ci_ref=CiRef("tiqora", TIQORA_CI_VISIBLE)) == ([MAIL], 1)
            assert await ids(ci_ref=CiRef("znuny", ci.ci_hidden)) == ([MAIL], 1)
            assert await ids(ci_ref=CiRef("znuny", ci.ci_visible)) == ([], 0)  # archived as tiqora:

            items, _ = await source.search(
                s, text="relay", ticket_id=None, ci_ref=None, limit=5, offset=0
            )
            summary = items[0]
            assert (summary.number, summary.state, summary.workorder_count) == (
                "2026110194000070",
                "in progress",
                3,
            )
            assert (summary.planned_start, summary.planned_end) == (None, None)
            assert [
                i.planned_start
                for i in (
                    await source.search(
                        s, text=None, ticket_id=None, ci_ref=None, limit=1, offset=0
                    )
                )[0]
            ] == [at(9, 18)]  # UPTIME: its one work order
            assert summary.changed_at == at(3, 8)

            assert await source.get(s, MAIL) == expected_mail
            assert await source.get(s, 946099) is None

            # F20 in Mode B: tiqora: refs through the provider, znuny: refs by their id only
            provider = FakeCiProvider("tiqora")
            provider.add_ci(TIQORA_CI_VISIBLE, "chg94-srv-01", OPERATIONAL)
            await grant(s, MEMBER, GROUP_A)
            await s.flush()
            links = await visible_links(s, expected_mail, user_id=MEMBER, provider=provider)
            assert [t.ticket_id for t in links.tickets] == [TICKET_A]
            shown = {(c.ref, c.name, c.number, c.workorder_number) for c in links.cis}
            assert shown == {
                (f"tiqora:{TIQORA_CI_VISIBLE}", "chg94-srv-01", f"2026{TIQORA_CI_VISIBLE:04d}", 1),
                (f"znuny:{ci.ci_hidden}", None, None, 1),
                (f"znuny:{GONE_CI}", None, None, 1),
                (f"znuny:{GONE_CI}", None, None, 2),
            }
            assert links.hidden == 1  # TICKET_B (queue B), counted once
            # a mapped CI the caller cannot read is left out
            hidden_provider = FakeCiProvider("tiqora")
            hidden_provider.add_ci(
                TIQORA_CI_VISIBLE, "chg94-srv-01", OPERATIONAL, hidden_for=[MEMBER]
            )
            links = await visible_links(s, expected_mail, user_id=MEMBER, provider=hidden_provider)
            assert f"tiqora:{TIQORA_CI_VISIBLE}" not in {c.ref for c in links.cis}
            assert links.hidden == 2
            change_group = await group_id(s, "itsm-change")
            await grant(s, MEMBER, change_group)
            await s.commit()

        # the read API answers from the archive in Mode B; the real Tiqora provider does not
        # know CI 947600 (no CMDB row), so that ref is hidden, the znuny: refs show their id
        async with _client(w, MEMBER) as client:
            page = (await client.get("/api/v1/itsm/legacy-changes")).json()
            assert page["source"] == "archive"
            assert [i["znuny_change_id"] for i in page["items"]] == [UPTIME, FIRMWARE, MAIL]
            res = await client.get(f"/api/v1/itsm/legacy-changes/{MAIL}")
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["source"] == "archive" and body["znuny_url"] is None
            assert {(c["ref"], c["number"]) for c in body["cis"]} == {
                (f"znuny:{ci.ci_hidden}", None),
                (f"znuny:{GONE_CI}", None),
            }
            assert body["hidden_links"] == 2  # TICKET_B and the unknown tiqora: CI
            assert [c["login"] for c in body["cab"] if c["kind"] == "customer_user"] == [CUSTOMER]


# --- GDPR ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_gdpr_erasure_anonymises_cab_customer(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            await seed_customer(s, CUSTOMER, "Erika", "Beispiel", "chg94-co-947640")
            # notification history: recipient address %% event (S5)
            for hid, address in ((NOTIFY_HISTORY, CUSTOMER), (OTHER_NOTIFY_HISTORY, OTHER_ADDRESS)):
                await seed_change_history(
                    s,
                    history_id=hid,
                    change_id=MAIL,
                    history_type="ChangeNotificationSent",
                    created_at=at(1, 12),
                    content_new=f"{address.upper() if hid == NOTIFY_HISTORY else address}"
                    "%%ChangeUpdate",
                    user_id=MANAGER,
                )
            customer_id = (
                await s.execute(
                    text("SELECT id FROM customer_user WHERE login = :l"), {"l": CUSTOMER}
                )
            ).scalar_one()
            await s.commit()
        await _archive(w)
        original = (await _rows(w))[MAIL]
        others = {z: r.data for z, r in (await _rows(w)).items() if z != MAIL}
        original_refs = await _refs(w, MAIL)
        assert CUSTOMER in original.data and ("customer_user", CUSTOMER, 0) in original_refs

        result = await run_erasure(
            w.factory,
            SETTINGS,
            customer_user_ids=[int(customer_id)],
            mode="anonymize",
            seed=7,
            force_parallel=True,
            actor="test",
        )

        assert result.counts["tiqora_itsm_legacy_change"] == 1
        assert result.counts["tiqora_itsm_legacy_change_ref"] == 1
        erased = (await _rows(w))[MAIL]
        assert CUSTOMER.lower() not in erased.data.lower()
        assert erased.fingerprint == original.fingerprint  # the next run does not re-read it
        change = change_from_data(json.loads(erased.data))
        customers = [login for kind, login in change.cab if kind == "customer_user"]
        assert len(customers) == 1 and customers[0] != CUSTOMER
        token = customers[0]
        assert [login for kind, login in change.cab if kind == "user"] == [
            "chg94.lmanager",
            "chg94.lagent",
        ]
        cab_rows = [h for h in change.history if h.field == "CABCustomers"]
        assert [(h.new, h.new_display) for h in cab_rows] == [(token, token)]
        sent = {h.new for h in change.history if h.history_type == "ChangeNotificationSent"}
        assert sent == {f"{token}%%ChangeUpdate", f"{OTHER_ADDRESS}%%ChangeUpdate"}
        assert ("customer_user", CUSTOMER, 0) not in await _refs(w, MAIL)
        assert {z: r.data for z, r in (await _rows(w)).items() if z != MAIL} == others
        assert _counts(await _archive(w)) == (0, 0, 3, 0)
        assert CUSTOMER.lower() not in (await _rows(w))[MAIL].data.lower()

        await rollback_job(w.factory, SETTINGS, result.job_id, force_parallel=True, actor="test")

        restored = (await _rows(w))[MAIL]
        assert (restored.data, restored.title) == (original.data, original.title)
        assert await _refs(w, MAIL) == original_refs
