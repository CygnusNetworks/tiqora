"""DB tests for tiqora.ai.triage_worker.

Seed ids use the 86xx band (unique per test via the ``ns`` offset) — see the
band registry in ``test_autoresponse.py``. This module deletes everything it
commits (``_cleanup``) so it also passes under TIQORA_STRICT_DB_LEAKS on its
own.

``build_llm_client`` is monkeypatched at the ``tiqora.ai.triage_worker``
module level, so no real HTTP call is ever made.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.ai import policies as ai_policies
from tiqora.ai import providers as ai_providers
from tiqora.ai.gate import (
    OPERATION_MODE_PARALLEL,
    OPERATION_MODE_TIQORA_PRIMARY,
    set_operation_mode,
)
from tiqora.ai.llm import LlmClient, LlmResponse, LlmUsage, ToolCall
from tiqora.ai.models import (
    TRIAGE_STATUS_APPLIED,
    TRIAGE_STATUS_NO_ACTION,
    TRIAGE_STATUS_OPEN,
)
from tiqora.ai.triage import queue_key
from tiqora.ai.triage_worker import run_triage_tick
from tiqora.config import get_settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.settings_store import (
    KEY_AI_TRIAGE_WATERMARK,
    KEY_OPERATION_MODE,
    get_setting,
    set_setting,
)

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


# Filled by _seed so the module teardown below knows which DB to clean.
_SYNC_URLS: set[str] = set()


@pytest.fixture(autouse=True, scope="module")
def _cleanup_triage_settings() -> Any:
    """Delete the global tiqora_settings rows this module writes.

    They are shared across the session-scoped container, so they go at module
    teardown rather than per test — removing them between tests would pull the
    ground out from under the next one.
    """
    yield
    for url in _SYNC_URLS:
        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM tiqora_settings WHERE `key` IN (:k1, :k2)"),
                {
                    "k1": KEY_AI_TRIAGE_WATERMARK,
                    "k2": KEY_OPERATION_MODE,
                },
            )
        engine.dispose()


NETZKOORDINATOR_DESC = (
    "Organisatorische Betreuung der Netzmentoren: Wahl, Rechenschaftsberichte, "
    "Koordination, Netzkonferenz."
)
NETADMIN_DESC = (
    "Technischer Support: Anschluss, Router, Sperren, Account, WLAN-Technik, "
    "IPTV, Diagnose, Freischaltung."
)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


class TriageLlm:
    """Fake LLM returning one scripted route_ticket tool call per sample."""

    def __init__(
        self,
        *,
        queue_key_value: str,
        confidence: int = 95,
        reason: str = "technische stoerung",
        content_instead_of_tool: bool = False,
        garbage: bool = False,
    ) -> None:
        self.queue_key_value = queue_key_value
        self.confidence = confidence
        self.reason = reason
        self.content_instead_of_tool = content_instead_of_tool
        self.garbage = garbage
        self.calls = 0
        self.last_messages: list[Any] = []

    async def chat(
        self,
        *,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LlmResponse:
        self.calls += 1
        self.last_messages = messages
        usage = LlmUsage(prompt_tokens=10, completion_tokens=5)
        if self.garbage:
            return LlmResponse(content="I am a teapot", usage=usage)
        payload = {
            "queue_key": self.queue_key_value,
            "confidence": self.confidence,
            "reason": self.reason,
        }
        if self.content_instead_of_tool:
            return LlmResponse(content=json.dumps(payload), usage=usage)
        return LlmResponse(
            content=None,
            tool_calls=[ToolCall(id="c1", name="route_ticket", arguments=payload)],
            usage=usage,
        )

    @property
    def last_user_message(self) -> str:
        return next(m.content for m in reversed(self.last_messages) if m.role == "user")


def _ids(ns: int) -> dict[str, int]:
    return {
        "agent_id": 8600 + ns,
        "group_id": 8630 + ns,
        "source_queue_id": 8600 + ns,
        "target_queue_id": 8640 + ns,
        "ticket_id": 8670 + ns,
    }


def _cleanup(sync_url: str, ns: int) -> None:
    """Delete everything this module's seed creates, children first."""
    i = _ids(ns)
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt, params in (
            ("DELETE FROM tiqora_ai_triage WHERE ticket_id = :tid", {"tid": i["ticket_id"]}),
            (
                "DELETE FROM tiqora_ai_article_origin WHERE queue_id IN (:q1, :q2)",
                {"q1": i["source_queue_id"], "q2": i["target_queue_id"]},
            ),
            (
                "DELETE FROM tiqora_ai_audit_log WHERE queue_id IN (:q1, :q2)",
                {"q1": i["source_queue_id"], "q2": i["target_queue_id"]},
            ),
            (
                "DELETE FROM tiqora_ai_usage WHERE queue_id IN (:q1, :q2)",
                {"q1": i["source_queue_id"], "q2": i["target_queue_id"]},
            ),
            (
                "DELETE FROM tiqora_ai_ticket_state WHERE ticket_id = :tid",
                {"tid": i["ticket_id"]},
            ),
            ("DELETE FROM tiqora_event_outbox WHERE ticket_id = :tid", {"tid": i["ticket_id"]}),
            # ticket_history.article_id has an FK on article, so history must
            # go before the articles it points at.
            ("DELETE FROM ticket_history WHERE ticket_id = :tid", {"tid": i["ticket_id"]}),
            (
                "DELETE FROM article_data_mime WHERE article_id IN"
                " (SELECT id FROM article WHERE ticket_id = :tid)",
                {"tid": i["ticket_id"]},
            ),
            ("DELETE FROM article WHERE ticket_id = :tid", {"tid": i["ticket_id"]}),
            ("DELETE FROM ticket WHERE id = :tid", {"tid": i["ticket_id"]}),
            (
                "DELETE FROM tiqora_ai_queue_policy WHERE queue_id IN (:q1, :q2)",
                {"q1": i["source_queue_id"], "q2": i["target_queue_id"]},
            ),
            (
                "DELETE FROM queue WHERE id IN (:q1, :q2)",
                {"q1": i["source_queue_id"], "q2": i["target_queue_id"]},
            ),
            (
                "DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid",
                {"uid": i["agent_id"], "gid": i["group_id"]},
            ),
            ("DELETE FROM permission_groups WHERE id = :gid", {"gid": i["group_id"]}),
            ("DELETE FROM users WHERE id = :uid", {"uid": i["agent_id"]}),
            (
                "DELETE FROM customer_user WHERE login = :login",
                {"login": f"s27tgras86{ns}@uni-bonn.de"},
            ),
            (
                "DELETE FROM tiqora_llm_provider WHERE name = :n",
                {"n": f"fake-triage-provider-{i['source_queue_id']}"},
            ),
            (
                "DELETE FROM tiqora_cache_invalidation WHERE ticket_id = :tid",
                {"tid": i["ticket_id"]},
            ),
        ):
            conn.execute(text(stmt), params)
    engine.dispose()


