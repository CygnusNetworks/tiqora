"""Template placeholders from plugins (D6, both dialects).

Seed band 55110-55129: group 55111, queue ``pp55-phq`` 55112, agent 55113 (``pp55.ph``),
notification 55114. Standard templates are named ``pp55-tpl-NN`` (autoincrement ids, removed
by name); tickets and their rows are removed with the row marks.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._plugin_points import boot_points_plugin, points_behaviour
from tests._row_cleanup import delete_above_marks, snapshot_marks
from tiqora.channels.email.placeholder import PlaceholderContext, expand_placeholders
from tiqora.config import Settings
from tiqora.domain import ticket_service
from tiqora.domain.ticket_service import TicketService
from tiqora.plugins.context import PluginTickets
from tiqora.plugins.state import set_loaded_plugins
from tiqora.plugins.writes import TicketDraft
from tiqora.worker import notifications
from tiqora.worker.notifications import (
    _build_notification_context,
    _NotificationRow,
    _Recipient,
    _render_message,
)
from tiqora.znuny.sysconfig import SysConfig

GROUP, QUEUE, AGENT, NOTIFICATION = 55111, 55112, 55113, 55114
NOW = datetime(2026, 10, 12, 12, 0, 0)

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
    conn.execute(
        text(f"DELETE FROM notification_event_message WHERE notification_id = {NOTIFICATION}")
    )
    conn.execute(text(f"DELETE FROM notification_event WHERE id = {NOTIFICATION}"))
    conn.execute(
        text(
            "DELETE FROM queue_standard_template WHERE standard_template_id IN"
            " (SELECT id FROM standard_template WHERE name LIKE 'pp55-tpl-%')"
        )
    )
    conn.execute(text("DELETE FROM standard_template WHERE name LIKE 'pp55-tpl-%'"))
    conn.execute(text(f"DELETE FROM queue_standard_template WHERE queue_id = {QUEUE}"))
    conn.execute(text(f"DELETE FROM queue WHERE id = {QUEUE}"))
    conn.execute(text(f"DELETE FROM group_user WHERE group_id = {GROUP}"))
    conn.execute(text(f"DELETE FROM users WHERE id = {AGENT}"))
    conn.execute(text(f"DELETE FROM permission_groups WHERE id = {GROUP}"))


def _seed(sync_url: str) -> None:
    from tiqora.db.tiqora.base import TiqoraBase

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn, checkfirst=True)
    with engine.begin() as conn:
        _clean_seed(conn)
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:g, 'pp55-phgrp', 1, :t, 1, :t, 1)"
            ),
            {"g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:q, 'pp55-phq', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"q": QUEUE, "g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, 'pp55.ph', 'x', 'Place', 'Holder', 1, :t, 1, :t, 1)"
            ),
            {"u": AGENT, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO notification_event (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:n, 'pp55-notification', 1, :t, 1, :t, 1)"
            ),
            {"n": NOTIFICATION, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO notification_event_message (notification_id, subject, text,"
                " content_type, language) VALUES (:n, :s, :b, 'text/plain', 'en')"
            ),
            {
                "n": NOTIFICATION,
                "s": "S <TIQORA_PLUGIN_POINTS_GREETING>",
                "b": "B <OTRS_PLUGIN_POINTS_GREETING> <TIQORA_PLUGIN_POINTS_WHOAMI[2]>",
            },
        )
    engine.dispose()


def _add_templates(sync_url: str, count: int, body: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for i in range(count):
            conn.execute(
                text(
                    "INSERT INTO standard_template (name, text, content_type, template_type,"
                    " valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:n, :b, 'text/plain', 'Answer', 1, :t, 1, :t, 1)"
                ),
                {"n": f"pp55-tpl-{i:02d}", "b": body, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue_standard_template (queue_id, standard_template_id,"
                " create_time, create_by, change_time, change_by)"
                " SELECT :q, id, :t, 1, :t, 1 FROM standard_template WHERE name LIKE 'pp55-tpl-%'"
            ),
            {"q": QUEUE, "t": NOW},
        )
    engine.dispose()


class Env:
    def __init__(self, sync_url: str, fac: async_sessionmaker[AsyncSession]) -> None:
        self.sync_url = sync_url
        self.factory = fac


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
        "tiqora.domain.ticket_write_service",
    ):
        monkeypatch.setattr(f"{mod}.get_session_factory", lambda fac=fac: fac, raising=False)
    boot_points_plugin(tmp_path, monkeypatch)
    yield Env(sync_url, fac)
    await engine.dispose()
    delete_above_marks(sync_url, marks)
    clean = create_engine(sync_url)
    with clean.begin() as conn:
        _clean_seed(conn)
    clean.dispose()


def _sysconfig() -> SysConfig:
    async def fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=fetch)


async def _ticket() -> int:
    return await PluginTickets().create(
        TicketDraft(title="Platzhalter", queue="pp55-phq", body="Hallo")
    )


def _calls(name: str) -> list[Any]:
    return [c for c in points_behaviour().CALLS if c[0] == name]


async def test_plugin_tag_resolves_both_prefixes(env: Env) -> None:
    tid = await _ticket()
    async with env.factory() as session:
        out = await expand_placeholders(
            session,
            _sysconfig(),
            "A <TIQORA_PLUGIN_POINTS_GREETING> B <OTRS_PLUGIN_POINTS_GREETING>",
            ticket_id=tid,
        )
    assert out == f"A Hallo aus Ticket {tid} B Hallo aus Ticket {tid}"
    # the same tag twice in one text calls the plugin once (memo per context)
    assert len(_calls("placeholder.GREETING")) == 1


async def test_request_carries_ticket_agent_bracket_and_recipient(env: Env) -> None:
    tid = await _ticket()
    async with env.factory() as session:
        out = await expand_placeholders(
            session,
            _sysconfig(),
            "<TIQORA_PLUGIN_POINTS_WHOAMI[7]> <TIQORA_PLUGIN_POINTS_WHOAMI>",
            ticket_id=tid,
            user_id=AGENT,
            context=None,
        )
    assert out == "-|7|UTC -|None|UTC"
    # different brackets are different memo keys: two calls, one with the bracket
    reqs = [c[1] for c in _calls("placeholder.WHOAMI")]
    assert [r.bracket for r in reqs] == ["7", None]
    assert all(r.ticket_id == tid and r.user_id == AGENT for r in reqs)
    assert reqs[0].plugin.plugin_id == "org.example.points"

    ctx = PlaceholderContext(notification_recipient={"useremail": "a@example.org"}, time_zone="UTC")
    out = await expand_placeholders(
        None, _sysconfig(), "<TIQORA_PLUGIN_POINTS_WHOAMI>", context=ctx
    )
    assert out == "a@example.org|None|UTC"


async def test_slow_and_broken_render_empty(env: Env) -> None:
    tid = await _ticket()
    text_ = (
        "T=<OTRS_TICKET_TicketID> [<TIQORA_PLUGIN_POINTS_SLOW>] [<TIQORA_PLUGIN_POINTS_BROKEN>]"
        " G=<TIQORA_PLUGIN_POINTS_GREETING>"
    )
    started = time.monotonic()
    async with env.factory() as session:
        out = await expand_placeholders(session, _sysconfig(), text_, ticket_id=tid)
    assert time.monotonic() - started < 0.5
    assert out == f"T={tid} [] [] G=Hallo aus Ticket {tid}"
    assert len(_calls("placeholder.SLOW")) == 1
    assert len(_calls("placeholder.BROKEN")) == 1


async def test_unknown_plugin_tag_empty_and_dash_in_znuny_mode(env: Env) -> None:
    tid = await _ticket()
    async with env.factory() as session:
        sysconfig = _sysconfig()
        plain = await expand_placeholders(
            session,
            sysconfig,
            "x<OTRS_PLUGIN_POINTS_NOPE>y<OTRS_PLUGIN_OTHER_GREETING>z",
            ticket_id=tid,
        )
        assert plain == "xyz"
        ctx = PlaceholderContext(znuny_dash=True)
        dashed = await expand_placeholders(
            None, sysconfig, "x<OTRS_PLUGIN_POINTS_NOPE>y", context=ctx
        )
        assert dashed == "x-y"
    assert _calls("placeholder.GREETING") == []


async def test_inactive_plugin_renders_empty(env: Env) -> None:
    set_loaded_plugins([])
    out = await expand_placeholders(
        None, _sysconfig(), "[<TIQORA_PLUGIN_POINTS_GREETING>]", context=PlaceholderContext()
    )
    assert out == "[]"
    assert points_behaviour().CALLS == []


async def test_html_escaped_when_context_escapes(env: Env) -> None:
    sysconfig = _sysconfig()
    raw = await expand_placeholders(
        None, sysconfig, "<TIQORA_PLUGIN_POINTS_HTMLISH>", context=PlaceholderContext()
    )
    assert raw == "<b>&</b>"
    escaped = await expand_placeholders(
        None,
        sysconfig,
        "<TIQORA_PLUGIN_POINTS_HTMLISH>",
        context=PlaceholderContext(escape_html=True),
    )
    assert escaped == "&lt;b&gt;&amp;&lt;/b&gt;"


async def test_one_expansion_shares_one_budget(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.channels.email import placeholder as placeholder_module

    monkeypatch.setattr(placeholder_module, "EXPAND_BUDGET_SECONDS", 0.25)
    # SLOW times out after 100 ms; distinct brackets defeat the memo, so every tag is a call
    tags = " ".join(f"<TIQORA_PLUGIN_POINTS_SLOW[{i}]>" for i in range(12))
    started = time.monotonic()
    out = await expand_placeholders(
        None, _sysconfig(), f"<TIQORA_PLUGIN_POINTS_HTMLISH> {tags}", context=PlaceholderContext()
    )
    assert time.monotonic() - started < 0.6
    assert out.startswith("<b>&</b>")  # resolved before the budget was used up
    assert 1 <= len(_calls("placeholder.SLOW")) <= 3  # the rest found the budget used up


async def test_template_list_shares_one_budget(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_templates(
        env.sync_url, 20, "a <TIQORA_PLUGIN_POINTS_SLOW> b <TIQORA_PLUGIN_POINTS_GREETING>"
    )
    tid = await _ticket()
    monkeypatch.setattr(ticket_service, "TEMPLATE_LIST_PLUGIN_BUDGET_SECONDS", 0.25)
    started = time.monotonic()
    async with env.factory() as session:
        out = await TicketService(session).list_templates(AGENT, tid)
    assert time.monotonic() - started < 3.0
    assert len(out) == 20
    # 20 slow tags at 100 ms would take 2 s; the shared budget stops the plugin after ~0.25 s
    assert len(_calls("placeholder.SLOW")) <= 3
    assert all(t.text.startswith("a  b ") for t in out)  # slow tag rendered empty everywhere


def _boot_registrar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from tiqora.plugins.context import make_plugin_context
    from tiqora.plugins.discovery import discover_plugins
    from tiqora.plugins.loader import import_and_register

    boot_points_plugin(tmp_path, monkeypatch)
    (discovered,) = [d for d in discover_plugins() if d.plugin_id == "org.example.points"]
    return import_and_register(discovered, make_plugin_context(discovered))


async def _resolve(req: Any) -> str:
    return "x"


def test_secret_looking_names_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.registrar import RegistrationRejected

    registrar = _boot_registrar(tmp_path, monkeypatch)
    for name in ("API_TOKEN", "DB_PASSWORD", "SECRET_X", "AUTH_CODE", "USER_PW"):
        with pytest.raises(RegistrationRejected, match="maskiert"):
            registrar.placeholder(name, _resolve)
    # the short id can be the cause: "tokenz" would mask every tag of the plugin
    registrar._manifest = registrar._manifest.model_copy(update={"short_id": "tokenz"})
    with pytest.raises(RegistrationRejected, match="Kurzname „tokenz“.*maskiert"):
        registrar.placeholder("GREETING", _resolve)


def test_name_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.plugins.placeholders import PLACEHOLDERS
    from tiqora.plugins.registrar import RegistrationRejected

    registrar = _boot_registrar(tmp_path, monkeypatch)
    with pytest.raises(RegistrationRejected, match="Ungültiger Platzhaltername"):
        registrar.placeholder("greeting", _resolve)
    with pytest.raises(RegistrationRejected, match="Ungültiger Platzhaltername"):
        registrar.placeholder("X", _resolve)
    with pytest.raises(RegistrationRejected, match="async"):
        registrar.placeholder("SYNCONE", lambda req: "x")  # type: ignore[arg-type]
    for bad in (49, 2001, True):
        with pytest.raises(RegistrationRejected, match="Zeitlimit"):
            registrar.placeholder("TOOFAST", _resolve, timeout_ms=bad)
    # a core name stays inside PLUGIN_POINTS_: it can never shadow <OTRS_TICKET_TITLE>
    assert registrar.placeholder("TICKET_TITLE", _resolve) == "PLUGIN_POINTS_TICKET_TITLE"
    staged = [i.tag for p, i in registrar.staged if p is PLACEHOLDERS]
    assert "PLUGIN_POINTS_TICKET_TITLE" in staged
    assert "TICKET_TITLE" not in staged


async def test_core_tag_unaffected_by_same_named_plugin_tag(env: Env) -> None:
    out = await expand_placeholders(
        None,
        _sysconfig(),
        "<OTRS_TICKET_TITLE> <TIQORA_PLUGIN_POINTS_TICKET_TITLE>",
        ticket={"title": "Kern"},
    )
    assert out == "Kern "


def test_placeholder_point_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.extensions.core import get_registry
    from tiqora.plugins.placeholders import PLACEHOLDERS

    boot_points_plugin(tmp_path, monkeypatch)
    tags = {p.tag: p for p in get_registry().items(PLACEHOLDERS)}
    assert set(tags) == {
        "PLUGIN_POINTS_GREETING",
        "PLUGIN_POINTS_SLOW",
        "PLUGIN_POINTS_BROKEN",
        "PLUGIN_POINTS_HTMLISH",
        "PLUGIN_POINTS_WHOAMI",
    }
    assert tags["PLUGIN_POINTS_SLOW"].timeout_ms == 100
    assert tags["PLUGIN_POINTS_GREETING"].timeout_ms == 500


async def test_notification_context_has_ticket_id(env: Env) -> None:
    tid = await _ticket()
    async with env.factory() as session:
        sysconfig = SysConfig(session)
        base = await _build_notification_context(
            session,
            sysconfig,
            Settings(),
            ticket={"id": tid},
            article_id=None,
            recipient_kind="Agent",
            time_zone="UTC",
        )
        assert base.ticket_id == tid
        recipient = _Recipient(kind="Agent", email="agent@example.org", user_id=AGENT)
        rendered = await _render_message(
            session,
            sysconfig,
            _NotificationRow(id=NOTIFICATION, name="pp55-notification"),
            "en",
            recipient=recipient,
            base_context=base,
            ticket_id=tid,
            hook_cfg=None,
            tn="",
        )
    assert rendered is not None
    subject, body, _ = rendered
    assert subject == f"S Hallo aus Ticket {tid}"
    # the recipient map reaches the plugin; the memo of the base context is not shared
    assert body == f"B Hallo aus Ticket {tid} agent@example.org|2|UTC"
    assert base.plugin_memo == {}
    assert notifications.MESSAGE_PLUGIN_BUDGET_SECONDS == 3.0
