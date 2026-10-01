"""DB integration tests for ``GET /api/v1/integrations/customer-tickets``.

Uses a dedicated id block (94100-94199) so it coexists with the other
session-scoped MariaDB fixture consumers (see ``test_ticket_customer_link_api.py``
for the convention). The agent has ``ro`` on queue A only; queue B holds a
matching ticket the agent must not see.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import tiqora.ai.models  # noqa: F401  (registers tiqora_ai_* tables on TiqoraBase)
from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2026, 9, 1, 12, 0, 0)

_UID_AGENT = 94101
_LOGIN_AGENT = "custtickets.api.agent"
_GROUP_A = 94130
_GROUP_B = 94131
_QUEUE_A = 94100
_QUEUE_B = 94101
_URL = "/api/v1/integrations/customer-tickets"

# id: (customer_user_id, queue, archive_flag, state_id, create_time, title)
_TICKETS: dict[int, tuple[str, int, int, int, datetime, str]] = {
    94101: ("z50test", _QUEUE_A, 0, 4, datetime(2026, 9, 1, 8, 0), "Störungsmeldung Internet"),
    94102: ("z50test#1", _QUEUE_A, 1, 2, datetime(2026, 9, 2, 8, 0), "Rückfrage Anna Kellbach"),
    94103: ("z50test#3", _QUEUE_A, 0, 4, datetime(2026, 9, 3, 8, 0), "PKZ 900002"),
    94109: ("z50test#3", _QUEUE_A, 0, 4, datetime(2026, 9, 3, 8, 0), "Frage von Bernd"),
    94104: ("z50test2", _QUEUE_A, 0, 4, datetime(2026, 9, 4, 8, 0), "Other customer"),
    94105: ("xz50test", _QUEUE_A, 0, 4, datetime(2026, 9, 5, 8, 0), "Other customer"),
    94106: ("z50test#3", _QUEUE_B, 0, 4, datetime(2026, 9, 6, 8, 0), "Hidden queue"),
    94107: ("z5%", _QUEUE_A, 0, 4, datetime(2026, 8, 1, 8, 0), "Wildcard login"),
    94108: ("z5_test", _QUEUE_A, 0, 4, datetime(2026, 8, 2, 8, 0), "Underscore login"),
    94110: ("back\\slash#2", _QUEUE_A, 0, 4, datetime(2026, 8, 3, 8, 0), "Backslash login"),
}


def _mysql_async(sync_url: str) -> str:
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _cleanup(conn: Any) -> None:
    ids = {"ids": list(_TICKETS)}
    conn.execute(
        text("DELETE FROM tiqora_queue_customer_link WHERE queue_id IN (:a, :b)"),
        {"a": _QUEUE_A, "b": _QUEUE_B},
    )
    conn.execute(
        text("DELETE FROM tiqora_ai_queue_policy WHERE queue_id IN (:a, :b)"),
        {"a": _QUEUE_A, "b": _QUEUE_B},
    )
    for stmt in (
        "DELETE FROM tiqora_ai_ticket_state WHERE ticket_id IN :ids",
        "DELETE FROM article_data_mime WHERE article_id IN"
        " (SELECT id FROM article WHERE ticket_id IN :ids)",
        "DELETE FROM article WHERE ticket_id IN :ids",
        "DELETE FROM ticket WHERE id IN :ids",
    ):
        conn.execute(text(stmt).bindparams(_expanding("ids")), ids)
    conn.execute(text("DELETE FROM queue WHERE id IN (:a, :b)"), {"a": _QUEUE_A, "b": _QUEUE_B})
    conn.execute(
        text("DELETE FROM group_user WHERE user_id = :u OR group_id IN (:a, :b)"),
        {"u": _UID_AGENT, "a": _GROUP_A, "b": _GROUP_B},
    )
    conn.execute(
        text("DELETE FROM permission_groups WHERE id IN (:a, :b)"), {"a": _GROUP_A, "b": _GROUP_B}
    )
    conn.execute(text("DELETE FROM customer_user WHERE login = 'z50test'"))
    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": _UID_AGENT})


def _expanding(name: str) -> Any:
    from sqlalchemy import bindparam

    return bindparam(name, expanding=True)


def _channel_id(conn: Any, name: str) -> int:
    return int(
        conn.execute(
            text("SELECT id FROM communication_channel WHERE name = :n"), {"n": name}
        ).scalar_one()
    )


def _add_article(
    conn: Any,
    ticket_id: int,
    *,
    channel_id: int,
    visible: int,
    at: datetime,
    a_from: str | None = None,
) -> None:
    conn.execute(
        text(
            "INSERT INTO article (ticket_id, article_sender_type_id, communication_channel_id,"
            " is_visible_for_customer, search_index_needs_rebuild, create_time, create_by,"
            " change_time, change_by) VALUES (:tid, 3, :ch, :vis, 0, :t, 1, :t, 1)"
        ),
        {"tid": ticket_id, "ch": channel_id, "vis": visible, "t": at},
    )
    if a_from is None:
        return
    article_id = conn.execute(text("SELECT MAX(id) FROM article")).scalar_one()
    conn.execute(
        text(
            "INSERT INTO article_data_mime (article_id, a_from, a_subject, a_content_type,"
            " a_body, incoming_time, create_time, create_by, change_time, change_by)"
            " VALUES (:aid, :frm, 'x', 'text/plain', 'x', 0, :t, 1, :t, 1)"
        ),
        {"aid": article_id, "frm": a_from, "t": at},
    )


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :login, 'x', 'Netadmin', 'Bot', 1, :t, 1, :t, 1)"
            ),
            {"id": _UID_AGENT, "login": _LOGIN_AGENT, "t": NOW},
        )
        for gid in (_GROUP_A, _GROUP_B):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "name": f"custtickets-{gid}", "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:uid, :gid, 'ro', :t, 1, :t, 1)"
            ),
            {"uid": _UID_AGENT, "gid": _GROUP_A, "t": NOW},
        )
        for qid, gid in ((_QUEUE_A, _GROUP_A), (_QUEUE_B, _GROUP_B)):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :name, :gid, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "name": f"CustTicketsQ-{qid}", "gid": gid, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO tiqora_queue_customer_link (queue_id, url_template, visibility,"
                " login_suffix_separator, create_by, create_time, change_by, change_time)"
                " VALUES (:q, 'https://netadmin.example/{customer_user}', 'all', '#',"
                " 1, :t, 1, :t)"
            ),
            {"q": _QUEUE_A, "t": NOW},
        )
        conn.execute(
            text(
                "INSERT INTO customer_user (login, email, customer_id, pw, first_name,"
                " last_name, valid_id, create_time, create_by, change_time, change_by)"
                " VALUES ('z50test', 'anna@example.com', 'Z50', 'x', 'Anna', 'Kellbach', 1,"
                " :t, 1, :t, 1)"
            ),
            {"t": NOW},
        )
        for tid, (cul, qid, archived, state_id, created, title) in _TICKETS.items():
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :qid, 1, 1, 1, 1, 3, :sid, 'Z50', :cul,"
                    " 0, 0, 0, 0, 0, 0, :arch, :t, 1, :changed, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"2026090{tid}",
                    "title": title,
                    "qid": qid,
                    "sid": state_id,
                    "cul": cul,
                    "arch": archived,
                    "t": created,
                    "changed": NOW,
                },
            )

        email = _channel_id(conn, "Email")
        internal = _channel_id(conn, "Internal")
        phone = _channel_id(conn, "Phone")
        _add_article(conn, 94101, channel_id=email, visible=1, at=datetime(2026, 9, 1, 8, 0))
        _add_article(conn, 94101, channel_id=email, visible=1, at=datetime(2026, 9, 1, 9, 0))
        _add_article(conn, 94101, channel_id=internal, visible=0, at=datetime(2026, 9, 1, 10, 0))
        _add_article(conn, 94101, channel_id=email, visible=0, at=datetime(2026, 9, 1, 11, 0))
        _add_article(conn, 94101, channel_id=phone, visible=1, at=datetime(2026, 9, 1, 12, 0))
        _add_article(
            conn,
            94109,
            channel_id=email,
            visible=1,
            at=datetime(2026, 9, 3, 8, 0),
            a_from="Bernd Beispiel <bernd@example.org>",
        )
        conn.execute(
            text(
                "INSERT INTO tiqora_ai_ticket_state (ticket_id, summary_body, summary_created_at,"
                " auto_reply_count, clarification_count, identity_attempts)"
                " VALUES (94101, 'Kunde meldet Ausfall.', :t, 0, 0, 0)"
            ),
            {"t": datetime(2026, 9, 1, 10, 0, 0)},
        )
    engine.dispose()


def _teardown(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()


@pytest.fixture
def seeded(mariadb_znuny_url: str) -> Generator[str, None, None]:
    _seed(mariadb_znuny_url)
    yield mariadb_znuny_url
    _teardown(mariadb_znuny_url)


async def _client(sync_url: str, overrides: dict[Any, Any] | None = None) -> tuple[Any, Any]:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    engine = create_async_engine(_mysql_async(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _override_get_db
    if overrides:
        app.dependency_overrides.update(overrides)
    else:
        fake_user = AuthenticatedUser(
            id=_UID_AGENT,
            login=_LOGIN_AGENT,
            first_name="Netadmin",
            last_name="Bot",
            auth_method="session",
        )
        app.dependency_overrides[get_current_user] = lambda: fake_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), engine


async def _get(sync_url: str, **params: Any) -> Any:
    client, engine = await _client(sync_url)
    async with client:
        resp = await client.get(_URL, params=params)
    await engine.dispose()
    return resp


@pytest.mark.asyncio
async def test_suffix_matching_permissions_archived_and_order(seeded: str) -> None:
    resp = await _get(seeded, login="z50test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["login"] == "z50test"
    # 94104 (z50test2), 94105 (xz50test): other customers; 94106: queue without ro.
    # Same create_time for 94103/94109 → id desc. 94102 is archived but included.
    assert [t["ticket_id"] for t in body["tickets"]] == [94109, 94103, 94102, 94101]
    by_id = {t["ticket_id"]: t for t in body["tickets"]}
    assert by_id[94103]["customer_user_id"] == "z50test#3"
    assert by_id[94102]["state"] == "closed successful"
    assert by_id[94102]["state_type"] == "closed"
    assert by_id[94101]["queue"] == f"CustTicketsQ-{_QUEUE_A}"
    assert by_id[94101]["ticket_number"] == "202609094101"


@pytest.mark.asyncio
async def test_limit(seeded: str) -> None:
    resp = await _get(seeded, login="z50test", limit=2)
    assert [t["ticket_id"] for t in resp.json()["tickets"]] == [94109, 94103]


@pytest.mark.asyncio
async def test_email_stats_summary_and_utc_datetimes(seeded: str) -> None:
    body = (await _get(seeded, login="z50test")).json()
    by_id = {t["ticket_id"]: t for t in body["tickets"]}
    t1 = by_id[94101]
    # Internal note, internal (not customer-visible) e-mail and phone call excluded.
    assert t1["email_count"] == 2
    assert t1["first_article_time"] == "2026-09-01T08:00:00+00:00"
    assert t1["last_article_time"] == "2026-09-01T09:00:00+00:00"
    assert t1["created"] == "2026-09-01T08:00:00+00:00"
    assert t1["changed"] == "2026-09-01T12:00:00+00:00"
    assert t1["summary"] == "Kunde meldet Ausfall."
    assert t1["summary_created_at"] == "2026-09-01T10:00:00+00:00"

    t3 = by_id[94103]
    assert t3["email_count"] == 0
    assert t3["first_article_time"] is None
    assert t3["last_article_time"] is None
    assert t3["summary"] is None
    assert t3["summary_created_at"] is None


@pytest.mark.asyncio
async def test_title_pii_free(seeded: str) -> None:
    body = (await _get(seeded, login="z50test")).json()
    flags = {t["ticket_id"]: t["title_pii_free"] for t in body["tickets"]}
    assert flags[94101] is True  # "Störungsmeldung Internet"
    assert flags[94102] is False  # customer_user name "Anna Kellbach"
    assert flags[94103] is False  # PKZ digits
    assert flags[94109] is False  # "Bernd" from the article's From header


@pytest.mark.asyncio
async def test_ner_runs_only_for_queues_with_ner_policy(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tiqora.ai.ner as ner
    from tiqora.ai.policies import create_queue_policy

    calls: list[str] = []

    def _fake_ner(title: str, **_: Any) -> list[str]:
        calls.append(title)
        return ["Internet"] if "Internet" in title else []

    monkeypatch.setattr(ner, "extract_person_names", _fake_ner)

    body = (await _get(seeded, login="z50test")).json()
    assert {t["ticket_id"]: t["title_pii_free"] for t in body["tickets"]}[94101] is True
    assert calls == []

    engine = create_async_engine(_mysql_async(seeded))
    async with async_sessionmaker(engine, class_=AsyncSession)() as session:
        await create_queue_policy(session, change_by=1, queue_id=_QUEUE_A, pii_ner_enabled=True)
    await engine.dispose()

    body = (await _get(seeded, login="z50test")).json()
    assert {t["ticket_id"]: t["title_pii_free"] for t in body["tickets"]}[94101] is False
    assert "Störungsmeldung Internet" in calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("login", "expected"),
    [
        ("z5%", [94107]),  # "%" literal: must not match z50test*
        ("z5_test", [94108]),  # "_" literal: must not match z50test#1/#3
        ("back\\slash", [94110]),  # backslash literal; "#2" suffix matches
        ("back\\", []),
        ("z5", []),
        ("unknown", []),
    ],
)
async def test_like_wildcards_are_escaped(seeded: str, login: str, expected: list[int]) -> None:
    resp = await _get(seeded, login=login)
    assert resp.status_code == 200, resp.text
    assert [t["ticket_id"] for t in resp.json()["tickets"]] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "login",
    ["", "z50test#3", "z50 test", "z50test\t", "z50\x00test", "z" * 151],
)
async def test_invalid_login_is_422(seeded: str, login: str) -> None:
    resp = await _get(seeded, login=login)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_limit_out_of_range_is_422(seeded: str) -> None:
    assert (await _get(seeded, login="z50test", limit=0)).status_code == 422
    assert (await _get(seeded, login="z50test", limit=201)).status_code == 422


class _StubAuth:
    def __init__(self, scopes: frozenset[str]) -> None:
        self._scopes = scopes

    async def resolve_session(self, token: str) -> None:
        return None

    async def resolve_api_key(self, raw: str) -> Any:
        from tiqora.domain.auth import AuthenticatedUser

        if raw != "tiqora_testkey":
            return None
        return AuthenticatedUser(
            id=_UID_AGENT,
            login=_LOGIN_AGENT,
            first_name="Netadmin",
            last_name="Bot",
            auth_method="api_key",
            api_key_scopes=self._scopes,
        )


class _NoopRedis:
    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del key, value, ex


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scopes", "auth_header", "expected_status"),
    [
        (frozenset({"tickets:ro"}), "Bearer tiqora_testkey", 200),
        (frozenset({"customers:ro"}), "Bearer tiqora_testkey", 403),
        (frozenset({"tickets:ro"}), "Bearer tiqora_wrong", 401),
        (frozenset({"tickets:ro"}), None, 401),
    ],
)
async def test_api_key_scope(
    seeded: str, scopes: frozenset[str], auth_header: str | None, expected_status: int
) -> None:
    from tiqora.api.deps import get_auth_service, get_redis

    client, engine = await _client(
        seeded, {get_auth_service: lambda: _StubAuth(scopes), get_redis: lambda: _NoopRedis()}
    )
    headers = {"Authorization": auth_header} if auth_header else {}
    async with client:
        resp = await client.get(_URL, params={"login": "z50test"}, headers=headers)
    await engine.dispose()
    assert resp.status_code == expected_status, resp.text
    if expected_status == 200:
        assert len(resp.json()["tickets"]) == 4
