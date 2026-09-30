"""DB integration tests: queue default for a new ticket.

``GET /customers/{login}/suggested-queue`` and ``GET /tickets/new/default-queue``
walk: the customer user's newest ticket → the company's newest ticket → the
screen's ``QueueDefault`` sysconfig → the first queue that is not Junk, Raw or
Postmaster → the first queue. Every step only yields queues the agent may
create tickets in; tickets in intake queues (Junk/Raw/Postmaster) never count
as history.

Uses the 874xx id range; the module deletes every row it creates.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.new_ticket_queue import is_intake_queue

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

AGENT_ALL = 87401  # rw on the test group + the stock "users" group; ro on GROUP_RO
AGENT_CREATE = 87402  # only ``create`` on the test group
AGENT_JUNK_ONLY = 87403  # only sees a Junk::* queue
AGENT_NONE = 87404  # no groups at all
USERS = (AGENT_ALL, AGENT_CREATE, AGENT_JUNK_ONLY, AGENT_NONE)

GROUP_MAIN = 87430
GROUP_RO = 87431
GROUP_JUNK = 87432
GROUPS = (GROUP_MAIN, GROUP_RO, GROUP_JUNK)
STOCK_USERS_GROUP = 1  # Postmaster, Raw, Junk, Misc live here

Q_ALPHA = 87410
Q_BETA = 87411
Q_GAMMA = 87412  # agent may only read, not create
Q_JUNK_CHILD = 87413
Q_INVALID = 87414
QUEUES = (Q_ALPHA, Q_BETA, Q_GAMMA, Q_JUNK_CHILD, Q_INVALID)
Q_STOCK_JUNK = 3
Q_STOCK_MISC = 4

TICKETS = (87470, 87471, 87472, 87473)
CUSTOMERS = ("sug.alice", "sug.bob", "sug.carol", "sug.dave", "sug.erin")


def _to_async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _in(ids: tuple[int, ...]) -> str:
    return "(" + ", ".join(str(i) for i in ids) + ")"


def _cleanup(conn: Any) -> None:
    logins = "(" + ", ".join(f"'{c}'" for c in CUSTOMERS) + ")"
    for sql in (
        f"DELETE FROM ticket WHERE id IN {_in(TICKETS)}",
        f"DELETE FROM queue WHERE id IN {_in(QUEUES)}",
        f"DELETE FROM group_user WHERE user_id IN {_in(USERS)} OR group_id IN {_in(GROUPS)}",
        f"DELETE FROM permission_groups WHERE id IN {_in(GROUPS)}",
        f"DELETE FROM users WHERE id IN {_in(USERS)}",
        f"DELETE FROM customer_user WHERE login IN {logins}",
    ):
        conn.execute(text(sql))


def _seed(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
        _cleanup(conn)
        for uid in USERS:
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :login, 'x', 'Sug', 'Agent', 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "login": f"sug.agent{uid}", "t": NOW},
            )
        for gid in GROUPS:
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:gid, :name, 1, :t, 1, :t, 1)"
                ),
                {"gid": gid, "name": f"sug-grp-{gid}", "t": NOW},
            )
        grants = [
            (AGENT_ALL, GROUP_MAIN, "rw"),
            (AGENT_ALL, STOCK_USERS_GROUP, "rw"),
            (AGENT_ALL, GROUP_RO, "ro"),
            (AGENT_CREATE, GROUP_MAIN, "create"),
            (AGENT_JUNK_ONLY, GROUP_JUNK, "rw"),
        ]
        for uid, gid, key in grants:
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:uid, :gid, :k, :t, 1, :t, 1)"
                ),
                {"uid": uid, "gid": gid, "k": key, "t": NOW},
            )
        queues = [
            (Q_ALPHA, "SugAlpha", GROUP_MAIN, 1),
            (Q_BETA, "SugBeta", GROUP_MAIN, 1),
            (Q_GAMMA, "SugGamma", GROUP_RO, 1),
            (Q_JUNK_CHILD, "Junk::SugOnly", GROUP_JUNK, 1),
            (Q_INVALID, "SugInvalid", GROUP_MAIN, 2),
        ]
        for qid, name, gid, valid in queues:
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:qid, :name, :gid, 1, 1, 1, 1, 0, :v, :t, 1, :t, 1)"
                ),
                {"qid": qid, "name": name, "gid": gid, "v": valid, "t": NOW},
            )
        customers = [
            ("sug.alice", "SUGCO"),
            ("sug.bob", "SUGCO"),
            ("sug.carol", "SUGLONE"),
            ("sug.dave", "SUGCO2"),
            ("sug.erin", "SUGCO"),
        ]
        for login, company in customers:
            conn.execute(
                text(
                    "INSERT INTO customer_user (login, email, customer_id, first_name,"
                    " last_name, pw, valid_id, create_time, create_by, change_time, change_by)"
                    " VALUES (:login, :email, :cid, 'Sug', 'Customer', 'x', 1,"
                    " :t, 1, :t, 1)"
                ),
                {"login": login, "email": f"{login}@example.com", "cid": company, "t": NOW},
            )
        # (id, queue, customer_user, company) -- higher id = newer.
        tickets = [
            (87470, Q_BETA, "sug.alice", "SUGCO"),
            (87471, Q_STOCK_JUNK, "sug.alice", "SUGCO"),  # spam: never history
            (87472, Q_GAMMA, "sug.dave", "SUGCO2"),  # agent can't create there
            (87473, Q_ALPHA, "sug.erin", "SUGCO"),  # newest of the company
        ]
        for tid, qid, cust, company in tickets:
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:tid, :tn, 'Suggestion ticket', :qid, 1, 1, 1, 1, 3, 4,"
                    " :company, :cust, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {
                    "tid": tid,
                    "tn": f"2024060{tid}",
                    "qid": qid,
                    "company": company,
                    "cust": cust,
                    "t": NOW,
                },
            )
    engine.dispose()


def _teardown(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        _cleanup(conn)
    engine.dispose()


@pytest.fixture
async def db(mariadb_znuny_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    _seed(mariadb_znuny_url)
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()
        _teardown(mariadb_znuny_url)


@pytest.fixture
def queue_default(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Sysconfig values for ``Ticket::Frontend::<screen>###QueueDefault``."""
    from tiqora.znuny.sysconfig import SysConfig

    values: dict[str, Any] = {}
    original = SysConfig.get

    async def fake_get(self: SysConfig, name: str, default: Any = None) -> Any:
        if name in values:
            return values[name]
        return await original(self, name, default)

    monkeypatch.setattr(SysConfig, "get", fake_get)
    return values


