"""Delegation (Vertretung) of approvals (A8).

Only the stub :func:`active_delegators` exists so far: the decision code asks it which agents
the actor currently substitutes for. The delegation tasks fill in the body and keep the signature.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession


async def active_delegators(
    session: AsyncSession, substitute_user_id: int, *, at: datetime
) -> set[int]:
    """User ids whose approvals *substitute_user_id* may decide at *at* (none until implemented)."""
    return set()
