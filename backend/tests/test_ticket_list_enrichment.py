"""DB tests for the ticket-list enrichment fields (attachment_count, has_ai_summary).

Seed ids use the 893xx range — disjoint from other DB test files sharing the
session-scoped testcontainer DB (see e.g. ``test_read_api_db.py`` 5/6/7/8/9xx,
``test_ai_summary.py`` 97xx).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import LargeBinary, bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_service import TicketService

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

AGENT_ID = 89300
GROUP_ID = 89320
QUEUE_ID = 89300
TICKET_ATTACHMENTS = 89310  # 2 real attachments + 1 inline image + 1 body part
TICKET_AI_SUMMARY = 89311  # has a tiqora_ai_ticket_state row with summary_body
TICKET_PLAIN = 89312  # no attachments, no ai summary
ARTICLE_ATTACHMENTS = 89310
ARTICLE_AI_SUMMARY = 89311
ARTICLE_PLAIN = 89312


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


def _seed(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)

        # Idempotent cleanup of our block (shared session-scoped DB); children
        # before parents so FKs do not block.
        conn.execute(
            text("DELETE FROM article_data_mime_attachment WHERE article_id IN (:a1, :a2, :a3)"),
            {"a1": ARTICLE_ATTACHMENTS, "a2": ARTICLE_AI_SUMMARY, "a3": ARTICLE_PLAIN},
        )
        conn.execute(
            text("DELETE FROM article_data_mime WHERE article_id IN (:a1, :a2, :a3)"),
            {"a1": ARTICLE_ATTACHMENTS, "a2": ARTICLE_AI_SUMMARY, "a3": ARTICLE_PLAIN},
        )
        conn.execute(
            text("DELETE FROM article WHERE id IN (:a1, :a2, :a3)"),
            {"a1": ARTICLE_ATTACHMENTS, "a2": ARTICLE_AI_SUMMARY, "a3": ARTICLE_PLAIN},
        )
        conn.execute(
            text("DELETE FROM tiqora_ai_ticket_state WHERE ticket_id IN (:t1, :t2, :t3)"),
            {"t1": TICKET_ATTACHMENTS, "t2": TICKET_AI_SUMMARY, "t3": TICKET_PLAIN},
        )
        conn.execute(
            text("DELETE FROM ticket WHERE id IN (:t1, :t2, :t3)"),
            {"t1": TICKET_ATTACHMENTS, "t2": TICKET_AI_SUMMARY, "t3": TICKET_PLAIN},
        )
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": QUEUE_ID})
        conn.execute(
            text("DELETE FROM group_user WHERE user_id = :uid OR group_id = :gid"),
            {"uid": AGENT_ID, "gid": GROUP_ID},
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = :id"), {"id": GROUP_ID})
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": AGENT_ID})

        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'reader.enrich893', 'x', 'Enrich', 'Reader', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'enrich893-grp', 1, :t, 1, :t, 1)"
            ),
            {"id": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": AGENT_ID, "gid": GROUP_ID, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'Enrich893Queue', :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": QUEUE_ID, "gid": GROUP_ID, "t": NOW},
        )

        for ticket_id, tn_suffix, title in (
            (TICKET_ATTACHMENTS, "1", "Ticket with attachments"),
            (TICKET_AI_SUMMARY, "2", "Ticket with AI summary"),
            (TICKET_PLAIN, "3", "Plain ticket"),
        ):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id,"
                    " timeout, until_time, escalation_time, escalation_update_time,"
                    " escalation_response_time, escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :qid, 1, 1,"
                    " :uid, 1, 3, 4,"
                    " NULL, NULL,"
                    " 0, 0, 0, 0, 0, 0, 0,"
                    " :t, 1, :t, 1)"
                ),
                {
                    "id": ticket_id,
                    "tn": f"202406018930{tn_suffix}",
                    "title": title,
                    "qid": QUEUE_ID,
                    "uid": AGENT_ID,
                    "t": NOW,
                },
            )

        for article_id, ticket_id in (
            (ARTICLE_ATTACHMENTS, TICKET_ATTACHMENTS),
            (ARTICLE_AI_SUMMARY, TICKET_AI_SUMMARY),
            (ARTICLE_PLAIN, TICKET_PLAIN),
        ):
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " search_index_needs_rebuild, create_time, create_by, change_time, change_by)"
                    " VALUES (:aid, :tid, 3, 1, 1, 0, :t, 1, :t, 1)"
                ),
                {"aid": article_id, "tid": ticket_id, "t": NOW},
            )
            conn.execute(
                text(
                    "INSERT INTO article_data_mime (id, article_id, a_from, a_subject,"
                    " a_content_type, a_body, incoming_time, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:id, :aid, 'alice@example.com', 'Subject',"
                    " 'text/plain; charset=utf-8', 'Body', 1717243200, :t, 1, :t, 1)"
                ),
                {"id": article_id, "aid": article_id, "t": NOW},
            )

        # Ticket A: 2 real attachments + 1 inline image + 1 body part.
        att_insert = text(
            "INSERT INTO article_data_mime_attachment (id, article_id, filename,"
            " content_size, content_type, content_id, content_alternative, disposition,"
            " content, create_time, create_by, change_time, change_by)"
            " VALUES (:id, :aid, :filename, '5', :ctype, :cid, '', :disposition,"
            " :content, :t, 1, :t, 1)"
        ).bindparams(bindparam("content", type_=LargeBinary()))
        for att_id, filename, ctype, cid, disposition in (
            (89330, "doc1.pdf", "application/pdf", None, "attachment"),
            (89331, "doc2.pdf", "application/pdf", None, None),
            (89332, "logo.png", "image/png", "<cid-logo893@local>", None),
            (89333, "file-1", "text/plain", None, None),
        ):
            conn.execute(
                att_insert,
                {
                    "id": att_id,
                    "aid": ARTICLE_ATTACHMENTS,
                    "filename": filename,
                    "ctype": ctype,
                    "cid": cid,
                    "disposition": disposition,
                    "content": b"x",
                    "t": NOW,
                },
            )

        conn.execute(
            text(
                "INSERT INTO tiqora_ai_ticket_state (ticket_id, summary_body)"
                " VALUES (:tid, :summary)"
            ),
            {"tid": TICKET_AI_SUMMARY, "summary": "Customer asked about invoice X."},
        )

    engine.dispose()
    return {
        "reader": AGENT_ID,
        "queue": QUEUE_ID,
        "ticket_attachments": TICKET_ATTACHMENTS,
        "ticket_ai_summary": TICKET_AI_SUMMARY,
        "ticket_plain": TICKET_PLAIN,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_list_tickets_enrichment_fields(
    url_fixture: str,
    request: pytest.FixtureRequest,
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        ts = TicketService(session)
        listed = await ts.list_tickets(ids["reader"], queue_id=ids["queue"], limit=50)

        # Scope assertions to our seeded ticket ids only — the testcontainer
        # DB is shared with other test modules.
        by_id = {
            i.id: i
            for i in listed.items
            if i.id in {ids["ticket_attachments"], ids["ticket_ai_summary"], ids["ticket_plain"]}
        }
        assert set(by_id) == {
            ids["ticket_attachments"],
            ids["ticket_ai_summary"],
            ids["ticket_plain"],
        }

        att_item = by_id[ids["ticket_attachments"]]
        assert att_item.attachment_count == 2
        assert att_item.has_ai_summary is False

        summary_item = by_id[ids["ticket_ai_summary"]]
        assert summary_item.attachment_count == 0
        assert summary_item.has_ai_summary is True

        plain_item = by_id[ids["ticket_plain"]]
        assert plain_item.attachment_count == 0
        assert plain_item.has_ai_summary is False

    await engine.dispose()
