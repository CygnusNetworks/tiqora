"""Znuny ``ConvertCertFormat`` port: every container a customer backend may hold → PEM."""

from __future__ import annotations

import base64

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7, pkcs12

from tests._smime_fixtures import make_ca, make_leaf
from tiqora.crypto.customer_fetch import _as_bytes
from tiqora.crypto.customer_keys import convert_cert_format
from tiqora.crypto.smime_store import SmimeStoreError

CA = make_ca()
LEAF = make_leaf("carla@example.com", ca=CA)


def _fp(pem: bytes) -> bytes:
    return x509.load_pem_x509_certificate(pem).fingerprint(hashes.SHA256())


def test_pem_passes_through() -> None:
    assert _fp(convert_cert_format(LEAF.cert_pem)) == _fp(LEAF.cert_pem)


def test_der() -> None:
    der = LEAF.cert.public_bytes(serialization.Encoding.DER)
    assert _fp(convert_cert_format(der)) == _fp(LEAF.cert_pem)


@pytest.mark.parametrize("encoding", [serialization.Encoding.DER, serialization.Encoding.PEM])
def test_pkcs7_bundle_picks_end_entity(encoding: serialization.Encoding) -> None:
    # CA first: the leaf must still win (LDAP userSMIMECertificate often carries the chain).
    p7 = pkcs7.serialize_certificates([CA.cert, LEAF.cert], encoding)
    assert _fp(convert_cert_format(p7)) == _fp(LEAF.cert_pem)


def test_pkcs12_without_passphrase_ignores_the_key() -> None:
    pfx = pkcs12.serialize_key_and_certificates(
        b"carla", LEAF.key, LEAF.cert, [CA.cert], serialization.NoEncryption()
    )
    out = convert_cert_format(pfx)
    assert _fp(out) == _fp(LEAF.cert_pem)
    assert b"PRIVATE KEY" not in out


def test_pkcs12_with_passphrase() -> None:
    pfx = pkcs12.serialize_key_and_certificates(
        b"carla", LEAF.key, LEAF.cert, None, serialization.BestAvailableEncryption(b"pw")
    )
    with pytest.raises(SmimeStoreError):
        convert_cert_format(pfx)
    assert _fp(convert_cert_format(pfx, "pw")) == _fp(LEAF.cert_pem)


def test_garbage_is_refused() -> None:
    with pytest.raises(SmimeStoreError):
        convert_cert_format(b"definitely not a certificate")


def test_backend_values_are_normalised() -> None:
    der = LEAF.cert.public_bytes(serialization.Encoding.DER)
    assert _as_bytes(der) == der
    assert _as_bytes(base64.b64encode(der).decode()) == der
    assert _as_bytes(LEAF.cert_pem.decode()) == LEAF.cert_pem.strip()
    assert _as_bytes("") is None
    assert _as_bytes(None) is None
