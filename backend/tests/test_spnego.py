"""Unit tests for Kerberos/SPNEGO negotiation.

``gssapi`` calls are indirected through ``_import_gssapi`` so we can swap in
a fake module here instead of requiring a real KDC/keytab. See
docs/deployment.md for manual KDC verification steps.
"""

from __future__ import annotations

import base64

import pytest

from tiqora.config import Settings
from tiqora.domain import spnego as spnego_module
from tiqora.domain.spnego import SpnegoService, SpnegoUnavailable, principal_to_login


class _FakeCredentials:
    def __init__(self, usage: str) -> None:
        self.usage = usage


class _FakeSecurityContext:
    """Fake single-leg gssapi.SecurityContext: step() completes immediately."""

    def __init__(self, creds: _FakeCredentials, usage: str, *, principal: str) -> None:
        self.creds = creds
        self.usage = usage
        self.complete = False
        self._principal = principal
        self.initiator_name = principal

    def step(self, token: bytes) -> bytes:
        assert token == b"client-token"
        self.complete = True
        self.initiator_name = self._principal
        return b"server-token"


def _fake_gssapi_module(principal: str = "alice@EXAMPLE.COM") -> object:
    class _Module:
        Credentials = staticmethod(lambda usage: _FakeCredentials(usage))

        @staticmethod
        def SecurityContext(creds: _FakeCredentials, usage: str) -> _FakeSecurityContext:
            return _FakeSecurityContext(creds, usage, principal=principal)

    return _Module()


def test_principal_to_login_strips_realm_and_service_component() -> None:
    assert principal_to_login("alice@EXAMPLE.COM") == "alice"
    assert principal_to_login("HTTP/tiqora.example.com@EXAMPLE.COM") == "HTTP"
    assert principal_to_login("bob") == "bob"


@pytest.mark.asyncio
async def test_accept_returns_principal_with_mocked_gssapi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        spnego_module, "_import_gssapi", lambda: _fake_gssapi_module("alice@EXAMPLE.COM")
    )
    settings = Settings(spnego_enabled=True)
    service = SpnegoService(settings)
    principal = await service.accept(b"client-token")
    assert principal == "alice@EXAMPLE.COM"
    assert principal_to_login(principal) == "alice"


@pytest.mark.asyncio
async def test_accept_raises_when_gssapi_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> object:
        raise SpnegoUnavailable("gssapi is not installed; install the 'kerberos' extra")

    monkeypatch.setattr(spnego_module, "_import_gssapi", _raise)
    settings = Settings(spnego_enabled=True)
    service = SpnegoService(settings)
    with pytest.raises(SpnegoUnavailable):
        await service.accept(b"client-token")


@pytest.mark.asyncio
async def test_spnego_endpoint_501_when_gssapi_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No Negotiate header -> 401; Negotiate header + missing gssapi -> 501."""
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app

    def _raise() -> object:
        raise SpnegoUnavailable("gssapi is not installed")

    monkeypatch.setattr(spnego_module, "_import_gssapi", _raise)

    settings = Settings(spnego_enabled=True)
    app = create_app(settings)
    token = base64.b64encode(b"client-token").decode("ascii")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp_no_header = await client.get("/api/v1/auth/spnego")
        assert resp_no_header.status_code == 401
        assert resp_no_header.headers.get("www-authenticate") == "Negotiate"

        resp = await client.get(
            "/api/v1/auth/spnego", headers={"Authorization": f"Negotiate {token}"}
        )
        assert resp.status_code == 501


@pytest.mark.asyncio
async def test_spnego_endpoint_404_when_disabled() -> None:
    from httpx import ASGITransport, AsyncClient

    from tiqora.api.app import create_app

    settings = Settings(spnego_enabled=False)
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/spnego")
        assert resp.status_code == 404