def _seed(sync_url: str, *, ns: int, created: datetime | None = None) -> dict[str, int]:
    _SYNC_URLS.add(sync_url)
    _cleanup(sync_url, ns)
    i = _ids(ns)
    created = created or NOW
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Triage', 'Bot', 1, :t, 1, :t, 1)"
            ),
            {"id": i["agent_id"], "login": f"agent.triage.86{ns}", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": i["group_id"], "name": f"triage-grp-86{ns}", "t": NOW},
        )
        for key in ("ro", "rw", "note", "move_into"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": i["agent_id"], "gid": i["group_id"], "k": key, "t": NOW},
            )
        for qid, name in (
            (i["source_queue_id"], f"stw-bn-netzkoordinator-86{ns}"),
            (i["target_queue_id"], f"stw-bn-86{ns}"),
        ):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "name": name, "gid": i["group_id"], "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, :title, :qid, 1, 1, :uid, 1, 3, 4, :cid, :cuid,"
                " 0, 0, 0, 0, 0, 0, 0, :ct, 1, :ct, 1)"
            ),
            {
                "id": i["ticket_id"],
                "tn": f"20240601860{ns:03d}",
                "title": f"Triage ticket 86{ns}",
                "qid": i["source_queue_id"],
                "uid": i["agent_id"],
                "cid": f"CUST86{ns}",
                "cuid": f"hausmeister86{ns}@example.org",
                "ct": created,
            },
        )
    engine.dispose()
    return i


