"""DB integration tests for ticket-zoom additions (both dialects).

Covers: history rendering + id resolution + order, empty dynamic-field hiding,
reply-draft (Re: dedup, quote, reply-all Cc), templates endpoint (queue join +
permission), and forward/bounce/split/link write paths.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_service import TicketAccessDenied, TicketService
from tiqora.domain.ticket_write_service import TicketWriteService
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
        for uid, login in ((7301, "agent.rw"), (7302, "agent.none")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, :pw, 'Ag', 'Ent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": login, "pw": pw, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (7330, 'zoom-grp', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        for key in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (7301, 7330, :k, :t, 1, :t, 1)"
                ),
                {"k": key, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (7300, 'ZoomQueue', 7330, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        # Ticket: state open=4, priority normal=3
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (7700, '20240601700001', 'Zoom ticket', 7300, 1, 1,"
                " 7301, 1, 3, 4, 'CUST1', 'alice@example.com',"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        # Article from customer with To + Cc for reply-all
        conn.execute(
            text(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (7800, 7700, 3, 1, 1, 0, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO article_data_mime (id, article_id, a_from, a_to, a_cc, a_subject,"
                " a_content_type, a_body, a_message_id, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (7800, 7800, 'alice@example.com', 'support@example.com,bob@example.com',"
                " 'carol@example.com', 'Re: Aw: Broken thing',"
                " 'text/plain; charset=utf-8', 'First line\nSecond line', '<msg-1@x>', 1717243200,"
                " :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        # Two dynamic fields: one with value, one empty (must be hidden)
        for fid, name in ((9101, "ZoomFilled"), (9102, "ZoomEmpty")):
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, valid_id, create_time, create_by,"
                    " change_time, change_by)"
                    " VALUES (:id, 0, :name, :name, :ord, 'Text', 'Ticket', 1, :t, 1, :t, 1)"
                ),
                {"id": fid, "name": name, "ord": fid, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO dynamic_field_value (id, field_id, object_id, value_text)"
                " VALUES (9101, 9101, 7700, 'HasValue')"
            )
        )
        # empty-valued row for ZoomEmpty (value_text = '')
        conn.execute(
            text(
                "INSERT INTO dynamic_field_value (id, field_id, object_id, value_text)"
                " VALUES (9102, 9102, 7700, '')"
            )
        )
        # OwnerUpdate history row referencing user id 7301 -> should resolve to login
        conn.execute(
            text(
                "INSERT INTO ticket_history (id, name, history_type_id, ticket_id, type_id,"
                " queue_id, owner_id, priority_id, state_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (7900, '%%agent.rw%%7301', "
                " (SELECT id FROM ticket_history_type WHERE name='OwnerUpdate'),"
                " 7700, 1, 7300, 7301, 3, 4, :t, 7301, :t, 7301)"
            ),
            {"t": NOW},
        )
        # A response template linked to the queue
        conn.execute(
            text(
                "INSERT INTO standard_template (id, name, text, content_type, template_type,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (7401, 'Greeting', 'Hello from support', 'text/plain', 'Answer',"
                " 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        # A non-Answer template (must NOT appear)
        conn.execute(
            text(
                "INSERT INTO standard_template (id, name, text, content_type, template_type,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (7402, 'FwdTpl', 'fwd', 'text/plain', 'Forward',"
                " 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue_standard_template (queue_id, standard_template_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (7300, 7401, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue_standard_template (queue_id, standard_template_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (7300, 7402, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
    engine.dispose()
    return {"agent": 7301, "no_access": 7302, "queue": 7300, "ticket": 7700, "article": 7800}


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_ticket_zoom(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        ts = TicketService(session)

        # Empty dynamic fields are hidden; only the filled one remains.
        detail = await ts.get_ticket(ids["agent"], ids["ticket"])
        names = {df.name for df in detail.dynamic_fields}
        assert "ZoomFilled" in names
        assert "ZoomEmpty" not in names
        assert detail.can_write is True
        assert detail.is_watched is False

        # History rendered + owner-id resolved to login, default desc order.
        hist = await ts.list_history(ids["agent"], ids["ticket"])
        owner_rows = [h for h in hist if h.history_type == "OwnerUpdate"]
        assert owner_rows and owner_rows[0].rendered == "Owner set to agent.rw."
        assert owner_rows[0].create_by_login == "agent.rw"
        assert all("%%" not in h.rendered for h in hist)

        # order=asc reverses.
        hist_asc = await ts.list_history(ids["agent"], ids["ticket"], order="asc")
        assert [h.id for h in hist_asc] == sorted(h.id for h in hist_asc)

        # Reply draft: Re: dedup + quote + attribution.
        draft = await ts.get_reply_draft(ids["agent"], ids["ticket"], ids["article"])
        assert draft.subject == "Re: Broken thing"
        assert draft.to_address == "alice@example.com"
        assert "> First line" in draft.body
        assert "wrote:" in draft.body
        assert draft.body.startswith("\n\n")  # empty answer area above quote

        # Reply-all: Cc includes other recipients, not the sender.
        draft_all = await ts.get_reply_draft(
            ids["agent"], ids["ticket"], ids["article"], reply_all=True
        )
        assert draft_all.cc is not None
        assert "bob@example.com" in draft_all.cc
        assert "carol@example.com" in draft_all.cc
        assert "alice@example.com" not in (draft_all.cc or "")

        # Templates: only the Answer template for this queue.
        tpls = await ts.list_templates(ids["agent"], ids["ticket"])
        assert [t.name for t in tpls] == ["Greeting"]
        assert tpls[0].text == "Hello from support"

        # Permission: no-access user rejected.
        with pytest.raises(TicketAccessDenied):
            await ts.list_templates(ids["no_access"], ids["ticket"])

    async with factory() as session, session.begin():
        svc = TicketWriteService(session, factory, sysconfig)

        # Forward → history type Forward.
        fwd_id = await svc.forward_article(
            ids["agent"],
            ids["ticket"],
            subject="Fwd: Broken thing",
            body="see below",
            to_address="ext@partner.com",
        )
        assert fwd_id > 0

        # Bounce → resends article body verbatim, history type Bounce.
        bounce_id = await svc.bounce_article(
            ids["agent"], ids["ticket"], ids["article"], to_address="ext2@partner.com"
        )
        assert bounce_id > 0

        # Split → new linked ticket.
        new_ticket_id = await svc.split_article(
            ids["agent"], ids["ticket"], ids["article"], queue_id=ids["queue"], title="Split off"
        )
        assert new_ticket_id != ids["ticket"]

        # Link listing shows the split-created link.
        links = await svc.list_links(ids["agent"], ids["ticket"])
        assert any(link["other_ticket_id"] == new_ticket_id for link in links)

    async with factory() as session:
        # Forward/Bounce history types present.
        rows = (
            await session.execute(
                text(
                    "SELECT ht.name FROM ticket_history h"
                    " JOIN ticket_history_type ht ON ht.id = h.history_type_id"
                    " WHERE h.ticket_id = :tid"
                ),
                {"tid": ids["ticket"]},
            )
        ).fetchall()
        htypes = {r[0] for r in rows}
        assert "Forward" in htypes
        assert "Bounce" in htypes

    await engine.dispose()