PHONE_DEFAULT = "Ticket::Frontend::AgentTicketPhone###QueueDefault"
EMAIL_DEFAULT = "Ticket::Frontend::AgentTicketEmail###QueueDefault"


def _client(factory: async_sessionmaker[AsyncSession], user_id: int) -> Any:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_current_user, get_db
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    async def _override_get_db() -> Any:
        async with factory() as session:
            yield session

    fake_user = AuthenticatedUser(
        id=user_id,
        login=f"sug.agent{user_id}",
        first_name="Sug",
        last_name="Agent",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _suggest(
    factory: async_sessionmaker[AsyncSession],
    login: str,
    *,
    user_id: int = AGENT_ALL,
    screen: str = "phone",
) -> dict[str, Any]:
    async with _client(factory, user_id) as client:
        resp = await client.get(
            f"/api/v1/customers/{login}/suggested-queue", params={"screen": screen}
        )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _default(
    factory: async_sessionmaker[AsyncSession], *, user_id: int = AGENT_ALL, screen: str = "phone"
) -> dict[str, Any]:
    async with _client(factory, user_id) as client:
        resp = await client.get("/api/v1/tickets/new/default-queue", params={"screen": screen})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_intake_queues_match_top_level_name_case_insensitively() -> None:
    for name in ("Junk", "junk", "RAW", "Postmaster", "Junk::Spam", " Raw "):
        assert is_intake_queue(name), name
    for name in ("Misc", "Support", "Support::Junk", "Junkyard", "PostmasterX"):
        assert not is_intake_queue(name), name


# ---------------------------------------------------------------------------
# Customer history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_customer_newest_ticket_wins_over_company_and_ignores_junk(
    db: async_sessionmaker[AsyncSession], queue_default: dict[str, Any]
) -> None:
    queue_default[PHONE_DEFAULT] = "Misc"
    # alice's newest ticket sits in Junk; her newest real one is in SugBeta,
    # although the company's newest ticket (erin) is in SugAlpha.
    assert await _suggest(db, "sug.alice") == {"queue_id": Q_BETA, "source": "customer"}


@pytest.mark.asyncio
async def test_company_newest_ticket_when_the_customer_has_none(
    db: async_sessionmaker[AsyncSession],
) -> None:
    assert await _suggest(db, "sug.bob") == {"queue_id": Q_ALPHA, "source": "company"}