def _add_article(
    sync_url: str,
    *,
    ticket_id: int,
    sender_type: str,
    body: str,
    subject: str = "Kein Internet",
    from_address: str = "hausmeister@example.org",
) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        st = conn.execute(
            text("SELECT id FROM article_sender_type WHERE name = :n LIMIT 1"), {"n": sender_type}
        ).scalar()
        ch = conn.execute(
            text("SELECT id FROM communication_channel WHERE name = 'Email' LIMIT 1")
        ).scalar()
        fp = f"fp-triage-{ticket_id}-{sender_type}-{abs(hash(body)) % 10**8}"
        conn.execute(
            text(
                "INSERT INTO article (ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " insert_fingerprint, create_time, create_by, change_time, change_by)"
                " VALUES (:tid, :st, :ch, 1, 0, :fp, :t, 1, :t, 1)"
            ),
            {"tid": ticket_id, "st": st, "ch": ch, "fp": fp, "t": NOW},
        )
        article_id = conn.execute(
            text("SELECT id FROM article WHERE insert_fingerprint = :fp LIMIT 1"), {"fp": fp}
        ).scalar()
        conn.execute(
            text(
                "INSERT INTO article_data_mime (article_id, a_subject, a_body, a_from,"
                " incoming_time, create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :subj, :body, :frm, 0, :t, 1, :t, 1)"
            ),
            {"aid": article_id, "subj": subject, "body": body, "frm": from_address, "t": NOW},
        )
    engine.dispose()
    assert article_id is not None
    return int(article_id)


def _insert_event(
    sync_url: str,
    *,
    ticket_id: int,
    article_id: int | None,
    event_type: str = "ArticleCreate",
    auto_generated: bool | None = None,
) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        payload: dict[str, Any] = {}
        if article_id is not None:
            payload["article_id"] = article_id
        if auto_generated is not None:
            payload["auto_generated"] = auto_generated
        conn.execute(
            text(
                "INSERT INTO tiqora_event_outbox (event_type, ticket_id, payload,"
                " created, processed) VALUES (:et, :tid, :pl, current_timestamp, 0)"
            ),
            {"et": event_type, "tid": ticket_id, "pl": json.dumps(payload)},
        )
        event_id = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    engine.dispose()
    return int(event_id or 0)


async def _setup_policies(
    session: Any,
    *,
    ids: dict[str, int],
    auto_threshold: int = 80,
    suggest_threshold: int = 50,
    samples: int = 1,
    customer_fix: bool = False,
    customer_fix_threshold: int = 100,
    delay_reply: bool = False,
    source_description: str = NETZKOORDINATOR_DESC,
    target_description: str | None = NETADMIN_DESC,
) -> None:
    await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
    provider = await ai_providers.create_provider(
        session,
        settings=get_settings(),
        change_by=1,
        name=f"fake-triage-provider-{ids['source_queue_id']}",
        kind="openai_compat",
        base_url="https://llm.example/v1",
        default_model="fake-model",
        api_key=None,
        extra_json=None,
        supports_tools=True,
        supports_streaming=False,
        eu_hosted=True,
    )
    # Destination queue first: the source policy's target validation needs it
    # to exist, and triage_delay_reply is read off the destination.
    await ai_policies.create_queue_policy(
        session,
        change_by=1,
        queue_id=ids["target_queue_id"],
        routing_description=target_description,
        triage_delay_reply=delay_reply,
    )
    await ai_policies.create_queue_policy(
        session,
        change_by=1,
        queue_id=ids["source_queue_id"],
        enabled_triage=True,
        service_user_id=ids["agent_id"],
        llm_provider_id=provider.id,
        pii_masking=False,
        routing_description=source_description,
        triage_target_queue_ids=json.dumps([ids["target_queue_id"]]),
        triage_auto_threshold=auto_threshold,
        triage_suggest_threshold=suggest_threshold,
        triage_samples=samples,
        triage_customer_fix_enabled=customer_fix,
        triage_customer_fix_auto_threshold=customer_fix_threshold,
    )


