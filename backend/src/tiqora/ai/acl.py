"""AI feature ACL CRUD (plan §3.1/§3.6): which group/role/user may use which
AI feature, with optional daily/monthly limits. Enforcement in the agent
runtime lands in later phases; Phase A only ships the admin CRUD surface."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.ai.models import ACL_SUBJECT_TYPES, AI_FEATURES, TiqoraAiAcl


class AiAclValidationError(ValueError):
    pass


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


__all__ = [
    "AiAclValidationError",
    "create_acl",
    "delete_acl",
    "get_acl",
    "list_acls",
    "update_acl",
]
