"""Decrypt-on-view for legacy (Znuny) articles that are still encrypted.

Znuny decrypts an inbound mail only when an agent first opens it
(``ArticleCheck::PGP``/``::SMIME`` + ``ArticleUpdate``), so a migrated
database holds articles whose body is still a ``BEGIN PGP MESSAGE`` block or
whose only attachment is ``smime.p7m`` / ``encrypted.asc``. Tiqora-ingested
mail is decrypted at ingest (:mod:`tiqora.crypto.inbound`) and never gets
here.

For such articles the read endpoints show the decrypted content **without
writing it back** (read-only; the article stays exactly as Znuny left it,
so both systems can keep reading it during parallel operation):

* the source is the stored raw mail (``article_data_mime_plain``), walked
  with :func:`tiqora.crypto.mime_walk.walk_message`; without a raw mail an
  inline-PGP body is decrypted on its own;
* the security result is cached in the ``TiqoraCrypto*`` article flags (so
  the article list shows the badge from then on);
* the decrypted body/attachments are cached in-process for a few minutes,
  keyed by article id — gpg/openssl run once per article and process, not
  once per request. A failed decryption is cached the same way, so new keys
  take effect after the TTL.

Legacy articles that are only *signed* get their security flags computed
once from the raw mail; their content is not touched.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.channels.email.parser import ParsedAttachment, parse_email
from tiqora.crypto.article_security import load_security, security_to_flags, write_security
from tiqora.crypto.config import CryptoConfig, load_crypto_config
from tiqora.crypto.mime_walk import SecurityResult, walk_message
from tiqora.db.legacy.article import ArticleDataMimePlain
from tiqora.storage.backend import AttachmentMeta

logger = structlog.get_logger(__name__)

_TTL_SECONDS = 600
_MAX_ENTRIES = 128
_ENCRYPTED_TYPES = (
    "application/pgp-encrypted",
    "application/pkcs7-mime",
    "application/x-pkcs7-mime",
)
_SIGNED_TYPES = (
    "application/pgp-signature",
    "application/pkcs7-signature",
    "application/x-pkcs7-signature",
)
_PGP_MIME_PAYLOAD_NAMES = ("encrypted.asc", "msg.asc", "openpgp-encrypted-message.asc")
#: Body Znuny's EmailParser leaves when a mail has no text part (S/MIME).
_NO_TEXT = "- no text message => see attachment -"


@dataclass
class DecryptedView:
    security: SecurityResult | None
    body: str | None = None  # None: nothing decrypted, show the stored article
    content_type: str = "text/plain; charset=utf-8"
    attachments: list[ParsedAttachment] = field(default_factory=list)
    created: float = field(default_factory=time.monotonic)

    def attachment_meta(self, article_id: int) -> list[AttachmentMeta]:
        """Virtual attachment rows; ids are negative (``-1`` = first)."""
        return [
            AttachmentMeta(
                id=-(i + 1),
                article_id=article_id,
                filename=a.filename,
                content_type=a.content_type,
                content_size=str(len(a.content)),
                content_id=a.content_id,
                content_alternative=None,
                disposition=a.disposition,
            )
            for i, a in enumerate(self.attachments)
        ]


_cache: OrderedDict[int, DecryptedView] = OrderedDict()
_lock = threading.Lock()


def _cache_get(article_id: int) -> DecryptedView | None:
    with _lock:
        view = _cache.get(article_id)
        if view is None:
            return None
        if time.monotonic() - view.created > _TTL_SECONDS:
            del _cache[article_id]
            return None
        _cache.move_to_end(article_id)
        return view


def _cache_put(article_id: int, view: DecryptedView) -> None:
    with _lock:
        _cache[article_id] = view
        _cache.move_to_end(article_id)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _base_type(ctype: str | None) -> str:
    return (ctype or "").split(";", 1)[0].strip().lower()


def looks_encrypted(body: str | None, attachments: list[AttachmentMeta]) -> bool:
    text = (body or "").lstrip()
    if text.startswith("-----BEGIN PGP MESSAGE-----"):
        return True
    for att in attachments:
        base = _base_type(att.content_type)
        if base in _ENCRYPTED_TYPES and "signed-data" not in (att.content_type or "").lower():
            return True
        # PGP/MIME payload part (Thunderbird / Enigmail names) — stored as a
        # plain octet-stream attachment when the version part was dropped.
        if (att.filename or "").lower() in _PGP_MIME_PAYLOAD_NAMES:
            return True
    return False


def looks_signed(body: str | None, attachments: list[AttachmentMeta]) -> bool:
    if "-----BEGIN PGP SIGNED MESSAGE-----" in (body or ""):
        return True
    return any(
        _base_type(a.content_type) in _SIGNED_TYPES
        or "signed-data" in (a.content_type or "").lower()
        for a in attachments
    )


def might_need_view(body: str | None, attachments: list[AttachmentMeta]) -> bool:
    """Cheap gate used before any flag/config lookup."""
    return looks_encrypted(body, attachments) or looks_signed(body, attachments)


def _inline_mail(body: str) -> bytes:
    from email.message import EmailMessage
    from email.policy import SMTP

    msg = EmailMessage()
    msg.set_content(body, charset="utf-8")
    return msg.as_bytes(policy=SMTP)


def _compute(raw: bytes | None, body: str | None, config: CryptoConfig) -> DecryptedView:
    source = raw if raw else (_inline_mail(body) if body else b"")
    if not source:
        return DecryptedView(security=None)
    outcome = walk_message(source, config)
    view = DecryptedView(security=outcome.security)
    if outcome.content is not None:
        inner = parse_email(outcome.content)
        view.body = inner.body
        view.content_type = inner.content_type
        view.attachments = inner.attachments
    return view


async def _raw_mail(session: AsyncSession, article_id: int) -> bytes | None:
    row = (
        await session.execute(
            select(ArticleDataMimePlain.body)
            .where(ArticleDataMimePlain.article_id == article_id)
            .order_by(ArticleDataMimePlain.id.desc())
        )
    ).first()
    if row is None or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        return value.encode("utf-8", "surrogateescape")
    return bytes(value)


async def _persist_flags(
    session: AsyncSession, article_id: int, result: SecurityResult, user_id: int
) -> None:
    """Cache the result in the flags on a short-lived session of its own.

    Read endpoints must not commit the caller's session (it may be inside
    someone else's transaction), so the flag write uses a fresh session on
    the same engine.
    """
    bind = session.bind
    try:
        async with AsyncSession(bind=bind) as own, own.begin():
            await write_security(own, article_id, result, user_id)
    except Exception as exc:  # noqa: BLE001 — cache write is best effort
        logger.warning("crypto_flag_cache_failed", article_id=article_id, error=str(exc))


async def article_crypto_view(
    session: AsyncSession,
    *,
    article_id: int,
    body: str | None,
    attachments: list[AttachmentMeta],
    user_id: int,
) -> tuple[SecurityResult | None, DecryptedView | None]:
    """Security of one article, plus the decrypted view for legacy encrypted ones.

    Returns ``(security, view)``; ``view`` is ``None`` unless the stored
    article is still encrypted and could be (or was tried to be) decrypted.
    """
    known = (await load_security(session, [article_id])).get(article_id)
    encrypted = looks_encrypted(body, attachments)
    if not encrypted and (known is not None or not looks_signed(body, attachments)):
        return known, None
    cached = _cache_get(article_id)
    if cached is not None:
        return cached.security or known, cached if encrypted else None
    config = await load_crypto_config(session)
    if not (config.pgp.enabled or config.smime.enabled):
        return known, None
    raw = await _raw_mail(session, article_id)
    if not encrypted and raw is None:
        return known, None
    view = await asyncio.to_thread(_compute, raw, body, config)
    _cache_put(article_id, view)
    if view.security is not None and (
        known is None or security_to_flags(view.security) != security_to_flags(known)
    ):
        await _persist_flags(session, article_id, view.security, user_id)
    return view.security or known, view if encrypted else None


__all__ = [
    "DecryptedView",
    "article_crypto_view",
    "clear_cache",
    "looks_encrypted",
    "looks_signed",
    "might_need_view",
]