def _patch_llm(monkeypatch: pytest.MonkeyPatch, llm: LlmClient) -> None:
    async def _fake(*_args: Any, **_kwargs: Any) -> LlmClient:
        return llm

    monkeypatch.setattr("tiqora.ai.triage_worker.build_llm_client", _fake)


async def _reset_watermark(factory: Any, sync_url: str) -> None:
    """Point the triage cursor just below the newest outbox row so the next
    tick sees only what the test inserts afterwards."""
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        current = conn.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM tiqora_event_outbox")
        ).scalar()
    engine.dispose()
    async with factory() as session:
        await set_setting(session, KEY_AI_TRIAGE_WATERMARK, str(int(current or 0)))


def _ticket_queue(sync_url: str, ticket_id: int) -> int:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        value = conn.execute(
            text("SELECT queue_id FROM ticket WHERE id = :tid"), {"tid": ticket_id}
        ).scalar()
    engine.dispose()
    return int(value or 0)


def _triage_row(sync_url: str, ticket_id: int) -> dict[str, Any] | None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        row = (
            conn.execute(
                text("SELECT * FROM tiqora_ai_triage WHERE ticket_id = :tid"), {"tid": ticket_id}
            )
            .mappings()
            .first()
        )
    engine.dispose()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------


async def test_first_tick_seeds_watermark_and_processes_nothing(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cursor must start at "now", never at 0.

    get_setting_int(..., default=0) cannot tell a missing row from a stored
    zero; with that default the first tick would replay the whole outbox and
    re-route every historical ticket.
    """
    ids = _seed(mariadb_znuny_url, ns=1, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]))
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids)
            await set_setting(session, KEY_AI_TRIAGE_WATERMARK, "")

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        result = await run_triage_tick(session_factory=factory)

        assert result["triage_events"] == 0
        assert "triage_seeded" in result
        assert llm.calls == 0
        assert _triage_row(mariadb_znuny_url, ids["ticket_id"]) is None

        async with factory() as session:
            stored = await get_setting(session, KEY_AI_TRIAGE_WATERMARK)
        assert stored is not None and int(stored) > 0
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 1)


async def test_parallel_still_drains_and_advances_watermark(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cutting over to tiqora_primary must not triage a backlog."""
    ids = _seed(mariadb_znuny_url, ns=2, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]))
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids)
            await set_operation_mode(session, OPERATION_MODE_PARALLEL)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        event_id = _insert_event(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id
        )

        result = await run_triage_tick(session_factory=factory)

        assert result["triage_events"] == 1
        assert llm.calls == 0
        assert _triage_row(mariadb_znuny_url, ids["ticket_id"]) is None
        async with factory() as session:
            stored = await get_setting(session, KEY_AI_TRIAGE_WATERMARK)
        assert int(stored or 0) >= event_id
    finally:
        async with factory() as session:
            await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 2)


async def test_parallel_operation_mode_blocks_triage(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=3, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]))
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids)
            await set_operation_mode(session, OPERATION_MODE_PARALLEL)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        assert llm.calls == 0
        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["source_queue_id"]
    finally:
        async with factory() as session:
            await set_operation_mode(session, OPERATION_MODE_TIQORA_PRIMARY)
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 3)


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


