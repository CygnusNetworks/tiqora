"""Outbound crypto: honor GenericInterface ``EmailSecurity`` params.

Mirrors Znuny's ``EmailSecurity`` block on ``TicketCreate``/``TicketUpdate``
articles (``Kernel::GenericInterface::Operation::Ticket::TicketCreate``,
``ArticleParams`` docs)::

    EmailSecurity => {
        Backend     => 'PGP',                       # PGP or SMIME
        SignKey     => '81877F5E',                   # optional
        EncryptKeys => [ '81877F5E', '3b630c80' ],    # optional
    }

Applied to the *stored* article body. Agent UI email replies now go through
the live SMTP path (``channels.email.outbound_reply``); GenericInterface-
created articles still primarily store then rely on workers/notifications.
This module makes the persisted content reflect sign/encrypt so an operator
inspecting the ticket sees what was intended. Gated behind
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
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.pgp import PgpEngine
from tiqora.crypto.smime import SmimeEngine
from tiqora.crypto.smime_store import FILENAME_RE, SmimeEntry, SmimeStore
from tiqora.domain.ticket_write_service import ArticleIn

logger = structlog.get_logger(__name__)


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _apply_pgp(
    article: ArticleIn, sign_key: str | None, encrypt_keys: list[str], config: CryptoConfig
) -> None:
    if not config.pgp.enabled:
        logger.warning("email_security_pgp_disabled")
        return
    body_bytes = article.body.encode("utf-8")
    try:
        engine = PgpEngine.from_config(config.pgp)
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


def _smime_lookup(store: SmimeStore, ref: str, *, private: bool) -> SmimeEntry:
    """Resolve a store filename (``<hash>.<n>``) or an email address to one entry."""
    if FILENAME_RE.match(ref):
        entry = store.entry(ref)
        if private and not entry.has_private:
            raise CryptoError(f"no S/MIME private key for {ref!r}")
        return entry
    found = store.search(ref, private=private, valid_only=True)
    if not found:
        raise CryptoError(f"no valid S/MIME {'key' if private else 'cert'} on file for {ref!r}")
    return found[0]


def _apply_smime(
    article: ArticleIn, sign_key: str | None, encrypt_keys: list[str], config: CryptoConfig
) -> None:
    if not config.smime.enabled:
        logger.warning("email_security_smime_disabled")
        return
    body_bytes = article.body.encode("utf-8")
    engine = SmimeEngine(openssl_bin=config.smime.openssl_bin)
    try:
        store = SmimeStore.from_config(config.smime)
        if encrypt_keys:
            cert_paths = [
                str(store.cert_dir / _smime_lookup(store, r, private=False).filename)
                for r in encrypt_keys
            ]
            body_bytes = engine.encrypt(body_bytes, cert_paths)
        elif sign_key:
            entry = _smime_lookup(store, sign_key, private=True)
            paths = store.private_paths(entry.filename)
            private = store.get_private(entry.filename)
            if paths is None or private is None:
                raise CryptoError(f"no S/MIME private key for {sign_key!r}")
            body_bytes = engine.sign(
                body_bytes,
                str(store.cert_dir / entry.filename),
                str(paths[0]),
                secret=private[1],
            )
        else:
            return
    except (CryptoError, CryptoUnavailableError):
        logger.exception("email_security_smime_failed")
        return
    article.content_type = "application/pkcs7-mime"
    article.body = body_bytes.decode("utf-8", "replace")


def apply_email_security_sync(
    article: ArticleIn,
    security: dict[str, Any],
    settings: Settings,
    config: CryptoConfig | None = None,
) -> ArticleIn:
    """Sign and/or encrypt ``article.body`` in place per an EmailSecurity dict.

    *config* is the SysConfig-resolved crypto config; without it only the
    ``TIQORA_CRYPTO_*`` env view of *settings* is used.
    """
    cfg = config or CryptoConfig.from_settings(settings)
    backend = (security.get("Backend") or "").strip().upper()
    sign_key = security.get("SignKey") or None
    encrypt_keys = _as_list(security.get("EncryptKeys"))

    if backend == "PGP":
        _apply_pgp(article, sign_key, encrypt_keys, cfg)
    elif backend == "SMIME":
        _apply_smime(article, sign_key, encrypt_keys, cfg)
    else:
        logger.warning("email_security_unknown_backend", backend=backend)
    return article


async def apply_email_security(
    article: ArticleIn,
    security: dict[str, Any],
    settings: Settings,
    config: CryptoConfig | None = None,
) -> ArticleIn:
    """Async wrapper: runs the blocking gpg/openssl subprocess calls in a thread."""
    return await asyncio.to_thread(apply_email_security_sync, article, security, settings, config)
