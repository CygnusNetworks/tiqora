"""Shared identity types and constants (wave 1)."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Final, Literal

ProviderKind = Literal["oidc", "saml", "ldap"]
Audience = Literal["agent", "customer", "both"]
UserKind = Literal["agent", "customer"]
ProviderSource = Literal["db", "env"]
SyncMode = Literal["managed", "authoritative"]
MatchBy = Literal["login", "email"]
GroupPermission = Literal["ro", "move_into", "create", "note", "owner", "priority", "rw"]

SSO_KINDS: Final = frozenset({"oidc", "saml"})
SCIM_KINDS: Final = SSO_KINDS  # oidc, saml: LDAP and env rows never get SCIM/step-up/lifetime
SESSION_IDLE_MIN: Final = 300
SESSION_ABSOLUTE_MIN: Final = 900
SESSION_FLOOR_SECONDS: Final = 60  # never issue a session shorter than this
SLUG_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
RESERVED_SLUGS: Final = frozenset({"env-oidc", "env-ldap", "env-customer-ldap", "logout", "login"})
ENV_OIDC_SLUG: Final = "env-oidc"
ENV_LDAP_SLUG: Final = "env-ldap"
ENV_CUSTOMER_LDAP_SLUG: Final = "env-customer-ldap"
# change_by for sync writes (Znuny AuthSync uses ChangeUserID => 1); role/group
# sync never touches this user's assignments.
SYSTEM_USER_ID: Final = 1


def audience_includes(audience: Audience, user_kind: UserKind) -> bool:
    """True when a provider with ``audience`` serves ``user_kind`` ("both" serves both)."""
    return audience == "both" or audience == user_kind


def subject_hash(subject: str) -> str:
    """sha256 hex of the exact (case-sensitive) subject."""
    return hashlib.sha256(subject.encode()).hexdigest()


def utc_now() -> datetime:
    """Naive UTC timestamp, as written to ``tiqora_identity_*`` columns."""
    return datetime.now(UTC).replace(tzinfo=None)
