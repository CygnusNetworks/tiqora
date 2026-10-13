"""Process ``LinkAdd`` towards ``ITSMConfigItem`` follows the ITSM mode (PROCESS-LINKADD-MODEB).

Both dialects (``cmdb_db`` world, seed band 740000-749999). Mode ``tiqora``: the link goes through
the CMDB link service as the process actor (cross ruling R12: ``rw`` on the ticket's queue, ``ro``
on the CI's class); ``znuny_readthrough``: the Znuny ``link_relation`` path as before; ``off``: CI
ends are refused with a German text recorded as a skipped action.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    GROUP_HIDDEN,
    GROUP_RO,
    TICKET_OPEN,
    USER_A,
    USER_B,
    CmdbWorld,
    make_ci,
    make_class,
    set_mode,
)
from tiqora.db.tiqora.models import TiqoraItsmLink
from tiqora.process import engine as process_engine
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import SkippedAction, execute_transition_action
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

MODULE = "Kernel::System::ProcessManagement::TransitionAction::LinkAdd"


@pytest.fixture(autouse=True)
def _clean_outbox(cmdb_db: CmdbWorld) -> Any:
    stmts = (
        "DELETE FROM tiqora_event_outbox WHERE ticket_id BETWEEN 740300 AND 740399",
        "DELETE FROM tiqora_cache_invalidation WHERE ticket_id BETWEEN 740300 AND 740399",
        "DELETE FROM link_relation WHERE source_key = '740300' OR target_key = '740300'",
    )
    for stmt in stmts:
        cmdb_db.sql(stmt)
    yield
    for stmt in stmts:
        cmdb_db.sql(stmt)


def _sysconfig(session: AsyncSession) -> SysConfig:
    async def fetch(name: str) -> Any:
        return None

    return SysConfig(session, fetch=fetch)


async def _ci(world: CmdbWorld, *, group_id: int = GROUP_RO) -> int:
    async with world.factory() as s:
        return await make_ci(s, await make_class(s, group_id=group_id), "pm-link-ci")


async def _link_add(
    world: CmdbWorld, ci_id: int, *, user_id: int = USER_A, extra: dict[str, Any] | None = None
) -> list[SkippedAction]:
    config = {
        "SourceObject": "Ticket",
        "SourceKey": str(TICKET_OPEN),
        "TargetObject": "ITSMConfigItem",
        "TargetKey": str(ci_id),
        "Type": "RelevantTo",
        **(extra or {}),
    }
    skipped: list[SkippedAction] = []
    async with world.factory() as session:
        result = await execute_transition_action(
            session,
            action=TransitionActionConfig(module=MODULE, config=config),
            ticket_id=TICKET_OPEN,
            process_entity_id="Process-pm",
            activity_entity_id="Activity-pm",
            transition_entity_id="Transition-pm",
            user_id=user_id,
            sysconfig=_sysconfig(session),
            skipped=skipped,
        )
        await session.commit()
    assert result is None  # LinkAdd is a supported action in every mode
    return skipped


async def _cmdb_links(world: CmdbWorld) -> list[tuple[str, str, str, str, str]]:
    async with world.factory() as s:
        rows = (await s.execute(select(TiqoraItsmLink).order_by(TiqoraItsmLink.id))).scalars()
        return [
            (r.source_kind, r.source_key, r.target_kind, r.target_key, r.link_type) for r in rows
        ]


def _znuny_links(world: CmdbWorld) -> int:
    return int(
        world.sql(
            "SELECT COUNT(*) FROM link_relation"
            " WHERE source_key = '740300' OR target_key = '740300'"
        )[0][0]
    )


def _outbox(world: CmdbWorld) -> list[tuple[str, dict[str, Any]]]:
    rows = world.sql(
        "SELECT event_type, payload FROM tiqora_event_outbox WHERE ticket_id = 740300 ORDER BY id"
    )
    return [(str(e), json.loads(p)) for e, p in rows]


async def test_mode_tiqora_links_through_the_cmdb_link_service(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _ci(cmdb_db)
    assert await _link_add(cmdb_db, ci_id) == []
    links = await _cmdb_links(cmdb_db)
    assert len(links) == 1
    assert {(links[0][0], links[0][1]), (links[0][2], links[0][3])} == {
        ("ci", str(ci_id)),
        ("ticket", str(TICKET_OPEN)),
    }
    assert links[0][4] == "RelevantTo"
    assert _znuny_links(cmdb_db) == 0  # nothing in Znuny's link_relation
    assert _outbox(cmdb_db) == [
        ("LinkAdd", {"target_object": "ITSMConfigItem", "target_key": str(ci_id)})
    ]
    # idempotent like the API: a second run adds no second row
    assert await _link_add(cmdb_db, ci_id) == []
    assert len(await _cmdb_links(cmdb_db)) == 1


async def test_mode_tiqora_ci_end_may_come_first(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _ci(cmdb_db)
    swapped = {
        "SourceObject": "ITSMConfigItem",
        "SourceKey": str(ci_id),
        "TargetObject": "Ticket",
        "TargetKey": str(TICKET_OPEN),
    }
    assert await _link_add(cmdb_db, ci_id, extra=swapped) == []
    assert len(await _cmdb_links(cmdb_db)) == 1
    assert _znuny_links(cmdb_db) == 0


async def test_mode_tiqora_actor_needs_rw_on_the_ticket_queue(cmdb_db: CmdbWorld) -> None:
    """R12: USER_B has only ``ro`` (no group of the queue): refused, nothing written."""
    set_mode(cmdb_db, "tiqora")
    ci_id = await _ci(cmdb_db)
    skipped = await _link_add(cmdb_db, ci_id, user_id=USER_B)
    assert [(s.module, s.reason) for s in skipped] == [("LinkAdd", "link_refused")]
    assert "Rechte" in skipped[0].detail
    assert await _cmdb_links(cmdb_db) == []
    assert _znuny_links(cmdb_db) == 0
    assert _outbox(cmdb_db) == []


async def test_mode_tiqora_uses_the_overridden_user_as_actor(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _ci(cmdb_db)
    skipped = await _link_add(cmdb_db, ci_id, user_id=USER_A, extra={"UserID": str(USER_B)})
    assert [s.reason for s in skipped] == ["link_refused"]
    assert await _cmdb_links(cmdb_db) == []


async def test_mode_tiqora_actor_needs_ro_on_the_ci_class(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _ci(cmdb_db, group_id=GROUP_HIDDEN)  # USER_A has no right in that group
    skipped = await _link_add(cmdb_db, ci_id)
    assert [s.reason for s in skipped] == ["link_refused"]
    assert await _cmdb_links(cmdb_db) == []


async def test_mode_tiqora_unknown_ci_or_type_is_a_skip_not_an_error(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _ci(cmdb_db)
    unknown_ci = await _link_add(cmdb_db, 999999)
    assert [s.reason for s in unknown_ci] == ["link_refused"]
    unknown_type = await _link_add(cmdb_db, ci_id, extra={"Type": "GibtEsNicht"})
    assert [s.reason for s in unknown_type] == ["link_refused"]
    assert "GibtEsNicht" in unknown_type[0].detail
    assert await _cmdb_links(cmdb_db) == []


async def test_mode_tiqora_other_object_kinds_are_refused(cmdb_db: CmdbWorld) -> None:
    set_mode(cmdb_db, "tiqora")
    ci_id = await _ci(cmdb_db)
    skipped = await _link_add(cmdb_db, ci_id, extra={"SourceObject": "FAQ", "SourceKey": "5"})
    assert [s.reason for s in skipped] == ["link_refused"]
    assert "FAQ" in skipped[0].detail


class _LinkObjectsSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, session: AsyncSession, **kw: Any) -> bool:
        self.calls.append((kw["source_object"], kw["target_object"]))
        return True


async def test_mode_znuny_readthrough_keeps_the_znuny_link_path(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mode(cmdb_db, "znuny_readthrough")
    spy = _LinkObjectsSpy()
    monkeypatch.setattr(process_engine, "link_objects", spy)
    ci_id = await _ci(cmdb_db)
    assert await _link_add(cmdb_db, ci_id) == []
    assert spy.calls == [("Ticket", "ITSMConfigItem")]
    assert await _cmdb_links(cmdb_db) == []  # the Tiqora CMDB is untouched


async def test_mode_off_refuses_ci_ends_with_a_german_message(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mode(cmdb_db, "off")
    spy = _LinkObjectsSpy()
    monkeypatch.setattr(process_engine, "link_objects", spy)
    ci_id = await _ci(cmdb_db)
    skipped = await _link_add(cmdb_db, ci_id)
    assert skipped == [
        SkippedAction(
            module="LinkAdd",
            reason="itsm_off",
            detail=(
                "LinkAdd: ITSM ist ausgeschaltet; Verknüpfungen mit Konfigurationseinträgen"
                " werden nicht angelegt."
            ),
        )
    ]
    assert spy.calls == []
    assert await _cmdb_links(cmdb_db) == []
    assert _znuny_links(cmdb_db) == 0


async def test_mode_off_still_links_tickets_with_each_other(
    cmdb_db: CmdbWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_mode(cmdb_db, "off")
    spy = _LinkObjectsSpy()
    monkeypatch.setattr(process_engine, "link_objects", spy)
    ci_id = await _ci(cmdb_db)
    ticket_pair = {"TargetObject": "Ticket", "TargetKey": "740302"}
    assert await _link_add(cmdb_db, ci_id, extra=ticket_pair) == []
    assert spy.calls == [("Ticket", "Ticket")]


async def test_skipped_ci_link_does_not_touch_the_ticket(cmdb_db: CmdbWorld) -> None:
    """The refusal happens before any write: the savepoint holds nothing."""
    set_mode(cmdb_db, "tiqora")
    ci_id = await _ci(cmdb_db)
    before = cmdb_db.sql("SELECT change_time FROM ticket WHERE id = 740300")
    await _link_add(cmdb_db, ci_id, user_id=USER_B)
    assert cmdb_db.sql("SELECT change_time FROM ticket WHERE id = 740300") == before
