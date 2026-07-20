"""Outbound crypto: honor GenericInterface ``EmailSecurity`` params.

Mirrors Znuny's ``EmailSecurity`` block on ``TicketCreate``/``TicketUpdate``
articles (``Kernel::GenericInterface::Operation::Ticket::TicketCreate``,
``ArticleParams`` docs)::

    EmailSecurity => {
        Backend     => 'PGP',                       # PGP or SMIME
        SignKey     => '81877F5E',                   # optional
        EncryptKeys => [ '81877F5E', '3b630c80' ],    # optional
    }

Applied to the *stored* article body — Tiqora has no live outbound-SMTP
delivery path for GenericInterface-created articles yet (see
docs/channels.md), so this makes the persisted content reflect sign/encrypt,
matching what an operator inspecting the ticket would see. Gated behind
``TIQORA_CRYPTO_PGP_ENABLED``/``TIQORA_CRYPTO_SMIME_ENABLED`` (both default
OFF); when disabled — or on any crypto failure — ``EmailSecurity`` is
silently ignored (logged at WARNING/ERROR), same as before this module
existed: a malformed or unusable EmailSecurity block must never block
ticket creation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from tiqora.config import Settings
from tiqora.crypto import CryptoError, CryptoUnavailableError
from tiqora.crypto.keystore import SmimeKeyStore
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime import SmimeEngine
from tiqora.domain.ticket_write_service import ArticleIn

logger = structlog.get_logger(__name__)


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _apply_pgp(
    article: ArticleIn, sign_key: str | None, encrypt_keys: list[str], settings: Settings
) -> None:
    if not settings.crypto_pgp_enabled:
        logger.warning("email_security_pgp_disabled")
        return
    body_bytes = article.body.encode("utf-8")
    try:
        engine = PgpEngine(settings.crypto_pgp_gnupghome)
        if encrypt_keys:
            body_bytes = engine.encrypt(body_bytes, encrypt_keys, sign_key_id=sign_key)
        elif sign_key:
            signature = engine.sign(body_bytes, sign_key)
            body_bytes = body_bytes + b"\n" + signature
        else:
            return
    except (CryptoError, CryptoUnavailableError):
        logger.exception("email_security_pgp_failed")
        return
    article.body = body_bytes.decode("utf-8", "replace")
    article.content_type = "text/plain; charset=utf-8"


def _apply_smime(
    article: ArticleIn, sign_key: str | None, encrypt_keys: list[str], settings: Settings
) -> None:
    if not settings.crypto_smime_enabled:
        logger.warning("email_security_smime_disabled")
        return
    body_bytes = article.body.encode("utf-8")
    store = SmimeKeyStore(settings.crypto_smime_cert_dir, settings.crypto_smime_private_dir)
    engine = SmimeEngine(openssl_bin=settings.crypto_openssl_bin)
    try:
        if encrypt_keys:
            cert_paths: list[str] = []
            for recipient in encrypt_keys:
                paths = store.lookup(recipient)
                if paths.cert_path is None:
                    raise CryptoError(f"no S/MIME cert on file for {recipient!r}")
                cert_paths.append(str(paths.cert_path))
            body_bytes = engine.encrypt(body_bytes, cert_paths)
        elif sign_key:
            paths = store.lookup(sign_key)
            if paths.cert_path is None or paths.key_path is None:
                raise CryptoError(f"no S/MIME cert/key on file for {sign_key!r}")
            body_bytes = engine.sign(body_bytes, str(paths.cert_path), str(paths.key_path))
        else:
            return
    except (CryptoError, CryptoUnavailableError):
        logger.exception("email_security_smime_failed")
        return
    article.content_type = "application/pkcs7-mime"
    article.body = body_bytes.decode("utf-8", "replace")


def apply_email_security_sync(
    article: ArticleIn, security: dict[str, Any], settings: Settings
) -> ArticleIn:
    """Sign and/or encrypt ``article.body`` in place per an EmailSecurity dict."""
    backend = (security.get("Backend") or "").strip().upper()
    sign_key = security.get("SignKey") or None
    encrypt_keys = _as_list(security.get("EncryptKeys"))

    if backend == "PGP":
        _apply_pgp(article, sign_key, encrypt_keys, settings)
    elif backend == "SMIME":
        _apply_smime(article, sign_key, encrypt_keys, settings)
    else:
        logger.warning("email_security_unknown_backend", backend=backend)
    return article


async def apply_email_security(
    article: ArticleIn, security: dict[str, Any], settings: Settings
) -> ArticleIn:
    """Async wrapper: runs the blocking gpg/openssl subprocess calls in a thread."""
    return await asyncio.to_thread(apply_email_security_sync, article, security, settings)
