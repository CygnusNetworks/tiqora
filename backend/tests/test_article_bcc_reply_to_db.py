"""DB integration test: article creation persists a_bcc + a_reply_to.

Uses UNIQUE ids/logins (88xx range, ``bcc.*`` logins) so it can run in the
shared session-scoped DB alongside test_ticket_zoom_db.py (73xx/77xx) and
test_read_api_db.py (2xx) without PK or login collisions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.smtp import CapturingMailSender
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_write_service import ArticleIn, TicketWriteService
from tiqora.znuny.password import hash_password
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _make_sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


def _seed(sync_url: str) -> dict[str, Any]:
    engine = create_engine(sync_url)
    pw = hash_password("secret")
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Idempotent cleanup of our block (shared session-scoped DB).
        conn.execute(text("DELETE FROM ticket WHERE id = 8870"))
        conn.execute(text("DELETE FROM queue WHERE id = 8800"))
        conn.execute(
            text("DELETE FROM group_user WHERE user_id = 8801 OR group_id = 8830"),
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = 8830"))
        conn.execute(text("DELETE FROM users WHERE id = 8801"))
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (8801, 'bcc.rw', :pw, 'Bcc', 'Rw', 1, :t, 1, :t, 1)"
            ),
            {"pw": pw, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (8830, 'bcc-grp', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        for key in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (8801, 8830, :k, :t, 1, :t, 1)"
                ),
                {"k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (8800, 'BccQueue', 8830, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (8870, '20240601880001', 'Bcc ticket', 8800, 1, 1,"
                " 8801, 1, 3, 4, 'CUST1', 'alice@example.com',"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
    engine.dispose()
    return {"agent": 8801, "queue": 8800, "ticket": 8870}


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_add_article_persists_bcc_and_reply_to(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        # Agent email replies go through SMTP; capture instead of real send.
        svc = TicketWriteService(session, factory, sysconfig, mail_sender=CapturingMailSender())
        article_id = await svc.add_article(
            ids["agent"],
            ids["ticket"],
            ArticleIn(
                sender_type="agent",
                is_visible_for_customer=True,
                subject="Re: Bcc ticket",
                body="reply body",
                channel="email",
                to_address="alice@example.com",
                cc="carol@example.com",
                bcc="secret@example.com",
                reply_to="noreply@support.example.com",
            ),
        )
        assert article_id > 0

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT a_to, a_cc, a_bcc, a_reply_to FROM article_data_mime"
                    " WHERE article_id = :aid LIMIT 1"
                ),
                {"aid": article_id},
            )
        ).first()
        assert row is not None
        assert row[0] == "alice@example.com"
        assert row[1] == "carol@example.com"
        assert row[2] == "secret@example.com"
        assert row[3] == "noreply@support.example.com"

    await engine.dispose()
