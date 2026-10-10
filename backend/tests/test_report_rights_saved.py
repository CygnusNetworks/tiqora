"""Report rights, share groups and the saved-report store (both dialects).

Band 48020-48059 (names contain ``rep48``): users 48020 ``rep48.plain`` (no ``stats`` rights),
48021 ``rep48.statsro`` (``ro`` on Znuny's seeded ``stats`` group, looked up by name), 48022
``rep48.statsrw`` (``rw`` on ``stats``, ``ro`` on 48030 and 48031), 48023 ``rep48.member``
(``ro`` on 48030), 48024 ``rep48.outsider``; groups 48030 / 48031 ``rep48-group-<id>``.
The Znuny admin is user 1 and owns no rows. Saved rows are deleted again by owner band.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.reporting.jobs as report_jobs
import tiqora.reporting.saved as saved_module
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.reports import get_report_cache
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.jobs.runner import JobRunner
from tiqora.jobs.service import utc_now
from tiqora.permissions.sharing import (
    ShareGroupError,
    check_share_groups,
    share_groups_for,
)
from tiqora.reporting.definition import DefinitionError, ReportDefinition
from tiqora.reporting.rights import ReportRights, report_rights
from tiqora.reporting.saved import (
    ReportForbidden,
    ReportGone,
    ReportNotFound,
    ReportVersionConflict,
    SavedReportIn,
    SavedReportUpdateIn,
    create_saved_report,
    delete_saved_report,
    dump_definition,
    get_saved_report,
    load_definition,
    purge_deleted_reports,
    resolve_report_ref,
    update_saved_report,
    visible_saved_reports,
)
from tiqora.reporting.settings import REPORTING_SECTION, load_reporting_settings
from tiqora.reporting.state import load_state
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

LO, HI = 48020, 48059
PLAIN, STATS_RO, STATS_RW, MEMBER, OUTSIDER = 48020, 48021, 48022, 48023, 48024
G30, G31 = 48030, 48031
LOGINS = {
    PLAIN: "rep48.plain",
    STATS_RO: "rep48.statsro",
    STATS_RW: "rep48.statsrw",
    MEMBER: "rep48.member",
    OUTSIDER: "rep48.outsider",
}
TICKETS_DEF = {
    "source": "tickets",
    "dimensions": [{"key": "queue"}],
    "measures": [{"key": "tickets"}],
}
_BAND = {"lo": LO, "hi": HI}
_RESET = (
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi"
    "  OR (owner_user_id = 1 AND name LIKE 'rep48%'))",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi"
    " OR (owner_user_id = 1 AND name LIKE 'rep48%')",
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE kind IN ('core.report_run', 'core.report_export'))",
    "DELETE FROM tiqora_job WHERE kind IN ('core.report_run', 'core.report_export')",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'",
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _run_sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    key = "`key`" if engine.dialect.name == "mysql" else '"key"'
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt.replace("{key}", key)), _BAND)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _CLEANUP)
    _URLS.clear()
    _SEEDED.clear()


def user(uid: int) -> AuthenticatedUser:
    login = "root@localhost" if uid == 1 else LOGINS[uid]
    return AuthenticatedUser(
        id=uid, login=login, first_name="Rep", last_name="Test", auth_method="session"
    )


def _seed(sync_url: str) -> None:
    t0 = datetime(2026, 10, 1)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        stats = conn.execute(
            text("SELECT id FROM permission_groups WHERE name = 'stats' AND valid_id = 1")
        ).scalar_one()
        for uid, login in LOGINS.items():
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', 'Rep', 'Test', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "t": t0},
            )
        for gid in (G30, G31):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep48-group-{gid}", "t": t0},
            )
        for uid, gid, key in (
            (STATS_RO, stats, "ro"),
            (STATS_RW, stats, "rw"),
            (STATS_RW, G30, "ro"),
            (STATS_RW, G31, "ro"),
            (MEMBER, G30, "ro"),
        ):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "k": key, "t": t0},
            )
    engine.dispose()


class _MemoryCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value


class Env:
    def __init__(
        self, sync_url: str, factory: async_sessionmaker[AsyncSession], client: AsyncClient
    ) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.client = client
        self.user = user(PLAIN)
        self.cache = _MemoryCache()

    def as_user(self, uid: int) -> None:
        self.user = user(uid)

    async def run_jobs(self) -> None:
        runner = JobRunner(
            session_factory=self.factory,
            settings=Settings(environment="test"),
            registry=get_registry(),
        )
        await runner.run_once()
        await runner.wait_idle()

    async def job(self, job_id: int) -> dict[str, Any]:
        resp = await self.client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        return resp.json()  # type: ignore[no-any-return]

    async def ensure_state(self) -> None:
        async with self.factory() as s:
            await load_state(s)
            await s.commit()

    async def facts_ready(self) -> None:
        async with self.factory() as s:
            (await load_state(s)).backfill_status = "done"
            await s.commit()

    async def set_access(self, access: str) -> None:
        async with self.factory() as s:
            await save_section_values(
                s,
                REPORTING_SECTION,
                {"access": access},
                secret_key=Settings(environment="test").secret_key,
            )
            await s.commit()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    if sync_url not in _SEEDED:
        _run_sql(sync_url, _CLEANUP)
        _seed(sync_url)
        _SEEDED.add(sync_url)
    _run_sql(sync_url, _RESET)

    cache = _MemoryCache()
    monkeypatch.setattr(report_jobs, "cache_backend_from_settings", lambda _settings: cache)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    holder: list[Env] = []
    app.dependency_overrides[get_current_user] = lambda: holder[0].user
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_report_cache] = lambda: cache
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            holder.append(Env(sync_url, factory, c))
            holder[0].cache = cache
            yield holder[0]
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


async def _rights_in(s: AsyncSession, uid: int) -> ReportRights:
    return await report_rights(
        s, uid, await load_reporting_settings(s, Settings(environment="test"))
    )


async def _rights(env: Env, uid: int, access: str = "all_agents") -> ReportRights:
    await env.set_access(access)
    async with env.factory() as s:
        return await _rights_in(s, uid)


def _in(name: str, **kw: Any) -> SavedReportIn:
    return SavedReportIn(name=name, definition=ReportDefinition.model_validate(TICKETS_DEF), **kw)


# ---------------------------------------------------------------- rights


async def test_rights_all_agents(env: Env) -> None:
    assert await _rights(env, PLAIN) == ReportRights(
        view=True, create=True, share=False, admin=False
    )
    assert await _rights(env, STATS_RW) == ReportRights(True, True, True, False)
    assert await _rights(env, 1) == ReportRights(view=True, create=True, share=True, admin=True)


async def test_rights_stats_group(env: Env) -> None:
    assert await _rights(env, PLAIN, "stats_group") == ReportRights(False, False, False, False)
    assert await _rights(env, STATS_RO, "stats_group") == ReportRights(True, False, False, False)
    assert await _rights(env, STATS_RW, "stats_group") == ReportRights(True, True, True, False)
    assert await _rights(env, 1, "stats_group") == ReportRights(True, True, True, True)


async def test_me_exposes_create_and_share(env: Env) -> None:
    await env.set_access("all_agents")
    env.as_user(PLAIN)
    me = (await env.client.get("/api/v1/auth/me")).json()
    assert (me["can_view_reports"], me["can_create_reports"], me["can_share_reports"]) == (
        True,
        True,
        False,
    )
    env.as_user(STATS_RW)
    me = (await env.client.get("/api/v1/auth/me")).json()
    assert me["can_share_reports"] is True and me["can_create_reports"] is True


async def test_me_create_and_share_follow_view(env: Env) -> None:
    await env.set_access("stats_group")
    expected = {
        PLAIN: (False, False, False),
        STATS_RO: (True, False, False),
        STATS_RW: (True, True, True),
    }
    for uid, flags in expected.items():
        env.as_user(uid)
        me = (await env.client.get("/api/v1/auth/me")).json()
        assert (me["can_view_reports"], me["can_create_reports"], me["can_share_reports"]) == flags


async def test_report_rights_load_permissions_once(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.permissions.engine import PermissionEngine

    calls: list[int] = []
    original = PermissionEngine.queue_permissions

    async def counting(self: Any, user_id: int) -> Any:
        calls.append(user_id)
        return await original(self, user_id)

    monkeypatch.setattr(PermissionEngine, "queue_permissions", counting)
    async with env.factory() as s:
        await _rights_in(s, STATS_RW)
    assert calls == [STATS_RW]


# ---------------------------------------------------------------- share groups


async def test_share_groups_member_vs_admin(env: Env) -> None:
    async with env.factory() as s:
        mine = [g.id for g in await share_groups_for(s, MEMBER)]
        assert G30 in mine and G31 not in mine
        assert {G30, G31} <= {g.id for g in await share_groups_for(s, 1)}
        assert await share_groups_for(s, OUTSIDER) == []
        with pytest.raises(ShareGroupError):
            await check_share_groups(s, MEMBER, [G30, G31])
        assert await check_share_groups(s, MEMBER, [G30, G30]) == (G30,)
        assert await check_share_groups(s, 1, [G31, G30]) == (G30, G31)


# ---------------------------------------------------------------- store


async def test_visibility_private_groups_all(env: Env) -> None:
    reg = get_registry()
    async with env.factory() as s:
        rw = await _rights_in(s, STATS_RW)
        priv = await create_saved_report(s, STATS_RW, rw, _in("rep48 priv"), registry=reg)
        grp = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 grp", visibility="groups", group_ids=[G30]), registry=reg
        )
        allv = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 all", visibility="all"), registry=reg
        )
        await s.commit()
        member = {r.id for r in await visible_saved_reports(s, MEMBER, admin=False)}
        outsider = {r.id for r in await visible_saved_reports(s, OUTSIDER, admin=False)}
        admin_default = {r.id for r in await visible_saved_reports(s, 1, admin=True)}
        admin_all = {
            r.id for r in await visible_saved_reports(s, 1, admin=True, include_others_private=True)
        }
    assert member >= {grp.id, allv.id} and priv.id not in member
    assert allv.id in outsider and grp.id not in outsider and priv.id not in outsider
    assert priv.id not in admin_default and priv.id in admin_all
    assert grp.group_ids == (G30,) and allv.group_ids == ()


async def test_get_invisible_is_not_found_and_deleted_is_gone(env: Env) -> None:
    reg = get_registry()
    async with env.factory() as s:
        rw = await _rights_in(s, STATS_RW)
        priv = await create_saved_report(s, STATS_RW, rw, _in("rep48 p2"), registry=reg)
        allv = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 a2", visibility="all"), registry=reg
        )
        await delete_saved_report(s, allv.id, STATS_RW, rw)
        await s.commit()
        with pytest.raises(ReportNotFound):
            await get_saved_report(s, priv.id, OUTSIDER, admin=False)
        with pytest.raises(ReportNotFound):
            await get_saved_report(s, 48_999_999, OUTSIDER, admin=False)
        with pytest.raises(ReportGone) as gone:
            await get_saved_report(s, allv.id, OUTSIDER, admin=False)
        assert gone.value.name == "rep48 a2"
        # R-ADMINID: an admin opens any report by id, even a colleague's private one
        assert (await get_saved_report(s, priv.id, 1, admin=True)).name == "rep48 p2"


async def test_create_rules(env: Env) -> None:
    reg = get_registry()
    async with env.factory() as s:
        plain = await _rights_in(s, PLAIN)
        with pytest.raises(ReportForbidden):
            await create_saved_report(
                s, PLAIN, plain, _in("rep48 x", visibility="all"), registry=reg
            )
        rw = await _rights_in(s, STATS_RW)
        with pytest.raises(ShareGroupError):
            await create_saved_report(
                s, STATS_RW, rw, _in("rep48 y", visibility="groups"), registry=reg
            )
        member_rw = ReportRights(view=True, create=True, share=True, admin=False)
        with pytest.raises(ShareGroupError):  # 48031 is not one of member's groups
            await create_saved_report(
                s, MEMBER, member_rw, _in("rep48 y2", visibility="groups", group_ids=[G31]),
                registry=reg,
            )  # fmt: skip
        with pytest.raises(DefinitionError) as bad:
            await create_saved_report(
                s,
                PLAIN,
                plain,
                SavedReportIn.model_construct(
                    name="rep48 z",
                    description="",
                    definition=ReportDefinition.model_validate(
                        {"source": "tickets", "measures": [{"key": "nope"}]}
                    ),
                    visibility="private",
                    group_ids=[],
                    copied_from=None,
                ),
                registry=reg,
            )
        assert bad.value.path.startswith("definition.measures")
        with pytest.raises(DefinitionError) as blank:
            await create_saved_report(s, PLAIN, plain, _in("   "), registry=reg)
        assert blank.value.path == "name"
        r = await create_saved_report(
            s, PLAIN, plain, _in("  rep48 trimmed  ", group_ids=[G30]), registry=reg
        )
        assert (r.name, r.visibility, r.group_ids, r.version) == ("rep48 trimmed", "private", (), 1)
        no_create = ReportRights(view=True, create=False, share=False, admin=False)
        with pytest.raises(ReportForbidden):
            await create_saved_report(s, STATS_RO, no_create, _in("rep48 nc"), registry=reg)


async def test_create_limit_per_user(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = get_registry()
    monkeypatch.setattr(saved_module, "MAX_SAVED_REPORTS_PER_USER", 2)
    async with env.factory() as s:
        plain = await _rights_in(s, PLAIN)
        a = await create_saved_report(s, PLAIN, plain, _in("rep48 l1"), registry=reg)
        await create_saved_report(s, PLAIN, plain, _in("rep48 l2"), registry=reg)
        with pytest.raises(DefinitionError) as limit:  # 422 with path, like saved searches
            await create_saved_report(s, PLAIN, plain, _in("rep48 l3"), registry=reg)
        assert limit.value.path == "name"
        await delete_saved_report(s, a.id, PLAIN, plain)  # deleted reports do not count
        await create_saved_report(s, PLAIN, plain, _in("rep48 l3"), registry=reg)


async def test_update_owner_admin_conflict(env: Env) -> None:
    reg = get_registry()
    async with env.factory() as s:
        plain = await _rights_in(s, PLAIN)
        rw = await _rights_in(s, STATS_RW)
        private = await create_saved_report(s, PLAIN, plain, _in("rep48 u"), registry=reg)
        shared = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 us", visibility="groups", group_ids=[G30]), registry=reg
        )
        await s.commit()
        upd = SavedReportUpdateIn(
            name="rep48 u2", definition=ReportDefinition.model_validate(TICKETS_DEF), version=1
        )
        member = await _rights_in(s, MEMBER)
        # R-UPDATE404: invisible -> NotFound, visible but not editable -> Forbidden
        with pytest.raises(ReportNotFound):
            await update_saved_report(s, private.id, MEMBER, member, upd, registry=reg)
        with pytest.raises(ReportForbidden):
            await update_saved_report(s, shared.id, MEMBER, member, upd, registry=reg)
        r2 = await update_saved_report(s, private.id, PLAIN, plain, upd, registry=reg)
        assert (r2.name, r2.version) == ("rep48 u2", 2)
        await s.commit()
        admin = await _rights_in(s, 1)
        with pytest.raises(ReportVersionConflict) as conflict:
            await update_saved_report(s, private.id, 1, admin, upd, registry=reg)  # stale v1
        assert conflict.value.current_version == 2
        fresh = SavedReportUpdateIn(
            name="rep48 by admin",
            definition=ReportDefinition.model_validate(TICKETS_DEF),
            version=2,
        )
        r3 = await update_saved_report(s, private.id, 1, admin, fresh, registry=reg)
        assert (r3.name, r3.version) == ("rep48 by admin", 3)


async def test_concurrent_save_rowcount_conflict_keeps_groups(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row changes between the read and the UPDATE: WHERE version matches nothing."""
    reg = get_registry()
    async with env.factory() as s:
        rw = await _rights_in(s, STATS_RW)
        rep = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 race", visibility="groups", group_ids=[G30]), registry=reg
        )  # fmt: skip
        await s.commit()
        defn = ReportDefinition.model_validate(TICKETS_DEF)
        await update_saved_report(
            s, rep.id, STATS_RW, rw,
            SavedReportUpdateIn(name="rep48 race b", definition=defn, version=1,
                                visibility="groups", group_ids=[G30]),
            registry=reg,
        )  # fmt: skip
        await s.commit()

        async def stale(*_a: Any, **_k: Any) -> Any:
            return rep  # still version 1, like a read that happened before the other save

        monkeypatch.setattr(saved_module, "get_saved_report", stale)
        with pytest.raises(ReportVersionConflict) as conflict:
            await update_saved_report(
                s, rep.id, STATS_RW, rw,
                SavedReportUpdateIn(name="rep48 race c", definition=defn, version=1,
                                    visibility="groups", group_ids=[G30, G31]),
                registry=reg,
            )  # fmt: skip
        assert conflict.value.current_version == 2
        monkeypatch.undo()
        current = await get_saved_report(s, rep.id, STATS_RW, admin=False)
        assert current.name == "rep48 race b" and current.group_ids == (G30,)


