"""API-key surface scopes: coarse tokens + per-area RO/RW.

Storage remains the ``tiqora_api_key.scopes`` CSV column. Empty/NULL means
unrestricted (full privileges of the bound user). Tokens:

* ``*`` — unrestricted
* ``read`` — legacy: every REST area as ``:ro``
* ``write`` — legacy: every REST area as ``:rw``
* ``mcp`` — legacy: ``mcp:rw``
* ``<area>:ro`` / ``<area>:rw`` — area-level access

Queue/group permissions of the bound user stay orthogonal.
"""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, status

ScopeLevel = Literal["ro", "rw"]

# REST + MCP/compat surface areas selectable in admin UI / CLI.
API_KEY_AREAS: tuple[str, ...] = (
    "tickets",
    "customers",
    "kb",
    "calendar",
    "stats",
    "ai",
    "process",
    "channels",
    "events",
    "agents",
    "admin",
    "mcp",
    "compat",
)

API_KEY_AREAS_SET: frozenset[str] = frozenset(API_KEY_AREAS)

# Areas expanded by legacy ``read`` / ``write`` (not mcp/compat unless explicit).
_REST_AREAS: tuple[str, ...] = tuple(a for a in API_KEY_AREAS if a not in ("mcp", "compat"))

_LEGACY_TOKENS: frozenset[str] = frozenset({"read", "write", "mcp", "*"})

_SAFE_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})

# Path prefixes under /api/v1 (and a few top-level mounts) → area.
# Longer / more specific prefixes should be checked first when matching.
_PATH_PREFIX_TO_AREA: tuple[tuple[str, str], ...] = (
    ("/api/v1/admin", "admin"),
    ("/api/v1/tickets", "tickets"),
    ("/api/v1/queues", "tickets"),
    ("/api/v1/reference", "tickets"),
    ("/api/v1/templates", "tickets"),
    ("/api/v1/search", "tickets"),
    ("/api/v1/customers", "customers"),
    ("/api/v1/kb", "kb"),
    ("/api/v1/calendar", "calendar"),
    ("/api/v1/stats", "stats"),
    ("/api/v1/ai", "ai"),
    ("/api/v1/process", "process"),
    ("/api/v1/channels", "channels"),
    ("/api/v1/events", "events"),
    ("/api/v1/agents", "agents"),
    ("/api/v1/sse", "events"),
    ("/znuny-compat", "compat"),
)

# Always allowed for any valid API key (identity / session plumbing).
_ALWAYS_ALLOWED_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth",
    "/api/v1/health",
    "/health",
    "/metrics",
)


class InvalidApiKeyScopeError(ValueError):
    """Raised when a scope token is not in the allowed vocabulary."""


def parse_api_key_scopes(raw: str | None) -> frozenset[str] | None:
    """Parse scopes column; None/empty means unrestricted."""
    if raw is None:
        return None
    parts = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return frozenset(parts) if parts else None


def _is_area_token(token: str) -> bool:
    if ":" not in token:
        return False
    area, _, level = token.partition(":")
    return area in API_KEY_AREAS_SET and level in ("ro", "rw")


def validate_scope_tokens(parts: list[str]) -> None:
    """Raise InvalidApiKeyScopeError if any token is unknown."""
    unknown: list[str] = []
    for p in parts:
        if p in _LEGACY_TOKENS or _is_area_token(p):
            continue
        unknown.append(p)
    if unknown:
        allowed = sorted(_LEGACY_TOKENS) + [f"{a}:ro/{a}:rw" for a in API_KEY_AREAS]
        raise InvalidApiKeyScopeError(
            f"Unknown API key scope(s): {sorted(unknown)}. Allowed: {allowed}"
        )


def normalize_scopes(raw: str | None) -> str | None:
    """Normalize comma-separated scopes; reject unknown tokens.

    Returns sorted CSV or None for unrestricted (empty input).
    """
    if raw is None:
        return None
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    validate_scope_tokens(parts)
    # Deduplicate; if * is present, store only *
    unique = set(parts)
    if "*" in unique:
        return "*"
    return ",".join(sorted(unique))


