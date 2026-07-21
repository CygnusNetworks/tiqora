"""Tests for the TOTP enrollment QR endpoint and SVG rendering.

The pure rendering function is tested directly; the endpoint is tested via
FastAPI dependency overrides (no DB/Redis fixture needed) — mirrors the
pattern used for auth-adjacent unit tests like test_spnego.py.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user_or_enroll, get_totp_service
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.totp_qr import totp_qr_svg


def test_totp_qr_svg_is_well_formed_svg() -> None:
    uri = "otpauth://totp/Tiqora:alice?secret=JBSWY3DPEHPK3PXP&issuer=Tiqora"
    svg = totp_qr_svg(uri)
    assert svg.startswith("<?xml") or "<svg" in svg
    assert "<svg" in svg
    assert svg.strip().endswith("</svg>")


class _FakeTOTPService:
    def __init__(self, uri: str | None) -> None:
        self._uri = uri

    async def get_pending_provisioning_uri(self, user_id: int, login: str) -> str | None:
        return self._uri


def _fake_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        id=1, login="alice", first_name="Alice", last_name="Agent", auth_method="session"
    )


@pytest.mark.asyncio
async def test_totp_enroll_qr_returns_svg_for_pending_enrollment() -> None:
    settings = Settings(secret_key="unit-test-secret-key")
    app = create_app(settings)
    pending_uri = "otpauth://totp/Tiqora:alice?secret=JBSWY3DPEHPK3PXP&issuer=Tiqora"

    app.dependency_overrides[get_current_user_or_enroll] = _fake_user
    app.dependency_overrides[get_totp_service] = lambda: _FakeTOTPService(pending_uri)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/totp/enroll/qr")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert "<svg" in resp.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_totp_enroll_qr_404_without_pending_enrollment() -> None:
    settings = Settings(secret_key="unit-test-secret-key")
    app = create_app(settings)

    app.dependency_overrides[get_current_user_or_enroll] = _fake_user
    app.dependency_overrides[get_totp_service] = lambda: _FakeTOTPService(None)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/totp/enroll/qr")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_totp_enroll_qr_requires_auth() -> None:
    settings = Settings(secret_key="unit-test-secret-key")
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/totp/enroll/qr")
    assert resp.status_code == 401
