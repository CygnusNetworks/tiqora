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
    assert path_to_area("/api/plugins/org.example.hello/hello") == "plugins"


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
    for area in (a for a in API_KEY_AREAS if a != "plugins"):
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
        # Plugin frontend list and static files: only the SPA (session cookie) uses them;
        # scoped integration keys have no use for them. Plugin HTTP routes themselves live
        # under /api/plugins and have the "plugins" area.
        "/api/v1/plugins/frontend",
        "/api/v1/plugins/{plugin_id}/assets/{version}/{path}",
    }
)
# Prefixes whose routes are deliberately sessions/unrestricted-keys only (R-DASH-APIKEY:
# the dashboard data route mixes the tickets, calendar and agents areas).
_UNSCOPED_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/v1/dashboards",
    "/api/v1/dashboard-widget-types",
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
        if p.startswith("/api/v1/")
        and p not in _UNSCOPED_EXEMPT
        and not p.startswith(_UNSCOPED_EXEMPT_PREFIXES)
        and path_to_area(p) == "_unknown"
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


def test_dashboards_stay_unknown_so_only_sessions_and_unrestricted_keys_get_in() -> None:
    """R-DASH-APIKEY: /data mixes tickets, calendar and agents, so no single area fits."""
    paths = (
        "/api/v1/dashboards",
        "/api/v1/dashboards/1",
        "/api/v1/dashboards/1/data",
        "/api/v1/dashboards/1/widgets/wid00001/settings",
        "/api/v1/dashboard-widget-types",
        "/api/v1/dashboard-widget-types/core.counter/data",
    )
    for path in paths:
        assert path_to_area(path) == "_unknown", path
        assert path_to_areas(path) == ("_unknown",), path
    # the admin templates keep their admin area by prefix
    assert path_to_area("/api/v1/admin/dashboard-templates") == "admin"
    assert path_to_area("/api/v1/admin/dashboard-kiosk-links") == "admin"
    assert path_to_area("/api/v1/admin/dashboard-kiosk-links/1/revoke") == "admin"
    for scopes in (
        frozenset({"tickets:rw"}),
        frozenset({"tickets:ro"}),
        frozenset({"read"}),
        frozenset({"write"}),
        frozenset({"stats:rw"}),
    ):
        for method in ("GET", "PUT", "PATCH", "DELETE", "POST"):
            for path in ("/api/v1/dashboards/1", "/api/v1/dashboard-widget-types"):
                assert not scopes_allow(scopes, method=method, path=path), (scopes, method, path)
        with pytest.raises(HTTPException) as exc:
            assert_scope_allows(scopes, method="GET", path="/api/v1/dashboards/1")
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            assert_scope_allows(scopes, method="PUT", path="/api/v1/dashboards/1")
        assert exc.value.status_code == 403
    for method in ("GET", "PUT", "DELETE"):
        assert scopes_allow(None, method=method, path="/api/v1/dashboards/1")
        assert_scope_allows(None, method=method, path="/api/v1/dashboards/1")


def test_plugins_area_scopes() -> None:
    path = "/api/plugins/org.example.hello/echo"
    assert not scopes_allow(frozenset({"kb:rw", "tickets:rw"}), method="GET", path=path)
    assert scopes_allow(frozenset({"plugins:ro"}), method="GET", path=path)
    assert not scopes_allow(frozenset({"plugins:ro"}), method="POST", path=path)
    assert scopes_allow(frozenset({"plugins:rw"}), method="POST", path=path)
    assert scopes_allow(None, method="POST", path=path)


def test_read_only_preset_excludes_plugins() -> None:
    raw = all_areas_read_only_scopes()
    assert "plugins" not in raw and "kb:ro" in raw
    path = "/api/plugins/org.example.hello/hello"
    assert not scopes_allow(parse_api_key_scopes(raw), method="GET", path=path)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/plugins/frontend", "/api/v1/plugins/org.example.hello/assets/1.0.0/web/index.js"],
)
def test_scoped_keys_cannot_reach_plugin_frontend_paths(path: str) -> None:
    """Only the SPA session uses these; a future area mapping must not widen access silently."""
    for scopes in ({"plugins:rw"}, {"plugins:ro"}, {"read"}, {"write"}, {"kb:ro"}, {"admin:rw"}):
        assert not scopes_allow(frozenset(scopes), method="GET", path=path), scopes
    assert scopes_allow(None, method="GET", path=path)


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


