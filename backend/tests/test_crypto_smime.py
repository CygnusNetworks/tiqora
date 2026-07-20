"""S/MIME roundtrip tests (tiqora/crypto/smime.py, keystore.py) against a
self-signed cert generated with ``openssl req``.

No Docker/DB needed. Skipped automatically if the ``openssl`` binary is not
on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tiqora.crypto import CryptoError
from tiqora.crypto.keystore import SmimeKeyStore
from tiqora.crypto.smime import SmimeEngine

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl binary not on PATH"
)


@pytest.fixture
def selfsigned_cert(tmp_path: Path) -> tuple[Path, Path]:
    """(cert_path, key_path) for a fresh self-signed 2048-bit RSA cert."""
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    proc = subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            "2",
            "-nodes",
            "-subj",
            "/CN=alice@example.com",
        ],
        capture_output=True,
        check=True,
    )
    assert proc.returncode == 0
    return cert_path, key_path


@pytest.fixture
def engine() -> SmimeEngine:
    return SmimeEngine()


def test_sign_produces_smime_signed_mime(
    engine: SmimeEngine, selfsigned_cert: tuple[Path, Path]
) -> None:
    cert_path, key_path = selfsigned_cert
    signed = engine.sign(b"hello smime", str(cert_path), str(key_path))
    assert b"MIME-Version" in signed
    assert b"hello smime" in signed


def test_sign_and_verify_roundtrip(engine: SmimeEngine, selfsigned_cert: tuple[Path, Path]) -> None:
    cert_path, key_path = selfsigned_cert
    signed = engine.sign(b"a signed message", str(cert_path), str(key_path))

    result = engine.verify(signed)
    assert result.valid is True


def test_verify_tampered_signature_fails(
    engine: SmimeEngine, selfsigned_cert: tuple[Path, Path]
) -> None:
    cert_path, key_path = selfsigned_cert
    signed = engine.sign(b"original content", str(cert_path), str(key_path))
    tampered = signed.replace(b"original content", b"tampered content!")

    result = engine.verify(tampered)
    assert result.valid is False


def test_encrypt_and_decrypt_roundtrip(
    engine: SmimeEngine, selfsigned_cert: tuple[Path, Path]
) -> None:
    cert_path, key_path = selfsigned_cert
    plaintext = b"a confidential smime message"

    ciphertext = engine.encrypt(plaintext, [str(cert_path)])
    assert plaintext not in ciphertext

    result = engine.decrypt(ciphertext, str(cert_path), str(key_path))
    assert result.ok is True
    assert plaintext in result.plaintext


def test_decrypt_wrong_key_fails(engine: SmimeEngine, tmp_path: Path) -> None:
    def _gen_cert(cn: str, subdir: str) -> tuple[Path, Path]:
        d = tmp_path / subdir
        d.mkdir()
        cert_path = d / "cert.pem"
        key_path = d / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key_path),
                "-out",
                str(cert_path),
                "-days",
                "2",
                "-nodes",
                "-subj",
                f"/CN={cn}",
            ],
            capture_output=True,
            check=True,
        )
        return cert_path, key_path

    cert_a, _key_a = _gen_cert("a@example.com", "a")
    _cert_b, key_b = _gen_cert("b@example.com", "b")

    ciphertext = engine.encrypt(b"only for A", [str(cert_a)])
    result = engine.decrypt(ciphertext, str(cert_a), str(key_b))
    assert result.ok is False


def test_encrypt_requires_recipients(engine: SmimeEngine) -> None:
    with pytest.raises(CryptoError):
        engine.encrypt(b"data", [])


def test_openssl_binary_not_found_raises(selfsigned_cert: tuple[Path, Path]) -> None:
    from tiqora.crypto import CryptoUnavailableError

    cert_path, key_path = selfsigned_cert
    bad_engine = SmimeEngine(openssl_bin="/nonexistent/openssl-binary")
    with pytest.raises(CryptoUnavailableError):
        bad_engine.sign(b"data", str(cert_path), str(key_path))


# ---------------------------------------------------------------------------
# keystore.SmimeKeyStore
# ---------------------------------------------------------------------------


@pytest.fixture
def key_dirs() -> Iterator[tuple[str, str]]:
    with (
        tempfile.TemporaryDirectory() as cert_dir,
        tempfile.TemporaryDirectory() as private_dir,
    ):
        yield cert_dir, private_dir


def test_smime_keystore_register_and_lookup(key_dirs: tuple[str, str]) -> None:
    cert_dir, private_dir = key_dirs
    store = SmimeKeyStore(cert_dir, private_dir)

    paths = store.register("Alice@Example.com", cert_pem=b"CERT-DATA", key_pem=b"KEY-DATA")
    assert paths.cert_path is not None
    assert paths.key_path is not None

    looked_up = store.lookup("alice@example.com")
    assert looked_up.cert_path == paths.cert_path
    assert looked_up.key_path == paths.key_path
    assert looked_up.cert_path.read_bytes() == b"CERT-DATA"


def test_smime_keystore_lookup_missing_returns_none(key_dirs: tuple[str, str]) -> None:
    cert_dir, private_dir = key_dirs
    store = SmimeKeyStore(cert_dir, private_dir)
    result = store.lookup("nobody@example.com")
    assert result.cert_path is None
    assert result.key_path is None


def test_smime_keystore_email_is_path_sanitized(key_dirs: tuple[str, str]) -> None:
    cert_dir, private_dir = key_dirs
    store = SmimeKeyStore(cert_dir, private_dir)
    store.register("../../etc/passwd@evil.com", cert_pem=b"X")
    # Must not have written outside cert_dir.
    written = list(Path(cert_dir).iterdir())
    assert len(written) == 1
    assert written[0].parent == Path(cert_dir)
