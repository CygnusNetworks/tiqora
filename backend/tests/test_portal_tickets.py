"""DB integration tests for PortalTicketService (Phase 3a subtask 1).

Covers: ticket isolation between customers, article visibility filtering
(internal notes never returned to portal), reply-reopens-closed-ticket state
transition (FollowUp.pm PostmasterFollowUpState semantics), reject-on-
followup for queues with ``follow_up_id == reject``, ticket creation via the
shared ``create_ticket`` write-service invariant bundle, and the
``portal.company_tickets_enabled`` scoping flag (both the direct
``customer_id`` match and the ``customer_user_customer`` mapping-table path).

Note: ``mariadb_znuny_url`` / ``postgres_znuny_url`` are session-scoped
testcontainer fixtures shared by every test function *and test module* in
the whole pytest run, so ``_seed`` namespaces every id/login/customer_id it
inserts with a random UUID fragment to avoid primary-key/unique collisions
both within this file and against unrelated files (e.g. test_portal_auth.py)
that reuse the same container.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.domain.portal_ticket_service import (
    SETTING_COMPANY_TICKETS,
    PortalFollowUpRejected,
    PortalTicketAccessDenied,
    PortalTicketNotFound,
    PortalTicketService,
)
from tiqora.domain.settings_store import set_setting
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return _mysql_async(sync_url)
    return sync_url


def _make_sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        return None

    return SysConfig(fetch=_fetch)


def _seed(sync_url: str) -> dict[str, Any]:
    """Seed customers, queues (possible + reject follow-up), and tickets/articles.

    All primary keys / logins / customer_ids are namespaced by a random UUID
    fragment so repeated calls against the same (session-scoped) testcontainer
    DB — shared across test functions and test modules — never collide.
    """
    ns = uuid.uuid4().hex[:8]
    base = int(ns, 16) % 1_000_000 * 1000

    def cid(name: str) -> str:
        return f"{name}-{ns}"

    def login(name: str) -> str:
        return f"{name}.{ns}@portal.example"

    engine = create_engine(sync_url)
    ids: dict[str, Any] = {}

    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)

        def _sid(name: str) -> int:
            row = conn.execute(
                text("SELECT id FROM ticket_state WHERE name = :n"), {"n": name}
            ).first()
            assert row is not None, f"missing ticket_state {name!r}"
            return int(row[0])

        def _pid(name: str) -> int:
            row = conn.execute(
                text("SELECT id FROM ticket_priority WHERE name = :n"), {"n": name}
            ).first()
            assert row is not None
            return int(row[0])

        def _lid(name: str) -> int:
            row = conn.execute(
                text("SELECT id FROM ticket_lock_type WHERE name = :n"), {"n": name}
            ).first()
            assert row is not None
            return int(row[0])

        state_open = _sid("open")
        state_closed = _sid("closed successful")
        prio = _pid("3 normal")
        lock = _lid("unlock")

        cid_alice, cid_dave, cid_mapped = cid("PORTAL1"), cid("PORTAL2"), cid("PORTAL3")
        login_alice, login_bob = login("alice"), login("bob")
        login_carol, login_dave, login_eve = login("carol"), login("dave"), login("eve")

        conn.execute(
            text(
                "INSERT INTO customer_company (customer_id, name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:c1, :n1, 1, :t, 1, :t, 1),"
                " (:c2, :n2, 1, :t, 1, :t, 1),"
                " (:c3, :n3, 1, :t, 1, :t, 1)"
            ),
            {
                "c1": cid_alice,
                "n1": f"Alice Co {ns}",
                "c2": cid_dave,
                "n2": f"Dave Co {ns}",
                "c3": cid_mapped,
                "n3": f"Mapped Co {ns}",
                "t": NOW,
            },
        )
        customer_ids = {
            base + 1: (login_alice, cid_alice),
            base + 2: (login_bob, cid_dave),
            base + 3: (login_carol, cid_alice),
            base + 4: (login_dave, cid_dave),
        }
        for uid, (lg, c) in customer_ids.items():
            conn.execute(
                text(
                    "INSERT INTO customer_user (id, login, email, customer_id, first_name,"
                    " last_name, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, :login, :cid, 'T', 'User', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": lg, "cid": c, "t": NOW},
            )
        # dave additionally mapped to the "mapped" company via customer_user_customer
        conn.execute(
            text(
                "INSERT INTO customer_user_customer (user_id, customer_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:login, :cid, :t, 1, :t, 1)"
            ),
            {"login": login_dave, "cid": cid_mapped, "t": NOW},
        )

        q_possible, q_reject = base + 10, base + 11
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:qp, :qpn, 1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1),"
                " (:qr, :qrn, 1, 1, 1, 1, 2, 0, 1, :t, 1, :t, 1)"
            ),
            {
                "qp": q_possible,
                "qpn": f"PortalPossible-{ns}",
                "qr": q_reject,
                "qrn": f"PortalReject-{ns}",
                "t": NOW,
            },
        )

        def _ticket(
            tid: int, tn: str, queue_id: int, state_id: int, cust_login: str, customer_id: str
        ) -> None:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time, escalation_solution_time,"
                    " archive_flag, create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'T', :qid, :lock, 1, 1, 1, :prio, :sid, :cid, :cul,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": tn,
                    "qid": queue_id,
                    "lock": lock,
                    "prio": prio,
                    "sid": state_id,
                    "cid": customer_id,
                    "cul": cust_login,
                    "t": NOW,
                },
            )

        t_open = base + 100
        t_closed_possible = base + 101
        t_closed_reject = base + 102
        t_other_customer = base + 103
        t_same_company = base + 104
        t_mapped_company = base + 105

        # alice, open, in "possible" queue — visible + internal article
        _ticket(t_open, f"T{t_open}", q_possible, state_open, login_alice, cid_alice)
        # alice, closed, "possible" queue — reopen-on-reply target
        _ticket(
            t_closed_possible,
            f"T{t_closed_possible}",
            q_possible,
            state_closed,
            login_alice,
            cid_alice,
        )
        # alice, closed, "reject" queue — reject-on-reply target
        _ticket(
            t_closed_reject, f"T{t_closed_reject}", q_reject, state_closed, login_alice, cid_alice
        )
        # bob, open — isolation target (alice must not see this)
        _ticket(
            t_other_customer, f"T{t_other_customer}", q_possible, state_open, login_bob, cid_dave
        )
        # carol (same company as alice, different login) — company-scope target
        _ticket(
            t_same_company, f"T{t_same_company}", q_possible, state_open, login_carol, cid_alice
        )
        # eve, customer_id in the "mapped" company — reachable only via dave's mapping row
        _ticket(
            t_mapped_company, f"T{t_mapped_company}", q_possible, state_open, login_eve, cid_mapped
        )

        art_visible, art_internal = base + 200, base + 201
        conn.execute(
            text(
                "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer,"
                " search_index_needs_rebuild, create_time, create_by, change_time, change_by)"
                " VALUES (:av, :t_open, 3, 1, 1, 0, :t, 1, :t, 1),"
                " (:ai, :t_open, 1, 1, 0, 0, :t, 1, :t, 1)"
            ),
            {"av": art_visible, "ai": art_internal, "t_open": t_open, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO article_data_mime (id, article_id, a_subject, a_content_type,"
                " a_body, incoming_time, create_time, create_by, change_time, change_by)"
                " VALUES (:av, :av, 'Visible', 'text/plain', 'hi', 0, :t, 1, :t, 1),"
                " (:ai, :ai, 'Internal note', 'text/plain', 'shh', 0, :t, 1, :t, 1)"
            ),
            {"av": art_visible, "ai": art_internal, "t": NOW},
        )

    engine.dispose()
    ids.update(
        {
            "open": state_open,
            "closed": state_closed,
            "ticket_open": t_open,
            "ticket_closed_possible": t_closed_possible,
            "ticket_closed_reject": t_closed_reject,
            "ticket_other_customer": t_other_customer,
            "ticket_same_company": t_same_company,
            "ticket_mapped_company": t_mapped_company,
            "article_visible": art_visible,
            "article_internal": art_internal,
            "alice": AuthenticatedCustomer(
                id=base + 1,
                login=login_alice,
                email=login_alice,
                customer_id=cid_alice,
                first_name="T",
                last_name="User",
            ),
            "bob": AuthenticatedCustomer(
                id=base + 2,
                login=login_bob,
                email=login_bob,
                customer_id=cid_dave,
                first_name="T",
                last_name="User",
            ),
            "dave": AuthenticatedCustomer(
                id=base + 4,
                login=login_dave,
                email=login_dave,
                customer_id=cid_dave,
                first_name="T",
                last_name="User",
            ),
        }
    )
    return ids


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_isolation_and_article_visibility(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    alice, bob = ids["alice"], ids["bob"]
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        svc = PortalTicketService(session, factory, sysconfig)

        # Alice sees her own tickets, not Bob's.
        listed = await svc.list_tickets(alice)
        tids = {t.id for t in listed.items}
        assert ids["ticket_open"] in tids
        assert ids["ticket_other_customer"] not in tids

        detail = await svc.get_ticket(alice, ids["ticket_open"])
        assert detail.id == ids["ticket_open"]

        with pytest.raises(PortalTicketAccessDenied):
            await svc.get_ticket(alice, ids["ticket_other_customer"])

        with pytest.raises(PortalTicketAccessDenied):
            await svc.get_ticket(bob, ids["ticket_open"])

        with pytest.raises(PortalTicketNotFound):
            await svc.get_ticket(alice, 999_999_999)

        # Internal notes are never returned to the portal.
        articles = await svc.list_visible_articles(alice, ids["ticket_open"])
        assert [a.id for a in articles] == [ids["article_visible"]]
        assert all(a.is_visible_for_customer for a in articles)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_reply_reopens_closed_ticket(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    alice = ids["alice"]
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        svc = PortalTicketService(session, factory, sysconfig)
        article_id, reopened = await svc.reply(
            alice, ids["ticket_closed_possible"], body="Following up"
        )
        assert reopened is True
        assert article_id > 0

    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT ticket_state_id FROM ticket WHERE id = :tid"),
                {"tid": ids["ticket_closed_possible"]},
            )
        ).first()
        assert row is not None
        assert int(row[0]) != ids["closed"], "ticket should have been reopened out of closed state"

        state_name = (
            await session.execute(
                text("SELECT name FROM ticket_state WHERE id = :sid"), {"sid": int(row[0])}
            )
        ).scalar_one()
        assert state_name == "open"  # DEFAULT_FOLLOWUP_REOPEN_STATE

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_reply_rejected_on_reject_followup_queue(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    alice = ids["alice"]
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        svc = PortalTicketService(session, factory, sysconfig)
        with pytest.raises(PortalFollowUpRejected):
            await svc.reply(alice, ids["ticket_closed_reject"], body="Are you there?")

    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT ticket_state_id FROM ticket WHERE id = :tid"),
                {"tid": ids["ticket_closed_reject"]},
            )
        ).first()
        assert row is not None
        assert int(row[0]) == ids["closed"], "rejected follow-up must not change ticket state"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_create_ticket_via_write_service(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    alice = ids["alice"]
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session, session.begin():
        svc = PortalTicketService(session, factory, sysconfig)
        ticket_id = await svc.create_ticket(alice, title="Help please", body="It broke")

    async with factory() as session:
        t = (
            await session.execute(
                text("SELECT customer_user_id, customer_id, queue_id FROM ticket WHERE id = :tid"),
                {"tid": ticket_id},
            )
        ).first()
        assert t is not None
        assert t[0] == alice.login
        assert t[1] == alice.customer_id

        art = (
            await session.execute(
                text(
                    "SELECT article_sender_type_id, is_visible_for_customer FROM article"
                    " WHERE ticket_id = :tid"
                ),
                {"tid": ticket_id},
            )
        ).first()
        assert art is not None
        assert art[1] == 1  # is_visible_for_customer

        svc2 = PortalTicketService(session, factory, sysconfig)
        detail = await svc2.get_ticket(alice, ticket_id)
        assert detail.title == "Help please"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_company_tickets_setting(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed(sync_url)
    alice, bob, dave = ids["alice"], ids["bob"], ids["dave"]
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    sysconfig = _make_sysconfig()

    async with factory() as session:
        svc = PortalTicketService(session, factory, sysconfig)

        # Disabled by default: Alice cannot see Carol's ticket (same company).
        listed = await svc.list_tickets(alice)
        assert ids["ticket_same_company"] not in {t.id for t in listed.items}
        with pytest.raises(PortalTicketAccessDenied):
            await svc.get_ticket(alice, ids["ticket_same_company"])

        # Dave cannot see the mapped-company ticket either, before enabling the flag.
        with pytest.raises(PortalTicketAccessDenied):
            await svc.get_ticket(dave, ids["ticket_mapped_company"])

    async with factory() as session:
        await set_setting(session, SETTING_COMPANY_TICKETS, "1")

    async with factory() as session:
        svc = PortalTicketService(session, factory, sysconfig)

        # Enabled: Alice sees Carol's ticket via matching customer_id.
        listed = await svc.list_tickets(alice)
        assert ids["ticket_same_company"] in {t.id for t in listed.items}
        detail = await svc.get_ticket(alice, ids["ticket_same_company"])
        assert detail.id == ids["ticket_same_company"]

        # Enabled: Dave sees the mapped-company ticket via customer_user_customer.
        detail2 = await svc.get_ticket(dave, ids["ticket_mapped_company"])
        assert detail2.id == ids["ticket_mapped_company"]

        # Bob (unrelated company, no mapping) still sees nothing extra.
        with pytest.raises(PortalTicketAccessDenied):
            await svc.get_ticket(bob, ids["ticket_same_company"])

    await engine.dispose()
