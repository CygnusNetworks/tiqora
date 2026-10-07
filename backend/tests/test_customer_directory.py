"""Customer directory: feature grants (agent / group / role / admin) and the
gated list, company picker and vCard exports. Own seed block 9860+."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin.common import CUSTOMER_USER_CACHE_TYPES
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.feature_grants import (
    CUSTOMER_DIRECTORY,
    CUSTOMER_EDIT,
    FeatureGrants,
    FeatureGrantService,
)

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
        conn.execute(text("DELETE FROM article WHERE id BETWEEN 9860 AND 9869"))
        conn.execute(text("DELETE FROM ticket WHERE id BETWEEN 9860 AND 9869"))
        # Written by the customer edit (Znuny cache signals).
        conn.execute(
            text("DELETE FROM tiqora_cache_invalidation WHERE cache_type IN :types").bindparams(
                bindparam("types", expanding=True)
            ),
            {"types": list(CUSTOMER_USER_CACHE_TYPES)},
        )
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


async def _grant(url: str, grants: FeatureGrants, feature: str = CUSTOMER_DIRECTORY) -> None:
    engine = create_async_engine(_async_url(url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await FeatureGrantService(session).set(feature, grants)
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


async def test_sort_orders(seeded: str) -> None:
    engine = create_engine(seeded)
    with engine.begin() as conn:
        # A contact without a company record, and distinct phone numbers.
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
                " phone, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES (:login, 'solo@custdir.example', 'CUSTDIR-SOLO', 'Sam', 'Adler',"
                " '0228 1', 1, :t, 1, :t, 1)"
            ),
            {"login": _PREFIX + "solo", "t": NOW},
        )
        for login, phone in (("laura", "0228 3"), ("anna", "0228 4"), ("kevin", "0228 2")):
            conn.execute(
                text("UPDATE customer_user SET phone = :p WHERE login = :l"),
                {"p": phone, "l": _PREFIX + login},
            )
    engine.dispose()

    client, http_engine = await _client(seeded, ROOT)

    async def names(**params: str) -> list[str]:
        resp = await client.get(
            "/api/v1/customer-directory", params={"search": "custdir", **params}
        )
        assert resp.status_code == 200, resp.text
        return [i["last_name"] for i in resp.json()["items"]]

    async with client:
        default = await names()
        company_asc = await names(sort="company")
        company_desc = await names(sort="company", order="desc")
        phone = await names(sort="phone")
        unknown = await names(sort="pw; drop", order="desc")
        admin = await client.get(
            "/api/v1/admin/customer-users", params={"search": "custdir", "sort": "company"}
        )
    await http_engine.dispose()

    assert default == ["Adler", "Berger", "Gomez", "Wu"]
    # By company *name* (Globex < Northwind), contacts without a company last.
    assert company_asc == ["Wu", "Berger", "Gomez", "Adler"]
    assert company_desc == ["Gomez", "Berger", "Wu", "Adler"]
    assert phone == ["Adler", "Wu", "Gomez", "Berger"]
    assert unknown == default
    assert admin.status_code == 200, admin.text
    assert [i["last_name"] for i in admin.json()["items"]] == company_asc


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


_EDIT_BODY = {
    "first_name": "Laura",
    "last_name": "Gomez-Ruiz",
    "email": "laura.new@custdir.example",
    "customer_id": "CUSTDIR-GX",
    "phone": "0228 555 0100",
    "mobile": "",
    "street": "Am Hof 1",
    "zip": "53113",
    "city": "Bonn",
    "comments": "",
}


async def test_edit_customer_needs_customer_edit_grant(seeded: str) -> None:
    # The directory grant alone is not enough to edit.
    await _grant(seeded, FeatureGrants(user_ids=[DIRECT]))
    client, engine = await _client(seeded, DIRECT)
    async with client:
        denied = await client.put(f"/api/v1/customers/{_PREFIX}laura", json=_EDIT_BODY)
        me = await client.get("/api/v1/auth/me")
    await engine.dispose()
    assert denied.status_code == 403
    assert me.json()["can_use_customer_directory"] is True
    assert me.json()["can_edit_customers"] is False


async def test_edit_customer_overwrites_and_clears(seeded: str) -> None:
    await _grant(seeded, FeatureGrants(user_ids=[DIRECT]), CUSTOMER_EDIT)
    client, engine = await _client(seeded, DIRECT)
    async with client:
        resp = await client.put(f"/api/v1/customers/{_PREFIX}laura", json=_EDIT_BODY)
        me = await client.get("/api/v1/auth/me")
        missing = await client.put("/api/v1/customers/custdir.nobody", json=_EDIT_BODY)
    await engine.dispose()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["last_name"] == "Gomez-Ruiz"
    assert body["email"] == "laura.new@custdir.example"
    assert body["customer_id"] == "CUSTDIR-GX"
    assert body["company_name"] == "Custdir Globex"
    assert body["city"] == "Bonn"
    # Empty optional fields clear the column (the seed had no mobile; phone replaced).
    assert body["phone"] == "0228 555 0100"
    assert body["mobile"] is None
    assert body["comments"] is None
    assert me.json()["can_edit_customers"] is True
    assert missing.status_code == 404


async def test_edit_customer_email_conflict(seeded: str) -> None:
    await _grant(seeded, FeatureGrants(user_ids=[DIRECT]), CUSTOMER_EDIT)
    client, engine = await _client(seeded, DIRECT)
    async with client:
        resp = await client.put(
            f"/api/v1/customers/{_PREFIX}laura",
            json={**_EDIT_BODY, "email": "ANNA@custdir.example"},
        )
        same = await client.put(
            f"/api/v1/customers/{_PREFIX}laura",
            json={**_EDIT_BODY, "email": "Laura@custdir.example"},
        )
    await engine.dispose()
    assert resp.status_code == 409
    assert resp.json()["detail"]["login"] == _PREFIX + "anna"
    # Keeping one's own address (other case) is not a conflict.
    assert same.status_code == 200, same.text


def _seed_activity(url: str) -> None:
    """Tickets with articles written by DIRECT (and one by OUTSIDER).

    channel ids follow Znuny's initial data: 1 Email, 2 Phone, 3 Internal.
    """
    now = datetime.now()
    tickets = (
        (9860, _PREFIX + "laura", "CUSTDIR-NW"),
        (9861, _PREFIX + "laura", "CUSTDIR-NW"),
        (9862, _PREFIX + "anna", "CUSTDIR-NW"),
        (9863, _PREFIX + "kevin", "CUSTDIR-GX"),
        (9864, _PREFIX + "old", "CUSTDIR-NW"),
        (9865, _PREFIX + "laura", "CUSTDIR-NW"),
    )
    articles = (
        # (article, ticket, author, channel, age)
        (9860, 9860, DIRECT, 1, timedelta(days=3)),
        (9861, 9861, DIRECT, 2, timedelta(days=1)),
        (9862, 9862, DIRECT, 1, timedelta(hours=1)),
        (9863, 9863, OUTSIDER, 1, timedelta(minutes=5)),
        (9864, 9864, DIRECT, 1, timedelta(minutes=10)),
        (9865, 9865, DIRECT, 3, timedelta(days=200)),
    )
    engine = create_engine(url)
    with engine.begin() as conn:
        for tid, login, cid in tickets:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'Custdir', 1, 1, 1, 1, 1, 3, 4, :cid, :login,"
                    " 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2024060198{tid}", "cid": cid, "login": login, "t": NOW},
            )
        for aid, tid, author, channel, age in articles:
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " search_index_needs_rebuild, create_time, create_by, change_time,"
                    " change_by) VALUES (:id, :tid, 1, :ch, 1, 0, :t, :by, :t, :by)"
                ),
                {"id": aid, "tid": tid, "ch": channel, "t": now - age, "by": author},
            )
    engine.dispose()


async def test_shortlist_recent_and_frequent(seeded: str) -> None:
    _seed_activity(seeded)
    await _grant(seeded, FeatureGrants(user_ids=[DIRECT, OUTSIDER]))
    client, engine = await _client(seeded, DIRECT)
    async with client:
        resp = await client.get("/api/v1/customer-directory/shortlist")
    await engine.dispose()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Kevin's article is OUTSIDER's, Olga is invalid, the 200-day-old article is out.
    assert [e["login"] for e in body["recent"]] == [_PREFIX + "anna", _PREFIX + "laura"]
    assert [e["login"] for e in body["frequent"]] == [_PREFIX + "laura", _PREFIX + "anna"]
    laura = body["frequent"][0]
    assert laura["ticket_count"] == 2
    assert laura["last_channel"] == "Phone"
    assert laura["company_name"] == "Custdir Northwind"
    assert body["recent"][0]["last_channel"] == "Email"


async def test_shortlist_empty_and_forbidden(seeded: str) -> None:
    await _grant(seeded, FeatureGrants(user_ids=[DIRECT]))
    client, engine = await _client(seeded, DIRECT)
    outsider, engine2 = await _client(seeded, OUTSIDER)
    async with client, outsider:
        empty = await client.get("/api/v1/customer-directory/shortlist")
        denied = await outsider.get("/api/v1/customer-directory/shortlist")
    await engine.dispose()
    await engine2.dispose()
    assert empty.json() == {"recent": [], "frequent": []}
    assert denied.status_code == 403


async def test_company_detail(seeded: str) -> None:
    await _grant(seeded, FeatureGrants(user_ids=[DIRECT]))
    client, engine = await _client(seeded, DIRECT)
    async with client:
        found = await client.get("/api/v1/customer-directory/companies/CUSTDIR-NW")
        missing = await client.get("/api/v1/customer-directory/companies/CUSTDIR-NOPE")
    await engine.dispose()
    assert found.status_code == 200, found.text
    assert found.json()["name"] == "Custdir Northwind"
    # Laura and Anna; Olga is invalid.
    assert found.json()["contact_count"] == 2
    assert missing.status_code == 404
