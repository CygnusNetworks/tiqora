"""SP key/certificate generation and temp key files for pysaml2 (xmlsec1 needs file paths)."""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_RSA_BITS = 3072

_lock = threading.Lock()
_tmp_dir: str | None = None


@dataclass(frozen=True, slots=True)
class KeyFiles:
    key_path: str
    cert_path: str


def generate_sp_keypair(common_name: str, *, days: int = 3650) -> tuple[str, str]:
    """Return ``(private_key_pem, cert_pem)``: RSA 3072, self-signed, SHA-256."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=_RSA_BITS)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _cleanup() -> None:
    global _tmp_dir  # noqa: PLW0603
    if _tmp_dir is not None:
        shutil.rmtree(_tmp_dir, ignore_errors=True)
        _tmp_dir = None


def _write_private(path: str, content: str) -> None:
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(content)
    os.replace(tmp, path)


def materialize_keypair(private_key_pem: str, cert_pem: str) -> KeyFiles:
    """Write the pair into the process-wide 0700 temp dir (files 0600); reused per cert."""
    global _tmp_dir  # noqa: PLW0603
    with _lock:
        if _tmp_dir is None or not os.path.isdir(_tmp_dir):
            _tmp_dir = tempfile.mkdtemp(prefix="tiqora-saml-")
            os.chmod(_tmp_dir, 0o700)
            atexit.register(_cleanup)
        digest = hashlib.sha256(cert_pem.encode()).hexdigest()[:16]
        key_path = os.path.join(_tmp_dir, f"{digest}.key")
        cert_path = os.path.join(_tmp_dir, f"{digest}.crt")
        if not os.path.exists(key_path):
            _write_private(key_path, private_key_pem)
        if not os.path.exists(cert_path):
            _write_private(cert_path, cert_pem)
        return KeyFiles(key_path=key_path, cert_path=cert_path)
