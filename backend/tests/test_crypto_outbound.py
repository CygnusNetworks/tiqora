"""Outbound crypto: EmailSecurity (PGP/S/MIME) applied to ArticleIn.body
(tiqora/crypto/outbound.py) — mirrors Znuny GenericInterface TicketCreate's
``EmailSecurity: {Backend, SignKey, EncryptKeys}``.
"""

from __future__ import annotations

import shutil
import tempfile

import pytest

pytest.importorskip("gnupg")  # PGP tests need the optional crypto extra

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
        # stored as inline PGP: a clear-signed text
        assert result.body.startswith("-----BEGIN PGP SIGNED MESSAGE-----")
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
@pytest.mark.parametrize("by", ["email", "filename"])
def test_smime_sign_applies_signature_via_znuny_store(by: str) -> None:
    from tests._smime_fixtures import make_leaf
    from tiqora.crypto.smime_store import SmimeStore

    with (
        tempfile.TemporaryDirectory() as cert_dir,
        tempfile.TemporaryDirectory() as private_dir,
    ):
        leaf = make_leaf("agent@example.org")
        store = SmimeStore(cert_dir, private_dir)
        entry = store.add_certificate(leaf.cert_pem)
        store.add_private_key(leaf.encrypted_key("geheim"), "geheim")
        article = _article()
        settings = Settings(
            TIQORA_CRYPTO_SMIME_ENABLED="1",
            TIQORA_CRYPTO_SMIME_CERT_DIR=cert_dir,
            TIQORA_CRYPTO_SMIME_PRIVATE_DIR=private_dir,
        )
        sign_key = "agent@example.org" if by == "email" else entry.filename
        result = apply_email_security_sync(
            article, {"Backend": "SMIME", "SignKey": sign_key}, settings
        )
        assert "MIME-Version" in result.body
        # The stored body is the signed MIME entity (built by mime_build).
        assert result.content_type == "multipart/signed"
        assert 'protocol="application/pkcs7-signature"' in result.body


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


def test_email_security_from_znuny_maps_the_gi_hash() -> None:
    from tiqora.crypto.outbound import email_security_from_znuny

    sec = email_security_from_znuny(
        {"Backend": "PGP", "Method": "Inline", "SignKey": "81877F5E", "EncryptKeys": ["A", "B"]}
    )
    assert sec is not None
    assert (sec.backend, sec.method, sec.sign_key, sec.encrypt, sec.encrypt_keys) == (
        "pgp",
        "inline",
        "81877F5E",
        True,
        ["A", "B"],
    )
    smime = email_security_from_znuny({"Backend": "SMIME", "Method": "Inline", "SignKey": "x"})
    assert smime is not None and smime.method == "detached" and not smime.encrypt
    assert email_security_from_znuny({"Backend": "ROT13"}) is None
    assert email_security_from_znuny({}) is None
