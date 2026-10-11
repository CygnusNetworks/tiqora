"""Secret masking for GenericInterface requester traffic (pure, no I/O).

Everything that may reach a log line, the debugger, a job result or an API response passes
through here first. Three kinds of input are masked: header values (by name and by known secret
value), configuration dumps (by key name) and URLs (user info and secret query values).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Final
from urllib.parse import unquote, urlsplit, urlunsplit

MASK: Final = "***"

# Key names (config keys, header names, URL query keys) whose non-empty value is a secret.
# ``bearer`` only matters for header names and query keys.
SECRET_KEY_RE: Final = re.compile(
    r"passw|secret|token|auth|userpw|apikey|api[-_]?key|x509keyfile|sslkey|x-api|signature|bearer",
    re.IGNORECASE,
)

#: Values shorter than this are never masked as "known secret values" (too many false hits).
MIN_SECRET_LENGTH: Final = 4

_ALWAYS_SECRET_HEADERS: Final = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie"}
)
_AUTH_SCHEME_RE: Final = re.compile(
    r"^(basic|bearer|digest|negotiate|ntlm|token|apikey|hoba|mutual|aws4-hmac-sha256)\s+\S",
    re.IGNORECASE,
)


def _usable(secret_values: Iterable[str]) -> list[str]:
    """Distinct secrets of at least ``MIN_SECRET_LENGTH`` characters, longest first."""
    found = {s for s in secret_values if isinstance(s, str) and len(s) >= MIN_SECRET_LENGTH}
    return sorted(found, key=len, reverse=True)


def redact_text(text: str, secret_values: Iterable[str]) -> str:
    """Replace every occurrence of every secret value (4+ characters) with ``***``."""
    for secret in _usable(secret_values):
        text = text.replace(secret, MASK)
    return text


def _mask_header_value(value: str) -> str:
    """``Basic abc`` -> ``Basic ***`` (scheme kept); anything else -> ``***``."""
    if not value:
        return value
    match = _AUTH_SCHEME_RE.match(value)
    if match:
        return f"{value[: match.end() - 1].split()[0]} {MASK}"
    return MASK


def redact_headers(
    headers: Mapping[str, str], *, secret_values: Iterable[str] = ()
) -> dict[str, str]:
    """Copy of *headers* that is safe to store or show.

    Credential headers and any header whose name looks like a secret keep only their scheme
    (``Basic ***``); every other header has known secret values replaced inside its value.
    """
    secrets = _usable(secret_values)
    out: dict[str, str] = {}
    for name, value in headers.items():
        text = value if isinstance(value, str) else str(value)
        if name.lower() in _ALWAYS_SECRET_HEADERS or SECRET_KEY_RE.search(name):
            masked = _mask_header_value(redact_text(text, secrets))
            # A value that is already fully masked by a known secret keeps its scheme.
            out[name] = masked
        else:
            out[name] = redact_text(text, secrets)
    return out


def redact_config(value: Any) -> Any:
    """Deep copy of *value* with the non-empty string behind every secret-looking key masked."""
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and SECRET_KEY_RE.search(key) and isinstance(item, str):
                result[key] = MASK if item else item
            else:
                result[key] = redact_config(item)
        return result
    if isinstance(value, list | tuple):
        return [redact_config(item) for item in value]
    return value


def redact_url(url: str) -> str:
    """Drop the user info and mask the values of secret-looking query keys."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return MASK
    netloc = parts.netloc.rsplit("@", 1)[-1]
    query = parts.query
    if query:
        pieces: list[str] = []
        for piece in query.split("&"):
            key, sep, _ = piece.partition("=")
            if sep and SECRET_KEY_RE.search(unquote(key)):
                pieces.append(f"{key}={MASK}")
            else:
                pieces.append(piece)
        query = "&".join(pieces)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
