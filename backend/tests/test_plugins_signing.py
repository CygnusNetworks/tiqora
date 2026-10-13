from __future__ import annotations

import base64
import json
import logging
import re

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tiqora.config import Settings
from tiqora.plugins.signing import (
    KeyRing,
    PublisherKey,
    SignatureError,
    TrustLevel,
    generate_keypair,
    key_id,
    load_private_key,
    local_keys,
    parse_public_key,
    parse_signature,
    public_key_text,
    resolve_trust,
    sign_bytes,
    verify_signature,
)

CHECKSUMS = b"tqplugin-checksums 1\n" + b"0" * 64 + b"  tiqora-plugin.toml\n"
PLUGIN = "org.example.hello"


def _ring(**kw) -> KeyRing:
    return KeyRing(
        official=kw.get("official", {}),
        local=kw.get("local", {}),
        publishers=kw.get("publishers", ()),
    )


def make_keypair() -> tuple[Ed25519PrivateKey, str, str]:
    pem, pub = generate_keypair()
    return load_private_key(pem), pub, key_id(parse_public_key(pub))


def _pub(pub: str) -> Ed25519PublicKey:
    return parse_public_key(pub)


def _publisher(pub: str, kid: str, **kw) -> PublisherKey:
    return PublisherKey(
        key_id=kid,
        public_key=_pub(pub),
        publisher_id=kw.get("publisher_id", "org.example"),
        publisher_name=kw.get("publisher_name", "Example GmbH"),
        repository_id=kw.get("repository_id", 1),
        revoked=kw.get("revoked", False),
    )


def _raw_sig(**over) -> bytes:
    d = {
        "format": 1,
        "alg": "ed25519",
        "key_id": "0" * 16,
        "signature": base64.b64encode(b"s" * 64).decode(),
    }
    d.update(over)
    return json.dumps(d).encode()


def test_key_text_roundtrip_and_key_id() -> None:
    pem, pub = generate_keypair()
    key = parse_public_key(pub)
    assert public_key_text(key) == pub and re.fullmatch(r"[0-9a-f]{16}", key_id(key))
    assert pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert key_id(load_private_key(pem).public_key()) == key_id(key)


@pytest.mark.parametrize(
    "bad",
    ["", "ed25519:", "rsa:AAAA", "ed25519:!!!", "ed25519:" + base64.b64encode(b"x" * 31).decode()],
)
def test_parse_public_key_rejects(bad) -> None:
    with pytest.raises(SignatureError):
        parse_public_key(bad)


def test_load_private_key_rejects_garbage() -> None:
    with pytest.raises(SignatureError):
        load_private_key(b"not a pem")


def test_sign_bytes_format_and_strict_parse() -> None:
    priv, _pub_text, kid = make_keypair()
    raw = sign_bytes(priv, CHECKSUMS)
    assert b" " not in raw
    parsed_kid, sig = parse_signature(raw)
    assert parsed_kid == kid and len(sig) == 64
    assert set(json.loads(raw)) == {"format", "alg", "key_id", "signature"}


def test_unsigned_without_signature() -> None:
    r = resolve_trust(None, CHECKSUMS, plugin_id=PLUGIN, manifest_publisher_id=None, ring=_ring())
    assert (r.level, r.problem) == (TrustLevel.UNSIGNED, None)


def test_verify_local_key() -> None:
    priv, pub, kid = make_keypair()
    sig = sign_bytes(priv, CHECKSUMS)
    r = resolve_trust(
        sig,
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id=None,
        ring=_ring(local={kid: _pub(pub)}),
    )
    assert r.level is TrustLevel.LOCAL and r.key_id == kid
    assert r.signer is not None and kid in r.signer and r.problem is None


def test_verify_official_key() -> None:
    priv, pub, kid = make_keypair()
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id=None,
        ring=_ring(official={kid: _pub(pub)}),
    )
    assert (r.level, r.signer, r.key_id) == (TrustLevel.OFFICIAL, "Tiqora", kid)


def test_verify_tampered_checksums_is_invalid() -> None:
    priv, pub, kid = make_keypair()
    sig = sign_bytes(priv, CHECKSUMS)
    r = resolve_trust(
        sig,
        CHECKSUMS + b"x",
        plugin_id=PLUGIN,
        manifest_publisher_id=None,
        ring=_ring(official={kid: _pub(pub)}),
    )
    assert r.level is TrustLevel.INVALID and r.problem == "Signatur ungültig"


def test_verify_signature_function_rejects_wrong_key() -> None:
    priv, _p, _k = make_keypair()
    _priv2, pub2, _k2 = make_keypair()
    with pytest.raises(SignatureError, match="Signatur ungültig"):
        verify_signature(sign_bytes(priv, CHECKSUMS), CHECKSUMS, _pub(pub2))


