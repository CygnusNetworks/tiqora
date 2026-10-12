"""Tiqora default link types and their insert-if-absent seeder (wave-4 step 0).

Both the ITSM core (``sync_link_type_rows``) and the Tiqora CMDB (``seed_defaults``) call
:func:`ensure_default_link_types`, so there is one set of defaults and one seeder.

Provisional values (the ITSM core's Znuny behaviour survey, plan Task 3, corrects them):

* ``pointed`` — ``DependsOn`` and ``Includes`` directed; ``ConnectedTo``, ``AlternativeTo`` and
  ``RelevantTo`` not (Znuny ITSM defines ``RelevantTo`` with equal source/target names).
* offer flags — Tiqora-origin types are never offered for ticket ↔ ticket (Znuny cannot store
  them); ticket ↔ CI only ``RelevantTo`` (Znuny's ticket ↔ CI type); CI ↔ CI the four structural
  types; CI ↔ service ``DependsOn``.
* propagation — only ``DependsOn`` (``source``: the source end gets the warning when the target is
  in incident; Znuny ``ITSM::Core::IncidentLinkTypeDirection`` default).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.itsm.models import TiqoraItsmLinkType


@dataclass(frozen=True, slots=True)
class DefaultLinkType:
    name: str
    pointed: bool
    propagation: Literal["none", "source", "target", "both"]
    offer_ticket_ticket: bool
    offer_ticket_ci: bool
    offer_ci_ci: bool
    offer_ci_service: bool
    position: int


DEFAULT_LINK_TYPES: Final[tuple[DefaultLinkType, ...]] = (
    DefaultLinkType("DependsOn", True, "source", False, False, True, True, 10),
    DefaultLinkType("Includes", True, "none", False, False, True, False, 20),
    DefaultLinkType("ConnectedTo", False, "none", False, False, True, False, 30),
    DefaultLinkType("AlternativeTo", False, "none", False, False, True, False, 40),
    DefaultLinkType("RelevantTo", False, "none", False, True, False, False, 50),
)


async def ensure_default_link_types(session: AsyncSession, *, user_id: int) -> int:
    """Insert every default whose ``name`` is absent (``origin="tiqora"``); returns the count.

    Never updates an existing row (admin overrides and Znuny-origin rows stay as they are),
    never deletes. Flushes only: the caller commits.
    """
    names = [d.name for d in DEFAULT_LINK_TYPES]
    existing = set(
        (
            await session.execute(
                select(TiqoraItsmLinkType.name).where(TiqoraItsmLinkType.name.in_(names))
            )
        ).scalars()
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    added = 0
    for d in DEFAULT_LINK_TYPES:
        if d.name in existing:
            continue
        session.add(
            TiqoraItsmLinkType(
                name=d.name,
                origin="tiqora",
                source_label=None,
                target_label=None,
                pointed=d.pointed,
                propagation=d.propagation,
                offer_ticket_ticket=d.offer_ticket_ticket,
                offer_ticket_ci=d.offer_ticket_ci,
                offer_ci_ci=d.offer_ci_ci,
                offer_ci_service=d.offer_ci_service,
                position=d.position,
                valid=True,
                create_time=now,
                create_by=user_id,
                change_time=now,
                change_by=user_id,
            )
        )
        added += 1
    if added:
        await session.flush()
    return added


__all__ = ["DEFAULT_LINK_TYPES", "DefaultLinkType", "ensure_default_link_types"]
