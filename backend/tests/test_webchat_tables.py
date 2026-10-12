"""Web-chat tables on both dialects: round trip and the uniqueness guarantees."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from tests._webchat_fixtures import NOW, cleanup_band, insert_config
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraChatAgentState,
    TiqoraChatConfig,
    TiqoraChatMessage,
    TiqoraChatSession,
)
from tiqora.webchat import store
from tiqora.webchat.store import config_values

pytestmark = pytest.mark.db

CONFIGS = range(48400, 48420)
QUEUE = 48402
USER = 48405
SESSION = "0" * 32
URLS = ["mariadb_znuny_url", "postgres_znuny_url"]


def _cleanup(engine: Engine) -> None:
    with engine.begin() as conn:
        cleanup_band(conn, config_ids=CONFIGS, queue_ids=[], group_ids=[], user_ids=[USER])


@pytest.fixture
def engine(url_fixture: str, request: pytest.FixtureRequest) -> Iterator[Engine]:
    eng = create_engine(request.getfixturevalue(url_fixture))
    TiqoraBase.metadata.create_all(eng, checkfirst=True)
    _cleanup(eng)
    try:
        yield eng
    finally:
        _cleanup(eng)
        eng.dispose()


def _message(seq: int, client_id: str | None) -> dict[str, object]:
    return {
        "session_id": SESSION,
        "ticket_id": 48403,
        "seq": seq,
        "sender": "visitor",
        "body": "hello",
        "client_id": client_id,
        "created": NOW,
    }


@pytest.mark.parametrize("url_fixture", URLS)
def test_config_session_message_round_trip(engine: Engine, url_fixture: str) -> None:
    with engine.begin() as conn:
        insert_config(
            conn,
            config_id=48401,
            queue_id=QUEUE,
            public_key="wc_" + "a" * 32,
            origins=["https://www.example.org"],
        )
        conn.execute(
            insert(TiqoraChatSession).values(
                id=SESSION,
                config_id=48401,
                queue_id=QUEUE,
                ticket_id=48403,
                status="waiting",
                token_hash="f" * 64,
                visitor_id="v" * 22,
                visitor_last_seen=NOW,
                created=NOW,
            )
        )
        # NULL client ids never collide
        conn.execute(insert(TiqoraChatMessage).values(**_message(1, None)))
        conn.execute(insert(TiqoraChatMessage).values(**_message(2, None)))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(TiqoraChatMessage).values(**_message(2, None)))
    with engine.begin() as conn:
        conn.execute(insert(TiqoraChatMessage).values(**_message(3, "c1")))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(TiqoraChatMessage).values(**_message(4, "c1")))

    with engine.connect() as conn:
        row = conn.execute(select(TiqoraChatConfig).where(TiqoraChatConfig.id == 48401)).one()
        assert json.loads(row.topics)[0]["queue_id"] == QUEUE
        assert json.loads(row.allowed_origins) == ["https://www.example.org"]
        assert row.valid is True and row.show_agent_names is True and row.retention_days == 30
        sess = conn.execute(select(TiqoraChatSession)).one()
        assert sess.last_seq == 0 and sess.purged_at is None
        seqs = [
            m.seq
            for m in conn.execute(
                select(TiqoraChatMessage)
                .where(TiqoraChatMessage.session_id == SESSION)
                .order_by(TiqoraChatMessage.seq)
            )
        ]
        assert seqs == [1, 2, 3]


@pytest.mark.parametrize("url_fixture", URLS)
def test_config_name_and_key_are_unique(engine: Engine, url_fixture: str) -> None:
    with engine.begin() as conn:
        insert_config(
            conn, config_id=48410, queue_id=QUEUE, public_key="wc_" + "b" * 32, origins=[]
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        insert_config(
            conn, config_id=48411, queue_id=QUEUE, public_key="wc_" + "b" * 32, origins=[]
        )


@pytest.mark.parametrize("url_fixture", URLS)
def test_agent_state_pk_is_user_id(engine: Engine, url_fixture: str) -> None:
    row = {
        "user_id": USER,
        "available": True,
        "topics": "[]",
        "max_concurrent": 3,
        "changed": NOW,
    }
    with engine.begin() as conn:
        conn.execute(insert(TiqoraChatAgentState).values(**row))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(TiqoraChatAgentState).values(**row))
    with engine.connect() as conn:
        state = conn.execute(select(TiqoraChatAgentState)).one()
        assert state.user_id == USER and state.available is True


BOT_CONFIGS = range(54400, 54420)
BOT_SESSION = "1" * 32


def _bot_cleanup(engine: Engine) -> None:
    with engine.begin() as conn:
        cleanup_band(conn, config_ids=BOT_CONFIGS, queue_ids=[], group_ids=[], user_ids=[])


@pytest.fixture
def bot_engine(url_fixture: str, request: pytest.FixtureRequest) -> Iterator[Engine]:
    eng = create_engine(request.getfixturevalue(url_fixture))
    TiqoraBase.metadata.create_all(eng, checkfirst=True)
    _bot_cleanup(eng)
    try:
        yield eng
    finally:
        _bot_cleanup(eng)
        eng.dispose()


def _insert_bot_session(conn: Connection, **over: object) -> None:
    values: dict[str, object] = dict(
        id=BOT_SESSION,
        config_id=54401,
        queue_id=QUEUE,
        ticket_id=54403,
        status="bot",
        token_hash="f" * 64,
        visitor_id="v" * 22,
        visitor_last_seen=NOW,
        created=NOW,
    )
    values.update(over)
    conn.execute(insert(TiqoraChatSession).values(**values))


@pytest.mark.parametrize("url_fixture", URLS)
def test_new_session_columns_defaults(bot_engine: Engine, url_fixture: str) -> None:
    with bot_engine.begin() as conn:
        insert_config(
            conn, config_id=54401, queue_id=QUEUE, public_key="wc_" + "c" * 32, origins=[]
        )
        _insert_bot_session(conn)
    with bot_engine.connect() as conn:
        sess = conn.execute(
            select(TiqoraChatSession).where(TiqoraChatSession.id == BOT_SESSION)
        ).one()
    assert sess.status == "bot"
    assert (sess.last_visitor_seq, sess.bot_answered_seq, sess.bot_attempts) == (0, 0, 0)
    assert sess.identity_source is None and sess.handoff_reason is None
    assert sess.bot_outcome is None and sess.handoff_at is None
    assert sess.bot_claimed_at is None and sess.bot_claim_owner is None


@pytest.mark.parametrize("url_fixture", URLS)
def test_new_session_columns_round_trip(bot_engine: Engine, url_fixture: str) -> None:
    owner = "h" * 40 + ":" + "1" * 6 + ":" + "a" * 8  # 55 chars, within VARCHAR(64)
    with bot_engine.begin() as conn:
        insert_config(
            conn, config_id=54401, queue_id=QUEUE, public_key="wc_" + "c" * 32, origins=[]
        )
        _insert_bot_session(
            conn,
            identity_source="website",
            last_visitor_seq=4,
            bot_answered_seq=3,
            bot_claimed_at=NOW,
            bot_claim_owner=owner,
            bot_attempts=2,
            bot_outcome="handoff",
            handoff_at=NOW,
            handoff_reason="unavailable",
        )
    with bot_engine.connect() as conn:
        sess = conn.execute(
            select(TiqoraChatSession).where(TiqoraChatSession.id == BOT_SESSION)
        ).one()
    assert (sess.identity_source, sess.last_visitor_seq, sess.bot_answered_seq) == (
        "website",
        4,
        3,
    )
    assert (sess.bot_claim_owner, sess.bot_attempts, sess.bot_outcome) == (owner, 2, "handoff")
    assert sess.handoff_reason == "unavailable" and sess.handoff_at == NOW


@pytest.mark.parametrize("url_fixture", URLS)
def test_config_values_read_new_scalar_columns(bot_engine: Engine, url_fixture: str) -> None:
    with bot_engine.begin() as conn:
        insert_config(
            conn, config_id=54402, queue_id=QUEUE, public_key="wc_" + "d" * 32, origins=[]
        )
        insert_config(
            conn,
            config_id=54403,
            queue_id=QUEUE,
            public_key="wc_" + "e" * 32,
            origins=[],
            values_override={
                "bot_name": "Robo",
                "bot_tone_prompt": "Kurz.",
                "show_on_portal": True,
                "identity_create_customers": True,
                "offline_mode": "ai",
            },
        )
    with Session(bot_engine) as db:
        plain = config_values(db.get(TiqoraChatConfig, 54402))  # type: ignore[arg-type]
        custom = config_values(db.get(TiqoraChatConfig, 54403))  # type: ignore[arg-type]
        raw = db.get(TiqoraChatConfig, 54402)
        assert raw is not None and raw.identity_secret is None
        raw.bot_name = None  # nullable column: NULL means the default
        with capture_logs() as logs:
            nulled = config_values(raw)
        raw.bot_name = ""
        with capture_logs() as bad_logs:
            blank = config_values(raw)
        db.rollback()
    assert plain.bot_name == "KI-Assistent" and plain.show_on_portal is False
    assert (custom.bot_name, custom.bot_tone_prompt) == ("Robo", "Kurz.")
    assert custom.show_on_portal is True and custom.identity_create_customers is True
    assert custom.offline_mode == "ai"
    assert nulled.bot_name == "KI-Assistent"
    assert [e for e in logs if e["event"] == "webchat_config_column_invalid"] == []
    assert blank.bot_name == "KI-Assistent"
    assert [e["column"] for e in bad_logs if e["event"] == "webchat_config_column_invalid"] == [
        "bot_name"
    ]


def test_live_statuses_include_bot() -> None:
    assert frozenset({"bot", "waiting", "active"}) == store.LIVE_STATUSES
