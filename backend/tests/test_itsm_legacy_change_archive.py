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
from sqlalchemy import delete, func, insert, select, text
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
from tests._itsm_fixtures import seed_change, seed_change_cab, seed_change_history
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
from tiqora.db.tiqora.models import (
    TiqoraGdprJob,
    TiqoraItsmImportMap,
    TiqoraJob,
    TiqoraSettings,
)
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry, set_registry_for_tests
from tiqora.gdpr.anonymize import anonymize_customer
from tiqora.gdpr.erasure import ErasureError, rollback_job, run_erasure
from tiqora.itsm.change.legacy import reader
from tiqora.itsm.change.legacy.archive import (
    ARCHIVE_JOB_KIND,
    KEY_CHANGED_COUNT,
    KEY_CHANGED_MESSAGE,
    KEY_CHANGED_WARNING,
    KEY_CHECK_KIND,
    KIND_LEGACY_CHANGE,
    MODE_B_ONLY,
    NO_CHANGE_TABLES,
    ArchiveReport,
    ErasedKeyChanged,
    LegacyArchivePayload,
    archive_blocker,
    archive_changes,
    change_from_data,
    erase_legacy_change_customers,
    run_legacy_archive_job,
    with_ci_refs,
)
from tiqora.itsm.change.legacy.redact import (
    REDACTED,
    MappingRedactor,
    TombstoneRedactor,
    erased_hash,
    erased_key,
    key_check,
    redact_data,
)
from tiqora.itsm.change.legacy.sources import ArchiveSource, get_legacy_source, visible_links
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmLegacyChange,
    TiqoraItsmLegacyChangeRef,
    TiqoraItsmLegacyErased,
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
#: a customer whose login is not its address (S5 "[gelöscht]" path)
CUSTOMER2, CUSTOMER2_MAIL = "chg94.kunde", "chg94-kunde@example.org"
CAB2, CAB2_HISTORY, CAB2_NOTIFY = 947632, 947633, 947634
#: a change first seen after the erasure: id, CAB row, history row
LATE_CHANGE, LATE_CAB, LATE_NOTIFY = 947650, 947651, 947652
#: a second customer sharing CUSTOMER2's address; RichText history rows
CUSTOMER3 = "chg94.kundin"
CAB3, SHARED_NOTIFY = 947635, 947636
RICH_HISTORY, RICH_HISTORY_2 = 947637, 947638
#: a customer whose login equals a ticket id
NUMERIC_LOGIN, NUMERIC_MAIL = str(TICKET_A), "chg94-num@example.org"
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
        settings=SETTINGS,
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


def _step_ctx(
    w: World, *, run_id: int, dry_run: bool = False, settings: Settings = SETTINGS
) -> StepContext:
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
        settings=settings,
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
            assert (await client.post(path, json={})).json()["job_id"] == job_id  # one active
            dry = (await client.post(path, json={"dry_run": True})).json()["job_id"]
            assert dry != job_id  # a dry run never joins a real run (and the other way round)
            assert (await client.post(path, json={"dry_run": True})).json()["job_id"] == dry
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
        assert result.counts["tiqora_itsm_legacy_erased"] == 1  # login == address: one hash
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
        assert (restored.data, restored.title, restored.fingerprint) == (
            original.data,
            original.title,
            original.fingerprint,
        )
        assert await _refs(w, MAIL) == original_refs
        assert await _tombstones(w) == []  # the erasure's tombstone went with the rollback


async def _tombstones(w: World) -> list[tuple[str, str]]:
    """The tombstones (not the key check row)."""
    async with w.factory() as s:
        rows = await s.execute(
            select(TiqoraItsmLegacyErased.identifier_hash, TiqoraItsmLegacyErased.kind).where(
                TiqoraItsmLegacyErased.kind != KEY_CHECK_KIND
            )
        )
        return sorted((str(h).strip(), str(k)) for h, k in rows.all())


async def _key_checks(w: World) -> list[str]:
    async with w.factory() as s:
        rows = await s.execute(
            select(TiqoraItsmLegacyErased.identifier_hash).where(
                TiqoraItsmLegacyErased.kind == KEY_CHECK_KIND
            )
        )
        return sorted(str(h).strip() for (h,) in rows.all())


async def _customer(s: AsyncSession, login: str, email: str) -> int:
    await s.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:l, :e, 'chg94-co-947641', 'Erika', 'Beispiel', 1, :t, 1, :t, 1)"
        ),
        {"l": login, "e": email, "t": at(1, 8)},
    )
    return int(
        (
            await s.execute(text("SELECT id FROM customer_user WHERE login = :l"), {"l": login})
        ).scalar_one()
    )


