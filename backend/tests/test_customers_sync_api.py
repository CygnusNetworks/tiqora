"""Agent customer API used by the AI secretary: create without e-mail,
companies, fill-only PATCH. Own seed block 9400+ (session-scoped MariaDB)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
_UID = 9401
_LOGIN = "custsync.agent"
_PREFIX = "custsync."


def _mysql_async(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE :p"), {"p": _PREFIX + "%"})
        conn.execute(
            text("DELETE FROM customer_company WHERE customer_id LIKE :p"), {"p": "CUSTSYNC%"}
        )
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": _UID})
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Sync', 'Agent', 1, :t, 1, :t, 1)"
            ),
            {"id": _UID, "login": _LOGIN, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " phone, mobile, comments, valid_id, create_time, create_by, change_time,"
                " change_by)"
                " VALUES (:login, :email, 'CUSTSYNC-OLD', '', 'Bestand', '', NULL, NULL, 1,"
                " :t, 1, :t, 1)"
            ),
            {"login": _PREFIX + "bestand", "email": "Bestand@CustSync.Example", "t": NOW},
        )
    engine.dispose()


def _cleanup(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE :p"), {"p": _PREFIX + "%"})
        conn.execute(
            text("DELETE FROM customer_company WHERE customer_id LIKE :p"), {"p": "CUSTSYNC%"}
        )
        conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": _UID})
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_after(mariadb_znuny_url: str) -> Iterator[None]:
    yield
    _cleanup(mariadb_znuny_url)


async def _client(url: str) -> tuple[Any, Any]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_mysql_async(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    user = AuthenticatedUser(
        id=_UID, login=_LOGIN, first_name="Sync", last_name="Agent", auth_method="session"
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


def _row(url: str, login: str) -> dict[str, Any]:
    engine = create_engine(url)
    with engine.connect() as conn:
        row = (
            conn.execute(text("SELECT * FROM customer_user WHERE login = :l"), {"l": login})
            .mappings()
            .one()
        )
    engine.dispose()
    return dict(row)


async def test_create_without_email_and_first_name(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    client, engine = await _client(mariadb_znuny_url)
    async with client:
        resp = await client.post(
            "/api/v1/customers",
            json={
                "login": _PREFIX + "tel-491717630944",
                "last_name": "Kettler",
                "customer_id": _PREFIX + "tel-491717630944",
                "mobile": "+491717630944",
                "comments": "Angelegt vom KI-Sekretariat, ungeprüft",
            },
        )
    await engine.dispose()
    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == ""
    row = _row(mariadb_znuny_url, _PREFIX + "tel-491717630944")
    assert row["first_name"] == ""
    assert row["mobile"] == "+491717630944"
    assert row["comments"] == "Angelegt vom KI-Sekretariat, ungeprüft"


async def test_create_with_existing_email_is_409_with_login(mariadb_znuny_url: str) -> None:
    _seed(mariadb_znuny_url)
    client, engine = await _client(mariadb_znuny_url)
    async with client:
        resp = await client.post(
            "/api/v1/customers",
            json={
                "login": _PREFIX + "neu",
                "email": "bestand@custsync.example",
                "last_name": "Neu",
                "customer_id": "CUSTSYNC-NEU",
            },
        )
    await engine.dispose()
    assert resp.status_code == 409
    assert resp.json()["detail"]["login"] == _PREFIX + "bestand"
