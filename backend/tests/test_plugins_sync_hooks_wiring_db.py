"""Sync before-hooks wired into ``TicketWriteService`` (both dialects).

Seed band 55040-55079: queue ``pp55h-q1`` 55041 in group 55042, agent ``pp55h-agent`` 55043
(``ro``/``rw``/``create``/``note``), queue ``pp55h-q2`` 55044, Text DF ``PP55Flag`` 55045.
Tickets are created through ``PluginTickets`` (system context, never hooked) and removed with
the other rows above the table marks.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.domain import ticket_write_service as tws
from tiqora.domain.ticket_write_service import ArticleIn, TicketIn, TicketWriteService
from tiqora.plugins import sync_hooks
from tiqora.plugins.context import PluginTickets
from tiqora.plugins.sync_hooks import OperationVetoed, SyncBreaker
from tiqora.plugins.writes import TicketDraft
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 12, 12, 0, 0)
QUEUE, GROUP, AGENT, QUEUE2, DF_FLAG = 55041, 55042, 55043, 55044, 55045
VETO = "Vertragsnummer fehlt"

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
    conn.execute(text(f"DELETE FROM dynamic_field_value WHERE field_id = {DF_FLAG}"))
    conn.execute(text(f"DELETE FROM dynamic_field WHERE id = {DF_FLAG}"))
    conn.execute(text(f"DELETE FROM queue WHERE id IN ({QUEUE}, {QUEUE2})"))
    conn.execute(text(f"DELETE FROM group_user WHERE group_id = {GROUP}"))
    conn.execute(text(f"DELETE FROM permission_groups WHERE id = {GROUP}"))
    conn.execute(text(f"DELETE FROM users WHERE id = {AGENT}"))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean_seed(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:i, 'pp55h-agent', 'x', 'Pp55', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"i": AGENT, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:g, 'pp55h-grp', 1, :t, 1, :t, 1)"
            ),
            {"g": GROUP, "t": NOW},
        )
        for k in ("ro", "rw", "create", "note", "move_into"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": AGENT, "g": GROUP, "k": k, "t": NOW},
            )
        for qid, name in ((QUEUE, "pp55h-q1"), (QUEUE2, "pp55h-q2")):
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
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 0, 'PP55Flag', 'PP55Flag', :id, 'Text', 'Ticket', '---', 1,"
                " :t, 1, :t, 1)"
            ),
            {"id": DF_FLAG, "t": NOW},
        )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str, fac: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = fac
        self.sender = CapturingMailSender()

    def rows(self, sql: str, **params: Any) -> list[Any]:
        engine = create_engine(self.sync_url)
        try:
            with engine.begin() as conn:
                return list(conn.execute(text(sql), params).all())
        finally:
            engine.dispose()

    def scalar(self, sql: str, **params: Any) -> Any:
        return self.rows(sql, **params)[0][0]

    def count(self, table: str, where: str = "1=1", **params: Any) -> int:
        return int(self.scalar(f"SELECT COUNT(*) FROM {table} WHERE {where}", **params))

    def state_id(self, name: str) -> int:
        return int(self.scalar("SELECT id FROM ticket_state WHERE name = :n", n=name))

    def priority_id(self, name: str) -> int:
        return int(self.scalar("SELECT id FROM ticket_priority WHERE name = :n", n=name))

    async def run(self, fn: Callable[[TicketWriteService], Awaitable[Any]]) -> Any:
        """One request transaction: commit on success, roll back when ``fn`` raises."""
        async with self.factory() as s, s.begin():
            return await fn(TicketWriteService(s, self.factory, SysConfig(s), self.sender))

    async def ticket(self, **kw: Any) -> int:
        return await PluginTickets().create(
            TicketDraft(title="Vertrag", queue="pp55h-q1", body="Hallo", **kw)
        )


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
        "tiqora.plugins.sync_hooks",
        "tiqora.domain.ticket_write_service",
    ):
        monkeypatch.setattr(f"{mod}.get_session_factory", lambda fac=fac: fac, raising=False)
    SyncBreaker.reset_for_tests()
    SyncBreaker.persistence = False
    boot_points_plugin(tmp_path, monkeypatch)
    yield Env(sync_url, fac)
    SyncBreaker.reset_for_tests()
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    clean = create_engine(sync_url)
    with clean.begin() as conn:
        _clean_seed(conn)
    clean.dispose()


def _calls(operation: str) -> list[Any]:
    return [op for name, op in points_behaviour().CALLS if name == f"hook.{operation}"]


def _article(**kw: Any) -> ArticleIn:
    base: dict[str, Any] = {
        "sender_type": "agent",
        "channel": "email",
        "subject": "Re",
        "body": "x",
        "to_address": "pp55-cust@example.org",
        "is_visible_for_customer": True,
    }
    return ArticleIn(**{**base, **kw})


def _ticket_in(env_: Env, **kw: Any) -> TicketIn:
    return TicketIn(
        title="Drucker defekt",
        queue_id=QUEUE,
        state_id=env_.state_id("new"),
        priority_id=env_.priority_id("3 normal"),
        owner_id=AGENT,
        **kw,
    )


async def test_veto_before_email_send(env: Env) -> None:
    points_behaviour().MODE["hook.article.create"] = "veto"
    tid = await env.ticket()
    before = env.count("article", "ticket_id = :t", t=tid)
    with pytest.raises(OperationVetoed, match=VETO):
        await env.run(lambda svc: svc.add_article(AGENT, tid, _article()))
    assert env.sender.sent == []  # nothing left the house
    assert env.count("article", "ticket_id = :t", t=tid) == before
    (op,) = _calls("article.create")
    assert (op.ticket_id, op.channel, op.to) == (tid, "email", "pp55-cust@example.org")


async def test_article_hook_sees_op_and_lets_note_through(env: Env) -> None:
    tid = await env.ticket()
    before = env.count("article", "ticket_id = :t", t=tid)
    await env.run(
        lambda svc: svc.add_article(
            AGENT,
            tid,
            ArticleIn(
                sender_type="agent",
                channel="note",
                subject="N",
                body="b",
                is_visible_for_customer=False,
                attachments=[("a.txt", "text/plain", b"x")],
            ),
        )
    )
    assert env.count("article", "ticket_id = :t", t=tid) == before + 1
    (op,) = _calls("article.create")
    assert (op.channel, op.visible_for_customer, op.attachment_names) == ("note", False, ("a.txt",))


async def test_veto_writes_nothing(env: Env) -> None:
    behaviour = points_behaviour()
    tid = await env.ticket()
    tickets, history = env.count("ticket"), env.count("ticket_history")

    behaviour.MODE["hook.ticket.create"] = "veto"
    with pytest.raises(OperationVetoed, match=VETO):
        await env.run(lambda svc: svc.create_ticket(AGENT, _ticket_in(env)))
    assert (env.count("ticket"), env.count("ticket_history")) == (tickets, history)

    state_before = env.scalar("SELECT ticket_state_id FROM ticket WHERE id = :t", t=tid)
    behaviour.MODE["hook.ticket.state_change"] = "veto"
    with pytest.raises(OperationVetoed, match=VETO):
        await env.run(lambda svc: svc.change_state(AGENT, tid, env.state_id("closed successful")))
    assert env.scalar("SELECT ticket_state_id FROM ticket WHERE id = :t", t=tid) == state_before
    assert env.count("ticket_history") == history

    behaviour.MODE["hook.ticket.queue_move"] = "veto"
    with pytest.raises(OperationVetoed, match=VETO):
        await env.run(lambda svc: svc.move_queue(AGENT, tid, QUEUE2))
    assert env.scalar("SELECT queue_id FROM ticket WHERE id = :t", t=tid) == QUEUE

    behaviour.MODE["hook.ticket.dynamic_fields_update"] = "veto"
    with pytest.raises(OperationVetoed, match=VETO):
        await env.run(
            lambda svc: svc.update_dynamic_field(AGENT, tid, field_name="PP55Flag", values=["1"])
        )
    with pytest.raises(OperationVetoed, match=VETO):
        await env.run(lambda svc: svc.update_dynamic_fields(AGENT, tid, {"PP55Flag": ["1"]}))
    assert env.count("dynamic_field_value", "field_id = :f", f=DF_FLAG) == 0
    assert env.count("ticket_history") == history


async def test_ops_carry_the_request(env: Env) -> None:
    tid = await env.ticket()
    closed = env.state_id("closed successful")
    await env.run(lambda svc: svc.change_state(AGENT, tid, closed))
    await env.run(lambda svc: svc.move_queue(AGENT, tid, QUEUE2))
    await env.run(lambda svc: svc.update_dynamic_fields(AGENT, tid, {"PP55Flag": ["7"]}))
    (change,) = _calls("ticket.state_change")
    assert (change.new_state, change.new_state_type, change.ticket.id) == (
        "closed successful",
        "closed",
        tid,
    )
    assert change.ticket.state == "new" and change.ticket.queue_name == "pp55h-q1"
    (move,) = _calls("ticket.queue_move")
    assert (move.new_queue_id, move.new_queue, move.ticket.queue_id) == (QUEUE2, "pp55h-q2", QUEUE)
    (dfs,) = _calls("ticket.dynamic_fields_update")
    assert dict(dfs.values) == {"PP55Flag": ("7",)}
    assert env.scalar("SELECT queue_id FROM ticket WHERE id = :t", t=tid) == QUEUE2


async def test_enrich_applies_to_created_ticket(env: Env) -> None:
    points_behaviour().MODE["hook.ticket.create"] = "enrich_df"
    tid = await env.run(
        lambda svc: svc.create_ticket(
            AGENT,
            _ticket_in(
                env,
                article=ArticleIn(
                    sender_type="agent",
                    channel="note",
                    subject="S",
                    body="b",
                    is_visible_for_customer=False,
                ),
            ),
        )
    )
    assert env.scalar("SELECT title FROM ticket WHERE id = :t", t=tid) == "[geprüft] Drucker defekt"
    assert env.rows(
        "SELECT value_text FROM dynamic_field_value WHERE object_id = :t AND field_id = :f",
        t=tid,
        f=DF_FLAG,
    ) == [("1",)]
    (op,) = _calls("ticket.create")
    assert (op.queue, op.state, op.priority, op.title) == (
        "pp55h-q1",
        "new",
        "3 normal",
        "Drucker defekt",
    )
    assert op.article is not None and op.article.subject == "S"


async def test_invalid_patch_is_ignored_by_a_fail_open_hook(env: Env) -> None:
    points_behaviour().MODE["hook.ticket.create"] = "bad_priority"
    tid = await env.run(lambda svc: svc.create_ticket(AGENT, _ticket_in(env)))
    title, prio = env.rows("SELECT title, ticket_priority_id FROM ticket WHERE id = :t", t=tid)[0]
    assert (title, prio) == ("Drucker defekt", env.priority_id("3 normal"))
    assert SyncBreaker.failures("sync:org.example.points:ticket_create") == 1


async def test_module_level_functions_never_hook(env: Env) -> None:
    behaviour = points_behaviour()
    for op in sync_hooks.SYNC_OPERATIONS:
        behaviour.MODE[f"hook.{op}"] = "veto"
    async with env.factory() as s, s.begin():
        cfg = SysConfig(s)
        tid = await tws.create_ticket(s, env.factory, cfg, params=_ticket_in(env), user_id=AGENT)
        await tws.add_article(
            s,
            ticket_id=tid,
            article=ArticleIn(
                sender_type="agent",
                channel="note",
                subject="N",
                body="b",
                is_visible_for_customer=False,
            ),
            user_id=AGENT,
            sysconfig=cfg,
        )
        await tws.change_state(
            s,
            ticket_id=tid,
            new_state_id=env.state_id("open"),
            user_id=AGENT,
            sysconfig=cfg,
        )
        await tws.move_queue(s, ticket_id=tid, new_queue_id=QUEUE2, user_id=AGENT, sysconfig=cfg)
    assert env.scalar("SELECT queue_id FROM ticket WHERE id = :t", t=tid) == QUEUE2
    assert behaviour.CALLS == []  # no hook ran
    # the system context of a plugin is not hooked either
    await PluginTickets().set_state(tid, "closed successful")
    assert behaviour.CALLS == []


async def test_plugin_write_as_agent_is_hooked(env: Env) -> None:
    tid = await env.ticket()
    points_behaviour().MODE["hook.ticket.state_change"] = "veto"
    with pytest.raises(Exception, match=VETO):
        await PluginTickets().set_state(tid, "closed successful", as_agent=AGENT)
    assert (
        env.scalar(
            "SELECT s.name FROM ticket t JOIN ticket_state s ON s.id = t.ticket_state_id"
            " WHERE t.id = :t",
            t=tid,
        )
        == "new"
    )


async def test_hook_on_other_operation_not_fired(env: Env) -> None:
    tid = await env.ticket()
    await env.run(lambda svc: svc.update_dynamic_fields(AGENT, tid, {"PP55Flag": ["1"]}))
    assert [n for n, _ in points_behaviour().CALLS] == ["hook.ticket.dynamic_fields_update"]


# --- through the API -----------------------------------------------------------------------


def _client(env_: Env, monkeypatch: pytest.MonkeyPatch) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.api.v1 import tickets as tickets_api
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _db() -> Any:
        async with env_.factory() as session:
            yield session

    monkeypatch.setattr(tickets_api, "get_session_factory", lambda *a, **kw: env_.factory)
    monkeypatch.setattr(
        tickets_api,
        "_write_service",
        lambda session, settings: TicketWriteService(
            session, env_.factory, SysConfig(session), mail_sender=env_.sender
        ),
    )
    user = AuthenticatedUser(
        id=AGENT, login="pp55h-agent", first_name="P", last_name="A", auth_method="session"
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _body(**extra: Any) -> dict[str, Any]:
    return {
        "subject": "Re: x",
        "body": "reply",
        "channel": "email",
        "sender_type": "agent",
        "from_address": "support@example.org",
        "to_address": "pp55-cust@example.org",
        **extra,
    }


async def test_api_returns_422_with_message(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    points_behaviour().MODE["hook.article.create"] = "veto"
    tid = await env.ticket()
    before = env.count("article", "ticket_id = :t", t=tid)
    async with _client(env, monkeypatch) as client:
        resp = await client.post(f"/api/v1/tickets/{tid}/articles", json=_body())
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == VETO
    assert env.sender.sent == []
    assert env.count("article", "ticket_id = :t", t=tid) == before


async def test_reply_with_state_veto_sends_nothing(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    points_behaviour().MODE["hook.ticket.state_change"] = "veto"
    tid = await env.ticket()
    before = env.count("article", "ticket_id = :t", t=tid)
    async with _client(env, monkeypatch) as client:
        resp = await client.post(
            f"/api/v1/tickets/{tid}/articles",
            json=_body(state_id=env.state_id("closed successful")),
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == VETO
    assert env.sender.sent == []  # the veto came before the send
    assert env.count("article", "ticket_id = :t", t=tid) == before
    assert _calls("article.create") == []  # the state veto stopped the request first


async def test_reply_with_state_fires_state_hook_once(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    tid = await env.ticket()
    closed = env.state_id("closed successful")
    async with _client(env, monkeypatch) as client:
        resp = await client.post(
            f"/api/v1/tickets/{tid}/articles",
            json=_body(subject="Re", channel="note", state_id=closed),
        )
    assert resp.status_code == 201, resp.text
    assert env.scalar("SELECT ticket_state_id FROM ticket WHERE id = :t", t=tid) == closed
    assert len(_calls("ticket.state_change")) == 1  # pre-flight only, not again in change_state
    assert len(_calls("article.create")) == 1