@pytest.mark.parametrize(
    "raw",
    [
        b"{not json",
        b"\xff\xfe",
        b"[]",
        _raw_sig(format=2),
        _raw_sig(alg="rsa"),
        _raw_sig(signature=base64.b64encode(b"s" * 63).decode()),
        _raw_sig(key_id="XYZ"),
        _raw_sig(signature="!!!"),
        json.dumps({"format": 1, "alg": "ed25519", "key_id": "0" * 16}).encode(),
        json.dumps(
            {
                "format": 1,
                "alg": "ed25519",
                "key_id": "0" * 16,
                "signature": base64.b64encode(b"s" * 64).decode(),
                "extra": 1,
            }
        ).encode(),
    ],
)
def test_verify_broken_signature_json_is_invalid(raw) -> None:
    r = resolve_trust(raw, CHECKSUMS, plugin_id=PLUGIN, manifest_publisher_id=None, ring=_ring())
    assert r.level is TrustLevel.INVALID and r.problem == "Signatur ist beschädigt"


def test_verify_unknown_key_is_unsigned_with_problem() -> None:
    priv, _pub_text, _kid = make_keypair()
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id=None,
        ring=_ring(),
    )
    assert r.level is TrustLevel.UNSIGNED
    assert r.problem == "Signiert mit einem unbekannten Schlüssel"


def test_verify_publisher_key_matching_id() -> None:
    priv, pub, kid = make_keypair()
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id="org.example",
        ring=_ring(publishers=[_publisher(pub, kid)]),
    )
    assert (
        r.level is TrustLevel.VERIFIED_PUBLISHER and r.signer == "Example GmbH" and r.key_id == kid
    )


@pytest.mark.parametrize(
    "plugin_id,publisher_id",
    [
        ("org.other.hello", "org.example"),
        ("org.examplex.hello", "org.example"),
        ("org.example.hello", None),
    ],
)
def test_verify_publisher_key_wrong_namespace_is_unsigned(plugin_id, publisher_id) -> None:
    priv, pub, kid = make_keypair()
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS,
        plugin_id=plugin_id,
        manifest_publisher_id=publisher_id,
        ring=_ring(publishers=[_publisher(pub, kid)]),
    )
    assert r.level is TrustLevel.UNSIGNED
    assert r.problem is not None and "nicht zu diesem Herausgeber gehört" in r.problem


def test_verify_revoked_publisher_key_is_invalid() -> None:
    priv, pub, kid = make_keypair()
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id="org.example",
        ring=_ring(publishers=[_publisher(pub, kid, revoked=True)]),
    )
    assert r.level is TrustLevel.INVALID and r.problem is not None and "zurückgezogen" in r.problem


def test_revoked_in_any_repository_wins_over_good_entry() -> None:
    priv, pub, kid = make_keypair()
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id="org.example",
        ring=_ring(
            publishers=[_publisher(pub, kid), _publisher(pub, kid, repository_id=2, revoked=True)]
        ),
    )
    assert r.level is TrustLevel.INVALID


def test_publisher_key_with_bad_signature_is_invalid_not_revoked_text() -> None:
    priv, pub, kid = make_keypair()
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS + b"x",
        plugin_id=PLUGIN,
        manifest_publisher_id="org.example",
        ring=_ring(publishers=[_publisher(pub, kid, revoked=True)]),
    )
    assert r.level is TrustLevel.INVALID and r.problem == "Signatur ungültig"


def test_official_wins_over_publisher_entry_with_same_key() -> None:
    priv, pub, kid = make_keypair()
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id="org.example",
        ring=_ring(official={kid: _pub(pub)}, publishers=[_publisher(pub, kid, revoked=True)]),
    )
    assert r.level is TrustLevel.OFFICIAL and r.signer == "Tiqora"


def test_two_repositories_claiming_one_publisher_id_are_both_unverified() -> None:
    priv1, pub1, kid1 = make_keypair()
    _priv2, pub2, kid2 = make_keypair()
    ring = _ring(
        publishers=[
            _publisher(pub1, kid1, repository_id=1),
            _publisher(pub2, kid2, repository_id=2),
        ]
    )
    r = resolve_trust(
        sign_bytes(priv1, CHECKSUMS),
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id="org.example",
        ring=ring,
    )
    assert r.level is TrustLevel.UNSIGNED
    assert r.problem is not None and "unterschiedlichen Schlüsseln" in r.problem


def test_same_key_in_two_repositories_stays_verified() -> None:
    priv, pub, kid = make_keypair()
    ring = _ring(
        publishers=[_publisher(pub, kid, repository_id=1), _publisher(pub, kid, repository_id=2)]
    )
    r = resolve_trust(
        sign_bytes(priv, CHECKSUMS),
        CHECKSUMS,
        plugin_id=PLUGIN,
        manifest_publisher_id="org.example",
        ring=ring,
    )
    assert r.level is TrustLevel.VERIFIED_PUBLISHER


def test_local_keys_from_settings_skip_bad_entries(caplog) -> None:
    _p1, good1, kid1 = make_keypair()
    _p2, good2, kid2 = make_keypair()
    settings = Settings(plugins_trusted_keys=f"{good1}, garbage , {good2}")
    with caplog.at_level(logging.WARNING, logger="tiqora.plugins.signing"):
        keys = local_keys(settings)
    assert set(keys) == {kid1, kid2}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "garbage" not in warnings[0].getMessage()
