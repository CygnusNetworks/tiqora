"""Linking a verified visitor identity to a Znuny customer, and its effect on a chat (C4).

``identity.py`` proves WHO signed a claim set; this module decides which customer that is
(``resolve_identity``) and applies it to a new chat (``create_session`` takes the result) or a
running one (``identify_session``). Customer lookups are single-table queries on
``customer_user`` (no JOIN with ``tiqora_*``); claims are never logged beyond ``source`` and
``known_customer``.

Lock order (R-LOCK-ORDER): ``identify_session`` locks the ``tiqora_chat_session`` row first,
then writes the ticket customer.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.crypto.secret import decrypt_secret
from tiqora.db.tiqora.models import TiqoraChatConfig, TiqoraChatSession
from tiqora.domain.customer_auth import AuthenticatedCustomer
from tiqora.domain.ticket_write_service import set_customer
from tiqora.domain.user_provisioning import ProvisioningError, create_customer_user
from tiqora.webchat.identity import (
    PORTAL_ASSERTION_SECONDS,
    PORTAL_KID,
    IdentityClaims,
    portal_identity_key,
    sign_identity_token,
    verify_identity_token,
)
from tiqora.webchat.models import ChatConfigValues
from tiqora.webchat.notify import queue_wakeup
from tiqora.webchat.prechat import free_text_customer_id
from tiqora.webchat.sessions import SYSTEM_USER_ID, ChatError, lock_session
from tiqora.webchat.store import LIVE_STATUSES
from tiqora.znuny.sysconfig import SysConfig

__all__ = [
    "ResolvedIdentity",
    "identify_session",
    "issue_portal_assertion",
    "resolve_identity",
    "verify_and_resolve",
    "website_secret",
]

logger = structlog.get_logger(__name__)

_FIRST_NAME_FALLBACK: Final = "Besucher"
_MAX_NAME_PART: Final = 100
_MAX_CREATE_EMAIL: Final = 150  # customer_user.email / customer_id are VARCHAR(150)


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    source: Literal["portal", "website"]
    customer_user_login: str  # existing login or the free-text id
    customer_id: str | None
    email: str | None  # verified address (from the token or the customer_user row)
    name: str | None
    known_customer: bool


def _epoch(now: datetime) -> int:
    """Naive UTC datetime → unix seconds."""
    return calendar.timegm(now.utctimetuple())


def website_secret(config: TiqoraChatConfig, settings: Settings) -> str | None:
    """The chat's identity secret in clear; ``None`` when unset or undecryptable."""
    token = config.identity_secret
    if not token:
        return None
    return decrypt_secret(settings.secret_key, token) or None


def _full_name(first: object, last: object) -> str | None:
    parts = [str(p).strip() for p in (first, last) if p and str(p).strip()]
    return " ".join(parts) or None


async def _customers_by(
    db: AsyncSession, column: str, value: str
) -> list[tuple[str, str | None, str | None, str | None]]:
    """Valid customer users whose *column* (``login`` / ``email``) equals *value*,
    case-insensitively: ``(login, customer_id, email, full name)``; at most two rows."""
    sql = (
        "SELECT login, customer_id, email, first_name, last_name FROM customer_user"  # noqa: S608
        f" WHERE LOWER({column}) = LOWER(:v) AND valid_id = 1 ORDER BY id LIMIT 2"
    )
    rows = (await db.execute(text(sql), {"v": value})).all()
    return [
        (
            str(r[0]),
            str(r[1]) if r[1] else None,
            str(r[2]) if r[2] else None,
            _full_name(r[3], r[4]),
        )
        for r in rows
    ]


def _split_name(name: str | None, email: str) -> tuple[str, str]:
    local = email.split("@", 1)[0]
    parts = (name or "").split()
    first = parts[0] if parts else _FIRST_NAME_FALLBACK
    last = " ".join(parts[1:]) if len(parts) > 1 else local
    return first[:_MAX_NAME_PART], (last or local)[:_MAX_NAME_PART]


