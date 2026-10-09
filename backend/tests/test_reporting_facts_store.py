"""Fact loading/writing and the dirty queue (both dialects).

Seed ids: 47530-47579 (names contain ``rep47``). The session-scoped containers are
shared, so every row this module writes is deleted again.

Layout: group 47530, queues 47531/47532, agent 47533, tickets 47540 (full life),
47541 (no history), 47542 (merged); 47579 is never a ticket.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import (
    TiqoraReportDirtyTicket,
    TiqoraReportTicketEvent,
    TiqoraReportTicketFact,
)
from tiqora.reporting import facts_store
from tiqora.reporting.dirty import clear_dirty, mark_tickets_dirty, take_dirty_ticket_ids
from tiqora.reporting.facts import FACT_VERSION
from tiqora.reporting.facts_store import (
    RebuildResult,
    fact_write_lock,
    rebuild_and_commit,
    rebuild_ticket_facts,
)

pytestmark = pytest.mark.db

LO, HI = 47530, 47579
GROUP, Q1, Q2, AGENT = 47530, 47531, 47532, 47533
T_FULL, T_BARE, T_MERGED, T_GONE = 47540, 47541, 47542, 47579
DF_ID = 47530  # only used when the Znuny seed has no ProcessManagementProcessID field
# Monday: inside the default working hours.
T0 = datetime(2024, 6, 3, 8, 0, 0)
BUILT = datetime(2024, 6, 10, 12, 0, 0)

_URLS: set[str] = set()
_BAND = {"lo": LO, "hi": HI}

_CLEANUP = (
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_ai_triage WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_ai_article_origin WHERE article_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_ai_draft WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_ai_ticket_state WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM dynamic_field_value WHERE object_id BETWEEN :lo AND :hi",
    "DELETE FROM dynamic_field WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM time_accounting WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM queue WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _clean(sync_url: str) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt), _BAND)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _clean(url)
    _URLS.clear()


def _one(conn: Any, sql: str, **params: Any) -> int:
    value = conn.execute(text(sql), params).scalar_one()
    return int(value)


def _seed(sync_url: str) -> dict[str, int]:
    """Fresh seed; returns the ids looked up by name from the Znuny seed data."""
    _URLS.add(sync_url)
    _clean(sync_url)
    engine = create_engine(sync_url)
    ids: dict[str, int] = {}
    with engine.begin() as conn:
        for name in ("NewTicket", "Move", "StateUpdate", "Merged"):
            ids[f"h_{name}"] = _one(
                conn, "SELECT id FROM ticket_history_type WHERE name = :n", n=name
            )
        for name in ("customer", "agent"):
            ids[f"s_{name}"] = _one(
                conn, "SELECT id FROM article_sender_type WHERE name = :n", n=name
            )
        ids["c_email"] = _one(conn, "SELECT id FROM communication_channel WHERE name = 'Email'")
        for key, name in (
            ("new", "new"),
            ("closed", "closed successful"),
            ("merged", "merged"),
        ):
            ids[f"st_{key}"] = _one(conn, "SELECT id FROM ticket_state WHERE name = :n", n=name)

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47.agent', 'x', 'Rita', 'Report', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": T0},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'rep47-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP, "t": T0},
        )
        for qid, name in ((Q1, "rep47-q1"), (Q2, "rep47-q2")):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " first_response_time, solution_time,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, 60, 480, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": name, "g": GROUP, "t": T0},
            )

        def ticket(tid: int, queue: int, state: int, tn: str) -> None:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'rep47 ticket', :q, 1, 1, :u, 1, 3, :s,"
                    " 'rep47-cust', 'rep47.cust@example.org', 0, 0, 0, 0, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": tn, "q": queue, "s": state, "u": AGENT, "t": T0},
            )

        ticket(T_FULL, Q2, ids["st_closed"], "2024060347540")
        ticket(T_BARE, Q1, ids["st_new"], "2024060347541")
        ticket(T_MERGED, Q1, ids["st_merged"], "2024060347542")

        def article(aid: int, tid: int, sender: str, visible: int, at: datetime) -> None:
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :s, :c, :v, :t, :by, :t, :by)"
                ),
                {
                    "id": aid,
                    "tid": tid,
                    "s": ids[f"s_{sender}"],
                    "c": ids["c_email"],
                    "v": visible,
                    "t": at,
                    "by": AGENT if sender == "agent" else 1,
                },
            )

        article(47540, T_FULL, "customer", 1, T0)
        article(47541, T_FULL, "agent", 1, T0 + timedelta(minutes=30))

        def history(
            hid: int,
            tid: int,
            htype: str,
            name: str,
            queue: int,
            state: int,
            at: datetime,
            article_id: int | None = None,
        ) -> None:
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, :a, 1, :q, :o, 3, :s, :t, :o, :t, :o)"
                ),
                {
                    "id": hid,
                    "n": name,
                    "ht": ids[f"h_{htype}"],
                    "tid": tid,
                    "a": article_id,
                    "q": queue,
                    "o": AGENT,
                    "s": state,
                    "t": at,
                },
            )

        history(
            47540,
            T_FULL,
            "NewTicket",
            "%%2024060347540%%rep47-q1%%3 normal%%new%%47540",
            Q1,
            ids["st_new"],
            T0,
            47540,
        )
        history(
            47541,
            T_FULL,
            "Move",
            f"%%rep47-q2%%{Q2}%%rep47-q1%%{Q1}",
            Q2,
            ids["st_new"],
            T0 + timedelta(minutes=10),
        )
        history(
            47542,
            T_FULL,
            "StateUpdate",
            "%%new%%closed successful",
            Q2,
            ids["st_closed"],
            T0 + timedelta(hours=2),
        )
        history(
            47543,
            T_MERGED,
            "NewTicket",
            "%%2024060347542%%rep47-q1%%3 normal%%new%%47542",
            Q1,
            ids["st_new"],
            T0,
        )
        history(
            47544,
            T_MERGED,
            "Merged",
            f"%%{T_MERGED}%%{T_FULL}%%47540%%47542",
            Q1,
            ids["st_merged"],
            T0 + timedelta(hours=1),
        )

        for tuid, unit in ((47540, "0.50"), (47541, "1.00")):
            conn.execute(
                text(
                    "INSERT INTO time_accounting (id, ticket_id, article_id, time_unit,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, NULL, :u, :t, 1, :t, 1)"
                ),
                {"id": tuid, "tid": T_FULL, "u": unit, "t": T0},
            )

        # R-DF-SEED: the Znuny seed ships the field; create it only if it is missing.
        field = conn.execute(
            text("SELECT id FROM dynamic_field WHERE name = 'ProcessManagementProcessID'")
        ).scalar_one_or_none()
        if field is None:
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 1, 'ProcessManagementProcessID', 'Process', 1, 'ProcessID',"
                    " 'Ticket', '---', 1, :t, 1, :t, 1)"
                ),
                {"id": DF_ID, "t": T0},
            )
            field = DF_ID
        conn.execute(
            text(
                "INSERT INTO dynamic_field_value (field_id, object_id, value_text)"
                " VALUES (:f, :o, 'Process-1')"
            ),
            {"f": int(field), "o": T_FULL},
        )
    engine.dispose()
    return ids


def _seed_ai(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tiqora_ai_triage (id, ticket_id, article_id, source_queue_id,"
                " status, queue_applied, customer_applied, create_time)"
                " VALUES (47540, :t, 47540, :q, 'applied', :yes, :no, :now)"
            ),
            {"t": T_FULL, "q": Q1, "yes": True, "no": False, "now": T0},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_ai_article_origin (article_id, source, queue_id, created)"
                " VALUES (47541, 'auto', :q, :now)"
            ),
            {"q": Q2, "now": T0},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_ai_ticket_state (ticket_id, ai_escalated_at) VALUES (:t, :now)"
            ),
            {"t": T_FULL, "now": T0},
        )
    engine.dispose()


def _factory(sync_url: str) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_to_async_url(sync_url))
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _fact(s: AsyncSession, ticket_id: int) -> TiqoraReportTicketFact | None:
    return await s.get(TiqoraReportTicketFact, ticket_id)


async def _kinds(s: AsyncSession, ticket_id: int) -> list[str]:
    rows = (
        await s.execute(
            select(TiqoraReportTicketEvent.kind)
            .where(TiqoraReportTicketEvent.ticket_id == ticket_id)
            .order_by(TiqoraReportTicketEvent.occurred_at, TiqoraReportTicketEvent.id)
        )
    ).scalars()
    return list(rows)


URLS = ["mariadb_znuny_url", "postgres_znuny_url"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rebuild_builds_facts_and_events(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine, factory = _factory(sync_url)
    try:
        async with factory() as s:
            res = await rebuild_ticket_facts(s, [T_FULL, T_BARE, T_MERGED], now=BUILT)
            await s.commit()
        assert res == RebuildResult(built=3, deleted=0)
        async with factory() as s:
            full = await _fact(s, T_FULL)
            assert full is not None
            assert full.queue_id == Q2 and full.queue_id_at_create == Q1
            assert full.state_type == "closed" and full.closed_at == T0 + timedelta(hours=2)
            assert full.accounted_time == Decimal("1.50")
            assert full.first_response_at == T0 + timedelta(minutes=30)
            assert full.first_response_minutes == 30
            assert full.channel == "email"
            assert full.fact_version == FACT_VERSION and full.built_at == BUILT
            assert full.process_entity_id == "Process-1"  # R-DF-SEED
            assert (full.customer_id, full.owner_id) == ("rep47-cust", AGENT)
            assert full.queue_move_count == 1 and full.close_count == 1
            assert full.article_in_count == 1 and full.article_out_count == 1

            bare = await _fact(s, T_BARE)
            assert bare is not None
            assert bare.created_at == T0
            assert bare.process_entity_id is None and bare.accounted_time == Decimal("0.00")
            assert bare.channel == "email" and bare.closed_at is None
            assert await _kinds(s, T_BARE) == ["create"]

            merged = await _fact(s, T_MERGED)
            assert merged is not None and merged.merged is True
            assert full.merged is False

            kinds = set(await _kinds(s, T_FULL))
            assert {"create", "queue", "state", "close", "customer_message", "reply"} <= kinds
            assert "merge" in await _kinds(s, T_MERGED)
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rebuild_is_idempotent(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine, factory = _factory(sync_url)
    cols = [c.name for c in TiqoraReportTicketFact.__table__.columns if c.name != "built_at"]
    ev_cols = [c.name for c in TiqoraReportTicketEvent.__table__.columns if c.name != "id"]
    try:

        async def snapshot() -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], int]:
            async with factory() as s:
                facts = (
                    (await s.execute(select(TiqoraReportTicketFact).order_by("ticket_id")))
                    .scalars()
                    .all()
                )
                facts = [f for f in facts if LO <= f.ticket_id <= HI]
                events = (
                    (
                        await s.execute(
                            select(TiqoraReportTicketEvent)
                            .where(TiqoraReportTicketEvent.ticket_id.between(LO, HI))
                            .order_by(
                                TiqoraReportTicketEvent.ticket_id,
                                TiqoraReportTicketEvent.occurred_at,
                                TiqoraReportTicketEvent.kind,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                return (
                    [tuple(getattr(f, c) for c in cols) for f in facts],
                    [tuple(getattr(e, c) for c in ev_cols) for e in events],
                    len(facts),
                )

        for stamp in (BUILT, BUILT + timedelta(days=1)):
            async with factory() as s:
                await rebuild_ticket_facts(s, [T_FULL, T_BARE, T_MERGED], now=stamp)
                await s.commit()
            if stamp == BUILT:
                first = await snapshot()
        second = await snapshot()
        assert first[2] == second[2] == 3
        assert len(first[1]) == len(second[1]) > 3
        assert first == second
        async with factory() as s:
            assert (await _fact(s, T_FULL)).built_at == BUILT + timedelta(days=1)  # type: ignore[union-attr]
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rebuild_deletes_facts_of_missing_ticket(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine, factory = _factory(sync_url)
    try:
        async with factory() as s:
            s.add(
                TiqoraReportTicketFact(
                    ticket_id=T_GONE, tn="x", created_at=T0, created_by=1, queue_id=Q1,
                    queue_id_at_create=Q1, state_id=1, state_type="new", priority_id=3,
                    owner_id=1, channel="email", accounted_time=Decimal("0"),
                    last_event_at=T0, fact_version=1, built_at=T0,
                )
            )  # fmt: skip
            s.add(
                TiqoraReportTicketEvent(
                    ticket_id=T_GONE, kind="create", occurred_at=T0, queue_id=Q1
                )
            )
            await s.commit()
        async with factory() as s:
            res = await rebuild_ticket_facts(s, [T_GONE])
            await s.commit()
        assert res == RebuildResult(built=0, deleted=1)
        async with factory() as s:
            assert await _fact(s, T_GONE) is None
            assert await _kinds(s, T_GONE) == []
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rebuild_spans_chunks(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    monkeypatch.setattr(facts_store, "FACT_CHUNK_SIZE", 1)
    engine, factory = _factory(sync_url)
    try:
        res = await rebuild_and_commit(factory, [T_MERGED, T_FULL, T_BARE, T_GONE])
        assert res == RebuildResult(built=3, deleted=0)
        async with factory() as s:
            count = (
                await s.execute(
                    select(func.count())
                    .select_from(TiqoraReportTicketFact)
                    .where(TiqoraReportTicketFact.ticket_id.between(LO, HI))
                )
            ).scalar_one()
            assert count == 3
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_ai_flags(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    _seed_ai(sync_url)
    engine, factory = _factory(sync_url)
    try:
        async with factory() as s:
            await rebuild_ticket_facts(s, [T_FULL, T_BARE], now=BUILT)
            await s.commit()
        async with factory() as s:
            full = await _fact(s, T_FULL)
            assert full is not None
            assert full.ai_triaged is True
            assert full.ai_triage_queue_applied is True
            assert full.ai_auto_replied is True
            assert full.ai_handed_off is True
            assert full.ai_draft_used is False
            bare = await _fact(s, T_BARE)
            assert bare is not None
            assert (
                bare.ai_triaged,
                bare.ai_triage_queue_applied,
                bare.ai_auto_replied,
                bare.ai_draft_used,
                bare.ai_handed_off,
            ) == (False, False, False, False, False)
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_draft_used_counts_manual_accept_origin_not_auto(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """R-AIFLAG: an article origin "manual_accept" (human accepted a draft) is a used
    draft and not an auto reply; "auto" is the reverse."""
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for article_id, source in ((47541, "manual_accept"), (47540, "auto")):
            conn.execute(
                text(
                    "INSERT INTO tiqora_ai_article_origin (article_id, source, queue_id, created)"
                    " VALUES (:a, :s, :q, :now)"
                ),
                {"a": article_id, "s": source, "q": Q2, "now": T0},
            )
    engine.dispose()
    engine, factory = _factory(sync_url)
    try:
        async with factory() as s:
            await rebuild_ticket_facts(s, [T_FULL], now=BUILT)
            await s.commit()
        async with factory() as s:
            full = await _fact(s, T_FULL)
            assert full is not None
            assert full.ai_draft_used is True and full.ai_auto_replied is True
        with create_engine(sync_url).begin() as conn:
            conn.execute(text("DELETE FROM tiqora_ai_article_origin WHERE article_id = 47540"))
        async with factory() as s:
            await rebuild_ticket_facts(s, [T_FULL], now=BUILT)
            await s.commit()
        async with factory() as s:
            full = await _fact(s, T_FULL)
            assert full is not None
            assert full.ai_draft_used is True and full.ai_auto_replied is False
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_dirty_queue_round_trip(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _URLS.add(sync_url)
    _clean(sync_url)
    engine, factory = _factory(sync_url)
    a, b = 47530, 47531
    try:
        async with factory() as s:
            await mark_tickets_dirty(s, [a, b], now=T0)
            await mark_tickets_dirty(s, [a], now=T0 + timedelta(seconds=10))  # no IntegrityError
            await s.commit()
        async with factory() as s:
            ids, taken_at = await take_dirty_ticket_ids(s, 10)
            # R-DIRTY: the re-marked ticket is newest -> last
            assert ids == [b, a]
            assert taken_at == T0 + timedelta(seconds=10)
            assert (await take_dirty_ticket_ids(s, 1))[0] == [b]  # limit, oldest first
            # does not delete
            rows = await s.execute(
                select(func.count())
                .select_from(TiqoraReportDirtyTicket)
                .where(TiqoraReportDirtyTicket.ticket_id.between(LO, HI))
            )
            assert rows.scalar_one() == 2

            # a mark made while the rebuild ran survives the clear
            await mark_tickets_dirty(s, [a], now=T0 + timedelta(seconds=20))
            await clear_dirty(s, ids, taken_at)
            await s.commit()
        async with factory() as s:
            ids_after, _ = await take_dirty_ticket_ids(s, 10)
            assert ids_after == [a]
            await clear_dirty(s, [a], T0 + timedelta(seconds=20))
            await s.commit()
        async with factory() as s:
            empty_ids, _ = await take_dirty_ticket_ids(s, 10)
            assert [i for i in empty_ids if LO <= i <= HI] == []
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_remark_in_same_second_survives_clear(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """A re-mark made in the same wall second as the take must not be cleared
    (MariaDB ``queued_at`` is DATETIME(6), so the microsecond difference is stored)."""
    sync_url = request.getfixturevalue(url_fixture)
    _URLS.add(sync_url)
    _clean(sync_url)
    engine, factory = _factory(sync_url)
    a = 47530
    try:
        async with factory() as s:
            await mark_tickets_dirty(s, [a], now=T0)
            await s.commit()
        async with factory() as s:
            ids, taken_at = await take_dirty_ticket_ids(s, 10)
            assert ids == [a] and taken_at == T0
            await mark_tickets_dirty(s, [a], now=T0 + timedelta(microseconds=5))
            await clear_dirty(s, ids, taken_at)
            await s.commit()
        async with factory() as s:
            ids_after, at_after = await take_dirty_ticket_ids(s, 10)
            assert ids_after == [a]
            assert at_after == T0 + timedelta(microseconds=5)
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_dirty_queue_empty_inputs(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _URLS.add(sync_url)
    _clean(sync_url)
    engine, factory = _factory(sync_url)
    try:
        async with factory() as s:
            await mark_tickets_dirty(s, [])
            await clear_dirty(s, [], T0)
            ids, taken_at = await take_dirty_ticket_ids(s, 5)
            assert [i for i in ids if LO <= i <= HI] == []
            assert isinstance(taken_at, datetime)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_rebuild_and_commit_retries_once_on_integrity_error(
    url_fixture: str, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_url = request.getfixturevalue(url_fixture)
    _seed(sync_url)
    real = facts_store.rebuild_ticket_facts
    calls = {"n": 0}

    async def flaky(session: AsyncSession, ticket_ids: Any, **kw: Any) -> RebuildResult:
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("duplicate"))
        return await real(session, ticket_ids, **kw)

    monkeypatch.setattr(facts_store, "rebuild_ticket_facts", flaky)
    engine, factory = _factory(sync_url)
    try:
        res = await rebuild_and_commit(factory, [T_BARE], now=BUILT)
        assert res.built == 1 and calls["n"] == 2
        async with factory() as s:
            assert await _fact(s, T_BARE) is not None

        calls["n"] = -100  # never raises again; now a persistent failure

        async def always(session: AsyncSession, ticket_ids: Any, **kw: Any) -> RebuildResult:
            calls["n"] += 1
            raise IntegrityError("INSERT", {}, Exception("duplicate"))

        monkeypatch.setattr(facts_store, "rebuild_ticket_facts", always)
        with pytest.raises(IntegrityError):
            await rebuild_and_commit(factory, [T_BARE], attempts=2)
        assert calls["n"] == -98
    finally:
        await engine.dispose()
    _clean(sync_url)


@pytest.mark.asyncio
async def test_fact_write_lock_is_per_loop() -> None:
    lock = fact_write_lock()
    assert fact_write_lock() is lock

    async def other_loop() -> asyncio.Lock:
        inner = fact_write_lock()
        async with inner:  # usable without "bound to a different event loop"
            assert inner.locked()
        return inner

    async with lock:
        other = await asyncio.to_thread(asyncio.run, other_loop())
    assert other is not lock
    assert fact_write_lock() is lock


T_MAIN, T_SRC = 47545, 47546


def _seed_merge(sync_url: str, ids: dict[str, int]) -> None:
    """T_SRC (queue Q2, owner 1, closed with a note) is merged into T_MAIN (Q1, new).

    The merge runs the exact statements of Znuny ``TicketMerge`` / Tiqora
    ``merge_tickets``: articles and article-linked history rows move to the main
    ticket, ``change_time = current_timestamp`` marks the moved history rows.
    """
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        h_note = _one(conn, "SELECT id FROM ticket_history_type WHERE name = 'AddNote'")
        for tid, queue, state, owner, tn in (
            (T_MAIN, Q1, ids["st_new"], AGENT, "2024060347545"),
            (T_SRC, Q2, ids["st_merged"], 1, "2024060347546"),
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'rep47 merge', :q, 1, 1, :u, 1, 3, :s,"
                    " 'rep47-cust', 'rep47.cust@example.org', 0, 0, 0, 0, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": tn, "q": queue, "s": state, "u": owner, "t": T0},
            )
        for aid, sender in ((47545, "customer"), (47546, "agent")):
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :s, :c, 0, :t, 1, :t, 1)"
                ),
                {
                    "id": aid,
                    "tid": T_SRC,
                    "s": ids[f"s_{sender}"],
                    "c": ids["c_email"],
                    "t": T0 - timedelta(minutes=30),
                },
            )
        rows = (
            # (id, ticket, type, article, queue, owner, state, minutes after T0)
            (47545, T_SRC, ids["h_NewTicket"], 47545, Q2, 1, ids["st_new"], -30),
            (47546, T_MAIN, ids["h_NewTicket"], None, Q1, AGENT, ids["st_new"], 0),
            # the merged ticket's close WITH its note: StateUpdate rows can carry an article
            (47547, T_SRC, h_note, 47546, Q2, 1, ids["st_closed"], 10),
            (47548, T_SRC, ids["h_StateUpdate"], 47546, Q2, 1, ids["st_closed"], 10),
        )
        for hid, tid, htype, aid, queue, owner, state, minutes in rows:
            at = T0 + timedelta(minutes=minutes)
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, '%%', :ht, :tid, :a, 1, :q, :o, 3, :s, :t, 1, :t, 1)"
                ),
                {"id": hid, "ht": htype, "tid": tid, "a": aid, "q": queue, "o": owner,
                 "s": state, "t": at},
            )  # fmt: skip
        # the merge (Ticket.pm TicketMerge / ticket_write_service.merge_tickets)
        conn.execute(
            text("UPDATE article SET ticket_id = :main WHERE ticket_id = :merge"),
            {"main": T_MAIN, "merge": T_SRC},
        )
        conn.execute(
            text(
                "UPDATE ticket_history SET ticket_id = :main, change_time = current_timestamp,"
                " change_by = 1 WHERE ticket_id = :merge AND article_id IS NOT NULL"
            ),
            {"main": T_MAIN, "merge": T_SRC},
        )
        merged_name = f"%%2024060347546%%{T_SRC}%%2024060347545%%{T_MAIN}"
        for hid, tid, queue, owner, state in ((47549, T_MAIN, Q1, AGENT, ids["st_new"]),):
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, :o, 3, :s, :t, 1, :t, 1)"
                ),
                {"id": hid, "n": merged_name, "ht": ids["h_Merged"], "tid": tid, "q": queue,
                 "o": owner, "s": state, "t": T0 + timedelta(minutes=20)},
            )  # fmt: skip
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", URLS)
async def test_merge_moved_history_fakes_no_events_on_main_ticket(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """C1: the main ticket keeps its own queue/state/owner history after a merge."""
    sync_url = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    _seed_merge(sync_url, ids)
    engine, factory = _factory(sync_url)
    try:
        async with factory() as s:
            await rebuild_ticket_facts(s, [T_MAIN], now=BUILT)
            await s.commit()
        async with factory() as s:
            main = await _fact(s, T_MAIN)
            assert main is not None
            assert (main.queue_id_at_create, main.state_type) == (Q1, "new")
            assert (main.close_count, main.reopen_count, main.closed_at) == (0, 0, None)
            assert (main.queue_move_count, main.owner_change_count) == (0, 0)
            assert main.queue_pingpong is False
            # the moved articles count for the main ticket (Znuny parity)
            assert (main.article_in_count, main.note_count) == (1, 1)
            kinds = await _kinds(s, T_MAIN)
            assert sorted(kinds) == ["create", "customer_message", "merge", "note"]
            queues = set(
                (
                    await s.execute(
                        select(TiqoraReportTicketEvent.queue_id).where(
                            TiqoraReportTicketEvent.ticket_id == T_MAIN
                        )
                    )
                ).scalars()
            )
            assert queues == {Q1}  # moved articles are attributed to the main ticket's queue
    finally:
        await engine.dispose()
    _clean(sync_url)
