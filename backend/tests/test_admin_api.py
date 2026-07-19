"""DB integration tests for the admin CRUD API (Phase 3a subtask 3).

Follows the direct-service-call pattern used by ``test_read_api_db.py``
(seed via raw SQL against a testcontainer, then exercise
``tiqora.api.v1.admin.*`` router functions and
``tiqora.permissions.engine.PermissionEngine`` directly against a real
async session) rather than going through httpx/TestClient — no existing
test in this repo drives authenticated routes end-to-end over HTTP, and the
admin routers are thin wrappers around ORM calls that are exercised
identically either way.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import yaml
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import customers as admin_customers
from tiqora.api.v1.admin import dynamic_fields as admin_dynamic_fields
from tiqora.api.v1.admin import queues as admin_queues
from tiqora.api.v1.admin import users as admin_users
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.api.v1.admin.schemas import (
    CustomerUserAdminCreate,
    DynamicFieldCreate,
    QueueCreate,
    UserCreate,
)
from tiqora.domain.auth import AuthenticatedUser, AuthService, SessionStore
from tiqora.domain.queue_service import QueueService
from tiqora.permissions.engine import PermissionEngine
from tiqora.znuny.password import hash_password

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


class _FakeRedis:
    """No-op Redis stand-in — SessionStore is not exercised by these tests."""

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        pass

    async def get(self, key: str) -> str | None:  # noqa: ARG002
        return None

    async def expire(self, key: str, ttl: int) -> None:  # noqa: ARG002
        pass

    async def delete(self, key: str) -> None:  # noqa: ARG002
        pass


class _FakeSettings:
    session_ttl_seconds = 86400


def _seed_admin_and_plain_user(sync_url: str) -> dict[str, int]:
    """Seed a non-admin agent. Root (id=1, group 'admin' rw) is already
    present via Znuny's ``initial_insert`` seed data loaded by the
    ``mariadb_znuny_url``/``postgres_znuny_url`` fixtures.
    """
    ns = uuid.uuid4().int % 1_000_000
    plain_id = 300_000 + ns
    pw = hash_password("secret")

    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO users (id, login, pw, first_name, last_name, valid_id,
                                  create_time, create_by, change_time, change_by)
                VALUES (:id, :login, :pw, 'Plain', 'Agent', 1, :t, 1, :t, 1)
                """
            ),
            {"id": plain_id, "login": f"plain.agent.{ns}", "pw": pw, "t": NOW},
        )
    engine.dispose()
    return {"admin_id": 1, "plain_id": plain_id}


