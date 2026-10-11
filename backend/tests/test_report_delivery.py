"""Scheduled report delivery: "Jetzt senden", the delivery job, run history, file download.

Both dialects. Band 52200-52259 (names contain ``rep52``): agents 52200 ``rep52.owner`` (``ro``
on queue 52230 via group 52220, ``rw`` on ``stats``, has an address), 52201 ``rep52.colleague``
(address), 52202 ``rep52.noemail`` (no address), 52203 ``rep52.empty`` (``rw`` on ``stats``,
``ro`` on the empty queue 52232 via group 52222); queues 52230 (tickets 52240, 52241), 52231
(ticket 52242; group 52221, NOT visible to the owner until a test grants it) and 52232 (no
tickets). Mail is captured (``delivery.build_outbound_sender`` is patched). Everything this
module writes is deleted again (runs and files, delivery jobs, mail-log rows, settings).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator, Generator
from datetime import datetime
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any

import aiosmtplib
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.reporting.delivery as delivery
from tests.test_report_pdf import needs_pdf
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.reports import get_report_cache
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraJob,
    TiqoraMailLog,
    TiqoraReportRun,
    TiqoraReportRunFile,
    TiqoraReportSchedule,
)
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import get_registry
from tiqora.jobs.context import JobCancelled, JobContext
from tiqora.jobs.runner import JobRunner
from tiqora.jobs.service import utc_now
from tiqora.reporting.delivery import (
    CHECKPOINT_SENDING,
    SHARE_LOST_MESSAGE,
    ReportDeliveryPayload,
    apply_placeholders,
    create_run,
    enqueue_delivery,
    on_delivery_interrupted,
    resolve_report_sender,
    run_delivery_job,
    scrub_text,
)
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.schedules import FAILURES_BEFORE_PAUSE, load_schedule
from tiqora.reporting.settings import REPORTING_SECTION
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

LO, HI = 52200, 52259
OWNER, COLLEAGUE, NOEMAIL, EMPTY = 52200, 52201, 52202, 52203
Q_OWN, Q_OTHER, Q_EMPTY = 52230, 52231, 52232
G_OWN, G_OTHER, G_EMPTY = 52220, 52221, 52222
TICKETS = (52240, 52241, 52242)
BUILT = datetime(2026, 9, 20, 11, 0)
STAMP = "2026-10-01 00:00:00"
SENDER = "reports@example.org"
LOGINS = {
    OWNER: "rep52.owner",
    COLLEAGUE: "rep52.colleague",
    NOEMAIL: "rep52.noemail",
    EMPTY: "rep52.empty",
}
NAMES = {
    OWNER: ("Olga", "Owner"),
    COLLEAGUE: ("Carla", "Colleague"),
    NOEMAIL: ("Nora", "Noemail"),
    EMPTY: ("Emil", "Empty"),
}
EMAILS = {
    OWNER: "rep52.owner@example.org",
    COLLEAGUE: "Rep52.Colleague@Example.org",
    EMPTY: "rep52.empty@example.org",
}
TICKETS_DEF = {
    "source": "tickets",
    "dimensions": [{"key": "queue"}],
    "measures": [{"key": "tickets"}],
}
BASE: dict[str, Any] = {
    "name": "rep52 Wochenbericht",
    "items": [{"report_key": "builtin.open_now"}],
    "format": "csv",
    "recurrence": {"kind": "weekly", "time": "07:00", "weekdays": [1]},
    "time_zone": "Europe/Berlin",
    "recipients": {"user_ids": [OWNER, COLLEAGUE, NOEMAIL]},
}
_BAND = {"lo": LO, "hi": HI}
_STATS = "(SELECT id FROM permission_groups WHERE name = 'stats' AND valid_id = 1)"
_GRANT_STATS = (
    "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
    f" change_time, change_by) SELECT {OWNER}, id, 'rw', '{STAMP}', 1, '{STAMP}', 1"
    " FROM permission_groups WHERE name = 'stats' AND valid_id = 1",
)
_REVOKE_STATS = (f"DELETE FROM group_user WHERE user_id = {OWNER} AND group_id = {_STATS}",)
_RESET = (
    "DELETE FROM tiqora_report_run_file WHERE run_id IN"
    " (SELECT id FROM tiqora_report_run WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_run WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE kind = 'core.report_delivery')",
    "DELETE FROM tiqora_job WHERE kind = 'core.report_delivery'",
    "DELETE FROM tiqora_mail_log WHERE detail LIKE 'report_run %'",
    "DELETE FROM tiqora_report_schedule_recipient WHERE schedule_id IN"
    " (SELECT id FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_schedule_item WHERE schedule_id IN"
    " (SELECT id FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_state",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'"
    " OR {key} = 'notification.sender_email'",
    f"DELETE FROM group_user WHERE user_id = {OWNER} AND group_id = {G_OTHER}",
    "UPDATE users SET valid_id = 1 WHERE id BETWEEN :lo AND :hi",
    f"DELETE FROM group_user WHERE user_id = {OWNER} AND group_id = {_STATS}",
)
#: what every test starts from (the owner shares, a sender is configured)
_ARRANGE = (
    *_GRANT_STATS,
    "INSERT INTO tiqora_settings ({key}, value) VALUES ('notification.sender_email', '"
    + SENDER
    + "')",
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM user_preferences WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
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
    first, last = ("Admin", "OTRS") if uid == 1 else NAMES[uid]
    return AuthenticatedUser(
        id=uid, login=login, first_name=first, last_name=last, auth_method="session"
    )


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    stamp = {"t": BUILT}
    with engine.begin() as conn:
        st_new = conn.execute(text("SELECT id FROM ticket_state WHERE name = 'new'")).scalar_one()
        for uid, login in LOGINS.items():
            first, last = NAMES[uid]
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "n": last, **stamp},
            )
        for uid, address in EMAILS.items():
            conn.execute(
                text(
                    "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                    " VALUES (:u, 'UserEmail', :v)"
                ),
                {"u": uid, "v": address.encode()},
            )
        for gid in (G_OWN, G_OTHER, G_EMPTY):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep52-group-{gid}", **stamp},
            )
        for qid, gid in ((Q_OWN, G_OWN), (Q_OTHER, G_OTHER), (Q_EMPTY, G_EMPTY)):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": f"rep52-queue-{qid}", "g": gid, **stamp},
            )
        stats = conn.execute(
            text("SELECT id FROM permission_groups WHERE name = 'stats' AND valid_id = 1")
        ).scalar_one()
        for uid, gid, key in (
            (OWNER, G_OWN, "ro"),
            (EMPTY, G_EMPTY, "ro"),
            (EMPTY, stats, "rw"),
        ):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "k": key, **stamp},
            )
        for tid, qid in zip(TICKETS, (Q_OWN, Q_OWN, Q_OTHER), strict=True):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, :s, 'rep52-cust',"
                    " 'rep52@example.org', 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"20260900{tid}", "title": f"rep52 ticket {tid}", "q": qid,
                 "u": OWNER, "s": st_new, "t": BUILT},
            )  # fmt: skip
    engine.dispose()


class _MemoryCache:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ex: int) -> None:
        return None


class Env:
    def __init__(
        self, sync_url: str, factory: async_sessionmaker[AsyncSession], client: AsyncClient
    ) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.client = client
        self.user = user(OWNER)

    def sql(self, *statements: str) -> None:
        _run_sql(self.sync_url, statements)

    async def setting(self, **values: Any) -> None:
        async with self.factory() as s:
            await save_section_values(
                s, REPORTING_SECTION, values, secret_key=Settings(environment="test").secret_key
            )
            await s.commit()

    def grant_queue(self, uid: int, group_id: int) -> None:
        self.sql(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            f" change_time, change_by) VALUES ({uid}, {group_id}, 'ro', '{STAMP}', 1, '{STAMP}', 1)"
        )

    def revoke_queue(self, uid: int, group_id: int) -> None:
        self.sql(f"DELETE FROM group_user WHERE user_id = {uid} AND group_id = {group_id}")

    def revoke_stats(self) -> None:
        self.sql(*_REVOKE_STATS)

    async def run_job(
        self, job_id: int, *, expect: str = "succeeded", ignore_due: bool = False
    ) -> TiqoraJob:
        """Claim and execute one queued job through the real runner path."""
        runner = JobRunner(
            session_factory=self.factory,
            settings=Settings(environment="test"),
            registry=get_registry(),
        )
        async with self.factory() as s:
            row = (
                await s.execute(
                    select(
                        TiqoraJob.kind, TiqoraJob.payload, TiqoraJob.created_by, TiqoraJob.due_at
                    ).where(TiqoraJob.id == job_id)
                )
            ).one()
        kind, payload, created_by, due_at = row
        assert ignore_due or due_at is None or due_at <= utc_now(), "the job is not due yet"
        attempt = await runner._claim(job_id)
        assert attempt, "the job could not be claimed"
        await runner._execute(job_id, kind, payload, created_by, attempt=attempt)
        async with self.factory() as s:
            job = (
                await s.execute(
                    select(TiqoraJob)
                    .where(TiqoraJob.id == job_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
        assert job.status == expect, (job.status, job.error)
        return job

    async def load_runs(self, schedule_id: int) -> list[TiqoraReportRun]:
        async with self.factory() as s:
            rows = (
                await s.execute(
                    select(TiqoraReportRun)
                    .where(TiqoraReportRun.schedule_id == schedule_id)
                    .order_by(TiqoraReportRun.id.desc())
                    .execution_options(populate_existing=True)
                )
            ).scalars()
            return list(rows)

    async def load_run_files(self, run_id: int) -> list[tuple[int, str, int]]:
        async with self.factory() as s:
            rows = await s.execute(
                select(
                    TiqoraReportRunFile.id,
                    TiqoraReportRunFile.name,
                    TiqoraReportRunFile.size_bytes,
                ).where(TiqoraReportRunFile.run_id == run_id)
            )
            return [(int(a), str(b), int(c)) for a, b, c in rows.all()]

    async def load_schedule(self, schedule_id: int) -> TiqoraReportSchedule:
        async with self.factory() as s:
            return (
                await s.execute(
                    select(TiqoraReportSchedule)
                    .where(TiqoraReportSchedule.id == schedule_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()

    async def queue_run(
        self, schedule_id: int, *, owner_only: bool, kind: str = "schedule"
    ) -> tuple[int, int]:
        """Create a run and queue its job without the route (as the scheduler does)."""
        async with self.factory() as s:
            schedule = await load_schedule(s, schedule_id)
            assert schedule is not None
            run = await create_run(
                s,
                schedule,
                run_kind=kind,  # type: ignore[arg-type]
                owner_only=owner_only,
                due_at=utc_now() if kind == "schedule" else None,
                now=utc_now(),
            )
            job = await enqueue_delivery(s, run, dedupe_key=f"rep52-run-{run.id}")
            await s.commit()
            return int(run.id), int(job.id)

    async def schedule(self, **changes: Any) -> int:
        self.user = user(OWNER)
        resp = await self.client.post("/api/v1/report-schedules", json={**BASE, **changes})
        assert resp.status_code == 201, resp.text
        return int(resp.json()["id"])

    async def run_now(self, schedule_id: int, *, owner_only: bool = True) -> dict[str, Any]:
        resp = await self.client.post(
            f"/api/v1/report-schedules/{schedule_id}/run", json={"owner_only": owner_only}
        )
        assert resp.status_code == 202, resp.text
        return resp.json()  # type: ignore[no-any-return]


@pytest.fixture
def mail(monkeypatch: pytest.MonkeyPatch) -> CapturingMailSender:
    sender = CapturingMailSender()

    async def fake_build(*args: Any, **kwargs: Any) -> CapturingMailSender:
        return sender

    monkeypatch.setattr(delivery, "build_outbound_sender", fake_build)
    return sender


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    if sync_url not in _SEEDED:
        _run_sql(sync_url, _CLEANUP)
        _seed(sync_url)
        async with factory() as s:
            await rebuild_ticket_facts(s, TICKETS, now=BUILT)
            await s.commit()
        _SEEDED.add(sync_url)
    _run_sql(sync_url, _RESET)
    _run_sql(sync_url, _ARRANGE)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    holder: list[Env] = []
    app.dependency_overrides[get_current_user] = lambda: holder[0].user
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_report_cache] = _MemoryCache
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            holder.append(Env(sync_url, factory, c))
            yield holder[0]
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


def _attachments(msg: EmailMessage) -> list[EmailMessage]:
    return list(msg.iter_attachments())


def _attachment_text(msg: EmailMessage, index: int = 0) -> str:
    payload = _attachments(msg)[index].get_payload(decode=True)
    assert isinstance(payload, bytes)
    return payload.decode("utf-8-sig")


def _envelope(msg: EmailMessage) -> list[str]:
    values = [str(v) for f in ("To", "Cc", "Bcc") for v in msg.get_all(f, [])]
    return [addr for _name, addr in getaddresses(values) if addr]


# ---------------------------------------------------------------- pure


def test_subject_placeholders_are_not_recursive() -> None:
    out = apply_placeholders(
        "{{schedule}} – {{period}} {{unknown}}", {"schedule": "{{period}}", "period": "Sep 2026"}
    )
    assert out == "{{period}} – Sep 2026 {{unknown}}"


def test_scrub_text_removes_addresses_and_truncates() -> None:
    raw = "550 5.1.1 <rep52.colleague@example.org>: Recipient rejected for a@example.org, ok"
    out = scrub_text(raw, 1000)
    assert "@" not in out and "Recipient rejected" in out
    assert len(scrub_text("x" * 50, 10)) == 10


# ---------------------------------------------------------------- run now


@needs_pdf
async def test_run_now_owner_only_sends_pdf(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule(format="pdf")
    acc = await env.run_now(sid, owner_only=True)
    assert acc["run_id"] > 0 and acc["job"]["status"] == "queued"
    await env.run_job(acc["job"]["job_id"])
    msg = mail.sent[-1]
    assert msg["Bcc"] == "rep52.owner@example.org"
    assert msg["To"] == "undisclosed-recipients:;" and msg["From"] == SENDER
    assert mail.envelope_senders[-1] == ""
    atts = _attachments(msg)
    assert len(atts) == 1 and atts[0].get_content_type() == "application/pdf"
    runs = (await env.client.get(f"/api/v1/report-schedules/{sid}/runs")).json()
    assert runs[0]["status"] == "succeeded" and runs[0]["recipient_count"] == 1
    assert runs[0]["run_kind"] == "manual" and runs[0]["owner_only"] is True
    assert runs[0]["id"] == acc["run_id"] and runs[0]["job_id"] == acc["job"]["job_id"]
    file = runs[0]["files"][0]
    assert file["content_type"] == "application/pdf" and file["name"].endswith(".pdf")
    dl = await env.client.get(f"/api/v1/report-runs/{runs[0]['id']}/files/{file['id']}")
    assert dl.status_code == 200 and dl.content.startswith(b"%PDF")
    assert "attachment" in dl.headers["content-disposition"]
    sched = await env.load_schedule(sid)
    assert sched.last_status == "succeeded" and sched.failure_count == 0


async def test_mail_envelope_is_exactly_the_recipient_list(
    env: Env, mail: CapturingMailSender
) -> None:
    sid = await env.schedule()
    acc = await env.run_now(sid, owner_only=False)
    await env.run_job(acc["job"]["job_id"])
    msg = mail.sent[-1]
    # To is an empty group: the only envelope recipients are the Bcc list, never the sender
    assert sorted(_envelope(msg)) == ["rep52.colleague@example.org", "rep52.owner@example.org"]
    assert SENDER not in _envelope(msg)
    assert msg["Subject"].startswith("Bericht: rep52 Wochenbericht (")
    body = msg.get_body(preferencelist=("plain",))
    assert body is not None
    text_body = body.get_content()
    assert "Olga Owner" in text_body and "Berichte, Zeitpläne" in text_body


async def test_all_recipients_partial_without_addresses_in_history(
    env: Env, mail: CapturingMailSender
) -> None:
    sid = await env.schedule(format="xlsx")
    acc = await env.run_now(sid, owner_only=False)
    await env.run_job(acc["job"]["job_id"])
    msg = mail.sent[-1]
    assert set(str(msg["Bcc"]).split(", ")) == {
        "rep52.owner@example.org",
        "rep52.colleague@example.org",
    }
    att = _attachments(msg)[0]
    assert att.get_content_type().endswith("spreadsheetml.sheet")
    run = (await env.client.get(f"/api/v1/report-schedules/{sid}/runs")).json()[0]
    assert run["status"] == "partial" and run["recipient_count"] == 2
    assert "keine E-Mail-Adresse" in run["detail"]
    assert "@" not in (run["detail"] or "") and "@" not in (run["error"] or "")
    assert (await env.load_schedule(sid)).last_status == "partial"


async def test_custom_subject_and_body_placeholders(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule(
        subject="{{schedule}} / {{reports}} / {{owner}}", body="Hallo\n{{unknown}} {{schedule}}"
    )
    await env.run_job((await env.run_now(sid))["job"]["job_id"])
    msg = mail.sent[-1]
    assert msg["Subject"] == "rep52 Wochenbericht / Offene Tickets jetzt / Olga Owner"
    body = msg.get_body(preferencelist=("plain",))
    assert body is not None and "{{unknown}} rep52 Wochenbericht" in body.get_content()


async def test_csv_bundle_is_zipped(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule(
        items=[{"report_key": "builtin.open_now"}, {"report_key": "builtin.open_now"}]
    )
    await env.run_job((await env.run_now(sid))["job"]["job_id"])
    atts = _attachments(mail.sent[-1])
    assert len(atts) == 1 and atts[0].get_content_type() == "application/zip"
    payload = atts[0].get_payload(decode=True)
    assert isinstance(payload, bytes)
    names = zipfile.ZipFile(io.BytesIO(payload)).namelist()
    assert len(names) == 2 and len(set(names)) == 2 and all(n.endswith(".csv") for n in names)


async def test_run_uses_owner_scope_at_run_time(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule()
    await env.run_job((await env.run_now(sid))["job"]["job_id"])
    first = _attachment_text(mail.sent[-1])
    assert "rep52-queue-52230" in first and "rep52-queue-52231" not in first
    env.grant_queue(OWNER, G_OTHER)  # the owner gains queue 52231 AFTER saving
    await env.run_job((await env.run_now(sid))["job"]["job_id"])
    assert "rep52-queue-52231" in _attachment_text(mail.sent[-1])
    env.revoke_queue(OWNER, G_OTHER)
    await env.run_job((await env.run_now(sid))["job"]["job_id"])
    assert "rep52-queue-52231" not in _attachment_text(mail.sent[-1])


# ---------------------------------------------------------------- run-time checks


async def test_owner_without_access_fails_without_mail(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule()
    await env.setting(access="stats_group")
    env.revoke_stats()
    _run_id, job_id = await env.queue_run(sid, owner_only=False)
    sent_before = len(mail.sent)
    await env.run_job(job_id, expect="failed")
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and "keinen Zugriff" in (run.error or "")
    assert all("rep52.colleague" not in str(m["Bcc"] or "") for m in mail.sent[sent_before:])
    assert (await env.load_schedule(sid)).failure_count == 1


async def test_owner_lost_share_right_fails_run(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule()
    env.revoke_stats()  # all_agents: still allowed to run reports, but no share right any more
    _run_id, job_id = await env.queue_run(sid, owner_only=False)
    sent_before = len(mail.sent)
    await env.run_job(job_id, expect="failed")
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and run.error == SHARE_LOST_MESSAGE
    new = mail.sent[sent_before:]
    assert all(not m["Bcc"] for m in new)  # nothing went to the recipients
    assert new and new[-1]["To"] == "rep52.owner@example.org"
    # the owner-only variant of the same schedule is still fine
    _run_id, job_id = await env.queue_run(sid, owner_only=True)
    await env.run_job(job_id)
    assert mail.sent[-1]["Bcc"] == "rep52.owner@example.org"


async def test_all_recipients_run_refused_for_the_owner_without_share_right(env: Env) -> None:
    sid = await env.schedule()
    env.revoke_stats()
    env.user = user(1)  # an admin starts it; the OWNER's right decides
    resp = await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": False})
    assert resp.status_code == 403 and resp.json()["detail"]["message"] == SHARE_LOST_MESSAGE
    assert (await env.load_runs(sid)) == []
    ok = await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": True})
    assert ok.status_code == 202


async def test_deleted_report_fails_run_and_mails_owner(
    env: Env, mail: CapturingMailSender
) -> None:
    env.user = user(OWNER)
    saved = await env.client.post(
        "/api/v1/reports", json={"name": "rep52 r", "definition": TICKETS_DEF}
    )
    assert saved.status_code == 201, saved.text
    key = saved.json()["key"]
    sid = await env.schedule(items=[{"report_key": "builtin.open_now"}, {"report_key": key}])
    assert (await env.client.delete(f"/api/v1/reports/{key}")).status_code == 204
    acc = await env.run_now(sid, owner_only=False)
    await env.run_job(acc["job"]["job_id"], expect="failed")
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and "nicht mehr verfügbar" in (run.error or "")
    assert "rep52 r" in (run.error or "")
    last = mail.sent[-1]
    assert last["To"] == "rep52.owner@example.org" and not _attachments(last)
    assert "rep52 r" not in str(last["Subject"]) or "rep52 Wochenbericht" in str(last["Subject"])
    manual = await env.load_schedule(sid)
    assert manual.failure_count == 0  # a manual run does not count towards the auto-pause


async def test_only_if_nonempty_skips(env: Env, mail: CapturingMailSender) -> None:
    env.user = user(EMPTY)
    resp = await env.client.post(
        "/api/v1/report-schedules",
        json={**BASE, "only_if_nonempty": True, "recipients": {"user_ids": [EMPTY]}},
    )
    assert resp.status_code == 201, resp.text
    sid = resp.json()["id"]
    sent_before = len(mail.sent)
    acc = await env.run_now(sid, owner_only=False)
    await env.run_job(acc["job"]["job_id"])
    assert len(mail.sent) == sent_before
    run = (await env.load_runs(sid))[0]
    assert run.status == "skipped" and "nichts versendet" in (run.detail or "")
    sched = await env.load_schedule(sid)
    assert sched.last_status == "skipped" and sched.failure_count == 0
    assert await env.load_run_files(run.id) == []


async def test_paused_schedule_skips_scheduled_runs_only(
    env: Env, mail: CapturingMailSender
) -> None:
    sid = await env.schedule()
    _run_id, job_id = await env.queue_run(sid, owner_only=False)
    async with env.factory() as s:
        await s.execute(
            update(TiqoraReportSchedule).where(TiqoraReportSchedule.id == sid).values(active=False)
        )
        await s.commit()
    await env.run_job(job_id)
    run = (await env.load_runs(sid))[0]
    assert run.status == "skipped" and not mail.sent
    _run_id, job_id = await env.queue_run(sid, owner_only=True, kind="manual")
    await env.run_job(job_id)
    assert (await env.load_runs(sid))[0].status == "succeeded"


async def test_no_sender_configured_fails(env: Env, mail: CapturingMailSender) -> None:
    env.sql("DELETE FROM tiqora_settings WHERE {key} = 'notification.sender_email'")
    async with env.factory() as s:
        # the shipped Znuny value is an unresolved tag, so nothing is usable
        assert await resolve_report_sender(s) is None
    sid = await env.schedule()
    await env.run_job((await env.run_now(sid))["job"]["job_id"], expect="failed")
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and "Kein Absender" in (run.error or "")
    assert not mail.sent
    # with a sender the same function returns the bare address
    env.sql(_ARRANGE[-1])
    async with env.factory() as s:
        assert await resolve_report_sender(s) == SENDER


# ---------------------------------------------------------------- SMTP errors


async def test_temporary_smtp_error_retries_without_rerender(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await env.schedule()
    sends: list[EmailMessage] = []

    class FlakySender:
        async def send(self, message: EmailMessage, *, envelope_from: str | None = None) -> None:
            sends.append(message)
            if len(sends) == 1:
                raise aiosmtplib.SMTPResponseException(451, "4.3.0 try again")

    async def fake_build(*a: Any, **k: Any) -> FlakySender:
        return FlakySender()

    monkeypatch.setattr(delivery, "build_outbound_sender", fake_build)
    renders: list[int] = []
    real_csv = delivery.write_report_csv
    monkeypatch.setattr(
        delivery, "write_report_csv", lambda *a, **k: renders.append(1) or real_csv(*a, **k)
    )
    _run_id, job_id = await env.queue_run(sid, owner_only=True)
    job = await env.run_job(job_id, expect="queued")  # JobRetry -> back in the queue
    assert job.due_at is not None and job.due_at > utc_now()
    run_row = (await env.load_runs(sid))[0]
    assert run_row.status == "running" and len(await env.load_run_files(run_row.id)) == 1
    await env.run_job(job_id, ignore_due=True)  # second claim (attempt 2)
    run_row = (await env.load_runs(sid))[0]
    assert run_row.status == "succeeded" and len(sends) == 2
    assert len(renders) == 1  # the stored file is sent again, not rendered again
    assert len(await env.load_run_files(run_row.id)) == 1
    assert [m.get_payload(decode=False) is not None for m in sends] == [True, True]
    assert (await env.load_schedule(sid)).last_status == "succeeded"


async def test_permanent_smtp_error_fails_counts_and_pauses(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await env.schedule()
    owner_mails: list[EmailMessage] = []

    class Rejecting:
        async def send(self, message: EmailMessage, *, envelope_from: str | None = None) -> None:
            if message["Bcc"]:
                raise aiosmtplib.SMTPResponseException(
                    550, "5.1.1 <rep52.colleague@example.org>: Recipient address rejected"
                )
            owner_mails.append(message)  # the failure mail to the owner (no Bcc)

    async def fake_build(*a: Any, **k: Any) -> Rejecting:
        return Rejecting()

    monkeypatch.setattr(delivery, "build_outbound_sender", fake_build)
    sched_row = await env.load_schedule(sid)
    for attempt in range(1, FAILURES_BEFORE_PAUSE + 1):
        _run_id, job_id = await env.queue_run(sid, owner_only=False)
        await env.run_job(job_id, expect="failed")
        sched_row = await env.load_schedule(sid)
        assert sched_row.failure_count == attempt and sched_row.last_status == "failed"
    assert sched_row.active is False and sched_row.next_run_at is None
    assert (sched_row.paused_reason or "").startswith("Nach 3 Fehlversuchen angehalten")
    assert "@" not in (sched_row.paused_reason or "")
    assert len(owner_mails) == FAILURES_BEFORE_PAUSE
    assert all(m["To"] == "rep52.owner@example.org" for m in owner_mails)
    assert "angehalten" in owner_mails[-1].get_content() and "angehalten" not in (
        owner_mails[0].get_content()
    )
    run_row = (await env.load_runs(sid))[0]
    assert "nicht versendet" in (run_row.error or "") and "@" not in (run_row.error or "")
    assert run_row.status == "failed" and run_row.finished_at is not None


async def test_attempts_are_exhausted_for_temporary_errors(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = await env.schedule()

    class Down:
        async def send(self, message: EmailMessage, *, envelope_from: str | None = None) -> None:
            if message["Bcc"]:
                raise OSError("connection to rep52.relay.example.org refused")

    async def fake_build(*a: Any, **k: Any) -> Down:
        return Down()

    monkeypatch.setattr(delivery, "build_outbound_sender", fake_build)
    _run_id, job_id = await env.queue_run(sid, owner_only=True)
    await env.run_job(job_id, expect="queued")
    await env.run_job(job_id, expect="queued", ignore_due=True)
    await env.run_job(job_id, expect="failed", ignore_due=True)  # attempt 3 is the last one
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and "nicht versendet" in (run.error or "")
    assert "rep52.relay.example.org" not in (run.error or "") or "@" not in (run.error or "")


async def test_attachment_too_large(
    env: Env, mail: CapturingMailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.setting(delivery_max_attachment_mb=1)
    monkeypatch.setattr(delivery, "write_report_csv", lambda *a, **k: b"x" * (1024 * 1024 + 1))
    sid = await env.schedule()
    acc = await env.run_now(sid, owner_only=False)
    sent_before = len(mail.sent)
    await env.run_job(acc["job"]["job_id"], expect="failed")
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and "größer als 1 MB" in (run.error or "")
    assert len(await env.load_run_files(run.id)) == 1  # kept, the owner can still download it
    assert all(not m["Bcc"] for m in mail.sent[sent_before:])


async def test_unexpected_error_marks_the_run_failed(
    env: Env, mail: CapturingMailSender, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> bytes:
        raise RuntimeError("kaputt für rep52.owner@example.org")

    monkeypatch.setattr(delivery, "write_report_csv", boom)
    sid = await env.schedule()
    _run_id, job_id = await env.queue_run(sid, owner_only=True)
    await env.run_job(job_id, expect="failed")
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and run.error == "Der Versand ist unerwartet fehlgeschlagen."
    assert run.finished_at is not None
    assert (await env.load_schedule(sid)).failure_count == 1
    assert mail.sent[-1]["To"] == "rep52.owner@example.org"


async def test_cancel_marks_the_run_failed(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = await env.schedule()
    run_id, _job_id = await env.queue_run(sid, owner_only=True)

    async def cancelled(self: JobContext, *a: Any, **k: Any) -> None:
        raise JobCancelled

    monkeypatch.setattr(JobContext, "progress", cancelled)
    ctx = _ctx(env)
    with pytest.raises(JobCancelled):
        await run_delivery_job(ctx, ReportDeliveryPayload(run_id=run_id))
    run = (await env.load_runs(sid))[0]
    assert run.status == "failed" and "abgebrochen" in (run.error or "")


def _ctx(env: Env, *, job_id: int = 0) -> JobContext:
    return JobContext(
        job_id=job_id,
        kind=delivery.REPORT_DELIVERY_KIND,
        created_by=OWNER,
        settings=Settings(environment="test"),
        session_factory=env.factory,
    )


async def test_interrupted_after_checkpoint_marks_failed(env: Env) -> None:
    assert CHECKPOINT_SENDING == "sending"
    sid = await env.schedule()
    run_id, _job_id = await env.queue_run(sid, owner_only=True)
    async with env.factory() as s:
        run = await s.get(TiqoraReportRun, run_id)
        assert run is not None
        run.status = "running"
        await s.commit()
    await on_delivery_interrupted(_ctx(env), ReportDeliveryPayload(run_id=run_id))
    row = (await env.load_runs(sid))[0]
    assert row.status == "failed" and "möglicherweise" in (row.error or "")
    assert row.finished_at is not None
    # a run that already finished is not touched
    async with env.factory() as s:
        done = await s.get(TiqoraReportRun, run_id)
        assert done is not None
        done.status = "succeeded"
        done.error = None
        await s.commit()
    await on_delivery_interrupted(_ctx(env), ReportDeliveryPayload(run_id=run_id))
    assert (await env.load_runs(sid))[0].status == "succeeded"


async def test_missing_and_finished_runs_are_skipped(env: Env) -> None:
    out = await run_delivery_job(_ctx(env), ReportDeliveryPayload(run_id=52999999))
    assert out == {"skipped": "run_missing"}
    sid = await env.schedule()
    run_id, _job = await env.queue_run(sid, owner_only=True)
    async with env.factory() as s:
        run = await s.get(TiqoraReportRun, run_id)
        assert run is not None
        run.status = "succeeded"
        await s.commit()
    assert await run_delivery_job(_ctx(env), ReportDeliveryPayload(run_id=run_id)) == {
        "skipped": "run_finished"
    }


async def test_deleted_schedule_fails_the_run(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule()
    run_id, job_id = await env.queue_run(sid, owner_only=True)
    assert (await env.client.delete(f"/api/v1/report-schedules/{sid}")).status_code == 204
    await env.run_job(job_id, expect="failed")
    async with env.factory() as s:
        run = await s.get(TiqoraReportRun, run_id)
        assert run is not None and run.error == "Der Zeitplan wurde gelöscht."
        assert run.status == "failed"
    assert mail.sent[-1]["To"] == "rep52.owner@example.org"


# ---------------------------------------------------------------- route rules


async def test_manual_run_dedupe_409(env: Env) -> None:
    sid = await env.schedule()
    await env.run_now(sid)
    second = await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"owner_only": True})
    assert second.status_code == 409
    assert second.json()["detail"]["message"] == "Ein Versand läuft bereits."
    assert len(await env.load_runs(sid)) == 1  # the second run row was rolled back


async def test_run_routes_hide_foreign_schedules(env: Env) -> None:
    sid = await env.schedule()
    env.user = user(COLLEAGUE)
    run = await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={})
    assert run.status_code == 404
    assert (await env.client.get(f"/api/v1/report-schedules/{sid}/runs")).status_code == 404
    env.user = user(1)  # an admin may start and read
    assert (await env.client.get(f"/api/v1/report-schedules/{sid}/runs")).status_code == 200
    assert (
        await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={})
    ).status_code == 202


async def test_run_history_order_limit_and_bounds(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule()
    ids = []
    for _ in range(3):
        acc = await env.run_now(sid)
        await env.run_job(acc["job"]["job_id"])
        ids.append(acc["run_id"])
    runs = (await env.client.get(f"/api/v1/report-schedules/{sid}/runs?limit=2")).json()
    assert [r["id"] for r in runs] == [ids[2], ids[1]]
    assert (await env.client.get(f"/api/v1/report-schedules/{sid}/runs?limit=0")).status_code == 422
    assert (
        await env.client.get(f"/api/v1/report-schedules/{sid}/runs?limit=101")
    ).status_code == 422
    assert (
        await env.client.post(f"/api/v1/report-schedules/{sid}/run", json={"x": 1})
    ).status_code == 422


async def test_file_download_owner_or_admin_only(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule()
    acc = await env.run_now(sid)
    await env.run_job(acc["job"]["job_id"])
    run = (await env.client.get(f"/api/v1/report-schedules/{sid}/runs")).json()[0]
    path = f"/api/v1/report-runs/{run['id']}/files/{run['files'][0]['id']}"
    ok = await env.client.get(path)
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/csv")
    assert "rep52-queue-52230" in ok.content.decode("utf-8-sig")
    env.user = user(COLLEAGUE)
    assert (await env.client.get(path)).status_code == 404
    env.user = user(1)
    assert (await env.client.get(path)).status_code == 200
    env.user = user(OWNER)
    other = f"/api/v1/report-runs/{run['id']}/files/{run['files'][0]['id'] + 100000}"
    assert (await env.client.get(other)).status_code == 404
    wrong_run = f"/api/v1/report-runs/{run['id'] + 100000}/files/{run['files'][0]['id']}"
    assert (await env.client.get(wrong_run)).status_code == 404


async def test_mail_log_row_has_no_addresses(env: Env, mail: CapturingMailSender) -> None:
    sid = await env.schedule()
    acc = await env.run_now(sid, owner_only=False)
    await env.run_job(acc["job"]["job_id"])
    async with env.factory() as s:
        rows = (
            (
                await s.execute(
                    select(TiqoraMailLog).where(TiqoraMailLog.detail.like("report_run %"))
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.to_addr == "2 Empfänger (Bcc)" and row.status == "sent"
    assert row.detail == f"report_run {acc['run_id']}"
    assert "@" not in row.to_addr and "@" not in (row.cc_addr or "")
