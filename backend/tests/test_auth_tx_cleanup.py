"""Regression: get_current_user must leave the request session transaction-clean.

The auth lookups read the DB on the shared request session, which autobegins a
transaction. If that transaction is left open, any write endpoint that does
``async with session.begin()`` fails with "A transaction is already begun on
this Session" (observed as a 500 on POST /tickets/{id}/articles — reply send).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.deps import get_current_user
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

_USER = AuthenticatedUser(id=1, login="agent", first_name="A", last_name="G", auth_method="session")


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


class _StubAuth:
    def __init__(self, session_user: AuthenticatedUser | None) -> None:
        self._user = session_user

    async def resolve_session(self, token: str) -> AuthenticatedUser | None:
        return self._user

    async def resolve_api_key(self, raw: str) -> AuthenticatedUser | None:
        return None


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(), cookies={})


class _NoopRedis:
    """Minimal Redis stand-in so auth cleanup tests ignore online presence."""

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del key, value, ex


@pytest.fixture
async def session(postgres_znuny_url: str) -> AsyncSession:
    engine = create_async_engine(_to_async_url(postgres_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_current_user_rolls_back_autobegun_tx(session: AsyncSession) -> None:
    # Simulate the read-only transaction the auth lookup autobegins.
    await session.execute(text("SELECT 1"))
    assert session.in_transaction()

    user = await get_current_user(
        request=_request(),  # type: ignore[arg-type]
        auth=_StubAuth(_USER),  # type: ignore[arg-type]
        settings=Settings(environment="test"),
        session=session,
        redis_client=_NoopRedis(),  # type: ignore[arg-type]
        authorization=None,
        tiqora_session="tok",
    )

    assert user is _USER
    # Clean session -> a write endpoint can now open its own begin().
    assert not session.in_transaction()
    async with session.begin():
        await session.execute(text("SELECT 1"))


@pytest.mark.asyncio
async def test_get_current_user_unauthenticated_still_clean(session: AsyncSession) -> None:
    from fastapi import HTTPException

    await session.execute(text("SELECT 1"))
    with pytest.raises(HTTPException):
        await get_current_user(
            request=_request(),  # type: ignore[arg-type]
            auth=_StubAuth(None),  # type: ignore[arg-type]
            settings=Settings(environment="test"),
            session=session,
            redis_client=_NoopRedis(),  # type: ignore[arg-type]
            authorization=None,
            tiqora_session="tok",
        )
    assert not session.in_transaction()
