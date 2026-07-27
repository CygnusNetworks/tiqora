"""Unit tests for area RO/RW API-key scopes (no DB)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from tiqora.domain.api_key_scopes import (
    API_KEY_AREAS,
    InvalidApiKeyScopeError,
    all_areas_read_only_scopes,
    assert_scope_allows,
    expand_scopes,
    mcp_scopes_allow_connect,
    mcp_scopes_allow_write,
    normalize_scopes,
    parse_api_key_scopes,
    path_to_area,
    scopes_allow,
)


def test_parse_empty_is_unrestricted() -> None:
    assert parse_api_key_scopes(None) is None
    assert parse_api_key_scopes("") is None
    assert parse_api_key_scopes("  ,  ") is None


def test_normalize_sorts_and_dedupes() -> None:
    assert normalize_scopes("kb:rw, tickets:ro, kb:rw") == "kb:rw,tickets:ro"
    assert normalize_scopes("* ,read") == "*"
    assert normalize_scopes(None) is None
    assert normalize_scopes("") is None


def test_normalize_rejects_unknown() -> None:
    with pytest.raises(InvalidApiKeyScopeError) as exc:
        normalize_scopes("tickets:rw,foo:ro")
    assert "foo:ro" in str(exc.value)


def test_expand_legacy_read_write() -> None:
    ro = expand_scopes(frozenset({"read"}))
    assert ro is not None
    assert ro["tickets"] == "ro"
    assert ro["kb"] == "ro"
    assert "mcp" not in ro

    rw = expand_scopes(frozenset({"write"}))
    assert rw is not None
    assert rw["tickets"] == "rw"
    assert rw["admin"] == "rw"


def test_expand_area_tokens_and_merge() -> None:
    expanded = expand_scopes(frozenset({"tickets:ro", "kb:rw", "tickets:rw"}))
    assert expanded == {"tickets": "rw", "kb": "rw"}


def test_expand_star_and_none_unrestricted() -> None:
    assert expand_scopes(None) is None
    assert expand_scopes(frozenset({"*"})) is None


def test_path_to_area_mapping() -> None:
    assert path_to_area("/api/v1/tickets/12") == "tickets"
    assert path_to_area("/api/v1/queues") == "tickets"
    assert path_to_area("/api/v1/kb/articles") == "kb"
    assert path_to_area("/api/v1/admin/api-keys") == "admin"
    assert path_to_area("/api/v1/auth/me") is None
    assert path_to_area("/znuny-compat/TicketGet") == "compat"
    assert path_to_area("/api/v1/unknown-thing") == "_unknown"


def test_scopes_allow_area_ro_rw() -> None:
    scopes = frozenset({"tickets:ro", "kb:rw"})
    assert scopes_allow(scopes, method="GET", path="/api/v1/tickets")
    assert not scopes_allow(scopes, method="POST", path="/api/v1/tickets")
    assert scopes_allow(scopes, method="POST", path="/api/v1/kb/articles")
    assert not scopes_allow(scopes, method="GET", path="/api/v1/calendar")
    assert scopes_allow(scopes, method="GET", path="/api/v1/auth/me")


def test_scopes_allow_unrestricted() -> None:
    assert scopes_allow(None, method="DELETE", path="/api/v1/tickets/1")
    assert scopes_allow(frozenset({"*"}), method="POST", path="/api/v1/admin/users")


def test_assert_scope_allows_raises_403() -> None:
    with pytest.raises(HTTPException) as exc:
        assert_scope_allows(
            frozenset({"tickets:ro"}),
            method="POST",
            path="/api/v1/tickets",
        )
    assert exc.value.status_code == 403
    assert "tickets:rw" in str(exc.value.detail)


def test_legacy_write_allows_mutate() -> None:
    scopes = frozenset({"write"})
    assert scopes_allow(scopes, method="POST", path="/api/v1/tickets")
    assert scopes_allow(scopes, method="GET", path="/api/v1/kb")


def test_mcp_connect_and_write() -> None:
    assert mcp_scopes_allow_connect(None)
    assert mcp_scopes_allow_write(None)
    assert mcp_scopes_allow_connect(frozenset({"mcp:ro"}))
    assert not mcp_scopes_allow_write(frozenset({"mcp:ro"}))
    assert mcp_scopes_allow_write(frozenset({"mcp:rw"}))
    assert mcp_scopes_allow_write(frozenset({"mcp"}))  # legacy
    assert not mcp_scopes_allow_connect(frozenset({"tickets:rw"}))


def test_all_areas_read_only_preset() -> None:
    raw = all_areas_read_only_scopes()
    scopes = parse_api_key_scopes(raw)
    assert scopes is not None
    for area in API_KEY_AREAS:
        assert f"{area}:ro" in scopes
    expanded = expand_scopes(scopes)
    assert expanded is not None
    assert all(v == "ro" for v in expanded.values())
    assert not scopes_allow(scopes, method="POST", path="/api/v1/tickets")
    assert scopes_allow(scopes, method="GET", path="/api/v1/tickets")
