"""Signed visitor identities for the web chat (pure token codec, C4).

A customer website proves who a visitor is with a compact HS256 token signed with the chat's
identity secret; the customer portal vouches for its logged-in customers with a short-lived
"portal assertion" signed with a key derived from the application secret. Only HS256 is accepted
and every check failure raises :class:`IdentityError` with a coarse code. Token contents are never
logged here.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Final, Literal

from tiqora.webchat.text import CONTROL_CHARS, normalize_email

PORTAL_KID: Final = "tiqora-portal"
MAX_TOKEN_LENGTH: Final = 4096
MAX_FUTURE_SECONDS: Final = 24 * 3600
LEEWAY_SECONDS: Final = 60
PORTAL_ASSERTION_SECONDS: Final = 600
MAX_CLAIM_LENGTH: Final = 200

IdentityErrorCode = Literal[
    "malformed",
    "algorithm",
    "signature",
    "expired",
    "not_yet_valid",
    "lifetime",
    "audience",
    "claims",
    "visitor",
]

_B64URL_RE: Final = re.compile(r"[A-Za-z0-9_-]*")
_FORBIDDEN_HEADER: Final = ("crit", "jku", "jwk", "x5u")


class IdentityError(ValueError):
    def __init__(self, code: IdentityErrorCode) -> None:
        super().__init__(code)
        self.code: IdentityErrorCode = code


@dataclass(frozen=True, slots=True)
class IdentityClaims:
    sub: str | None
    email: str | None
    name: str | None
    source: Literal["portal", "website"]
    visitor_id: str | None


def new_identity_secret() -> str:
    return secrets.token_urlsafe(32)


def portal_identity_key(secret_key: str) -> bytes:
    """Portal-assertion key, derived so the raw application secret never signs anything."""
    return hmac.new(
        secret_key.encode("utf-8"), b"webchat-portal-identity-v1", hashlib.sha256
    ).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_identity_token(claims: dict[str, Any], key: bytes, *, kid: str | None = None) -> str:
    header: dict[str, Any] = {"alg": "HS256", "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    head = _b64(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body = _b64(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{head}.{body}".encode("ascii")
    return f"{head}.{body}.{_b64(hmac.new(key, signing_input, hashlib.sha256).digest())}"


def _b64_decode(segment: str) -> bytes:
    if _B64URL_RE.fullmatch(segment) is None or len(segment) % 4 == 1:
        raise IdentityError("malformed")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError) as exc:
        raise IdentityError("malformed") from exc


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError) as exc:
        raise IdentityError("malformed") from exc
    if not isinstance(value, dict):
        raise IdentityError("malformed")
    return value


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _clean_text(value: object, *, truncate: bool) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IdentityError("claims")
    text = CONTROL_CHARS.sub("", value).strip()
    if len(text) > MAX_CLAIM_LENGTH:
        if not truncate:
            raise IdentityError("claims")
        text = text[:MAX_CLAIM_LENGTH].strip()
    return text or None


def verify_identity_token(
    token: str,
    *,
    website_secret: str | None,
    portal_key: bytes,
    audience: str,
    visitor_id: str | None,
    now: int,
) -> IdentityClaims:
    if not token or len(token) > MAX_TOKEN_LENGTH or not token.isascii():
        raise IdentityError("malformed")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts[:2]):
        raise IdentityError("malformed")
    head_b64, body_b64, sig_b64 = parts
    header = _json_object(_b64_decode(head_b64))
    sig = _b64_decode(sig_b64)

    if header.get("alg") != "HS256" or any(k in header for k in _FORBIDDEN_HEADER):
        raise IdentityError("algorithm")
    if "typ" in header and header["typ"] != "JWT":
        raise IdentityError("malformed")
    kid = header.get("kid")
    portal = kid == PORTAL_KID
    if portal:
        key: bytes | None = portal_key
    else:
        key = website_secret.encode("utf-8") if website_secret else None
    if key is None:
        raise IdentityError("signature")
    expected = hmac.new(key, f"{head_b64}.{body_b64}".encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise IdentityError("signature")

    payload = _json_object(_b64_decode(body_b64))

    exp = payload.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool):
        raise IdentityError("claims")
    if any(name in payload and not _is_int(payload[name]) for name in ("nbf", "iat")):
        raise IdentityError("claims")
    if exp + LEEWAY_SECONDS < now:
        raise IdentityError("expired")
    if exp > now + MAX_FUTURE_SECONDS:
        raise IdentityError("lifetime")
    nbf: int | None = payload.get("nbf")
    if nbf is not None and nbf > now + LEEWAY_SECONDS:
        raise IdentityError("not_yet_valid")

    aud = payload.get("aud")
    if aud is None:
        if portal:
            raise IdentityError("audience")
    elif not isinstance(aud, str) or aud != audience:
        raise IdentityError("audience")

    src = payload.get("src")
    if portal:
        if src != "portal":
            raise IdentityError("claims")
    elif src is not None:
        # a website token must not pose as a portal assertion
        raise IdentityError("claims")

    sub = _clean_text(payload.get("sub"), truncate=False)
    name = _clean_text(payload.get("name"), truncate=True)
    raw_email = payload.get("email")
    email: str | None = None
    if raw_email is not None:
        if not isinstance(raw_email, str):
            raise IdentityError("claims")
        email = normalize_email(raw_email)
        if email is None:
            raise IdentityError("claims")
    if sub is None and email is None:
        raise IdentityError("claims")

    vid: str | None = None
    if portal:
        vid = payload.get("vid")
        if (
            not isinstance(vid, str)
            or visitor_id is None
            or not hmac.compare_digest(vid.encode("utf-8"), visitor_id.encode("utf-8"))
        ):
            raise IdentityError("visitor")

    return IdentityClaims(
        sub=sub, email=email, name=name, source="portal" if portal else "website", visitor_id=vid
    )
