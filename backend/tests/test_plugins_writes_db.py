"""Write-capable plugin ticket services and read-only customers (both dialects).

Seed band 55000-55039: queue ``pp55-q1`` 55001 in group 55002, agent ``pp55-agent`` 55003
(``rw``), agent ``pp55-ro`` 55004 (``ro`` only), customer ``pp55-cust``, Text DF
``PP55Contract`` 55005, Date DF ``PP55Due`` 55006.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.plugins.context import PluginTickets
from tiqora.plugins.customers import PluginCustomers
from tiqora.plugins.writes import (
    PluginPermissionDenied,
    PluginWriteError,
    TicketDraft,
)

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 12, 12, 0, 0)
QUEUE, GROUP, AGENT, AGENT_RO, DF_TEXT, DF_DATE = 55001, 55002, 55003, 55004, 55005, 55006
QUEUE2 = 55007
CUST = "pp55-cust"

MARK_TABLES: tuple[str, ...] = (
    "tiqora_event_outbox",
    "tiqora_cache_invalidation",
    "ticket_history",
    "dynamic_field_value",
    "article_data_mime_attachment",
    "article_data_mime_plain",
    "article_data_mime",
    "article",
    "ticket",
    "ticket_number_counter",
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean_seed(conn: Any) -> None:
    conn.execute(text("DELETE FROM dynamic_field_value WHERE field_id IN (55005, 55006)"))
    conn.execute(text("DELETE FROM dynamic_field WHERE id IN (55005, 55006)"))
    conn.execute(text(f"DELETE FROM customer_user WHERE login = '{CUST}'"))
    conn.execute(text("DELETE FROM queue WHERE id IN (55001, 55007)"))
    conn.execute(text("DELETE FROM group_user WHERE group_id = 55002"))
    conn.execute(text("DELETE FROM permission_groups WHERE id = 55002"))
    conn.execute(text("DELETE FROM users WHERE id IN (55003, 55004)"))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean_seed(conn)
        for uid, login in ((AGENT, "pp55-agent"), (AGENT_RO, "pp55-ro")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:i, :l, 'x', 'Pp55', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"i": uid, "l": login, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:g, 'pp55-grp', 1, :t, 1, :t, 1)"
            ),
            {"g": GROUP, "t": NOW},
        )
        for uid, keys in ((AGENT, ("ro", "rw")), (AGENT_RO, ("ro",))):
            for k in keys:
                conn.execute(
                    text(
                        "INSERT INTO group_user (user_id, group_id, permission_key,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                    ),
                    {"u": uid, "g": GROUP, "k": k, "t": NOW},
                )
        for qid, name in ((QUEUE, "pp55-q1"), (QUEUE2, "pp55-q2")):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:q, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"q": qid, "n": name, "g": GROUP, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:l, 'pp55-cust@example.org', 'pp55-co', 'Kim', 'Kunde', 1, :t, 1, :t, 1)"
            ),
            {"l": CUST, "t": NOW},
        )
        for fid, name, ftype in ((DF_TEXT, "PP55Contract", "Text"), (DF_DATE, "PP55Due", "Date")):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 0, :n, :n, :id, :ft, 'Ticket', '---', 1, :t, 1, :t, 1)"
                ),
                {"id": fid, "n": name, "ft": ftype, "t": NOW},
            )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str, fac: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = fac

    def scalar(self, sql: str, **params: Any) -> Any:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                return conn.execute(text(sql), params).scalar_one()
        finally:
            engine.dispose()

    def rows(self, sql: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                return list(conn.execute(text(sql), params).all())
        finally:
            engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _seed(sync_url)
    marks = snapshot_marks(sync_url, MARK_TABLES)
    engine = create_async_engine(_to_async_url(sync_url))
    fac = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    for mod in (
        "tiqora.plugins.writes",
        "tiqora.plugins.customers",
        "tiqora.plugins.context",
        "tiqora.domain.ticket_write_service",
    ):
        monkeypatch.setattr(f"{mod}.get_session_factory", lambda fac=fac: fac, raising=False)
    yield Env(sync_url, fac)
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    clean = create_engine(sync_url)
    with clean.begin() as conn:
        _clean_seed(conn)
    clean.dispose()


async def _create(env_: Env, **kw: Any) -> int:
    return await PluginTickets().create(
        TicketDraft(title="Vertrag", queue="pp55-q1", customer_user=CUST, body="Hallo", **kw)
    )


async def test_create_as_system(env: Env) -> None:
    tid = await PluginTickets().create(
        TicketDraft(
            title="Vertrag",
            queue="pp55-q1",
            customer_user=CUST,
            body="Hallo",
            dynamic_fields={"PP55Contract": ["V-1"]},
        )
    )
    q, owner, create_by = env.rows(
        "SELECT queue_id, user_id, create_by FROM ticket WHERE id = :t", t=tid
    )[0]
    assert (q, owner, create_by) == (QUEUE, 1, 1)
    arts = env.rows(
        "SELECT cc.name, a.create_by FROM article a JOIN communication_channel cc"
        " ON cc.id = a.communication_channel_id WHERE a.ticket_id = :t",
        t=tid,
    )
    assert [(n, c) for n, c in arts] == [("Internal", 1)]
    assert env.rows(
        "SELECT value_text FROM dynamic_field_value WHERE object_id = :t AND field_id = :f",
        t=tid,
        f=DF_TEXT,
    ) == [("V-1",)]
    assert (
        env.scalar(
            "SELECT COUNT(*) FROM ticket_history h JOIN ticket_history_type ht"
            " ON ht.id = h.history_type_id WHERE h.ticket_id = :t AND ht.name = 'NewTicket'",
            t=tid,
        )
        == 1
    )


async def test_create_as_agent_checks_permission(env: Env) -> None:
    before = env.scalar("SELECT COUNT(*) FROM ticket WHERE queue_id = :q", q=QUEUE)
    with pytest.raises(PluginPermissionDenied):
        await PluginTickets().create(TicketDraft(title="x", queue="pp55-q1"), as_agent=AGENT_RO)
    assert env.scalar("SELECT COUNT(*) FROM ticket WHERE queue_id = :q", q=QUEUE) == before
    tid = await PluginTickets().create(TicketDraft(title="y", queue="pp55-q1"), as_agent=AGENT)
    assert env.scalar("SELECT user_id FROM ticket WHERE id = :t", t=tid) == AGENT


async def test_unknown_names_are_plain_errors(env: Env) -> None:
    tid = await _create(env)
    with pytest.raises(PluginWriteError, match="Queue „gibt-es-nicht“"):
        await PluginTickets().create(TicketDraft(title="x", queue="gibt-es-nicht"))
    with pytest.raises(PluginWriteError, match="Status"):
        await PluginTickets().set_state(tid, "kaputt")
    with pytest.raises(PluginWriteError, match="Queue"):
        await PluginTickets().move(tid, "gibt-es-nicht")
    with pytest.raises(PluginWriteError, match="Dynamisches Feld „Nope“"):
        await PluginTickets().set_dynamic_field(tid, "Nope", ["x"])
    with pytest.raises(PluginWriteError, match="gibt es nicht"):
        await PluginTickets().add_article(tid + 100000, body="x")


async def test_state_and_move(env: Env) -> None:
    tid = await _create(env)
    await PluginTickets().set_state(tid, "closed successful")
    assert (
        env.scalar(
            "SELECT s.name FROM ticket t JOIN ticket_state s ON s.id = t.ticket_state_id"
            " WHERE t.id = :t",
            t=tid,
        )
        == "closed successful"
    )
    await PluginTickets().move(tid, "pp55-q2")
    assert env.scalar("SELECT queue_id FROM ticket WHERE id = :t", t=tid) == QUEUE2
    with pytest.raises(PluginPermissionDenied):
        await PluginTickets().move(tid, "pp55-q1", as_agent=AGENT_RO)
    assert env.scalar("SELECT queue_id FROM ticket WHERE id = :t", t=tid) == QUEUE2


async def test_email_channel_refused(env: Env) -> None:
    tid = await _create(env)
    before = env.scalar("SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=tid)
    with pytest.raises(PluginWriteError, match="einen eigenen Kanal"):
        await PluginTickets().add_article(tid, body="x", channel="email")
    with pytest.raises(PluginWriteError, match="einen eigenen Kanal"):
        await PluginTickets().create(
            TicketDraft(title="x", queue="pp55-q1", body="b", channel="telegram")
        )
    assert env.scalar("SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=tid) == before


async def test_caller_session_not_committed(env: Env) -> None:
    tid = await _create(env)
    before = env.scalar("SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=tid)
    async with env.factory() as s:
        art = await PluginTickets().add_article(tid, body="note", session=s)
        assert art > 0
        await s.rollback()
    assert env.scalar("SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=tid) == before
    # an own session commits
    await PluginTickets().add_article(tid, body="kept")
    assert env.scalar("SELECT COUNT(*) FROM article WHERE ticket_id = :t", t=tid) == before + 1


async def test_own_session_rolls_back_on_error(env: Env) -> None:
    tid = await _create(env)
    with pytest.raises(PluginWriteError):
        await PluginTickets().set_dynamic_field(tid, "PP55Due", ["kein Datum"])
    assert (
        env.scalar(
            "SELECT COUNT(*) FROM dynamic_field_value WHERE object_id = :t AND field_id = :f",
            t=tid,
            f=DF_DATE,
        )
        == 0
    )
    # a failing create leaves no ticket, article or value rows behind
    tickets = env.scalar("SELECT COUNT(*) FROM ticket")
    arts = env.scalar("SELECT COUNT(*) FROM article")
    with pytest.raises(PluginWriteError):
        await PluginTickets().create(
            TicketDraft(
                title="x",
                queue="pp55-q1",
                body="b",
                dynamic_fields={"PP55Due": ["kein Datum"]},
            )
        )
    assert env.scalar("SELECT COUNT(*) FROM ticket") == tickets
    assert env.scalar("SELECT COUNT(*) FROM article") == arts


async def test_set_dynamic_field_as_agent(env: Env) -> None:
    tid = await _create(env)
    await PluginTickets().set_dynamic_field(tid, "PP55Contract", ["V-2"], as_agent=AGENT)
    assert env.rows(
        "SELECT value_text FROM dynamic_field_value WHERE object_id = :t AND field_id = :f",
        t=tid,
        f=DF_TEXT,
    ) == [("V-2",)]
    with pytest.raises(PluginPermissionDenied):
        await PluginTickets().set_dynamic_field(tid, "PP55Contract", ["V-3"], as_agent=AGENT_RO)


async def test_customers_read_only(env: Env) -> None:
    c = await PluginCustomers().get(CUST)
    assert c is not None
    assert (c.email, c.customer_id, c.valid) == ("pp55-cust@example.org", "pp55-co", True)
    assert await PluginCustomers().get("pp55-nobody") is None
    assert [x.login for x in await PluginCustomers().search("pp55-cu")] == [CUST]
    assert not hasattr(PluginCustomers, "create")
