"""Outbound crypto: EmailSecurity (PGP/S/MIME) applied to ArticleIn.body
(tiqora/crypto/outbound.py) — mirrors Znuny GenericInterface TicketCreate's
``EmailSecurity: {Backend, SignKey, EncryptKeys}``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tiqora.config import Settings
from tiqora.crypto.outbound import apply_email_security_sync
from tiqora.domain.ticket_write_service import ArticleIn

pytestmark = pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH")


def _article(body: str = "hello world") -> ArticleIn:
    return ArticleIn(
        sender_type="agent",
        is_visible_for_customer=True,
        subject="Test",
        body=body,
    )


def _gen_pgp_key(gnupghome: str) -> str:
    import gnupg

    gpg = gnupg.GPG(gnupghome=gnupghome)
    gpg.encoding = "utf-8"
    input_data = gpg.gen_key_input(
        name_email="agent@example.com",
        passphrase="",
        key_type="RSA",
        key_length=2048,
        no_protection=True,
    )
    key = gpg.gen_key(input_data)
    assert key.fingerprint
    return str(key.fingerprint)


def test_pgp_backend_disabled_leaves_body_unchanged() -> None:
    article = _article()
    settings = Settings(TIQORA_CRYPTO_PGP_ENABLED="0")
    result = apply_email_security_sync(article, {"Backend": "PGP", "SignKey": "DEADBEEF"}, settings)
    assert result.body == "hello world"


def test_pgp_sign_applies_detached_signature_to_body() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as gnupghome:  # noqa: S108
        fp = _gen_pgp_key(gnupghome)
        article = _article()
        settings = Settings(TIQORA_CRYPTO_PGP_ENABLED="1", TIQORA_CRYPTO_PGP_GNUPGHOME=gnupghome)
        result = apply_email_security_sync(article, {"Backend": "PGP", "SignKey": fp}, settings)
        assert "hello world" in result.body
        assert "BEGIN PGP SIGNATURE" in result.body


def test_pgp_encrypt_replaces_body_with_ciphertext() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as gnupghome:  # noqa: S108
        fp = _gen_pgp_key(gnupghome)
        article = _article("a secret reply")
        settings = Settings(TIQORA_CRYPTO_PGP_ENABLED="1", TIQORA_CRYPTO_PGP_GNUPGHOME=gnupghome)
        result = apply_email_security_sync(
            article, {"Backend": "PGP", "EncryptKeys": [fp]}, settings
        )
        assert "a secret reply" not in result.body
        assert "BEGIN PGP MESSAGE" in result.body


def test_unknown_backend_leaves_body_unchanged() -> None:
    article = _article()
    settings = Settings()
    result = apply_email_security_sync(article, {"Backend": "ROT13"}, settings)
    assert result.body == "hello world"


def test_pgp_sign_with_unknown_key_leaves_body_unchanged() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as gnupghome:  # noqa: S108
        article = _article()
        settings = Settings(TIQORA_CRYPTO_PGP_ENABLED="1", TIQORA_CRYPTO_PGP_GNUPGHOME=gnupghome)
        result = apply_email_security_sync(
            article, {"Backend": "PGP", "SignKey": "0000000000000000"}, settings
        )
        # Sign failed (unknown key) -> best-effort no-op, body untouched.
        assert result.body == "hello world"


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH")
def test_smime_sign_applies_signature_via_keystore() -> None:
    with (
        tempfile.TemporaryDirectory() as cert_dir,
        tempfile.TemporaryDirectory() as private_dir,
    ):
        cert_path = Path(cert_dir) / "agent@example.com.crt"
        key_path = Path(private_dir) / "agent@example.com.key"
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
                "/CN=agent@example.com",
            ],
            capture_output=True,
            check=True,
        )
        article = _article()
        settings = Settings(
            TIQORA_CRYPTO_SMIME_ENABLED="1",
            TIQORA_CRYPTO_SMIME_CERT_DIR=cert_dir,
            TIQORA_CRYPTO_SMIME_PRIVATE_DIR=private_dir,
        )
        result = apply_email_security_sync(
            article, {"Backend": "SMIME", "SignKey": "agent@example.com"}, settings
        )
        assert "MIME-Version" in result.body
        assert result.content_type == "application/pkcs7-mime"


def test_smime_sign_missing_cert_leaves_body_unchanged() -> None:
    with (
        tempfile.TemporaryDirectory() as cert_dir,
        tempfile.TemporaryDirectory() as private_dir,
    ):
        article = _article()
        settings = Settings(
            TIQORA_CRYPTO_SMIME_ENABLED="1",
            TIQORA_CRYPTO_SMIME_CERT_DIR=cert_dir,
            TIQORA_CRYPTO_SMIME_PRIVATE_DIR=private_dir,
        )
        result = apply_email_security_sync(
            article, {"Backend": "SMIME", "SignKey": "nobody@example.com"}, settings
        )
        assert result.body == "hello world"
