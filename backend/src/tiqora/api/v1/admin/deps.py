"""Admin-only dependency: wraps ``CurrentUser`` with an admin-group check.

Mirrors the pattern in :mod:`tiqora.api.deps` (``CurrentUser``/``DbSession``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from tiqora.api.deps import CurrentUser, DbSession
from tiqora.domain.auth import AuthenticatedUser
from tiqora.permissions.engine import PermissionEngine


async def get_admin_user(
    user: CurrentUser,
    session: DbSession,
) -> AuthenticatedUser:
    """Return the authenticated user if they are a member of the ``admin``
    group with ``rw`` permission; otherwise raise 403.
    """
    engine = PermissionEngine(session)
    if not await engine.is_admin(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


AdminUser = Annotated[AuthenticatedUser, Depends(get_admin_user)]
