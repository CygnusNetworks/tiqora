"""DB integration tests for read-only REST path (both dialects)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.queue_service import QueueService
from tiqora.domain.ticket_service import (
    TicketAccessDenied,
    TicketNotFound,
    TicketService,
)
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    if sync_url.startswith("mysql://"):
        return sync_url.replace("mysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed_tickets(sync_url: str) -> dict[str, Any]:
    """Seed users, groups, queues, tickets, articles, attachments, dynamic fields."""
    engine = create_engine(sync_url)
    ids: dict[str, Any] = {}
    pw = hash_password("secret")

    with engine.begin() as conn:
        # tiqora tables for this test DB
        TiqoraBase.metadata.create_all(conn)

        for uid, login in ((200, "reader.alpha"), (201, "reader.none")):
            conn.execute(
                text(
                    """
                    INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                      create_time, create_by, change_time, change_by)
                    VALUES (:id, :login, :pw, 'Read', 'Er', 1, :t, 1, :t, 1)
                    """
                ),
                {"id": uid, "login": login, "pw": pw, "t": NOW},
            )

        conn.execute(
            text(
                """
                INSERT INTO permission_groups
                (id, name, valid_id, create_time, create_by, change_time, change_by)
                VALUES (20, 'read-alpha', 1, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO group_user
                (user_id, group_id, permission_key,
                 create_time, create_by, change_time, change_by)
                VALUES (200, 20, 'ro', :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        # reader.none has no groups

        conn.execute(
            text(
                """
                INSERT INTO queue (
                    id, name, group_id, system_address_id, salutation_id, signature_id,
                    follow_up_id, follow_up_lock, valid_id,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    200, 'ReadQueue', 20, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )

        # Ticket: state open=4, priority normal=3, lock unlock=1 (Znuny initial_insert)
        conn.execute(
            text(
                """
                INSERT INTO ticket (
                    id, tn, title, queue_id, ticket_lock_id, type_id,
                    user_id, responsible_user_id, ticket_priority_id, ticket_state_id,
                    customer_id, customer_user_id,
                    timeout, until_time, escalation_time, escalation_update_time,
                    escalation_response_time, escalation_solution_time, archive_flag,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    500, '20240601000001', 'Test ticket', 200, 1, 1,
                    200, 1, 3, 4,
                    'CUST1', 'alice@example.com',
                    0, 0, 0, 0, 0, 0, 0,
                    :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )

        # Second ticket, freshly arrived from a customer mail: state 'new'
        # (id=1, type_id=1 "new" — see initial_insert.*.sql). Regression
        # fixture for the "new tickets invisible under the default Offen
        # filter" bug (Ticket::ViewableStateType must include "new").
        conn.execute(
            text(
                """
                INSERT INTO ticket (
                    id, tn, title, queue_id, ticket_lock_id, type_id,
                    user_id, responsible_user_id, ticket_priority_id, ticket_state_id,
                    customer_id, customer_user_id,
                    timeout, until_time, escalation_time, escalation_update_time,
                    escalation_response_time, escalation_solution_time, archive_flag,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    501, '20240601000002', 'Fresh mail ticket', 200, 1, 1,
                    1, 1, 3, 1,
                    'CUST1', 'alice@example.com',
                    0, 0, 0, 0, 0, 0, 0,
                    :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )

        # Dynamic field + value (unique name — avoid clashing with Znuny seed fields)
        conn.execute(
            text(
                """
                INSERT INTO dynamic_field (
                    id, internal_field, name, label, field_order, field_type, object_type,
                    valid_id, create_time, create_by, change_time, change_by
                ) VALUES (
                    9001, 0, 'TiqoraTestField', 'Test Field', 100, 'Text', 'Ticket',
                    1, :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO dynamic_field_value (id, field_id, object_id, value_text)
                VALUES (9001, 9001, 500, 'PROC-1')
                """
            ),
        )

        # Article + mime + attachment
        conn.execute(
            text(
                """
                INSERT INTO article (
                    id, ticket_id, article_sender_type_id, communication_channel_id,
                    is_visible_for_customer, search_index_needs_rebuild,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    600, 500, 3, 1, 1, 0, :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO article_data_mime (
                    id, article_id, a_from, a_to, a_subject, a_content_type, a_body,
                    incoming_time, create_time, create_by, change_time, change_by
                ) VALUES (
                    600, 600, 'alice@example.com', 'support@example.com',
                    'Re: Test', 'text/plain; charset=utf-8', 'Hello body',
                    1717243200, :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )
        # binary content — bind as LargeBinary so both MySQL BLOB and PG bytea work
        from sqlalchemy import LargeBinary, bindparam

        conn.execute(
            text(
                """
                INSERT INTO article_data_mime_attachment (
                    id, article_id, filename, content_size, content_type, content_id,
                    content_alternative, disposition, content,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    700, 600, 'note.txt', '5', 'text/plain', 'cid-note@local',
                    '', 'attachment', :content,
                    :t, 1, :t, 1
                )
                """
            ).bindparams(bindparam("content", type_=LargeBinary())),
            {"content": b"hello", "t": NOW},
        )

        # History
        conn.execute(
            text(
                """
                INSERT INTO ticket_history (
                    id, name, history_type_id, ticket_id, type_id, queue_id,
                    owner_id, priority_id, state_id,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    800, '%%Test ticket%%', 1, 500, 1, 200,
                    200, 3, 4, :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )

        # Customer
        conn.execute(
            text(
                """
                INSERT INTO customer_company (
                    customer_id, name, valid_id,
                    create_time, create_by, change_time, change_by
                ) VALUES ('CUST1', 'Acme Corp', 1, :t, 1, :t, 1)
                """
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                """
                INSERT INTO customer_user (
                    id, login, email, customer_id, first_name, last_name, valid_id,
                    create_time, create_by, change_time, change_by
                ) VALUES (
                    50, 'alice@example.com', 'alice@example.com', 'CUST1',
                    'Alice', 'Example', 1, :t, 1, :t, 1
                )
                """
            ),
            {"t": NOW},
        )

    engine.dispose()
    ids.update(
        {
            "reader": 200,
            "no_access": 201,
            "queue": 200,
            "ticket": 500,
            "new_ticket": 501,
            "article": 600,
            "attachment": 700,
        }
    )
    return ids


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_queue_ticket_detail_attachment_permissions(
    url_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_tickets(sync_url)
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        qs = QueueService(session)
        trees = await qs.list_queues(ids["reader"])
        assert any(n.id == ids["queue"] for n in trees)
        # open count: ticket in open state + the "new" ticket (viewable)
        node = next(n for n in trees if n.id == ids["queue"])
        assert node.counts.open >= 2
        # per-queue new-vs-open split (issue: queue tree showed one lumped
        # total) — exactly the one "new" ticket seeded above.
        assert node.counts.new == 1
        assert node.counts.total == node.counts.open

        empty = await qs.list_queues(ids["no_access"])
        assert empty == []

        ts = TicketService(session)
        listed = await ts.list_tickets(ids["reader"], queue_id=ids["queue"])
        assert listed.total >= 1
        assert listed.items[0].tn == "20240601000001"

        # Default "Offen" view (state_type=open) must include "new" tickets —
        # Znuny's Ticket::ViewableStateType is new+open+pending reminder+
        # pending auto, not a literal match on the state type named "open".
        offen = await ts.list_tickets(ids["reader"], queue_id=ids["queue"], state_type="open")
        assert {i.tn for i in offen.items} == {"20240601000001", "20240601000002"}

        # Dedicated "Neu" tab: only the new-state ticket.
        neu = await ts.list_tickets(ids["reader"], queue_id=ids["queue"], state_type="new")
        assert [i.tn for i in neu.items] == ["20240601000002"]
        assert neu.items[0].state_type == "new"

        listed_none = await ts.list_tickets(ids["no_access"])
        assert listed_none.total == 0

        detail = await ts.get_ticket(ids["reader"], ids["ticket"])
        assert detail.title == "Test ticket"
        assert any(df.name == "TiqoraTestField" for df in detail.dynamic_fields)
        proc = next(df for df in detail.dynamic_fields if df.name == "TiqoraTestField")
        assert proc.values == ["PROC-1"]

        with pytest.raises(TicketAccessDenied):
            await ts.get_ticket(ids["no_access"], ids["ticket"])

        articles = await ts.list_articles(ids["reader"], ids["ticket"])
        assert len(articles) == 1
        assert articles[0].subject == "Re: Test"
        assert articles[0].sender_type == "customer"

        body = await ts.get_article_body(ids["reader"], ids["ticket"], ids["article"])
        assert "Hello body" in body.body

        atts = await ts.list_attachments(ids["reader"], ids["ticket"], ids["article"])
        assert len(atts) == 1
        assert atts[0].filename == "note.txt"

        content = await ts.get_attachment(
            ids["reader"], ids["ticket"], ids["article"], ids["attachment"]
        )
        assert content.content == b"hello"

        by_cid = await ts.get_attachment_by_cid(
            ids["reader"], ids["ticket"], ids["article"], "cid-note@local"
        )
        assert by_cid.content == b"hello"

        history = await ts.list_history(ids["reader"], ids["ticket"])
        assert len(history) >= 1

        with pytest.raises(TicketNotFound):
            await ts.get_ticket(ids["reader"], 999999)

    await engine.dispose()
