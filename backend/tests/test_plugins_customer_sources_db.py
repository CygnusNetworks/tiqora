"""Customer sources from plugins (D13, both dialects).

Seed band 55390-55429: the made-up directory people are ``pp55-ext1`` ... ``pp55-ext3`` at
``example.org`` (``pp55-ext2`` works for company ``PP55-FIRMA``); local customers the tests add
have logins ``pp55-local-*``. All of them and the companies ``PP55-*`` are removed by name;
tickets, articles and cache-signal rows with the row marks. The mails go through
``postmaster.process_account`` (fetch patched), like the inbound mail filter tests.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._plugins_state_helpers import to_async_url
from tests._row_cleanup import DEFAULT_TABLES, delete_above_marks, snapshot_marks
from tests.test_mail_log import _ensure_tables, _make_sysconfig
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.channels.email.fetch import FetchedMessage, FetchResult
from tiqora.config import Settings
from tiqora.db.legacy.mail_account import MailAccount
from tiqora.domain import user_provisioning
from tiqora.domain.auth import AuthenticatedUser
from tiqora.worker import postmaster as postmaster_mod
from tiqora.worker.postmaster import process_account

pytestmark = pytest.mark.db

SOURCE = "org.example.points:directory"
ACCOUNT_ID = 55390
NOW = datetime(2026, 10, 13, 9, 0, 0)
AGENT = AuthenticatedUser(
    id=1, login="agent@example.org", first_name="Ann", last_name="Agent", auth_method="session"
)


def _clean(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE 'pp55-%'"))
        conn.execute(text("DELETE FROM customer_company WHERE customer_id LIKE 'PP55-%'"))
        conn.execute(text("DELETE FROM customer_company WHERE customer_id LIKE 'pp55-%'"))
    engine.dispose()


def _sql(url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []
    finally:
        engine.dispose()


class Env:
    def __init__(self, url: str, factory: async_sessionmaker[AsyncSession], mp: Any) -> None:
        self.url = url
        self.factory = factory
        self.mp = mp
        self.ticket_mark = 0
        #: Znuny's default address check refuses example.org; tests turn it off unless asked
        self.strict_email_check = False
        real = user_provisioning.check_email

        async def _check(session: Any, address: str) -> bool:
            return await real(session, address) if self.strict_email_check else True

        mp.setattr(user_provisioning, "check_email", _check)

    def client(self) -> AsyncClient:
        factory = self.factory

        async def _db() -> Any:
            async with factory() as session:
                yield session

        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: AGENT
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    def add_local_customer(self, login: str, email: str) -> None:
        _sql(
            self.url,
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name, pw,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:l, :e, 'pp55-local', 'Lena', 'Lokal', 'x', 1,"
            " current_timestamp, 1, current_timestamp, 1)",
            l=login,
            e=email,
        )

    async def deliver(self, sender: str, subject: str = "Anfrage") -> dict[str, int]:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = "support@example.org"
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{subject.lower()}.pp55c@example.org>"
        msg.set_content("Bitte um Rückruf.")
        fetched = [FetchedMessage(raw=msg.as_bytes(), uid="UID-0")]

        async def fake_fetch(account: MailAccount, **_k: Any) -> FetchResult:
            return FetchResult(account_id=account.id, messages=fetched, oversized=0, errors=[])

        async def fake_max_size(_factory: Any) -> int:
            return 1024

        self.mp.setattr(postmaster_mod, "fetch_account", fake_fetch)
        self.mp.setattr(postmaster_mod, "_max_email_size_kb", fake_max_size)
        self.mp.setattr(postmaster_mod, "SysConfig", lambda _session: _make_sysconfig())
        account = MailAccount(
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
        return await process_account(
            self.factory,
            account,
            settings=None,  # type: ignore[arg-type]
            mail_sender=object(),  # type: ignore[arg-type]
            leave_on_server=True,
        )

    def ticket_customers(self) -> list[tuple[str | None, str | None]]:
        rows = _sql(
            self.url,
            "SELECT customer_id, customer_user_id FROM ticket WHERE id > :m ORDER BY id",
            m=self.ticket_mark,
        )
        return [(r[0], r[1]) for r in rows]


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    _ensure_tables(url)
    _clean(url)
    marks = snapshot_marks(url, DEFAULT_TABLES)
    engine = create_async_engine(to_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    boot_points_plugin(tmp_path, monkeypatch)
    result = Env(url, factory, monkeypatch)
    result.ticket_mark = marks["ticket"]
    yield result
    await engine.dispose()
    delete_above_marks(url, marks)
    _clean(url)


def _calls(name: str) -> list[Any]:
    return [c for c in points_behaviour().CALLS if c[0] == name]


async def test_search_returns_external_when_db_has_few(env: Env) -> None:
    async with env.client() as client:
        r = await client.get(
            "/api/v1/reference/customer-search", params={"q": "pp55-ext", "external": "true"}
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contacts"] == []
    assert [e["login"] for e in body["external"]] == ["pp55-ext1", "pp55-ext2", "pp55-ext3"]
    assert {e["source"] for e in body["external"]} == {SOURCE}
    assert {e["source_label"] for e in body["external"]} == {"Beispielverzeichnis"}
    assert body["external"][1]["company_name"] == "Beispiel GmbH"
    assert body["external"][0]["company_name"] is None


async def test_search_asks_sources_only_when_requested(env: Env) -> None:
    async with env.client() as client:
        r = await client.get("/api/v1/reference/customer-search", params={"q": "pp55-ext"})
    assert r.status_code == 200
    assert r.json()["external"] == []
    assert _calls("customers.search") == []


async def test_search_skips_what_is_already_local(env: Env) -> None:
    async with env.client() as client:
        imported = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext1"}
        )
        r = await client.get(
            "/api/v1/reference/customer-search", params={"q": "pp55-ext", "external": "true"}
        )
    assert imported.status_code == 200, imported.text
    body = r.json()
    assert [c["login"] for c in body["contacts"]] == ["pp55-ext1"]
    assert [e["login"] for e in body["external"]] == ["pp55-ext2", "pp55-ext3"]


@pytest.mark.parametrize("mode", ["raise", "hang"])
async def test_failing_source_is_skipped(env: Env, mode: str) -> None:
    points_behaviour().MODE["cs"] = mode
    started = time.monotonic()
    async with env.client() as client:
        r = await client.get(
            "/api/v1/reference/customer-search", params={"q": "pp55-ext", "external": "true"}
        )
    assert r.status_code == 200, r.text
    assert r.json()["external"] == []
    assert len(_calls("customers.search")) == 1
    assert time.monotonic() - started < 6  # a hanging source is cut at timeout_seconds=1


async def test_import_creates_customer_and_company(env: Env) -> None:
    async with env.client() as client:
        r = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext2"}
        )
    assert r.status_code == 200, r.text
    assert r.json()["login"] == "pp55-ext2"
    assert r.json()["email"] == "pp55-ext2@example.org"
    assert r.json()["customer_id"] == "PP55-FIRMA"
    (user,) = _sql(
        env.url,
        "SELECT pw, valid_id, customer_id, create_by FROM customer_user WHERE login = 'pp55-ext2'",
    )
    assert user[0] and user[0] != "x" and user[1] == 1 and user[2] == "PP55-FIRMA"
    assert user[3] == AGENT.id
    companies = _sql(
        env.url,
        "SELECT name, city, valid_id FROM customer_company WHERE customer_id = 'PP55-FIRMA'",
    )
    assert [tuple(c) for c in companies] == [("Beispiel GmbH", "Musterstadt", 1)]


async def test_import_without_company_uses_email_as_customer_id(env: Env) -> None:
    async with env.client() as client:
        r = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext1"}
        )
    assert r.status_code == 200, r.text
    assert r.json()["customer_id"] == "pp55-ext1@example.org"
    assert (
        _sql(env.url, "SELECT customer_id FROM customer_company WHERE customer_id LIKE 'pp55-%'")
        == []
    )


async def test_import_existing_login_returns_it_unchanged(env: Env) -> None:
    async with env.client() as client:
        first = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext2"}
        )
        _sql(env.url, "UPDATE customer_user SET first_name = 'Geaendert' WHERE login = 'pp55-ext2'")
        again = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext2"}
        )
    assert first.status_code == 200 and again.status_code == 200
    assert again.json()["first_name"] == "Geaendert"  # never updated from the directory
    rows = _sql(env.url, "SELECT login FROM customer_user WHERE login = 'pp55-ext2'")
    assert len(rows) == 1
    companies = _sql(
        env.url, "SELECT customer_id FROM customer_company WHERE customer_id = 'PP55-FIRMA'"
    )
    assert len(companies) == 1


async def test_email_conflict_409(env: Env) -> None:
    env.add_local_customer("pp55-local-3", "pp55-ext3@example.org")
    async with env.client() as client:
        r = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext3"}
        )
    assert r.status_code == 409, r.text
    assert "E-Mail" in r.json()["detail"]
    assert _sql(env.url, "SELECT login FROM customer_user WHERE login = 'pp55-ext3'") == []


async def test_address_znuny_refuses_is_409(env: Env) -> None:
    env.strict_email_check = True  # default CheckEmailInvalidAddress refuses example.org
    async with env.client() as client:
        r = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext2"}
        )
    assert r.status_code == 409, r.text
    assert "E-Mail" in r.json()["detail"]
    assert _sql(env.url, "SELECT login FROM customer_user WHERE login = 'pp55-ext2'") == []
    assert (
        _sql(env.url, "SELECT customer_id FROM customer_company WHERE customer_id = 'PP55-FIRMA'")
        == []
    )


async def test_conflict_writes_no_company(env: Env) -> None:
    env.add_local_customer("pp55-local-2", "pp55-ext2@example.org")
    async with env.client() as client:
        r = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext2"}
        )
    assert r.status_code == 409, r.text
    assert (
        _sql(env.url, "SELECT customer_id FROM customer_company WHERE customer_id = 'PP55-FIRMA'")
        == []
    )


async def test_unknown_login_404(env: Env) -> None:
    async with env.client() as client:
        r = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-gibtsnicht"}
        )
        unknown_source = await client.post(
            "/api/v1/customers/external-import",
            json={"source": "org.example.points:nope", "login": "pp55-ext1"},
        )
    assert r.status_code == 404
    assert unknown_source.status_code == 404
    assert _sql(env.url, "SELECT login FROM customer_user WHERE login LIKE 'pp55-%'") == []


@pytest.mark.parametrize("mode", ["raise", "hang"])
async def test_import_source_failure_is_404(env: Env, mode: str) -> None:
    points_behaviour().MODE["cs"] = mode
    started = time.monotonic()
    async with env.client() as client:
        r = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext1"}
        )
    assert r.status_code == 404
    assert time.monotonic() - started < 6


async def test_inbound_mail_unknown_sender_taken_over(env: Env) -> None:
    stats = await env.deliver("pp55-ext1@example.org")
    assert stats["created"] == 1 and stats["errors"] == 0
    assert env.ticket_customers() == [("pp55-ext1@example.org", "pp55-ext1")]
    (row,) = _sql(env.url, "SELECT email, create_by FROM customer_user WHERE login = 'pp55-ext1'")
    assert tuple(row) == ("pp55-ext1@example.org", 1)
    assert [c[0] for c in _calls("customers.by_email")] == ["customers.by_email"]


async def test_inbound_mail_known_sender_does_not_ask_the_source(env: Env) -> None:
    env.add_local_customer("pp55-local-9", "pp55-local-9@example.org")
    stats = await env.deliver("pp55-local-9@example.org")
    assert stats["created"] == 1
    assert env.ticket_customers() == [("pp55-local", "pp55-local-9")]
    assert _calls("customers.by_email") == []


async def test_inbound_sender_unknown_to_every_source_is_unchanged(env: Env) -> None:
    stats = await env.deliver("niemand@example.org")
    assert stats["created"] == 1
    assert env.ticket_customers() == [("niemand@example.org", "niemand@example.org")]
    assert _sql(env.url, "SELECT login FROM customer_user WHERE login LIKE 'niemand%'") == []


@pytest.mark.parametrize("mode", ["raise", "hang"])
async def test_inbound_lookup_failure_keeps_old_behaviour(env: Env, mode: str) -> None:
    points_behaviour().MODE["cs"] = mode
    started = time.monotonic()
    stats = await env.deliver("pp55-ext1@example.org")
    assert stats["created"] == 1 and stats["errors"] == 0
    assert env.ticket_customers() == [("pp55-ext1@example.org", "pp55-ext1@example.org")]
    assert _sql(env.url, "SELECT login FROM customer_user WHERE login = 'pp55-ext1'") == []
    assert time.monotonic() - started < 6


async def test_inbound_email_conflict_keeps_old_behaviour(env: Env) -> None:
    # a local customer owns the address under another login: the lookup finds it first, so the
    # mail belongs to that customer and the directory is never asked
    env.add_local_customer("pp55-local-1", "pp55-ext1@example.org")
    stats = await env.deliver("pp55-ext1@example.org")
    assert stats["created"] == 1
    assert env.ticket_customers() == [("pp55-local", "pp55-local-1")]
    assert _calls("customers.by_email") == []


async def test_inbound_login_of_unrelated_local_customer_is_not_taken_over(env: Env) -> None:
    # the directory's pp55-ext1 is another person than the local login of the same name
    env.add_local_customer("pp55-ext1", "pp55-andere@example.org")
    stats = await env.deliver("pp55-ext1@example.org")
    assert stats["created"] == 1
    assert env.ticket_customers() == [("pp55-ext1@example.org", "pp55-ext1@example.org")]
    (row,) = _sql(env.url, "SELECT email FROM customer_user WHERE login = 'pp55-ext1'")
    assert row[0] == "pp55-andere@example.org"


async def test_import_login_of_unrelated_local_customer_is_409(env: Env) -> None:
    env.add_local_customer("pp55-ext1", "pp55-andere@example.org")
    async with env.client() as client:
        r = await client.post(
            "/api/v1/customers/external-import", json={"source": SOURCE, "login": "pp55-ext1"}
        )
    assert r.status_code == 409, r.text
    assert "Login" in r.json()["detail"]
    (row,) = _sql(env.url, "SELECT email FROM customer_user WHERE login = 'pp55-ext1'")
    assert row[0] == "pp55-andere@example.org"


async def test_materialize_rejects_over_long_optional_fields(env: Env) -> None:
    from tiqora.plugins.customer_sources import (
        CustomerSourceConflict,
        ExternalCustomer,
        materialize,
    )

    ext = ExternalCustomer(
        login="pp55-lang",
        email="pp55-lang@example.org",
        first_name="Lang",
        last_name="Wert",
        title="x" * 51,
    )
    async with env.factory() as session:
        with pytest.raises(CustomerSourceConflict):
            await materialize(session, ext, actor_id=1)
