"""Agent features granted to single agents, permission groups or roles.

Backed by ``tiqora_feature_grant``. Admins (``rw`` on ``admin``) always have
every feature; otherwise a feature with no matching grant is denied.

Semantics of a grant:

* ``user``  — that agent.
* ``group`` — every member of the permission group, whatever permission key
  they hold there (directly or via a role). "Alle Mitglieder der Gruppe".
* ``role``  — every agent holding the (valid) role directly.

Invalid agents never get a feature, even with a direct grant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.db.tiqora.models import TiqoraFeatureGrant
from tiqora.permissions.engine import PermissionEngine

#: The agent "Kunden" page: customer list, company filter and vCard list exports.
CUSTOMER_DIRECTORY: Final[str] = "customer_directory"
#: Create and edit customer users from the agent "Kunden" page.
CUSTOMER_EDIT: Final[str] = "customer_edit"

FEATURES: Final[frozenset[str]] = frozenset({CUSTOMER_DIRECTORY, CUSTOMER_EDIT})

SubjectType = Literal["user", "group", "role"]


@dataclass
class FeatureGrants:
    user_ids: list[int] = field(default_factory=list)
    group_ids: list[int] = field(default_factory=list)
    role_ids: list[int] = field(default_factory=list)


class FeatureGrantService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._perms = PermissionEngine(session)

    async def may_use(self, user_id: int, feature: str) -> bool:
        """True if *user_id* may use *feature* (admin, or a matching grant)."""
        if await self._perms.is_admin(user_id):
            return True
        if not await self._perms.is_valid_user(user_id):
            return False
        grants = await self.get(feature)
        if user_id in grants.user_ids:
            return True
        if grants.role_ids and await self._perms.role_ids(user_id) & set(grants.role_ids):
            return True
        if grants.group_ids:
            member_of = set(await self._perms.queue_permissions(user_id))
            if member_of & set(grants.group_ids):
                return True
        return False

    async def get(self, feature: str) -> FeatureGrants:
        rows = await self._session.execute(
            select(TiqoraFeatureGrant.subject_type, TiqoraFeatureGrant.subject_id)
            .where(TiqoraFeatureGrant.feature == feature)
            .order_by(TiqoraFeatureGrant.subject_type, TiqoraFeatureGrant.subject_id)
        )
        out = FeatureGrants()
        for subject_type, subject_id in rows.all():
            if subject_type == "user":
                out.user_ids.append(subject_id)
            elif subject_type == "group":
                out.group_ids.append(subject_id)
            elif subject_type == "role":
                out.role_ids.append(subject_id)
        return out

    async def set(self, feature: str, grants: FeatureGrants) -> None:
        """Replace every grant of *feature* (admin action). Caller commits."""
        await self._session.execute(
            delete(TiqoraFeatureGrant).where(TiqoraFeatureGrant.feature == feature)
        )
        subjects: list[tuple[SubjectType, list[int]]] = [
            ("user", grants.user_ids),
            ("group", grants.group_ids),
            ("role", grants.role_ids),
        ]
        for subject_type, ids in subjects:
            for subject_id in dict.fromkeys(ids):
                self._session.add(
                    TiqoraFeatureGrant(
                        feature=feature, subject_type=subject_type, subject_id=subject_id
                    )
                )
        await self._session.flush()
