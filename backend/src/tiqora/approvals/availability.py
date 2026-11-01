"""Subject types that stand still because the module they belong to is switched off.

A subject type may name ``available``. While it answers False its open requests are frozen: not in
the inbox or on the ticket, votes and links refused, no mail, and the sweep leaves them alone and
pauses their deadlines. Hooks of requests already decided still run (jobs of the job runner).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.approvals.contract import APPROVAL_SUBJECT_TYPES, ApprovalError
from tiqora.extensions.core import get_registry
from tiqora.extensions.registry import Registry


async def frozen_subject_types(
    session: AsyncSession, registry: Registry | None = None
) -> frozenset[str]:
    reg = registry or get_registry()
    frozen: set[str] = set()
    for subject in reg.items(APPROVAL_SUBJECT_TYPES):
        if subject.available is not None and not await subject.available(session):
            frozen.add(subject.key)
    return frozenset(frozen)


async def require_subject_available(
    session: AsyncSession, subject_type: str, registry: Registry | None = None
) -> None:
    """409 ``subject_unavailable`` while the request's module is switched off."""
    subject = (registry or get_registry()).get(APPROVAL_SUBJECT_TYPES, subject_type)
    if (
        subject is not None
        and subject.available is not None
        and not await subject.available(session)
    ):
        raise ApprovalError("subject_unavailable", 409)
