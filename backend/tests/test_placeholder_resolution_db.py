"""DB integration tests for full <OTRS_...> placeholder resolution.

Covers Answer-template expansion (list_templates) and queue-signature expansion
on the agent-reply path. Unique seed ids in the 92xx band so the session-scoped
testcontainer DB is shared safely with other files.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.email.outbound_reply import prepare_outgoing_agent_email
from tiqora.channels.email.placeholder import expand_placeholders
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.ticket_service import TicketService
from tiqora.domain.ticket_write_service import ArticleIn
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


def _seed(sync_url: str, *, ns: int) -> dict[str, Any]:
    """Seed agent, queue, customer_user (+ custom wpnum), ticket, Answer template, signature."""
    agent_id = 9200 + ns
    owner_id = 9210 + ns
    group_id = 9230 + ns
    queue_id = 9200 + ns
    ticket_id = 9270 + ns
    sig_id = 9200 + ns
    sa_id = 9200 + ns
    tpl_id = 9240 + ns
    login = f"agent.ph.92{ns}"
    owner_login = f"owner.ph.92{ns}"
    queue_name = f"PhQueue92{ns}"
    cust_login = f"cust.ph.92{ns}@example.com"
    cust_company = f"COMP92{ns}"
    tn = f"20240601920{ns:03d}"
    wpnum = f"WP-92{ns:02d}"

    engine = create_engine(sync_url)
    pw = hash_password("secret")

    # Site-specific column used as <OTRS_CUSTOMER_DATA_wpnum> in real templates.
    # Separate connection so a failed ALTER cannot poison the seed transaction.
    with engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect.startswith("postgres"):
            has_col = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns"
                    " WHERE table_schema = current_schema()"
                    " AND table_name = 'customer_user' AND column_name = 'wpnum'"
                    " LIMIT 1"
                )
            ).first()
            if not has_col:
                conn.execute(
                    text("ALTER TABLE customer_user ADD COLUMN IF NOT EXISTS wpnum VARCHAR(64)")
                )
        else:
            has_col = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns"
                    " WHERE table_schema = DATABASE()"
                    " AND table_name = 'customer_user' AND column_name = 'wpnum'"
                    " LIMIT 1"
                )
            ).first()
            if not has_col:
                conn.execute(text("ALTER TABLE customer_user ADD COLUMN wpnum VARCHAR(64) NULL"))

    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)

        for uid, ulogin, first, last in (
            (agent_id, login, "Ada", "Lovelace"),
            (owner_id, owner_login, "Grace", "Hopper"),
        ):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, :pw, :fn, :ln, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": ulogin, "pw": pw, "fn": first, "ln": last, "t": NOW},
            )

        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, :t, 1, :t, 1)"
            ),
            {"id": group_id, "name": f"ph-grp-92{ns}", "t": NOW},
        )
        for key in ("ro", "rw", "create"):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": agent_id, "gid": group_id, "k": key, "t": NOW},
            )

        conn.execute(
            text(
                "INSERT INTO customer_company (customer_id, name, street, zip, city,"
                " country, url, comments, valid_id, create_time, create_by,"
                " change_time, change_by)"
                " VALUES (:cid, :name, 'Main St', '12345', 'Berlin', 'DE',"
                " 'https://example.com', 'co', 1, :t, 1, :t, 1)"
            ),
            {"cid": cust_company, "name": f"Company 92{ns}", "t": NOW},
        )
        # Insert customer_user with custom wpnum when the column exists.
        dialect = conn.dialect.name
        if dialect.startswith("postgres"):
            has_wpnum = bool(
                conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns"
                        " WHERE table_schema = current_schema()"
                        " AND table_name = 'customer_user' AND column_name = 'wpnum'"
                        " LIMIT 1"
                    )
                ).first()
            )
        else:
            has_wpnum = bool(
                conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns"
                        " WHERE table_schema = DATABASE()"
                        " AND table_name = 'customer_user' AND column_name = 'wpnum'"
                        " LIMIT 1"
                    )
                ).first()
            )
        if has_wpnum:
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name,"
                    " last_name, phone, valid_id, create_time, create_by, change_time,"
                    " change_by, wpnum)"
                    " VALUES (:login, :email, :cid, 'Alice', 'Customer', '555',"
                    " 1, :t, 1, :t, 1, :wp)"
                ),
                {
                    "login": cust_login,
                    "email": cust_login,
                    "cid": cust_company,
                    "wp": wpnum,
                    "t": NOW,
                },
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name,"
                    " last_name, phone, valid_id, create_time, create_by, change_time,"
                    " change_by)"
                    " VALUES (:login, :email, :cid, 'Alice', 'Customer', '555',"
                    " 1, :t, 1, :t, 1)"
                ),
                {
                    "login": cust_login,
                    "email": cust_login,
                    "cid": cust_company,
                    "t": NOW,
                },
            )
            wpnum = ""  # cannot verify custom column

        sig_text = (
            "\n-- \n"
            "<OTRS_AGENT_UserFirstname> <OTRS_AGENT_UserLastname>\n"
            "Ticket <OTRS_TICKET_TicketNumber> / customer wp=<OTRS_CUSTOMER_DATA_wpnum>\n"
            "Queue domain=<OTRS_QUEUE_Domain>\n"
        )
        conn.execute(
            text(
                "INSERT INTO signature (id, name, text, content_type, comments, valid_id,"
                " create_by, create_time, change_by, change_time)"
                " VALUES (:id, :name, :txt, 'text/plain; charset=utf-8', 'test', 1, 1, :t, 1, :t)"
            ),
            {
                "id": sig_id,
                "name": f"ph-sig-92{ns}",
                "txt": sig_text,
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO system_address (id, value0, value1, comments, valid_id, queue_id,"
                " create_by, create_time, change_by, change_time)"
                " VALUES (:id, :addr, 'Ph Support', 'test', 1, 1, 1, :t, 1, :t)"
            ),
            {"id": sa_id, "addr": f"ph{ns}@support.example", "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :gid, :sa, 1, :sig, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {
                "id": queue_id,
                "name": queue_name,
                "gid": group_id,
                "sa": sa_id,
                "sig": sig_id,
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                " escalation_update_time, escalation_response_time, escalation_solution_time,"
                " archive_flag, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tn, :title, :qid, 1, 1,"
                " :owner, 1, 3, 4, :cid, :cuid,"
                " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
            ),
            {
                "id": ticket_id,
                "tn": tn,
                "title": f"Placeholder ticket 92{ns}",
                "qid": queue_id,
                "owner": owner_id,
                "cid": cust_company,
                "cuid": cust_login,
                "t": NOW,
            },
        )

        tpl_body = (
            "https://startup.<OTRS_QUEUE_Domain>/?wpn=<OTRS_CUSTOMER_DATA_wpnum>"
            "&tn=<OTRS_TICKET_TicketNumber>&lang=de\n"
            "Hello <OTRS_CUSTOMER_DATA_UserFirstname>,\n"
            "Owner: <OTRS_OWNER_UserFirstname>\n"
            "Agent: <OTRS_CURRENT_UserFirstname>\n"
        )
        conn.execute(
            text(
                "INSERT INTO standard_template (id, name, text, content_type, template_type,"
                " valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, :txt, 'text/plain', 'Answer', 1, :t, 1, :t, 1)"
            ),
            {
                "id": tpl_id,
                "name": f"PhAnswer92{ns}",
                "txt": tpl_body,
                "t": NOW,
            },
        )
        conn.execute(
            text(
                "INSERT INTO queue_standard_template (queue_id, standard_template_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qid, :tid, :t, 1, :t, 1)"
            ),
            {"qid": queue_id, "tid": tpl_id, "t": NOW},
        )

    engine.dispose()
    return {
        "agent": agent_id,
        "owner": owner_id,
        "queue": queue_id,
        "ticket": ticket_id,
        "tpl": tpl_id,
        "tn": tn,
        "wpnum": wpnum,
        "has_wpnum": bool(wpnum),
        "cust_login": cust_login,
        "queue_name": queue_name,
        "ns": ns,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_list_templates_expands_placeholders(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=1)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        tpls = await TicketService(session).list_templates(ids["agent"], ids["ticket"])
        assert len(tpls) == 1
        body = tpls[0].text
        assert "<OTRS_" not in body, f"raw tags left in template: {body!r}"
        assert ids["tn"] in body
        assert "Hello Alice," in body
        assert "Owner: Grace" in body
        assert "Agent: Ada" in body
        # QUEUE_Domain is not a real column → empty string
        assert "https://startup./?wpn=" in body or "https://startup./" in body
        if ids["has_wpnum"]:
            assert ids["wpnum"] in body

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_signature_expanded_on_prepare_reply(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=2)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        prepared = await prepare_outgoing_agent_email(
            session,
            sysconfig,
            ticket_id=ids["ticket"],
            queue_id=ids["queue"],
            user_id=ids["agent"],
            article=ArticleIn(
                sender_type="agent",
                is_visible_for_customer=True,
                subject="Re: test",
                body="Thanks for writing in.",
                channel="email",
                to_address=ids["cust_login"],
            ),
        )
        body = prepared.body or ""
        assert "Thanks for writing in." in body
        assert "Ada" in body and "Lovelace" in body
        assert ids["tn"] in body
        assert "<OTRS_" not in body, f"raw tags left in signature: {body!r}"
        # Unknown QUEUE_Domain → empty
        assert "domain=" in body
        if ids["has_wpnum"]:
            assert ids["wpnum"] in body

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_expand_unknown_and_error_best_effort(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=3)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        expanded = await expand_placeholders(
            session,
            sysconfig,
            "x=<OTRS_NO_SUCH_TAG> tn=<OTRS_TICKET_TicketNumber>",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert expanded == f"x= tn={ids['tn']}"
        assert "<OTRS_" not in expanded

        original = "FAIL <OTRS_TICKET_TicketNumber> KEEP"
        with patch(
            "tiqora.channels.email.placeholder.load_placeholder_context",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            # ticket_id path calls load_placeholder_context — failure must not raise
            recovered = await expand_placeholders(
                session,
                sysconfig,
                original,
                ticket_id=ids["ticket"],
                user_id=ids["agent"],
            )
        assert recovered == original

    await engine.dispose()
