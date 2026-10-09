"""Login planning and application for external identities (D7).

``plan_login`` only reads. Matching order: link (no fallback when the linked
account is gone/invalid) -> login (exact first, then a unique case-insensitive hit)
-> e-mail -> just-in-time creation (step 5, ``jit_enabled``). Profile changes (7) are
planned for every login; role/group sync (8) is planned for agents only (managed
assignments are agent-only) and never touches the system user (id 1).

Every lookup is by id/login/e-mail of ONE Znuny table; nothing joins ``tiqora_*``
with Znuny tables on strings (mixed collations on production MariaDB).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.config import Settings
from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.user import UserPreferences, Users
from tiqora.db.tiqora.models import TiqoraUserAuthConfig
from tiqora.identity.config_models import OidcProviderConfig, SamlProviderConfig
from tiqora.identity.links import LinkConflictError, find_link, upsert_link
from tiqora.identity.mapping import (
    AssignmentDiff,
    AssignmentState,
    MappedProfile,
    attribute_present,
    diff_assignments,
    evaluate_rules,
    map_profile,
)
from tiqora.identity.store import ProviderRecord
from tiqora.identity.types import SSO_KINDS, SYSTEM_USER_ID, UserKind, audience_includes

log = logging.getLogger(__name__)
# NOTE: ``tiqora.identity.provisioning`` / ``tiqora.domain.user_provisioning`` are imported
# inside the functions that need them: ``user_provisioning`` pulls in ``tiqora.api.v1``,
# which imports ``identity.http`` -> this module (circular at import time).


@dataclass(frozen=True, slots=True)
class SsoSessionDraft:
    """What the adapter learned for logout."""

    session_index: str | None = None  # SAML
    name_id: str | None = None
    name_id_format: str | None = None
    name_qualifier: str | None = None
    sp_name_qualifier: str | None = None
    sid: str | None = None  # OIDC
    id_token_hint: str | None = field(default=None, repr=False)  # OIDC, for end_session


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
    "email_invalid",
    "email_in_use",
    "jit_domain_not_allowed",
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
    "email_invalid": "Die E-Mail-Adresse vom Anmeldedienst ist für ein neues Konto nicht zulässig.",
    "email_in_use": "Die E-Mail-Adresse wird bereits von einem anderen Kundenkonto verwendet.",
    "jit_domain_not_allowed": (
        "Die E-Mail-Domain ist für die automatische Anlage von Konten nicht freigegeben."
    ),
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
    reject_detail: str | None = None  # replaces the generic reject message (e.g. missing fields)


class LoginRejected(ValueError):  # noqa: N818 - a reject, not a failure
    """``apply_login`` cannot proceed; ``reject_code`` is the matching ``RejectCode``."""

    def __init__(self, reject_code: RejectCode) -> None:
        super().__init__(reject_code)
        self.reject_code: RejectCode = reject_code


@dataclass(frozen=True, slots=True)
class _Account:
    id: int
    login: str


def _reject(
    user_kind: UserKind,
    profile: MappedProfile,
    code: RejectCode,
    *,
    link_exists: bool = False,
    detail: str | None = None,
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
        reject_detail=detail,
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


def _fold(value: str) -> str:
    """Python-side comparison key: strip + casefold (no accent folding, unlike *_ci collations)."""
    return value.strip().casefold()


async def _match_login(session: AsyncSession, user_kind: UserKind, value: str) -> _Account | None:
    """Exact login first, else exactly one case-insensitive hit.

    The SQL ``lower()`` query is only a prefilter: a case/accent-insensitive PAD SPACE
    collation (prod MariaDB) would treat 'jose' = 'josé' and ignore trailing spaces.
    Exact / unique / ambiguous is decided in Python after re-checking every row.
    """
    value = value.strip()
    if not value:
        return None
    if user_kind == "agent":
        stmt = select(Users.id, Users.login).where(
            func.lower(func.rtrim(Users.login)) == value.lower(), Users.valid_id == 1
        )
    else:
        stmt = select(CustomerUser.id, CustomerUser.login).where(
            func.lower(func.rtrim(CustomerUser.login)) == value.lower(), CustomerUser.valid_id == 1
        )
    rows = [_Account(id=r[0], login=r[1]) for r in (await session.execute(stmt)).all()]
    exact = [r for r in rows if r.login == value]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None
    folded = [r for r in rows if _fold(r.login) == _fold(value)]
    return folded[0] if len(folded) == 1 else None


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
    wanted = _fold(email)
    if user_kind == "customer":
        rows = (
            await session.execute(
                select(CustomerUser.id, CustomerUser.login, CustomerUser.email).where(
                    func.lower(func.rtrim(CustomerUser.email)) == email.strip().lower(),
                    CustomerUser.valid_id == 1,
                )
            )
        ).all()
        accounts = {r[0]: _Account(id=r[0], login=r[1]) for r in rows if _fold(r[2]) == wanted}
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
            if _fold(_decode_pref(r[2])) == wanted
        }
    if len(accounts) > 1:
        return None, True
    return (next(iter(accounts.values())) if accounts else None), False


def _email_unverified(provider: ProviderRecord, identity: ExternalIdentity) -> bool:
    """OIDC provider demands a verified e-mail and the IdP did not assert one."""
    cfg = provider.config
    return (
        provider.kind == "oidc"
        and isinstance(cfg, OidcProviderConfig)
        and cfg.email_must_be_verified
        and identity.email_verified is not True
    )


async def _login_in_use(session: AsyncSession, user_kind: UserKind, login: str) -> bool:
    """Any account (valid or not) already owns this login, as ``UserAdd`` checks it."""
    column = Users.login if user_kind == "agent" else CustomerUser.login
    stmt = select(column).where(func.lower(column) == login.lower()).limit(1)
    return (await session.execute(stmt)).first() is not None


async def _plan_jit(
    session: AsyncSession,
    provider: ProviderRecord,
    identity: ExternalIdentity,
    profile: MappedProfile,
    user_kind: UserKind,
) -> LoginPlan:
    """Step 5: nothing matched. Create the account when the provider allows it. Reads only."""
    from tiqora.domain.user_provisioning import check_email, customer_email_in_use
    from tiqora.identity import provisioning

    if not provider.jit_enabled:
        return _reject(user_kind, profile, "no_match")
    if _email_unverified(provider, identity):
        return _reject(user_kind, profile, "email_unverified")
    account, missing = provisioning.resolve_new_account(provider, profile, user_kind)
    if account is None:
        return _reject(
            user_kind,
            profile,
            "jit_missing_fields",
            detail="Es fehlen: " + ", ".join(missing),
        )
    if not provisioning.email_domain_allowed(provider, account.email):
        return _reject(user_kind, profile, "jit_domain_not_allowed")
    if await _login_in_use(session, user_kind, account.login):
        return _reject(user_kind, profile, "login_taken")
    if not await check_email(session, account.email):
        return _reject(user_kind, profile, "email_invalid")
    if user_kind == "customer" and await customer_email_in_use(session, account.email):
        return _reject(user_kind, profile, "email_in_use")
    return LoginPlan(
        user_kind=user_kind,
        decision="create",
        matched_by="jit",
        user_id=None,
        login=account.login,
        reject_code=None,
        profile=profile,
        profile_changes={},
        assignments=await _plan_assignments(session, provider, identity, user_kind, None),
        link_exists=False,
    )


async def _plan_profile_changes(
    session: AsyncSession,
    provider: ProviderRecord,
    identity: ExternalIdentity,
    profile: MappedProfile,
    user_kind: UserKind,
    user_id: int,
) -> dict[str, tuple[str | None, str]]:
    """Step 7: fields the IdP sent that differ from the account. A new e-mail address
    Znuny would refuse, or that the IdP did not verify (OIDC), is left out: the sign-in
    itself must not fail over it, and an unverified address must never overwrite the account's."""
    from tiqora.domain.user_provisioning import check_email
    from tiqora.identity import provisioning

    if not provider.jit_defaults.update_profile_on_login:
        return {}
    changes = provisioning.diff_profile(
        profile, await provisioning.current_profile(session, user_kind, user_id)
    )
    new_email = changes.get("email")
    if new_email is not None and (
        _email_unverified(provider, identity) or not await check_email(session, new_email[1])
    ):
        del changes["email"]
    return changes


