"""MariaDB zero-date coercion for legacy DateTime columns (admin list 500 fix)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import customers as admin_customers
from tiqora.api.v1.admin.pagination import ListParams
from tiqora.db.legacy.types import LegacyDateTime, _is_zero_date
from tiqora.domain.auth import AuthenticatedUser
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def test_is_zero_date_recognises_mysql_forms() -> None:
    assert _is_zero_date("0000-00-00 00:00:00")
    assert _is_zero_date("0000-00-00")
    assert _is_zero_date(b"0000-00-00 00:00:00".decode())  # str form
    assert not _is_zero_date(None)
    assert not _is_zero_date(datetime(2024, 1, 1, 0, 0, 0))
    assert not _is_zero_date("2024-01-01 00:00:00")


def test_legacy_datetime_result_processor_coerces_zero_to_none() -> None:
    col = LegacyDateTime()
    dialect = MagicMock()
    assert col.process_result_value("0000-00-00 00:00:00", dialect) is None
    assert col.process_result_value("0000-00-00", dialect) is None
    assert col.process_result_value(None, dialect) is None
    ts = datetime(2024, 6, 1, 12, 0, 0)
    assert col.process_result_value(ts, dialect) is ts


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _seed_zero_date_customer(sync_url: str, *, ns: str) -> dict[str, Any]:
    """Insert a customer_user row with MariaDB zero-dates (prod failure mode).

    Relaxes sql_mode for the session so NO_ZERO_DATE does not reject the insert —
    real Znuny DBs already contain these values historically.
    """
    login = f"zerodate.{ns}@example.com"
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("SET SESSION sql_mode = ''"))
        conn.execute(
            text(
                "INSERT INTO customer_user ("
                " login, email, customer_id, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                # valid_id=2 so we do not pollute default "valid" admin list
                # totals in sibling tests that share the session-scoped container.
                " VALUES ("
                " :login, :email, :cid, :pw, 'Zero', 'Date', 2,"
                " '0000-00-00 00:00:00', 1, '0000-00-00 00:00:00', 1)"
            ),
            {
                "login": login,
                "email": login,
                "cid": f"ZERO-{ns}",
                "pw": hash_password("x"),
            },
        )
        row_id = conn.execute(
            text("SELECT id FROM customer_user WHERE login = :login"),
            {"login": login},
        ).scalar_one()
    engine.dispose()
    return {"id": int(row_id), "login": login}


@pytest.mark.asyncio
async def test_admin_customer_users_list_coerces_zero_dates(
    mariadb_znuny_url: str,
) -> None:
    """GET-equivalent list must return 200-shaped Page with null timestamps,
    not raise Pydantic validation on MariaDB zero-dates (prod 500)."""
    import uuid

    ns = uuid.uuid4().hex[:8]
    seeded = _seed_zero_date_customer(mariadb_znuny_url, ns=ns)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    admin = AuthenticatedUser(
        id=1,
        login="root@localhost",
        first_name="Admin",
        last_name="Znuny",
        auth_method="session",
    )

    async with factory() as session:
        page = await admin_customers.list_customer_users(
            admin,
            session,
            ListParams(page=1, page_size=500, valid="all"),
        )
        # Page.model_validate already ran — would 500 if create_time were a string.
        match = [item for item in page.items if item.login == seeded["login"]]
        assert len(match) == 1, "zero-date row must appear in admin list"
        item = match[0]
        assert item.create_time is None
        assert item.change_time is None
        assert item.id == seeded["id"]
        assert item.first_name == "Zero"

        # Single-get path uses the same out-schema / ORM mapping.
        one = await admin_customers.get_customer_user(seeded["id"], admin, session)
        assert one.create_time is None
        assert one.change_time is None

    await engine.dispose()
