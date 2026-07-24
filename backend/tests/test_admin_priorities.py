"""DB tests for the admin ticket-priorities CRUD API, following the
direct-router-call pattern used by ``test_admin_channels.py`` /
``test_admin_api.py``."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import priorities as admin_priorities
from tiqora.api.v1.admin.pagination import ListParams
from tiqora.api.v1.admin.schemas import PriorityCreate, PriorityUpdate
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db


def _mysql_async(url: str) -> str:
    return url.replace("mysql+pymysql://", "mysql+aiomysql://")


def _ensure_tiqora_tables(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        TiqoraBase.metadata.create_all(conn)
    engine.dispose()


def _root_user() -> AuthenticatedUser:
    # Root (id=1) is present via Znuny's initial_insert seed data loaded by
    # the mariadb_znuny_url fixture; admin.priorities routes don't themselves
    # check group membership (that's the get_admin_user dependency, which
    # is bypassed when calling router functions directly, same as
    # test_admin_channels.py).
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


def _list_params(**overrides: object) -> ListParams:
    return ListParams(**{"page": 1, "page_size": 25, "valid": "valid", **overrides})


async def test_create_get_update_priority(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_priorities.create_priority(
                PriorityCreate(name="9 custom priority"), _root_user(), session
            )
            assert created.id is not None
            assert created.name == "9 custom priority"
            assert created.valid_id == 1

            fetched = await admin_priorities.get_priority(created.id, _root_user(), session)
            assert fetched.name == "9 custom priority"

            updated = await admin_priorities.update_priority(
                created.id,
                PriorityUpdate(name="9 custom priority (renamed)"),
                _root_user(),
                session,
            )
            assert updated.name == "9 custom priority (renamed)"
    finally:
        await engine.dispose()


async def test_list_priorities_default_filters_to_valid(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_priorities.create_priority(
                PriorityCreate(name="temp priority"), _root_user(), session
            )
            await admin_priorities.deactivate_priority(created.id, _root_user(), session)

            valid_page = await admin_priorities.list_priorities(
                _root_user(), session, _list_params(valid="valid")
            )
            assert all(p.id != created.id for p in valid_page.items)

            invalid_page = await admin_priorities.list_priorities(
                _root_user(), session, _list_params(valid="invalid")
            )
            assert any(p.id == created.id for p in invalid_page.items)

            all_page = await admin_priorities.list_priorities(
                _root_user(), session, _list_params(valid="all")
            )
            assert any(p.id == created.id for p in all_page.items)
    finally:
        await engine.dispose()


async def test_get_unknown_priority_returns_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_priorities.get_priority(999_999, _root_user(), session)
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()


async def test_update_unknown_priority_returns_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_priorities.update_priority(
                    999_999, PriorityUpdate(name="x"), _root_user(), session
                )
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()


async def test_deactivate_unknown_priority_returns_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_priorities.deactivate_priority(999_999, _root_user(), session)
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()


async def test_deactivate_priority_in_use_by_ticket_is_allowed(mariadb_znuny_url: str) -> None:
    """Deactivating a priority is a soft ``valid_id = 2`` flip, not a hard
    delete — there is no FK constraint from ``ticket.ticket_priority_id`` that
    would block it, and the route does not check ticket usage before
    deactivating. Document that behaviour: deactivation succeeds even while a
    ticket still references the priority, and the ticket row is left with
    its (now-invalid) priority id intact — only the Znuny cache-invalidation
    signal rows are affected."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            priority = await admin_priorities.create_priority(
                PriorityCreate(name="in-use priority"), _root_user(), session
            )

            # Minimal ticket row referencing this priority. Uses raw SQL to
            # avoid pulling in the full ticket-creation domain flow, which is
            # exercised elsewhere.
            await session.execute(
                text(
                    "INSERT INTO ticket "
                    "(tn, title, queue_id, ticket_lock_id, "
                    "user_id, responsible_user_id, ticket_priority_id, "
                    "ticket_state_id, timeout, until_time, escalation_time, "
                    "escalation_update_time, escalation_response_time, "
                    "escalation_solution_time, "
                    "create_time, create_by, change_time, change_by) "
                    "VALUES (:tn, 'in-use test', 1, 1, 1, 1, :pid, 1, "
                    "0, 0, 0, 0, 0, 0, NOW(), 1, NOW(), 1)"
                ),
                {"tn": "test-priority-in-use-1", "pid": priority.id},
            )
            await session.commit()

            await admin_priorities.deactivate_priority(priority.id, _root_user(), session)

            fetched = await admin_priorities.get_priority(priority.id, _root_user(), session)
            assert fetched.valid_id == 2

            ticket_priority = await session.scalar(
                text("SELECT ticket_priority_id FROM ticket WHERE tn = :tn"),
                {"tn": "test-priority-in-use-1"},
            )
            assert ticket_priority == priority.id
    finally:
        await engine.dispose()
