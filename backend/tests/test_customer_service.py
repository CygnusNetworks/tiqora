"""DB tests for ``CustomerService.get_by_login`` — the read-only
customer_user/customer_company lookup used for ticket display."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tiqora.db.legacy.customer import CustomerCompany, CustomerUser
from tiqora.domain.customer_service import CustomerService

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _cleanup(
    engine: AsyncEngine, *, logins: Sequence[str] = (), companies: Sequence[str] = ()
) -> None:
    """Drop this file's seed rows.

    The DB container is session-scoped, so committed rows outlive the test and
    leak into every later file. ``customer_id="no-such-company"`` in particular
    is test_gdpr_erasure.py's negative-control sentinel — leaving it behind
    turns that test's ``count == 0`` into ``count == 1``.
    """
    async with engine.begin() as conn:
        if logins:
            await conn.execute(delete(CustomerUser).where(CustomerUser.login.in_(list(logins))))
        if companies:
            await conn.execute(
                delete(CustomerCompany).where(CustomerCompany.customer_id.in_(list(companies)))
            )


async def test_get_by_login_returns_none_when_missing(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            service = CustomerService(session)
            result = await service.get_by_login("no-such-user")
            assert result is None
    finally:
        await engine.dispose()


async def test_get_by_login_without_company(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ts = _now()
            session.add(
                CustomerUser(
                    login="jdoe",
                    email="jdoe@example.com",
                    customer_id="",
                    first_name="Jane",
                    last_name="Doe",
                    title="Ms.",
                    phone="+1 555 0100",
                    valid_id=1,
                    create_time=ts,
                    create_by=1,
                    change_time=ts,
                    change_by=1,
                )
            )
            await session.commit()

            service = CustomerService(session)
            result = await service.get_by_login("jdoe")
            assert result is not None
            assert result.login == "jdoe"
            assert result.email == "jdoe@example.com"
            assert result.first_name == "Jane"
            assert result.last_name == "Doe"
            assert result.title == "Ms."
            assert result.phone == "+1 555 0100"
            assert result.company_name is None
    finally:
        await _cleanup(engine, logins=["jdoe"])
        await engine.dispose()


async def test_get_by_login_resolves_company_name(mariadb_znuny_url: str) -> None:
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ts = _now()
            session.add(
                CustomerCompany(
                    customer_id="acme",
                    name="Acme Corp",
                    valid_id=1,
                    create_time=ts,
                    create_by=1,
                    change_time=ts,
                    change_by=1,
                )
            )
            session.add(
                CustomerUser(
                    login="wcoyote",
                    email="wcoyote@acme.example",
                    customer_id="acme",
                    first_name="Wile E.",
                    last_name="Coyote",
                    valid_id=1,
                    create_time=ts,
                    create_by=1,
                    change_time=ts,
                    change_by=1,
                )
            )
            await session.commit()

            service = CustomerService(session)
            result = await service.get_by_login("wcoyote")
            assert result is not None
            assert result.customer_id == "acme"
            assert result.company_name == "Acme Corp"
    finally:
        await _cleanup(engine, logins=["wcoyote"], companies=["acme"])
        await engine.dispose()


async def test_get_by_login_missing_company_leaves_company_name_none(
    mariadb_znuny_url: str,
) -> None:
    """The customer_user references a customer_id that has no matching
    customer_company row — get_by_login must not raise, just leave
    company_name unset."""
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ts = _now()
            session.add(
                CustomerUser(
                    login="orphan",
                    email="orphan@example.com",
                    customer_id="no-such-company",
                    first_name="Orphan",
                    last_name="User",
                    valid_id=1,
                    create_time=ts,
                    create_by=1,
                    change_time=ts,
                    change_by=1,
                )
            )
            await session.commit()

            service = CustomerService(session)
            result = await service.get_by_login("orphan")
            assert result is not None
            assert result.company_name is None
    finally:
        await _cleanup(engine, logins=["orphan"])
        await engine.dispose()