async def test_update_keeps_stored_sharing_without_share_right(env: Env) -> None:
    """R-SHAREKEEP: an owner who lost the share right/membership can still save edits."""
    reg = get_registry()
    defn = ReportDefinition.model_validate(TICKETS_DEF)
    async with env.factory() as s:
        rw = await _rights_in(s, STATS_RW)
        rep = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 keep", visibility="groups", group_ids=[G30, G31]),
            registry=reg,
        )  # fmt: skip
        await s.commit()
        owner_now = ReportRights(view=True, create=True, share=False, admin=False)
        kept = await update_saved_report(
            s, rep.id, STATS_RW, owner_now,
            SavedReportUpdateIn(name="rep48 keep2", definition=defn, version=1,
                                visibility="groups", group_ids=[G31, G30]),
            registry=reg,
        )  # fmt: skip
        assert (kept.visibility, kept.group_ids, kept.version) == ("groups", (G30, G31), 2)
        # narrowing (dropping a group) needs no share right, like saved searches and the docs
        narrowed = await update_saved_report(
            s, rep.id, STATS_RW, owner_now,
            SavedReportUpdateIn(name="rep48 keep3", definition=defn, version=2,
                                visibility="groups", group_ids=[G30]),
            registry=reg,
        )  # fmt: skip
        assert (narrowed.group_ids, narrowed.version) == ((G30,), 3)
        with pytest.raises(ReportForbidden):  # widening (adding a group back) does
            await update_saved_report(
                s, rep.id, STATS_RW, owner_now,
                SavedReportUpdateIn(name="rep48 keep3", definition=defn, version=3,
                                    visibility="groups", group_ids=[G30, G31]),
                registry=reg,
            )  # fmt: skip
        # private / all ignore group_ids without validation and store no group rows
        priv = await update_saved_report(
            s, rep.id, STATS_RW, owner_now,
            SavedReportUpdateIn(name="rep48 keep4", definition=defn, version=3,
                                visibility="private", group_ids=[48999]),
            registry=reg,
        )  # fmt: skip
        assert (priv.visibility, priv.group_ids) == ("private", ())
        # an added group the owner is not in is refused (member holds only 48030)
        member_share = ReportRights(view=True, create=True, share=True, admin=False)
        own = await create_saved_report(
            s, MEMBER, member_share, _in("rep48 own", visibility="groups", group_ids=[G30]),
            registry=reg,
        )  # fmt: skip
        with pytest.raises(ShareGroupError):
            await update_saved_report(
                s, own.id, MEMBER, member_share,
                SavedReportUpdateIn(name="rep48 own", definition=defn, version=1,
                                    visibility="groups", group_ids=[G30, G31]),
                registry=reg,
            )  # fmt: skip