def test_tickets_query_is_a_post_and_needs_tickets_rw() -> None:
    # POST /tickets/query only reads, but like POST /ticket-filters/count it is a POST:
    # a read-only key cannot call it (R-APIKEY), a tickets:rw key can.
    path = "/api/v1/tickets/query"
    assert path_to_area(path) == "tickets"
    assert not scopes_allow(frozenset({"tickets:ro"}), method="POST", path=path)
    assert not scopes_allow(frozenset({"read"}), method="POST", path=path)
    assert scopes_allow(frozenset({"tickets:rw"}), method="POST", path=path)
    assert scopes_allow(frozenset({"tickets:ro"}), method="GET", path="/api/v1/tickets")


def test_saved_searches_map_to_tickets_and_writes_need_rw() -> None:
    # R-APIKEY: list/get/counts read, create/update/prefs/delete write (prefs is a PUT).
    base = "/api/v1/saved-searches"
    for path in (base, base + "/counts", base + "/5", base + "/5/prefs"):
        assert path_to_area(path) == "tickets", path
    assert scopes_allow(frozenset({"tickets:ro"}), method="GET", path=base + "/counts")
    for method, path in (
        ("POST", base),
        ("PUT", base + "/5"),
        ("PUT", base + "/5/prefs"),
        ("DELETE", base + "/5"),
    ):
        assert not scopes_allow(frozenset({"tickets:ro"}), method=method, path=path), path
        assert scopes_allow(frozenset({"tickets:rw"}), method=method, path=path), path
    assert not scopes_allow(frozenset({"kb:rw"}), method="GET", path=base)


def test_znuny_import_preview_reads_and_apply_writes() -> None:
    # R-APIKEY: the preview only reads Znuny, applying creates saved searches
    path = "/api/v1/saved-searches/import/znuny"
    assert path_to_area(path) == "tickets"
    assert scopes_allow(frozenset({"tickets:ro"}), method="GET", path=path)
    assert not scopes_allow(frozenset({"tickets:ro"}), method="POST", path=path)
    assert scopes_allow(frozenset({"tickets:rw"}), method="POST", path=path)


def test_agent_chat_api_maps_to_tickets() -> None:
    """R-CHAT-SCOPE: ``/api/v1/chat/*`` is the ``tickets`` area (GET ro, writes rw)."""
    accept = "/api/v1/chat/sessions/0123456789abcdef0123456789abcdef/accept"
    for path in ("/api/v1/chat/inbox", "/api/v1/chat/agent-state", "/api/v1/chat/topics", accept):
        assert path_to_area(path) == "tickets", path
    assert path_to_area("/api/v1/tickets/5/chat/typing") == "tickets"
    ro, rw, kb = frozenset({"tickets:ro"}), frozenset({"tickets:rw"}), frozenset({"kb:rw"})
    assert scopes_allow(ro, method="GET", path="/api/v1/chat/inbox")
    assert not scopes_allow(ro, method="POST", path=accept)
    assert not scopes_allow(ro, method="PUT", path="/api/v1/chat/agent-state")
    assert scopes_allow(rw, method="POST", path=accept)
    assert scopes_allow(rw, method="GET", path="/api/v1/chat/inbox")
    assert not scopes_allow(kb, method="GET", path="/api/v1/chat/inbox")
    assert not scopes_allow(kb, method="POST", path=accept)
    with pytest.raises(HTTPException) as exc:
        assert_scope_allows(ro, method="POST", path=accept)
    assert exc.value.status_code == 403
    assert exc.value.detail == "API key lacks tickets:rw scope"
    with pytest.raises(HTTPException) as exc:
        assert_scope_allows(kb, method="GET", path="/api/v1/chat/inbox")
    assert exc.value.detail == "API key lacks tickets:ro scope"