def all_areas_read_only_scopes() -> str:
    """CSV granting :ro on every catalog area (admin 'read-only' preset)."""
    return ",".join(f"{a}:ro" for a in API_KEY_AREAS)


def expand_scopes(scopes: frozenset[str] | None) -> dict[str, ScopeLevel] | None:
    """Expand tokens to ``{area: ro|rw}``.

    Returns ``None`` for unrestricted (``scopes is None`` or contains ``*``).
    """
    if scopes is None or "*" in scopes:
        return None

    out: dict[str, ScopeLevel] = {}

    def set_level(area: str, level: ScopeLevel) -> None:
        prev = out.get(area)
        if prev == "rw" or level == "rw":
            out[area] = "rw"
        else:
            out[area] = "ro"

    if "read" in scopes:
        for a in _REST_AREAS:
            set_level(a, "ro")
    if "write" in scopes:
        for a in _REST_AREAS:
            set_level(a, "rw")
    if "mcp" in scopes:
        set_level("mcp", "rw")

    for token in scopes:
        if ":" not in token:
            continue
        area, _, level = token.partition(":")
        if area not in API_KEY_AREAS_SET or level not in ("ro", "rw"):
            continue
        set_level(area, level)  # type: ignore[arg-type]

    return out


def path_to_area(path: str) -> str | None:
    """Map a request path to a scope area, or None if unscoped/always-open."""
    # Strip query string if callers pass full URL path+query
    path = path.split("?", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    for prefix in _ALWAYS_ALLOWED_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return None
    for prefix, area in _PATH_PREFIX_TO_AREA:
        if path == prefix or path.startswith(prefix + "/"):
            return area
    # Unknown /api/v1/* paths: require unrestricted or deny via no-area match
    if path.startswith("/api/v1/") or path.startswith("/znuny-compat"):
        return "_unknown"
    return None


def method_needs_rw(method: str) -> bool:
    return method.upper() not in _SAFE_METHODS


def scopes_allow(
    scopes: frozenset[str] | None,
    *,
    method: str,
    path: str,
) -> bool:
    """Return True if the key may perform ``method`` on ``path``."""
    expanded = expand_scopes(scopes)
    if expanded is None:
        return True
    area = path_to_area(path)
    if area is None:
        return True
    if area == "_unknown":
        return False
    have = expanded.get(area)
    if have is None:
        return False
    if method_needs_rw(method):
        return have == "rw"
    return have in ("ro", "rw")


def assert_scope_allows(
    scopes: frozenset[str] | None,
    *,
    method: str,
    path: str,
) -> None:
    """Raise HTTP 403 if the API key scopes forbid this request."""
    if scopes_allow(scopes, method=method, path=path):
        return
    area = path_to_area(path) or "resource"
    need = "rw" if method_needs_rw(method) else "ro"
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"API key lacks {area}:{need} scope",
    )


def mcp_scopes_allow_connect(scopes: frozenset[str] | None) -> bool:
    """MCP session open: needs mcp:ro, mcp:rw, or legacy mcp/write/*."""
    expanded = expand_scopes(scopes)
    if expanded is None:
        return True
    return expanded.get("mcp") in ("ro", "rw")


def mcp_scopes_allow_write(scopes: frozenset[str] | None) -> bool:
    """Mutating MCP tools: need mcp:rw (or unrestricted / legacy write)."""
    expanded = expand_scopes(scopes)
    if expanded is None:
        return True
    return expanded.get("mcp") == "rw"


__all__ = [
    "API_KEY_AREAS",
    "API_KEY_AREAS_SET",
    "InvalidApiKeyScopeError",
    "ScopeLevel",
    "all_areas_read_only_scopes",
    "assert_scope_allows",
    "expand_scopes",
    "mcp_scopes_allow_connect",
    "mcp_scopes_allow_write",
    "method_needs_rw",
    "normalize_scopes",
    "parse_api_key_scopes",
    "path_to_area",
    "scopes_allow",
    "validate_scope_tokens",
]