async def test_high_confidence_moves_ticket_and_writes_marked_note(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=4, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url,
            ticket_id=ids["ticket_id"],
            sender_type="customer",
            body="Mein Router blinkt rot, kein Internet.",
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        result = await run_triage_tick(session_factory=factory)

        assert result["triage_applied"] == 1
        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["target_queue_id"]

        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None
        assert row["status"] == TRIAGE_STATUS_APPLIED
        assert row["queue_applied"] == 1
        assert row["suggested_queue_id"] == ids["target_queue_id"]

        sync = create_engine(mariadb_znuny_url)
        with sync.begin() as conn:
            moves = conn.execute(
                text(
                    "SELECT th.create_by FROM ticket_history th"
                    " JOIN ticket_history_type tht ON tht.id = th.history_type_id"
                    " WHERE th.ticket_id = :tid AND tht.name = 'Move'"
                ),
                {"tid": ids["ticket_id"]},
            ).fetchall()
            note = (
                conn.execute(
                    text(
                        "SELECT m.article_id, m.a_body FROM article_data_mime m"
                        " JOIN article a ON a.id = m.article_id"
                        " WHERE a.ticket_id = :tid AND m.a_subject = 'KI-Triage'"
                    ),
                    {"tid": ids["ticket_id"]},
                )
                .mappings()
                .first()
            )
            origin = conn.execute(
                text("SELECT 1 FROM tiqora_ai_article_origin WHERE article_id = :aid"),
                {"aid": note["article_id"] if note else 0},
            ).first()
            outbox_move = conn.execute(
                text(
                    "SELECT 1 FROM tiqora_event_outbox"
                    " WHERE ticket_id = :tid AND event_type = 'TicketQueueUpdate'"
                ),
                {"tid": ids["ticket_id"]},
            ).first()
        sync.dispose()

        assert len(moves) == 1
        assert int(moves[0][0]) == ids["agent_id"]
        assert note is not None
        assert outbox_move is not None
        # Without the origin marker the reply agent reads its own triage
        # rationale back as ticket context on the next turn.
        assert origin is not None
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 4)


async def test_mid_confidence_only_suggests(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=5, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=60)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80, suggest_threshold=50)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        result = await run_triage_tick(session_factory=factory)

        assert result["triage_suggested"] == 1
        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["source_queue_id"]
        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None
        assert row["status"] == TRIAGE_STATUS_OPEN
        assert row["queue_applied"] == 0
        assert row["queue_confidence"] == 60
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 5)


async def test_low_confidence_does_nothing(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=6, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=20)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80, suggest_threshold=50)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["source_queue_id"]
        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None
        assert row["status"] == TRIAGE_STATUS_NO_ACTION
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 6)


async def test_triage_does_not_set_reply_loop_guard(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """last_customer_article_id belongs to the reply path.

    Setting it here would silently suppress the auto-reply for the very
    article that triggered the move — unless the destination queue asked for
    that via triage_delay_reply (covered by the next test).
    """
    ids = _seed(mariadb_znuny_url, ns=7, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80, delay_reply=False)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        sync = create_engine(mariadb_znuny_url)
        with sync.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT last_customer_article_id FROM tiqora_ai_ticket_state"
                    " WHERE ticket_id = :tid"
                ),
                {"tid": ids["ticket_id"]},
            ).first()
        sync.dispose()
        assert state is None or state[0] is None
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 7)


