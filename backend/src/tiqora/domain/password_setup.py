"""One-time links that let a new agent choose their own password.

The alternative — generating a password and mailing it — leaves a working
credential sitting in an inbox forever, and forces the plaintext through the
API layer (it used to surface in an error response when the mail failed).
Here the account is created with an unusable random hash and the only thing
that travels by mail is a token that expires and can be spent once.

Storage keeps the SHA-256 of the token, never the token: whoever reads the
database cannot reconstruct a working link. Redemption looks the row up *by*
that hash, so the unique index doubles as the lookup path.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.user import Users
from tiqora.db.tiqora.models import TiqoraPasswordSetupToken
from tiqora.znuny.password import hash_password

TOKEN_TTL = timedelta(days=7)
"""Long enough to survive a weekend or a week of leave; the admin can always
issue a fresh link from the user list."""


def _utcnow() -> datetime:
    """Naive UTC — matches the DateTime columns, which store naive."""
    return datetime.utcnow()  # noqa: DTZ003 — intentional naive UTC for DB columns


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def unusable_password_hash() -> str:
    """A hash of a random secret nobody holds — the account exists but cannot
    be logged into until a setup link is redeemed."""
    return hash_password(secrets.token_urlsafe(32))


async def issue_token(session: AsyncSession, user_id: int) -> str:
    """Invalidate the agent's outstanding links and return a fresh token.

    The plaintext is returned to the caller and never stored; this is the only
    moment it exists. Caller owns the transaction.
    """
    # Re-issuing supersedes: a link the admin just replaced must stop working,
    # otherwise "resend" would widen rather than move the window.
    await session.execute(
        update(TiqoraPasswordSetupToken)
        .where(
            TiqoraPasswordSetupToken.user_id == user_id,
            TiqoraPasswordSetupToken.used.is_(None),
        )
        .values(used=_utcnow())
    )
    token = secrets.token_urlsafe(32)
    session.add(
        TiqoraPasswordSetupToken(
            user_id=user_id,
            token_hash=_digest(token),
            expires=_utcnow() + TOKEN_TTL,
        )
    )
    return token


async def resolve_token(session: AsyncSession, token: str) -> int | None:
    """User id behind an unspent, unexpired token, else None."""
    row = (
        await session.execute(
            select(TiqoraPasswordSetupToken).where(
                TiqoraPasswordSetupToken.token_hash == _digest(token)
            )
        )
    ).scalar_one_or_none()
    if row is None or row.used is not None or row.expires <= _utcnow():
        return None
    return row.user_id


async def redeem_token(session: AsyncSession, token: str, new_password: str) -> int | None:
    """Set the password and spend the token. Returns the user id, or None when
    the token is unknown, already spent or expired. Caller owns the
    transaction and must have run
    :func:`tiqora.domain.password_policy.validate_password` first."""
    digest = _digest(token)
    row = (
        await session.execute(
            select(TiqoraPasswordSetupToken).where(TiqoraPasswordSetupToken.token_hash == digest)
        )
    ).scalar_one_or_none()
    if row is None or row.used is not None or row.expires <= _utcnow():
        return None

    now = _utcnow()
    # Spend the token in the same transaction as the password change, so a
    # failure cannot leave a redeemed link with the old password in place.
    row.used = now
    await session.execute(
        update(Users)
        .where(Users.id == row.user_id)
        .values(pw=hash_password(new_password), change_time=now, change_by=row.user_id)
    )
    return row.user_id


def setup_url(settings: Settings, token: str) -> str:
    """Absolute link for the mail. Falls back to the first CORS origin when
    ``TIQORA_PUBLIC_BASE_URL`` is unset (same convention as the OAuth2 mail
    redirect URI)."""
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        origins = settings.cors_origin_list
        base = (origins[0] if origins else "").rstrip("/")
    return f"{base}/set-password?token={token}"
