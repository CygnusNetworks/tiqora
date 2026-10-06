"""Customer directory: feature grants (agent / group / role / admin) and the
gated list, company picker and vCard exports. Own seed block 9860+."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.feature_grants import CUSTOMER_DIRECTORY, FeatureGrants, FeatureGrantService

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)
ROOT = 1  # root@localhost — rw on "admin" in Znuny's initial data
DIRECT = 9860  # granted individually
GROUPED = 9861  # member (only "create") of GROUP
ROLED = 9862  # holds ROLE
OUTSIDER = 9863  # nothing
INVALID = 9864  # granted individually but invalid
INVALID_ROLE_USER = 9865  # holds INVALID_ROLE only
GROUP = 9860
ROLE = 9860
INVALID_ROLE = 9861
_USERS = (DIRECT, GROUPED, ROLED, OUTSIDER, INVALID, INVALID_ROLE_USER)
_PREFIX = "custdir."


def _async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        conn.execute(text("DELETE FROM tiqora_feature_grant"))
        conn.execute(text("DELETE FROM customer_user WHERE login LIKE :p"), {"p": _PREFIX + "%"})
        conn.execute(
            text("DELETE FROM customer_company WHERE customer_id LIKE 'CUSTDIR%'"),
        )
        conn.execute(
            text("DELETE FROM role_user WHERE role_id IN (:a, :b)"), {"a": ROLE, "b": INVALID_ROLE}
        )
        conn.execute(text("DELETE FROM roles WHERE id IN (:a, :b)"), {"a": ROLE, "b": INVALID_ROLE})
        conn.execute(text("DELETE FROM group_user WHERE group_id = :g"), {"g": GROUP})
        conn.execute(text("DELETE FROM permission_groups WHERE id = :g"), {"g": GROUP})
        conn.execute(text(f"DELETE FROM users WHERE id IN ({', '.join(map(str, _USERS))})"))
    engine.dispose()


def _seed(url: str) -> None:
    _cleanup(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        for uid in _USERS:
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', 'Dir', 'Agent', :valid, :t, 1, :t, 1)"
                ),
                {
                    "id": uid,
                    "login": f"custdir.agent{uid}",
                    "valid": 2 if uid == INVALID else 1,
                    "t": NOW,
                },
            )
        conn.execute(
            text(
                "INSERT INTO permission_groups (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:g, 'custdir-group', 1, :t, 1, :t, 1)"
            ),
            {"g": GROUP, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, 'create', :t, 1, :t, 1)"
            ),
            {"u": GROUPED, "g": GROUP, "t": NOW},
        )
        for rid, name, valid in ((ROLE, "custdir-role", 1), (INVALID_ROLE, "custdir-old", 2)):
            conn.execute(
                text(
                    "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:r, :n, :v, :t, 1, :t, 1)"
                ),
                {"r": rid, "n": name, "v": valid, "t": NOW},
            )
        for uid, rid in ((ROLED, ROLE), (INVALID_ROLE_USER, INVALID_ROLE)):
            conn.execute(
                text(
                    "INSERT INTO role_user (user_id, role_id, create_time, create_by,"
                    " change_time, change_by) VALUES (:u, :r, :t, 1, :t, 1)"
                ),
                {"u": uid, "r": rid, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO customer_company (customer_id, name, valid_id, create_time,"
                " create_by, change_time, change_by)"
                " VALUES ('CUSTDIR-NW', 'Custdir Northwind', 1, :t, 1, :t, 1),"
                "        ('CUSTDIR-GX', 'Custdir Globex', 1, :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        for login, first, last, cid, valid in (
            ("laura", "Laura", "Gomez", "CUSTDIR-NW", 1),
            ("anna", "Anna", "Berger", "CUSTDIR-NW", 1),
            ("old", "Olga", "Alt", "CUSTDIR-NW", 2),
            ("kevin", "Kevin", "Wu", "CUSTDIR-GX", 1),
        ):
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name,"
                    " last_name, phone, valid_id, create_time, create_by, change_time,"
                    " change_by) VALUES (:login, :email, :cid, :first, :last, '0228 4012',"
                    " :valid, :t, 1, :t, 1)"
                ),
                {
                    "login": _PREFIX + login,
                    "email": f"{login}@custdir.example",
                    "cid": cid,
                    "first": first,
                    "last": last,
                    "valid": valid,
                    "t": NOW,
                },
            )
    engine.dispose()


@pytest.fixture
def seeded(mariadb_znuny_url: str) -> Iterator[str]:
    _seed(mariadb_znuny_url)
    yield mariadb_znuny_url
    _cleanup(mariadb_znuny_url)


async def _grant(url: str, grants: FeatureGrants) -> None:
    engine = create_async_engine(_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await FeatureGrantService(session).set(CUSTOMER_DIRECTORY, grants)
        await session.commit()
    await engine.dispose()


async def _may_use(url: str, user_id: int) -> bool:
    engine = create_async_engine(_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        result = await FeatureGrantService(session).may_use(user_id, CUSTOMER_DIRECTORY)
    await engine.dispose()
    return result


async def _client(url: str, user_id: int) -> tuple[Any, Any]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    user = AuthenticatedUser(
        id=user_id, login=f"u{user_id}", first_name="Dir", last_name="Agent", auth_method="session"
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


async def test_no_grants_is_admin_only(seeded: str) -> None:
    assert await _may_use(seeded, ROOT) is True
    for uid in _USERS:
        assert await _may_use(seeded, uid) is False, uid


async def test_user_group_and_role_grants(seeded: str) -> None:
    await _grant(
        seeded,
        FeatureGrants(user_ids=[DIRECT, INVALID], group_ids=[GROUP], role_ids=[ROLE, INVALID_ROLE]),
    )
    assert await _may_use(seeded, DIRECT) is True
    # Any membership counts, not only rw ("alle Mitglieder").
    assert await _may_use(seeded, GROUPED) is True
    assert await _may_use(seeded, ROLED) is True
    assert await _may_use(seeded, OUTSIDER) is False
    # Invalid agent / invalid role never grant.
    assert await _may_use(seeded, INVALID) is False
    assert await _may_use(seeded, INVALID_ROLE_USER) is False


async def test_set_replaces_and_dedupes(seeded: str) -> None:
    await _grant(seeded, FeatureGrants(user_ids=[DIRECT, DIRECT], role_ids=[ROLE]))
    await _grant(seeded, FeatureGrants(group_ids=[GROUP]))
    engine = create_async_engine(_async_url(seeded))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        grants = await FeatureGrantService(session).get(CUSTOMER_DIRECTORY)
    await engine.dispose()
    assert grants == FeatureGrants(user_ids=[], group_ids=[GROUP], role_ids=[])


async def test_routes_forbidden_without_grant(seeded: str) -> None:
    client, engine = await _client(seeded, OUTSIDER)
    async with client:
        responses = [
            await client.get("/api/v1/customer-directory"),
            await client.get("/api/v1/customer-directory/companies"),
            await client.get("/api/v1/customer-directory/vcards"),
            await client.get("/api/v1/customers/companies/CUSTDIR-NW/vcards"),
        ]
        single = await client.get(f"/api/v1/customers/{_PREFIX}laura/vcard")
        me = await client.get("/api/v1/auth/me")
    await engine.dispose()
    assert [r.status_code for r in responses] == [403, 403, 403, 403]
    # One contact's vCard stays ticket work for every agent.
    assert single.status_code == 200
    assert me.json()["can_use_customer_directory"] is False


async def test_list_search_company_and_me_flag(seeded: str) -> None:
    await _grant(seeded, FeatureGrants(role_ids=[ROLE]))
    client, engine = await _client(seeded, ROLED)
    async with client:
        all_ = await client.get("/api/v1/customer-directory", params={"search": "custdir"})
        two_words = await client.get(
            "/api/v1/customer-directory", params={"search": "laura northwind"}
        )
        by_company = await client.get(
            "/api/v1/customer-directory", params={"customer_id": "CUSTDIR-NW", "valid": "all"}
        )
        companies = await client.get(
            "/api/v1/customer-directory/companies", params={"search": "custdir"}
        )
        me = await client.get("/api/v1/auth/me")
    await engine.dispose()
    assert all_.status_code == 200, all_.text
    body = all_.json()
    # Name order: Berger, Gomez, Wu (invalid Alt hidden by default).
    assert [i["last_name"] for i in body["items"]] == ["Berger", "Gomez", "Wu"]
    assert body["total"] == 3
    assert body["items"][0]["company_name"] == "Custdir Northwind"
    assert [i["login"] for i in two_words.json()["items"]] == [_PREFIX + "laura"]
    assert by_company.json()["total"] == 3
    assert {c["customer_id"] for c in companies.json()} == {"CUSTDIR-GX", "CUSTDIR-NW"}
    assert me.json()["can_use_customer_directory"] is True


async def test_vcard_exports(seeded: str) -> None:
    await _grant(seeded, FeatureGrants(user_ids=[DIRECT]))
    client, engine = await _client(seeded, DIRECT)
    async with client:
        selection = await client.post(
            "/api/v1/customer-directory/vcards",
            json={"logins": [_PREFIX + "kevin", _PREFIX + "laura"]},
        )
        company = await client.get(
            "/api/v1/customer-directory/vcards", params={"customer_id": "CUSTDIR-NW"}
        )
        empty = await client.get(
            "/api/v1/customer-directory/vcards", params={"search": "nobody-xyz"}
        )
        legacy = await client.get("/api/v1/customers/companies/CUSTDIR-NW/vcards")
    await engine.dispose()
    assert selection.status_code == 200, selection.text
    assert selection.text.count("BEGIN:VCARD") == 2
    assert selection.text.index("FN:Laura Gomez") < selection.text.index("FN:Kevin Wu")
    assert "ORG:Custdir Globex" in selection.text
    assert 'filename="kontakte.vcf"' in selection.headers["content-disposition"]
    assert company.text.count("BEGIN:VCARD") == 2  # invalid Olga excluded
    assert 'filename="Custdir Northwind.vcf"' in company.headers["content-disposition"]
    assert empty.status_code == 404
    assert legacy.status_code == 200


async def test_export_cap(seeded: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.api.v1 import customer_directory

    monkeypatch.setattr(customer_directory, "EXPORT_MAX", 2)
    client, engine = await _client(seeded, ROOT)
    async with client:
        too_many = await client.get(
            "/api/v1/customer-directory/vcards", params={"search": "custdir"}
        )
        ok = await client.get(
            "/api/v1/customer-directory/vcards", params={"customer_id": "CUSTDIR-NW"}
        )
        too_many_selected = await client.post(
            "/api/v1/customer-directory/vcards",
            json={"logins": [_PREFIX + "laura", _PREFIX + "anna", _PREFIX + "kevin"]},
        )
    await engine.dispose()
    assert too_many.status_code == 422
    assert ok.status_code == 200
    assert too_many_selected.status_code == 422
