"""DB tests for the admin ticket-states CRUD API, following the
direct-router-call pattern used by ``test_admin_channels.py`` /
``test_admin_api.py``."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tiqora.api.v1.admin import states as admin_states
from tiqora.api.v1.admin.pagination import ListParams
from tiqora.api.v1.admin.schemas import StateCreate, StateUpdate
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
    # the mariadb_znuny_url fixture; admin.states routes don't themselves
    # check group membership (that's the get_admin_user dependency, which
    # is bypassed when calling router functions directly, same as
    # test_admin_channels.py).
    return AuthenticatedUser(
        id=1, login="root@localhost", first_name="Admin", last_name="Znuny", auth_method="session"
    )


def _list_params(**overrides: object) -> ListParams:
    return ListParams(**{"page": 1, "page_size": 25, "valid": "valid", **overrides})


async def test_create_get_update_state(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_states.create_state(
                StateCreate(name="my custom state", type_id=1, comments="test"),
                _root_user(),
                session,
            )
            assert created.id is not None
            assert created.name == "my custom state"
            assert created.type_id == 1
            assert created.valid_id == 1

            fetched = await admin_states.get_state(created.id, _root_user(), session)
            assert fetched.name == "my custom state"

            updated = await admin_states.update_state(
                created.id, StateUpdate(name="renamed state"), _root_user(), session
            )
            assert updated.name == "renamed state"
            # type_id was left unset in the update body, so it must be preserved.
            assert updated.type_id == 1
    finally:
        await engine.dispose()


async def test_list_states_default_filters_to_valid(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            created = await admin_states.create_state(
                StateCreate(name="temp state", type_id=1), _root_user(), session
            )
            await admin_states.deactivate_state(created.id, _root_user(), session)

            valid_page = await admin_states.list_states(
                _root_user(), session, _list_params(valid="valid")
            )
            assert all(s.id != created.id for s in valid_page.items)

            invalid_page = await admin_states.list_states(
                _root_user(), session, _list_params(valid="invalid")
            )
            assert any(s.id == created.id for s in invalid_page.items)

            all_page = await admin_states.list_states(
                _root_user(), session, _list_params(valid="all")
            )
            assert any(s.id == created.id for s in all_page.items)
    finally:
        await engine.dispose()


async def test_get_unknown_state_returns_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_states.get_state(999_999, _root_user(), session)
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()


async def test_update_unknown_state_returns_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_states.update_state(
                    999_999, StateUpdate(name="x"), _root_user(), session
                )
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()


async def test_deactivate_unknown_state_returns_404(mariadb_znuny_url: str) -> None:
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(HTTPException) as exc_info:
                await admin_states.deactivate_state(999_999, _root_user(), session)
            assert exc_info.value.status_code == 404
    finally:
        await engine.dispose()


async def test_deactivate_state_in_use_by_ticket_is_allowed(mariadb_znuny_url: str) -> None:
    """Deactivating a state is a soft ``valid_id = 2`` flip, not a hard
    delete — there is no FK constraint from ``ticket.ticket_state_id`` that
    would block it, and the route does not check ticket usage before
    deactivating. Document that behaviour: deactivation succeeds even while a
    ticket still references the state, and the ticket row is left with its
    (now-invalid) state id intact — only the Znuny cache-invalidation signal
    rows are affected."""
    _ensure_tiqora_tables(mariadb_znuny_url)
    engine = create_async_engine(_mysql_async(mariadb_znuny_url))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            state = await admin_states.create_state(
                StateCreate(name="in-use state", type_id=1), _root_user(), session
            )

            # Minimal ticket row referencing this state. Uses raw SQL to
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
                    "VALUES (:tn, 'in-use test', 1, 1, 1, 1, 1, :sid, "
                    "0, 0, 0, 0, 0, 0, NOW(), 1, NOW(), 1)"
                ),
                {"tn": "test-state-in-use-1", "sid": state.id},
            )
            await session.commit()

            await admin_states.deactivate_state(state.id, _root_user(), session)

            fetched = await admin_states.get_state(state.id, _root_user(), session)
            assert fetched.valid_id == 2

            ticket_state = await session.scalar(
                text("SELECT ticket_state_id FROM ticket WHERE tn = :tn"),
                {"tn": "test-state-in-use-1"},
            )
            assert ticket_state == state.id
    finally:
        await engine.dispose()
