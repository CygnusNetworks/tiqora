"""Throwaway S/MIME CA + leaf certificates for crypto tests (built with ``cryptography``).

Not a test module (leading underscore): imported by the S/MIME store, index
and admin API tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

CA_NAME = "example root ca"


@dataclass(frozen=True)
class Issued:
    cert_pem: bytes
    key_pem: bytes  # unencrypted PKCS#8
    key: rsa.RSAPrivateKey
    cert: x509.Certificate

    def encrypted_key(self, secret: str) -> bytes:
        return self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(secret.encode()),
        )


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_ca(common_name: str = CA_NAME) -> Issued:
    key = _key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return _issued(cert, key)


def make_leaf(
    email: str,
    *,
    ca: Issued | None = None,
    common_name: str | None = None,
    not_before: datetime | None = None,
    not_after: datetime | None = None,
    key: rsa.RSAPrivateKey | None = None,
) -> Issued:
    key = key or _key()
    now = datetime.now(UTC)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, common_name or email),
            x509.NameAttribute(NameOID.EMAIL_ADDRESS, email),
        ]
    )
    issuer_name = ca.cert.subject if ca else subject
    signer = ca.key if ca else key
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or now - timedelta(days=1))
        .not_valid_after(not_after or now + timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.RFC822Name(email)]), critical=False)
        .sign(signer, hashes.SHA256())
    )
    return _issued(cert, key)


def _issued(cert: x509.Certificate, key: rsa.RSAPrivateKey) -> Issued:
    return Issued(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM),
        key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key=key,
        cert=cert,
    )