async def test_narrowing_from_all_to_groups_needs_no_share_right(env: Env) -> None:
    reg = get_registry()
    defn = ReportDefinition.model_validate(TICKETS_DEF)
    async with env.factory() as s:
        rw = await _rights_in(s, STATS_RW)
        rep = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 alle", visibility="all"), registry=reg
        )
        await s.commit()
        owner_now = ReportRights(view=True, create=True, share=False, admin=False)
        narrowed = await update_saved_report(
            s, rep.id, STATS_RW, owner_now,
            SavedReportUpdateIn(name="rep48 alle", definition=defn, version=1,
                                visibility="groups", group_ids=[G30]),
            registry=reg,
        )  # fmt: skip
        assert (narrowed.visibility, narrowed.group_ids) == ("groups", (G30,))
        with pytest.raises(ReportForbidden):  # back to everybody is widening again
            await update_saved_report(
                s, rep.id, STATS_RW, owner_now,
                SavedReportUpdateIn(name="rep48 alle", definition=defn, version=2,
                                    visibility="all"),
                registry=reg,
            )  # fmt: skip


async def test_delete_rules(env: Env) -> None:
    reg = get_registry()
    async with env.factory() as s:
        plain = await _rights_in(s, PLAIN)
        rw = await _rights_in(s, STATS_RW)
        private = await create_saved_report(s, PLAIN, plain, _in("rep48 d1"), registry=reg)
        shared = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 d2", visibility="groups", group_ids=[G30]), registry=reg
        )
        mine = await create_saved_report(s, PLAIN, plain, _in("rep48 d3"), registry=reg)
        await s.commit()
        member = await _rights_in(s, MEMBER)
        with pytest.raises(ReportNotFound):
            await delete_saved_report(s, private.id, MEMBER, member)
        with pytest.raises(ReportForbidden):
            await delete_saved_report(s, shared.id, MEMBER, member)
        # R-OWNDELETE: the owner deletes their own report even without the create right
        no_create = ReportRights(view=False, create=False, share=False, admin=False)
        await delete_saved_report(s, mine.id, PLAIN, no_create)
        with pytest.raises(ReportGone):
            await get_saved_report(s, mine.id, PLAIN, admin=False)
        admin = await _rights_in(s, 1)
        await delete_saved_report(s, shared.id, 1, admin)
        with pytest.raises(ReportGone):
            await delete_saved_report(s, shared.id, 1, admin)
        # but editing still needs the create right
        upd = SavedReportUpdateIn(
            name="rep48 d1b", definition=ReportDefinition.model_validate(TICKETS_DEF), version=1
        )
        with pytest.raises(ReportForbidden):
            await update_saved_report(s, private.id, PLAIN, no_create, upd, registry=reg)


