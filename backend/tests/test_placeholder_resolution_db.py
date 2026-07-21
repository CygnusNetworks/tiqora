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
from tiqora.channels.email.placeholder import (
    KEY_CUSTOMER_ALLOWLIST,
    expand_placeholders,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.db.tiqora.models import TiqoraPlaceholderField
from tiqora.domain.settings_store import set_setting
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

        # Idempotent cleanup of our ns block (shared session-scoped DB).
        # Children before parents so FKs do not block.
        conn.execute(text("DELETE FROM ticket WHERE id = :id"), {"id": ticket_id})
        conn.execute(
            text(
                "DELETE FROM queue_standard_template WHERE queue_id = :qid"
                " OR standard_template_id = :tid"
            ),
            {"qid": queue_id, "tid": tpl_id},
        )
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": queue_id})
        conn.execute(text("DELETE FROM standard_template WHERE id = :id"), {"id": tpl_id})
        conn.execute(
            text("DELETE FROM group_user WHERE user_id IN (:u1, :u2) OR group_id = :g"),
            {"u1": agent_id, "u2": owner_id, "g": group_id},
        )
        conn.execute(text("DELETE FROM permission_groups WHERE id = :id"), {"id": group_id})
        conn.execute(
            text("DELETE FROM customer_user WHERE login = :login"),
            {"login": cust_login},
        )
        conn.execute(
            text("DELETE FROM customer_company WHERE customer_id = :cid"),
            {"cid": cust_company},
        )
        # Queue may reference signature / system_address — already deleted above.
        conn.execute(text("DELETE FROM signature WHERE id = :id"), {"id": sig_id})
        conn.execute(text("DELETE FROM system_address WHERE id = :id"), {"id": sa_id})
        conn.execute(
            text("DELETE FROM users WHERE id IN (:u1, :u2)"),
            {"u1": agent_id, "u2": owner_id},
        )

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


def _ensure_queue_domain_column(sync_url: str) -> bool:
    """Add synthetic queue.domain when missing (site-specific Znuny patch).

    Returns True if the column is usable after this call.
    """
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            dialect = conn.dialect.name
            if dialect.startswith("postgres"):
                has_col = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns"
                        " WHERE table_schema = current_schema()"
                        " AND table_name = 'queue' AND column_name = 'domain'"
                        " LIMIT 1"
                    )
                ).first()
                if not has_col:
                    conn.execute(
                        text("ALTER TABLE queue ADD COLUMN IF NOT EXISTS domain VARCHAR(128)")
                    )
            else:
                has_col = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns"
                        " WHERE table_schema = DATABASE()"
                        " AND table_name = 'queue' AND column_name = 'domain'"
                        " LIMIT 1"
                    )
                ).first()
                if not has_col:
                    conn.execute(text("ALTER TABLE queue ADD COLUMN domain VARCHAR(128) NULL"))
        return True
    except Exception:
        return False
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_queue_custom_column_domain_placeholder(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """SELECT q.* exposes site-specific queue columns as <OTRS_QUEUE_...>.

    Missing columns stay empty (no error); when ``queue.domain`` exists and is
    set, ``<OTRS_QUEUE_Domain>`` resolves to its value.
    """
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=4)
    sysconfig = _make_sysconfig()
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Without the column (or with NULL): empty, no error.
    async with factory() as session:
        expanded = await expand_placeholders(
            session,
            sysconfig,
            "d=<OTRS_QUEUE_Domain> n=<OTRS_QUEUE_Name>",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert expanded.startswith("d=")
        assert f"n={ids['queue_name']}" in expanded
        assert "<OTRS_" not in expanded

    await engine.dispose()

    assert _ensure_queue_domain_column(sync_url)
    domain_val = f"custom-domain-92{ids['ns']}.example"
    sync_engine = create_engine(sync_url)
    with sync_engine.begin() as conn:
        conn.execute(
            text("UPDATE queue SET domain = :d WHERE id = :id"),
            {"d": domain_val, "id": ids["queue"]},
        )
    sync_engine.dispose()

    # Fresh engine after DDL — asyncpg caches prepared plans per connection and
    # would otherwise raise InvalidCachedStatementError on SELECT q.*.
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        expanded = await expand_placeholders(
            session,
            sysconfig,
            "https://startup.<OTRS_QUEUE_Domain>/x",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert expanded == f"https://startup.{domain_val}/x"
        assert "<OTRS_" not in expanded

        # Still-missing field name must stay empty, not raise.
        missing = await expand_placeholders(
            session,
            sysconfig,
            "p=<OTRS_QUEUE_Phonenumber>",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert missing == "p="

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_reply_draft_includes_expanded_signature(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """get_reply_draft returns expanded queue signature for composer preview."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=5)
    article_id = 9280 + ids["ns"]
    sync_engine = create_engine(sync_url)
    with sync_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer, search_index_needs_rebuild,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :tid, 3, 1, 1, 0, :t, 1, :t, 1)"
            ),
            {"id": article_id, "tid": ids["ticket"], "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO article_data_mime (id, article_id, a_from, a_to, a_subject,"
                " a_content_type, a_body, a_message_id, incoming_time,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :aid, :frm, 'support@example.com', 'Hello',"
                " 'text/plain; charset=utf-8', 'Customer body', :mid, 1717243200,"
                " :t, 1, :t, 1)"
            ),
            {
                "id": article_id,
                "aid": article_id,
                "frm": ids["cust_login"],
                "mid": f"<ph-draft-92{ids['ns']}@example.com>",
                "t": NOW,
            },
        )
    sync_engine.dispose()

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        draft = await TicketService(session).get_reply_draft(
            ids["agent"], ids["ticket"], article_id
        )
        assert draft.signature
        assert draft.signature_is_html is False
        # Expanded agent + ticket placeholders from the seeded signature.
        assert "Ada" in draft.signature and "Lovelace" in draft.signature
        assert ids["tn"] in draft.signature
        assert "<OTRS_" not in draft.signature
        # Signature must not be folded into the editable body.
        assert "Ada Lovelace" not in draft.body
        assert draft.body.startswith("\n\n")

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_configured_queue_variable_otrs_and_tiqora_prefix(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Configured tiqora_queue_variable resolves via OTRS_ and TIQORA_ prefixes.

    Precedence: configured var → physical column → empty. Queue-specific
    overrides global (queue_id IS NULL).
    """
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=6)
    sysconfig = _make_sysconfig()
    domain_global = f"global-92{ids['ns']}.example"
    domain_queue = f"queue-92{ids['ns']}.example"
    only_global = f"only-global-92{ids['ns']}"

    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        # Global defaults
        conn.execute(
            text(
                "INSERT INTO tiqora_queue_variable (queue_id, name, value)"
                " VALUES (NULL, 'Domain', :v), (NULL, 'OnlyGlobal', :og)"
            ),
            {"v": domain_global, "og": only_global},
        )
        # Queue-specific Domain overrides global
        conn.execute(
            text(
                "INSERT INTO tiqora_queue_variable (queue_id, name, value)"
                " VALUES (:qid, 'Domain', :v)"
            ),
            {"qid": ids["queue"], "v": domain_queue},
        )
    engine_sync.dispose()

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        both = await expand_placeholders(
            session,
            sysconfig,
            "o=<OTRS_QUEUE_Domain> t=<TIQORA_QUEUE_Domain>"
            " g=<OTRS_QUEUE_OnlyGlobal> u=<OTRS_QUEUE_UnknownVar>",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert both == (f"o={domain_queue} t={domain_queue} g={only_global} u=")
        assert "<OTRS_" not in both and "<TIQORA_" not in both

        # Unknown configured name + no physical column → empty (not raw tag).
        unknown = await expand_placeholders(
            session,
            sysconfig,
            "x=<TIQORA_QUEUE_NoSuchField>",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert unknown == "x="

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_configured_queue_variable_beats_physical_column(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Configured variable wins over a real queue column of the same name."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=7)
    sysconfig = _make_sysconfig()
    assert _ensure_queue_domain_column(sync_url)
    physical = f"physical-92{ids['ns']}.example"
    configured = f"configured-92{ids['ns']}.example"

    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(
            text("UPDATE queue SET domain = :d WHERE id = :id"),
            {"d": physical, "id": ids["queue"]},
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_queue_variable (queue_id, name, value)"
                " VALUES (:qid, 'Domain', :v)"
            ),
            {"qid": ids["queue"], "v": configured},
        )
    engine_sync.dispose()

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        expanded = await expand_placeholders(
            session,
            sysconfig,
            "d=<OTRS_QUEUE_Domain>",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert expanded == f"d={configured}"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_customer_allowlist_gate_default_off(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Allow-list gate defaults OFF — all customer columns still resolve."""
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url, ns=8)
    if not ids["has_wpnum"]:
        pytest.skip("wpnum column not available")
    sysconfig = _make_sysconfig()

    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine_sync.dispose()

    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        # No allow-list rows, flag unset → wpnum still resolves.
        expanded = await expand_placeholders(
            session,
            sysconfig,
            "w=<OTRS_CUSTOMER_DATA_wpnum> f=<OTRS_CUSTOMER_DATA_UserFirstname>",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert ids["wpnum"] in expanded
        assert "Alice" in expanded

        # Enable gate with only UserFirstname registered → wpnum blocked.
        await set_setting(session, KEY_CUSTOMER_ALLOWLIST, "true")
        session.add(
            TiqoraPlaceholderField(
                source_table="customer_user",
                column_name="first_name",
                tag_name="UserFirstname",
                label="First name",
                enabled=True,
            )
        )
        await session.commit()

        gated = await expand_placeholders(
            session,
            sysconfig,
            "w=<OTRS_CUSTOMER_DATA_wpnum> f=<OTRS_CUSTOMER_DATA_UserFirstname>",
            ticket_id=ids["ticket"],
            user_id=ids["agent"],
        )
        assert gated == "w= f=Alice"

    await engine.dispose()
