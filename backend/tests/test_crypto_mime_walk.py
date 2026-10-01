"""Inbound MIME-tree walk (tiqora.crypto.mime_walk) against real gpg/openssl.

Every shape is built by a real signer/encrypter (tests/_crypto_mail_fixtures.py)
and read back — round-trip, no canned bytes. The committed sample mails in
tests/fixtures/crypto/ (Thunderbird PGP/MIME, Outlook S/MIME) are checked at
the end with the keys stored next to them.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._crypto_mail_fixtures import (
    CUSTOMER,
    FIXTURE_DIR,
    SAMPLE_PDF,
    SUPPORT,
    CryptoWorld,
    _gpg,
    crlf,
    gen_pgp_key,
    mail,
    mixed_entity,
    outlook_smime_signed,
    outlook_smime_signed_entity,
    pgp_mime_encrypted,
    pgp_mime_signed,
    smime_detached,
    strip_mime_version,
    text_entity,
    thunderbird_pgp_mime,
)
from tiqora.channels.email.parser import ParsedEmail, parse_email
from tiqora.crypto.config import CryptoConfig, PgpConfig, SmimeConfig
from tiqora.crypto.mime_walk import LayerResult, combine_layers, walk_message
from tiqora.crypto.smime_store import SmimeStore

pytestmark = [
    pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg binary not on PATH"),
    pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl binary not on PATH"),
]


@pytest.fixture(scope="module")
def world() -> Iterator[CryptoWorld]:
    pytest.importorskip("gnupg")
    w = CryptoWorld.create()
    yield w
    w.cleanup()


def _content(outcome_content: bytes | None) -> ParsedEmail:
    assert outcome_content is not None
    return parse_email(outcome_content)


def _mixed() -> bytes:
    return mixed_entity(
        "Hello support, see attachment.\r\n", [("report.pdf", "application/pdf", SAMPLE_PDF)]
    )


# ------------------------------------------------------------------- PGP/MIME


def test_pgp_mime_signed_verified_keeps_attachments(world: CryptoWorld) -> None:
    raw = mail(pgp_mime_signed(world, _mixed()))
    out = walk_message(raw, world.config())
    assert out.security is not None
    assert out.security.method == "pgp"
    assert out.security.status == "verified"
    assert out.security.signed and not out.security.encrypted
    assert out.security.key_id == world.customer_fp
    assert CUSTOMER in (out.security.signer or "")
    parsed = _content(out.content)
    assert parsed.body.startswith("Hello support")
    assert [a.filename for a in parsed.attachments] == ["report.pdf"]
    assert parsed.attachments[0].content == SAMPLE_PDF


def test_pgp_mime_signed_lf_only_mail_still_verifies(world: CryptoWorld) -> None:
    raw = mail(pgp_mime_signed(world, text_entity("Hello\r\n"))).replace(b"\r\n", b"\n")
    out = walk_message(raw, world.config())
    assert out.security is not None and out.security.status == "verified"


def test_pgp_mime_signed_tampered_fails(world: CryptoWorld) -> None:
    raw = mail(pgp_mime_signed(world, text_entity("Hello support\r\n")))
    tampered = raw.replace(b"SGVsbG8gc3VwcG9ydA", b"SGVsbG8gc3VwcG9ydB")
    if tampered == raw:  # quoted-printable / 7bit body
        tampered = raw.replace(b"Hello support", b"Hello suppory")
    out = walk_message(tampered, world.config())
    assert out.security is not None
    assert out.security.status == "verify_failed"


def test_pgp_mime_signed_unknown_key(world: CryptoWorld) -> None:
    # Signed by a key that is not in Tiqora's keyring.
    stranger_fp = gen_pgp_key(world.customer_home, "Stranger", "stranger@example.net")
    inner = text_entity("Hi\r\n")
    entity = pgp_mime_signed(world, inner)
    stranger_sig = crlf(world.pgp_detached_sign(crlf(inner), fp=stranger_fp)).strip()
    start = entity.index(b"-----BEGIN PGP SIGNATURE-----")
    end = entity.index(b"-----END PGP SIGNATURE-----") + len(b"-----END PGP SIGNATURE-----")
    out = walk_message(mail(entity[:start] + stranger_sig + entity[end:]), world.config())
    assert out.security is not None
    assert out.security.status == "unknown_key"
    assert out.security.key_id and stranger_fp.endswith(out.security.key_id[-16:])


def test_pgp_signer_sender_mismatch_is_untrusted(world: CryptoWorld) -> None:
    raw = mail(pgp_mime_signed(world, text_entity("Hi\r\n")), frm="ceo@example.com")
    out = walk_message(raw, world.config())
    assert out.security is not None
    assert out.security.status == "signed_untrusted"
    assert "does not match" in out.security.detail


def test_pgp_mime_encrypted_restores_body_and_attachments(world: CryptoWorld) -> None:
    raw = mail(pgp_mime_encrypted(world, _mixed()))
    out = walk_message(raw, world.config())
    assert out.security is not None
    assert out.security.status == "decrypted"
    assert out.security.encrypted and not out.security.signed
    parsed = _content(out.content)
    assert "Hello support" in parsed.body
    assert [(a.filename, a.content) for a in parsed.attachments] == [("report.pdf", SAMPLE_PDF)]


def test_pgp_mime_signed_and_encrypted_combined(world: CryptoWorld) -> None:
    out = walk_message(mail(pgp_mime_encrypted(world, _mixed(), sign=True)), world.config())
    assert out.security is not None
    assert out.security.status == "verified"
    assert out.security.encrypted and out.security.signed
    assert out.security.key_id == world.customer_fp


def test_pgp_mime_signed_inside_encrypted(world: CryptoWorld) -> None:
    signed = pgp_mime_signed(world, _mixed())
    out = walk_message(mail(pgp_mime_encrypted(world, signed)), world.config())
    assert out.security is not None
    assert out.security.status == "verified"
    assert out.security.encrypted and out.security.signed
    parsed = _content(out.content)
    assert [a.filename for a in parsed.attachments] == ["report.pdf"]


def test_pgp_mime_encrypted_for_other_key_fails(world: CryptoWorld) -> None:
    other_home = f"{world.root}/o"
    Path(other_home).mkdir(mode=0o700)
    other_fp = gen_pgp_key(other_home, "Other", "other@example.org")
    g = _gpg(world.customer_home)
    g.import_keys(_gpg(other_home).export_keys(other_fp))
    armored = g.encrypt(
        b"Content-Type: text/plain\r\n\r\nsecret\r\n", [other_fp], always_trust=True
    )
    entity = pgp_mime_encrypted(world, text_entity("x"))
    start = entity.index(b"-----BEGIN PGP MESSAGE-----")
    end = entity.index(b"-----END PGP MESSAGE-----") + len(b"-----END PGP MESSAGE-----")
    entity = entity[:start] + bytes(armored.data).replace(b"\n", b"\r\n").strip() + entity[end:]
    out = walk_message(mail(entity), world.config())
    assert out.security is not None
    assert out.security.status == "decrypt_failed"
    assert out.content is None


# ------------------------------------------------------------------ inline PGP


def test_inline_pgp_encrypted_body(world: CryptoWorld) -> None:
    armored = world.pgp_encrypt("Grüße, inline secret".encode(), sign=True).decode()
    out = walk_message(mail(text_entity(armored)), world.config())
    assert out.security is not None
    assert out.security.status == "verified"
    assert out.security.encrypted
    assert _content(out.content).body.strip() == "Grüße, inline secret"


def test_inline_pgp_clearsigned_body_is_unarmored(world: CryptoWorld) -> None:
    signed = world.pgp_clearsign("please help\n- dashed line\n")
    out = walk_message(mail(text_entity(signed)), world.config())
    assert out.security is not None
    assert out.security.status == "verified"
    body = _content(out.content).body
    assert "BEGIN PGP" not in body
    assert "please help" in body and "- dashed line" in body


def test_inline_pgp_clearsigned_tampered(world: CryptoWorld) -> None:
    signed = world.pgp_clearsign("please help\n").replace("please help", "please pay")
    out = walk_message(mail(text_entity(signed)), world.config())
    assert out.security is not None
    assert out.security.status == "verify_failed"


def test_inline_pgp_encrypted_attachment_is_decrypted_and_renamed(world: CryptoWorld) -> None:
    enc = world.pgp_encrypt(SAMPLE_PDF, armor=False)
    armored_body = world.pgp_encrypt(b"see attachment").decode()
    entity = mixed_entity(armored_body, [("report.pdf.pgp", "application/octet-stream", enc)])
    out = walk_message(mail(entity), world.config())
    assert out.security is not None and out.security.status == "decrypted"
    parsed = _content(out.content)
    assert parsed.body.strip() == "see attachment"
    assert [(a.filename, a.content) for a in parsed.attachments] == [("report.pdf", SAMPLE_PDF)]


def test_pgp_disabled_is_a_noop(world: CryptoWorld) -> None:
    out = walk_message(mail(pgp_mime_encrypted(world, _mixed())), world.config(pgp=False))
    assert out.security is None and out.content is None


def test_plain_mail_is_untouched(world: CryptoWorld) -> None:
    out = walk_message(mail(_mixed()), world.config())
    assert out.security is None and out.content is None


# ---------------------------------------------------------------------- S/MIME


def test_smime_detached_trusted_via_certpath(world: CryptoWorld) -> None:
    out = walk_message(outlook_smime_signed(world), world.config())
    assert out.security is not None
    assert out.security.method == "smime"
    assert out.security.status == "verified", out.security.detail
    assert out.security.signer == CUSTOMER
    assert out.security.key_id and len(out.security.key_id) == 40
    parsed = _content(out.content)
    assert "reset my VPN token" in parsed.body
    assert parsed.attachments == []  # smime.p7s is gone


def test_smime_detached_tampered(world: CryptoWorld) -> None:
    raw = mail(smime_detached(world, text_entity("Hello there\r\n"), tamper=True))
    out = walk_message(raw, world.config())
    assert out.security is not None
    assert out.security.status == "verify_failed"


def test_smime_opaque_signed_extracts_content(world: CryptoWorld) -> None:
    opaque = strip_mime_version(world.smime_opaque_sign(_mixed()))
    out = walk_message(mail(opaque), world.config())
    assert out.security is not None
    assert out.security.status == "verified", out.security.detail
    parsed = _content(out.content)
    assert [a.filename for a in parsed.attachments] == ["report.pdf"]


def test_smime_enveloped_decrypts_with_recipient_key(world: CryptoWorld) -> None:
    enc = strip_mime_version(world.smime_encrypt(_mixed()))
    out = walk_message(mail(enc), world.config())
    assert out.security is not None
    assert out.security.status == "decrypted", out.security.detail
    assert out.security.encrypted and not out.security.signed
    parsed = _content(out.content)
    assert [(a.filename, a.content) for a in parsed.attachments] == [("report.pdf", SAMPLE_PDF)]


def test_smime_signed_inside_encrypted(world: CryptoWorld) -> None:
    enc = strip_mime_version(world.smime_encrypt(outlook_smime_signed_entity(world)))
    out = walk_message(mail(enc), world.config())
    assert out.security is not None
    assert out.security.status == "verified", out.security.detail
    assert out.security.encrypted and out.security.signed
    assert "reset my VPN token" in _content(out.content).body


def test_smime_enveloped_uses_delivered_to(world: CryptoWorld) -> None:
    enc = strip_mime_version(world.smime_encrypt(text_entity("hi\r\n")))
    raw = mail(enc, to="list@example.com", extra=f"Delivered-To: {SUPPORT}\r\n")
    out = walk_message(raw, world.config())
    assert out.security is not None and out.security.status == "decrypted"


def test_smime_enveloped_without_matching_key(world: CryptoWorld) -> None:
    enc = strip_mime_version(world.smime_encrypt(text_entity("hi\r\n")))
    out = walk_message(mail(enc, to="someone@example.com"), world.config())
    assert out.security is not None
    assert out.security.status == "decrypt_failed"
    assert "no private key" in out.security.detail


def test_smime_untrusted_without_ca(world: CryptoWorld, tmp_path: Path) -> None:
    # Separate store: only support's cert + key, no CA → chain does not validate.
    cert_dir, private_dir = tmp_path / "c", tmp_path / "p"
    store = SmimeStore(str(cert_dir), str(private_dir))
    store.add_certificate(world.support_cert.cert_pem)
    cfg = CryptoConfig(
        pgp=PgpConfig(enabled=False),
        smime=SmimeConfig(enabled=True, cert_path=str(cert_dir), private_path=str(private_dir)),
    )
    out = walk_message(outlook_smime_signed(world), cfg)
    assert out.security is not None
    assert out.security.status == "signed_untrusted"
    assert out.security.signer == CUSTOMER


def test_smime_no_verify_counts_unverified_chain_as_verified(
    world: CryptoWorld, tmp_path: Path
) -> None:
    # SMIME::NoVerify (Znuny semantics): same untrusted store as above, but on.
    cert_dir, private_dir = tmp_path / "c", tmp_path / "p"
    SmimeStore(str(cert_dir), str(private_dir)).add_certificate(world.support_cert.cert_pem)
    cfg = CryptoConfig(
        pgp=PgpConfig(enabled=False),
        smime=SmimeConfig(
            enabled=True, cert_path=str(cert_dir), private_path=str(private_dir), no_verify=True
        ),
    )
    out = walk_message(outlook_smime_signed(world), cfg)
    assert out.security is not None
    assert out.security.status == "verified"
    assert "NoVerify" in out.security.detail


# ------------------------------------------------------------- combine rules


def test_combine_layers_worst_status_wins() -> None:
    layers = [
        LayerResult("pgp", "encryption", "decrypted"),
        LayerResult("pgp", "signature", "verify_failed", signer="x", key_id="AB"),
    ]
    res = combine_layers(layers)
    assert res is not None
    assert (res.status, res.signed, res.encrypted, res.key_id) == (
        "verify_failed",
        True,
        True,
        "AB",
    )
    assert combine_layers([]) is None


# ------------------------------------------------------------ sample mails


@pytest.fixture(scope="module")
def sample_config(tmp_path_factory: pytest.TempPathFactory) -> Iterator[CryptoConfig]:
    import tempfile

    pytest.importorskip("gnupg")
    home = tempfile.mkdtemp(prefix="tqs", dir="/tmp")  # noqa: S108 — gpg socket path
    g = _gpg(home)
    g.import_keys((FIXTURE_DIR / "customer-public.asc").read_text())
    g.import_keys((FIXTURE_DIR / "support-secret.asc").read_text())
    base = tmp_path_factory.mktemp("samples")
    store = SmimeStore(str(base / "certs"), str(base / "private"))
    store.add_certificate((FIXTURE_DIR / "ca.crt").read_bytes())
    store.add_certificate((FIXTURE_DIR / "support.crt").read_bytes())
    store.add_private_key((FIXTURE_DIR / "support.key").read_bytes(), "")
    yield CryptoConfig(
        pgp=PgpConfig(enabled=True, homedir=home),
        smime=SmimeConfig(
            enabled=True, cert_path=str(base / "certs"), private_path=str(base / "private")
        ),
    )
    shutil.rmtree(home, ignore_errors=True)


def test_sample_thunderbird_pgp_mime(sample_config: CryptoConfig) -> None:
    raw = (FIXTURE_DIR / "thunderbird-pgp-mime-signed-encrypted.eml").read_bytes()
    out = walk_message(raw, sample_config)
    assert out.security is not None
    assert (out.security.status, out.security.encrypted, out.security.signed) == (
        "verified",
        True,
        True,
    )
    assert out.protected_subject == "Invoice 2026-17 question"
    parsed = _content(out.content)
    assert "invoice is attached" in parsed.body
    assert [(a.filename, a.content) for a in parsed.attachments] == [("invoice.pdf", SAMPLE_PDF)]


def test_sample_outlook_smime_signed(sample_config: CryptoConfig) -> None:
    raw = (FIXTURE_DIR / "outlook-smime-detached-signed.eml").read_bytes()
    out = walk_message(raw, sample_config)
    assert out.security is not None
    assert out.security.status == "verified", out.security.detail
    assert out.security.signer == CUSTOMER


def test_sample_outlook_smime_encrypted(sample_config: CryptoConfig) -> None:
    raw = (FIXTURE_DIR / "outlook-smime-encrypted.eml").read_bytes()
    out = walk_message(raw, sample_config)
    assert out.security is not None
    assert (out.security.status, out.security.encrypted, out.security.signed) == (
        "verified",
        True,
        True,
    )
    assert "reset my VPN token" in _content(out.content).body


def test_samples_are_generated_by_the_fixture_builders() -> None:
    # Guard against hand-edited samples: the builders' shapes are recognisable.
    tb = (FIXTURE_DIR / "thunderbird-pgp-mime-signed-encrypted.eml").read_bytes()
    assert b"This is an OpenPGP/MIME encrypted message" in tb
    assert b"Subject: ..." in tb
    ol = (FIXTURE_DIR / "outlook-smime-detached-signed.eml").read_bytes()
    assert b'protocol="application/x-pkcs7-signature"' in ol
    _ = thunderbird_pgp_mime