async def _sso_eligible(session: AsyncSession, user_id: int) -> bool:
    flag = (
        await session.execute(
            select(TiqoraUserAuthConfig.sso_eligible).where(TiqoraUserAuthConfig.user_id == user_id)
        )
    ).scalar_one_or_none()
    return bool(flag)


async def _plan_assignments(
    session: AsyncSession,
    provider: ProviderRecord,
    identity: ExternalIdentity,
    user_kind: UserKind,
    user_id: int | None,
) -> AssignmentDiff:
    """Step 8: roles/groups the provider's rules grant. Agents only, never the system
    user; a not yet existing account (JIT preview) is diffed against an empty state."""
    from tiqora.domain.user_provisioning import valid_group_ids, valid_role_ids
    from tiqora.identity import provisioning

    if user_kind != "agent" or user_id == SYSTEM_USER_ID or not provider.role_rules:
        return AssignmentDiff()
    for rule_attribute in {r.attribute.strip() for r in provider.role_rules}:
        if not attribute_present(identity.attributes, rule_attribute):
            # Fail safe: Entra group overage or a broken attribute release must not strip
            # roles. A present but empty list still means "no groups".
            log.warning(
                "role sync skipped for provider %s: claim %r not released",
                provider.slug,
                rule_attribute,
            )
            return AssignmentDiff()
    valid_roles = await valid_role_ids(session)
    valid_groups = await valid_group_ids(session)
    desired = evaluate_rules(
        identity.attributes,
        provider.role_rules,
        valid_role_ids=valid_roles,
        valid_group_ids=valid_groups,
    )
    if user_id is None:
        state = AssignmentState(frozenset(), frozenset(), frozenset(), frozenset())
    else:
        state = await provisioning.load_assignment_state(
            session, provider_id=provider.id, user_id=user_id
        )
    return diff_assignments(
        desired,
        state,
        mode=provider.sync_mode,
        valid_role_ids=valid_roles,
        valid_group_ids=valid_groups,
    )


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
    if not audience_includes(provider.audience, user_kind):
        return _reject(user_kind, profile, "no_match")

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
        if _email_unverified(provider, identity):
            return _reject(user_kind, profile, "email_unverified")
        if profile.email:
            account, ambiguous = await _match_email(session, user_kind, profile.email)
            if ambiguous:
                return _reject(user_kind, profile, "ambiguous_email")

    if account is None:
        return await _plan_jit(session, provider, identity, profile, user_kind)

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
        profile_changes=await _plan_profile_changes(
            session, provider, identity, profile, user_kind, account.id
        ),
        assignments=await _plan_assignments(session, provider, identity, user_kind, account.id),
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


