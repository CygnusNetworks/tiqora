"""Web-chat tables on both dialects: round trip and the uniqueness guarantees."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, insert, select
from sqlalchemy.exc import IntegrityError

from tests._webchat_fixtures import NOW, cleanup_band, insert_config
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraChatAgentState,
    TiqoraChatConfig,
    TiqoraChatMessage,
    TiqoraChatSession,
)

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