async def test_purge_deletes_old_soft_deleted_reports_and_groups(env: Env) -> None:
    reg = get_registry()
    async with env.factory() as s:
        rw = await _rights_in(s, STATS_RW)
        old = await create_saved_report(
            s, STATS_RW, rw, _in("rep48 old", visibility="groups", group_ids=[G30]), registry=reg
        )
        recent = await create_saved_report(s, STATS_RW, rw, _in("rep48 recent"), registry=reg)
        live = await create_saved_report(s, STATS_RW, rw, _in("rep48 live"), registry=reg)
        await delete_saved_report(s, old.id, STATS_RW, rw)
        await delete_saved_report(s, recent.id, STATS_RW, rw)
        await s.commit()
        later = utc_now() + timedelta(days=91)
        # `old` is purged because "now" is 91 days later; `recent` is aged to a day before
        await s.execute(
            text("UPDATE tiqora_report SET changed_at = :t WHERE id = :i"),
            {"t": utc_now() + timedelta(days=80), "i": recent.id},
        )
        assert await purge_deleted_reports(s, now=later) >= 1
        await s.commit()
        left = await s.execute(
            text("SELECT id FROM tiqora_report WHERE id IN (:a, :b, :c)"),
            {"a": old.id, "b": recent.id, "c": live.id},
        )
        ids = {r[0] for r in left.all()}
        groups = await s.execute(
            text("SELECT COUNT(*) FROM tiqora_report_group WHERE report_id = :i"), {"i": old.id}
        )
    assert ids == {recent.id, live.id}
    assert groups.scalar_one() == 0


