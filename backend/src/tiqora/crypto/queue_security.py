"""Per-queue email security defaults: sign by default, encryption off / auto / required.

Znuny only knows the queue's ``default_sign_key``; Tiqora adds two settings per
queue (``tiqora_settings`` key ``queue_security.<queue_id>``):

* ``sign_default`` — sign with the queue's sign key unless the sender opts out
* ``encrypt`` — ``off`` (never offered), ``auto`` (encrypt when every
  recipient has a usable key) or ``required`` (no key for a recipient → the
  mail is not sent)

The same :func:`decide` drives the compose preselection (which modes the
composer offers, what is selected) and every mail sent without an explicit
choice: AI replies, auto-responses, process/MCP sends and API calls that omit
``email_security``. Nothing is offered or applied unless a backend is enabled
and usable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from sqlalchemy import select, text

from tiqora.db.tiqora.models import TiqoraSettings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tiqora.crypto.compose import CryptoOptionsOut, EmailSecurityIn

EncryptMode = Literal["off", "auto", "required"]
Mode = Literal["none", "sign", "encrypt", "sign_encrypt"]
ENCRYPT_MODES: tuple[EncryptMode, ...] = ("off", "auto", "required")
KEY_PREFIX = "queue_security."


@dataclass(frozen=True)
class QueueSecurityPolicy:
    sign_default: bool = True
    encrypt: EncryptMode = "off"


@dataclass(frozen=True)
class SecurityDecision:
    #: modes the composer offers, in display order (empty: hide the control)
    modes: list[Mode] = field(default_factory=list)
    #: what is sent when nobody chooses (and what the composer preselects)
    default: EmailSecurityIn | None = None
    #: set when ``encrypt == "required"`` cannot be met — nothing may be sent
    blocked: str | None = None


class QueueSecurityRequiredError(Exception):
    """The queue requires encryption and it cannot (or was not asked to) happen."""


# ------------------------------------------------------------------ storage


def _key(queue_id: int) -> str:
    return f"{KEY_PREFIX}{int(queue_id)}"


def _parse(raw: str | None) -> QueueSecurityPolicy:
    if not raw:
        return QueueSecurityPolicy()
    try:
        data = json.loads(raw)
    except ValueError:
        return QueueSecurityPolicy()
    if not isinstance(data, dict):
        return QueueSecurityPolicy()
    encrypt = data.get("encrypt")
    return QueueSecurityPolicy(
        sign_default=bool(data.get("sign_default", True)),
        encrypt=cast(EncryptMode, encrypt) if encrypt in ENCRYPT_MODES else "off",
    )


async def load_queue_policy(session: AsyncSession, queue_id: int) -> QueueSecurityPolicy:
    try:
        row = (
            await session.execute(
                select(TiqoraSettings.value).where(TiqoraSettings.key == _key(queue_id))
            )
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 — tiqora_settings absent (fresh test DB)
        return QueueSecurityPolicy()
    return _parse(row)


async def load_queue_policies(session: AsyncSession) -> dict[int, QueueSecurityPolicy]:
    rows = (
        await session.execute(
            select(TiqoraSettings.key, TiqoraSettings.value).where(
                TiqoraSettings.key.like(KEY_PREFIX + "%")
            )
        )
    ).all()
    out: dict[int, QueueSecurityPolicy] = {}
    for key, value in rows:
        try:
            out[int(str(key)[len(KEY_PREFIX) :])] = _parse(value)
        except ValueError:
            continue
    return out


async def save_queue_policy(
    session: AsyncSession, queue_id: int, policy: QueueSecurityPolicy
) -> None:
    if policy.encrypt not in ENCRYPT_MODES:
        raise ValueError(f"encrypt must be one of {', '.join(ENCRYPT_MODES)}")
    value = json.dumps({"sign_default": policy.sign_default, "encrypt": policy.encrypt})
    row = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == _key(queue_id)))
    ).scalar_one_or_none()
    if row is None:
        session.add(TiqoraSettings(key=_key(queue_id), value=value))
    else:
        row.value = value


async def delete_queue_policy(session: AsyncSession, queue_id: int) -> None:
    row = (
        await session.execute(select(TiqoraSettings).where(TiqoraSettings.key == _key(queue_id)))
    ).scalar_one_or_none()
    if row is not None:
        await session.delete(row)


# ------------------------------------------------------------------ decision


def decide(options: CryptoOptionsOut, policy: QueueSecurityPolicy) -> SecurityDecision:
    """Offered modes and the default for one mail, from the compose options.

    ``options.default`` is the queue's usable sign key (see
    :func:`tiqora.crypto.compose.crypto_options_sync`).
    """
    from tiqora.crypto.compose import EmailSecurityIn

    available = [b for b in options.backends if b.available]
    if not options.enabled or not available:
        return SecurityDecision()
    queue_sign = options.default
    if queue_sign is not None and not any(b.backend == queue_sign.backend for b in available):
        queue_sign = None
    encrypt = policy.encrypt

    modes: list[Mode] = []
    if encrypt != "required":
        modes.append("none")
        if queue_sign is not None:
            modes.append("sign")
    if encrypt != "off":
        modes.append("encrypt")
        if queue_sign is not None:
            modes.append("sign_encrypt")

    # Encryption backend: the sign key's backend first, then any other that
    # has a usable key for every recipient.
    order = sorted(
        available, key=lambda b: 0 if queue_sign and b.backend == queue_sign.backend else 1
    )
    enc = next((b for b in order if b.can_encrypt), None)

    blocked: str | None = None
    want_encrypt = encrypt == "required" or (encrypt == "auto" and enc is not None)
    if encrypt == "required" and enc is None:
        first = order[0]
        lacking = [r.address for r in first.recipients if r.status != "ok"]
        blocked = (
            "queue requires encryption, but no usable key for: " + ", ".join(lacking)
            if lacking
            else "queue requires encryption, but the mail has no recipients"
        )
    enc_backend = enc.backend if enc is not None else order[0].backend
    sign_now = (
        queue_sign is not None
        and policy.sign_default
        and (not want_encrypt or queue_sign.backend == enc_backend)
    )

    default: EmailSecurityIn | None = None
    if want_encrypt:
        same = queue_sign is not None and queue_sign.backend == enc_backend
        default = EmailSecurityIn(
            backend=enc_backend,
            method=queue_sign.method if same and queue_sign is not None else "detached",
            sign_key=queue_sign.sign_key if sign_now and queue_sign is not None else None,
            encrypt=True,
        )
    elif sign_now:
        default = queue_sign
    return SecurityDecision(modes=modes, default=default, blocked=blocked)


async def queue_default_sign_key(session: AsyncSession, queue_id: int) -> str | None:
    row = (
        await session.execute(
            text("SELECT default_sign_key FROM queue WHERE id = :qid"), {"qid": queue_id}
        )
    ).first()
    return str(row[0]) if row is not None and row[0] else None


async def decide_for_mail(
    session: AsyncSession,
    *,
    queue_id: int,
    from_address: str | None,
    recipients: list[str],
) -> tuple[CryptoOptionsOut, QueueSecurityPolicy, SecurityDecision]:
    """Load config, queue sign key and policy, then :func:`decide`."""
    from tiqora.crypto.compose import crypto_options
    from tiqora.crypto.config import load_crypto_config

    config = await load_crypto_config(session)
    options = await crypto_options(
        config,
        from_address=from_address,
        recipients=recipients,
        default_sign_key=await queue_default_sign_key(session, queue_id),
    )
    policy = await load_queue_policy(session, queue_id)
    return options, policy, decide(options, policy)


async def resolve_mail_security(
    session: AsyncSession,
    *,
    queue_id: int,
    from_address: str | None,
    recipients: list[str],
    requested: EmailSecurityIn | None,
    explicit: bool,
) -> EmailSecurityIn | None:
    """The security to apply to one outgoing mail.

    *explicit*: the caller chose (``requested`` may be ``None`` = plain) — it
    wins unless the queue requires encryption and the choice does not encrypt.
    Otherwise the queue default applies. Raises
    :class:`QueueSecurityRequiredError` when encryption is required but not
    possible / not chosen.
    """
    from tiqora.crypto.config import load_crypto_config

    config = await load_crypto_config(session)
    if not (config.pgp.enabled or config.smime.enabled):
        return requested if explicit else None
    policy = await load_queue_policy(session, queue_id)
    if explicit and policy.encrypt != "required":
        return requested
    if explicit and requested is not None and requested.encrypt:
        return requested
    _options, _policy, decision = await decide_for_mail(
        session, queue_id=queue_id, from_address=from_address, recipients=recipients
    )
    if not decision.modes:  # no backend usable: nothing to enforce
        return requested if explicit else None
    if decision.blocked:
        raise QueueSecurityRequiredError(decision.blocked)
    if explicit and policy.encrypt == "required":
        raise QueueSecurityRequiredError("queue requires encryption for every mail")
    return decision.default


__all__ = [
    "ENCRYPT_MODES",
    "EncryptMode",
    "Mode",
    "QueueSecurityPolicy",
    "QueueSecurityRequiredError",
    "SecurityDecision",
    "decide",
    "decide_for_mail",
    "delete_queue_policy",
    "load_queue_policies",
    "load_queue_policy",
    "queue_default_sign_key",
    "resolve_mail_security",
    "save_queue_policy",
]
