"""S/MIME: sign/verify/encrypt/decrypt via the ``openssl`` CLI.

Mirrors Znuny's ``Kernel::System::Crypt::SMIME``, which shells out to
``openssl smime ...`` for every operation — the ``cryptography`` package
(already a Tiqora dependency, used elsewhere for TOTP/Fernet) has no public
API for S/MIME verify/encrypt/decrypt, only signature *building*
(``cryptography.hazmat.primitives.serialization.pkcs7``), so shelling out to
``openssl`` (same tool Znuny uses) is the pragmatic choice here rather than
hand-rolling PKCS7 parsing.

Certificate/key lookup (:mod:`tiqora.crypto.keystore`) is a Tiqora-owned
simplification of Znuny's ``SMIME::CertPath``/``SMIME::PrivatePath`` — a flat
directory of ``<email>.crt`` / ``<email>.key`` files, not Znuny's
hash-indexed OpenSSL certificate store layout.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from tiqora.crypto import CryptoError, CryptoUnavailableError

DEFAULT_OPENSSL_BIN = "openssl"
_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class SmimeVerifyResult:
    valid: bool
    detail: str


@dataclass(frozen=True)
class SmimeDecryptResult:
    ok: bool
    plaintext: bytes
    detail: str


class SmimeEngine:
    """Thin wrapper around ``openssl smime`` subprocess calls."""

    def __init__(self, *, openssl_bin: str = DEFAULT_OPENSSL_BIN) -> None:
        self._openssl_bin = openssl_bin

    def _run(self, args: list[str], input_bytes: bytes) -> subprocess.CompletedProcess[bytes]:
        try:
            return subprocess.run(  # noqa: S603 — fixed binary name, args are our own list
                [self._openssl_bin, *args],
                input=input_bytes,
                capture_output=True,
                check=False,
                timeout=_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise CryptoUnavailableError(
                f"openssl binary not found ({self._openssl_bin!r}) — S/MIME support requires it"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CryptoError(f"openssl smime call timed out: {exc}") from exc

    def sign(self, data: bytes, cert_path: str, key_path: str) -> bytes:
        """Detached S/MIME signature (``multipart/signed`` MIME output)."""
        proc = self._run(
            ["smime", "-sign", "-signer", cert_path, "-inkey", key_path, "-text"], data
        )
        if proc.returncode != 0:
            raise CryptoError(
                f"openssl smime -sign failed: {proc.stderr.decode('utf-8', 'replace')}"
            )
        return proc.stdout

    def encrypt(self, data: bytes, recipient_cert_paths: list[str]) -> bytes:
        if not recipient_cert_paths:
            raise CryptoError("openssl smime -encrypt requires at least one recipient cert")
        proc = self._run(["smime", "-encrypt", "-aes256", *recipient_cert_paths], data)
        if proc.returncode != 0:
            raise CryptoError(
                f"openssl smime -encrypt failed: {proc.stderr.decode('utf-8', 'replace')}"
            )
        return proc.stdout

    def decrypt(self, data: bytes, cert_path: str, key_path: str) -> SmimeDecryptResult:
        proc = self._run(["smime", "-decrypt", "-recip", cert_path, "-inkey", key_path], data)
        ok = proc.returncode == 0
        return SmimeDecryptResult(
            ok=ok,
            plaintext=proc.stdout if ok else b"",
            detail=proc.stderr.decode("utf-8", "replace"),
        )

    def verify(self, data: bytes, *, ca_path: str | None = None) -> SmimeVerifyResult:
        """Verify a detached S/MIME signature.

        Without ``ca_path``, ``-noverify`` is used: the signature is checked
        cryptographically but the certificate chain is NOT validated against
        a trust root. This matches the ephemeral self-signed test certs this
        module is tested against; production use should pass ``ca_path`` to
        get real chain-of-trust validation (Znuny's ``SignerCertRelation``
        table equivalent is out of scope here — see docs/crypto.md).
        """
        args = ["smime", "-verify"]
        args += ["-CAfile", ca_path] if ca_path else ["-noverify"]
        proc = self._run(args, data)
        return SmimeVerifyResult(
            valid=proc.returncode == 0, detail=proc.stderr.decode("utf-8", "replace")
        )
