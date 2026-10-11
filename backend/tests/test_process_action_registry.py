"""TransitionAction registry: names, resolution, parameter specs (no DB)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tiqora.extensions.core import get_registry, register_core
from tiqora.extensions.registry import DuplicateExtensionError, Registry
from tiqora.process.actions import (
    TRANSITION_ACTIONS,
    TransitionActionModule,
    core_transition_actions,
    resolve_action,
)
from tiqora.process.config import TransitionActionConfig
from tiqora.process.engine import execute_transition_action
from tiqora.znuny.sysconfig import SysConfig

# Every module under Kernel/System/ProcessManagement/TransitionAction/ in Znuny 6.5.22
# (minus Base.pm). Literal so the check runs where the reference tree is absent (CI,
# worktrees); the tree comparison below is an extra check.
ZNUNY_TRANSITION_ACTIONS = frozenset(
    {
        "AppointmentCreate",
        "AppointmentRemove",
        "AppointmentUpdate",
        "ArticleSend",
        "ConfigItemUpdate",
        "DynamicFieldIncrement",
        "DynamicFieldPendingTimeSet",
        "DynamicFieldRemove",
        "DynamicFieldSet",
        "ExecuteInvoker",
        "LinkAdd",
        "TicketArticleCreate",
        "TicketCreate",
        "TicketCustomerSet",
        "TicketLockSet",
        "TicketOwnerSet",
        "TicketPrioritySet",
        "TicketQueueSet",
        "TicketResponsibleSet",
        "TicketSLASet",
        "TicketServiceSet",
        "TicketStateSet",
        "TicketTitleSet",
        "TicketTypeSet",
        "TicketWatchSet",
    }
)

# Run by Tiqora today (the former engine._ACTION_HANDLERS keys).
IMPLEMENTED = frozenset(
    {
        "TicketStateSet",
        "TicketQueueSet",
        "TicketOwnerSet",
        "TicketPrioritySet",
        "TicketTitleSet",
        "TicketCustomerSet",
        "TicketResponsibleSet",
        "TicketLockSet",
        "DynamicFieldSet",
        "DynamicFieldRemove",
        "DynamicFieldIncrement",
        "DynamicFieldPendingTimeSet",
        "TicketArticleCreate",
        "ArticleSend",
        "TicketTypeSet",
        "TicketServiceSet",
        "TicketSLASet",
        "TicketWatchSet",
        "LinkAdd",
        "TicketCreate",
    }
)


def _sysconfig() -> SysConfig:
    async def _fetch(name: str) -> Any:
        del name
        return None

    return SysConfig(fetch=_fetch)


def test_registry_has_every_znuny_module_name() -> None:
    names = {m.name for m in get_registry().items(TRANSITION_ACTIONS)}
    assert names >= ZNUNY_TRANSITION_ACTIONS


def test_registry_matches_znuny_reference_tree() -> None:
    ref = (
        Path(__file__).resolve().parents[2]
        / "znuny-6.5.22"
        / "Kernel/System/ProcessManagement/TransitionAction"
    )
    if not ref.is_dir():
        pytest.skip("Znuny reference tree not present (gitignored; CI)")
    znuny = {p.stem for p in ref.glob("*.pm")} - {"Base"}
    assert znuny == ZNUNY_TRANSITION_ACTIONS


def test_core_list_runs_exactly_the_implemented_handlers() -> None:
    modules = core_transition_actions()
    assert len({m.name for m in modules}) == len(modules)
    assert {m.name for m in modules if m.run is not None} == IMPLEMENTED
    unsupported = {m.name for m in modules if m.run is None}
    assert unsupported == {
        "ExecuteInvoker",
        "ConfigItemUpdate",
        "AppointmentCreate",
        "AppointmentUpdate",
        "AppointmentRemove",
    }
    assert not any(m.tiqora_only for m in modules)


def test_resolve_action_accepts_full_perl_path_and_short_name() -> None:
    full = resolve_action("Kernel::System::ProcessManagement::TransitionAction::TicketStateSet")
    assert full is not None
    assert full.name == "TicketStateSet"
    assert full.znuny_module == (
        "Kernel::System::ProcessManagement::TransitionAction::TicketStateSet"
    )
    assert resolve_action("TicketStateSet") is full
    assert resolve_action("NoSuchModule") is None
    assert resolve_action("Kernel::System::ProcessManagement::TransitionAction::Nope") is None


def test_param_specs_match_pod_required_keys() -> None:
    state = resolve_action("TicketStateSet")
    assert state is not None
    assert any(p.one_of == ("State", "StateID") and p.required for p in state.params)
    df = resolve_action("DynamicFieldSet")
    assert df is not None
    # DF names are free keys → raw form only (POD: "DynamicField_X => value")
    assert df.params == ()

    create = resolve_action("AppointmentCreate")
    assert create is not None
    by_name = {p.name: p for p in create.params}
    assert by_name["CalendarName"].one_of == ("CalendarName", "CalendarID")
    assert by_name["CalendarName"].required
    assert {"Title", "StartTime", "EndTime"} <= {p.name for p in create.params if p.required}
    assert by_name["TeamID"].list_value and by_name["ResourceID"].list_value
    assert by_name["StartTime"].kind == "datetime"

    title = resolve_action("TicketTitleSet")
    assert title is not None
    assert [(p.name, p.kind, p.required) for p in title.params] == [("Title", "text", True)]


def test_user_id_is_implicit_never_a_param() -> None:
    for module in core_transition_actions():
        keys = {p.name for p in module.params} | {k for p in module.params for k in p.one_of}
        assert "UserID" not in keys, module.name
        assert "DynamicField_UserID" not in keys, module.name


def test_one_of_groups_start_with_their_own_name() -> None:
    for module in core_transition_actions():
        for p in module.params:
            if p.one_of:
                assert p.one_of[0] == p.name, (module.name, p.name)
                assert len(p.one_of) >= 2, (module.name, p.name)


async def test_unsupported_module_is_reported_not_raised() -> None:
    # run=None module → execute_transition_action returns its short name before
    # touching the session (a bare object would raise on any attribute access).
    result = await execute_transition_action(
        cast(AsyncSession, object()),
        action=TransitionActionConfig(
            module="Kernel::System::ProcessManagement::TransitionAction::ConfigItemUpdate",
            config={"ConfigItemID": "1"},
        ),
        ticket_id=1,
        process_entity_id="P",
        activity_entity_id="A",
        transition_entity_id="T",
        user_id=1,
        sysconfig=_sysconfig(),
    )
    assert result == "ConfigItemUpdate"


async def test_unknown_module_is_reported_not_raised() -> None:
    result = await execute_transition_action(
        cast(AsyncSession, object()),
        action=TransitionActionConfig(module="Kernel::System::Custom::Frobnicate", config={}),
        ticket_id=1,
        process_entity_id="P",
        activity_entity_id="A",
        transition_entity_id="T",
        user_id=1,
        sysconfig=_sysconfig(),
    )
    assert result == "Frobnicate"


def test_plugin_registration_is_rejected_on_duplicate_name() -> None:
    reg = Registry()
    register_core(reg)
    with pytest.raises(DuplicateExtensionError):
        reg.add(TRANSITION_ACTIONS, TransitionActionModule(name="TicketStateSet", run=None))
