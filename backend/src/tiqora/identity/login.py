"""Login planning and application for external identities (D7, phase-1 subset).

``plan_login`` only reads. Matching order: link (no fallback when the linked
account is gone/invalid) -> login (exact first, then a unique case-insensitive hit)
-> e-mail. JIT (step 5), profile changes (7) and role sync (8) arrive with later tasks;
until then step 5 answers ``no_match`` and 7/8 are empty.

Every lookup is by id/login/e-mail of ONE Znuny table; nothing joins ``tiqora_*``
with Znuny tables on strings (mixed collations on production MariaDB).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.user import UserPreferences, Users
from tiqora.db.tiqora.models import TiqoraUserAuthConfig
from tiqora.identity.config_models import OidcProviderConfig, SamlProviderConfig
from tiqora.identity.links import find_link, upsert_link
from tiqora.identity.mapping import AssignmentDiff, MappedProfile, map_profile
from tiqora.identity.store import ProviderRecord
from tiqora.identity.types import SSO_KINDS, UserKind


@dataclass(frozen=True, slots=True)
class SsoSessionDraft:
    """What the adapter learned for logout."""

    session_index: str | None = None  # SAML
    name_id: str | None = None
    name_id_format: str | None = None
    name_qualifier: str | None = None
    sp_name_qualifier: str | None = None
    sid: str | None = None  # OIDC
    id_token_hint: str | None = None  # OIDC, for end_session


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    provider_id: int
    subject: str  # OIDC sub, SAML NameID, LDAP subject_attr or DN
    attributes: Mapping[str, tuple[str, ...]]
    email_verified: bool | None = None  # OIDC only; None = not asserted
    sso: SsoSessionDraft | None = None


RejectCode = Literal[
    "no_subject",
    "no_match",
    "user_invalid",
    "sso_not_enabled",
    "email_unverified",
    "ambiguous_email",
    "login_taken",
    "jit_missing_fields",
    "provider_disabled",
]

REJECT_MESSAGES: Final[dict[str, str]] = {
    "no_subject": "Der Anmeldedienst hat keine eindeutige Kennung übermittelt.",
    "no_match": "Es wurde kein passendes Benutzerkonto gefunden.",
    "user_invalid": "Das verknüpfte Benutzerkonto ist deaktiviert oder gelöscht.",
    "sso_not_enabled": (
        "Für dieses Benutzerkonto ist die Anmeldung über den Anmeldedienst nicht freigeschaltet."
    ),
    "email_unverified": "Die E-Mail-Adresse wurde vom Anmeldedienst nicht als bestätigt gemeldet.",
    "ambiguous_email": "Die E-Mail-Adresse gehört zu mehreren Benutzerkonten.",
    "login_taken": "Der Anmeldename ist bereits für ein anderes Benutzerkonto vergeben.",
    "jit_missing_fields": "Für die automatische Anlage fehlen Angaben vom Anmeldedienst.",
    "provider_disabled": "Dieser Anmeldedienst ist deaktiviert.",
}


@dataclass(frozen=True, slots=True)
class LoginPlan:
    user_kind: UserKind
    decision: Literal["login", "create", "reject"]
    matched_by: Literal["link", "login", "email", "jit"] | None
    user_id: int | None
    login: str | None
    reject_code: RejectCode | None
    profile: MappedProfile
    profile_changes: Mapping[str, tuple[str | None, str]]
    assignments: AssignmentDiff
    link_exists: bool


@dataclass(frozen=True, slots=True)
class _Account:
    id: int
    login: str


def _reject(
    user_kind: UserKind, profile: MappedProfile, code: RejectCode, *, link_exists: bool = False
) -> LoginPlan:
    return LoginPlan(
        user_kind=user_kind,
        decision="reject",
        matched_by=None,
        user_id=None,
        login=None,
        reject_code=code,
        profile=profile,
        profile_changes={},
        assignments=AssignmentDiff(),
        link_exists=link_exists,
    )


async def _load_by_id(session: AsyncSession, user_kind: UserKind, user_id: int) -> _Account | None:
    """Valid account with this id, or None (missing or ``valid_id != 1``)."""
    if user_kind == "agent":
        row = (
            await session.execute(
                select(Users.id, Users.login).where(Users.id == user_id, Users.valid_id == 1)
            )
        ).first()
    else:
        row = (
            await session.execute(
                select(CustomerUser.id, CustomerUser.login).where(
                    CustomerUser.id == user_id, CustomerUser.valid_id == 1
                )
            )
        ).first()
    return _Account(id=row[0], login=row[1]) if row else None


async def _match_login(session: AsyncSession, user_kind: UserKind, value: str) -> _Account | None:
    """Exact login first, else exactly one case-insensitive hit.

    One ``lower()`` query, decided in Python: a case-insensitive collation
    (MariaDB) must not turn "exact" into "any case".
    """
    if user_kind == "agent":
        stmt = select(Users.id, Users.login).where(
            func.lower(Users.login) == value.lower(), Users.valid_id == 1
        )
    else:
        stmt = select(CustomerUser.id, CustomerUser.login).where(
            func.lower(CustomerUser.login) == value.lower(), CustomerUser.valid_id == 1
        )
    rows = [_Account(id=r[0], login=r[1]) for r in (await session.execute(stmt)).all()]
    exact = [r for r in rows if r.login == value]
    if len(exact) == 1:
        return exact[0]
    if not exact and len(rows) == 1:
        return rows[0]
    return None


def _decode_pref(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return bytes(raw).decode("utf-8", errors="replace")


async def _match_email(
    session: AsyncSession, user_kind: UserKind, email: str
) -> tuple[_Account | None, bool]:
    """Return (account, ambiguous). Compared lowercase in Python (collation/dialect safe)."""
    wanted = email.strip().lower()
    if user_kind == "customer":
        rows = (
            await session.execute(
                select(CustomerUser.id, CustomerUser.login).where(
                    func.lower(CustomerUser.email) == wanted, CustomerUser.valid_id == 1
                )
            )
        ).all()
        accounts = {r[0]: _Account(id=r[0], login=r[1]) for r in rows}
    else:
        # Int join only (user_preferences.user_id = users.id), no string columns involved.
        pref_rows = (
            await session.execute(
                select(Users.id, Users.login, UserPreferences.preferences_value)
                .join(UserPreferences, UserPreferences.user_id == Users.id)
                .where(UserPreferences.preferences_key == "UserEmail", Users.valid_id == 1)
            )
        ).all()
        accounts = {
            r[0]: _Account(id=r[0], login=r[1])
            for r in pref_rows
            if _decode_pref(r[2]).strip().lower() == wanted
        }
    if len(accounts) > 1:
        return None, True
    return (next(iter(accounts.values())) if accounts else None), False


async def _sso_eligible(session: AsyncSession, user_id: int) -> bool:
    flag = (
        await session.execute(
            select(TiqoraUserAuthConfig.sso_eligible).where(TiqoraUserAuthConfig.user_id == user_id)
        )
    ).scalar_one_or_none()
    return bool(flag)


async def plan_login(
    session: AsyncSession,
    provider: ProviderRecord,
    identity: ExternalIdentity,
    *,
    user_kind: UserKind,
    settings: Settings,
) -> LoginPlan:
    """Decide what a login would do. Reads only."""
    profile = map_profile(identity.attributes, provider.attribute_map)
    if not identity.subject:
        return _reject(user_kind, profile, "no_subject")

    account: _Account | None = None
    matched_by: Literal["link", "login", "email"]
    link = await find_link(
        session, provider_id=provider.id, user_kind=user_kind, subject=identity.subject
    )
    if link is not None:
        account = await _load_by_id(session, user_kind, link.user_id)
        if account is None:
            return _reject(user_kind, profile, "user_invalid", link_exists=True)
        matched_by = "link"
    elif provider.match_by == "login":
        matched_by = "login"
        if profile.login:
            account = await _match_login(session, user_kind, profile.login)
    else:
        matched_by = "email"
        cfg = provider.config
        if (
            provider.kind == "oidc"
            and isinstance(cfg, OidcProviderConfig)
            and cfg.email_must_be_verified
            and identity.email_verified is not True
        ):
            return _reject(user_kind, profile, "email_unverified")
        if profile.email:
            account, ambiguous = await _match_email(session, user_kind, profile.email)
            if ambiguous:
                return _reject(user_kind, profile, "ambiguous_email")

    if account is None:
        # Step 5 (JIT) arrives with Task 17.
        return _reject(user_kind, profile, "no_match")

    if (
        user_kind == "agent"
        and provider.kind in SSO_KINDS
        and provider.require_sso_opt_in
        and not await _sso_eligible(session, account.id)
    ):
        return _reject(user_kind, profile, "sso_not_enabled", link_exists=link is not None)

    return LoginPlan(
        user_kind=user_kind,
        decision="login",
        matched_by=matched_by,
        user_id=account.id,
        login=account.login,
        reject_code=None,
        profile=profile,
        profile_changes={},
        assignments=AssignmentDiff(),
        link_exists=link is not None,
    )


def _stores_links(provider: ProviderRecord) -> bool:
    """A SAML transient NameID changes on every login: it must never become a link."""
    cfg = provider.config
    return not (
        provider.kind == "saml"
        and isinstance(cfg, SamlProviderConfig)
        and cfg.name_id_format == "transient"
    )


async def apply_login(
    session: AsyncSession,
    provider: ProviderRecord,
    identity: ExternalIdentity,
    plan: LoginPlan,
    *,
    settings: Settings,
) -> tuple[int, str]:
    """Persist the link for a ``login`` plan and commit. Returns (user_id, login)."""
    if plan.decision == "reject":
        raise ValueError(f"cannot apply a rejected login plan ({plan.reject_code})")
    if plan.decision != "login" or plan.user_id is None or plan.login is None:
        raise ValueError(f"unsupported login plan decision: {plan.decision}")
    if _stores_links(provider):
        await upsert_link(
            session,
            provider_id=provider.id,
            user_kind=plan.user_kind,
            subject=identity.subject,
            user_id=plan.user_id,
            user_login=plan.login,
        )
    await session.commit()
    return plan.user_id, plan.login


def plan_to_dict(plan: LoginPlan) -> dict[str, Any]:
    """JSON-able summary (ids only; no secrets) for Test-tab storage."""
    a = plan.assignments
    return {
        "user_kind": plan.user_kind,
        "decision": plan.decision,
        "matched_by": plan.matched_by,
        "user_id": plan.user_id,
        "login": plan.login,
        "reject_code": plan.reject_code,
        "reject_message": REJECT_MESSAGES.get(plan.reject_code) if plan.reject_code else None,
        "profile": {
            "login": plan.profile.login,
            "email": plan.profile.email,
            "first_name": plan.profile.first_name,
            "last_name": plan.profile.last_name,
            "customer_id": plan.profile.customer_id,
            "mobile": plan.profile.mobile,
        },
        "profile_changes": {k: [old, new] for k, (old, new) in plan.profile_changes.items()},
        "assignments": {
            "add_roles": sorted(a.add_roles),
            "remove_roles": sorted(a.remove_roles),
            "add_groups": sorted([g, p] for g, p in a.add_groups),
            "remove_groups": sorted([g, p] for g, p in a.remove_groups),
        },
        "link_exists": plan.link_exists,
    }
