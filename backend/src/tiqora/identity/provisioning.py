"""Just-in-time account creation and profile sync for external identities (D7 steps 5, 7).

Every write goes through ``tiqora.domain.user_provisioning`` (the one Znuny-identical
write path). Nothing here commits; ``login.apply_login`` owns the transaction.

New accounts get an unusable random password (the IdP is the only way in) and, for
agents, ``sso_eligible``. Znuny requires a first and a last name (``UserAdd``); when the
IdP sends none, documented fallbacks are used: first name = local part of the e-mail
address, last name = the login. The login falls back to the e-mail address when a provider
matches by e-mail and maps no login. An e-mail address is mandatory. Customers are
always written to the ``customer_user`` table (a customer directory configured only in
Znuny's Config.pm is never read by Tiqora). An agent created with ``match_by="login"``
may share its e-mail address with another agent (Znuny ``UserAdd`` does not forbid it).

Role/group sync (agents only) notes:

* ``jit_defaults.initial_*`` are written without a managed mark. In ``authoritative`` mode
  the next login strips any such role/group the rules do not grant (Znuny replace
  semantics); use rules instead of initial roles for sites running that mode.
* Switching ``sync_mode`` reclassifies nothing: managed marks stay as they are. Moving to
  ``authoritative`` makes everything not granted by a rule go, marked or not; moving back
  to ``managed`` keeps the remaining marks as the only removable set.
* An admin granting (PUT) a role/group key that is already managed keeps the managed
  mark: the grant is a no-op, so the rule still owns it and removes it when it stops
  matching. Remove it by hand first (which drops the mark) and grant again to make it manual.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.legacy.customer import CustomerUser
from tiqora.db.legacy.user import UserPreferences, Users
from tiqora.db.tiqora.models import TiqoraIdentityManagedAssignment, TiqoraUserAuthConfig
from tiqora.domain.password_setup import unusable_password_hash
from tiqora.domain.user_provisioning import (
    ProvisioningError,
    create_agent,
    create_customer_user,
    current_group_perms,
    current_roles,
    grant_role,
    record_managed_assignment,
    revoke_role,
    set_group_permissions,
    update_agent_profile,
    update_customer_profile,
    valid_group_ids,
    valid_role_ids,
)
from tiqora.identity.mapping import AssignmentDiff, AssignmentState, MappedProfile
from tiqora.identity.store import ProviderRecord
from tiqora.identity.types import SYSTEM_USER_ID, UserKind

log = logging.getLogger(__name__)

PROFILE_FIELDS = ("first_name", "last_name", "email", "mobile")


@dataclass(frozen=True, slots=True)
class NewAccount:
    login: str
    email: str
    first_name: str
    last_name: str
    customer_id: str
    mobile: str | None


def email_domain_allowed(provider: ProviderRecord, email: str) -> bool:
    """``jit_defaults.allowed_email_domains``: empty = any domain, else exact (case-insensitive)."""
    allowed = provider.jit_defaults.allowed_email_domains
    if not allowed:
        return True
    domain = email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""
    return domain in {d.strip().lower().lstrip("@") for d in allowed}


def resolve_new_account(
    provider: ProviderRecord, profile: MappedProfile, user_kind: UserKind
) -> tuple[NewAccount | None, tuple[str, ...]]:
    """(account fields with fallbacks, German names of the missing mandatory fields)."""
    email = (profile.email or "").strip()
    login = (profile.login or "").strip() or (email if provider.match_by == "email" else "")
    missing = tuple(
        label for label, value in (("Anmeldename", login), ("E-Mail", email)) if not value
    )
    if missing:
        return None, missing
    first = (profile.first_name or "").strip() or email.split("@", 1)[0]
    last = (profile.last_name or "").strip() or login
    customer_id = ""
    if user_kind == "customer":
        customer_id = (
            (profile.customer_id or "").strip()
            or provider.jit_defaults.customer_id.strip()
            or email
        )
    return (
        NewAccount(
            login=login,
            email=email,
            first_name=first,
            last_name=last,
            customer_id=customer_id,
            mobile=(profile.mobile or "").strip() or None,
        ),
        (),
    )


def _require_account(
    provider: ProviderRecord, profile: MappedProfile, user_kind: UserKind
) -> NewAccount:
    account, missing = resolve_new_account(provider, profile, user_kind)
    if account is None:
        raise ProvisioningError("jit_missing_fields", "Es fehlen: " + ", ".join(missing))
    return account


async def provision_agent(
    session: AsyncSession, provider: ProviderRecord, profile: MappedProfile
) -> Users:
    """Create an agent from the IdP profile. Raises ``ProvisioningError`` (e-mail refused,
    login taken, mandatory field missing); flushes, never commits."""
    acc = _require_account(provider, profile, "agent")
    user = await create_agent(
        session,
        login=acc.login,
        first_name=acc.first_name,
        last_name=acc.last_name,
        email=acc.email,
        mobile=acc.mobile,
        title=None,
        pw_hash=unusable_password_hash(),
        valid_id=1,
        actor_id=SYSTEM_USER_ID,
        require_email=True,
    )
    defaults = provider.jit_defaults
    valid_roles = await valid_role_ids(session)
    for role_id in dict.fromkeys(defaults.initial_role_ids):
        if role_id in valid_roles:
            await grant_role(session, user_id=user.id, role_id=role_id, actor_id=SYSTEM_USER_ID)
    valid_groups = await valid_group_ids(session)
    wanted: dict[int, set[str]] = {}
    for grant in defaults.initial_groups:
        if grant.group_id in valid_groups:
            wanted.setdefault(grant.group_id, set()).update(grant.permissions)
    for group_id, keys in wanted.items():
        await set_group_permissions(
            session,
            user_id=user.id,
            group_id=group_id,
            permission_keys=keys,
            actor_id=SYSTEM_USER_ID,
        )
    # Inserted directly: AuthConfigService.set commits.
    session.add(TiqoraUserAuthConfig(user_id=user.id, sso_eligible=True))
    await session.flush()
    return user


async def provision_customer(
    session: AsyncSession, provider: ProviderRecord, profile: MappedProfile
) -> CustomerUser:
    """Create a customer user (DB backend only; the caller checks). customer_id =
    mapped value, else ``jit_defaults.customer_id``, else the e-mail address."""
    acc = _require_account(provider, profile, "customer")
    return await create_customer_user(
        session,
        login=acc.login,
        email=acc.email,
        customer_id=acc.customer_id,
        first_name=acc.first_name,
        last_name=acc.last_name,
        pw_hash=unusable_password_hash(),
        actor_id=SYSTEM_USER_ID,
        mobile=acc.mobile,
    )


async def current_profile(
    session: AsyncSession, user_kind: UserKind, user_id: int
) -> dict[str, str | None]:
    """Current first/last name, e-mail and mobile of an account (one Znuny table each)."""
    if user_kind == "agent":
        row = (
            await session.execute(
                select(Users.first_name, Users.last_name).where(Users.id == user_id)
            )
        ).first()
        prefs = {
            str(k): _text(v)
            for k, v in (
                await session.execute(
                    select(
                        UserPreferences.preferences_key, UserPreferences.preferences_value
                    ).where(
                        UserPreferences.user_id == user_id,
                        UserPreferences.preferences_key.in_(("UserEmail", "UserMobile")),
                    )
                )
            ).all()
        }
        return {
            "first_name": row[0] if row else None,
            "last_name": row[1] if row else None,
            "email": prefs.get("UserEmail"),
            "mobile": prefs.get("UserMobile"),
        }
    crow = (
        await session.execute(
            select(
                CustomerUser.first_name,
                CustomerUser.last_name,
                CustomerUser.email,
                CustomerUser.mobile,
            ).where(CustomerUser.id == user_id)
        )
    ).first()
    return dict(zip(PROFILE_FIELDS, crow, strict=True)) if crow else dict.fromkeys(PROFILE_FIELDS)


def _text(raw: bytes | str | None) -> str | None:
    if raw is None:
        return None
    return raw if isinstance(raw, str) else bytes(raw).decode("utf-8", errors="replace")


def diff_profile(
    profile: MappedProfile, current: Mapping[str, str | None]
) -> dict[str, tuple[str | None, str]]:
    """Only fields the IdP actually sent (mapped, non-empty) and that differ."""
    changes: dict[str, tuple[str | None, str]] = {}
    for field in PROFILE_FIELDS:
        new = (getattr(profile, field) or "").strip()
        if not new:
            continue
        old = current.get(field)
        same = (
            (old or "").strip().casefold() == new.casefold()
            if field == "email"
            else (old or "").strip() == new
        )
        if not same:
            changes[field] = (old, new)
    return changes


async def sync_profile(
    session: AsyncSession,
    *,
    user_kind: UserKind,
    user_id: int,
    changes: Mapping[str, tuple[str | None, str]],
) -> None:
    """Apply planned changes (``change_by`` = system user). No-op for an empty set."""
    if not changes:
        return
    new = {k: v[1] for k, v in changes.items()}
    if user_kind == "agent":
        user = await session.get(Users, user_id)
        if user is None:
            return
        await update_agent_profile(
            session,
            user,
            first_name=new.get("first_name"),
            last_name=new.get("last_name"),
            email=new.get("email"),
            mobile=new.get("mobile"),
            actor_id=SYSTEM_USER_ID,
        )
    else:
        cu = await session.get(CustomerUser, user_id)
        if cu is None:
            return
        await update_customer_profile(
            session,
            cu,
            first_name=new.get("first_name"),
            last_name=new.get("last_name"),
            email=new.get("email"),
            mobile=new.get("mobile"),
            actor_id=SYSTEM_USER_ID,
        )


async def _managed_marks(
    session: AsyncSession, provider_id: int, user_id: int
) -> tuple[frozenset[int], frozenset[tuple[int, str]]]:
    rows = (
        await session.execute(
            select(
                TiqoraIdentityManagedAssignment.kind,
                TiqoraIdentityManagedAssignment.target_id,
                TiqoraIdentityManagedAssignment.permission_key,
            ).where(
                TiqoraIdentityManagedAssignment.provider_id == provider_id,
                TiqoraIdentityManagedAssignment.user_id == user_id,
            )
        )
    ).all()
    return (
        frozenset(int(t) for k, t, _ in rows if k == "role"),
        frozenset((int(t), str(p)) for k, t, p in rows if k == "group"),
    )


async def load_assignment_state(
    session: AsyncSession, *, provider_id: int, user_id: int
) -> AssignmentState:
    """Current role/group assignments of an agent plus the ones THIS provider manages.

    Managed rows of other providers are not part of the state: a provider never
    owns what another provider (or an admin) assigned.
    """
    managed_roles, managed_groups = await _managed_marks(session, provider_id, user_id)
    return AssignmentState(
        current_roles=await current_roles(session, user_id),
        current_group_perms=await current_group_perms(session, user_id),
        managed_roles=managed_roles,
        managed_group_perms=managed_groups,
    )


async def apply_assignments(
    session: AsyncSession,
    *,
    provider_id: int,
    user_id: int,
    diff: AssignmentDiff,
    provider_slug: str | None = None,
) -> None:
    """Write a role/group diff for an agent (actor = system user); flush only.

    Roles are granted/revoked one by one. Group keys are written once per group from
    ``diff.final_group_keys`` (Znuny ``PermissionGroupUserAdd`` replaces the whole key
    set, ``rw`` alone), never as independent add/remove rows. Then the managed marks
    are inserted/deleted (a concurrent duplicate insert is ignored). Group keys are written
    exactly as planned (no ``rw`` collapse beyond the desired set: a manual ``ro`` may sit
    next to a managed ``rw``). Every grant/revoke is logged like Znuny's "sync ldap role"
    notices. The system user (id 1) is never synced.
    """
    if user_id == SYSTEM_USER_ID or diff.empty:
        return
    who = provider_slug or provider_id
    for role_id in sorted(diff.remove_roles):
        if await revoke_role(session, user_id=user_id, role_id=role_id):
            log.info("sync role revoke: user %s, provider %s, role %s", user_id, who, role_id)
    for role_id in sorted(diff.add_roles):
        if await grant_role(session, user_id=user_id, role_id=role_id, actor_id=SYSTEM_USER_ID):
            log.info("sync role grant: user %s, provider %s, role %s", user_id, who, role_id)
    for group_id, keys in sorted(diff.final_group_keys.items()):
        before = {k for g, k in diff.add_groups | diff.remove_groups if g == group_id}
        if await set_group_permissions(
            session,
            user_id=user_id,
            group_id=group_id,
            permission_keys=keys,
            actor_id=SYSTEM_USER_ID,
            collapse_rw=False,
        ):
            log.info(
                "sync group set: user %s, provider %s, group %s, keys %s (changed %s)",
                user_id,
                who,
                group_id,
                sorted(keys),
                sorted(before),
            )

    existing_roles, existing_groups = await _managed_marks(session, provider_id, user_id)
    for role_id in sorted(diff.drop_managed_roles):
        await _drop_mark(session, provider_id, user_id, "role", role_id, "")
    for group_id, key in sorted(diff.drop_managed_groups):
        await _drop_mark(session, provider_id, user_id, "group", group_id, key)
    for role_id in sorted(diff.record_managed_roles - existing_roles):
        await _record_mark(session, provider_id, user_id, "role", role_id, "")
    for group_id, key in sorted(diff.record_managed_groups - existing_groups):
        await _record_mark(session, provider_id, user_id, "group", group_id, key)


async def _record_mark(
    session: AsyncSession, provider_id: int, user_id: int, kind: str, target_id: int, key: str
) -> None:
    """Insert a managed mark; a concurrent login that inserted it first is not an error."""
    try:
        async with session.begin_nested():
            await record_managed_assignment(
                session,
                provider_id=provider_id,
                user_kind="agent",
                user_id=user_id,
                kind=kind,
                target_id=target_id,
                permission_key=key,
            )
    except IntegrityError:
        log.info("managed mark already present: user %s, %s %s", user_id, kind, target_id)


async def _drop_mark(
    session: AsyncSession, provider_id: int, user_id: int, kind: str, target_id: int, key: str
) -> None:
    await session.execute(
        delete(TiqoraIdentityManagedAssignment).where(
            TiqoraIdentityManagedAssignment.provider_id == provider_id,
            TiqoraIdentityManagedAssignment.user_id == user_id,
            TiqoraIdentityManagedAssignment.kind == kind,
            TiqoraIdentityManagedAssignment.target_id == target_id,
            TiqoraIdentityManagedAssignment.permission_key == key,
        )
    )


__all__ = [
    "NewAccount",
    "apply_assignments",
    "load_assignment_state",
    "current_profile",
    "diff_profile",
    "email_domain_allowed",
    "provision_agent",
    "provision_customer",
    "resolve_new_account",
    "sync_profile",
]
