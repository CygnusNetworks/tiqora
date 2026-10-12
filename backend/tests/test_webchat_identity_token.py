"""Identity token codec (pure): every refusal path is pinned with hand-built tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

import pytest

from tiqora.webchat.identity import (
    PORTAL_KID,
    IdentityError,
    new_identity_secret,
    portal_identity_key,
    sign_identity_token,
    verify_identity_token,
)

SECRET = "s3cr3t-" + "x" * 40
KEY = "wc_" + "a" * 32
NOW = 1_800_000_000
PK = portal_identity_key("application-secret-" + "p" * 20)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _tok_raw(header: bytes, payload: bytes, key: bytes) -> str:
    h, p = _b64(header), _b64(payload)
    sig = hmac.new(key, f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{_b64(sig)}"


def _tok(header: dict[str, Any], payload: dict[str, Any], key: bytes) -> str:
    return _tok_raw(json.dumps(header).encode(), json.dumps(payload).encode(), key)


def _web(payload: dict[str, Any]) -> str:
    return _tok({"alg": "HS256", "typ": "JWT"}, payload, SECRET.encode())


def _verify(token: str, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "website_secret": SECRET,
        "portal_key": PK,
        "audience": KEY,
        "visitor_id": None,
        "now": NOW,
    }
    args.update(kw)
    return verify_identity_token(token, **args)


def _code(token: str, **kw: Any) -> str:
    with pytest.raises(IdentityError) as exc:
        _verify(token, **kw)
    return exc.value.code


def test_verify_accepts_minimal_website_token() -> None:
    c = _verify(_web({"sub": "erika", "exp": NOW + 600}))
    assert (c.sub, c.email, c.source) == ("erika", None, "website")


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "none"},
        {"alg": "HS512"},
        {"alg": "RS256"},
        {"alg": "hs256"},
        {"alg": "HS256", "crit": ["exp"]},
        {"alg": "HS256", "jwk": {}},
    ],
)
def test_verify_rejects_algorithms(header: dict[str, Any]) -> None:
    assert _code(_tok(header, {"sub": "e", "exp": NOW + 60}, SECRET.encode())) == "algorithm"


def test_verify_rejects_wrong_secret() -> None:
    t = _tok({"alg": "HS256"}, {"sub": "e", "exp": NOW + 60}, b"other-secret")
    assert _code(t) == "signature"
    assert _code(_web({"sub": "e", "exp": NOW + 60}), website_secret=None) == "signature"


def test_verify_rejects_website_token_with_portal_kid() -> None:
    t = _tok(
        {"alg": "HS256", "kid": PORTAL_KID},
        {"src": "portal", "sub": "e", "aud": KEY, "vid": "v", "exp": NOW + 60},
        SECRET.encode(),
    )
    assert _code(t, visitor_id="v") == "signature"


def test_verify_rejects_missing_or_expired_exp() -> None:
    assert _code(_web({"sub": "e"})) == "claims"
    assert _code(_web({"sub": "e", "exp": True})) == "claims"
    assert _code(_web({"sub": "e", "exp": "1800000100"})) == "claims"
    assert _code(_web({"sub": "e", "exp": NOW - 61})) == "expired"
    assert _verify(_web({"sub": "e", "exp": NOW - 30})).sub == "e"


def test_verify_rejects_lifetime_over_24h() -> None:
    assert _code(_web({"sub": "e", "exp": NOW + 86401 + 60})) == "lifetime"
    assert _verify(_web({"sub": "e", "exp": NOW + 86400})).sub == "e"


def test_verify_rejects_nbf_in_future() -> None:
    assert _code(_web({"sub": "e", "exp": NOW + 600, "nbf": NOW + 61})) == "not_yet_valid"
    assert _verify(_web({"sub": "e", "exp": NOW + 600, "nbf": NOW + 30})).sub == "e"


def test_verify_audience() -> None:
    base = {"sub": "e", "exp": NOW + 600}
    assert _code(_web({**base, "aud": "wc_" + "b" * 32})) == "audience"
    assert _code(_web({**base, "aud": [KEY]})) == "audience"
    assert _verify(_web({**base, "aud": KEY})).sub == "e"


def test_verify_requires_sub_or_email() -> None:
    assert _code(_web({"name": "x", "exp": NOW + 600})) == "claims"
    assert _verify(_web({"email": "a@Example.ORG", "exp": NOW + 600})).email == "a@example.org"


@pytest.mark.parametrize("email", ["a@b", 'x"<y>@example.org', 5, ""])
def test_verify_rejects_bad_email(email: Any) -> None:
    assert _code(_web({"sub": "e", "email": email, "exp": NOW + 600})) == "claims"


def test_verify_rejects_malformed() -> None:
    good = _web({"sub": "e", "exp": NOW + 600})
    h, p, s = good.split(".")
    cases = [
        "",
        "a.b",
        "a.b.c.d",
        "é.b.c",
        "a" * 4097,
        "!!.!!.!!",
        "a=.b.c",
        f"{h}.{p}.{s}=",
        f"{h}..{s}",
        _tok_raw(b'{"alg":"HS256"}', b"[]", SECRET.encode()),
        _tok_raw(b'{"alg":"HS256"}', b'{"sub":"a","sub":"b","exp":1}', SECRET.encode()),
        _tok_raw(b"[]", b"{}", SECRET.encode()),
    ]
    for t in cases:
        assert _code(t) == "malformed", t[:30]


def test_name_and_sub_are_cleaned() -> None:
    c = _verify(_web({"sub": " er\x00ika ", "name": "‮Erika\x00", "exp": NOW + 600}))
    assert (c.sub, c.name) == ("erika", "Erika")
    assert _code(_web({"sub": "s" * 201, "exp": NOW + 600})) == "claims"
    long_name = _verify(_web({"sub": "e", "name": "n" * 500, "exp": NOW + 600})).name
    assert long_name == "n" * 200


def test_website_token_cannot_claim_portal_source() -> None:
    assert _code(_web({"src": "portal", "sub": "e", "exp": NOW + 600})) == "claims"


def test_portal_assertion_roundtrip_and_binding() -> None:
    claims = {
        "src": "portal",
        "sub": "erika",
        "email": "erika@example.org",
        "name": "Erika Muster",
        "aud": KEY,
        "vid": "v" * 22,
        "iat": NOW,
        "exp": NOW + 600,
    }
    t = sign_identity_token(claims, PK, kid=PORTAL_KID)
    c = _verify(t, website_secret=None, visitor_id="v" * 22)
    assert (c.source, c.sub, c.email, c.name, c.visitor_id) == (
        "portal",
        "erika",
        "erika@example.org",
        "Erika Muster",
        "v" * 22,
    )
    assert _code(t, website_secret=None, visitor_id="w" * 22) == "visitor"
    assert _code(t, website_secret=None, visitor_id=None) == "visitor"
    assert _code(t, website_secret=None, visitor_id="v" * 22, audience="wc_" + "b" * 32) == (
        "audience"
    )
    no_aud = {k: v for k, v in claims.items() if k != "aud"}
    t2 = sign_identity_token(no_aud, PK, kid=PORTAL_KID)
    assert _code(t2, website_secret=None, visitor_id="v" * 22) == "audience"
    no_src = {k: v for k, v in claims.items() if k != "src"}
    t3 = sign_identity_token(no_src, PK, kid=PORTAL_KID)
    assert _code(t3, website_secret=None, visitor_id="v" * 22) == "claims"


def test_sign_roundtrip_website() -> None:
    t = sign_identity_token({"sub": "e", "exp": NOW + 60}, SECRET.encode())
    assert _verify(t).source == "website"


def test_portal_key_is_derived_not_raw() -> None:
    assert portal_identity_key("k" * 40) != b"k" * 40
    assert len(portal_identity_key("k" * 40)) == 32
    assert portal_identity_key("a" * 40) != portal_identity_key("b" * 40)


def test_new_identity_secret_entropy() -> None:
    a, b = new_identity_secret(), new_identity_secret()
    assert a != b and len(a) >= 43
