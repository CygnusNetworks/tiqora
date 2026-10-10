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
    path_to_areas,
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


def test_restricted_key_cannot_mutate_auth_surface() -> None:
    # /api/v1/auth is always-open for identity/plumbing, but a restricted key
    # must NOT be able to POST/DELETE auth state (TOTP enroll/confirm, passkey
    # delete). Safe methods stay open (security review regression).
    scopes = parse_api_key_scopes(all_areas_read_only_scopes())
    assert scopes is not None
    assert scopes_allow(scopes, method="GET", path="/api/v1/auth/me")
    assert not scopes_allow(scopes, method="POST", path="/api/v1/auth/totp/enroll")
    assert not scopes_allow(scopes, method="POST", path="/api/v1/auth/totp/confirm")
    assert not scopes_allow(scopes, method="DELETE", path="/api/v1/auth/passkey/5")
    # A narrowly-scoped key is likewise blocked from mutating the auth surface.
    narrow = parse_api_key_scopes("tickets:ro")
    assert not scopes_allow(narrow, method="POST", path="/api/v1/auth/totp/enroll")
    # Unrestricted keys retain full access.
    assert scopes_allow(None, method="POST", path="/api/v1/auth/totp/enroll")


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


def test_integrations_path_maps_to_tickets_area() -> None:
    path = "/api/v1/integrations/customer-tickets"
    assert path_to_area(path) == "tickets"
    ro = frozenset({"tickets:ro"})
    assert scopes_allow(ro, method="GET", path=path + "?login=z50test")
    assert not scopes_allow(ro, method="POST", path=path)
    assert not scopes_allow(frozenset({"customers:ro"}), method="GET", path=path)


# Mounted /api/v1 paths that deliberately have no scope area. Add a path only if it is
# meant to be unreachable for scoped keys.
_UNSCOPED_EXEMPT: frozenset[str] = frozenset(
    {
        # Browser redirect target of the OAuth2 provider (no API key involved).
        "/api/v1/oauth2/callback",
    }
)


def test_every_api_v1_route_resolves_to_a_known_scope_area() -> None:
    """A new router without a prefix in ``_PATH_PREFIX_TO_AREA`` would 403 scoped keys."""
    from tiqora.api.app import create_app
    from tiqora.config import Settings

    app = create_app(Settings(environment="test"))
    # FastAPI wraps included routers (``app.routes`` is not flat); the OpenAPI
    # schema lists every mounted path.
    paths = app.openapi()["paths"]
    assert any(p.startswith("/api/v1/jobs") for p in paths)  # guards the walk itself
    unknown = sorted(
        p
        for p in paths
        if p.startswith("/api/v1/") and p not in _UNSCOPED_EXEMPT and path_to_area(p) == "_unknown"
    )
    assert unknown == []


def test_jobs_and_ticket_filters_map_to_tickets() -> None:
    assert path_to_area("/api/v1/jobs/5/files/3") == "tickets"
    assert path_to_area("/api/v1/ticket-filters/count") == "tickets"
    assert path_to_area("/api/v1/customer-directory/vcards") == "customers"
    assert scopes_allow(frozenset({"tickets:rw"}), method="GET", path="/api/v1/jobs/5/files/3")


def test_jobs_accept_a_tickets_or_a_stats_key() -> None:
    """I3: a stats-only key polls, downloads and cancels the report jobs it started."""
    files = "/api/v1/jobs/5/files/3"
    for scopes in ({"stats:ro"}, {"stats:rw"}, {"tickets:ro"}, {"tickets:rw"}):
        assert scopes_allow(frozenset(scopes), method="GET", path="/api/v1/jobs/5"), scopes
        assert scopes_allow(frozenset(scopes), method="GET", path=files), scopes
    cancel = "/api/v1/jobs/5/cancel"
    assert scopes_allow(frozenset({"stats:rw"}), method="POST", path=cancel)
    assert not scopes_allow(frozenset({"stats:ro"}), method="POST", path=cancel)
    assert not scopes_allow(frozenset({"stats:ro", "tickets:ro"}), method="POST", path=cancel)
    assert scopes_allow(frozenset({"stats:ro", "tickets:rw"}), method="POST", path=cancel)
    assert not scopes_allow(frozenset({"kb:rw"}), method="GET", path="/api/v1/jobs/5")
    assert path_to_areas("/api/v1/jobs/5") == ("tickets", "stats")
    assert path_to_areas("/api/v1/reports/run") == ("stats",)
    assert path_to_areas("/api/v1/auth/me") == ()
    with pytest.raises(HTTPException) as exc:
        assert_scope_allows(frozenset({"kb:ro"}), method="GET", path="/api/v1/jobs/5")
    assert exc.value.detail == "API key lacks tickets:ro scope"


def test_reports_and_reporting_map_to_stats() -> None:
    for path in (
        "/api/v1/reports",
        "/api/v1/reports/builtin.channel_mix",
        "/api/v1/reports/run",
        "/api/v1/reports/export",
        "/api/v1/reporting/sources",
        "/api/v1/reporting/status",
    ):
        assert path_to_area(path) == "stats", path
    ro, rw = frozenset({"stats:ro"}), frozenset({"stats:rw"})
    assert scopes_allow(ro, method="GET", path="/api/v1/reports")
    assert scopes_allow(ro, method="GET", path="/api/v1/reporting/sources")
    assert not scopes_allow(ro, method="POST", path="/api/v1/reports/run")  # enqueues jobs
    assert scopes_allow(rw, method="POST", path="/api/v1/reports/run")
    assert scopes_allow(rw, method="POST", path="/api/v1/reports/export")
    assert not scopes_allow(frozenset({"tickets:rw"}), method="GET", path="/api/v1/reports")
    # the admin reporting page stays admin-only
    assert path_to_area("/api/v1/admin/reporting/status") == "admin"


def test_saved_report_routes_and_share_groups_scopes() -> None:
    """Saved-report CRUD and files fall under ``stats``; share groups under ``tickets``."""
    for path in (
        "/api/v1/reports/12",
        "/api/v1/reports/12/file",
        "/api/v1/reports/import-file",
    ):
        assert path_to_area(path) == "stats", path
    assert path_to_area("/api/v1/reference/share-groups") == "tickets"
    ro, rw = frozenset({"stats:ro"}), frozenset({"stats:rw"})
    assert scopes_allow(ro, method="GET", path="/api/v1/reports/12/file")
    for method, path in (
        ("POST", "/api/v1/reports"),
        ("PUT", "/api/v1/reports/12"),
        ("DELETE", "/api/v1/reports/12"),
    ):
        assert not scopes_allow(ro, method=method, path=path), (method, path)
        assert scopes_allow(rw, method=method, path=path), (method, path)
    assert scopes_allow(
        frozenset({"tickets:ro"}), method="GET", path="/api/v1/reference/share-groups"
    )
    assert not scopes_allow(ro, method="GET", path="/api/v1/reference/share-groups")