async def test_destination_delay_reply_parks_the_reply(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=8, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80, delay_reply=True)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        sync = create_engine(mariadb_znuny_url)
        with sync.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT last_customer_article_id FROM tiqora_ai_ticket_state"
                    " WHERE ticket_id = :tid"
                ),
                {"tid": ids["ticket_id"]},
            ).first()
        sync.dispose()
        assert state is not None
        assert int(state[0]) == article_id
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 8)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario",
    ["not_first_article", "auto_generated", "agent_sender", "already_moved", "locked", "too_old"],
)
async def test_guards_prevent_triage(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    ns = {
        "not_first_article": 10,
        "auto_generated": 11,
        "agent_sender": 12,
        "already_moved": 13,
        "locked": 14,
        "too_old": 15,
    }[scenario]
    created = NOW - timedelta(days=5) if scenario == "too_old" else datetime.now()
    ids = _seed(mariadb_znuny_url, ns=ns, created=created)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80)
        await _reset_watermark(factory, mariadb_znuny_url)

        sender = "agent" if scenario == "agent_sender" else "customer"
        if scenario == "not_first_article":
            _add_article(
                mariadb_znuny_url,
                ticket_id=ids["ticket_id"],
                sender_type="customer",
                body="erste nachricht",
            )
        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type=sender, body="hilfe"
        )

        sync = create_engine(mariadb_znuny_url)
        with sync.begin() as conn:
            if scenario == "already_moved":
                htype = conn.execute(
                    text("SELECT id FROM ticket_history_type WHERE name = 'Move' LIMIT 1")
                ).scalar()
                conn.execute(
                    text(
                        "INSERT INTO ticket_history (name, history_type_id, ticket_id,"
                        " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                        " create_time, create_by, change_time, change_by)"
                        " VALUES ('%%Moved%%', :ht, :tid, NULL, 1, :qid, 1, 3, 4,"
                        " :t, 1, :t, 1)"
                    ),
                    {"ht": htype, "tid": ids["ticket_id"], "qid": ids["source_queue_id"], "t": NOW},
                )
            if scenario == "locked":
                conn.execute(
                    text("UPDATE ticket SET ticket_lock_id = 2 WHERE id = :tid"),
                    {"tid": ids["ticket_id"]},
                )
        sync.dispose()

        _insert_event(
            mariadb_znuny_url,
            ticket_id=ids["ticket_id"],
            article_id=article_id,
            auto_generated=True if scenario == "auto_generated" else None,
        )

        await run_triage_tick(session_factory=factory)

        assert llm.calls == 0, f"{scenario} should not reach the model"
        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["source_queue_id"]
        assert _triage_row(mariadb_znuny_url, ids["ticket_id"]) is None
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, ns)


