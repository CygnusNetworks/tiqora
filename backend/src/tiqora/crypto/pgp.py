"""PGP: sign/verify/encrypt/decrypt via the ``gpg`` binary (python-gnupg wrapper).

Mirrors Znuny's ``Kernel::System::Crypt::PGP``, which shells out to
``PGP::Bin`` (default ``/usr/bin/gpg``) — same approach here, via
``python-gnupg`` rather than hand-rolled subprocess/regex parsing of gpg
output. ``GNUPGHOME`` is configurable (``TIQORA_CRYPTO_GNUPG_HOME``) so the
keyring is Tiqora-owned and does not collide with any host/system keyring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tiqora.crypto import CryptoError, CryptoUnavailableError


@dataclass(frozen=True)
class PgpVerifyStatus:
    valid: bool
    fingerprint: str | None
    username: str | None
    status: str


@dataclass(frozen=True)
class PgpDecryptResult:
    ok: bool
    plaintext: bytes
    verify: PgpVerifyStatus | None
    status: str


def _require_gnupg() -> Any:
    try:
        import gnupg
    except ImportError as exc:
        raise CryptoUnavailableError(
            "python-gnupg is required for PGP support but is not installed. "
            "It ships in the backend 'crypto' extra — run "
            "`uv sync --extra crypto` (or `uv sync --all-extras`)."
        ) from exc
    return gnupg


class PgpEngine:
    """Thin, testable wrapper around a ``python-gnupg`` GPG instance.

    One instance per ``GNUPGHOME``; each call opens its own ``gnupg.GPG()``
    (cheap — python-gnupg shells out per-call anyway) so this class holds no
    long-lived subprocess state.
    """

    def __init__(self, gnupghome: str) -> None:
        if not gnupghome:
            raise CryptoUnavailableError("PGP GNUPGHOME is not configured")
        self._gnupghome = gnupghome

    def _gpg(self) -> Any:
        gnupg = _require_gnupg()
        Path(self._gnupghome).mkdir(parents=True, exist_ok=True)
        gpg = gnupg.GPG(gnupghome=self._gnupghome)
        gpg.encoding = "utf-8"
        return gpg

    def import_key(self, key_data: str) -> list[str]:
        """Import an ASCII-armored public or private key. Returns fingerprints."""
        result = self._gpg().import_keys(key_data)
        if not result.fingerprints:
            raise CryptoError(f"PGP key import failed: {result.stderr}")
        return list(result.fingerprints)

    def list_key_fingerprints(self, *, secret: bool = False) -> list[str]:
        keys = self._gpg().list_keys(secret)
        return [str(k["fingerprint"]) for k in keys]

    def sign(self, data: bytes, key_id: str, *, passphrase: str | None = None) -> bytes:
        """Detached ASCII-armored signature over *data*."""
        signed = self._gpg().sign(data, keyid=key_id, detach=True, passphrase=passphrase)
        if not signed:
            raise CryptoError(f"PGP sign failed: {getattr(signed, 'stderr', '')}")
        return bytes(signed.data)

    def encrypt(
        self,
        data: bytes,
        recipients: list[str],
        *,
        sign_key_id: str | None = None,
        passphrase: str | None = None,
        always_trust: bool = True,
    ) -> bytes:
        result = self._gpg().encrypt(
            data,
            recipients,
            sign=sign_key_id,
            passphrase=passphrase,
            always_trust=always_trust,
        )
        if not result.ok:
            raise CryptoError(f"PGP encrypt failed: {result.status}: {result.stderr}")
        return bytes(result.data)

    def decrypt(
        self, data: bytes, *, passphrase: str | None = None, always_trust: bool = True
    ) -> PgpDecryptResult:
        """Decrypt (and, if the payload is also signed, verify).

        ``result.valid`` from python-gnupg reflects signature validity when
        the decrypted payload was also signed; for encrypt-only payloads it
        stays ``False`` even though decryption succeeded — callers should
        check ``ok`` for decrypt success and ``verify`` (``None`` if
        unsigned) separately for signature status.
        """
        result = self._gpg().decrypt(data, passphrase=passphrase, always_trust=always_trust)
        verify = None
        if result.fingerprint:
            verify = PgpVerifyStatus(
                valid=bool(result.valid),
                fingerprint=result.fingerprint,
                username=result.username,
                status=result.status or "",
            )
        return PgpDecryptResult(
            ok=bool(result.ok),
            plaintext=bytes(result.data),
            verify=verify,
            status=result.status or "",
        )

    def verify(self, data: bytes) -> PgpVerifyStatus:
        """Verify a clear-signed or inline-signed message (no decryption)."""
        result = self._gpg().verify(data)
        return PgpVerifyStatus(
            valid=bool(result.valid),
            fingerprint=result.fingerprint,
            username=result.username,
            status=result.status or "",
        )
