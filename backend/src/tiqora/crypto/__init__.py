"""PGP and S/MIME support: verify/decrypt inbound mail, sign/encrypt outbound mail.

Ports the concepts (not the implementation) of Znuny's
``Kernel::System::Crypt::PGP`` and ``Kernel::System::Crypt::SMIME``:

* PGP (:mod:`tiqora.crypto.pgp`) shells out to the ``gpg`` binary via the
  ``python-gnupg`` wrapper, same as Znuny's ``PGP::Bin``.
* S/MIME (:mod:`tiqora.crypto.smime`) shells out to the ``openssl`` binary
  directly (``openssl smime ...``), same as Znuny's ``SMIME.pm`` — the
  ``cryptography`` package (already a Tiqora dependency) does not expose a
  public S/MIME verify/encrypt/decrypt API, only signature *building*.

Both are OFF by default (``TIQORA_CRYPTO_PGP_ENABLED`` /
``TIQORA_CRYPTO_SMIME_ENABLED``) and require their respective external
binary to be present. See ``docs/crypto.md``.
"""

from __future__ import annotations


class CryptoError(Exception):
    """Base class for PGP/S/MIME operation failures."""


class CryptoUnavailableError(CryptoError):
    """Raised when a required external tool (gpg/openssl) or key is missing."""
