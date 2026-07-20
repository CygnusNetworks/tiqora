"""Tests for ``tiqora gdpr anonymize-customer`` (tiqora/gdpr/anonymize.py, gate.py).

Ownership-gate refusal is exercised against a real MariaDB testcontainer
(the gate reads ``tiqora_settings``, so it needs a working DB session); the
end-to-end scrub then re-runs with ``force_parallel=True`` since the fixture
never sets ``TIQORA_SCHEMA_OWNERSHIP``.
"""

from __future__ import annotations

import contextlib
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.ticket import Ticket
from tiqora.gdpr.anonymize import CustomerNotFoundError, anonymize_customer
from tiqora.gdpr.gate import GdprRefusedError

NOW = datetime(2026, 1, 1, 12, 0, 0)


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


async def _seed_tiqora_tables(session: AsyncSession) -> None:
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_settings ("
                "`key` VARCHAR(200) PRIMARY KEY, value TEXT)"
            )
        )
    with contextlib.suppress(Exception):
        await session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS tiqora_gdpr_audit ("
                "id BIGINT AUTO_INCREMENT PRIMARY KEY, action VARCHAR(50) NOT NULL,"
                " target VARCHAR(255) NOT NULL, actor VARCHAR(200) NOT NULL,"
                " counts TEXT NOT NULL, force_parallel TINYINT(1) NOT NULL DEFAULT 0,"
                " created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    await session.commit()
    with contextlib.suppress(Exception):
        await session.execute(
            text("DELETE FROM tiqora_settings WHERE `key` LIKE 'schema.ownership%'")
        )
        await session.execute(text("DELETE FROM tiqora_gdpr_audit"))
        await session.commit()


async def _insert_customer(session: AsyncSession, *, login: str, customer_id: str) -> None:
    async with session.begin():
        session.add(
            CustomerUser(
                login=login,
                email=login,
                customer_id=customer_id,
                first_name="Target",
                last_name="Person",
                phone="+49 30 1234567",
                valid_id=1,
                create_time=NOW,
                create_by=1,
                change_time=NOW,
                change_by=1,
            )
        )


async def _insert_ticket(session: AsyncSession, *, customer_user_id: str) -> int:
    t = Ticket(
        tn="2026010112345678",
        title="Test",
        queue_id=1,
        ticket_lock_id=1,
        user_id=1,
        responsible_user_id=1,
        ticket_priority_id=3,
        ticket_state_id=2,
        customer_id="ANON-GDPR",
        customer_user_id=customer_user_id,
        timeout=0,
        until_time=0,
        escalation_time=0,
        escalation_update_time=0,
        escalation_response_time=0,
        escalation_solution_time=0,
        create_time=NOW,
        create_by=1,
        change_time=NOW,
        change_by=1,
    )
    async with session.begin():
        session.add(t)
        await session.flush()
        ticket_id = t.id
    return ticket_id


async def _insert_article(
    session: AsyncSession, *, ticket_id: int, from_addr: str, body: str
) -> None:
    async with session.begin():
        await session.execute(
            text(
                "INSERT INTO article (ticket_id, article_sender_type_id,"
                " communication_channel_id, is_visible_for_customer,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:ticket_id, 2, 1, 1, :ct, 1, :ct, 1)"
            ),
            {"ticket_id": ticket_id, "ct": NOW},
        )
        article_id = (
            await session.execute(
                text("SELECT MAX(id) FROM article WHERE ticket_id = :tid"), {"tid": ticket_id}
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO article_data_mime (article_id, a_from, a_to, a_body,"
                " incoming_time, create_time, create_by, change_time, change_by)"
                " VALUES (:aid, :a_from, 'agent@tiqora.test', :a_body, 0, :ct, 1, :ct, 1)"
            ),
            {"aid": article_id, "a_from": from_addr, "a_body": body, "ct": NOW},
        )


@pytest.mark.db
@pytest.mark.asyncio
async def test_anonymize_customer_refuses_without_ownership_or_force(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _seed_tiqora_tables(session)
        await _insert_customer(session, login="gdpr.refuse@example.com", customer_id="ANON-GDPR")

    with pytest.raises(GdprRefusedError):
        await anonymize_customer(
            factory,
            Settings(),
            login="gdpr.refuse@example.com",
        )
    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_anonymize_customer_force_parallel_scrubs_pii(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    login = "gdpr.scrub@example.com"
    async with factory() as session:
        await _seed_tiqora_tables(session)
        await _insert_customer(session, login=login, customer_id="ANON-GDPR2")

    result = await anonymize_customer(
        factory,
        Settings(),
        login=login,
        seed=5,
        force_parallel=True,
        actor="test",
    )
    assert result.customer_users == 1

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT first_name, last_name, email, login, phone FROM customer_user"
                    " WHERE customer_id = 'ANON-GDPR2'"
                )
            )
        ).first()
        assert row is not None
        first_name, last_name, email, new_login, phone = row
        assert first_name != "Target"
        assert last_name != "Person"
        assert email != login
        assert new_login != login
        assert phone != "+49 30 1234567"

        audit = (
            await session.execute(
                text(
                    "SELECT action, target, force_parallel FROM tiqora_gdpr_audit"
                    " WHERE action='anonymize_customer'"
                )
            )
        ).first()
        assert audit is not None
        assert audit[1] == login
        assert bool(audit[2]) is True
    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_anonymize_customer_mapping_is_referentially_consistent(
    mariadb_znuny_url: str,
) -> None:
    """Same seed -> same login replacement, deterministically."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    login = "gdpr.consistent@example.com"
    async with factory() as session:
        await _seed_tiqora_tables(session)
        await _insert_customer(session, login=login, customer_id="ANON-GDPR3")

    await anonymize_customer(factory, Settings(), login=login, seed=99, force_parallel=True)

    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT login FROM customer_user WHERE customer_id = 'ANON-GDPR3'")
            )
        ).first()
        assert row is not None
        scrubbed_login = row[0]

    # Re-anonymizing the (already scrubbed) row with the same seed maps the
    # new login value deterministically -> same output on a second pass.
    await anonymize_customer(
        factory, Settings(), login=scrubbed_login, seed=99, force_parallel=True
    )
    async with factory() as session:
        row2 = (
            await session.execute(
                text("SELECT login FROM customer_user WHERE customer_id = 'ANON-GDPR3'")
            )
        ).first()
        assert row2 is not None

    from tiqora.domain.dev_anonymize import ValueMapper

    mapper = ValueMapper(seed=99)
    assert mapper.map_value(scrubbed_login, "login") == row2[0]
    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_anonymize_customer_scrubs_article_bodies_and_addresses(
    mariadb_znuny_url: str,
) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    login = "gdpr.article@example.com"
    async with factory() as session:
        await _seed_tiqora_tables(session)
        await _insert_customer(session, login=login, customer_id="ANON-GDPR4")
        ticket_id = await _insert_ticket(session, customer_user_id=login)
        await _insert_article(
            session,
            ticket_id=ticket_id,
            from_addr=f'"Target Person" <{login}>',
            body="Hello, this is Target Person writing about my account.",
        )

    result = await anonymize_customer(
        factory, Settings(), login=login, seed=11, force_parallel=True
    )
    assert result.tickets_touched == 1
    assert result.articles == 1

    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT a_from, a_body FROM article_data_mime am"
                    " JOIN article a ON a.id = am.article_id WHERE a.ticket_id = :tid"
                ),
                {"tid": ticket_id},
            )
        ).first()
        assert row is not None
        a_from, a_body = row
        assert login not in (a_from or "")
        assert "Target Person" not in (a_body or "")
    await engine.dispose()


@pytest.mark.db
@pytest.mark.asyncio
async def test_anonymize_customer_raises_when_login_not_found(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await _seed_tiqora_tables(session)
    with pytest.raises(CustomerNotFoundError):
        await anonymize_customer(
            factory, Settings(), login="does.not.exist@example.com", force_parallel=True
        )
    await engine.dispose()
