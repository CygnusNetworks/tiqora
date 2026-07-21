"""H-1: Authorization: Bearer must only accept tiqora_* API keys, not session tokens."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.deps import get_current_user
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser

pytestmark = pytest.mark.db

_USER = AuthenticatedUser(
    id=42, login="agent", first_name="A", last_name="G", auth_method="api_key"
)


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


class _StubAuth:
    def __init__(
        self,
        *,
        session_user: AuthenticatedUser | None = None,
        api_key_user: AuthenticatedUser | None = None,
    ) -> None:
        self._session_user = session_user
        self._api_key_user = api_key_user
        self.session_calls: list[str] = []
        self.api_key_calls: list[str] = []

    async def resolve_session(self, token: str) -> AuthenticatedUser | None:
        self.session_calls.append(token)
        return self._session_user if token else None

    async def resolve_api_key(self, raw: str) -> AuthenticatedUser | None:
        self.api_key_calls.append(raw)
        if self._api_key_user is not None and raw.startswith("tiqora_"):
            return self._api_key_user
        return None


class _NoopRedis:
    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del key, value, ex


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(), cookies={})


@pytest.fixture
async def session(postgres_znuny_url: str) -> AsyncSession:
    engine = create_async_engine(_to_async_url(postgres_znuny_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_bearer_raw_session_token_rejected(session: AsyncSession) -> None:
    """Opaque session tokens as Bearer must not authenticate (H-1)."""
    auth = _StubAuth(session_user=_USER)
    with pytest.raises(HTTPException) as exc:
        await get_current_user(
            request=_request(),  # type: ignore[arg-type]
            auth=auth,  # type: ignore[arg-type]
            settings=Settings(environment="test"),
            session=session,
            redis_client=_NoopRedis(),  # type: ignore[arg-type]
            authorization="Bearer opaque-session-token-not-an-api-key",
            tiqora_session=None,
        )
    assert exc.value.status_code == 401
    # Must not even attempt session resolve for non-tiqora_ bearer tokens.
    assert auth.session_calls == []
    assert auth.api_key_calls == []


@pytest.mark.asyncio
async def test_bearer_tiqora_api_key_still_works(session: AsyncSession) -> None:
    """tiqora_* API keys remain valid Bearer credentials (MCP/CLI)."""
    auth = _StubAuth(api_key_user=_USER)
    user = await get_current_user(
        request=_request(),  # type: ignore[arg-type]
        auth=auth,  # type: ignore[arg-type]
        settings=Settings(environment="test"),
        session=session,
        redis_client=_NoopRedis(),  # type: ignore[arg-type]
        authorization="Bearer tiqora_test_key_abcdef0123456789",
        tiqora_session=None,
    )
    assert user is _USER
    assert auth.api_key_calls == ["tiqora_test_key_abcdef0123456789"]
    assert auth.session_calls == []


@pytest.mark.asyncio
async def test_session_cookie_still_works(session: AsyncSession) -> None:
    """Cookie session path is unchanged — only Bearer session tokens are blocked."""
    auth = _StubAuth(session_user=_USER)
    user = await get_current_user(
        request=_request(),  # type: ignore[arg-type]
        auth=auth,  # type: ignore[arg-type]
        settings=Settings(environment="test"),
        session=session,
        redis_client=_NoopRedis(),  # type: ignore[arg-type]
        authorization=None,
        tiqora_session="cookie-session-token",
    )
    assert user is _USER
    assert auth.session_calls == ["cookie-session-token"]
