"""Tests for the phone/CTI note channel: logs inbound/outbound calls with the
correct PhoneCallCustomer/PhoneCallAgent history type, resolves caller
number -> customer_user, and appends to an explicit ticket_id when given."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.channels.phone.service import log_phone_call
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.znuny.sysconfig import SysConfig


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


async def _insert_customer_user(session: AsyncSession, login: str, phone: str) -> None:
    await session.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
            " phone, pw, valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:login, :email, :login, 'Test', 'Customer', :phone, 'x', 1,"
            " current_timestamp, 1, current_timestamp, 1)"
        ),
        {"login": login, "email": f"{login}@example.com", "phone": phone},
    )


async def _history_type_for_article(session: AsyncSession, article_id: int) -> str:
    row = (
        await session.execute(
            text(
                "SELECT ht.name FROM ticket_history th"
                " JOIN ticket_history_type ht ON ht.id = th.history_type_id"
                " WHERE th.article_id = :aid ORDER BY th.id DESC LIMIT 1"
            ),
            {"aid": article_id},
        )
    ).first()
    assert row is not None
    return str(row[0])


@pytest.mark.db
async def test_inbound_call_creates_ticket_with_phone_call_customer_history(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await _insert_customer_user(session, "phonetestuser1", "+49 30 5551234")
            await session.commit()

            sysconfig = SysConfig(session)
            result = await log_phone_call(
                session,
                factory,
                sysconfig,
                direction="inbound",
                caller_number="+493055551234",
                note="Customer called about a billing question.",
                ticket_id=None,
                user_id=1,
            )
            await session.commit()

            assert result.created is True
            history_type = await _history_type_for_article(session, result.article_id)
            assert history_type == "PhoneCallCustomer"
    finally:
        await engine.dispose()


@pytest.mark.db
async def test_outbound_call_appends_to_existing_ticket_with_agent_history(
    mariadb_znuny_url: str,
) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            sysconfig = SysConfig(session)
            first = await log_phone_call(
                session,
                factory,
                sysconfig,
                direction="inbound",
                caller_number="+493055559999",
                note="Initial inbound call.",
                ticket_id=None,
                user_id=1,
            )
            await session.commit()

            second = await log_phone_call(
                session,
                factory,
                sysconfig,
                direction="outbound",
                caller_number="+493055559999",
                note="Called back with an update.",
                ticket_id=first.ticket_id,
                user_id=1,
            )
            await session.commit()

            assert second.ticket_id == first.ticket_id
            assert second.created is False
            history_type = await _history_type_for_article(session, second.article_id)
            assert history_type == "PhoneCallAgent"
    finally:
        await engine.dispose()


async def test_log_phone_call_rejects_invalid_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        await log_phone_call(
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            direction="sideways",
            caller_number="123",
            note="x",
            ticket_id=1,
            user_id=1,
        )
