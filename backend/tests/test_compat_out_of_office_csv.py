"""OutOfOffice accepts OutOfOfficeEntriesCSVString on the canonical route (both dialects).

Seed ids live in the band 8990-8999; every row is deleted again after the module.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.compat.operations import op_out_of_office
from tiqora.domain.auth import SessionStore

pytestmark = pytest.mark.db

SESSION = "GI8990ADMIN"
_SEEDED: list[str] = []

_CLEANUP = (
    "DELETE FROM user_preferences WHERE user_id IN (8990, 8991, 8992)",
    "DELETE FROM sessions WHERE session_id LIKE 'GI8990%'",
    "DELETE FROM group_user WHERE user_id = 8990",
    "DELETE FROM permission_groups WHERE id = 8990",
    "DELETE FROM users WHERE id IN (8990, 8991, 8992)",
)

CSV = (
    "UserLogin,StartDate,EndDate,OutOfOffice\n"
    "gi8991.agent,01.03.2026 00:00:00,15.03.2026 23:59:59,yes\n"
    "gi8992.agent,2026-04-01,2026-04-02,no\n"
)


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt in _CLEANUP:
                conn.execute(text(stmt))
        engine.dispose()
    _SEEDED.clear()


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed(sync_url: str) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    now = datetime(2024, 6, 1, 12, 0, 0)
    stamp = str(int(time.time()))
    with engine.begin() as conn:
        for stmt in _CLEANUP:
            conn.execute(text(stmt))
        for uid, login in ((8990, "gi8990.admin"), (8991, "gi8991.agent"), (8992, "gi8992.agent")):
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', 'Gi', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "t": now},
            )
        conn.execute(
            text(
                "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                " VALUES (8992, 'UserEmail', 'gi8992@example.test')"
            )
        )
        group_id = conn.execute(
            text("SELECT id FROM permission_groups WHERE name = 'admin'")
        ).scalar()
        if group_id is None:
            group_id = 8990
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (8990, 'admin', 1, :t, 1, :t, 1)"
                ),
                {"t": now},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (8990, :g, 'rw', :t, 1, :t, 1)"
            ),
            {"g": group_id, "t": now},
        )
        for key, val in {
            "UserID": "8990",
            "UserLogin": "gi8990.admin",
            "UserType": "User",
            "UserLastRequest": stamp,
            "UserSessionStart": stamp,
        }.items():
            conn.execute(
                text(
                    "INSERT INTO sessions (session_id, data_key, data_value, serialized)"
                    " VALUES (:sid, :k, :v, 0)"
                ),
                {"sid": SESSION, "k": key, "v": val},
            )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
def sync_url(request: pytest.FixtureRequest) -> str:
    url: str = request.getfixturevalue(request.param)
    _seed(url)
    return url


class _FakeRedis:
    async def get(self, key: str) -> str | None:
        return None


class _FakeSettings:
    session_ttl_seconds = 86400
    session_cookie_name = "tiqora_session"


async def _post(sync_url: str, data: dict[str, Any]) -> dict[str, Any]:
    engine = create_async_engine(_async_url(sync_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionStore(_FakeRedis(), _FakeSettings())  # type: ignore[arg-type]
    try:
        async with factory() as session:
            return await op_out_of_office({"SessionID": SESSION, **data}, session, store)
    finally:
        await engine.dispose()


def _prefs(sync_url: str, uid: int) -> dict[str, str]:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT preferences_key, preferences_value FROM user_preferences WHERE user_id=:u"
            ),
            {"u": uid},
        ).all()
    engine.dispose()
    return {
        str(k): (bytes(v).decode() if isinstance(v, (bytes, bytearray, memoryview)) else str(v))
        for k, v in rows
    }


async def test_out_of_office_csv_on_canonical_route(sync_url: str) -> None:
    res = await _post(sync_url, {"OutOfOfficeEntriesCSVString": CSV})
    assert "Error" not in res, res
    assert res == {"Success": 1}  # OutOfOffice.pm:260-264
    on, off = _prefs(sync_url, 8991), _prefs(sync_url, 8992)
    assert on["OutOfOffice"] == "1"
    assert (on["OutOfOfficeStartYear"], on["OutOfOfficeStartMonth"], on["OutOfOfficeStartDay"]) == (
        "2026",
        "03",
        "01",
    )
    assert (on["OutOfOfficeEndYear"], on["OutOfOfficeEndMonth"], on["OutOfOfficeEndDay"]) == (
        "2026",
        "03",
        "15",
    )
    assert off["OutOfOffice"] == "0"
    assert off["OutOfOfficeStartDay"] == "01"
    assert off["OutOfOfficeEndDay"] == "02"


async def test_explicit_entries_win_over_csv_string(sync_url: str) -> None:
    res = await _post(
        sync_url,
        {
            "OutOfOfficeEntriesCSVString": "bogus",
            "OutOfOfficeEntries": [{"UserLogin": "gi8991.agent", "OutOfOffice": 0}],
        },
    )
    assert "Error" not in res, res
    assert res == {"Success": 1}
    assert _prefs(sync_url, 8991)["OutOfOffice"] == "0"


async def test_bad_csv_is_wrong_input_structure(sync_url: str) -> None:
    res = await _post(sync_url, {"OutOfOfficeEntriesCSVString": "UserLogin\n"})
    assert res["Error"]["ErrorCode"] == "OutOfOffice.WrongInputStructure"
    assert "OutOfOfficeEntriesCSVString" in res["Error"]["ErrorMessage"]


async def test_csv_row_resolved_by_email(sync_url: str) -> None:
    csv = "UserEmail,StartDate,EndDate,OutOfOffice\ngi8992@example.test,2026-05-01,2026-05-03,yes\n"
    res = await _post(sync_url, {"OutOfOfficeEntriesCSVString": csv})
    assert "Error" not in res, res
    assert res == {"Success": 1}
    prefs = _prefs(sync_url, 8992)
    assert prefs["OutOfOffice"] == "1"
    assert (prefs["OutOfOfficeStartMonth"], prefs["OutOfOfficeEndDay"]) == ("05", "03")
