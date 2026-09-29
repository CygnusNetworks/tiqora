"""Signed/encrypted event notifications — port of Znuny's ``SecurityOptionsGet``.

Znuny configures email security per notification in ``notification_event_
item`` rows (``Kernel::System::Ticket::Event::NotificationEvent::Transport::
Email``, admin screen ``AdminNotificationEvent``):

====================================  =========================================
``EmailSecuritySettings``             ``1`` = "Enable email security" checked
``EmailSigningCrypting``              ``PGPSign`` | ``PGPCrypt`` |
                                      ``PGPSignCrypt`` | ``SMIMESign`` |
                                      ``SMIMECrypt`` | ``SMIMESignCrypt``
                                      (empty = none)
``EmailMissingSigningKeys``           ``Skip`` | ``Send`` (send unsigned)
``EmailMissingCryptingKeys``          ``Skip`` | ``Send`` (send unencrypted)
====================================  =========================================

:func:`notification_security_sync` reproduces ``SecurityOptionsGet``:

* nothing happens unless the checkbox is set **and** a level is chosen;
* the backend must be enabled and working, otherwise the notification is
  **not sent** (Znuny: ``No PGP support!`` → ``return``);
* sign keys = the sender's valid secret keys / private certificates (the
  ``From`` address of the notification); for customer notifications the
  queue's ``default_sign_key`` wins when it is one of them, otherwise the
  first one is used;
* encryption = the first valid key/certificate of the recipient (one
  notification = one recipient);
* a missing key follows the policy: ``Skip`` drops the notification, any
  other value (including none) sends it unsigned / unencrypted;
* PGP method: detached, or ``PGP::Method`` when ``Frontend::RichText`` is off
  (Znuny reads it only when the layout has no rich text).

Key matching uses the exact email address of a key/certificate, where Znuny
runs a gpg substring search (``support@x`` would also find
``mysupport@x``); everything else follows Znuny.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tiqora.crypto import CryptoError
from tiqora.crypto.config import CryptoConfig
from tiqora.crypto.mime_build import SecurityPlan
from tiqora.crypto.pgp import PgpEngine, PgpKeyInfo
from tiqora.crypto.smime_store import SmimeEntry, SmimeStore

#: ``EmailSigningCrypting`` values as Znuny's admin screen offers them.
SIGNING_CRYPTING_VALUES: tuple[str, ...] = (
    "PGPSign",
    "PGPCrypt",
    "PGPSignCrypt",
    "SMIMESign",
    "SMIMECrypt",
    "SMIMESignCrypt",
)
#: ``EmailMissingSigningKeys`` / ``EmailMissingCryptingKeys`` values.
MISSING_KEY_VALUES: tuple[str, ...] = ("Skip", "Send")

ITEM_ENABLED = "EmailSecuritySettings"
ITEM_LEVEL = "EmailSigningCrypting"
ITEM_MISSING_SIGN = "EmailMissingSigningKeys"
ITEM_MISSING_CRYPT = "EmailMissingCryptingKeys"
SECURITY_ITEM_KEYS: tuple[str, ...] = (
    ITEM_ENABLED,
    ITEM_LEVEL,
    ITEM_MISSING_SIGN,
    ITEM_MISSING_CRYPT,
)


def _first(items: Mapping[str, Sequence[str]], key: str) -> str:
    values = items.get(key) or []
    return str(values[0]).strip() if values else ""


@dataclass(frozen=True)
class NotificationSecurityConfig:
    """The security items of one notification (Znuny reads ``->[0]`` of each)."""

    enabled: bool
    level: str
    on_missing_sign: str
    on_missing_crypt: str

    @classmethod
    def from_items(cls, items: Mapping[str, Sequence[str]]) -> NotificationSecurityConfig:
        return cls(
            enabled=bool(_first(items, ITEM_ENABLED)) and _first(items, ITEM_ENABLED) != "0",
            level=_first(items, ITEM_LEVEL),
            on_missing_sign=_first(items, ITEM_MISSING_SIGN),
            on_missing_crypt=_first(items, ITEM_MISSING_CRYPT),
        )

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.level)

    @property
    def backend(self) -> str | None:
        low = self.level.lower()
        if low.startswith("pgp"):
            return "pgp"
        if low.startswith("smime"):
            return "smime"
        return None

    @property
    def signs(self) -> bool:
        return "sign" in self.level.lower()

    @property
    def encrypts(self) -> bool:
        return "crypt" in self.level.lower()


def validate_security_items(items: Mapping[str, Sequence[str]]) -> list[str]:
    """Problems with the security items of an admin write (empty = fine)."""
    problems: list[str] = []
    level = _first(items, ITEM_LEVEL)
    if level and level not in SIGNING_CRYPTING_VALUES:
        problems.append(
            f"{ITEM_LEVEL} must be one of {', '.join(SIGNING_CRYPTING_VALUES)} (got {level!r})"
        )
    for key in (ITEM_MISSING_SIGN, ITEM_MISSING_CRYPT):
        value = _first(items, key)
        if value and value not in MISSING_KEY_VALUES:
            problems.append(f"{key} must be Skip or Send (got {value!r})")
    enabled = _first(items, ITEM_ENABLED)
    if enabled and enabled not in ("0", "1"):
        problems.append(f"{ITEM_ENABLED} must be 1 (got {enabled!r})")
    return problems


@dataclass
class NotificationSecurityDecision:
    """Outcome for one notification + recipient.

    ``skip`` → do not send. Otherwise ``plan`` is what to apply (``None`` =
    send plain). ``log`` holds Znuny's log lines (``(level, message)``).
    """

    skip: bool = False
    plan: SecurityPlan | None = None
    log: list[tuple[str, str]] = field(default_factory=list)


def default_sign_key_ref(default_sign_key: str | None) -> str | None:
    """Znuny's parsing of ``queue.default_sign_key`` (legacy two-part form too)."""
    if not default_sign_key:
        return None
    parts = default_sign_key.split("::")
    if "Inline" in default_sign_key or "Detached" in default_sign_key:
        return parts[2] if len(parts) > 2 else None
    return parts[1] if len(parts) > 1 else None


