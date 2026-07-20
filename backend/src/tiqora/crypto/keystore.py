"""Key lookup + import bookkeeping for PGP and S/MIME.

PGP keys live in the gpg keyring (``TIQORA_CRYPTO_GNUPG_HOME``) — gpg is
already the source of truth for "which keys do we have", so
:func:`import_pgp_key` only adds a :class:`~tiqora.db.tiqora.models.
TiqoraCryptoKey` audit row alongside the keyring import.

S/MIME has no such keyring: certs/private keys are plain files. This module
implements a small, Tiqora-owned convention — flat directories of
``<email>.crt`` / ``<email>.key`` — deliberately simpler than Znuny's
hash-indexed ``SMIME::CertPath``/``SMIME::PrivatePath`` OpenSSL certificate
store layout (see docs/crypto.md for the trade-off).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto import CryptoError
from tiqora.crypto.pgp import PgpEngine
from tiqora.db.tiqora.models import TiqoraCryptoKey

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_.@-]")


def _safe_filename(email: str) -> str:
    """Collapse an email address to a filesystem-safe basename (no path traversal)."""
    return _SAFE_CHARS.sub("_", email.strip().lower())


@dataclass(frozen=True)
class SmimeKeyPaths:
    cert_path: Path | None
    key_path: Path | None


class SmimeKeyStore:
    """Flat-directory cert/key lookup by email address."""

    def __init__(self, cert_dir: str, private_dir: str) -> None:
        self._cert_dir = Path(cert_dir) if cert_dir else None
        self._private_dir = Path(private_dir) if private_dir else None

    def lookup(self, email: str) -> SmimeKeyPaths:
        basename = _safe_filename(email)
        cert_path = None
        key_path = None
        if self._cert_dir is not None:
            candidate = self._cert_dir / f"{basename}.crt"
            if candidate.is_file():
                cert_path = candidate
        if self._private_dir is not None:
            candidate = self._private_dir / f"{basename}.key"
            if candidate.is_file():
                key_path = candidate
        return SmimeKeyPaths(cert_path=cert_path, key_path=key_path)

    def register(
        self, email: str, *, cert_pem: bytes | None = None, key_pem: bytes | None = None
    ) -> SmimeKeyPaths:
        """Write cert/key PEM bytes into the configured directories (0600 for keys)."""
        basename = _safe_filename(email)
        cert_path = None
        key_path = None
        if cert_pem is not None:
            if self._cert_dir is None:
                raise CryptoError("S/MIME cert directory is not configured")
            self._cert_dir.mkdir(parents=True, exist_ok=True)
            cert_path = self._cert_dir / f"{basename}.crt"
            cert_path.write_bytes(cert_pem)
        if key_pem is not None:
            if self._private_dir is None:
                raise CryptoError("S/MIME private-key directory is not configured")
            self._private_dir.mkdir(parents=True, exist_ok=True)
            key_path = self._private_dir / f"{basename}.key"
            key_path.write_bytes(key_pem)
            key_path.chmod(0o600)
        return SmimeKeyPaths(cert_path=cert_path, key_path=key_path)


async def import_pgp_key(
    session: AsyncSession,
    engine: PgpEngine,
    key_data: str,
    *,
    email: str | None = None,
    purpose: str = "both",
) -> list[str]:
    """Import a PGP key into the keyring and record an audit row per fingerprint."""
    fingerprints = engine.import_key(key_data)
    has_private = "-----BEGIN PGP PRIVATE KEY BLOCK-----" in key_data
    for fp in fingerprints:
        session.add(
            TiqoraCryptoKey(
                key_type="pgp",
                identifier=fp,
                email=email,
                purpose=purpose,
                has_private_key=has_private,
            )
        )
    await session.commit()
    return fingerprints


async def register_smime_key(
    session: AsyncSession,
    store: SmimeKeyStore,
    email: str,
    *,
    cert_pem: bytes | None = None,
    key_pem: bytes | None = None,
    purpose: str = "both",
) -> SmimeKeyPaths:
    """Write cert/key files and record an audit row."""
    paths = store.register(email, cert_pem=cert_pem, key_pem=key_pem)
    session.add(
        TiqoraCryptoKey(
            key_type="smime",
            identifier=email,
            email=email,
            purpose=purpose,
            has_private_key=key_pem is not None,
        )
    )
    await session.commit()
    return paths
