"""PGP roundtrip tests (tiqora/crypto/pgp.py) against an ephemeral gpg keyring.

No Docker/DB needed — each test generates its own throwaway key in a fresh
``GNUPGHOME``, so these are plain unit tests (no ``@pytest.mark.db``).
Skipped automatically if the ``gpg`` binary is not on PATH.

``GNUPGHOME`` uses a short path directly under ``/tmp`` rather than pytest's
``tmp_path`` fixture: gpg-agent listens on a Unix domain socket under
``GNUPGHOME``, and macOS enforces a ~104-byte path limit on those — pytest's
nested ``tmp_path`` (``/private/var/.../pytest-of-user/pytest-N/test_name0``)
routinely exceeds it, making ``gpg gen-key`` fail with an opaque
"failed to start gpg-agent" error that has nothing to do with the code under
test.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator

import pytest

pytest.importorskip("gnupg")  # PGP tests need the optional crypto extra

from tiqora.crypto import CryptoError
from tiqora.crypto.pgp import PgpEngine

pytestmark = pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH")


@pytest.fixture
def gnupghome() -> Iterator[str]:
    with tempfile.TemporaryDirectory(dir="/tmp") as d:  # noqa: S108 — short path needed for gpg-agent socket
        yield d


def _gen_key(engine: PgpEngine, email: str) -> str:
    gpg = engine._gpg()  # noqa: SLF001 — test-only access to generate a throwaway key
    input_data = gpg.gen_key_input(
        name_email=email,
        passphrase="",
        key_type="RSA",
        key_length=2048,
        no_protection=True,
    )
    key = gpg.gen_key(input_data)
    assert key.fingerprint, f"key generation failed: {key.stderr}"
    return str(key.fingerprint)


def test_sign_produces_detached_signature(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen_key(engine, "alice@example.com")
    data = b"hello, this is a signed message"

    signature = engine.sign(data, fp)
    assert signature
    assert b"BEGIN PGP SIGNATURE" in signature


def test_verify_clear_signed_message(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen_key(engine, "bob@example.com")
    gpg = engine._gpg()  # noqa: SLF001
    clear_signed = gpg.sign("a clear-signed message", keyid=fp, clearsign=True)
    assert clear_signed

    verify = engine.verify(bytes(clear_signed.data))
    assert verify.valid is True
    assert verify.fingerprint == fp


def test_verify_tampered_clear_signed_message_fails(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen_key(engine, "carol@example.com")
    gpg = engine._gpg()  # noqa: SLF001
    clear_signed = gpg.sign("original content", keyid=fp, clearsign=True)
    tampered = bytes(clear_signed.data).replace(b"original", b"tampered!")

    verify = engine.verify(tampered)
    assert verify.valid is False


def test_encrypt_and_decrypt_roundtrip(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen_key(engine, "dave@example.com")
    plaintext = b"a confidential message"

    ciphertext = engine.encrypt(plaintext, [fp])
    assert b"BEGIN PGP MESSAGE" in ciphertext
    assert plaintext not in ciphertext

    result = engine.decrypt(ciphertext)
    assert result.ok is True
    assert result.plaintext == plaintext


def test_encrypt_and_sign_then_decrypt_verifies_signature(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen_key(engine, "erin@example.com")
    plaintext = b"signed and sealed"

    ciphertext = engine.encrypt(plaintext, [fp], sign_key_id=fp)
    result = engine.decrypt(ciphertext)
    assert result.ok is True
    assert result.plaintext == plaintext
    assert result.verify is not None
    assert result.verify.valid is True
    assert result.verify.fingerprint == fp


def test_decrypt_wrong_key_fails() -> None:
    with (
        tempfile.TemporaryDirectory(dir="/tmp") as home_a,  # noqa: S108
        tempfile.TemporaryDirectory(dir="/tmp") as home_b,  # noqa: S108
    ):
        engine_a = PgpEngine(home_a)
        fp_a = _gen_key(engine_a, "recipient@example.com")
        ciphertext = engine_a.encrypt(b"only for A", [fp_a])

        engine_b = PgpEngine(home_b)
        _gen_key(engine_b, "other@example.com")
        result = engine_b.decrypt(ciphertext)
        assert result.ok is False


def test_import_key_returns_fingerprint(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    fp = _gen_key(engine, "frank@example.com")
    gpg = engine._gpg()  # noqa: SLF001
    exported = gpg.export_keys(fp)

    with tempfile.TemporaryDirectory(dir="/tmp") as other_home:  # noqa: S108
        other_engine = PgpEngine(other_home)
        imported = other_engine.import_key(exported)
        assert fp in imported


def test_pgp_engine_requires_gnupghome() -> None:
    from tiqora.crypto import CryptoUnavailableError

    with pytest.raises(CryptoUnavailableError):
        PgpEngine("")


def test_sign_with_unknown_key_raises(gnupghome: str) -> None:
    engine = PgpEngine(gnupghome)
    with pytest.raises(CryptoError):
        engine.sign(b"data", "DEADBEEFDEADBEEF")
