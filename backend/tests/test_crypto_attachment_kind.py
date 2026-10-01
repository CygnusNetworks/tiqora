"""Classification of PGP / S-MIME side files (tiqora.crypto.attachment_kind)."""

from __future__ import annotations

import pytest

from tiqora.crypto.attachment_kind import (
    SNIFF_MAX_BYTES,
    classify_attachment,
    needs_sniff,
    sniff_armor,
)

PUB = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQENBF...\n-----END PGP PUBLIC KEY BLOCK-----\n"
SIG = b"-----BEGIN PGP SIGNATURE-----\n\niQEzBAEB...\n-----END PGP SIGNATURE-----\n"
MSG = b"-----BEGIN PGP MESSAGE-----\n\nhQEMA...\n-----END PGP MESSAGE-----\n"
CLEAR = b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\nHallo\n" + SIG
# Split so secret scanners do not mistake the fixture for a real key.
PRIV = b"-----BEGIN PGP " + b"PRIVATE KEY BLOCK-----\n\nlQOYBF...\n"
CERT = b"-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----\n"


@pytest.mark.parametrize(
    ("filename", "content_type", "head", "expected"),
    [
        # PGP Desktop / Symantec Outlook plugin: HTML rendering of the body.
        ("PGPexch.htm", "text/html", None, "pgp_html_body"),
        ("PGPexch.htm.asc", "application/octet-stream", None, "pgp_html_body"),
        ("pgpexch.html", "text/html; charset=windows-1252", None, "pgp_html_body"),
        ("PGPexch.rtf.pgp", "application/octet-stream", None, "pgp_html_body"),
        # .asc decided by its armor line
        ("public_key_someone@example.org.asc", "application/pgp-keys", PUB, "pgp_public_key"),
        ("0xDEADBEEF.asc", "text/plain", PUB, "pgp_public_key"),
        ("signature.asc", "application/pgp-signature", SIG, "pgp_signature"),
        ("msg.asc", "application/octet-stream", MSG, "pgp_message"),
        ("note.asc", "text/plain", CLEAR, "pgp_signed_message"),
        ("leaked.asc", "application/pgp-keys", PRIV, None),
        ("random.asc", "text/plain", b"just some text", None),
        # A key pasted into a .txt is a key; any other .txt is not.
        ("key.txt", "text/plain", b"Here it is:\n" + PUB, "pgp_public_key"),
        ("notes.txt", "text/plain", b"hello", None),
        ("server.pem", "application/x-pem-file", CERT, "x509_certificate"),
        ("server.pem", "application/x-pem-file", b"-----BEGIN PRIVATE KEY-----", None),
        # MIME type / name only (not sniffed)
        ("signature.asc", "application/pgp-signature", None, "pgp_signature"),
        ("encrypted.bin", "application/pgp-encrypted", None, "pgp_message"),
        ("smime.p7s", "application/pkcs7-signature", None, "smime_signature"),
        ("smime.p7s", "application/octet-stream", None, "smime_signature"),
        ("x.dat", "application/x-pkcs7-signature", None, "smime_signature"),
        ("ca.cer", "application/octet-stream", None, "x509_certificate"),
        ("user.crt", "application/pkix-cert", None, "x509_certificate"),
        ("certs.p7c", "application/pkcs7-mime; smime-type=certs-only", None, "x509_certificate"),
        ("backup.gpg", "application/octet-stream", None, "pgp_message"),
        ("detached.sig", "application/octet-stream", None, "pgp_signature"),
        # ordinary attachments
        ("report.pdf", "application/pdf", None, None),
        ("index.htm", "text/html", None, None),
        ("smime.p7m", "application/pkcs7-mime; smime-type=enveloped-data", None, None),
        (None, None, None, None),
    ],
)
def test_classify(
    filename: str | None, content_type: str | None, head: bytes | None, expected: str | None
) -> None:
    assert classify_attachment(filename, content_type, head) == expected


def test_needs_sniff_only_small_ambiguous_files() -> None:
    assert needs_sniff("key.asc", "text/plain", "7700")
    assert needs_sniff("x.bin", "application/pgp-keys", 1200)
    assert needs_sniff("cert.pem", None, "900")
    assert not needs_sniff("key.asc", "text/plain", str(SNIFF_MAX_BYTES + 1))
    assert not needs_sniff("key.asc", "text/plain", None)
    assert not needs_sniff("report.pdf", "application/pdf", "1000")
    # PGPexch is decided by name, never read.
    assert not needs_sniff("PGPexch.htm.asc", "text/plain", "1000")
    # signature.asc: sniffed, so a mislabelled key still shows as a key.
    assert needs_sniff("signature.asc", "application/pgp-signature", "833")


def test_sniff_picks_the_first_armor_line() -> None:
    # A clear-signed text contains a SIGNATURE block too — the first line wins.
    assert sniff_armor(CLEAR) == (True, "pgp_signed_message")
    assert sniff_armor(b"no armor") == (False, None)
    assert sniff_armor(PRIV) == (True, None)