def _pgp_keys(
    config: CryptoConfig, sender: str, recipient: str
) -> tuple[list[PgpKeyInfo], list[PgpKeyInfo]]:
    keys = PgpEngine.from_config(config.pgp).list_keys()
    sign = [
        k
        for k in keys
        if k.has_secret and k.status == "good" and sender in {e.lower() for e in k.emails}
    ]
    encrypt = [k for k in keys if k.status == "good" and recipient in {e.lower() for e in k.emails}]
    return sign, encrypt


def _smime_keys(
    config: CryptoConfig, sender: str, recipient: str
) -> tuple[list[SmimeEntry], list[SmimeEntry]]:
    now = datetime.now(UTC)
    entries = [
        e
        for e in SmimeStore.from_config(config.smime).list_entries()
        if e.info is not None and not e.info.is_expired(now)
    ]
    sign = [e for e in entries if e.has_private and e.info and sender in e.info.emails]
    encrypt = [e for e in entries if e.info and not e.info.is_ca and recipient in e.info.emails]
    return sign, encrypt


def notification_security_sync(
    config: CryptoConfig,
    security: NotificationSecurityConfig,
    *,
    notification_name: str,
    sender_email: str,
    recipient_email: str,
    queue_default_sign_key: str | None = None,
    rich_text: bool = True,
    pgp_method: str = "Detached",
) -> NotificationSecurityDecision:
    """Port of ``Transport::Email::SecurityOptionsGet`` (blocking: runs gpg/openssl).

    *queue_default_sign_key* is only passed for customer notifications, as in
    Znuny (agent notifications have no queue).
    """
    decision = NotificationSecurityDecision()
    if not security.active:
        return decision
    backend = security.backend
    sender = sender_email.strip().lower()
    recipient = recipient_email.strip().lower()
    label = "PGP" if backend == "pgp" else "SMIME"

    if backend is None:
        # An unknown level matches neither /^PGP/ nor /^SMIME/: Znuny then
        # finds no keys and follows the missing-key policies below.
        label = ""
    backend_config = (
        config.pgp if backend == "pgp" else config.smime if backend == "smime" else None
    )
    if backend_config is not None and not backend_config.enabled:
        decision.skip = True
        decision.log.append(("error", f"No {label} support!"))
        return decision

    sign_keys: list[PgpKeyInfo] | list[SmimeEntry] = []
    encrypt_keys: list[PgpKeyInfo] | list[SmimeEntry] = []
    try:
        if backend == "pgp":
            sign_keys, encrypt_keys = _pgp_keys(config, sender, recipient)
        elif backend == "smime":
            sign_keys, encrypt_keys = _smime_keys(config, sender, recipient)
    except CryptoError as exc:
        decision.skip = True
        decision.log.append(("error", f"No {label} support! ({exc})"))
        return decision

    sign_key: PgpKeyInfo | SmimeEntry | None = None
    ref = default_sign_key_ref(queue_default_sign_key)
    if ref:
        for key in sign_keys:
            if (isinstance(key, PgpKeyInfo) and key.matches(ref)) or (
                isinstance(key, SmimeEntry) and key.filename == ref
            ):
                sign_key = key
                break
    if sign_key is None and sign_keys:
        sign_key = sign_keys[0]
    encrypt_key = encrypt_keys[0] if encrypt_keys else None

    plan_sign: str | None = None
    plan_encrypt: tuple[str, ...] = ()
    signer: str | None = None
    if security.signs:
        if sign_key is None:
            message = (
                f"Could not sign notification '{notification_name}' due to missing"
                f" {label} sign key for '{sender_email}'"
            )
            if security.on_missing_sign == "Skip":
                decision.skip = True
                decision.log.append(("notice", message + ", skipping notification distribution!"))
                return decision
            decision.log.append(("notice", message + ", sending unsigned!"))
        elif isinstance(sign_key, PgpKeyInfo):
            plan_sign = sign_key.fingerprint
            signer = sign_key.uids[0] if sign_key.uids else sign_key.fingerprint
        else:
            plan_sign = sign_key.filename
            signer = sign_key.info.email_joined if sign_key.info else sign_key.filename

    if security.encrypts:
        if encrypt_key is None:
            message = (
                f"Could not encrypt notification '{notification_name}' due to missing"
                f" {label} encryption key for '{recipient_email}'"
            )
            if security.on_missing_crypt == "Skip":
                decision.skip = True
                decision.log.append(("notice", message + ", skipping notification distribution!"))
                return decision
            decision.log.append(("notice", message + ", sending unencrypted!"))
        elif isinstance(encrypt_key, PgpKeyInfo):
            plan_encrypt = (encrypt_key.fingerprint,)
        else:
            plan_encrypt = (encrypt_key.filename,)

    if backend is None or not (plan_sign or plan_encrypt):
        return decision
    method = "detached"
    if backend == "pgp" and not rich_text and pgp_method.strip().lower() == "inline":
        method = "inline"
    decision.plan = SecurityPlan(
        backend=backend,
        method=method,
        sign_key=plan_sign,
        encrypt_keys=plan_encrypt,
        signer_label=signer,
    )
    return decision


__all__ = [
    "ITEM_ENABLED",
    "ITEM_LEVEL",
    "ITEM_MISSING_CRYPT",
    "ITEM_MISSING_SIGN",
    "MISSING_KEY_VALUES",
    "NotificationSecurityConfig",
    "NotificationSecurityDecision",
    "SECURITY_ITEM_KEYS",
    "SIGNING_CRYPTING_VALUES",
    "default_sign_key_ref",
    "notification_security_sync",
    "validate_security_items",
]
