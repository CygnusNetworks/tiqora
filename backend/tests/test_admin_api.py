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

from tiqora.api.v1.admin import attachments as admin_attachments
from tiqora.api.v1.admin import auto_responses as admin_auto_responses
from tiqora.api.v1.admin import customers as admin_customers
from tiqora.api.v1.admin import dynamic_fields as admin_dynamic_fields
from tiqora.api.v1.admin import groups as admin_groups
from tiqora.api.v1.admin import queues as admin_queues
from tiqora.api.v1.admin import readonly as admin_readonly
from tiqora.api.v1.admin import roles as admin_roles
from tiqora.api.v1.admin import templates as admin_templates
from tiqora.api.v1.admin import users as admin_users
from tiqora.api.v1.admin.deps import get_admin_user
from tiqora.api.v1.admin.pagination import ListParams
from tiqora.api.v1.admin.schemas import (
    AutoResponseCreate,
    CustomerCompanyCreate,
    CustomerUserAdminCreate,
    CustomerUserCustomerAssignment,
    CustomerUserGroupAssignment,
    DynamicFieldCreate,
    GroupAssignment,
    GroupCreate,
    GroupRoleAssignment,
    QueueAutoResponseAssignment,
    QueueCreate,
    QueueTemplateAssignment,
    RoleAssignment,
    RoleCreate,
    StandardAttachmentCreate,
    StandardAttachmentOut,
    StandardAttachmentUpdate,
    StandardTemplateCreate,
    TemplateAttachmentsReplace,
    UserCreate,
)
from tiqora.db.tiqora.base import TiqoraBase
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
    # Admin writes now enqueue Znuny cache-invalidation signals into the
    # additive tiqora_* tables (tiqora_cache_invalidation); create them here so
    # master-data CRUD works against the Znuny-only DDL loaded by the fixtures.
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: TiqoraBase.metadata.create_all(c, checkfirst=True))
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


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_customer_user_list_pagination_and_valid_filter(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The list endpoint pages the result set and hides invalid rows by
    default — the fix for the slow/empty Customer Users admin list."""
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

        # 12 valid + 3 invalid customer users, all in a unique customer_id so
        # the assertions are isolated from Znuny's seed data.
        cust = f"PAGE-{ns}"
        created_ids: list[int] = []
        for i in range(15):
            cu = await admin_customers.create_customer_user(
                CustomerUserAdminCreate(
                    login=f"pageuser.{i:02d}.{ns}@example.com",
                    email=f"pageuser.{i:02d}.{ns}@example.com",
                    customer_id=cust,
                    first_name="Page",
                    last_name=f"User{i:02d}",
                ),
                admin_user,
                s,
            )
            created_ids.append(cu.id)
        for cid in created_ids[:3]:
            await admin_customers.deactivate_customer_user(cid, admin_user, s)

        # Default valid filter hides the 3 invalid rows; page window caps items.
        page1 = await admin_customers.list_customer_users(
            admin_user, s, ListParams(page=1, page_size=5, valid="valid")
        )
        assert page1.total == 12
        assert page1.page == 1
        assert len(page1.items) == 5
        assert all(item.valid_id == 1 for item in page1.items)

        page3 = await admin_customers.list_customer_users(
            admin_user, s, ListParams(page=3, page_size=5, valid="valid")
        )
        assert page3.total == 12
        assert len(page3.items) == 2  # 12 valid → pages of 5/5/2

        # No page overlap: distinct logins across the two windows.
        assert {i.login for i in page1.items}.isdisjoint({i.login for i in page3.items})

        invalid = await admin_customers.list_customer_users(
            admin_user, s, ListParams(page=1, page_size=50, valid="invalid")
        )
        invalid_logins = {i.login for i in invalid.items if i.customer_id == cust}
        assert len(invalid_logins) == 3
        assert all(i.valid_id != 1 for i in invalid.items)

        all_rows = await admin_customers.list_customer_users(
            admin_user, s, ListParams(page=1, page_size=50, valid="all")
        )
        all_for_cust = {i.login for i in all_rows.items if i.customer_id == cust}
        assert len(all_for_cust) == 15

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_state_types_reference(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """The state-types reference resolves ticket_state.type_id to a name."""
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
        types = await admin_readonly.list_state_types(admin_user, s)
        names = {t.name for t in types}
        # Znuny's initial_insert seeds these ticket_state_type rows.
        assert {"new", "open", "closed"} <= names

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_user_role_assignment_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Assign/revoke an agent's role and read the current set back — the
    Agent↔Roles assignment editor's backend."""
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
        agent = await admin_users.create_user(
            UserCreate(
                login=f"roleagent.{ns}", password="pw12345678", first_name="Role", last_name="Agent"
            ),
            admin_user,
            s,
        )
        role = await admin_roles.create_role(RoleCreate(name=f"role-{ns}"), admin_user, s)

        assert await admin_users.get_user_roles(agent.id, admin_user, s) == []

        await admin_users.assign_role(agent.id, RoleAssignment(role_id=role.id), admin_user, s)
        assigned = await admin_users.get_user_roles(agent.id, admin_user, s)
        assert [r.id for r in assigned] == [role.id]

        # Idempotent assign (no duplicate PK), then revoke.
        await admin_users.assign_role(agent.id, RoleAssignment(role_id=role.id), admin_user, s)
        assert len(await admin_users.get_user_roles(agent.id, admin_user, s)) == 1

        await admin_users.revoke_role(agent.id, role.id, admin_user, s)
        assert await admin_users.get_user_roles(agent.id, admin_user, s) == []

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_user_group_assignment_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Assign/revoke an agent's group (rw) and read the current set back — the
    Agent↔Groups assignment editor's backend."""
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
        agent = await admin_users.create_user(
            UserCreate(
                login=f"groupagent.964.{ns}",
                password="pw12345678",
                first_name="Group",
                last_name="Agent",
            ),
            admin_user,
            s,
        )
        group = await admin_groups.create_group(GroupCreate(name=f"grp-964-{ns}"), admin_user, s)

        assert await admin_users.get_user_groups(agent.id, admin_user, s) == []

        await admin_users.assign_group(
            agent.id, GroupAssignment(group_id=group.id, permission_key="rw"), admin_user, s
        )
        assigned = await admin_users.get_user_groups(agent.id, admin_user, s)
        assert [g.id for g in assigned] == [group.id]

        # Idempotent assign (no duplicate PK), then revoke the rw grant.
        await admin_users.assign_group(
            agent.id, GroupAssignment(group_id=group.id, permission_key="rw"), admin_user, s
        )
        assert len(await admin_users.get_user_groups(agent.id, admin_user, s)) == 1

        await admin_users.revoke_group(agent.id, group.id, "rw", admin_user, s)
        assert await admin_users.get_user_groups(agent.id, admin_user, s) == []

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_role_group_assignment_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Assign/revoke a role's group (rw) and read the current set back — the
    Role↔Groups assignment editor's backend."""
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
        role = await admin_roles.create_role(RoleCreate(name=f"role-964-{ns}"), admin_user, s)
        group = await admin_groups.create_group(GroupCreate(name=f"rgrp-964-{ns}"), admin_user, s)

        assert await admin_roles.get_role_groups(role.id, admin_user, s) == []

        await admin_roles.assign_group_role(
            role.id,
            GroupRoleAssignment(group_id=group.id, permission_key="rw", permission_value=1),
            admin_user,
            s,
        )
        assigned = await admin_roles.get_role_groups(role.id, admin_user, s)
        assert [g.id for g in assigned] == [group.id]

        # Re-assign updates in place (no duplicate PK), then revoke.
        await admin_roles.assign_group_role(
            role.id,
            GroupRoleAssignment(group_id=group.id, permission_key="rw", permission_value=1),
            admin_user,
            s,
        )
        assert len(await admin_roles.get_role_groups(role.id, admin_user, s)) == 1

        await admin_roles.revoke_group_role(role.id, group.id, "rw", admin_user, s)
        assert await admin_roles.get_role_groups(role.id, admin_user, s) == []

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_customer_user_customer_assignment_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Assign/revoke a customer user's extra company visibility and read it
    back — the Customer-User↔Customers editor's backend (customer_user_customer,
    keyed by login, distinct from the primary customer_id)."""
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
        login = f"custcust.964.{ns}@example.com"
        await admin_customers.create_customer_user(
            CustomerUserAdminCreate(
                login=login,
                email=login,
                customer_id=f"HOME-964-{ns}",
                first_name="Cust",
                last_name="User",
            ),
            admin_user,
            s,
        )
        company = await admin_customers.create_customer_company(
            CustomerCompanyCreate(customer_id=f"EXTRA-964-{ns}", name=f"Extra {ns}"),
            admin_user,
            s,
        )

        assert await admin_customers.get_customer_user_companies(login, admin_user, s) == []

        await admin_customers.assign_customer_company(
            login, CustomerUserCustomerAssignment(customer_id=company.customer_id), admin_user, s
        )
        assigned = await admin_customers.get_customer_user_companies(login, admin_user, s)
        assert [c.customer_id for c in assigned] == [company.customer_id]

        # Idempotent assign, then revoke.
        await admin_customers.assign_customer_company(
            login, CustomerUserCustomerAssignment(customer_id=company.customer_id), admin_user, s
        )
        assert len(await admin_customers.get_customer_user_companies(login, admin_user, s)) == 1

        await admin_customers.revoke_customer_company(login, company.customer_id, admin_user, s)
        assert await admin_customers.get_customer_user_companies(login, admin_user, s) == []

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_attachment_crud_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Create/list/update/soft-delete a standard_attachment; content is
    base64 in the API and lands as a BLOB in the Znuny table."""
    import base64

    sync_url: str = request.getfixturevalue(url_fixture)
    ids = _seed_admin_and_plain_user(sync_url)
    session, engine = await _make_session(sync_url)
    ns = uuid.uuid4().hex[:8]
    raw = b"hello-attachment-" + ns.encode()
    b64 = base64.b64encode(raw).decode("ascii")

    async with session as s:
        admin_user = AuthenticatedUser(
            id=ids["admin_id"],
            login="root@localhost",
            first_name="Admin",
            last_name="Znuny",
            auth_method="session",
        )
        created = await admin_attachments.create_attachment(
            StandardAttachmentCreate(
                name=f"att-{ns}",
                content_type="text/plain",
                content=b64,
                filename=f"file-{ns}.txt",
                comments="seed",
            ),
            admin_user,
            s,
        )
        assert created.id > 0
        assert created.name == f"att-{ns}"
        assert created.filename == f"file-{ns}.txt"
        assert created.valid_id == 1
        # ORM returns bytes; Out schema base64-encodes on validate.
        out = StandardAttachmentOut.model_validate(created)
        assert out.content == b64
        assert base64.b64decode(out.content) == raw

        page = await admin_attachments.list_attachments(
            admin_user, s, ListParams(page=1, page_size=500, valid="valid")
        )
        names = {item.name for item in page.items}
        assert f"att-{ns}" in names

        updated = await admin_attachments.update_attachment(
            created.id,
            StandardAttachmentUpdate(comments="updated", name=f"att-renamed-{ns}"),
            admin_user,
            s,
        )
        assert updated.comments == "updated"
        assert updated.name == f"att-renamed-{ns}"

        # Content rewrite via base64.
        raw2 = b"rewritten-" + ns.encode()
        b64_2 = base64.b64encode(raw2).decode("ascii")
        rewritten = await admin_attachments.update_attachment(
            created.id,
            StandardAttachmentUpdate(content=b64_2),
            admin_user,
            s,
        )
        assert base64.b64decode(StandardAttachmentOut.model_validate(rewritten).content) == raw2

        # Soft-delete (valid_id=2); row still readable by id.
        await admin_attachments.deactivate_attachment(created.id, admin_user, s)
        got = await admin_attachments.get_attachment(created.id, admin_user, s)
        assert got.valid_id == 2

        # Direct table assertion — columns match Znuny standard_attachment.
        # Note: PG fixture maps LONGBLOB→TEXT, so LargeBinary binds land as
        # ``\x..`` hex text there; MariaDB stores raw BLOB. Decode both the
        # same way the API Out schema / storage._as_bytes does.
        row = (
            await s.execute(
                text(
                    "SELECT name, content_type, filename, comments, valid_id, content "
                    "FROM standard_attachment WHERE id = :id"
                ),
                {"id": created.id},
            )
        ).one()
        assert row.name == f"att-renamed-{ns}"
        assert row.content_type == "text/plain"
        assert row.filename == f"file-{ns}.txt"
        assert row.comments == "updated"
        assert row.valid_id == 2
        blob = row.content
        if isinstance(blob, memoryview):
            blob = blob.tobytes()
        if isinstance(blob, str) and blob.startswith("\\x"):
            blob = bytes.fromhex(blob[2:])
        elif isinstance(blob, bytes) and blob.startswith(b"\\x"):
            blob = bytes.fromhex(blob[2:].decode("ascii"))
        assert blob == raw2

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_template_attachment_replace_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Replace the set of attachments linked to a template via
    standard_template_attachment; empty list clears the assignment."""
    import base64

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
        tmpl = await admin_templates.create_template(
            StandardTemplateCreate(
                name=f"tmpl-att-{ns}",
                text="body",
                template_type="Answer",
            ),
            admin_user,
            s,
        )
        att_a = await admin_attachments.create_attachment(
            StandardAttachmentCreate(
                name=f"tmpl-a-{ns}",
                content_type="text/plain",
                content=base64.b64encode(b"a").decode("ascii"),
                filename=f"a-{ns}.txt",
            ),
            admin_user,
            s,
        )
        att_b = await admin_attachments.create_attachment(
            StandardAttachmentCreate(
                name=f"tmpl-b-{ns}",
                content_type="text/plain",
                content=base64.b64encode(b"b").decode("ascii"),
                filename=f"b-{ns}.txt",
            ),
            admin_user,
            s,
        )

        assert await admin_templates.get_template_attachments(tmpl.id, admin_user, s) == []

        await admin_templates.replace_template_attachments(
            tmpl.id,
            TemplateAttachmentsReplace(attachment_ids=[att_a.id, att_b.id]),
            admin_user,
            s,
        )
        linked = await admin_templates.get_template_attachments(tmpl.id, admin_user, s)
        assert sorted(a.id for a in linked) == sorted([att_a.id, att_b.id])

        # Replace with a subset — att_b should be dropped from the link table.
        await admin_templates.replace_template_attachments(
            tmpl.id,
            TemplateAttachmentsReplace(attachment_ids=[att_b.id]),
            admin_user,
            s,
        )
        linked = await admin_templates.get_template_attachments(tmpl.id, admin_user, s)
        assert [a.id for a in linked] == [att_b.id]

        # Empty set clears all links.
        await admin_templates.replace_template_attachments(
            tmpl.id, TemplateAttachmentsReplace(attachment_ids=[]), admin_user, s
        )
        assert await admin_templates.get_template_attachments(tmpl.id, admin_user, s) == []

        # Table-level: no rows remain for this template.
        count = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM standard_template_attachment "
                    "WHERE standard_template_id = :tid"
                ),
                {"tid": tmpl.id},
            )
        ).scalar_one()
        assert count == 0

        # Re-link and assert columns on the join table.
        await admin_templates.replace_template_attachments(
            tmpl.id,
            TemplateAttachmentsReplace(attachment_ids=[att_a.id]),
            admin_user,
            s,
        )
        row = (
            await s.execute(
                text(
                    "SELECT standard_attachment_id, standard_template_id, "
                    "create_by, change_by FROM standard_template_attachment "
                    "WHERE standard_template_id = :tid"
                ),
                {"tid": tmpl.id},
            )
        ).one()
        assert row.standard_attachment_id == att_a.id
        assert row.standard_template_id == tmpl.id
        assert row.create_by == ids["admin_id"]
        assert row.change_by == ids["admin_id"]

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_customer_user_group_assignment_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Assign/revoke a customer user's group (rw) keyed by *login* string —
    group_customer_user, not the numeric customer_user.id."""
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
        login = f"cugroup.{ns}@example.com"
        await admin_customers.create_customer_user(
            CustomerUserAdminCreate(
                login=login,
                email=login,
                customer_id=f"CUG-HOME-{ns}",
                first_name="Cu",
                last_name="Group",
            ),
            admin_user,
            s,
        )
        group = await admin_groups.create_group(GroupCreate(name=f"cugrp-{ns}"), admin_user, s)

        assert await admin_customers.get_customer_user_groups(login, admin_user, s) == []

        await admin_customers.assign_customer_user_group(
            login,
            CustomerUserGroupAssignment(group_id=group.id, permission_key="rw", permission_value=1),
            admin_user,
            s,
        )
        assigned = await admin_customers.get_customer_user_groups(login, admin_user, s)
        assert [g.id for g in assigned] == [group.id]

        # Idempotent re-assign (updates permission_value in place).
        await admin_customers.assign_customer_user_group(
            login,
            CustomerUserGroupAssignment(group_id=group.id, permission_key="rw", permission_value=1),
            admin_user,
            s,
        )
        assert len(await admin_customers.get_customer_user_groups(login, admin_user, s)) == 1

        # Table-level: user_id is the login string, not the numeric id.
        row = (
            await s.execute(
                text(
                    "SELECT user_id, group_id, permission_key, permission_value "
                    "FROM group_customer_user "
                    "WHERE user_id = :login AND group_id = :gid AND permission_key = 'rw'"
                ),
                {"login": login, "gid": group.id},
            )
        ).one()
        assert row.user_id == login
        assert row.group_id == group.id
        assert row.permission_key == "rw"
        assert row.permission_value == 1

        await admin_customers.revoke_customer_user_group(login, group.id, "rw", admin_user, s)
        assert await admin_customers.get_customer_user_groups(login, admin_user, s) == []

        remaining = (
            await s.execute(
                text(
                    "SELECT COUNT(*) FROM group_customer_user "
                    "WHERE user_id = :login AND group_id = :gid"
                ),
                {"login": login, "gid": group.id},
            )
        ).scalar_one()
        assert remaining == 0

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_queue_template_assignment_get_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Queue↔template read side: assign, GET both directions, revoke, empty; 404 anchors."""
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
        queue = await admin_queues.create_queue(
            QueueCreate(
                name=f"qt-q-{ns}",
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
        tmpl = await admin_templates.create_template(
            StandardTemplateCreate(
                name=f"qt-t-{ns}",
                text="body",
                template_type="Answer",
            ),
            admin_user,
            s,
        )

        assert await admin_templates.get_queue_templates(queue.id, admin_user, s) == []
        assert await admin_templates.get_template_queues(tmpl.id, admin_user, s) == []

        await admin_templates.assign_queue_template(
            queue.id,
            QueueTemplateAssignment(standard_template_id=tmpl.id),
            admin_user,
            s,
        )
        linked_tmpls = await admin_templates.get_queue_templates(queue.id, admin_user, s)
        assert [t.id for t in linked_tmpls] == [tmpl.id]
        linked_queues = await admin_templates.get_template_queues(tmpl.id, admin_user, s)
        assert [q.id for q in linked_queues] == [queue.id]

        await admin_templates.revoke_queue_template(queue.id, tmpl.id, admin_user, s)
        assert await admin_templates.get_queue_templates(queue.id, admin_user, s) == []
        assert await admin_templates.get_template_queues(tmpl.id, admin_user, s) == []

        with pytest.raises(HTTPException) as missing_queue:
            await admin_templates.get_queue_templates(9_999_999, admin_user, s)
        assert missing_queue.value.status_code == 404

        with pytest.raises(HTTPException) as missing_tmpl:
            await admin_templates.get_template_queues(9_999_999, admin_user, s)
        assert missing_tmpl.value.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_queue_auto_response_assignment_get_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Queue↔auto-response read side: assign, GET both directions, revoke, empty; 404 anchors."""
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
        queue = await admin_queues.create_queue(
            QueueCreate(
                name=f"qar-q-{ns}",
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
        ar = await admin_auto_responses.create_auto_response(
            AutoResponseCreate(
                name=f"qar-ar-{ns}",
                type_id=1,
                system_address_id=1,
                text0="hello",
            ),
            admin_user,
            s,
        )

        assert await admin_auto_responses.get_queue_auto_responses(queue.id, admin_user, s) == []
        assert await admin_auto_responses.get_auto_response_queues(ar.id, admin_user, s) == []

        await admin_auto_responses.assign_queue_auto_response(
            queue.id,
            QueueAutoResponseAssignment(auto_response_id=ar.id),
            admin_user,
            s,
        )
        linked_ars = await admin_auto_responses.get_queue_auto_responses(queue.id, admin_user, s)
        assert [a.id for a in linked_ars] == [ar.id]
        linked_queues = await admin_auto_responses.get_auto_response_queues(ar.id, admin_user, s)
        assert [q.id for q in linked_queues] == [queue.id]

        await admin_auto_responses.revoke_queue_auto_response(queue.id, ar.id, admin_user, s)
        assert await admin_auto_responses.get_queue_auto_responses(queue.id, admin_user, s) == []
        assert await admin_auto_responses.get_auto_response_queues(ar.id, admin_user, s) == []

        with pytest.raises(HTTPException) as missing_queue:
            await admin_auto_responses.get_queue_auto_responses(9_999_999, admin_user, s)
        assert missing_queue.value.status_code == 404

        with pytest.raises(HTTPException) as missing_ar:
            await admin_auto_responses.get_auto_response_queues(9_999_999, admin_user, s)
        assert missing_ar.value.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_group_users_reverse_get_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Group↔users reverse read: assign, GET reverse, revoke empty; 404 anchor."""
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
        agent = await admin_users.create_user(
            UserCreate(
                login=f"rev.gu.{ns}@example.com",
                password="pw12345678",
                first_name="Rev",
                last_name="GroupUser",
            ),
            admin_user,
            s,
        )
        group = await admin_groups.create_group(GroupCreate(name=f"rev-gu-{ns}"), admin_user, s)

        assert await admin_groups.get_group_users(group.id, admin_user, s) == []

        await admin_users.assign_group(
            agent.id, GroupAssignment(group_id=group.id, permission_key="rw"), admin_user, s
        )
        linked = await admin_groups.get_group_users(group.id, admin_user, s)
        assert [u.id for u in linked] == [agent.id]

        await admin_users.revoke_group(agent.id, group.id, "rw", admin_user, s)
        assert await admin_groups.get_group_users(group.id, admin_user, s) == []

        with pytest.raises(HTTPException) as missing:
            await admin_groups.get_group_users(9_999_999, admin_user, s)
        assert missing.value.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_role_users_reverse_get_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Role↔users reverse read: assign, GET reverse, revoke empty; 404 anchor."""
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
        agent = await admin_users.create_user(
            UserCreate(
                login=f"rev.ru.{ns}@example.com",
                password="pw12345678",
                first_name="Rev",
                last_name="RoleUser",
            ),
            admin_user,
            s,
        )
        role = await admin_roles.create_role(RoleCreate(name=f"rev-ru-{ns}"), admin_user, s)

        assert await admin_roles.get_role_users(role.id, admin_user, s) == []

        await admin_users.assign_role(agent.id, RoleAssignment(role_id=role.id), admin_user, s)
        linked = await admin_roles.get_role_users(role.id, admin_user, s)
        assert [u.id for u in linked] == [agent.id]

        await admin_users.revoke_role(agent.id, role.id, admin_user, s)
        assert await admin_roles.get_role_users(role.id, admin_user, s) == []

        with pytest.raises(HTTPException) as missing:
            await admin_roles.get_role_users(9_999_999, admin_user, s)
        assert missing.value.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_group_roles_reverse_get_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Group↔roles reverse read: assign, GET reverse, revoke empty; 404 anchor."""
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
        role = await admin_roles.create_role(RoleCreate(name=f"rev-gr-{ns}"), admin_user, s)
        group = await admin_groups.create_group(GroupCreate(name=f"rev-gr-g-{ns}"), admin_user, s)

        assert await admin_groups.get_group_roles(group.id, admin_user, s) == []

        await admin_roles.assign_group_role(
            role.id,
            GroupRoleAssignment(group_id=group.id, permission_key="rw", permission_value=1),
            admin_user,
            s,
        )
        linked = await admin_groups.get_group_roles(group.id, admin_user, s)
        assert [r.id for r in linked] == [role.id]

        await admin_roles.revoke_group_role(role.id, group.id, "rw", admin_user, s)
        assert await admin_groups.get_group_roles(group.id, admin_user, s) == []

        with pytest.raises(HTTPException) as missing:
            await admin_groups.get_group_roles(9_999_999, admin_user, s)
        assert missing.value.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_group_customer_users_reverse_get_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Group↔customer-users reverse read: assign, GET reverse, revoke empty; 404."""
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
        login = f"rev.gcu.{ns}@example.com"
        await admin_customers.create_customer_user(
            CustomerUserAdminCreate(
                login=login,
                email=login,
                customer_id=f"REV-GCU-HOME-{ns}",
                first_name="Rev",
                last_name="Gcu",
            ),
            admin_user,
            s,
        )
        group = await admin_groups.create_group(GroupCreate(name=f"rev-gcu-{ns}"), admin_user, s)

        assert await admin_groups.get_group_customer_users(group.id, admin_user, s) == []

        await admin_customers.assign_customer_user_group(
            login,
            CustomerUserGroupAssignment(group_id=group.id, permission_key="rw", permission_value=1),
            admin_user,
            s,
        )
        linked = await admin_groups.get_group_customer_users(group.id, admin_user, s)
        assert [cu.login for cu in linked] == [login]

        await admin_customers.revoke_customer_user_group(login, group.id, "rw", admin_user, s)
        assert await admin_groups.get_group_customer_users(group.id, admin_user, s) == []

        with pytest.raises(HTTPException) as missing:
            await admin_groups.get_group_customer_users(9_999_999, admin_user, s)
        assert missing.value.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_customer_company_users_reverse_get_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Company↔customer-users reverse read (customer_user_customer); 404 anchor."""
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
        login = f"rev.ccu.{ns}@example.com"
        await admin_customers.create_customer_user(
            CustomerUserAdminCreate(
                login=login,
                email=login,
                customer_id=f"REV-CCU-HOME-{ns}",
                first_name="Rev",
                last_name="Ccu",
            ),
            admin_user,
            s,
        )
        company = await admin_customers.create_customer_company(
            CustomerCompanyCreate(customer_id=f"REV-CCU-EXTRA-{ns}", name=f"Rev CCU {ns}"),
            admin_user,
            s,
        )

        assert (
            await admin_customers.get_customer_company_users(company.customer_id, admin_user, s)
            == []
        )

        await admin_customers.assign_customer_company(
            login, CustomerUserCustomerAssignment(customer_id=company.customer_id), admin_user, s
        )
        linked = await admin_customers.get_customer_company_users(
            company.customer_id, admin_user, s
        )
        assert [cu.login for cu in linked] == [login]

        await admin_customers.revoke_customer_company(login, company.customer_id, admin_user, s)
        assert (
            await admin_customers.get_customer_company_users(company.customer_id, admin_user, s)
            == []
        )

        with pytest.raises(HTTPException) as missing:
            await admin_customers.get_customer_company_users("missing-company-rev", admin_user, s)
        assert missing.value.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_admin_attachment_templates_reverse_get_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    """Attachment↔templates reverse read: link, GET reverse, clear empty; 404."""
    import base64

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
        tmpl = await admin_templates.create_template(
            StandardTemplateCreate(
                name=f"rev-at-t-{ns}",
                text="body",
                template_type="Answer",
            ),
            admin_user,
            s,
        )
        att = await admin_attachments.create_attachment(
            StandardAttachmentCreate(
                name=f"rev-at-a-{ns}",
                content_type="text/plain",
                content=base64.b64encode(b"rev").decode("ascii"),
                filename=f"rev-at-{ns}.txt",
            ),
            admin_user,
            s,
        )

        assert await admin_attachments.get_attachment_templates(att.id, admin_user, s) == []

        await admin_templates.replace_template_attachments(
            tmpl.id,
            TemplateAttachmentsReplace(attachment_ids=[att.id]),
            admin_user,
            s,
        )
        linked = await admin_attachments.get_attachment_templates(att.id, admin_user, s)
        assert [t.id for t in linked] == [tmpl.id]

        await admin_templates.replace_template_attachments(
            tmpl.id, TemplateAttachmentsReplace(attachment_ids=[]), admin_user, s
        )
        assert await admin_attachments.get_attachment_templates(att.id, admin_user, s) == []

        with pytest.raises(HTTPException) as missing:
            await admin_attachments.get_attachment_templates(9_999_999, admin_user, s)
        assert missing.value.status_code == 404

    await engine.dispose()