async def resolve_identity(
    db: AsyncSession, *, claims: IdentityClaims, values: ChatConfigValues
) -> ResolvedIdentity:
    """Map verified claims to a customer (C4 steps 1-4)."""
    found: tuple[str, str | None, str | None, str | None] | None = None
    if claims.sub:
        rows = await _customers_by(db, "login", claims.sub)
        found = rows[0] if rows else None
    if found is None and claims.email:
        rows = await _customers_by(db, "email", claims.email)
        found = rows[0] if len(rows) == 1 else None  # several → treat as not found
    if found is not None:
        login, customer_id, email, full = found
        return ResolvedIdentity(
            source=claims.source,
            customer_user_login=login,
            customer_id=customer_id,
            email=email or claims.email,
            name=claims.name or full,
            known_customer=True,
        )

    email = claims.email
    if values.identity_create_customers and email and len(email) <= _MAX_CREATE_EMAIL:
        first, last = _split_name(claims.name, email)
        try:
            async with db.begin_nested():
                await create_customer_user(
                    db,
                    login=email,
                    email=email,
                    customer_id=email,
                    first_name=first,
                    last_name=last,
                    pw_hash=None,
                    actor_id=SYSTEM_USER_ID,
                )
        except ProvisioningError as exc:
            # E.g. the address belongs to an invalid or ambiguous account: the documented
            # "unknown customer" behaviour (step 4). Log the code only.
            logger.info("webchat_identity_create_refused", code=exc.code)
        else:
            return ResolvedIdentity(
                source=claims.source,
                customer_user_login=email,
                customer_id=email,
                email=email,
                name=claims.name,
                known_customer=True,
            )

    free_text = email or claims.sub
    assert free_text is not None  # IdentityClaims always carries sub or email
    return ResolvedIdentity(
        source=claims.source,
        customer_user_login=free_text,
        customer_id=free_text_customer_id(free_text),
        email=email,
        name=claims.name,
        known_customer=False,
    )


async def verify_and_resolve(
    db: AsyncSession,
    settings: Settings,
    *,
    config: TiqoraChatConfig,
    values: ChatConfigValues,
    token: str,
    visitor_id: str,
    now: datetime,
) -> ResolvedIdentity:
    """Verify a website token or portal assertion for this chat, then link it.

    ``IdentityError`` passes through; nothing is written on failure.
    """
    claims = verify_identity_token(
        token,
        website_secret=website_secret(config, settings),
        portal_key=portal_identity_key(settings.secret_key),
        audience=config.public_key,
        visitor_id=visitor_id,
        now=_epoch(now),
    )
    resolved = await resolve_identity(db, claims=claims, values=values)
    logger.info(
        "webchat_identity_resolved", source=resolved.source, known_customer=resolved.known_customer
    )
    return resolved


async def identify_session(
    db: AsyncSession,
    sysconfig: SysConfig,
    *,
    chat: TiqoraChatSession,
    identity: ResolvedIdentity,
    now: datetime,
) -> bool:
    """Attach a verified identity to a running chat; ``True`` when something changed.

    Refusals (before any write): ``not_found``, ``session_closed`` (not live),
    ``identity_conflict`` (the chat already belongs to another verified identity). The
    same identity again is a no-op. The ticket customer is only replaced while it still is
    the anonymous / typed one (it equals the chat's ``customer_user_login`` from before).
    """
    del sysconfig  # kept for the service signature; the customer write needs no SysConfig
    locked = await lock_session(db, chat.id)  # R-LOCK-ORDER step 2
    if locked.status not in LIVE_STATUSES:
        raise ChatError("session_closed", 409)
    if locked.identity_source is not None:
        if locked.customer_user_login == identity.customer_user_login:
            return False
        raise ChatError("identity_conflict", 409)

    ticket = (
        await db.execute(
            text("SELECT customer_id, customer_user_id FROM ticket WHERE id = :t"),
            {"t": int(locked.ticket_id)},
        )
    ).first()
    if ticket is not None:
        current_cid = str(ticket[0]) if ticket[0] else None
        current_user = str(ticket[1]) if ticket[1] else None
        untouched = current_user == locked.customer_user_login
        changed = (current_cid, current_user) != (
            identity.customer_id,
            identity.customer_user_login,
        )
        if untouched and changed:
            await set_customer(
                db,
                ticket_id=int(locked.ticket_id),
                customer_id=identity.customer_id,
                customer_user_id=identity.customer_user_login,
                user_id=SYSTEM_USER_ID,
            )

    locked.identity_source = identity.source
    locked.customer_user_login = identity.customer_user_login
    # Unconditional: a typed prechat address must never stay behind a set identity_source,
    # or ticket_chat_info would report it as verified (R-NOTIFY b).
    locked.visitor_email = identity.email
    # Also unconditional: a typed prechat name must not show as the verified visitor's name.
    locked.visitor_name = identity.name[:200] if identity.name else None
    await db.flush([locked])
    queue_wakeup(db, locked.id)
    return True


def issue_portal_assertion(
    customer: AuthenticatedCustomer,
    *,
    public_key: str,
    visitor_id: str,
    settings: Settings,
    now: datetime,
) -> str:
    """Short-lived (10 min) assertion that the logged-in portal customer is this visitor."""
    iat = _epoch(now)
    claims: dict[str, object] = {
        "src": "portal",
        "sub": customer.login,
        "aud": public_key,
        "vid": visitor_id,
        "iat": iat,
        "exp": iat + PORTAL_ASSERTION_SECONDS,
    }
    if customer.email:
        claims["email"] = customer.email
    full = _full_name(customer.first_name, customer.last_name)
    if full:
        claims["name"] = full
    return sign_identity_token(claims, portal_identity_key(settings.secret_key), kid=PORTAL_KID)
