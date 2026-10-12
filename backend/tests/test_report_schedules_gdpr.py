# ruff: noqa: E501
"""GDPR erasure and agent removal for report schedules (D14), both dialects.

Band 52300-52319: agents 52300 / 52301, tickets 52310, customer user 52311 (login
``rep52.kunde``, address kunde52@example.org), company ``REP52-CO``, saved reports from
the sequence the database assigns (owner 52300), schedules / runs by owner band.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.user_delete import blocking_references, delete_user_rows
from tiqora.gdpr.erasure import rollback_job, run_erasure
from tiqora.gdpr.saved_criteria import remove_erased_recipient_emails, scrub_schedule_items

pytestmark = pytest.mark.db
LO, HI = 52300, 52319
BAND = {"lo": LO, "hi": HI}
NOW = datetime(2026, 10, 11, 7, 0)
LOGIN = "rep52.kunde"
EMAIL = "kunde52@example.org"
COMPANY = "REP52-CO"
CU_ID, TICKET = 52311, 52310
CUSTOMER_PARAM_DEF = {
    "source": "tickets",
    "dimensions": [{"key": "queue"}],
    "measures": [{"key": "tickets"}],
    "parameters": [{"key": "p_kunde", "field": "customer_user", "op": "in"}],
}
_JOBS = "SELECT id FROM tiqora_gdpr_job WHERE actor = 'rep52'"
_CLEANUP = (
    f"DELETE FROM tiqora_gdpr_backup WHERE job_id IN ({_JOBS})",
    "DELETE FROM tiqora_gdpr_job WHERE actor = 'rep52'",
    "DELETE FROM tiqora_gdpr_audit WHERE actor = 'rep52'",
    "DELETE FROM tiqora_cache_invalidation"
    " WHERE ticket_id IS NULL OR ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_run_file WHERE run_id IN"
    " (SELECT id FROM tiqora_report_run WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_run WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_schedule_recipient WHERE schedule_id IN"
    " (SELECT id FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_schedule_item WHERE schedule_id IN"
    " (SELECT id FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM customer_user WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM customer_company WHERE customer_id LIKE 'REP52-%'",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    return (
        sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
        .replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
        .replace("postgresql://", "postgresql+asyncpg://", 1)
    )


def _run_sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt), BAND)
    engine.dispose()


@dataclass
class Env:
    sync_url: str
    factory: async_sessionmaker[AsyncSession]

    def _exec(self, sql: str, params: dict[str, Any] | None = None) -> Any:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                result = conn.execute(text(sql), params or {})
                return result.fetchall() if result.returns_rows else result.lastrowid
        finally:
            engine.dispose()

    def seed_agents(self) -> None:
        for uid in (52300, 52301):
            self._exec(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id, create_time,"
                " create_by, change_time, change_by) VALUES (:id, :l, 'x', 'Rep52', :n, 1, :t, 1, :t, 1)",
                {"id": uid, "l": f"rep52.agent{uid}", "n": f"A{uid}", "t": NOW},
            )

    def insert_saved_report(self, *, owner: int, definition: dict[str, Any]) -> int:
        self._exec(
            "INSERT INTO tiqora_report (name, owner_user_id, visibility, definition,"
            " schema_version, version, valid, created_at, created_by, changed_at, changed_by)"
            " VALUES ('rep52 report', :o, 'private', :d, 1, 1, :v, :t, :o, :t, :o)",
            {"o": owner, "d": json.dumps(definition), "v": True, "t": NOW},
        )
        return int(
            self._exec("SELECT MAX(id) FROM tiqora_report WHERE owner_user_id = :o", {"o": owner})[
                0
            ][0]
        )

    def insert_schedule(
        self,
        *,
        owner: int,
        items: list[tuple[str, dict[str, Any] | None]] | None = None,
        emails: list[str] | None = None,
        user_ids: list[int] | None = None,
    ) -> int:
        self._exec(
            "INSERT INTO tiqora_report_schedule (owner_user_id, name, format, recurrence, time_zone,"
            " language, subject, body, only_if_nonempty, active, failure_count, version,"
            " created_at, changed_at) VALUES (:o, 'rep52 schedule', 'csv', '{}', 'UTC', 'de', '',"
            " '', :f, :a, 0, 1, :t, :t)",
            {"o": owner, "f": False, "a": True, "t": NOW},
        )
        sid = int(
            self._exec(
                "SELECT MAX(id) FROM tiqora_report_schedule WHERE owner_user_id = :o", {"o": owner}
            )[0][0]
        )
        for pos, (key, overrides) in enumerate(items or []):
            self._exec(
                "INSERT INTO tiqora_report_schedule_item (schedule_id, position, report_key, overrides)"
                " VALUES (:s, :p, :k, :ov)",
                {
                    "s": sid,
                    "p": pos,
                    "k": key,
                    "ov": None if overrides is None else json.dumps(overrides),
                },
            )
        for addr in emails or []:
            self._exec(
                "INSERT INTO tiqora_report_schedule_recipient (schedule_id, kind, email)"
                " VALUES (:s, 'email', :e)",
                {"s": sid, "e": addr},
            )
        for uid in user_ids or []:
            self._exec(
                "INSERT INTO tiqora_report_schedule_recipient (schedule_id, kind, ref_id)"
                " VALUES (:s, 'user', :u)",
                {"s": sid, "u": uid},
            )
        return sid

    def insert_run(self, *, owner: int, schedule_id: int, file: bytes) -> int:
        self._exec(
            "INSERT INTO tiqora_report_run (schedule_id, schedule_name, owner_user_id, run_kind,"
            " owner_only, status, report_keys, created_at) VALUES (:s, 'rep52', :o, 'schedule', :f,"
            " 'succeeded', '[]', :t)",
            {"s": schedule_id, "o": owner, "f": False, "t": NOW},
        )
        rid = int(
            self._exec(
                "SELECT MAX(id) FROM tiqora_report_run WHERE owner_user_id = :o", {"o": owner}
            )[0][0]
        )
        self._exec(
            "INSERT INTO tiqora_report_run_file (run_id, name, content_type, size_bytes, data,"
            " created_at) VALUES (:r, 'a.pdf', 'application/pdf', :n, :d, :t)",
            {"r": rid, "n": len(file), "d": file, "t": NOW},
        )
        return rid

    def load_items(self, sid: int) -> list[Any]:
        return list(
            self._exec(
                "SELECT position, report_key, overrides FROM tiqora_report_schedule_item"
                " WHERE schedule_id = :s ORDER BY position",
                {"s": sid},
            )
        )

    def load_schedule(self, sid: int) -> Any:
        rows = self._exec(
            "SELECT id, version FROM tiqora_report_schedule WHERE id = :s", {"s": sid}
        )
        return rows[0] if rows else None

    def load_recipients(self, sid: int) -> list[Any]:
        return list(
            self._exec(
                "SELECT kind, ref_id, email FROM tiqora_report_schedule_recipient"
                " WHERE schedule_id = :s ORDER BY id",
                {"s": sid},
            )
        )

    def load_run(self, rid: int) -> Any:
        rows = self._exec("SELECT id FROM tiqora_report_run WHERE id = :r", {"r": rid})
        return rows[0] if rows else None

    def count_run_files(self, rid: int) -> int:
        return int(
            self._exec("SELECT COUNT(*) FROM tiqora_report_run_file WHERE run_id = :r", {"r": rid})[
                0
            ][0]
        )

    def seed_customer_user(self) -> None:
        self._exec(
            "INSERT INTO customer_company (customer_id, name, street, zip, city, country, valid_id,"
            " create_time, create_by, change_time, change_by) VALUES (:c, 'Rep52 Co', 'Main 1',"
            " '10115', 'Berlin', 'DE', 1, :t, 1, :t, 1)",
            {"c": COMPANY, "t": NOW},
        )
        self._exec(
            "INSERT INTO customer_user (id, login, email, customer_id, first_name, last_name, phone,"
            " valid_id, create_time, create_by, change_time, change_by) VALUES (:id, :l, :e, :c,"
            " 'Target', 'Person', '+49 30 1234567', 1, :t, 1, :t, 1)",
            {"id": CU_ID, "l": LOGIN, "e": EMAIL, "c": COMPANY, "t": NOW},
        )
        self._exec(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, customer_id, customer_user_id,"
            " timeout, until_time, escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time, change_by)"
            " VALUES (:id, '2026101052310', 'rep52 erase me', 1, 1, 1, 1, 1, 3, 4, :c, :l, 0, 0, 0, 0,"
            " 0, 0, 0, :t, 1, :t, 1)",
            {"id": TICKET, "c": COMPANY, "l": LOGIN, "t": NOW},
        )

    async def run_erasure(self, *, mode: str = "anonymize") -> Any:
        return await run_erasure(
            self.factory,
            Settings(),
            customer_user_ids=[CU_ID],
            mode=mode,  # type: ignore[arg-type]
            seed=7,
            actor="rep52",
            force_parallel=True,
        )


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _CLEANUP)
    _URLS.clear()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    _run_sql(sync_url, _CLEANUP)
    e = Env(sync_url, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
    e.seed_agents()
    try:
        yield e
    finally:
        _run_sql(sync_url, _CLEANUP)
        await engine.dispose()


async def test_erasure_rewrites_schedule_item_parameters(env: Env) -> None:
    report_id = env.insert_saved_report(owner=52300, definition=CUSTOMER_PARAM_DEF)
    sid = env.insert_schedule(
        owner=52300,
        items=[
            (str(report_id), {"parameters": {"p_kunde": [LOGIN, "rep52.other"]}}),
            ("builtin.volume", None),
        ],
    )
    backups: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    async def backup(table: str, pk: dict[str, Any], original: dict[str, Any]) -> None:
        backups.append((table, pk, original))

    async with env.factory() as s:
        changed = await scrub_schedule_items(s, {LOGIN: "erased-52300"}, None, backup=backup)
        await s.commit()
    assert changed == 1
    items = env.load_items(sid)
    assert json.loads(items[0][2])["parameters"]["p_kunde"] == ["erased-52300", "rep52.other"]
    assert items[1][2] is None
    assert env.load_schedule(sid)[1] == 2
    assert backups[0][0] == "tiqora_report_schedule_item"
    assert backups[0][1] == {"schedule_id": sid, "position": 0}
    assert LOGIN in backups[0][2]["overrides"]


async def test_erasure_ignores_non_customer_parameters(env: Env) -> None:
    definition = {
        **CUSTOMER_PARAM_DEF,
        "parameters": [{"key": "p_queue", "field": "queue", "op": "in"}],
    }
    report_id = env.insert_saved_report(owner=52300, definition=definition)
    sid = env.insert_schedule(
        owner=52300, items=[(str(report_id), {"parameters": {"p_queue": [LOGIN]}})]
    )
    async with env.factory() as s:
        assert await scrub_schedule_items(s, {LOGIN: "erased-52300"}, None, backup=None) == 0
        await s.commit()
    assert json.loads(env.load_items(sid)[0][2])["parameters"]["p_queue"] == [LOGIN]
    assert env.load_schedule(sid)[1] == 1


async def test_erasure_removes_recipient_email(env: Env) -> None:
    sid = env.insert_schedule(owner=52300, emails=[EMAIL, "other52@example.org"])
    async with env.factory() as s:
        removed = await remove_erased_recipient_emails(s, ["Kunde52@Example.org"])
        await s.commit()
    assert removed == 1
    assert [r[2] for r in env.load_recipients(sid)] == ["other52@example.org"]
    assert env.load_schedule(sid)[1] == 2


@pytest.mark.parametrize("mode", ["anonymize", "delete"])
async def test_full_erasure_flow_calls_both_and_rollback_restores_items(
    env: Env, mode: str
) -> None:
    env.seed_customer_user()
    report_id = env.insert_saved_report(owner=52300, definition=CUSTOMER_PARAM_DEF)
    sid = env.insert_schedule(
        owner=52300,
        emails=[EMAIL],
        items=[(str(report_id), {"parameters": {"p_kunde": [LOGIN]}})],
    )
    result = await env.run_erasure(mode=mode)
    assert env.load_recipients(sid) == []
    scrubbed = json.loads(env.load_items(sid)[0][2])["parameters"]["p_kunde"]
    assert scrubbed != [LOGIN] and len(scrubbed) == 1
    assert result.counts["schedule_items"] == 1 and result.counts["schedule_recipients"] == 1
    assert env.load_schedule(sid)[1] == 3  # item scrub + recipient removal
    await rollback_job(env.factory, Settings(), result.job_id, force_parallel=True, actor="rep52")
    assert json.loads(env.load_items(sid)[0][2])["parameters"]["p_kunde"] == [LOGIN]
    assert env.load_schedule(sid)[1] == 4
    assert env.load_recipients(sid) == []  # recipient removal is documented as not restored


async def test_agent_delete_removes_schedules_and_runs(env: Env) -> None:
    own = env.insert_schedule(owner=52301, user_ids=[52301])
    other = env.insert_schedule(owner=52300, user_ids=[52300, 52301])
    untouched = env.insert_schedule(owner=52300, user_ids=[52300])
    version = env.load_schedule(other)[1]
    run_id = env.insert_run(owner=52301, schedule_id=own, file=b"%PDF-1.7")
    async with env.factory() as s:
        refs = await blocking_references(s, 52301)
        assert [r for r in refs if r.table.startswith("tiqora_report")] == []
        await delete_user_rows(s, 52301)
        await s.commit()
    assert env.load_schedule(own) is None
    assert env.load_run(run_id) is None and env.count_run_files(run_id) == 0
    assert [r[1] for r in env.load_recipients(other)] == [52300]
    assert env.load_schedule(other) is not None
    # an open editor of the other schedule must not save the removed recipient back
    assert env.load_schedule(other)[1] == version + 1
    assert env.load_schedule(untouched)[1] == version
