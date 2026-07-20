"""Inbound crypto: best-effort PGP/S/MIME decrypt+verify for postmaster mail.

Wired into the postmaster pipeline (:mod:`tiqora.channels.email.pipeline`)
right after ``parse_email(raw)``. Gated by ``TIQORA_CRYPTO_PGP_ENABLED`` /
``TIQORA_CRYPTO_SMIME_ENABLED`` (both OFF by default) — when disabled this
is a no-op. A decrypt/verify failure never blocks delivery: the article is
still created with whatever body was parsed, and the failure is recorded as
the crypto status (mirrors Znuny's ``ArticleCheck::PGP``/``::SMIME``, which
annotate rather than reject).

Scope note (simplification vs Znuny): this operates on the inline PGP block
(``-----BEGIN PGP MESSAGE-----...``) found in the raw message, and on the
raw message as a whole for S/MIME (``openssl smime`` parses the MIME
structure itself) — not a full MIME-tree walk that decrypts/verifies
individual ``multipart/encrypted`` or ``multipart/signed`` sub-parts while
preserving attachments. Good enough for the common "PGP/Inline in the body"
and "S/MIME as the whole message" cases; see docs/crypto.md.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from tiqora.config import Settings
from tiqora.crypto import CryptoError, CryptoUnavailableError
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime import SmimeEngine

_PGP_MESSAGE_RE = re.compile(rb"-----BEGIN PGP MESSAGE-----.*?-----END PGP MESSAGE-----", re.DOTALL)
_PGP_SIGNED_RE = re.compile(
    rb"-----BEGIN PGP SIGNED MESSAGE-----.*?-----END PGP SIGNATURE-----", re.DOTALL
)
_SMIME_CONTENT_TYPE_RE = re.compile(
    rb"^Content-Type:\s*application/(?:x-)?pkcs7-mime", re.IGNORECASE | re.MULTILINE
)


@dataclass(frozen=True)
class CryptoInboundResult:
    method: str  # "pgp" | "smime"
    # "decrypted_verified" | "decrypted_unverified" | "verified" | "verify_failed"
    # | "decrypt_failed" | "unavailable" | "error"
    status: str
    detail: str

    @property
    def article_flag_value(self) -> str:
        """Value stored in the ``TiqoraCryptoVerify`` article_flag row."""
        return f"{self.method}:{self.status}"


def _find_pgp_block(raw: bytes) -> tuple[bytes, bool] | None:
    """Return (block, is_encrypted) for the first PGP block found, if any."""
    m = _PGP_MESSAGE_RE.search(raw)
    if m:
        return m.group(0), True
    m = _PGP_SIGNED_RE.search(raw)
    if m:
        return m.group(0), False
    return None


def _process_pgp(raw: bytes, settings: Settings) -> tuple[str | None, CryptoInboundResult]:
    found = _find_pgp_block(raw)
    if found is None:
        return None, CryptoInboundResult("pgp", "not_present", "no PGP block found")
    block, is_encrypted = found
    try:
        engine = PgpEngine(settings.crypto_pgp_gnupghome)
    except CryptoUnavailableError as exc:
        return None, CryptoInboundResult("pgp", "unavailable", str(exc))
    try:
        if is_encrypted:
            result = engine.decrypt(block)
            if not result.ok:
                return None, CryptoInboundResult("pgp", "decrypt_failed", result.status)
            status = (
                "decrypted_verified"
                if result.verify is not None and result.verify.valid
                else "decrypted_unverified"
            )
            return result.plaintext.decode("utf-8", "replace"), CryptoInboundResult(
                "pgp", status, result.status
            )
        verify = engine.verify(block)
        status = "verified" if verify.valid else "verify_failed"
        return None, CryptoInboundResult("pgp", status, verify.status)
    except CryptoError as exc:
        return None, CryptoInboundResult("pgp", "error", str(exc))


def _process_smime(raw: bytes, settings: Settings) -> tuple[str | None, CryptoInboundResult]:
    """Verify-only: signature check on a signed S/MIME message.

    Simplification: encrypted (``-encrypt``) S/MIME inbound decrypt is not
    wired up at the postmaster level — it would need the *receiving* mail
    account's own private key resolved from :class:`~tiqora.crypto.keystore.
    SmimeKeyStore` by recipient address, which the pipeline does not thread
    through to this call. :func:`tiqora.crypto.smime.SmimeEngine.decrypt` is
    available and covered by unit tests; only the inbound wiring stops at
    verify. See docs/crypto.md.
    """
    if not _SMIME_CONTENT_TYPE_RE.search(raw):
        return None, CryptoInboundResult("smime", "not_present", "no pkcs7-mime content-type")
    engine = SmimeEngine(openssl_bin=settings.crypto_openssl_bin)
    try:
        verify = engine.verify(raw, ca_path=None)
        status = "verified" if verify.valid else "verify_failed"
        return None, CryptoInboundResult("smime", status, verify.detail)
    except (CryptoError, CryptoUnavailableError) as exc:
        return None, CryptoInboundResult("smime", "error", str(exc))


def process_inbound_crypto_sync(
    raw: bytes, settings: Settings
) -> tuple[str | None, CryptoInboundResult | None]:
    """Synchronous core (shells out to gpg/openssl) — see :func:`process_inbound_crypto`
    for the async wrapper used by the (async) postmaster pipeline.

    Returns ``(body_override, result)``. ``body_override`` is the decrypted
    plaintext to use as the article body (``None`` = keep the parsed body
    unchanged). ``result`` is ``None`` when both PGP and S/MIME are disabled
    or neither method was detected in the message.
    """
    if settings.crypto_pgp_enabled:
        body, result = _process_pgp(raw, settings)
        if result.status != "not_present":
            return body, result
    if settings.crypto_smime_enabled:
        body, result = _process_smime(raw, settings)
        if result.status != "not_present":
            return body, result
    return None, None


async def process_inbound_crypto(
    raw: bytes, settings: Settings
) -> tuple[str | None, CryptoInboundResult | None]:
    """Async wrapper: runs the blocking gpg/openssl subprocess calls in a thread."""
    if not settings.crypto_pgp_enabled and not settings.crypto_smime_enabled:
        return None, None
    return await asyncio.to_thread(process_inbound_crypto_sync, raw, settings)
