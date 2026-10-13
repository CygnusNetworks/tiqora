"""``ConfigItemUpdate`` transition action on the Tiqora CMDB (Task 20; both dialects).

Rows come from the CMDB world of ``_cmdb_helpers`` (band 740000, names ``cmdb4``); the 56400-56429
band of the plan only names the CI numbers' tail, the numbers themselves are generated.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _cmdb_helpers
from tests._cmdb_helpers import (
    CUSTOMER_A,
    TICKET_OPEN,
    USER_A,
    USER_ADMIN,
    USER_B,
    CmdbWorld,
    make_ci,
    make_class,
    set_mode,
)
from tiqora.cmdb.seed import DEPLOYMENT_STATE
from tiqora.cmdb.service import CiService, system_access
from tiqora.itsm.config_item_update import (
    CHANGE_NOTE,
    UnsupportedInMode,
    action_config_item_update,
)
from tiqora.process.actions.registry import ActionUnsupported, resolve_action
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

cmdb_db = _cmdb_helpers.cmdb_db
_cmdb_rows_reset = _cmdb_helpers._cmdb_rows_reset

MODULE = "Kernel::System::ProcessManagement::TransitionAction::ConfigItemUpdate"


def _sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        del name
        return None

    return SysConfig(fetch=_fetch)


async def _run(s: AsyncSession, config: dict[str, Any], user_id: int = USER_A) -> None:
    await action_config_item_update(s, config, TICKET_OPEN, user_id, _sysconfig())
    await s.commit()


async def _record(s: AsyncSession, ci_id: int) -> Any:
    return await CiService(s).get(system_access(USER_ADMIN), ci_id, enforce_permissions=False)


async def _two_cis(world: CmdbWorld) -> tuple[int, int, str, str]:
    set_mode(world, "tiqora")
    async with world.factory() as s:
        cid = await make_class(s)
        a = await make_ci(s, cid, "cmdb4-one", attributes={"Vendor": [{"v": "Old"}]})
        b = await make_ci(s, cid, "cmdb4-two", attributes={"Vendor": [{"v": "Old"}]})
        return a, b, (await _record(s, a)).number, (await _record(s, b)).number


async def test_numbers_update_both_cis_as_the_acting_user(cmdb_db: CmdbWorld) -> None:
    a, b, num_a, num_b = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        await _run(s, {"ConfigItemNumber": f"{num_a}, {num_b}", "Vendor::1": "Acme"})
        for ci_id in (a, b):
            rec = await _record(s, ci_id)
            assert rec.attributes["Vendor"] == [{"v": "Acme"}]
            assert rec.version_count == 2 and rec.changed_by == USER_A
            versions = await CiService(s).list_versions(
                system_access(USER_ADMIN), ci_id, enforce_permissions=False
            )
            assert versions.items[0].change_note == CHANGE_NOTE


async def test_user_id_in_config_overrides_the_acting_user(cmdb_db: CmdbWorld) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        await _run(s, {"ConfigItemID": str(a), "Vendor::1": "Acme", "UserID": str(USER_B)})
        rec = await _record(s, a)
        assert rec.changed_by == USER_B and rec.attributes["Vendor"] == [{"v": "Acme"}]


async def test_duplicate_ids_make_one_version(cmdb_db: CmdbWorld) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        await _run(s, {"ConfigItemID": f"{a}, {a},{a}", "Vendor::1": "Acme"})
        assert (await _record(s, a)).version_count == 2


async def test_unknown_number_is_logged_and_the_other_ci_still_updates(
    cmdb_db: CmdbWorld, caplog: pytest.LogCaptureFixture
) -> None:
    a, _b, num_a, _nb = await _two_cis(cmdb_db)
    caplog.set_level(logging.WARNING, logger="tiqora.itsm.config_item_update")
    async with cmdb_db.factory() as s:
        await _run(s, {"ConfigItemNumber": f"no-such-number, {num_a}", "Vendor::1": "Acme"})
        assert (await _record(s, a)).attributes["Vendor"] == [{"v": "Acme"}]
    assert "no-such-number" in caplog.text


async def test_unknown_id_does_not_stop_the_others(cmdb_db: CmdbWorld) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        await _run(s, {"ConfigItemID": f"999999999, {a}", "Vendor::1": "Acme"})
        assert (await _record(s, a)).attributes["Vendor"] == [{"v": "Acme"}]


async def test_unchanged_data_writes_no_version(cmdb_db: CmdbWorld) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        await _run(s, {"ConfigItemID": str(a), "Vendor::1": "Old"})
        rec = await _record(s, a)
        assert rec.version_count == 1 and rec.changed_by == USER_A


async def test_unknown_state_name_logs_and_changes_nothing(
    cmdb_db: CmdbWorld, caplog: pytest.LogCaptureFixture
) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    caplog.set_level(logging.WARNING, logger="tiqora.itsm.config_item_update")
    async with cmdb_db.factory() as s:
        before = await _record(s, a)
        await _run(s, {"ConfigItemID": str(a), "DeplStateName": "No such state", "Vendor::1": "X"})
        after = await _record(s, a)
        assert after.version_count == 1
        assert after.deployment_state_id == before.deployment_state_id
        assert after.attributes["Vendor"] == [{"v": "Old"}]
    assert "No such state" in caplog.text


async def test_state_name_and_header_name_are_applied(cmdb_db: CmdbWorld) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        before = await _record(s, a)
        await _run(s, {"ConfigItemID": str(a), "DeplStateName": "Expired", "Name": "cmdb4-renamed"})
        after = await _record(s, a)
        assert after.name == "cmdb4-renamed" and after.version_count == 2
        assert after.deployment_state_id != before.deployment_state_id
        names = await CiService(s)._catalog({after.deployment_state_id})
        assert names[after.deployment_state_id][:2] == (DEPLOYMENT_STATE, "Expired")


async def test_customer_attribute_resolves_a_unique_customer(cmdb_db: CmdbWorld) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        await _run(s, {"ConfigItemID": str(a), "Owner::1": CUSTOMER_A.split("@")[0]})
        assert (await _record(s, a)).attributes["Owner"] == [{"v": CUSTOMER_A}]


async def test_invalid_value_rolls_back_to_the_savepoint_and_keeps_the_session(
    cmdb_db: CmdbWorld, caplog: pytest.LogCaptureFixture
) -> None:
    a, b, _na, _nb = await _two_cis(cmdb_db)
    caplog.set_level(logging.WARNING, logger="tiqora.itsm.config_item_update")
    async with cmdb_db.factory() as s:
        # an integer attribute with text fails the CMDB validation: logged, nothing written
        await action_config_item_update(
            s, {"ConfigItemID": f"{a},{b}", "CPU::1": "many"}, TICKET_OPEN, USER_A, _sysconfig()
        )
        # the transaction is still usable (PostgreSQL would be aborted without the savepoint)
        await _run(s, {"ConfigItemID": str(b), "Vendor::1": "Acme"})
        assert (await _record(s, a)).version_count == 1
        assert (await _record(s, b)).attributes["Vendor"] == [{"v": "Acme"}]
    assert "not updated" in caplog.text


async def test_missing_ci_keys_is_logged_not_raised(
    cmdb_db: CmdbWorld, caplog: pytest.LogCaptureFixture
) -> None:
    await _two_cis(cmdb_db)
    caplog.set_level(logging.WARNING, logger="tiqora.itsm.config_item_update")
    async with cmdb_db.factory() as s:
        await _run(s, {"Vendor::1": "Acme"})
    assert "ConfigItemID or ConfigItemNumber" in caplog.text


async def test_registry_runs_the_handler() -> None:
    entry = resolve_action("ConfigItemUpdate")
    assert entry is not None and entry.run is action_config_item_update
    assert {p.name for p in entry.params} >= {
        "ConfigItemID",
        "Name",
        "DeplStateName",
        "InciStateName",
        "Separator",
    }
    assert issubclass(UnsupportedInMode, ActionUnsupported)


async def test_engine_runs_the_action_in_mode_tiqora(cmdb_db: CmdbWorld) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    async with cmdb_db.factory() as s:
        result = await execute_transition_action(
            s,
            action=TransitionActionConfig(
                module=MODULE, config={"ConfigItemID": str(a), "Vendor::1": "Acme"}
            ),
            ticket_id=TICKET_OPEN,
            process_entity_id="P",
            activity_entity_id="A",
            transition_entity_id="T",
            user_id=USER_A,
            sysconfig=_sysconfig(),
        )
        await s.commit()
        assert result is None
        assert (await _record(s, a)).attributes["Vendor"] == [{"v": "Acme"}]


async def test_engine_reports_unsupported_outside_mode_tiqora(cmdb_db: CmdbWorld) -> None:
    a, _b, _na, _nb = await _two_cis(cmdb_db)
    set_mode(cmdb_db, "znuny_readthrough")
    async with cmdb_db.factory() as s:
        result = await execute_transition_action(
            s,
            action=TransitionActionConfig(
                module=MODULE, config={"ConfigItemID": str(a), "Vendor::1": "Acme"}
            ),
            ticket_id=TICKET_OPEN,
            process_entity_id="P",
            activity_entity_id="A",
            transition_entity_id="T",
            user_id=USER_A,
            sysconfig=_sysconfig(),
        )
        assert result == "ConfigItemUpdate"
        rec = await _record(s, a)
        assert rec.version_count == 1 and rec.attributes["Vendor"] == [{"v": "Old"}]