# ---------------------------------------------------------------- definitions, keys


async def test_load_definition_reports_broken_json(env: Env) -> None:
    defn, err = load_definition('{"schema_version": 2, "source": "tickets"}')
    assert defn is None and "nicht mehr gelesen" in (err or "")
    defn, err = load_definition(dump_definition(ReportDefinition.model_validate(TICKETS_DEF)))
    assert err is None and defn is not None and defn.source == "tickets"


async def test_broken_stored_definition_resolves_to_definition_error(env: Env) -> None:
    reg = get_registry()
    async with env.factory() as s:
        plain = await _rights_in(s, PLAIN)
        rep = await create_saved_report(s, PLAIN, plain, _in("rep48 broken"), registry=reg)
        await s.execute(
            text("UPDATE tiqora_report SET definition = :d WHERE id = :i"),
            {"d": '{"schema_version": 2}', "i": rep.id},
        )
        await s.commit()
        listed = [r for r in await visible_saved_reports(s, PLAIN, admin=False) if r.id == rep.id]
        assert listed and listed[0].definition is None and listed[0].definition_error
        with pytest.raises(DefinitionError):
            await resolve_report_ref(s, str(rep.id), PLAIN, admin=False)


async def test_resolve_report_ref(env: Env) -> None:
    reg = get_registry()
    async with env.factory() as s:
        builtin = await resolve_report_ref(s, "builtin.ticket_kpis", OUTSIDER, admin=False)
        assert builtin.builtin is not None and builtin.saved is None
        for bad in ("builtin.nope", "0", "12abc", "-3", "", "01", "12\n"):
            with pytest.raises(ReportNotFound):
                await resolve_report_ref(s, bad, OUTSIDER, admin=False)
        plain = await _rights_in(s, PLAIN)
        rep = await create_saved_report(s, PLAIN, plain, _in("rep48 ref"), registry=reg)
        await s.commit()
        got = await resolve_report_ref(s, str(rep.id), PLAIN, admin=False)
        assert got.saved is not None and got.saved.id == rep.id and got.builtin is None
        with pytest.raises(ReportNotFound):
            await resolve_report_ref(s, str(rep.id), OUTSIDER, admin=False)


