"""AI feature ACL CRUD (plan §3.1/§3.6): which group/role/user may use which
AI feature, with optional daily/monthly limits. Enforcement in the agent
runtime lands in later phases; Phase A only ships the admin CRUD surface."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import (
    ACL_SUBJECT_GROUP,
    ACL_SUBJECT_ROLE,
    ACL_SUBJECT_TYPES,
    ACL_SUBJECT_USER,
    AI_FEATURES,
    TiqoraAiAcl,
    TiqoraAiUsage,
)


class AiAclValidationError(ValueError):
    pass


class AclLimitExceededError(Exception):
    """Raised by :func:`check_feature_limits` when a configured ACL limit
    (requests/day, tokens/day, requests/month) is already reached."""

    def __init__(self, message: str, *, limit_kind: str) -> None:
        super().__init__(message)
        self.limit_kind = limit_kind


def _validate(subject_type: str, feature: str) -> None:
    if subject_type not in ACL_SUBJECT_TYPES:
        raise AiAclValidationError(
            f"Invalid subject_type: {subject_type!r} (expected one of {sorted(ACL_SUBJECT_TYPES)})"
        )
    if feature not in AI_FEATURES:
        raise AiAclValidationError(
            f"Invalid feature: {feature!r} (expected one of {sorted(AI_FEATURES)})"
        )


async def list_acls(session: AsyncSession) -> list[TiqoraAiAcl]:
    rows = (await session.execute(select(TiqoraAiAcl).order_by(TiqoraAiAcl.id))).scalars().all()
    return list(rows)


async def get_acl(session: AsyncSession, acl_id: int) -> TiqoraAiAcl | None:
    return await session.get(TiqoraAiAcl, acl_id)


async def create_acl(
    session: AsyncSession,
    *,
    subject_type: str,
    subject_id: int,
    feature: str,
    allowed: bool = True,
    limit_requests_day: int | None = None,
    limit_tokens_day: int | None = None,
    limit_requests_month: int | None = None,
) -> TiqoraAiAcl:
    _validate(subject_type, feature)
    row = TiqoraAiAcl(
        subject_type=subject_type,
        subject_id=subject_id,
        feature=feature,
        allowed=allowed,
        limit_requests_day=limit_requests_day,
        limit_tokens_day=limit_tokens_day,
        limit_requests_month=limit_requests_month,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def update_acl(session: AsyncSession, row: TiqoraAiAcl, **fields: object) -> TiqoraAiAcl:
    subject_type = fields.get("subject_type", row.subject_type)
    feature = fields.get("feature", row.feature)
    assert isinstance(subject_type, str)
    assert isinstance(feature, str)
    _validate(subject_type, feature)
    for key, value in fields.items():
        if hasattr(row, key):
            setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_acl(session: AsyncSession, row: TiqoraAiAcl) -> None:
    await session.delete(row)
    await session.commit()


async def _applicable_acl_rows(
    session: AsyncSession, user_id: int, feature: str
) -> list[TiqoraAiAcl]:
    """Resolve the ACL row(s) governing *user_id* for *feature*.

    An explicit user-level row, if present, is the **only** applicable row
    (it always wins over group/role — see :func:`check_feature_access`).
    Otherwise, every matching group/role row is returned so the caller can
    apply OR/precedence semantics for ``allowed`` and take the tightest of
    any configured limits.
    """
    from tiqora.db.legacy.user import Roles, RoleUser
    from tiqora.permissions.engine import PermissionEngine

    user_row = (
        await session.execute(
            select(TiqoraAiAcl).where(
                TiqoraAiAcl.subject_type == ACL_SUBJECT_USER,
                TiqoraAiAcl.subject_id == user_id,
                TiqoraAiAcl.feature == feature,
            )
        )
    ).scalar_one_or_none()
    if user_row is not None:
        return [user_row]

    engine = PermissionEngine(session)
    # queue_permissions is keyed by group_id and already folds role → group
    # membership into that map, but ACL "group"/"role" subjects here refer to
    # permission_groups.id / roles.id directly (not queue group ownership),
    # so resolve both membership sets independently.
    group_ids = set(await engine.queue_permissions(user_id))
    role_rows = await session.execute(
        select(RoleUser.role_id)
        .join(Roles, Roles.id == RoleUser.role_id)
        .where(RoleUser.user_id == user_id, Roles.valid_id == 1)
    )
    role_ids = {r[0] for r in role_rows.all()}

    filters = []
    if group_ids:
        filters.append((ACL_SUBJECT_GROUP, group_ids))
    if role_ids:
        filters.append((ACL_SUBJECT_ROLE, role_ids))
    if not filters:
        return []

    rows: list[TiqoraAiAcl] = []
    for subject_type, ids in filters:
        matched = (
            (
                await session.execute(
                    select(TiqoraAiAcl).where(
                        TiqoraAiAcl.subject_type == subject_type,
                        TiqoraAiAcl.subject_id.in_(ids),
                        TiqoraAiAcl.feature == feature,
                    )
                )
            )
            .scalars()
            .all()
        )
        rows.extend(matched)
    return rows


async def check_feature_access(session: AsyncSession, user_id: int, feature: str) -> bool:
    """Resolve whether *user_id* may use *feature* (plan §3.6).

    Precedence: an explicit user-level ACL row always wins. Otherwise, any
    matching group/role row with ``allowed=False`` denies; else any matching
    row with ``allowed=True`` grants. With **no** ACL configured for this
    feature at all, access is unrestricted (Phase A/B ship no seed data —
    an empty ACL table must not lock every agent out of a feature).
    """
    rows = await _applicable_acl_rows(session, user_id, feature)
    if not rows:
        return True
    if any(not r.allowed for r in rows):
        return False
    return any(r.allowed for r in rows)


async def _usage_request_count(
    session: AsyncSession, user_id: int, feature: str, *, since: datetime
) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(TiqoraAiUsage)
                .where(
                    TiqoraAiUsage.user_id == user_id,
                    TiqoraAiUsage.feature == feature,
                    TiqoraAiUsage.ts >= since,
                )
            )
        ).scalar_one()
    )


async def _usage_token_sum(
    session: AsyncSession, user_id: int, feature: str, *, since: datetime
) -> int:
    total = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(TiqoraAiUsage.prompt_tokens + TiqoraAiUsage.completion_tokens), 0
                )
            ).where(
                TiqoraAiUsage.user_id == user_id,
                TiqoraAiUsage.feature == feature,
                TiqoraAiUsage.ts >= since,
            )
        )
    ).scalar_one()
    return int(total)


async def check_feature_limits(session: AsyncSession, user_id: int, feature: str) -> None:
    """Enforce ``limit_requests_day`` / ``limit_tokens_day`` / ``limit_requests_month``
    (plan §3.6) against already-recorded ``tiqora_ai_usage`` rows.

    ``NULL`` on a limit column means unbounded (plan: "NULL = unbegrenzt").
    When several ACL rows apply (multiple group/role memberships), the
    tightest (``min``) of each configured limit kind is enforced. Manual-path
    only — the auto-reply path budgets via the queue policy, not agent ACL
    (plan §3.6: "keine Kreuz-Anrechnung").

    Raises :class:`AclLimitExceededError` when a limit is already reached
    (i.e. this call would be the one that exceeds it) — callers translate
    that into an HTTP 429.
    """
    rows = await _applicable_acl_rows(session, user_id, feature)
    if not rows:
        return

    limits_requests_day = [r.limit_requests_day for r in rows if r.limit_requests_day is not None]
    limits_tokens_day = [r.limit_tokens_day for r in rows if r.limit_tokens_day is not None]
    limits_requests_month = [
        r.limit_requests_month for r in rows if r.limit_requests_month is not None
    ]
    if not (limits_requests_day or limits_tokens_day or limits_requests_month):
        return

    now = datetime.now(UTC).replace(tzinfo=None)
    day_start = datetime(now.year, now.month, now.day)
    month_start = datetime(now.year, now.month, 1)

    if limits_requests_day:
        limit = min(limits_requests_day)
        count = await _usage_request_count(session, user_id, feature, since=day_start)
        if count >= limit:
            raise AclLimitExceededError(
                f"Daily request limit ({limit}) reached for {feature}", limit_kind="requests_day"
            )

    if limits_tokens_day:
        limit = min(limits_tokens_day)
        tokens = await _usage_token_sum(session, user_id, feature, since=day_start)
        if tokens >= limit:
            raise AclLimitExceededError(
                f"Daily token limit ({limit}) reached for {feature}", limit_kind="tokens_day"
            )

    if limits_requests_month:
        limit = min(limits_requests_month)
        count = await _usage_request_count(session, user_id, feature, since=month_start)
        if count >= limit:
            raise AclLimitExceededError(
                f"Monthly request limit ({limit}) reached for {feature}",
                limit_kind="requests_month",
            )


__all__ = [
    "AclLimitExceededError",
    "AiAclValidationError",
    "check_feature_access",
    "check_feature_limits",
    "create_acl",
    "delete_acl",
    "get_acl",
    "list_acls",
    "update_acl",
]
