"""Recognise PGP / S-MIME side files among an article's attachments.

Signed or encrypted mail drags along files that mean nothing to an agent as
plain download rows: ``signature.asc``, ``smime.p7s``, a sender's public key,
or ``PGPexch.htm`` — the HTML rendering of the message body that the PGP
Desktop / Symantec Encryption Outlook plugin attaches because PGP/Inline only
covers the text part. :func:`classify_attachment` labels them so the UI can
explain them and move them out of the way.

Classification is cheap: filename and MIME type decide most cases; only small
text-ish files whose name is ambiguous (``*.asc``, ``*.pem`` …) are sniffed by
their first bytes (:func:`needs_sniff` says which). Pure functions, no I/O.
"""

from __future__ import annotations

import re
from typing import Literal

CryptoKind = Literal[
    "pgp_public_key",
    "pgp_signature",
    "pgp_signed_message",
    "pgp_message",
    "pgp_html_body",
    "smime_signature",
    "x509_certificate",
]

#: Only files up to this size are read to sniff their armor header.
SNIFF_MAX_BYTES = 256 * 1024
#: Bytes of the head that are searched for an armor line.
SNIFF_HEAD_BYTES = 4096

#: ``PGPexch.htm`` / ``PGPexch.rtf.asc`` … written by PGP Desktop for Outlook.
_PGPEXCH_RE = re.compile(r"^pgpexch\.(?:htm|html|rtf)(?:\.(?:asc|pgp|gpg|sig))?$")
#: Extensions whose content decides what the file is (armored text, PEM).
_SNIFF_EXTS = frozenset({"asc", "pem", "key", "pub", "txt"})

_ARMOR: tuple[tuple[bytes, CryptoKind | None], ...] = (
    (b"-----BEGIN PGP PUBLIC KEY BLOCK-----", "pgp_public_key"),
    # A private key is not something to explain away — leave it a plain file.
    (b"-----BEGIN PGP PRIVATE KEY BLOCK-----", None),
    (b"-----BEGIN PGP SIGNED MESSAGE-----", "pgp_signed_message"),
    (b"-----BEGIN PGP SIGNATURE-----", "pgp_signature"),
    (b"-----BEGIN PGP MESSAGE-----", "pgp_message"),
    (b"-----BEGIN CERTIFICATE-----", "x509_certificate"),
)

_MIME_KIND: dict[str, CryptoKind] = {
    "application/pgp-keys": "pgp_public_key",
    "application/pgp-signature": "pgp_signature",
    "application/pgp-encrypted": "pgp_message",
    "application/pkcs7-signature": "smime_signature",
    "application/x-pkcs7-signature": "smime_signature",
    "application/pkix-cert": "x509_certificate",
    "application/x-x509-ca-cert": "x509_certificate",
    "application/x-x509-user-cert": "x509_certificate",
}

_EXT_KIND: dict[str, CryptoKind] = {
    "p7s": "smime_signature",
    "cer": "x509_certificate",
    "crt": "x509_certificate",
    "der": "x509_certificate",
    "p7c": "x509_certificate",
    "pgp": "pgp_message",
    "gpg": "pgp_message",
    "sig": "pgp_signature",
}


def _base_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _ext(name: str) -> str:
    dot = name.rfind(".")
    return name[dot + 1 :] if dot >= 0 else ""


def _size(content_size: str | int | None) -> int | None:
    try:
        return int(str(content_size))
    except (TypeError, ValueError):
        return None


def needs_sniff(
    filename: str | None, content_type: str | None, content_size: str | int | None
) -> bool:
    """True when the first bytes should decide (small, ambiguous text file)."""
    name = (filename or "").strip().lower()
    if _PGPEXCH_RE.match(name):
        return False
    size = _size(content_size)
    if size is None or size > SNIFF_MAX_BYTES:
        return False
    ctype = _base_type(content_type)
    return _ext(name) in _SNIFF_EXTS or ctype in {"application/pgp-keys", "application/pgp"}


def sniff_armor(head: bytes) -> tuple[bool, CryptoKind | None]:
    """``(found, kind)`` for the first armor line in *head* (``found`` False: none)."""
    chunk = head[:SNIFF_HEAD_BYTES]
    first: tuple[int, CryptoKind | None] | None = None
    for marker, kind in _ARMOR:
        pos = chunk.find(marker)
        if pos >= 0 and (first is None or pos < first[0]):
            first = (pos, kind)
    if first is None:
        return False, None
    return True, first[1]


def classify_attachment(
    filename: str | None,
    content_type: str | None,
    head: bytes | None = None,
) -> CryptoKind | None:
    """What crypto side file this is, or ``None`` for an ordinary attachment.

    *head* (the first bytes, see :func:`needs_sniff`) wins over the name: an
    ``.asc`` file is a key, a signature or an encrypted message depending on
    its armor line, and a ``.txt`` is only a key when it says so.
    """
    name = (filename or "").strip().lower()
    if _PGPEXCH_RE.match(name):
        return "pgp_html_body"
    ext = _ext(name)
    if head is not None:
        found, kind = sniff_armor(head)
        if found:
            return kind
        if ext in _SNIFF_EXTS:
            # Sniffed and no armor: the name alone is not enough (a ``.txt``,
            # or a binary ``.key`` — only the MIME type can still tell).
            return _MIME_KIND.get(_base_type(content_type))
    ctype = _base_type(content_type)
    if ctype in _MIME_KIND:
        return _MIME_KIND[ctype]
    if name == "smime.p7s":
        return "smime_signature"
    if (
        ctype in {"application/pkcs7-mime", "application/x-pkcs7-mime"}
        and "certs-only" in (content_type or "").lower()
    ):
        return "x509_certificate"
    return _EXT_KIND.get(ext)


__all__ = [
    "SNIFF_HEAD_BYTES",
    "SNIFF_MAX_BYTES",
    "CryptoKind",
    "classify_attachment",
    "needs_sniff",
    "sniff_armor",
]
