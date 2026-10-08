from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_admin_extensions_lists_channels_point() -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app
    from tiqora.api.deps import get_db
    from tiqora.api.v1.admin.deps import get_admin_user
    from tiqora.config import Settings
    from tiqora.domain.auth import AuthenticatedUser

    root = AuthenticatedUser(
        id=1, login="root@localhost", first_name="Root", last_name="Admin", auth_method="session"
    )

    async def _no_db() -> Any:
        yield None

    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_admin_user] = lambda: root
    app.dependency_overrides[get_db] = _no_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/admin/extensions")

    assert resp.status_code == 200
    points = {p["key"]: p for p in resp.json()}
    channels = points["core.channels"]
    assert {"key": "telegram", "owner": "core"} in channels["items"]
    assert channels["sdk_stable"] is False
