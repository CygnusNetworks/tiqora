"""Unit tests for domain/oauth2_mail — Znuny-compatible OAuth2 mail tokens."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tiqora.config import Settings
from tiqora.domain import oauth2_mail as m


def test_state_roundtrip() -> None:
    assert m.state_for_config_id(7) == "TokenConfigID7"
    assert m.parse_token_config_id_from_state("TokenConfigID7") == 7
    assert m.parse_token_config_id_from_state("nope") is None
    assert m.parse_token_config_id_from_state(None) is None


def test_state_nonce_roundtrip() -> None:
    # Anti-CSRF nonce is embedded in state and recoverable; config id still parses
    # for Znuny compatibility (security review).
    state = m.state_for_config_id(7, "abc-DEF_123")
    assert state == "TokenConfigID7.abc-DEF_123"
    assert m.parse_token_config_id_from_state(state) == 7
    assert m.parse_nonce_from_state(state) == "abc-DEF_123"
    # Bare (nonce-less) state has no nonce.
    assert m.parse_nonce_from_state("TokenConfigID7") is None
    assert m.parse_nonce_from_state("nope") is None


def test_sasl_xoauth2_raw_not_base64() -> None:
    raw = m.assemble_sasl_xoauth2_raw("user@example.com", "tok123")
    assert raw.startswith(b"user=user@example.com\x01auth=Bearer tok123")
    assert b"\x01\x01" in raw
    # Must NOT be base64 (imaplib encodes it).
    assert b"dXNlcj0" not in raw


def test_sasl_xoauth2_b64_matches_znuny_shape() -> None:
    b64 = m.assemble_sasl_xoauth2_b64("user@example.com", "tok123")
    from base64 import b64decode

    decoded = b64decode(b64)
    assert decoded == m.assemble_sasl_xoauth2_raw("user@example.com", "tok123")


def test_build_authorization_url_autofills() -> None:
    settings = Settings(public_base_url="https://helpdesk.example.com")
    config = m.TEMPLATE_MICROSOFT_EXCHANGE_ONLINE["config"].copy()
    config = json.loads(json.dumps(config))  # deep copy via JSON
    config["ClientID"] = "cid-1"
    config["ClientSecret"] = "sec"
    row = SimpleNamespace(id=3, config=json.dumps(config))
    url = m.build_authorization_url(row, settings=settings)  # type: ignore[arg-type]
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["cid-1"]
    assert qs["state"] == ["TokenConfigID3"]
    assert qs["redirect_uri"] == ["https://helpdesk.example.com/api/v1/oauth2/callback"]
    assert "scope" in qs
    assert qs["response_type"] == ["code"]


def test_token_status_lifecycle() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0)
    empty = SimpleNamespace(
        token=None,
        token_expiration_date=None,
        refresh_token=None,
        refresh_token_expiration_date=None,
        error_message=None,
        error_description=None,
        error_code=None,
    )
    assert m.token_status(None, now=now) == "none"
    assert m.token_status(empty, now=now) == "none"  # type: ignore[arg-type]

    valid = SimpleNamespace(
        token="abc",
        token_expiration_date=now + timedelta(hours=1),
        refresh_token="r",
        refresh_token_expiration_date=None,
        error_message=None,
        error_description=None,
        error_code=None,
    )
    assert m.token_status(valid, now=now) == "valid"  # type: ignore[arg-type]

    expired = SimpleNamespace(
        token="abc",
        token_expiration_date=now - timedelta(minutes=1),
        refresh_token="r",
        refresh_token_expiration_date=None,
        error_message=None,
        error_description=None,
        error_code=None,
    )
    assert m.token_status(expired, now=now) == "expired"  # type: ignore[arg-type]

    needs = SimpleNamespace(
        token="abc",
        token_expiration_date=now - timedelta(minutes=1),
        refresh_token=None,
        refresh_token_expiration_date=None,
        error_message=None,
        error_description=None,
        error_code=None,
    )
    assert m.token_status(needs, now=now) == "needs_reauth"  # type: ignore[arg-type]


def test_templates_cover_microsoft_and_google() -> None:
    ids = {t["id"] for t in m.list_provider_templates()}
    assert ids == {"microsoft-exchange-online", "google-mail"}
    for t in m.list_provider_templates():
        cfg = t["config"]
        assert "ClientID" in cfg
        assert "Requests" in cfg
        assert "AuthorizationCode" in cfg["Requests"]
        assert "TokenByAuthorizationCode" in cfg["Requests"]


@pytest.mark.asyncio
async def test_request_token_by_code_httpx_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exchange code against a mock token endpoint and map response fields."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = dict(parse_qs(request.content.decode()))
        # parse_qs values are lists
        assert body["grant_type"] == ["authorization_code"]
        assert body["code"] == ["auth-code-1"]
        assert body["client_id"] == ["cid"]
        return httpx.Response(
            200,
            json={
                "access_token": "access-xyz",
                "refresh_token": "refresh-xyz",
                "expires_in": 3600,
            },
        )

    transport = httpx.MockTransport(handler)
    settings = Settings(public_base_url="https://helpdesk.example.com")

    config = m.TEMPLATE_GOOGLE_MAIL["config"].copy()
    config = json.loads(json.dumps(config))
    config["ClientID"] = "cid"
    config["ClientSecret"] = "sec"
    config["Requests"]["TokenByAuthorizationCode"]["Request"]["URL"] = (
        "https://oauth2.googleapis.com/token"
    )

    # Minimal fake session/rows — exercise pure HTTP + mapping via private helpers.
    mapped = m._map_json_response(  # noqa: SLF001
        {
            "access_token": "access-xyz",
            "refresh_token": "refresh-xyz",
            "expires_in": 3600,
        },
        m._response_mapping(config, m.REQUEST_TOKEN_BY_CODE),  # noqa: SLF001
        now=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
    )
    assert mapped["Token"] == "access-xyz"
    assert mapped["RefreshToken"] == "refresh-xyz"
    assert isinstance(mapped["TokenExpirationDate"], datetime)

    status, body = await m._post_form(  # noqa: SLF001
        "https://oauth2.googleapis.com/token",
        {
            "grant_type": "authorization_code",
            "code": "auth-code-1",
            "client_id": "cid",
            "client_secret": "sec",
            "redirect_uri": m.get_redirect_uri(settings),
        },
        transport=transport,
    )
    assert status == 200
    assert body["access_token"] == "access-xyz"
    _ = monkeypatch


def test_get_redirect_uri_uses_public_base() -> None:
    s = Settings(public_base_url="https://x.example/")
    assert m.get_redirect_uri(s) == "https://x.example/api/v1/oauth2/callback"