async def _make_session(sync_url: str) -> tuple[AsyncSession, object]:
    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory(), engine


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_gate_403_for_non_admin_200_for_admin(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_plain_user(sync_url)
    session, engine = await _make_session(sync_url)

    async with session as s:
        engine_perm = PermissionEngine(s)
        assert await engine_perm.is_admin(ids["admin_id"]) is True
        assert await engine_perm.is_admin(ids["plain_id"]) is False

        plain_user = AuthenticatedUser(
            id=ids["plain_id"],
            login="plain",
            first_name="Plain",
            last_name="Agent",
            auth_method="session",
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_admin_user(plain_user, s)
        assert exc_info.value.status_code == 403

        admin_user = AuthenticatedUser(
            id=ids["admin_id"],
            login="root@localhost",
            first_name="Admin",
            last_name="Znuny",
            auth_method="session",
        )
        resolved = await get_admin_user(admin_user, s)
        assert resolved.id == ids["admin_id"]

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_user_create_bcrypt_login_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_plain_user(sync_url)
    session, engine = await _make_session(sync_url)
    ns = uuid.uuid4().hex[:8]

    async with session as s:
        admin_user = AuthenticatedUser(
            id=ids["admin_id"],
            login="root@localhost",
            first_name="Admin",
            last_name="Znuny",
            auth_method="session",
        )
        created = await admin_users.create_user(
            UserCreate(
                login=f"newagent.{ns}",
                password="newpassword123",
                first_name="New",
                last_name="Agent",
            ),
            admin_user,
            s,
        )
        assert created.login == f"newagent.{ns}"
        assert created.valid_id == 1

        # Roundtrip: BCRYPT hash actually verifies via the existing agent
        # auth path (domain/auth.py AuthService.authenticate_password).
        auth = AuthService(s, SessionStore(_FakeRedis(), _FakeSettings()), _FakeSettings())  # type: ignore[arg-type]
        authed = await auth.authenticate_password(f"newagent.{ns}", "newpassword123")
        assert authed is not None
        assert authed.id == created.id

        wrong = await auth.authenticate_password(f"newagent.{ns}", "wrong-password")
        assert wrong is None

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_queue_create_visible_in_queue_list(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_plain_user(sync_url)
    session, engine = await _make_session(sync_url)
    ns = uuid.uuid4().hex[:8]

    async with session as s:
        admin_user = AuthenticatedUser(
            id=ids["admin_id"],
            login="root@localhost",
            first_name="Admin",
            last_name="Znuny",
            auth_method="session",
        )
        # Admin (root) is already a member of group 1 ('users') via seed data.
        created = await admin_queues.create_queue(
            QueueCreate(
                name=f"AdminQueue-{ns}",
                group_id=1,
                system_address_id=1,
                salutation_id=1,
                signature_id=1,
                follow_up_id=1,
                follow_up_lock=0,
            ),
            admin_user,
            s,
        )
        assert created.name == f"AdminQueue-{ns}"

        qs = QueueService(s)
        tree = await qs.list_queues(ids["admin_id"])
        assert any(n.id == created.id for n in tree)

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_dynamic_field_dropdown_yaml_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_plain_user(sync_url)
    session, engine = await _make_session(sync_url)
    ns = uuid.uuid4().hex[:8]

    async with session as s:
        admin_user = AuthenticatedUser(
            id=ids["admin_id"],
            login="root@localhost",
            first_name="Admin",
            last_name="Znuny",
            auth_method="session",
        )
        created = await admin_dynamic_fields.create_dynamic_field(
            DynamicFieldCreate(
                name=f"TiqoraAdminDropdown{ns}",
                label="Admin Dropdown",
                field_order=999,
                field_type="Dropdown",
                object_type="Ticket",
                config={
                    "PossibleValues": {"a": "Option A", "b": "Option B"},
                    "PossibleNone": 1,
                    "TranslatableValues": 0,
                    "DefaultValue": "a",
                },
            ),
            admin_user,
            s,
        )
        print("TYPE", type(created.config), repr(created.config))
        assert created.config["PossibleValues"] == {"a": "Option A", "b": "Option B"}

        fetched = await admin_dynamic_fields.get_dynamic_field(created.id, admin_user, s)
        assert fetched.config["PossibleValues"] == {"a": "Option A", "b": "Option B"}
        assert fetched.config["DefaultValue"] == "a"

        # Also assert the raw stored bytes are YAML with Znuny's exact key names
        # (PossibleValues / DefaultValue / TranslatableValues), independent of
        # our own config_from_yaml() helper.
        from tiqora.db.legacy.dynamic_field import DynamicField

        row = await s.get(DynamicField, created.id)
        assert row is not None
        raw_yaml = row.config.decode("utf-8") if isinstance(row.config, bytes) else row.config
        loaded = yaml.safe_load(raw_yaml)
        assert loaded["PossibleValues"] == {"a": "Option A", "b": "Option B"}
        assert loaded["DefaultValue"] == "a"

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_dynamic_field_rejects_invalid_config(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_plain_user(sync_url)
    session, engine = await _make_session(sync_url)

    async with session as s:
        admin_user = AuthenticatedUser(
            id=ids["admin_id"],
            login="root@localhost",
            first_name="Admin",
            last_name="Znuny",
            auth_method="session",
        )
        with pytest.raises(HTTPException) as exc_info:
            await admin_dynamic_fields.create_dynamic_field(
                DynamicFieldCreate(
                    name="InvalidDropdown",
                    label="Invalid",
                    field_order=1,
                    field_type="Dropdown",
                    object_type="Ticket",
                    config={},  # missing required PossibleValues
                ),
                admin_user,
                s,
            )
        assert exc_info.value.status_code == 422

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_customer_user_create_and_soft_delete(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_plain_user(sync_url)
    session, engine = await _make_session(sync_url)
    ns = uuid.uuid4().hex[:8]

    async with session as s:
        admin_user = AuthenticatedUser(
            id=ids["admin_id"],
            login="root@localhost",
            first_name="Admin",
            last_name="Znuny",
            auth_method="session",
        )
        created = await admin_customers.create_customer_user(
            CustomerUserAdminCreate(
                login=f"newcust.{ns}@example.com",
                email=f"newcust.{ns}@example.com",
                customer_id=f"CUST-{ns}",
                first_name="New",
                last_name="Customer",
            ),
            admin_user,
            s,
        )
        assert created.valid_id == 1

        await admin_customers.deactivate_customer_user(created.id, admin_user, s)
        refreshed = await admin_customers.get_customer_user(created.id, admin_user, s)
        assert refreshed.valid_id == 2

    await engine.dispose()
