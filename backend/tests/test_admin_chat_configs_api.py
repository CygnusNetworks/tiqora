"""Admin API for chat configurations (band 48730-48759, both dialects)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._fake_redis import FakeRedis
from tests._webchat_fixtures import NOW, cleanup_band, enable_webchat, seed_queue_agent
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_redis
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

GROUP, QUEUE, ADMIN_ID, PLAIN_ID = 48730, 48731, 48732, 48733
DF_TEXT, DF_DATE = 48740, 48741
SESSION_CONFIG = 48735
CONFIG_IDS = range(48730, 48760)
CUSTOMER = "wc48-kunde@example.org"
URLS = ["mariadb_znuny_url", "postgres_znuny_url"]
BASE = "/api/v1/admin/chat-configs"
TOPIC = {"key": "support", "label": "Support", "queue_id": QUEUE}


def _async_url(url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if url.startswith(old):
            return url.replace(old, new, 1)
    return url


def _cleanup(url: str) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        like = {"p": "%wc48%"}
        conn.execute(
            text(
                "DELETE FROM tiqora_chat_session WHERE config_id IN"
                " (SELECT id FROM tiqora_chat_config WHERE name LIKE :p)"
            ),
            like,
        )
        conn.execute(text("DELETE FROM tiqora_chat_config WHERE name LIKE :p"), like)
        cleanup_band(
            conn,
            config_ids=CONFIG_IDS,
            queue_ids=[QUEUE],
            group_ids=[GROUP],
            user_ids=[ADMIN_ID, PLAIN_ID],
        )
        conn.execute(
            text("DELETE FROM dynamic_field WHERE id IN (:a, :b)"), {"a": DF_TEXT, "b": DF_DATE}
        )
        conn.execute(text("DELETE FROM customer_user WHERE login = :l"), {"l": CUSTOMER})
    engine.dispose()


def _seed(conn: Any) -> None:
    seed_queue_agent(conn, queue_id=QUEUE, group_id=GROUP, user_id=ADMIN_ID, login="wc48-admin")
    conn.execute(
        text(
            "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, 'wc48-plain', 'x', 'Plain', 'Agent', 1, :t, 1, :t, 1)"
        ),
        {"u": PLAIN_ID, "t": NOW},
    )
    # admin group (name 'admin', rw) for the admin agent
    admin_gid = conn.execute(
        text("SELECT id FROM permission_groups WHERE name = 'admin'")
    ).scalar_one()
    conn.execute(
        text(
            "INSERT INTO group_user (user_id, group_id, permission_key,"
            " create_time, create_by, change_time, change_by)"
            " VALUES (:u, :g, 'rw', :t, 1, :t, 1)"
        ),
        {"u": ADMIN_ID, "g": admin_gid, "t": NOW},
    )
    for fid, name, ftype in ((DF_TEXT, "wc48Text", "Text"), (DF_DATE, "wc48Date", "Date")):
        conn.execute(
            text(
                "INSERT INTO dynamic_field (id, internal_field, name, label, field_order,"
                " field_type, object_type, config, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 0, :n, :n, :id, :ft, 'Ticket', '---', 1, :t, 1, :t, 1)"
            ),
            {"id": fid, "n": name, "ft": ftype, "t": NOW},
        )
    conn.execute(
        text(
            "INSERT INTO customer_user (login, email, customer_id, first_name, last_name,"
            " valid_id, create_time, create_by, change_time, change_by)"
            " VALUES (:l, :l, 'wc48', 'Kim', 'Kunde', 1, :t, 1, :t, 1)"
        ),
        {"l": CUSTOMER, "t": NOW},
    )
    enable_webchat(conn)


@dataclass
class Env:
    url: str
    factory: async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    url: str = request.getfixturevalue(request.param)
    engine = create_engine(url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()
    _cleanup(url)
    engine = create_engine(url)
    with engine.begin() as conn:
        _seed(conn)
    engine.dispose()
    aengine = create_async_engine(_async_url(url))
    yield Env(url, async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False))
    await aengine.dispose()
    _cleanup(url)


parametrize_db = pytest.mark.parametrize("env", URLS, indirect=True)


def _client(env: Env, user_id: int) -> AsyncClient:
    app = create_app(Settings(environment="test"))
    app.state.session_factory = env.factory
    user = AuthenticatedUser(
        id=user_id, login=f"wc48-{user_id}", first_name="W", last_name="C", auth_method="session"
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_redis] = lambda: FakeRedis()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _body(name: str = "Website wc48", **values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {"allowed_origins": ["https://www.example.org"], "topics": [TOPIC]}
    merged.update(values)
    return {"name": name, "values": merged}


@pytest.mark.asyncio
@parametrize_db
async def test_create_get_update_list(env: Env) -> None:
    async with _client(env, ADMIN_ID) as client:
        body = _body(allowed_origins=["https://WWW.example.org/"])
        res = await client.post(BASE, json=body)
        assert res.status_code == 201, res.text
        out = res.json()
        key = out["public_key"]
        assert key.startswith("wc_") and len(key) == 35
        assert out["values"]["allowed_origins"] == ["https://www.example.org"]
        assert out["embed_snippet"] == (
            f'<script src="http://testserver/chat/v1/loader.js" data-tiqora-chat="{key}" async>'
            "</script>"
        )
        assert out["session_count"] == 0 and out["valid"] is True

        values = dict(out["values"], greeting="Hallo!")
        res = await client.patch(f"{BASE}/{out['id']}", json={"values": values})
        assert res.status_code == 200, res.text
        assert res.json()["values"]["greeting"] == "Hallo!"
        assert res.json()["public_key"] == key

        res = await client.get(BASE)
        assert [c["id"] for c in res.json() if c["id"] == out["id"]] == [out["id"]]
        got = (await client.get(f"{BASE}/{out['id']}")).json()
        assert got["values"]["greeting"] == "Hallo!"
        assert (await client.get(f"{BASE}/999999")).status_code == 404

    async with env.factory() as db:
        row = (
            await db.execute(
                text("SELECT changed_by, created_by FROM tiqora_chat_config WHERE id = :i"),
                {"i": out["id"]},
            )
        ).one()
        assert (row[0], row[1]) == (ADMIN_ID, ADMIN_ID)


@pytest.mark.asyncio
@parametrize_db
async def test_save_time_checks(env: Env) -> None:
    async with _client(env, ADMIN_ID) as client:

        async def errors_of(**values: Any) -> dict[str, str]:
            res = await client.post(BASE, json=_body(**values))
            assert res.status_code == 422, res.text
            return dict(res.json()["detail"]["errors"])

        bad_topic = {"key": "support", "label": "Support", "queue_id": 48799}
        assert await errors_of(topics=[bad_topic]) == {
            "topics.0.queue_id": "Diese Queue gibt es nicht."
        }
        bad_prio = dict(TOPIC, priority_id=48799)
        assert "topics.0.priority_id" in await errors_of(topics=[bad_prio])

        def field(df: str) -> dict[str, Any]:
            return {
                "key": "extra",
                "kind": "text",
                "label": "X",
                "maps_to": "dynamic_field",
                "dynamic_field": df,
            }

        errs = await errors_of(prechat_fields=[field("NoSuchField")])
        assert list(errs) == ["prechat_fields.0.dynamic_field"]
        errs = await errors_of(prechat_fields=[field("wc48Date")])
        assert list(errs) == ["prechat_fields.0.dynamic_field"]
        assert "Feldtyp" in errs["prechat_fields.0.dynamic_field"]
        errs = await errors_of(default_customer_user="nobody-wc48@example.org")
        assert list(errs) == ["default_customer_user"]

        # pydantic-level errors use the same shape (R-ADMIN-422)
        errs = await errors_of(allowed_origins=["https://*.example.org"])
        assert list(errs) == ["allowed_origins"]
        assert errs["allowed_origins"]
        assert "Value error" not in errs["allowed_origins"]

        # valid references are accepted
        ok = await client.post(
            BASE,
            json=_body(
                name="Ok wc48",
                prechat_fields=[field("wc48Text")],
                default_customer_user=CUSTOMER,
            ),
        )
        assert ok.status_code == 201, ok.text

        # duplicate name
        dup = await client.post(BASE, json=_body(name="Ok wc48"))
        assert dup.status_code == 422
        assert list(dup.json()["detail"]["errors"]) == ["name"]

        # the handler is path-scoped: other routes keep FastAPI's list shape
        other = await client.put("/api/v1/chat/agent-state", json={"available": "x"})
        assert other.status_code == 422
        assert isinstance(other.json()["detail"], list)


@pytest.mark.asyncio
@parametrize_db
async def test_rotate_key_changes_key(env: Env) -> None:
    async with _client(env, ADMIN_ID) as client:
        created = (await client.post(BASE, json=_body(name="Rotate wc48"))).json()
        old = created["public_key"]
        params = {"k": old, "origin": "https://www.example.org"}
        assert (await client.get("/api/chat/v1/config", params=params)).status_code == 200
        res = await client.post(f"{BASE}/{created['id']}/rotate-key")
        assert res.status_code == 200, res.text
        new = res.json()["public_key"]
        assert new != old and new.startswith("wc_") and len(new) == 35
        assert (await client.get("/api/chat/v1/config", params=params)).status_code == 404
        assert (
            await client.get("/api/chat/v1/config", params={**params, "k": new})
        ).status_code == 200


@pytest.mark.asyncio
@parametrize_db
async def test_delete_in_use_conflict(env: Env) -> None:
    async with _client(env, ADMIN_ID) as client:
        used = (await client.post(BASE, json=_body(name="Used wc48"))).json()
        free = (await client.post(BASE, json=_body(name="Free wc48"))).json()
        async with env.factory() as db:
            await db.execute(
                text(
                    "INSERT INTO tiqora_chat_session (id, config_id, queue_id, ticket_id, status,"
                    " token_hash, visitor_id, last_seq, visitor_last_seen, created)"
                    " VALUES ('wc48sessionid000000000000000001', :c, :q, 1, 'ended', '', 'v', 0,"
                    " :t, :t)"
                ),
                {"c": used["id"], "q": QUEUE, "t": NOW},
            )
            await db.commit()
        res = await client.delete(f"{BASE}/{used['id']}")
        assert res.status_code == 409 and res.json() == {"detail": "in_use"}
        assert (await client.get(f"{BASE}/{used['id']}")).json()["session_count"] == 1
        assert (await client.delete(f"{BASE}/{free['id']}")).status_code == 204
        assert (await client.get(f"{BASE}/{free['id']}")).status_code == 404


@pytest.mark.asyncio
@parametrize_db
async def test_non_admin_forbidden(env: Env) -> None:
    async with _client(env, PLAIN_ID) as client:
        assert (await client.get(BASE)).status_code == 403
        assert (await client.post(BASE, json=_body())).status_code == 403
        assert (await client.delete(f"{BASE}/1")).status_code == 403
        assert (await client.post(f"{BASE}/1/rotate-key")).status_code == 403
