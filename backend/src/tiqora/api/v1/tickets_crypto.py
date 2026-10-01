"""Ticket-side PGP / S-MIME endpoints: compose options and attached public keys.

``GET /tickets[/{id}]/crypto-options`` feeds the compose "Sicherheit" control
(reply, forward, new email ticket): the sender's sign keys with the queue's
``default_sign_key`` preselected, every recipient's encryption keys and their
status, and warnings. See :mod:`tiqora.crypto.compose`.

``.../attachments/{id}/pgp-key`` parses a public key a sender attached (key
card in the agent article view) and imports it on request.

Mounted under ``/tickets`` **before** the main tickets router so the id-less
route is not captured by ``/tickets/{ticket_id}``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.api.v1.customer_keys import CUSTOMER_ADMIN_GROUPS, raise_crypto
from tiqora.channels.email.outbound_reply import queue_outbound_meta
from tiqora.crypto import CryptoError
from tiqora.crypto import keystore as ks
from tiqora.crypto.attachment_kind import SNIFF_MAX_BYTES
from tiqora.crypto.compose import CryptoOptionsOut, crypto_options, split_addresses
from tiqora.crypto.config import CryptoConfig, load_crypto_config
from tiqora.crypto.pgp import PgpEngine, PgpKeyInfo, scan_armored_keys
from tiqora.crypto.queue_security import decide, load_queue_policy
from tiqora.domain.ticket_service import TicketAccessDenied, TicketNotFound, TicketService
from tiqora.domain.ticket_write_service import InvalidInput
from tiqora.permissions.engine import PermissionEngine

router = APIRouter(prefix="/tickets", tags=["tickets"])


async def _options(
    session: AsyncSession, queue_id: int, to: str | None, cc: str | None, bcc: str | None
) -> CryptoOptionsOut:
    try:
        from_line, _name, _sig, _ct = await queue_outbound_meta(session, queue_id)
    except InvalidInput as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    default_sign_key = (
        await session.execute(
            text("SELECT default_sign_key FROM queue WHERE id = :qid"), {"qid": queue_id}
        )
    ).scalar_one_or_none()
    config = await load_crypto_config(session)
    if not (config.pgp.enabled or config.smime.enabled):
        return CryptoOptionsOut(enabled=False, from_address=None)
    options = await crypto_options(
        config,
        from_address=from_line,
        recipients=split_addresses(to, cc, bcc),
        default_sign_key=str(default_sign_key) if default_sign_key else None,
    )
    policy = await load_queue_policy(session, queue_id)
    decision = decide(options, policy)
    options.queue_sign = options.default
    options.default = decision.default
    options.modes = list(decision.modes)
    options.sign_default = policy.sign_default
    options.encrypt_policy = policy.encrypt
    options.blocked = decision.blocked
    return options


@router.get("/crypto-options", response_model=CryptoOptionsOut)
async def new_ticket_crypto_options(
    user: CurrentUser,
    session: DbSession,
    queue_id: int = Query(...),
    to: str | None = Query(None),
    cc: str | None = Query(None),
    bcc: str | None = Query(None),
) -> CryptoOptionsOut:
    """Crypto compose options for a new email ticket in *queue_id* (``create`` right)."""
    if not await PermissionEngine(session).check(user.id, queue_id, "create"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return await _options(session, queue_id, to, cc, bcc)


@router.get("/{ticket_id}/crypto-options", response_model=CryptoOptionsOut)
async def ticket_crypto_options(
    ticket_id: int,
    user: CurrentUser,
    session: DbSession,
    to: str | None = Query(None),
    cc: str | None = Query(None),
    bcc: str | None = Query(None),
) -> CryptoOptionsOut:
    """Crypto compose options for a reply/forward on *ticket_id* (sender = its queue)."""
    try:
        ticket = await TicketService(session)._assert_ticket_ro(user.id, ticket_id)  # noqa: SLF001
    except TicketNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    except TicketAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc
    return await _options(session, int(ticket.queue_id), to, cc, bcc)


# ------------------------------------------------- public key in an attachment

_PUBLIC_BLOCK = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
_PRIVATE_BLOCK = "-----BEGIN PGP PRIVATE KEY BLOCK-----"


class AttachmentPgpKeyOut(BaseModel):
    """One public key found in an attachment (parsed, not imported)."""

    fingerprint: str
    key_id: str
    uids: list[str]
    emails: list[str]
    algorithm: str
    bits: int | None = None
    created: datetime | None = None
    expires: datetime | None = None
    status: str  # good | expired | revoked
    #: Already in the Tiqora/Znuny keyring (same fingerprint).
    in_keyring: bool = False


class AttachmentPgpKeysOut(BaseModel):
    #: gpg could read the attachment. False: show the file label only.
    available: bool
    keys: list[AttachmentPgpKeyOut] = Field(default_factory=list)
    #: PGP enabled, keyring usable and the caller may manage keys
    #: (``rw`` in admin or users — same as the customer key module).
    can_import: bool = False
    problem: str | None = None


def _armored_text(content: bytes) -> str:
    if len(content) > SNIFF_MAX_BYTES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Attachment too large for a key"
        )
    return content.decode("utf-8", errors="replace")


async def _keyring_fingerprints(config: CryptoConfig) -> set[str] | None:
    """Fingerprints in the configured keyring; None when there is none to use."""
    if not config.pgp.homedir:
        return None
    try:
        engine = PgpEngine.from_config(config.pgp)
        fps = await asyncio.to_thread(engine.list_key_fingerprints)
    except CryptoError:
        return None
    return {fp.upper() for fp in fps}


def _key_out(key: PgpKeyInfo, keyring: set[str] | None) -> AttachmentPgpKeyOut:
    return AttachmentPgpKeyOut(
        fingerprint=key.fingerprint,
        key_id=key.key_id,
        uids=key.uids,
        emails=key.emails,
        algorithm=key.algorithm,
        bits=key.bits,
        created=key.created,
        expires=key.expires,
        status=key.status,
        in_keyring=keyring is not None and key.fingerprint.upper() in keyring,
    )


async def _attachment_bytes(
    session: AsyncSession, user_id: int, ticket_id: int, article_id: int, attachment_id: int
) -> bytes:
    try:
        att = await TicketService(session).get_attachment(
            user_id, ticket_id, article_id, attachment_id
        )
    except TicketNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    except TicketAccessDenied as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Forbidden") from exc
    return att.content


async def _inspect_key(
    session: AsyncSession, user_id: int, text_: str, config: CryptoConfig
) -> AttachmentPgpKeysOut:
    if _PUBLIC_BLOCK not in text_ or _PRIVATE_BLOCK in text_:
        return AttachmentPgpKeysOut(available=False, problem="No PGP public key block")
    try:
        keys = await asyncio.to_thread(scan_armored_keys, text_, gpg_bin=config.pgp.gpg_bin)
    except CryptoError as exc:
        return AttachmentPgpKeysOut(available=False, problem=str(exc))
    if not keys:
        return AttachmentPgpKeysOut(available=False, problem="gpg found no key in the block")
    keyring = await _keyring_fingerprints(config)
    can_import = (
        config.pgp.enabled
        and keyring is not None
        and await PermissionEngine(session).has_rw_in_any_group(user_id, CUSTOMER_ADMIN_GROUPS)
    )
    return AttachmentPgpKeysOut(
        available=True,
        keys=[_key_out(k, keyring) for k in keys],
        can_import=can_import,
    )


@router.get(
    "/{ticket_id}/articles/{article_id}/attachments/{attachment_id}/pgp-key",
    response_model=AttachmentPgpKeysOut,
)
async def attachment_pgp_key(
    ticket_id: int,
    article_id: int,
    attachment_id: int,
    user: CurrentUser,
    session: DbSession,
) -> AttachmentPgpKeysOut:
    """Parse the PGP public key(s) in an attachment (same access as downloading it).

    Read-only: the key is scanned in a throwaway gpg home, never imported.
    ``in_keyring`` tells whether the shared keyring already has it.
    """
    content = await _attachment_bytes(session, user.id, ticket_id, article_id, attachment_id)
    config = await load_crypto_config(session)
    return await _inspect_key(session, user.id, _armored_text(content), config)


@router.post(
    "/{ticket_id}/articles/{article_id}/attachments/{attachment_id}/pgp-key/import",
    response_model=AttachmentPgpKeysOut,
)
async def import_attachment_pgp_key(
    ticket_id: int,
    article_id: int,
    attachment_id: int,
    user: CurrentUser,
    session: DbSession,
) -> AttachmentPgpKeysOut:
    """Import the attached public key into the keyring (``rw`` in admin or users).

    Same permission as the agent customer-key module; writes the usual
    ``tiqora_crypto_key`` audit row. Private key blocks are refused.
    """
    content = await _attachment_bytes(session, user.id, ticket_id, article_id, attachment_id)
    if not await PermissionEngine(session).has_rw_in_any_group(user.id, CUSTOMER_ADMIN_GROUPS):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Importing keys needs rw in the admin or users group",
        )
    config = await load_crypto_config(session)
    if not config.pgp.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="PGP is not enabled")
    text_ = _armored_text(content)
    if _PUBLIC_BLOCK not in text_ or _PRIVATE_BLOCK in text_:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="The attachment is not a PGP public key"
        )
    try:
        engine = PgpEngine.from_config(config.pgp)
        scanned = await asyncio.to_thread(scan_armored_keys, text_, gpg_bin=config.pgp.gpg_bin)
        emails = sorted({e for k in scanned for e in k.emails})
        await ks.import_pgp_key(
            session,
            engine,
            text_,
            email=", ".join(emails) or None,
            user_id=user.id,
        )
    except CryptoError as exc:
        await session.rollback()
        raise_crypto(exc)
    return await _inspect_key(session, user.id, text_, config)