async def test_ticket_is_never_triaged_twice(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ping-pong regression.

    Four independent guards stop a second run (first-article, the
    UNIQUE(ticket_id) row, the Move history entry, the age cutoff). Any one
    of them alone would do; this asserts the combination, because a
    refactoring that drops one must not silently start a loop.
    """
    ids = _seed(mariadb_znuny_url, ns=16, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)
        await run_triage_tick(session_factory=factory)
        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["target_queue_id"]
        calls_after_first = llm.calls

        # Replay the very same event, as a watermark reset would.
        await _reset_watermark(factory, mariadb_znuny_url)
        async with factory() as session:
            await set_setting(session, KEY_AI_TRIAGE_WATERMARK, "0")
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        assert llm.calls == calls_after_first, "second pass must not call the model again"
        sync = create_engine(mariadb_znuny_url)
        with sync.begin() as conn:
            rows = conn.execute(
                text("SELECT COUNT(*) FROM tiqora_ai_triage WHERE ticket_id = :tid"),
                {"tid": ids["ticket_id"]},
            ).scalar()
        sync.dispose()
        assert int(rows or 0) == 1
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 16)


# ---------------------------------------------------------------------------
# Real-world fixtures
# ---------------------------------------------------------------------------

TICKET_43105_BODY = """\
Hallo,

seit gestern Abend habe ich in meinem Zimmer keine Internetverbindung mehr.
Das Netzwerkkabel steckt, die Lampe am Router leuchtet rot. Ein Neustart hat
nichts gebracht. Koennt ihr bitte pruefen, ob mein Anschluss gesperrt wurde?

Viele Gruesse
"""

TICKET_2026081310000037_BODY = """\
Guten Tag,

ich wohne im Wohnheim und bekomme seit der Freischaltung keine IP-Adresse.
Die Registrierung auf startup.stw-bonn.de habe ich abgeschlossen.
Was kann ich tun?

Mit freundlichen Gruessen
"""


@pytest.mark.parametrize(
    ("ns", "body", "subject"),
    [
        (17, TICKET_43105_BODY, "Kein Internet - Anschluss gesperrt?"),
        (18, TICKET_2026081310000037_BODY, "Keine IP-Adresse nach Freischaltung"),
    ],
)
async def test_real_world_tickets_route_to_netadmin_queue(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch, ns: int, body: str, subject: str
) -> None:
    """The two tickets that motivated the feature (43105 and
    2026081310000037), both technical and both mis-filed.

    The model is scripted, so this does not test the model's judgement. What
    it does test is that the prompt actually carries the subject and every
    candidate's routing_description — the assertion that breaks when someone
    rewrites the prompt builder.
    """
    ids = _seed(mariadb_znuny_url, ns=ns, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url,
            ticket_id=ids["ticket_id"],
            sender_type="customer",
            body=body,
            subject=subject,
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["target_queue_id"]
        prompt = llm.last_user_message
        assert subject in prompt
        assert NETADMIN_DESC in prompt, "target routing_description missing from the prompt"
        assert queue_key(ids["target_queue_id"]) in prompt
        assert body.splitlines()[2] in prompt
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, ns)


async def test_missing_target_description_falls_back_to_name(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=19, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, target_description=None)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        assert "stw-bn-8619" in llm.last_user_message
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 19)


async def test_unparsable_model_output_records_error_and_changes_nothing(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=20, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value="", garbage=True)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        result = await run_triage_tick(session_factory=factory)

        assert result["triage_errors"] == 1
        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["source_queue_id"]
        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None and row["error"]
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 20)


async def test_json_in_content_is_accepted_when_provider_ignores_tool_choice(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=21, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(
        queue_key_value=queue_key(ids["target_queue_id"]),
        confidence=95,
        content_instead_of_tool=True,
    )
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["target_queue_id"]
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 21)


async def test_hallucinated_queue_key_is_not_acted_on(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed(mariadb_znuny_url, ns=22, created=datetime.now())
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value="stw-bn", confidence=100)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url, ticket_id=ids["ticket_id"], sender_type="customer", body="hilfe"
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        assert _ticket_queue(mariadb_znuny_url, ids["ticket_id"]) == ids["source_queue_id"]
        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None
        assert row["suggested_queue_id"] is None
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, 22)


# ---------------------------------------------------------------------------
# Customer correction (forwarded mail)
# ---------------------------------------------------------------------------

FORWARDED_BODY_TEMPLATE = """\
Hallo NetAdmin,

der Bewohner hat sich bei mir gemeldet, ich leite das mal weiter.

Viele Gruesse
Hausmeister

-----Urspruengliche Nachricht-----
Von: Sven Gras <{address}>
Gesendet: Donnerstag, 19. Juni 2026 09:12
An: hausmeister@example.org
Betreff: Kein Internet

Mein Anschluss funktioniert seit gestern nicht mehr.
"""


def _add_customer_user(sync_url: str, *, login: str, email: str, customer_id: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:login, :email, :cid, 'Sven', 'Gras', 1, :t, 1, :t, 1)"
            ),
            {"login": login, "email": email, "cid": customer_id, "t": NOW},
        )
    engine.dispose()


def _ticket_customer(sync_url: str, ticket_id: int) -> tuple[str | None, str | None]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT customer_id, customer_user_id FROM ticket WHERE id = :tid"),
            {"tid": ticket_id},
        ).first()
    engine.dispose()
    return (row[0], row[1]) if row else (None, None)


async def test_forwarded_mail_sets_the_original_sender_as_customer(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape of prod ticket 2026061910000021: a caretaker forwards a
    student's mail, so the postmaster files the caretaker as the customer."""
    ns = 30
    ids = _seed(mariadb_znuny_url, ns=ns, created=datetime.now())
    address = f"s27tgras86{ns}@uni-bonn.de"
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        _add_customer_user(mariadb_znuny_url, login=address, email=address, customer_id="UNIBONN")
        async with factory() as session:
            await _setup_policies(
                session, ids=ids, auto_threshold=80, customer_fix=True, customer_fix_threshold=90
            )
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url,
            ticket_id=ids["ticket_id"],
            sender_type="customer",
            body=FORWARDED_BODY_TEMPLATE.format(address=address),
            from_address="hausmeister@example.org",
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        customer_id, customer_user_id = _ticket_customer(mariadb_znuny_url, ids["ticket_id"])
        # ticket.customer_user_id stores the LOGIN, not the numeric PK.
        assert customer_user_id == address
        assert customer_id == "UNIBONN"

        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None
        assert row["extracted_email"] == address
        assert row["customer_applied"] == 1
        assert row["customer_source"] == "header_parse"
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, ns)