async def _erase(w: World, customer_id: int, settings: Settings = SETTINGS) -> Any:
    return await run_erasure(
        w.factory,
        settings,
        customer_user_ids=[customer_id],
        mode="anonymize",
        seed=7,
        force_parallel=True,
        actor="test",
    )


def _absent(data: str, *values: str) -> bool:
    return not any(v.lower() in data.lower() for v in values)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_erasure_tombstone_keeps_customer_out(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """Erase -> Znuny change edited -> re-archive, and a change first archived after the
    erasure: the customer stays out; rolling the erasure back lets it in again."""
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            customer_id = await _customer(s, CUSTOMER2, CUSTOMER2_MAIL)
            await seed_change_cab(s, change_id=MAIL, cab_id=CAB2, customer_user_id=CUSTOMER2)
            await seed_change_history(
                s,
                history_id=CAB2_HISTORY,
                change_id=MAIL,
                history_type="ChangeCABUpdate",
                created_at=at(1, 13),
                fieldname="CABCustomers",
                content_old=CUSTOMER,
                content_new=f"{CUSTOMER}%%{CUSTOMER2}",
                user_id=MANAGER,
            )
            await seed_change_history(
                s,
                history_id=CAB2_NOTIFY,
                change_id=MAIL,
                history_type="ChangeNotificationSent",
                created_at=at(1, 14),
                content_new=f"{CUSTOMER2_MAIL}%%ChangeUpdate",
                user_id=MANAGER,
            )
            await s.commit()
        await _archive(w)
        before = (await _rows(w))[MAIL]
        assert CUSTOMER2 in before.data and CUSTOMER2_MAIL in before.data

        result = await _erase(w, customer_id)

        # tombstones: keyed hashes of the lowercased login and address, never the plain value
        assert await _tombstones(w) == sorted(
            [
                (erased_hash(erased_key(SETTINGS), CUSTOMER2), "login"),
                (erased_hash(erased_key(SETTINGS), CUSTOMER2_MAIL), "email"),
            ]
        )
        assert result.counts["tiqora_itsm_legacy_erased"] == 2
        erased = change_from_data(json.loads((await _rows(w))[MAIL].data))
        token = next(
            login for kind, login in erased.cab if kind == "customer_user" and login != CUSTOMER
        )
        assert token not in (CUSTOMER2, REDACTED)
        cab_row = next(h for h in erased.history if h.field == "CABCustomers" and h.old)
        assert (cab_row.old, cab_row.new) == (CUSTOMER, f"{CUSTOMER}%%{token}")
        assert cab_row.new_display == f"{CUSTOMER}, {token}"
        sent = {h.new for h in erased.history if h.history_type == "ChangeNotificationSent"}
        # S5 with a login that is not the address: the address becomes "[gelöscht]"
        assert f"{REDACTED}%%ChangeUpdate" in sent
        assert _absent((await _rows(w))[MAIL].data, CUSTOMER2, CUSTOMER2_MAIL)
        assert _counts(await _archive(w)) == (0, 0, 3, 0)  # Znuny unchanged: nothing re-read

        # 1) the change is edited in Znuny: the update keeps the customer out
        async with w.factory() as s:
            await s.execute(
                text("UPDATE change_item SET title = :t, change_time = :c WHERE id = :i"),
                {"t": "chg94 Mail relay replacement v2", "c": at(6, 9), "i": MAIL},
            )
            # 2) a change first seen after the erasure, with the erased customer in its CAB
            await seed_change(
                s,
                change_id=LATE_CHANGE,
                number="2026110194000650",
                title=f"chg94 late change for {CUSTOMER2}",
                state="requested",
                builder_id=MANAGER,
                created_at=at(6, 10),
                description=f"Requested by {CUSTOMER2_MAIL}.",
                profile=profile,
            )
            await seed_change_cab(
                s, change_id=LATE_CHANGE, cab_id=LATE_CAB, customer_user_id=CUSTOMER2
            )
            await seed_change_history(
                s,
                history_id=LATE_NOTIFY,
                change_id=LATE_CHANGE,
                history_type="ChangeNotificationSent",
                created_at=at(6, 11),
                content_new=f"{CUSTOMER2_MAIL.upper()}%%ChangeAdd",
                user_id=MANAGER,
            )
            await s.commit()
        assert _counts(await _archive(w)) == (1, 1, 2, 0)
        rows = await _rows(w)
        for znuny_id in (MAIL, LATE_CHANGE):
            assert _absent(rows[znuny_id].data, CUSTOMER2, CUSTOMER2_MAIL), znuny_id
            assert ("customer_user", CUSTOMER2, 0) not in await _refs(w, znuny_id)
        assert rows[MAIL].title == "chg94 Mail relay replacement v2"
        assert CUSTOMER in rows[MAIL].data  # the other customer stays
        assert ("customer_user", CUSTOMER, 0) in await _refs(w, MAIL)
        late = change_from_data(json.loads(rows[LATE_CHANGE].data))
        assert late.cab == (("customer_user", REDACTED),)
        assert late.title == f"chg94 late change for {REDACTED}"
        assert rows[LATE_CHANGE].title == late.title
        assert late.description == f"Requested by {REDACTED}."
        assert _counts(await _archive(w)) == (0, 0, 4, 0)  # stable while the tombstones stay

        # rollback: the tombstones go, the next run brings the customer back
        await rollback_job(w.factory, SETTINGS, result.job_id, force_parallel=True, actor="test")
        assert await _tombstones(w) == []
        assert _counts(await _archive(w)) == (0, 2, 2, 0)
        rows = await _rows(w)
        for znuny_id in (MAIL, LATE_CHANGE):
            assert CUSTOMER2 in rows[znuny_id].data, znuny_id
            assert ("customer_user", CUSTOMER2, 0) in await _refs(w, znuny_id)
        assert CUSTOMER2_MAIL.upper() in rows[LATE_CHANGE].data


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_erasure_of_numeric_login_leaves_links_intact(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """A customer login equal to a ticket id: links, ids and refs are never rewritten."""
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            customer_id = await _customer(s, NUMERIC_LOGIN, NUMERIC_MAIL)
            await s.commit()
        await _archive(w)
        before = {z: r.data for z, r in (await _rows(w)).items()}
        refs_before = {z: await _refs(w, z) for z in ARCHIVED}
        assert ("ticket", NUMERIC_LOGIN, 0) in refs_before[MAIL]

        result = await _erase(w, customer_id)

        assert result.counts["tiqora_itsm_legacy_change"] == 0
        assert {z: r.data for z, r in (await _rows(w)).items()} == before
        assert {z: await _refs(w, z) for z in ARCHIVED} == refs_before
        assert len(await _tombstones(w)) == 2
        # the archive's tombstone pass ignores links and ids as well
        async with w.factory() as s:
            await s.execute(
                text("UPDATE change_item SET change_time = :c WHERE id = :i"),
                {"c": at(7, 8), "i": MAIL},
            )
            await s.commit()
        assert _counts(await _archive(w)) == (0, 1, 2, 0)
        mail = change_from_data(json.loads((await _rows(w))[MAIL].data))
        assert any(li.key == NUMERIC_LOGIN and li.object == "Ticket" for li in mail.links)
        assert await _refs(w, MAIL) == refs_before[MAIL]


async def _tombstone_jobs(w: World) -> list[tuple[str, int | None]]:
    async with w.factory() as s:
        rows = await s.execute(
            select(TiqoraItsmLegacyErased.identifier_hash, TiqoraItsmLegacyErased.job_id).where(
                TiqoraItsmLegacyErased.kind != KEY_CHECK_KIND
            )
        )
        return sorted(
            ((str(h).strip(), None if j is None else int(j)) for h, j in rows.all()),
            key=lambda t: (t[0], t[1] or 0),
        )


async def _edit_mail(w: World, day: int) -> None:
    async with w.factory() as s:
        await s.execute(
            text("UPDATE change_item SET change_time = :c WHERE id = :i"),
            {"c": at(day, 8), "i": MAIL},
        )
        await s.commit()


def test_redact_matches_identifiers_next_to_html_entities() -> None:
    """Raw RichText history values: entities and tags around an identifier, an encoded one."""
    key = erased_key(SETTINGS)
    values = {
        "field": "Description",
        "history_type": "ChangeUpdate",
        "old": "<p>Bitte an Erika &lt;chg94-kunde@example.org&gt; senden</p>",
        "new": "Kontakt: chg94.kunde&nbsp;(Tel), nicht chg94.kunde.extra",
        "old_display": "Mail: chg94-kunde&#64;example.org.",
        "new_display": "-chg94.kunde-",
    }
    data = {"history": [values], "cab": [], "workorders": []}
    erased = redact_data(data, MappingRedactor(key, {CUSTOMER2: "tok94"}, [CUSTOMER2_MAIL]))[
        "history"
    ][0]
    assert erased["old"] == f"<p>Bitte an Erika &lt;{REDACTED}&gt; senden</p>"
    assert erased["new"] == "Kontakt: tok94&nbsp;(Tel), nicht chg94.kunde.extra"
    assert erased["old_display"] == f"Mail: {REDACTED}."  # matched decoded, stored decoded
    assert erased["new_display"] == "-tok94-"
    tombstones = TombstoneRedactor(
        key, {erased_hash(key, CUSTOMER2), erased_hash(key, CUSTOMER2_MAIL)}
    )
    archived = redact_data(data, tombstones)["history"][0]
    assert archived["old"] == f"<p>Bitte an Erika &lt;{REDACTED}&gt; senden</p>"
    assert archived["new"] == f"Kontakt: {REDACTED}&nbsp;(Tel), nicht chg94.kunde.extra"
    assert archived["old_display"] == f"Mail: {REDACTED}."
    assert tombstones.hits == {erased_hash(key, CUSTOMER2), erased_hash(key, CUSTOMER2_MAIL)}
    # other fields of the same row are no person-bearing text: left alone
    ids = {"field": "ChangeStateID", "history_type": "ChangeUpdate", "old": CUSTOMER2}
    assert redact_data({"history": [ids]}, tombstones)["history"][0]["old"] == CUSTOMER2


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_rich_text_history_redacted_on_erasure_and_rearchive(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            customer_id = await _customer(s, CUSTOMER2, CUSTOMER2_MAIL)
            for hid, field, old, new in (
                (
                    RICH_HISTORY,
                    "Description",
                    "<p>Kontakt: chg94.kunde&nbsp;(Tel)</p>",
                    f"<p>Bitte an Erika &lt;{CUSTOMER2_MAIL}&gt; senden</p>",
                ),
                (RICH_HISTORY_2, "Justification", "", "Mail: chg94-kunde&#64;example.org"),
            ):
                await seed_change_history(
                    s,
                    history_id=hid,
                    change_id=MAIL,
                    history_type="ChangeUpdate",
                    created_at=at(1, 15),
                    fieldname=field,
                    content_old=old,
                    content_new=new,
                    user_id=MANAGER,
                )
            await s.commit()
        await _archive(w)
        await _erase(w, customer_id)

        def rich(raw: str) -> list[tuple[str | None, str | None]]:
            change = change_from_data(json.loads(raw))
            return [
                (h.old, h.new)
                for h in change.history
                if h.field in ("Description", "Justification")
            ]

        erased = (await _rows(w))[MAIL].data
        assert _absent(erased, CUSTOMER2, CUSTOMER2_MAIL, "chg94-kunde&#64;example.org")
        assert rich(erased)[1] == ("", f"Mail: {REDACTED}")
        assert rich(erased)[0][1] == f"<p>Bitte an Erika &lt;{REDACTED}&gt; senden</p>"
        assert _counts(await _archive(w)) == (0, 0, 3, 0)

        await _edit_mail(w, 7)  # re-archive from Znuny against the tombstones
        assert _counts(await _archive(w)) == (0, 1, 2, 0)
        archived = (await _rows(w))[MAIL].data
        assert _absent(archived, CUSTOMER2, CUSTOMER2_MAIL, "chg94-kunde&#64;example.org")
        assert rich(archived)[0] == (
            f"<p>Kontakt: {REDACTED}&nbsp;(Tel)</p>",
            f"<p>Bitte an Erika &lt;{REDACTED}&gt; senden</p>",
        )


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_rollback_keeps_a_shared_address_another_erasure_holds(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """Two customers share an address, so it is erased twice (jobs A, B): rolling A back
    first removes only A's tombstones and redacts the restored row again for B in the same
    transaction; rolling B back then leaves the row clear."""
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            first = await _customer(s, CUSTOMER2, CUSTOMER2_MAIL)
            second = await _customer(s, CUSTOMER3, CUSTOMER2_MAIL)
            for cab_id, login in ((CAB2, CUSTOMER2), (CAB3, CUSTOMER3)):
                await seed_change_cab(s, change_id=MAIL, cab_id=cab_id, customer_user_id=login)
            await seed_change_history(
                s,
                history_id=SHARED_NOTIFY,
                change_id=MAIL,
                history_type="ChangeNotificationSent",
                created_at=at(1, 14),
                content_new=f"{CUSTOMER2_MAIL}%%ChangeUpdate",
                user_id=MANAGER,
            )
            await s.commit()
        await _archive(w)
        clear = (await _rows(w))[MAIL].data
        assert CUSTOMER2 in clear and CUSTOMER3 in clear and CUSTOMER2_MAIL in clear
        key = erased_key(SETTINGS)
        shared = erased_hash(key, CUSTOMER2_MAIL)

        job_a = (await _erase(w, first)).job_id
        job_b = (await _erase(w, second)).job_id

        assert [j for h, j in await _tombstone_jobs(w) if h == shared] == sorted([job_a, job_b])
        assert len(await _tombstone_jobs(w)) == 4  # login + address per job

        await rollback_job(w.factory, SETTINGS, job_a, force_parallel=True, actor="test")

        assert await _tombstone_jobs(w) == sorted(
            [(erased_hash(key, CUSTOMER3), job_b), (shared, job_b)],
            key=lambda t: (t[0], t[1] or 0),
        )
        # no archive run in between: the rollback itself redacted the restored row for B
        data = (await _rows(w))[MAIL].data
        assert CUSTOMER2 in data
        assert _absent(data, CUSTOMER3, CUSTOMER2_MAIL)
        refs = await _refs(w, MAIL)
        assert ("customer_user", CUSTOMER2, 0) in refs
        assert ("customer_user", CUSTOMER3, 0) not in refs
        # and it is stored as an archive run would store it
        assert _counts(await _archive(w)) == (0, 0, 3, 0)

        await rollback_job(w.factory, SETTINGS, job_b, force_parallel=True, actor="test")

        assert await _tombstone_jobs(w) == []
        assert (await _rows(w))[MAIL].data == clear
        refs = await _refs(w, MAIL)
        assert ("customer_user", CUSTOMER2, 0) in refs
        assert ("customer_user", CUSTOMER3, 0) in refs
        assert _counts(await _archive(w)) == (0, 0, 3, 0)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_cli_anonymisation_also_leaves_tombstones(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            await _customer(s, CUSTOMER2, CUSTOMER2_MAIL)
            await seed_change_cab(s, change_id=MAIL, cab_id=CAB2, customer_user_id=CUSTOMER2)
            await s.commit()
        await _archive(w)
        assert CUSTOMER2 in (await _rows(w))[MAIL].data

        result = await anonymize_customer(
            w.factory, SETTINGS, login=CUSTOMER2, seed=7, force_parallel=True, actor="test"
        )

        assert result.legacy_changes == 1
        key = erased_key(SETTINGS)
        # no job, no rollback: permanent tombstones (job_id NULL)
        assert await _tombstone_jobs(w) == sorted(
            [(erased_hash(key, CUSTOMER2), None), (erased_hash(key, CUSTOMER2_MAIL), None)],
            key=lambda t: (t[0], t[1] or 0),
        )
        assert _absent((await _rows(w))[MAIL].data, CUSTOMER2, CUSTOMER2_MAIL)
        assert ("customer_user", CUSTOMER2, 0) not in await _refs(w, MAIL)
        await _edit_mail(w, 8)
        assert _counts(await _archive(w)) == (0, 1, 2, 0)
        assert _absent((await _rows(w))[MAIL].data, CUSTOMER2, CUSTOMER2_MAIL)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
@pytest.mark.parametrize("setting", ["itsm_change.enabled", "approvals.enabled"])
async def test_anonymisation_and_archive_job_work_while_the_module_is_off(
    dialect: str, profile: str, setting: str, request: pytest.FixtureRequest
) -> None:
    """Off hides the views only: the stored archive stays, its job runs and GDPR reaches it."""
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            await _customer(s, CUSTOMER2, CUSTOMER2_MAIL)
            await seed_change_cab(s, change_id=MAIL, cab_id=CAB2, customer_user_id=CUSTOMER2)
            await s.execute(insert(TiqoraSettings).values(key=setting, value="0"))
            await s.commit()
        try:
            await _archive(w)
            assert CUSTOMER2 in (await _rows(w))[MAIL].data
            result = await anonymize_customer(
                w.factory, SETTINGS, login=CUSTOMER2, seed=7, force_parallel=True, actor="test"
            )
            assert result.legacy_changes == 1
            assert _absent((await _rows(w))[MAIL].data, CUSTOMER2, CUSTOMER2_MAIL)
        finally:
            async with w.factory() as s:
                await s.execute(delete(TiqoraSettings).where(TiqoraSettings.key == setting))
                await s.commit()


# --- final review: the tombstone key (B-I2), tombstones during a page (B-M4) -----------------

#: the site secret after a rotation; with ``itsm_legacy_erased_key`` = the old secret
_AFTER_ROTATION = "chg94" + "-rotated" * 5
ROTATED = Settings(environment="test", secret_key=_AFTER_ROTATION)
PINNED = Settings(
    environment="test", secret_key=_AFTER_ROTATION, itsm_legacy_erased_key=SETTINGS.secret_key
)
CUSTOMER4, CUSTOMER4_MAIL = "chg94.vierte", "chg94-vierte@example.org"


async def _stored(w: World) -> dict[int, tuple[str, str]]:
    return {z: (r.data, r.fingerprint) for z, r in (await _rows(w)).items()}


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_rotated_key_refuses_instead_of_unredacting(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """``TIQORA_SECRET_KEY`` rotated: the admin job fails, the import step reports
    ``archive_key_changed``; nothing is written. With ``TIQORA_ITSM_LEGACY_ERASED_KEY`` = the
    old secret (and no erasure since) the archive runs and stays redacted."""
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            customer_id = await _customer(s, CUSTOMER2, CUSTOMER2_MAIL)
            await seed_change_cab(s, change_id=MAIL, cab_id=CAB2, customer_user_id=CUSTOMER2)
            await s.commit()
        await _archive(w)
        await _erase(w, customer_id)
        # the first tombstone stored the check value of the key it was written with
        assert await _key_checks(w) == [key_check(erased_key(SETTINGS))]
        assert key_check(erased_key(ROTATED)) != key_check(erased_key(SETTINGS))
        assert erased_key(PINNED) == erased_key(SETTINGS)
        await _edit_mail(w, 8)  # Znuny changed: a run would rewrite the row from Znuny
        before = await _stored(w)

        with pytest.raises(JobFailed) as failed:
            await run_legacy_archive_job(
                JobContext(
                    job_id=0,
                    kind=ARCHIVE_JOB_KIND,
                    created_by=ADMIN,
                    settings=ROTATED,
                    session_factory=w.factory,
                ),
                LegacyArchivePayload(user_id=ADMIN),
            )
        assert failed.value.message == KEY_CHANGED_MESSAGE
        assert "TIQORA_ITSM_LEGACY_ERASED_KEY" in KEY_CHANGED_MESSAGE

        ctx = _step_ctx(w, run_id=RUN_ARCHIVE + 7, settings=ROTATED)
        await run_changes(ctx)
        issues = [i for i in ctx.report.issues if i.step == STEP_CHANGES]
        assert [i.code for i in issues] == ["archive_key_changed"]
        counts = ctx.report.steps[STEP_CHANGES]
        assert (counts.created, counts.updated, counts.unchanged, counts.problems) == (0, 0, 0, 1)

        assert await _stored(w) == before  # nothing rewritten anywhere

        report = await archive_changes(
            w.factory,
            run_id=RUN_ARCHIVE,
            dry_run=False,
            page_size=2,
            progress=_noop,
            check_cancelled=_noop,
            report=ArchiveReport(),
            settings=PINNED,
        )
        assert _counts(report) == (0, 1, 2, 0)
        assert _absent((await _rows(w))[MAIL].data, CUSTOMER2, CUSTOMER2_MAIL)
        assert ("customer_user", CUSTOMER2, 0) not in await _refs(w, MAIL)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_tombstone_committed_during_a_page_counts(
    dialect: str, profile: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B-M4: the tombstones are read again (in a session of their own) after the Znuny read,
    right before the page writes; an erasure that committed meanwhile is redacted at once."""
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            await seed_change_cab(s, change_id=MAIL, cab_id=CAB2, customer_user_id=CUSTOMER2)
            await s.commit()
        real = reader.read_changes
        erased: list[bool] = []

        async def read_then_erase(session: AsyncSession, ids: Any) -> Any:
            found = await real(session, ids)
            if not erased:  # a concurrent erasure commits while the page is being read
                erased.append(True)
                async with w.factory() as other:
                    other.add(
                        TiqoraItsmLegacyErased(
                            identifier_hash=erased_hash(erased_key(SETTINGS), CUSTOMER2),
                            kind="login",
                            job_id=None,
                            created=at(6, 1),
                        )
                    )
                    await other.commit()
            return found

        monkeypatch.setattr(reader, "read_changes", read_then_erase)
        await _archive(w, page_size=500)
        stored = (await _rows(w))[MAIL]
        assert _absent(stored.data, CUSTOMER2)
        assert ("customer_user", CUSTOMER2, 0) not in await _refs(w, MAIL)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_tombstones_without_a_job_are_not_duplicated(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """The unique key (hash, job) treats NULL jobs as distinct; the writer's existence check
    keeps one CLI tombstone per identifier, and one key check row."""
    async with archive_world(request, dialect, profile) as w:
        for _ in range(2):
            async with w.factory() as s:
                counts = await erase_legacy_change_customers(
                    s,
                    replacements={CUSTOMER2: "chg94-token"},
                    emails=[CUSTOMER2_MAIL],
                    add_backup=_noop,
                    settings=SETTINGS,
                    job_id=None,
                )
                await s.commit()
        assert counts["tiqora_itsm_legacy_erased"] == 0  # the second run added none
        key = erased_key(SETTINGS)
        assert await _tombstone_jobs(w) == sorted(
            [(erased_hash(key, CUSTOMER2), None), (erased_hash(key, CUSTOMER2_MAIL), None)],
            key=lambda t: (t[0], t[1] or 0),
        )
        assert await _key_checks(w) == [key_check(key)]


# --- round 2: a changed key never blocks an erasure, only archive runs ------------------------


async def _archive_with(w: World, settings: Settings) -> ArchiveReport:
    return await archive_changes(
        w.factory,
        run_id=RUN_ARCHIVE,
        dry_run=False,
        page_size=2,
        progress=_noop,
        check_cancelled=_noop,
        report=ArchiveReport(),
        settings=settings,
    )


async def _two_customers_in_the_archive(w: World) -> tuple[int, int]:
    async with w.factory() as s:
        first = await _customer(s, CUSTOMER2, CUSTOMER2_MAIL)
        second = await _customer(s, CUSTOMER4, CUSTOMER4_MAIL)
        await seed_change_cab(s, change_id=MAIL, cab_id=CAB2, customer_user_id=CUSTOMER2)
        await seed_change_cab(s, change_id=MAIL, cab_id=CAB3, customer_user_id=CUSTOMER4)
        await s.commit()
    await _archive(w)
    return first, second


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_erasure_under_a_changed_key_completes_and_archive_runs_stay_refused(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """The stored row of the person erased during the mismatch is redacted (no key needed), the
    job says so in its counts, and with tombstones of two keys no key can run the archive: not the
    new one, not the old one set through ``TIQORA_ITSM_LEGACY_ERASED_KEY``."""
    async with archive_world(request, dialect, profile) as w:
        first, second = await _two_customers_in_the_archive(w)
        await _erase(w, first)
        assert CUSTOMER4 in (await _rows(w))[MAIL].data

        result = await _erase(w, second, ROTATED)

        assert result.counts[KEY_CHANGED_COUNT] == 1
        assert result.counts["tiqora_itsm_legacy_change"] == 1
        stored = (await _rows(w))[MAIL]
        assert _absent(stored.data, CUSTOMER2, CUSTOMER2_MAIL, CUSTOMER4, CUSTOMER4_MAIL)
        assert ("customer_user", CUSTOMER4, 0) not in await _refs(w, MAIL)
        new_key = erased_key(ROTATED)
        assert erased_hash(new_key, CUSTOMER4) in {h for h, _ in await _tombstones(w)}
        assert sorted(await _key_checks(w)) == sorted(
            [key_check(erased_key(SETTINGS)), key_check(new_key)]
        )
        async with w.factory() as s:
            login = (
                await s.execute(
                    text("SELECT login FROM customer_user WHERE id = :i"), {"i": second}
                )
            ).scalar_one()
        assert login != CUSTOMER4

        await _edit_mail(w, 8)
        before = await _stored(w)
        for settings in (ROTATED, PINNED):
            with pytest.raises(ErasedKeyChanged):
                await _archive_with(w, settings)
            ctx = _step_ctx(w, run_id=RUN_ARCHIVE + 7, settings=settings)
            await run_changes(ctx)
            assert [i.code for i in ctx.report.issues if i.step == STEP_CHANGES] == [
                "archive_key_changed"
            ]
        assert await _stored(w) == before
        for case in ("KEY", "Löschungen"):
            assert case in KEY_CHANGED_MESSAGE


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_cli_anonymisation_under_a_changed_key_warns(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    async with archive_world(request, dialect, profile) as w:
        first, _ = await _two_customers_in_the_archive(w)
        await _erase(w, first)

        result = await anonymize_customer(
            w.factory, ROTATED, login=CUSTOMER4, seed=7, force_parallel=True, actor="test"
        )

        assert result.customer_users == 1
        assert KEY_CHANGED_WARNING in result.progress
        assert _absent((await _rows(w))[MAIL].data, CUSTOMER4, CUSTOMER4_MAIL)
        with pytest.raises(ErasedKeyChanged):
            await _archive_with(w, ROTATED)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_rollback_refuses_only_when_it_needs_the_changed_key(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """A rollback that restores archive rows must redact them against the tombstones that remain,
    which takes the key; it refuses as a whole. One that restores no archive row goes through."""
    async with archive_world(request, dialect, profile) as w:
        first, second = await _two_customers_in_the_archive(w)
        async with w.factory() as s:
            unarchived = await _customer(s, "chg94.ohne", "chg94-ohne@example.org")
            await s.commit()
        await _erase(w, first)
        job = (await _erase(w, second, ROTATED)).job_id
        other_job = (await _erase(w, unarchived, ROTATED)).job_id
        before = await _stored(w)

        with pytest.raises(ErasureError, match="TIQORA_ITSM_LEGACY_ERASED_KEY"):
            await rollback_job(w.factory, ROTATED, job, force_parallel=True, actor="test")
        assert await _stored(w) == before
        assert _absent((await _rows(w))[MAIL].data, CUSTOMER2, CUSTOMER4)
        async with w.factory() as s:
            status = (await s.get(TiqoraGdprJob, job)).status  # type: ignore[union-attr]
        assert status == "applied"

        restored = await rollback_job(
            w.factory, ROTATED, other_job, force_parallel=True, actor="test"
        )
        assert restored["restored_rows"] > 0
        async with w.factory() as s:
            status = (await s.get(TiqoraGdprJob, other_job)).status  # type: ignore[union-attr]
        assert status == "rolled_back"


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_stale_key_check_without_tombstones_is_replaced(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """The rollback of the only erasure leaves the check row: nothing can come back under a new
    key, so archive runs go on, and the next tombstone replaces the stale check."""
    async with archive_world(request, dialect, profile) as w:
        first, second = await _two_customers_in_the_archive(w)
        job = (await _erase(w, first)).job_id
        await rollback_job(w.factory, SETTINGS, job, force_parallel=True, actor="test")
        assert await _tombstones(w) == []
        assert await _key_checks(w) == [key_check(erased_key(SETTINGS))]

        assert _counts(await _archive_with(w, ROTATED))[3] == 0

        result = await _erase(w, second, ROTATED)
        assert KEY_CHANGED_COUNT not in result.counts
        assert await _key_checks(w) == [key_check(erased_key(ROTATED))]
        await _edit_mail(w, 8)
        await _archive_with(w, ROTATED)
        assert _absent((await _rows(w))[MAIL].data, CUSTOMER4, CUSTOMER4_MAIL)


@pytest.mark.parametrize(("dialect", "profile"), ITSM_PARAMS)
async def test_erasure_under_another_key_during_a_run_stops_it(
    dialect: str, profile: str, request: pytest.FixtureRequest
) -> None:
    """N3: the run passed its start check when the erasure commits. Its key cannot match the new
    tombstones, so writing the page would store the person in the clear, and the erasure's check
    row would refuse every run that could repair it. The page rolls back instead. The erasure fires
    before the page opens: this world's sessions share one connection, so a page rollback would
    also undo an erasure nested inside it."""
    async with archive_world(request, dialect, profile) as w:
        async with w.factory() as s:
            customer_id = await _customer(s, CUSTOMER2, CUSTOMER2_MAIL)
            await seed_change_cab(s, change_id=MAIL, cab_id=CAB2, customer_user_id=CUSTOMER2)
            await s.commit()
        await _archive(w)
        await _edit_mail(w, 8)  # Znuny changed: the run would rewrite the row from Znuny
        erased: list[bool] = []

        async def erase_once() -> None:
            if not erased:
                erased.append(True)
                await _erase(w, customer_id, ROTATED)

        report = ArchiveReport()
        with pytest.raises(ErasedKeyChanged):
            await archive_changes(
                w.factory,
                run_id=RUN_ARCHIVE,
                dry_run=False,
                page_size=1,
                progress=_noop,
                check_cancelled=erase_once,
                report=report,
                settings=SETTINGS,
            )

        assert erased == [True]
        assert _absent((await _rows(w))[MAIL].data, CUSTOMER2, CUSTOMER2_MAIL)
        assert ("customer_user", CUSTOMER2, 0) not in await _refs(w, MAIL)
        assert report.updated == 0
        with pytest.raises(ErasedKeyChanged):  # and the next run does not start
            await _archive_with(w, SETTINGS)
