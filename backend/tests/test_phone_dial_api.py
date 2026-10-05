"""POST /api/v1/phone/dial: session-only, extension, number, rate limit, ARI."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tiqora.api.deps import get_current_user, get_db, get_redis
from tiqora.channels.phone.originate import OriginateConfig, OriginateError
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser

CFG = OriginateConfig(
    ari_url="http://pbx-asterisk:8088/ari",
    ari_user="tiqora",
    ari_secret="pw",
    context="tiqora-dial",
    endpoint="SIP/{extension}",
    timeout=30,
    internal=frozenset({"60", "61", "62", "69"}),
)


class _Redis:
    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> Any:
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


def _app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    auth_method: str = "session",
    config: OriginateConfig | None = CFG,
    extensions: list[str] | None = None,
    originate: Any = None,
) -> Any:
    from tiqora.api.app import create_app
    from tiqora.api.v1 import phone_cti

    user = AuthenticatedUser(
        id=7, login="agent7", first_name="Ada", last_name="Agent", auth_method=auth_method
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_current_user] = lambda: user
    redis = _Redis()
    app.dependency_overrides[get_redis] = lambda: redis

    async def _db() -> Any:
        yield MagicMock()

    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(phone_cti, "load_originate_config", AsyncMock(return_value=config))
    monkeypatch.setattr(
        phone_cti,
        "extensions_for_user",
        AsyncMock(return_value=["60"] if extensions is None else extensions),
    )
    monkeypatch.setattr(phone_cti, "originate", originate or AsyncMock(return_value=None))
    return app


async def _dial(app: Any, body: dict[str, Any]) -> Any:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/phone/dial", json=body)


async def test_dial_rings_the_agents_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = AsyncMock(return_value=None)
    app = _app(monkeypatch, originate=orig)
    resp = await _dial(app, {"number": "+49 171 7630944", "name": "Kettler"})
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"extension": "60", "number": "01717630944"}
    orig.assert_awaited_once_with(CFG, "60", "01717630944", "Kettler")


async def test_dial_rejects_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await _dial(_app(monkeypatch, auth_method="api_key"), {"number": "01717630944"})
    assert resp.status_code == 403


async def test_dial_404_when_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await _dial(_app(monkeypatch, config=None), {"number": "01717630944"})
    assert resp.status_code == 404


async def test_dial_409_without_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await _dial(_app(monkeypatch, extensions=[]), {"number": "01717630944"})
    assert resp.status_code == 409


async def test_dial_422_on_bad_number(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = await _dial(_app(monkeypatch), {"number": "+4930"})
    assert resp.status_code == 422


async def test_dial_429_within_five_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    from httpx import ASGITransport, AsyncClient

    app = _app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/phone/dial", json={"number": "01717630944"})
        second = await client.post("/api/v1/phone/dial", json={"number": "01717630944"})
    assert first.status_code == 202
    assert second.status_code == 429


async def test_dial_502_when_ari_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = AsyncMock(side_effect=OriginateError("ARI 400: Endpoint not found"))
    resp = await _dial(_app(monkeypatch, originate=orig), {"number": "01717630944"})
    assert resp.status_code == 502
