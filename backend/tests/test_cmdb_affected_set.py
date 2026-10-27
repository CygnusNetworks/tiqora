"""``affected_set`` (system-scope union of the reach of several CIs) and
``LinkService.ci_ids_for_tickets`` (names ``cmdb4``; ids of the change plan band 946600-946699 are
not needed, the rows are created by the services)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    SERVICE_ID,
    SERVICE_ID_2,
    TICKET_CLOSED,
    TICKET_HIDDEN,
    TICKET_OPEN,
    USER_A,
    USER_ADMIN,
    CmdbWorld,
    make_ci,
    make_class,
)
from tiqora.cmdb.access import load_access
from tiqora.cmdb.impact import AffectedSet, affected, affected_set
from tiqora.cmdb.links import LinkEnd, LinkKind, LinkService
from tiqora.cmdb.service import system_access
from tiqora.cmdb.settings import CmdbSettings

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset


async def _link(
    s: AsyncSession,
    source: tuple[LinkKind, str | int],
    target: tuple[LinkKind, str | int],
    link_type: str = "DependsOn",
) -> None:
    await LinkService(s).add(
        system_access(USER_A),
        source=LinkEnd(source[0], str(source[1])),
        target=LinkEnd(target[0], str(target[1])),
        link_type_key=link_type,
        enforce_permissions=False,
    )
    await s.commit()


def _cap(cap: int) -> CmdbSettings:
    # below the validated floor (100): model_construct skips validation, test only
    return CmdbSettings.model_construct(
        **{**CmdbSettings().model_dump(), "propagation_node_cap": cap}
    )


async def _chain(s: AsyncSession, class_id: int, prefix: str, length: int) -> list[int]:
    """``prefix0 DependsOn prefix1 DependsOn ...`` (DependsOn propagates both ways)."""
    ids = [await make_ci(s, class_id, f"{prefix}{i}") for i in range(length)]
    for a, b in zip(ids, ids[1:], strict=False):
        await _link(s, ("ci", a), ("ci", b))
    return ids


async def test_affected_set_union_and_cap(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        x = await _chain(s, cid, "cmdb4-x", 3)
        y = await _chain(s, cid, "cmdb4-y", 3)
        await _link(s, ("service", SERVICE_ID), ("ci", x[2]))
        await _link(s, ("service", SERVICE_ID_2), ("ci", y[1]))

        union = await affected_set(s, [x[0], y[0]], settings=CmdbSettings())
        # a root that the first root reaches adds nothing; duplicates and unknown ids are dropped
        shared = await affected_set(s, [x[0], x[2], x[2], 749999], settings=CmdbSettings())
        none = await affected_set(s, [], settings=CmdbSettings())
        capped = await affected_set(s, [x[0], y[0]], settings=_cap(4))

    assert isinstance(union, AffectedSet)
    assert union.ci_ids == frozenset(x) | frozenset(y)
    assert union.service_ids == frozenset({SERVICE_ID, SERVICE_ID_2})
    assert union.truncated is False
    assert shared.ci_ids == frozenset(x) and shared.service_ids == frozenset({SERVICE_ID})
    assert not shared.truncated
    assert none == AffectedSet(frozenset(), frozenset(), False)
    # the cap is shared by both roots: 3 + 3 CIs do not fit into 4, and both roots stay
    assert capped.truncated is True and len(capped.ci_ids) == 4
    assert {x[0], y[0]} <= capped.ci_ids


async def test_affected_set_has_no_permission_filter(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        hidden_cls = await make_class(s, key="cmdb4hid", group_id=GROUP_HIDDEN)
        db = await make_ci(s, cid, "cmdb4-db")
        secret = await make_ci(s, hidden_cls, "cmdb4-secret", user_id=USER_ADMIN)
        await _link(s, ("ci", secret), ("ci", db))
        await _link(s, ("service", SERVICE_ID), ("ci", secret))

        everything = await affected_set(s, [db], settings=CmdbSettings())
        # contrast: the permission-filtered view of user A sees neither the CI nor its service
        filtered = await affected(s, await load_access(s, USER_A), db, settings=CmdbSettings())

    assert everything.ci_ids == frozenset({db, secret})
    assert everything.service_ids == frozenset({SERVICE_ID})
    assert filtered.ci_count == 1 and filtered.services == []


async def test_ci_ids_for_tickets(cmdb_db: CmdbWorld) -> None:
    async with cmdb_db.factory() as s:
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-a")
        b = await make_ci(s, cid, "cmdb4-b")
        c = await make_ci(s, cid, "cmdb4-c")
        # both storage directions, any link type, and a ticket in a queue nobody here may read
        await _link(s, ("ticket", TICKET_OPEN), ("ci", a), "RelevantTo")
        await _link(s, ("ci", b), ("ticket", TICKET_OPEN), "RelevantTo")
        await _link(s, ("ci", c), ("ticket", TICKET_HIDDEN), "RelevantTo")
        await _link(s, ("ci", a), ("ci", b))  # CI-CI links are not ticket links

        got = await LinkService(s).ci_ids_for_tickets(
            [TICKET_OPEN, TICKET_HIDDEN, TICKET_CLOSED, TICKET_OPEN]
        )
        empty = await LinkService(s).ci_ids_for_tickets([])

    assert got == {TICKET_OPEN: {a, b}, TICKET_HIDDEN: {c}}  # TICKET_CLOSED has no link
    assert empty == {}
