"""Visitor session ids and tokens, keyed IP hashing (pure)."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import uuid
from typing import Final

CHAT_TOKEN_HEADER: Final = "X-Chat-Token"
VISITOR_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_TOKEN_RE: Final = re.compile(r"([0-9a-f]{32})\.([A-Za-z0-9_-]{20,100})")
_MAX_TOKEN_LEN: Final = 140


def new_session_id() -> str:
    return uuid.uuid4().hex


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_visitor_token(session_id: str) -> tuple[str, str]:
    """``("<sid>.<secret>", sha256(secret) hex)`` — only the digest is stored."""
    secret = secrets.token_urlsafe(32)
    return f"{session_id}.{secret}", _digest(secret)


def parse_visitor_token(token: str | None) -> tuple[str, str] | None:
    if not token or len(token) > _MAX_TOKEN_LEN:
        return None
    m = _TOKEN_RE.fullmatch(token)
    if m is None:
        return None
    return m.group(1), m.group(2)


def token_matches(secret: str, token_hash: str) -> bool:
    """Constant-time comparison of ``sha256(secret)`` with the stored digest."""
    if not secret or not token_hash:
        return False
    try:
        return hmac.compare_digest(_digest(secret).encode("ascii"), token_hash.encode("utf-8"))
    except UnicodeEncodeError:  # lone surrogate: never a match
        return False


def _normalise_ip(ip: str) -> str:
    raw = (ip or "").strip()
    try:
        addr = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return "invalid:" + raw[:64]
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def hash_ip(ip: str, secret_key: str) -> str:
    """Keyed, truncated hash of the client address (R-IPV6).

    IPv4-mapped IPv6 → IPv4; other IPv6 → its /64 (one subscriber controls 2^64 addresses).
    The HMAC key is derived from ``secret_key`` so it is not used directly.
    """
    key = hmac.new(secret_key.encode("utf-8"), b"webchat-ip-v1", hashlib.sha256).digest()
    msg = ("webchat-ip:" + _normalise_ip(ip)).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]
