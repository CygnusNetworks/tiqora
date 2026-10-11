"""Pure tests for ``tiqora.process.condition_modules`` and ``Type: Module`` conditions."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tiqora.extensions.registry import Registry
from tiqora.process import conditions
from tiqora.process.condition_modules import (
    CONDITION_MODULES,
    ConditionModule,
    core_condition_modules,
    resolve_condition_module,
)
from tiqora.process.conditions import evaluate_condition_field, evaluate_transition
from tiqora.process.config import TransitionConditionField, TransitionConfig

DEMO = "Kernel::System::ProcessManagement::TransitionValidation::ValidateDemo"


def _module_field(match: str) -> TransitionConditionField:
    return TransitionConditionField(match=match, type_="Module")


def test_validate_demo_matches_queue_raw_only() -> None:
    assert evaluate_condition_field("Any", _module_field(DEMO), {"Queue": "Raw"})
    assert not evaluate_condition_field("Any", _module_field(DEMO), {"Queue": "Junk"})
    assert not evaluate_condition_field("Any", _module_field(DEMO), {"Queue": "raw"})
    assert not evaluate_condition_field("Any", _module_field(DEMO), {})
    assert [m.match for m in core_condition_modules()] == [DEMO]


def test_match_with_trailing_pm_and_whitespace_resolves() -> None:
    for variant in (DEMO + ".pm", f"  {DEMO}\n", f" {DEMO}.pm "):
        module = resolve_condition_module(variant)
        assert module is not None and module.match == DEMO
        assert evaluate_condition_field("Any", _module_field(variant), {"Queue": "Raw"})
    assert resolve_condition_module(None) is None
    assert resolve_condition_module(["x"]) is None


def test_unknown_module_does_not_match_and_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    conditions._WARNED_MODULES.discard("Some::Missing::Module")
    field = _module_field("Some::Missing::Module")
    with caplog.at_level(logging.WARNING, logger="tiqora.process.conditions"):
        assert not evaluate_condition_field("Any", field, {"Queue": "Raw"})
        assert not evaluate_condition_field("Any", field, {"Queue": "Raw"})
    messages = [r.getMessage() for r in caplog.records if "not available" in r.getMessage()]
    assert messages == ["transition condition module not available: Some::Missing::Module"]


def test_module_condition_inside_or_group_with_string_condition() -> None:
    raw: dict[str, Any] = {
        "ConditionLinking": "and",
        "Condition": {
            "1": {
                "Type": "or",
                "Fields": {
                    "Check": {"Type": "Module", "Match": DEMO},
                    "State": {"Type": "String", "Match": "open"},
                },
            }
        },
    }
    cfg = TransitionConfig.from_dict(raw)
    assert evaluate_transition(cfg, {"Queue": "Raw", "State": "closed"})  # module only
    assert evaluate_transition(cfg, {"Queue": "Junk", "State": "open"})  # string only
    assert not evaluate_transition(cfg, {"Queue": "Junk", "State": "closed"})


def test_registry_add_resolves_new_module() -> None:
    registry = Registry()
    for m in core_condition_modules():
        registry.add(CONDITION_MODULES, m)
    custom = ConditionModule(
        "Tiqora::OnlyEven", lambda data, field: str(data.get(field)) in {"2", "4"}, tiqora_only=True
    )
    registry.add(CONDITION_MODULES, custom)
    resolved = resolve_condition_module("Tiqora::OnlyEven", registry)
    assert resolved is custom
    assert resolved.validate({"N": "2"}, "N") and not resolved.validate({"N": "3"}, "N")
    assert resolve_condition_module("Tiqora::Nope", registry) is None
