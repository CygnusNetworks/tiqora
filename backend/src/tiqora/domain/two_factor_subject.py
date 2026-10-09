"""Who a second factor belongs to: an agent or a portal customer.

``TOTPService`` and ``WebAuthnService`` run unchanged for both. The subject
selects the ORM models (agent ``users.id`` and ``customer_user.id`` are
different id spaces) and keeps every Redis key and WebAuthn user handle of the
two populations apart, so customer 5 can never collide with agent 5. The agent
subject reproduces the historical key strings and user handle byte for byte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

from tiqora.db.tiqora.models import (
    TiqoraCustomerPasskey,
    TiqoraCustomerTotp,
    TiqoraUserPasskey,
    TiqoraUserTotp,
)


@dataclass(frozen=True, slots=True)
class TwoFactorSubject:
    kind: Literal["agent", "customer"]
    totp_model: Any  # TiqoraUserTotp | TiqoraCustomerTotp (both expose .user_id)
    passkey_model: Any  # TiqoraUserPasskey | TiqoraCustomerPasskey
    redis_ns: str  # "" (agent: existing keys unchanged) | "customer:"
    user_handle_prefix: bytes  # b"" (agent) | b"tiqora-customer:"


AGENT_2FA: Final = TwoFactorSubject(
    kind="agent",
    totp_model=TiqoraUserTotp,
    passkey_model=TiqoraUserPasskey,
    redis_ns="",
    user_handle_prefix=b"",
)
CUSTOMER_2FA: Final = TwoFactorSubject(
    kind="customer",
    totp_model=TiqoraCustomerTotp,
    passkey_model=TiqoraCustomerPasskey,
    redis_ns="customer:",
    user_handle_prefix=b"tiqora-customer:",
)


def webauthn_user_handle(subject: TwoFactorSubject, user_id: int) -> bytes:
    return subject.user_handle_prefix + user_id.to_bytes(8, "big")
