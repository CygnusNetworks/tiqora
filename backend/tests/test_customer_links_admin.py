"""Admin CRUD for ``tiqora_queue_customer_link`` — external per-queue
customer-tool link config.

Direct router-function calls (same pattern as
``test_placeholder_variables_admin.py``) against mariadb + postgres
testcontainers.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import customer_links as admin_cl
from tiqora.api.v1.admin.schemas import QueueCustomerLinkCreate, QueueCustomerLinkUpdate
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
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
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


def _seed_queue(sync_url: str, *, queue_id: int, name: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": queue_id})
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, :name, 1, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": queue_id, "name": name, "t": NOW},
        )
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_queue_customer_link_crud_roundtrip(
    url_fixture: str, request: pytest.FixtureRequest
) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    ns = uuid.uuid4().int % 100_000
    queue_id = 87000 + (ns % 100)
    _seed_queue(sync_url, queue_id=queue_id, name=f"CustLinkQ-{ns}")

    session, engine = await _make_session(sync_url)
    admin = _admin()

    async with session as s:
        created = await admin_cl.create_queue_customer_link(
            QueueCustomerLinkCreate(
                queue_id=queue_id,
                url_template="https://netadmin.example/?u={customer_user}",
                admin_url_template="https://netadmin-krb.example/?u={customer_user}",
                label="Kundendaten",
                visibility="all",
            ),
            admin,
            s,
        )
        assert created.id > 0
        assert created.queue_id == queue_id
        assert created.queue_name == f"CustLinkQ-{ns}"
        assert created.label == "Kundendaten"
        assert created.visibility == "all"

        # A second row for the same queue is rejected (queue_id is unique).
        with pytest.raises(HTTPException) as conflict:
            await admin_cl.create_queue_customer_link(
                QueueCustomerLinkCreate(queue_id=queue_id, url_template="https://x.example/"),
                admin,
                s,
            )
        assert conflict.value.status_code == 409

        # Invalid visibility is rejected on create.
        with pytest.raises(HTTPException) as bad_vis:
            await admin_cl.create_queue_customer_link(
                QueueCustomerLinkCreate(
                    queue_id=queue_id + 1,
                    url_template="https://x.example/",
                    visibility="bogus",
                ),
                admin,
                s,
            )
        assert bad_vis.value.status_code == 422

        listed = await admin_cl.list_queue_customer_links(admin, s)
        assert any(
            item.id == created.id and item.queue_name == f"CustLinkQ-{ns}" for item in listed
        )

        got = await admin_cl.get_queue_customer_link(created.id, admin, s)
        assert got.url_template == "https://netadmin.example/?u={customer_user}"

        updated = await admin_cl.update_queue_customer_link(
            created.id,
            QueueCustomerLinkUpdate(label="Diagnose", visibility="admins"),
            admin,
            s,
        )
        assert updated.label == "Diagnose"
        assert updated.visibility == "admins"

        with pytest.raises(HTTPException) as bad_vis_update:
            await admin_cl.update_queue_customer_link(
                created.id, QueueCustomerLinkUpdate(visibility="nope"), admin, s
            )
        assert bad_vis_update.value.status_code == 422

        await admin_cl.delete_queue_customer_link(created.id, admin, s)
        with pytest.raises(HTTPException) as not_found:
            await admin_cl.get_queue_customer_link(created.id, admin, s)
        assert not_found.value.status_code == 404

    await engine.dispose()
    engine_sync = create_engine(sync_url)
    with engine_sync.begin() as conn:
        conn.execute(text("DELETE FROM queue WHERE id = :id"), {"id": queue_id})
    engine_sync.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url_fixture", ["mariadb_znuny_url", "postgres_znuny_url"])
async def test_update_missing_link_is_404(url_fixture: str, request: pytest.FixtureRequest) -> None:
    sync_url: str = request.getfixturevalue(url_fixture)
    session, engine = await _make_session(sync_url)
    admin = _admin()

    async with session as s:
        with pytest.raises(HTTPException) as exc:
            await admin_cl.update_queue_customer_link(
                9_999_999, QueueCustomerLinkUpdate(label="x"), admin, s
            )
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc2:
            await admin_cl.delete_queue_customer_link(9_999_999, admin, s)
        assert exc2.value.status_code == 404

    await engine.dispose()