async def test_ambiguous_email_leaves_the_customer_alone(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """customer_user is unique on login but NOT on email, so one address can
    match two people. Picking one at random is worse than doing nothing."""
    ns = 31
    ids = _seed(mariadb_znuny_url, ns=ns, created=datetime.now())
    address = f"s27tgras86{ns}@uni-bonn.de"
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        _add_customer_user(mariadb_znuny_url, login=address, email=address, customer_id="UNIBONN")
        _add_customer_user(
            mariadb_znuny_url, login=f"twin-{address}", email=address, customer_id="OTHER"
        )
        async with factory() as session:
            await _setup_policies(
                session, ids=ids, auto_threshold=80, customer_fix=True, customer_fix_threshold=90
            )
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url,
            ticket_id=ids["ticket_id"],
            sender_type="customer",
            body=FORWARDED_BODY_TEMPLATE.format(address=address),
            from_address="hausmeister@example.org",
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        _cid, customer_user_id = _ticket_customer(mariadb_znuny_url, ids["ticket_id"])
        assert customer_user_id == f"hausmeister86{ns}@example.org"
        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None
        assert row["customer_applied"] == 0
        assert row["suggested_customer_user_id"] is None
    finally:
        engine_sync = create_engine(mariadb_znuny_url)
        with engine_sync.begin() as conn:
            conn.execute(text("DELETE FROM customer_user WHERE email = :e"), {"e": address})
        engine_sync.dispose()
        await engine.dispose()
        _cleanup(mariadb_znuny_url, ns)


async def test_unknown_forwarded_sender_is_recorded_but_not_applied(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "The real sender is X, and X is not a customer" is still worth
    recording — an agent can act on it."""
    ns = 32
    ids = _seed(mariadb_znuny_url, ns=ns, created=datetime.now())
    address = f"s27tgras86{ns}@uni-bonn.de"
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        async with factory() as session:
            await _setup_policies(
                session, ids=ids, auto_threshold=80, customer_fix=True, customer_fix_threshold=90
            )
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url,
            ticket_id=ids["ticket_id"],
            sender_type="customer",
            body=FORWARDED_BODY_TEMPLATE.format(address=address),
            from_address="hausmeister@example.org",
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        _cid, customer_user_id = _ticket_customer(mariadb_znuny_url, ids["ticket_id"])
        assert customer_user_id == f"hausmeister86{ns}@example.org"
        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None
        assert row["extracted_email"] == address
        assert row["suggested_customer_user_id"] is None
        assert row["customer_applied"] == 0
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, ns)


async def test_customer_fix_disabled_records_but_never_writes(
    mariadb_znuny_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ns = 33
    ids = _seed(mariadb_znuny_url, ns=ns, created=datetime.now())
    address = f"s27tgras86{ns}@uni-bonn.de"
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = TriageLlm(queue_key_value=queue_key(ids["target_queue_id"]), confidence=95)
    _patch_llm(monkeypatch, llm)
    try:
        _add_customer_user(mariadb_znuny_url, login=address, email=address, customer_id="UNIBONN")
        async with factory() as session:
            await _setup_policies(session, ids=ids, auto_threshold=80, customer_fix=False)
        await _reset_watermark(factory, mariadb_znuny_url)

        article_id = _add_article(
            mariadb_znuny_url,
            ticket_id=ids["ticket_id"],
            sender_type="customer",
            body=FORWARDED_BODY_TEMPLATE.format(address=address),
            from_address="hausmeister@example.org",
        )
        _insert_event(mariadb_znuny_url, ticket_id=ids["ticket_id"], article_id=article_id)

        await run_triage_tick(session_factory=factory)

        _cid, customer_user_id = _ticket_customer(mariadb_znuny_url, ids["ticket_id"])
        assert customer_user_id == f"hausmeister86{ns}@example.org"
        row = _triage_row(mariadb_znuny_url, ids["ticket_id"])
        assert row is not None
        assert row["suggested_customer_user_id"] == address
        assert row["customer_applied"] == 0
    finally:
        await engine.dispose()
        _cleanup(mariadb_znuny_url, ns)
