"""Dependencies that gate agent features granted via ``tiqora_feature_grant``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.domain.auth import AuthenticatedUser
from tiqora.domain.feature_grants import CUSTOMER_DIRECTORY, CUSTOMER_EDIT, FeatureGrantService


async def get_customer_directory_user(
    user: CurrentUser,
    session: DbSession,
) -> AuthenticatedUser:
    """The current agent if they may use the customer directory; 403 otherwise."""
    if not await FeatureGrantService(session).may_use(user.id, CUSTOMER_DIRECTORY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer directory access required",
        )
    return user


CustomerDirectoryUser = Annotated[AuthenticatedUser, Depends(get_customer_directory_user)]


async def get_customer_edit_user(
    user: CurrentUser,
    session: DbSession,
) -> AuthenticatedUser:
    """The current agent if they may edit customer users; 403 otherwise."""
    if not await FeatureGrantService(session).may_use(user.id, CUSTOMER_EDIT):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Customer edit permission required",
        )
    return user


CustomerEditUser = Annotated[AuthenticatedUser, Depends(get_customer_edit_user)]