_PROVISIONING_REJECTS: Final[dict[str, RejectCode]] = {
    "email_invalid": "email_invalid",
    "email_in_use": "email_in_use",
    "login_exists": "login_taken",
    "jit_missing_fields": "jit_missing_fields",
    "email_required": "jit_missing_fields",
    "name_required": "jit_missing_fields",
}


class _RaceLost(Exception):  # noqa: N818 - internal control flow
    """A concurrent first login created the same account between plan and apply."""


async def _create_account(
    session: AsyncSession, provider: ProviderRecord, plan: LoginPlan
) -> tuple[int, str]:
    from tiqora.domain.user_provisioning import ProvisioningError
    from tiqora.identity import provisioning

    try:
        if plan.user_kind == "agent":
            user = await provisioning.provision_agent(session, provider, plan.profile)
            return user.id, user.login
        customer = await provisioning.provision_customer(session, provider, plan.profile)
        return customer.id, customer.login
    except ProvisioningError as exc:
        await session.rollback()
        if exc.code == "login_exists":
            raise _RaceLost from exc
        code = _PROVISIONING_REJECTS.get(exc.code)
        if code is None:
            raise
        raise LoginRejected(code) from exc
    except IntegrityError as exc:  # concurrent first login created the same login
        await session.rollback()
        raise _RaceLost from exc


