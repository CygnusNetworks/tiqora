"""Admin CRUD for queue variables + customer-field registry.

Direct router-function calls (same pattern as ``test_admin_api.py`` /
``test_webhooks.py``) against mariadb + postgres testcontainers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import placeholder_variables as admin_pv
from tiqora.api.v1.admin.pagination import ListParams
from tiqora.api.v1.admin.schemas import (
    PlaceholderFieldCreate,
    PlaceholderFieldUpdate,
    QueueVariableCreate,
    QueueVariableUpdate,
)
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

NOW = datetime(2024, 6, 1, 12, 0, 0)


def _to_async_url(sync_url: str) -> str:
    for old, new in (
        ("postgresql+psycopg2://", "postgresql+asyncpg://"),
        ("postgresql://", "postgresql+asyncpg://"),
        ("mysql+pymysql://", "mysql+aiomysql://"),
        ("mysql://", "mysql+aiomysql://"),
    ):
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    return sync_url


def _admin() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1,
        login="root@localhost",
        first_name="Admin",
        last_name="Znuny",
        auth_method="session",
    )


async def _make_session(sync_url: str) -> tuple[AsyncSession, object]:
    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine_sync.dispose()

    async_url = _to_async_url(sync_url)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory(), engine


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_queue_variable_crud_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ns = uuid.uuid4().int % 100_000
    queue_id = 9300 + (ns % 500)
    session, engine = await _make_session(sync_url)
    admin = _admin()
    params = ListParams(page=1, page_size=50, valid="all")

    async with session as s:
        created = await admin_pv.create_queue_variable(
            QueueVariableCreate(queue_id=queue_id, name="Domain", value=f"q{ns}.example"),
            admin,
            s,
        )
        assert created.id > 0
        assert created.queue_id == queue_id
        assert created.name == "Domain"
        assert created.value == f"q{ns}.example"

        # Global row
        global_row = await admin_pv.create_queue_variable(
            QueueVariableCreate(queue_id=None, name="GlobalOnly", value="gval"),
            admin,
            s,
        )
        assert global_row.queue_id is None

        listed = await admin_pv.list_queue_variables(
            admin, s, params, queue_id=queue_id, global_only=False
        )
        assert any(item.id == created.id for item in listed.items)
        assert all(item.queue_id == queue_id for item in listed.items)

        globals_only = await admin_pv.list_queue_variables(
            admin, s, params, queue_id=None, global_only=True
        )
        assert any(item.id == global_row.id for item in globals_only.items)
        assert all(item.queue_id is None for item in globals_only.items)

        got = await admin_pv.get_queue_variable(created.id, admin, s)
        assert got.name == "Domain"

        updated = await admin_pv.update_queue_variable(
            created.id,
            QueueVariableUpdate(value=f"updated-{ns}.example"),
            admin,
            s,
        )
        assert updated.value == f"updated-{ns}.example"

        await admin_pv.delete_queue_variable(created.id, admin, s)
        with pytest.raises(HTTPException) as exc:
            await admin_pv.get_queue_variable(created.id, admin, s)
        assert exc.value.status_code == 404

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_customer_field_crud_and_available_columns(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ns = uuid.uuid4().int % 100_000
    session, engine = await _make_session(sync_url)
    admin = _admin()
    params = ListParams(page=1, page_size=50, valid="all")

    # Ensure a custom column exists so introspection can surface it.
    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        dialect = conn.dialect.name
        if dialect.startswith("postgres"):
            has_col = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns"
                    " WHERE table_schema = current_schema()"
                    " AND table_name = 'customer_user' AND column_name = 'wpnum'"
                    " LIMIT 1"
                )
            ).first()
            if not has_col:
                conn.execute(
                    text("ALTER TABLE customer_user ADD COLUMN IF NOT EXISTS wpnum VARCHAR(64)")
                )
        else:
            has_col = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns"
                    " WHERE table_schema = DATABASE()"
                    " AND table_name = 'customer_user' AND column_name = 'wpnum'"
                    " LIMIT 1"
                )
            ).first()
            if not has_col:
                conn.execute(text("ALTER TABLE customer_user ADD COLUMN wpnum VARCHAR(64) NULL"))
    engine_sync.dispose()

    async with session as s:
        cols = await admin_pv.list_available_customer_columns(admin, s, source="customer_user")
        assert "login" in cols or "Login" in [c.lower() for c in cols]
        assert any(c.lower() == "wpnum" for c in cols)

        with pytest.raises(HTTPException) as exc:
            await admin_pv.list_available_customer_columns(admin, s, source="ticket")
        assert exc.value.status_code == 422

        created = await admin_pv.create_customer_field(
            PlaceholderFieldCreate(
                source_table="customer_user",
                column_name="wpnum",
                tag_name=f"wpnum{ns}",
                label="WP number",
                enabled=True,
            ),
            admin,
            s,
        )
        assert created.id > 0
        assert created.column_name == "wpnum"
        assert created.tag_name == f"wpnum{ns}"
        assert created.enabled is True

        listed = await admin_pv.list_customer_fields(admin, s, params)
        assert any(item.id == created.id for item in listed.items)

        got = await admin_pv.get_customer_field(created.id, admin, s)
        assert got.label == "WP number"

        updated = await admin_pv.update_customer_field(
            created.id,
            PlaceholderFieldUpdate(label="WP-Nr.", enabled=False),
            admin,
            s,
        )
        assert updated.label == "WP-Nr."
        assert updated.enabled is False

        await admin_pv.delete_customer_field(created.id, admin, s)
        with pytest.raises(HTTPException) as exc2:
            await admin_pv.get_customer_field(created.id, admin, s)
        assert exc2.value.status_code == 404

    await engine.dispose()
