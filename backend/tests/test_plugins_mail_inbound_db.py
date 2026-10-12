"""Inbound mail filters from plugins (D9, both dialects).

Seed band 55210-55239: the mail account id is 55210; the only seeded rows are the
``postmaster_filter`` row named ``pp55-db-prio`` (no id column, removed by name). Tickets and
articles the pipeline creates are removed with the row marks. Mails go through
``postmaster.process_account`` (fetch patched), because the post filters run there, right after
the commit of the message's transaction.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import DEFAULT_TABLES, delete_above_marks, snapshot_marks
from tests.test_mail_log import _ensure_tables, _make_sysconfig
from tiqora.channels.email.fetch import FetchedMessage, FetchResult
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.worker import postmaster as postmaster_mod
from tiqora.worker.postmaster import process_account

pytestmark = pytest.mark.db

ACCOUNT_ID = 55210
NOW = datetime(2026, 10, 12, 12, 0, 0)
DB_FILTER = "pp55-db-prio"


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _account() -> MailAccount:
    return MailAccount(
        id=ACCOUNT_ID,
        login="pp55@example.org",
        pw="x",
        host="mail.example.org",
        account_type="IMAP",
        queue_id=1,
        trusted=0,
        valid_id=1,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )


def _raw(subject: str, sender: str = "kunde@example.org") -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "support@example.org"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{subject.replace(' ', '-').lower()}.pp55@example.org>"
    msg.set_content("Der Drucker streikt.")
    return msg.as_bytes()


def _clean_filters(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM postmaster_filter WHERE f_name = :n"), {"n": DB_FILTER})
    engine.dispose()


class Env:
    def __init__(
        self, sync_url: str, factory: async_sessionmaker[AsyncSession], mp: pytest.MonkeyPatch
    ) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.mp = mp

    async def deliver(self, *raws: bytes) -> dict[str, int]:
        messages = [FetchedMessage(raw=raw, uid=f"UID-{i}") for i, raw in enumerate(raws)]

        async def fake_fetch(account: MailAccount, **_k: Any) -> FetchResult:
            return FetchResult(account_id=account.id, messages=messages, oversized=0, errors=[])

        async def fake_max_size(_factory: Any) -> int:
            return 1024

        self.mp.setattr(postmaster_mod, "fetch_account", fake_fetch)
        self.mp.setattr(postmaster_mod, "_max_email_size_kb", fake_max_size)
        self.mp.setattr(postmaster_mod, "SysConfig", lambda _session: _make_sysconfig())
        return await process_account(
            self.factory,
            _account(),
            settings=None,  # type: ignore[arg-type]
            mail_sender=object(),  # type: ignore[arg-type]
            leave_on_server=True,
        )

    async def tickets(self) -> list[tuple[int, str, str]]:
        """``(id, title, priority)`` of the tickets the test created, oldest first."""
        async with self.factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT t.id, t.title, p.name FROM ticket t"
                        " JOIN ticket_priority p ON p.id = t.ticket_priority_id"
                        " WHERE t.id > :m ORDER BY t.id"
                    ),
                    {"m": self.ticket_mark},
                )
            ).all()
        return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]

    ticket_mark = 0


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _ensure_tables(sync_url)
    _clean_filters(sync_url)
    marks = snapshot_marks(sync_url, DEFAULT_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    boot_points_plugin(tmp_path, monkeypatch)
    # ``ctx.plugin.tickets`` opens its own session: point it at the test database
    for mod in ("tiqora.plugins.writes", "tiqora.plugins.context"):
        monkeypatch.setattr(f"{mod}.get_session_factory", lambda fac=factory: fac, raising=False)
    result = Env(sync_url, factory, monkeypatch)
    result.ticket_mark = marks["ticket"]
    yield result
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    _clean_filters(sync_url)


def _calls(name: str) -> list[Any]:
    return [c[1] for c in points_behaviour().CALLS if c[0] == name]


async def test_pre_filter_sets_priority(env: Env) -> None:
    stats = await env.deliver(_raw("DRINGEND: Drucker"), _raw("Drucker ohne Eile"))
    assert stats["created"] == 2 and stats["errors"] == 0
    (urgent, calm) = await env.tickets()
    assert urgent[2] == "5 very high"
    assert calm[2] == "3 normal"


async def test_pre_filter_can_ignore(env: Env) -> None:
    stats = await env.deliver(_raw("Gewinnspiel", sender="x@spam.example.net"))
    assert stats["ignored"] == 1 and stats["created"] == 0
    assert await env.tickets() == []
    assert _calls("mail.post") == [("ignored", None)]


@pytest.mark.parametrize("mode", ["raise", "hang"])
async def test_pre_filter_crash_mail_still_processed(env: Env, mode: str) -> None:
    points_behaviour().MODE["mail.pre"] = mode
    started = time.monotonic()
    stats = await env.deliver(_raw("DRINGEND: Scanner"))
    elapsed = time.monotonic() - started
    # The failing filter had asked to drop the mail; being fail-open, that change is undone.
    assert stats["created"] == 1 and stats["ignored"] == 0 and stats["errors"] == 0
    (ticket,) = await env.tickets()
    assert ticket[2] == "5 very high"  # the healthy tagger still ran after the broken one
    assert elapsed < 3.0


async def test_db_filters_run_before_plugin_filters(env: Env) -> None:
    engine = create_engine(env.sync_url)
    with engine.begin() as conn:
        for f_type, key, value in (
            ("Match", "Subject", "DRINGEND"),
            ("Set", "X-OTRS-Priority", "2 low"),
        ):
            conn.execute(
                text(
                    "INSERT INTO postmaster_filter (f_name, f_stop, f_type, f_key, f_value, f_not)"
                    " VALUES (:n, 0, :t, :k, :v, 0)"
                ),
                {"n": DB_FILTER, "t": f_type, "k": key, "v": value},
            )
    engine.dispose()
    await env.deliver(_raw("DRINGEND: Toner"))
    (ticket,) = await env.tickets()
    assert ticket[2] == "5 very high"  # the plugin ran after the database filter and won


async def test_post_filter_sees_committed_ticket(env: Env) -> None:
    await env.deliver(_raw("Vertrag pruefen"))
    (ticket,) = await env.tickets()
    assert _calls("mail.post") == [("new_ticket", ticket[0])]
    # read through the SDK in the filter's own session: only possible after the commit
    assert _calls("mail.post.title") == [ticket[1]]
    (headers,) = _calls("mail.post.headers")
    assert headers["Subject"] == "Vertrag pruefen"


async def test_post_filter_failure_does_not_change_result(env: Env) -> None:
    points_behaviour().MODE["mail.post"] = "raise"
    stats = await env.deliver(_raw("Rechnung"), _raw("Mahnung"))
    assert stats["created"] == 2 and stats["errors"] == 0
    assert len(await env.tickets()) == 2
    assert len(_calls("mail.post")) == 2  # the failure of the first did not stop the second
