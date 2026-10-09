"""DB integration tests: POST /reference/field-options (ACL-filtered options
incl. dynamic fields) and the "Freie Felder" screen of GET /reference/dynamic-fields.

Uses the 897xx id range; the module deletes every row it creates (also the ACL
rows it seeds into Znuny's ``acl`` table).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.db.tiqora.base import TiqoraBase

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)

AGENT = 89701
GROUP = 89730
GROUP_HIDDEN = 89731
Q1 = 89710
Q2 = 89711
Q_HIDDEN = 89712
Q1_NAME = "WaveOptQ1"
Q2_NAME = "WaveOptQ2"
T_Q1 = 89770
T_Q2 = 89771
T_HIDDEN = 89772
DF_AREA = 89790
DF_TEXT = 89791
ACL_NAME = "wave16-field-options-acl"

URL = "/api/v1/reference/field-options"
AREA = "DynamicField_WaveArea"

AREA_CONFIG = "---\nPossibleValues:\n  tv: TV\n  net: Netz\n  phone: Telefon\n"
ACL_CHANGE = "---\nPossible:\n  Ticket:\n    DynamicField_WaveArea:\n    - net\n    - tv\n"

_SEEDED: list[str] = []


def _to_async_url(sync_url: str) -> str:
    return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)


def _seed_statements() -> tuple[tuple[str, dict[str, Any]], ...]:
    """DELETEs removing everything :func:`_seed` creates (run before and after)."""
    tickets = {"a": T_Q1, "b": T_Q2, "c": T_HIDDEN}
    return (
        ("DELETE FROM acl WHERE name = :n", {"n": ACL_NAME}),
        ("DELETE FROM ticket WHERE id IN (:a, :b, :c)", tickets),
        ("DELETE FROM dynamic_field WHERE id IN (:a, :b)", {"a": DF_AREA, "b": DF_TEXT}),
        ("DELETE FROM queue WHERE id IN (:a, :b, :c)", {"a": Q1, "b": Q2, "c": Q_HIDDEN}),
        (
            "DELETE FROM group_user WHERE user_id = :u OR group_id IN (:g1, :g2)",
            {"u": AGENT, "g1": GROUP, "g2": GROUP_HIDDEN},
        ),
        ("DELETE FROM permission_groups WHERE id IN (:g1, :g2)", {"g1": GROUP, "g2": GROUP_HIDDEN}),
        ("DELETE FROM users WHERE id = :u", {"u": AGENT}),
    )


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for sync_url in set(_SEEDED):
        engine = create_engine(sync_url)
        with engine.begin() as conn:
            for stmt, params in _seed_statements():
                conn.execute(text(stmt), params)
        engine.dispose()
    _SEEDED.clear()


def _seed(sync_url: str, *, with_acl: bool = True) -> None:
    _SEEDED.append(sync_url)
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt, params in _seed_statements():
            conn.execute(text(stmt), params)
        conn.execute(
            text(
                "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'wave.options', 'x', 'Wanda', 'Options', 1, :t, 1, :t, 1)"
            ),
            {"id": AGENT, "t": NOW},
        )
        for gid, name in ((GROUP, "wave-opt-grp"), (GROUP_HIDDEN, "wave-opt-hidden")):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": name, "t": NOW},
            )
        conn.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": AGENT, "g": GROUP, "t": NOW},
        )
        for qid, name, gid in (
            (Q1, Q1_NAME, GROUP),
            (Q2, Q2_NAME, GROUP),
            (Q_HIDDEN, "WaveOptHidden", GROUP_HIDDEN),
        ):
            conn.execute(
                text(
                    "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                    " signature_id, follow_up_id, follow_up_lock, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
                ),
                {"id": qid, "n": name, "g": gid, "t": NOW},
            )
        dfs = (
            (
                DF_AREA,
                "WaveArea",
                "Dropdown",
                "---\nPossibleValues:\n  tv: TV\n  net: Netz\n  phone: Telefon\n",
            ),
            (DF_TEXT, "WaveText", "Text", "---\nDefaultValue: ''\n"),
        )
        for dfid, name, ftype, cfg in dfs:
            conn.execute(
                text(
                    "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                    " field_type, object_type, config, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, 0, :n, :n, 1, :ft, 'Ticket', :cfg, 1, :t, 1, :t, 1)"
                ),
                {"id": dfid, "n": name, "ft": ftype, "cfg": cfg, "t": NOW},
            )
        for tid, qid in ((T_Q1, Q1), (T_Q2, Q2), (T_HIDDEN, Q_HIDDEN)):
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, 'Options ticket', :q, 1, 1, 1, 1, 3, 4,"
                    " NULL, NULL, 0, 0, 0, 0, 0, 0, 0, :t, 1, :t, 1)"
                ),
                {"id": tid, "tn": f"2024060189{tid}", "q": qid, "t": NOW},
            )
        if with_acl:
            conn.execute(
                text(
                    "INSERT INTO acl (name, comments, description, valid_id, stop_after_match,"
                    " config_match, config_change,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:n, '', '', 1, 0, :m, :c, :t, 1, :t, 1)"
                ),
                {
                    "n": ACL_NAME,
                    "m": f"---\nProperties:\n  Ticket:\n    Queue:\n    - {Q1_NAME}\n",
                    "c": ACL_CHANGE,
                    "t": NOW,
                },
            )
    engine.dispose()


@pytest.fixture
async def db(mariadb_znuny_url: str) -> AsyncIterator[tuple[str, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(_to_async_url(mariadb_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield mariadb_znuny_url, factory
    finally:
        await engine.dispose()


def _client(factory: async_sessionmaker[AsyncSession], user_id: int = AGENT) -> Any:
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
        login=f"agent{user_id}",
        first_name="Wanda",
        last_name="Options",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[get_db] = _override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


NET = {"value": "net", "label": "Netz"}
TV = {"value": "tv", "label": "TV"}
PHONE = {"value": "phone", "label": "Telefon"}


@pytest.mark.asyncio
async def test_df_options_sorted_and_acl_filtered(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    body = {"fields": [AREA], "context": {"ticket_id": T_Q1, "action": "AgentTicketFreeText"}}
    async with _client(factory) as client:
        resp = await client.post(URL, json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"options": {AREA: [NET, TV]}}


@pytest.mark.asyncio
async def test_df_options_keep_yaml_lookalike_keys_as_strings(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    """Znuny keys are strings: unquoted yes/no/on/01 must not become True/False/1."""
    url, factory = db
    _seed(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE dynamic_field SET config = :c WHERE id = :i"),
            {
                "c": (
                    "---\nPossibleValues:\n  yes: Ja\n  no: Nein\n  on: An\n"
                    "  01: Eins\n  '02': Zwei\n"
                ),
                "i": DF_AREA,
            },
        )
    engine.dispose()
    body = {"fields": [AREA], "context": {"ticket_id": T_Q2, "action": "AgentTicketFreeText"}}
    async with _client(factory) as client:
        resp = await client.post(URL, json=body)
    assert resp.status_code == 200, resp.text
    got = {o["value"]: o["label"] for o in resp.json()["options"][AREA]}
    assert got == {"yes": "Ja", "no": "Nein", "on": "An", "01": "Eins", "02": "Zwei"}


@pytest.mark.asyncio
async def test_df_options_without_matching_acl(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    body = {"fields": [AREA], "context": {"ticket_id": T_Q2, "action": "AgentTicketFreeText"}}
    async with _client(factory) as client:
        resp = await client.post(URL, json=body)
    assert resp.status_code == 200, resp.text
    # No ACL matches queue 2 -> all values, sorted by label (Netz, Telefon, TV).
    assert resp.json() == {"options": {AREA: [NET, PHONE, TV]}}


@pytest.mark.asyncio
async def test_form_context_queue_drives_acl(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory) as client:
        q1 = await client.post(URL, json={"fields": [AREA], "context": {"queue_id": Q1}})
        q2 = await client.post(URL, json={"fields": [AREA], "context": {"queue_id": Q2}})
    assert q1.status_code == 200, q1.text
    assert q1.json() == {"options": {AREA: [NET, TV]}}
    assert q2.json() == {"options": {AREA: [NET, PHONE, TV]}}


@pytest.mark.asyncio
async def test_attribute_fields_are_lists(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory) as client:
        resp = await client.post(URL, json={"fields": ["queue", "state"]})
    assert resp.status_code == 200, resp.text
    options = resp.json()["options"]
    queues = options["queue"]
    assert isinstance(queues, list)
    # Only queues the agent may read (ro), as {"value": id, "label": name}.
    assert {"value": str(Q1), "label": Q1_NAME} in queues
    assert {"value": str(Q2), "label": Q2_NAME} in queues
    assert all(q["label"] != "WaveOptHidden" for q in queues)
    assert [q["label"] for q in queues] == sorted(q["label"] for q in queues)
    states = options["state"]
    assert isinstance(states, list) and states
    assert all(set(s) == {"value", "label"} for s in states)


@pytest.mark.asyncio
async def test_unknown_and_text_fields_are_omitted(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    body = {"fields": ["DynamicField_WaveText", "DynamicField_Missing", "nope", AREA]}
    async with _client(factory) as client:
        resp = await client.post(URL, json=body)
    assert resp.status_code == 200, resp.text
    assert list(resp.json()["options"]) == [AREA]


@pytest.mark.asyncio
async def test_unreadable_ticket_is_404(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    url, factory = db
    _seed(url)
    async with _client(factory) as client:
        missing = await client.post(URL, json={"fields": [AREA], "context": {"ticket_id": 89999}})
        hidden = await client.post(URL, json={"fields": [AREA], "context": {"ticket_id": T_HIDDEN}})
    assert missing.status_code == 404, missing.text
    # Same mapping as GET /tickets/{id}: no ro on the queue -> 403.
    assert hidden.status_code == 403, hidden.text


@pytest.mark.asyncio
async def test_form_dynamic_field_values_feed_acl_checks(
    db: tuple[str, async_sessionmaker[AsyncSession]],
) -> None:
    """A ``DynamicField_<name>`` ACL property is matched against the form values."""
    url, factory = db
    _seed(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE acl SET config_match = :m WHERE name = :n"),
            {
                "n": ACL_NAME,
                "m": "---\nProperties:\n  Ticket:\n    DynamicField_WaveText:\n    - gate\n",
            },
        )
    engine.dispose()
    async with _client(factory) as client:
        hit = await client.post(
            URL,
            json={"fields": [AREA], "context": {"dynamic_fields": {"WaveText": ["gate"]}}},
        )
        miss = await client.post(
            URL,
            json={"fields": [AREA], "context": {"dynamic_fields": {"WaveText": ["other"]}}},
        )
    assert hit.json() == {"options": {AREA: [NET, TV]}}
    assert miss.json() == {"options": {AREA: [NET, PHONE, TV]}}


@pytest.mark.asyncio
async def test_free_text_screen_lists_configured_fields(
    db: tuple[str, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tiqora.znuny.sysconfig import SysConfig

    url, factory = db
    _seed(url)
    original = SysConfig.get

    async def fake_get(self: SysConfig, name: str, default: Any = None) -> Any:
        if name == "Ticket::Frontend::AgentTicketFreeText###DynamicField":
            return {"WaveArea": 1}
        return await original(self, name, default)

    monkeypatch.setattr(SysConfig, "get", fake_get)
    async with _client(factory) as client:
        resp = await client.get(
            "/api/v1/reference/dynamic-fields", params={"screen": "AgentTicketFreeText"}
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [f["name"] for f in data] == ["WaveArea"]
    assert data[0]["field_type"] == "Dropdown"
    assert data[0]["required"] is False
    assert data[0]["possible_values"] == {"tv": "TV", "net": "Netz", "phone": "Telefon"}
