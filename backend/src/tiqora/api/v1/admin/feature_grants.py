"""Admin: who may use an agent feature (agents, permission groups, roles)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from tiqora.api.deps import DbSession
from tiqora.api.v1.admin.deps import AdminUser
from tiqora.domain.feature_grants import FEATURES, FeatureGrants, FeatureGrantService

router = APIRouter(prefix="/feature-grants", tags=["admin:feature-grants"])


class FeatureGrantsOut(BaseModel):
    """Grants of one feature. Admins always have it; empty lists = admin-only."""

    user_ids: list[int]
    group_ids: list[int]
    role_ids: list[int]


class FeatureGrantsUpdate(BaseModel):
    user_ids: list[int] = []
    group_ids: list[int] = []
    role_ids: list[int] = []


def _check_feature(feature: str) -> None:
    if feature not in FEATURES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown feature")


def _out(grants: FeatureGrants) -> FeatureGrantsOut:
    return FeatureGrantsOut(
        user_ids=grants.user_ids, group_ids=grants.group_ids, role_ids=grants.role_ids
    )


@router.get("/{feature}", response_model=FeatureGrantsOut)
async def get_feature_grants(
    feature: str, admin: AdminUser, session: DbSession
) -> FeatureGrantsOut:
    _ = admin
    _check_feature(feature)
    return _out(await FeatureGrantService(session).get(feature))


@router.put("/{feature}", response_model=FeatureGrantsOut)
async def set_feature_grants(
    feature: str, body: FeatureGrantsUpdate, admin: AdminUser, session: DbSession
) -> FeatureGrantsOut:
    """Replace who may use *feature*."""
    _ = admin
    _check_feature(feature)
    svc = FeatureGrantService(session)
    await svc.set(
        feature,
        FeatureGrants(user_ids=body.user_ids, group_ids=body.group_ids, role_ids=body.role_ids),
    )
    await session.commit()
    return _out(await svc.get(feature))