@pytest.mark.asyncio
async def test_create_permission_suffices_for_history(
    db: async_sessionmaker[AsyncSession],
) -> None:
    got = await _suggest(db, "sug.alice", user_id=AGENT_CREATE)
    assert got == {"queue_id": Q_BETA, "source": "customer"}


@pytest.mark.asyncio
async def test_history_queue_without_create_permission_is_skipped(
    db: async_sessionmaker[AsyncSession],
) -> None:
    # dave's (and his company's) only ticket is in SugGamma, where the agent
    # holds ``ro`` only → next rule: first non-intake queue.
    assert await _suggest(db, "sug.dave") == {"queue_id": Q_STOCK_MISC, "source": "fallback"}


@pytest.mark.asyncio
async def test_customer_without_history_gets_the_configured_default(
    db: async_sessionmaker[AsyncSession], queue_default: dict[str, Any]
) -> None:
    queue_default[PHONE_DEFAULT] = "SugBeta"
    assert await _suggest(db, "sug.carol") == {"queue_id": Q_BETA, "source": "default"}


@pytest.mark.asyncio
async def test_customer_without_history_and_default_never_gets_junk(
    db: async_sessionmaker[AsyncSession],
) -> None:
    # Visible: Junk, Misc, Postmaster, Raw, SugAlpha, SugBeta (+ SugGamma ro).
    assert await _suggest(db, "sug.carol") == {"queue_id": Q_STOCK_MISC, "source": "fallback"}


@pytest.mark.asyncio
async def test_unknown_customer_falls_through_to_the_default(
    db: async_sessionmaker[AsyncSession],
) -> None:
    got = await _suggest(db, "sug.nobody", user_id=AGENT_CREATE)
    assert got == {"queue_id": Q_ALPHA, "source": "fallback"}


# ---------------------------------------------------------------------------
# Configured default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured",
    [
        "NoSuchQueue",  # doesn't exist
        "SugInvalid",  # exists, invalid
        "SugGamma",  # exists, no create permission
        "Junk",  # intake queue
        "",  # unset
        None,
        ["SugBeta"],  # not a queue name
    ],
)
async def test_unusable_configured_default_is_ignored(
    db: async_sessionmaker[AsyncSession], queue_default: dict[str, Any], configured: Any
) -> None:
    queue_default[PHONE_DEFAULT] = configured
    assert await _default(db) == {"queue_id": Q_STOCK_MISC, "source": "fallback"}


@pytest.mark.asyncio
async def test_default_is_read_per_screen(
    db: async_sessionmaker[AsyncSession], queue_default: dict[str, Any]
) -> None:
    queue_default[PHONE_DEFAULT] = "SugAlpha"
    queue_default[EMAIL_DEFAULT] = " SugBeta "
    assert await _default(db, screen="phone") == {"queue_id": Q_ALPHA, "source": "default"}
    assert await _default(db, screen="email") == {"queue_id": Q_BETA, "source": "default"}


@pytest.mark.asyncio
async def test_default_queue_ignores_customer_history(
    db: async_sessionmaker[AsyncSession],
) -> None:
    got = await _default(db, user_id=AGENT_CREATE)
    assert got == {"queue_id": Q_ALPHA, "source": "fallback"}


# ---------------------------------------------------------------------------
# Last resorts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_intake_queues_visible_falls_back_to_the_first(
    db: async_sessionmaker[AsyncSession],
) -> None:
    got = await _default(db, user_id=AGENT_JUNK_ONLY)
    assert got == {"queue_id": Q_JUNK_CHILD, "source": "fallback"}


@pytest.mark.asyncio
async def test_agent_without_create_rights_gets_nothing(
    db: async_sessionmaker[AsyncSession],
) -> None:
    assert await _default(db, user_id=AGENT_NONE) == {"queue_id": None, "source": None}
    assert await _suggest(db, "sug.alice", user_id=AGENT_NONE) == {
        "queue_id": None,
        "source": None,
    }


@pytest.mark.asyncio
async def test_unknown_screen_is_422(db: async_sessionmaker[AsyncSession]) -> None:
    async with _client(db, AGENT_ALL) as client:
        a = await client.get("/api/v1/tickets/new/default-queue", params={"screen": "fax"})
        b = await client.get(
            "/api/v1/customers/sug.alice/suggested-queue", params={"screen": "fax"}
        )
    assert a.status_code == 422
    assert b.status_code == 422
