"""GenericInterface ``EmailSecurity`` (TicketCreate / TicketUpdate articles).

Znuny's ``Article`` block may carry::

    EmailSecurity => {
        Backend     => 'PGP',                        # PGP or SMIME
        Method      => 'Detached',                   # or Inline (PGP only)
        SignKey     => '81877F5E',                    # optional
        EncryptKeys => [ '81877F5E', '3b630c80' ],     # optional
    }

Two cases, both built by the same :mod:`tiqora.crypto.mime_build`:

* ``ArticleSend => 1`` (Znuny: only then is ``EmailSecurity`` used) — the
  article is sent through the agent email path
  (:func:`tiqora.channels.email.outbound_reply.deliver_agent_email_reply`)
  with :func:`email_security_from_znuny` as its ``email_security``: signed/
  encrypted on the wire, stored in clear, the sent raw mail kept as the
  article's plain source. Key problems fail the operation.
* no ``ArticleSend`` — nothing is sent, so Tiqora keeps its earlier
  behaviour and rewrites the **stored** body (:func:`apply_email_security`):
  PGP as inline PGP (clear-signed / armored text), S/MIME as the
  signed/encrypted MIME entity. Best effort: a disabled backend or a crypto
  failure leaves the body untouched (logged), never blocking ticket creation.
"""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
from typing import Any

import structlog
from pydantic import ValidationError

from tiqora.config import Settings
from tiqora.crypto import CryptoError
from tiqora.crypto.compose import EmailSecurityIn, resolve_plan_sync, split_addresses
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.mime_build import secure_message
from tiqora.domain.ticket_write_service import ArticleIn

logger = structlog.get_logger(__name__)


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def email_security_from_znuny(security: dict[str, Any] | None) -> EmailSecurityIn | None:
    """Znuny ``EmailSecurity`` hash → :class:`EmailSecurityIn` (``None`` = invalid/empty)."""
    if not isinstance(security, dict) or not security:
        return None
    backend = str(security.get("Backend") or "").strip().upper()
    if backend not in ("PGP", "SMIME"):
        return None
    method = str(security.get("Method") or "Detached").strip().lower()
    encrypt_keys = _as_list(security.get("EncryptKeys"))
    try:
        return EmailSecurityIn(
            backend="pgp" if backend == "PGP" else "smime",
            method="inline" if method == "inline" and backend == "PGP" else "detached",
            sign_key=str(security.get("SignKey") or "").strip() or None,
            encrypt=bool(encrypt_keys),
            encrypt_keys=encrypt_keys or None,
        )
    except ValidationError:
        return None


def apply_email_security_sync(
    article: ArticleIn,
    security: dict[str, Any],
    settings: Settings,
    config: CryptoConfig | None = None,
) -> ArticleIn:
    """Rewrite ``article.body`` in place per an EmailSecurity dict (no send).

    *config* is the SysConfig-resolved crypto config; without it only the
    ``TIQORA_CRYPTO_*`` env view of *settings* is used.
    """
    cfg = config or CryptoConfig.from_settings(settings)
    sec = email_security_from_znuny(security)
    if sec is None or not sec.active:
        logger.warning("email_security_ignored", backend=security.get("Backend"))
        return article
    if sec.backend == "pgp":
        # A stored body can only carry PGP inline; PGP/MIME needs a mail.
        sec = sec.model_copy(update={"method": "inline"})
    try:
        plan = resolve_plan_sync(
            cfg,
            sec,
            recipients=split_addresses(article.to_address, article.cc, article.bcc),
            check_recipients=False,
        )
        if plan is None:
            return article
        msg = EmailMessage()
        subtype = "html" if "html" in (article.content_type or "").lower() else "plain"
        msg.set_content(article.body or "", subtype=subtype, charset="utf-8")
        secured = secure_message(msg, plan, cfg)
    except CryptoError as exc:
        logger.error("email_security_failed", backend=sec.backend, error=str(exc))
        return article
    if sec.backend == "pgp":
        article.body = str(secured.message.get_content())
        article.content_type = "text/plain; charset=utf-8"
    else:
        article.body = secured.raw.decode("ascii", "replace")
        article.content_type = secured.message.get_content_type()
    return article


async def apply_email_security(
    article: ArticleIn,
    security: dict[str, Any],
    settings: Settings,
    config: CryptoConfig | None = None,
) -> ArticleIn:
    """Async wrapper: runs the blocking gpg/openssl subprocess calls in a thread."""
    return await asyncio.to_thread(apply_email_security_sync, article, security, settings, config)


__all__ = ["apply_email_security", "apply_email_security_sync", "email_security_from_znuny"]