async def _sync_existing(session: AsyncSession, plan: LoginPlan, user_id: int) -> None:
    """One ``sync_profile`` call. A new e-mail address is vetted first (Znuny CheckEmail,
    customer uniqueness) and dropped when refused. If the provisioning layer still refuses
    it (a concurrent change slipped in after the pre-check), the sync is retried once
    without the e-mail; every attempt runs in a savepoint so no partial write survives."""
    from tiqora.domain.user_provisioning import (
        ProvisioningError,
        check_email,
        customer_email_in_use,
    )
    from tiqora.identity.provisioning import sync_profile

    changes = dict(plan.profile_changes)
    new_email = changes.get("email")
    if new_email is not None:
        address = new_email[1].strip()
        refused = not await check_email(session, address) or (
            plan.user_kind == "customer"
            and await customer_email_in_use(session, address, exclude_id=user_id)
        )
        if refused:
            log.warning("profile sync skipped the e-mail address of %s %s", plan.user_kind, user_id)
            del changes["email"]
    try:
        async with session.begin_nested():
            await sync_profile(session, user_kind=plan.user_kind, user_id=user_id, changes=changes)
    except ProvisioningError as exc:
        if "email" not in changes or exc.code not in ("email_in_use", "email_invalid"):
            raise
        log.warning(
            "profile sync dropped the e-mail address of %s %s: %s",
            plan.user_kind,
            user_id,
            exc.code,
        )
        del changes["email"]
        async with session.begin_nested():
            await sync_profile(session, user_kind=plan.user_kind, user_id=user_id, changes=changes)


async def apply_login(
    session: AsyncSession,
    provider: ProviderRecord,
    identity: ExternalIdentity,
    plan: LoginPlan,
    *,
    settings: Settings,
    _replanned: bool = False,
) -> tuple[int, str]:
    """Create (JIT) or sync the account, store the link and commit. Returns (user_id, login).

    Order: create -> flush -> profile sync -> role/group sync (agents) -> link -> commit.
    Losing a race against a concurrent first login re-plans once (the other request's
    committed link/login then matches); otherwise ``login_taken``.
    """
    if plan.decision == "reject":
        raise LoginRejected(plan.reject_code or "no_match")
    if plan.decision == "create":
        try:
            user_id, login = await _create_account(session, provider, plan)
        except _RaceLost:
            if _replanned:
                raise LoginRejected("login_taken") from None
            replan = await plan_login(
                session, provider, identity, user_kind=plan.user_kind, settings=settings
            )
            if replan.decision != "login":
                raise LoginRejected(
                    (replan.reject_code if replan.decision == "reject" else None) or "login_taken"
                ) from None
            return await apply_login(
                session, provider, identity, replan, settings=settings, _replanned=True
            )
        await session.flush()
    elif plan.decision == "login" and plan.user_id is not None and plan.login is not None:
        user_id, login = plan.user_id, plan.login
        await _sync_existing(session, plan, user_id)
    else:
        raise ValueError(f"unsupported login plan decision: {plan.decision}")
    if plan.user_kind == "agent" and provider.role_rules:
        # Re-planned inside this transaction after the account writes above, so JIT
        # defaults and the profile sync are visible. Under a repeatable-read snapshot
        # (MariaDB) commits of concurrent requests are not; concurrent duplicate marks are
        # tolerated by ``apply_assignments``.
        from tiqora.identity.provisioning import apply_assignments

        await apply_assignments(
            session,
            provider_id=provider.id,
            user_id=user_id,
            diff=await _plan_assignments(session, provider, identity, plan.user_kind, user_id),
            provider_slug=provider.slug,
        )
    if _stores_links(provider):
        try:
            await upsert_link(
                session,
                provider_id=provider.id,
                user_kind=plan.user_kind,
                subject=identity.subject,
                user_id=user_id,
                user_login=login,
            )
        except LinkConflictError:
            # The subject got linked to another account since planning (race): link wins.
            await session.rollback()
            raise LoginRejected("user_invalid") from None
    await session.commit()
    return user_id, login


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
        "reject_message": plan.reject_detail
        or (REJECT_MESSAGES.get(plan.reject_code) if plan.reject_code else None),
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
