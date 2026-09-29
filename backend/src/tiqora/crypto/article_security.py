"""Article security status: stored as ``TiqoraCrypto*`` rows in Znuny's ``article_flag``.

``article_flag.article_value`` is ``VARCHAR(50)``, so the summary is spread
over a few keys (all written by the postmaster user, read regardless of
``create_by``):

=========================  =================================================
``TiqoraCryptoVerify``     ``<method>:<status>`` (pre-B2 format, kept)
``TiqoraCryptoLayers``     ``signed``, ``encrypted`` or ``signed,encrypted``
``TiqoraCryptoSigner``     signer address / uid (truncated)
``TiqoraCryptoKeyID``      PGP fingerprint, S/MIME SHA-1 fingerprint (hex) or
                           the S/MIME key file used to decrypt
``TiqoraCryptoDetail``     short human-readable detail (truncated)
=========================  =================================================

Pre-B2 rows only have ``TiqoraCryptoVerify`` with the older statuses
(``decrypted_verified``, ``decrypted_unverified``); they are mapped on read.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.crypto.mime_walk import SecurityResult
from tiqora.db.legacy.article import ArticleDataMimePlain, ArticleFlag

KEY_VERIFY = "TiqoraCryptoVerify"
KEY_LAYERS = "TiqoraCryptoLayers"
KEY_SIGNER = "TiqoraCryptoSigner"
KEY_KEY_ID = "TiqoraCryptoKeyID"
KEY_DETAIL = "TiqoraCryptoDetail"
FLAG_KEYS = (KEY_VERIFY, KEY_LAYERS, KEY_SIGNER, KEY_KEY_ID, KEY_DETAIL)

_MAX = 50

#: pre-B2 status → (status, signed, encrypted)
_LEGACY_STATUS = {
    "decrypted_verified": ("verified", True, True),
    "decrypted_unverified": ("decrypted", False, True),
}


def _clip(value: str | None) -> str | None:
    if not value:
        return None
    value = " ".join(value.split())
    return value if len(value) <= _MAX else value[: _MAX - 1] + "…"


def security_to_flags(result: SecurityResult) -> dict[str, str]:
    layers = ",".join(
        name for name, on in (("signed", result.signed), ("encrypted", result.encrypted)) if on
    )
    flags = {KEY_VERIFY: result.article_flag_value[:_MAX]}
    for key, value in (
        (KEY_LAYERS, layers),
        (KEY_SIGNER, _clip(result.signer)),
        (KEY_KEY_ID, _clip(result.key_id)),
        (KEY_DETAIL, _clip(result.detail)),
    ):
        if value:
            flags[key] = value
    return flags


def security_from_flags(flags: dict[str, str | None]) -> SecurityResult | None:
    verify = flags.get(KEY_VERIFY) or ""
    method, _, status = verify.partition(":")
    if not method or not status:
        return None
    layers = flags.get(KEY_LAYERS)
    if status in _LEGACY_STATUS and layers is None:
        status, signed, encrypted = _LEGACY_STATUS[status]
    elif layers is None:
        # pre-B2 single-layer rows: verify-only statuses mean "signed"
        signed = status not in ("decrypt_failed", "decrypted", "not_present")
        encrypted = status in ("decrypt_failed", "decrypted")
    else:
        parts = set(layers.split(","))
        signed, encrypted = "signed" in parts, "encrypted" in parts
    return SecurityResult(
        method=method,
        signed=signed,
        encrypted=encrypted,
        status=status,
        signer=flags.get(KEY_SIGNER) or None,
        key_id=flags.get(KEY_KEY_ID) or None,
        detail=flags.get(KEY_DETAIL) or "",
    )


async def write_security(
    session: AsyncSession, article_id: int, result: SecurityResult, user_id: int
) -> None:
    """Replace the article's ``TiqoraCrypto*`` flags (caller commits)."""
    await session.execute(
        delete(ArticleFlag).where(
            ArticleFlag.article_id == article_id, ArticleFlag.article_key.in_(FLAG_KEYS)
        )
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    for key, value in security_to_flags(result).items():
        session.add(
            ArticleFlag(
                article_id=article_id,
                article_key=key,
                article_value=value,
                create_time=now,
                create_by=user_id,
            )
        )


async def load_security(
    session: AsyncSession, article_ids: Iterable[int]
) -> dict[int, SecurityResult]:
    ids = list(article_ids)
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(ArticleFlag.article_id, ArticleFlag.article_key, ArticleFlag.article_value)
            .where(ArticleFlag.article_id.in_(ids), ArticleFlag.article_key.in_(FLAG_KEYS))
            .order_by(ArticleFlag.create_time)
        )
    ).all()
    by_article: dict[int, dict[str, str | None]] = {}
    for article_id, key, value in rows:
        by_article.setdefault(int(article_id), {})[str(key)] = value
    out: dict[int, SecurityResult] = {}
    for article_id, flags in by_article.items():
        result = security_from_flags(flags)
        if result is not None:
            out[article_id] = result
    return out


async def store_raw_message(
    session: AsyncSession, article_id: int, raw: bytes, user_id: int
) -> None:
    """Keep the original (signed/encrypted) mail in ``article_data_mime_plain``.

    Znuny stores every inbound mail there (``ArticlePlain``); Tiqora only does
    it for crypto mails, where the article itself holds the decrypted content
    and the raw source is the evidence behind the security status.
    """
    exists = (
        await session.execute(
            select(ArticleDataMimePlain.id).where(ArticleDataMimePlain.article_id == article_id)
        )
    ).first()
    if exists is not None:
        return
    now = datetime.now(UTC).replace(tzinfo=None)
    session.add(
        ArticleDataMimePlain(
            article_id=article_id,
            body=raw,
            create_time=now,
            create_by=user_id,
            change_time=now,
            change_by=user_id,
        )
    )


__all__ = [
    "FLAG_KEYS",
    "KEY_VERIFY",
    "load_security",
    "security_from_flags",
    "security_to_flags",
    "store_raw_message",
    "write_security",
]