async def test_update_racing_a_delete_does_not_touch_the_deleted_row(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report is soft-deleted between the read and the UPDATE (M1)."""
    reg = get_registry()
    defn = ReportDefinition.model_validate(TICKETS_DEF)
    async with env.factory() as s:
        plain = await _rights_in(s, PLAIN)
        rep = await create_saved_report(s, PLAIN, plain, _in("rep48 racedel"), registry=reg)
        await s.commit()
        await delete_saved_report(s, rep.id, PLAIN, plain)
        await s.commit()
        before = await _row(s, rep.id)

        async def stale(*_a: Any, **_k: Any) -> Any:
            return rep  # read before the delete: still valid, version 1

        monkeypatch.setattr(saved_module, "get_saved_report", stale)
        with pytest.raises(ReportNotFound):
            await update_saved_report(
                s, rep.id, PLAIN, plain,
                SavedReportUpdateIn(name="rep48 resurrect", definition=defn, version=1),
                registry=reg,
            )  # fmt: skip
        monkeypatch.undo()
        await s.rollback()
        assert await _row(s, rep.id) == before  # name, valid flag and purge clock untouched


async def _row(s: AsyncSession, report_id: int) -> tuple[Any, ...]:
    r = await s.execute(
        text("SELECT name, valid, version, changed_at FROM tiqora_report WHERE id = :i"),
        {"i": report_id},
    )
    return tuple(r.one())
