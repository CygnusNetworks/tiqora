"""ITSM built-in reports (category ``itsm``): definitions only, no database."""

from __future__ import annotations

from tiqora.extensions.core import register_core
from tiqora.extensions.registry import Registry
from tiqora.reporting.catalogue import BUILTIN_REPORTS, QUEUE_PARAM_FIELD, BuiltinReport
from tiqora.reporting.catalogue_itsm import ITSM_EXPECTED, itsm_builtin_reports
from tiqora.reporting.definition import validate_definition


def _registry() -> Registry:
    reg = Registry()
    register_core(reg)
    return reg


def _by_name() -> dict[str, BuiltinReport]:
    return {r.name: r for r in itsm_builtin_reports()}


def test_itsm_builtins_order_and_category() -> None:
    reports = itsm_builtin_reports()
    assert [r.name for r in reports] == ITSM_EXPECTED
    assert len(ITSM_EXPECTED) == 16
    assert ITSM_EXPECTED.index("itsm_change_transitions") == (
        ITSM_EXPECTED.index("itsm_change_outcomes") + 1
    )
    assert {r.category for r in reports} == {"itsm"}
    assert not any(r.overview for r in reports)
    assert all(r.key == f"builtin.{r.name}" for r in reports)
    registered = [r.key for r in _registry().items(BUILTIN_REPORTS)]
    assert registered[-16:] == [f"builtin.{n}" for n in ITSM_EXPECTED]


def test_itsm_builtins_validate() -> None:
    reg = _registry()
    for r in itsm_builtin_reports():
        src = validate_definition(r.definition, reg)
        assert src.key == r.definition.source, r.key


def test_itsm_builtin_definitions() -> None:
    by = _by_name()
    fls = by["itsm_first_level_solution"].definition
    assert fls.filter is not None
    assert fls.filter.where.model_dump(exclude_none=True) == {
        "all": [
            {
                "field": "itsm_role",
                "op": "in",
                "value": ["incident", "major_incident", "service_request"],
            },
            {"field": "state_type", "op": "in", "value": ["closed"]},
        ]
    }
    assert [m.key for m in fls.measures] == [
        "tickets",
        "first_level_solved",
        "first_level_solution_rate",
    ]
    cis = by["cmdb_cis_by_class"].definition
    assert cis.time_range is None and cis.parameters == []
    assert by["cmdb_cis_created"].definition.time_field == "created"
    per_class = by["itsm_changes_per_ci_class"].definition
    assert per_class.filter is not None
    assert per_class.filter.where.model_dump(exclude_none=True) == {
        "field": "is_change",
        "op": "is_true",
    }
    assert per_class.visualization.chart_measures == [0]
    prone = by["itsm_incident_prone_cis"].definition
    assert prone.dimensions[0].key == "ci" and prone.dimensions[0].top_n == 20
    assert prone.dimensions[0].choices == ["ci_class"]
    assert by["itsm_open_by_type"].definition.time_range is None
    transitions = by["itsm_change_transitions"].definition
    assert transitions.source == "change_events"
    assert [d.key for d in transitions.dimensions] == ["occurred", "to_phase"]
    assert transitions.dimensions[0].grain == "day"
    assert transitions.filter is not None
    assert transitions.filter.where.model_dump(exclude_none=True) == {
        "field": "to_phase",
        "op": "in",
        "value": ["rejected", "cancelled"],
    }


def test_queue_parameter_where_queue_scoped() -> None:
    assert {"tickets", "open_tickets", "changes", "change_events", "ci_tickets"} <= set(
        QUEUE_PARAM_FIELD
    )
    assert "config_items" not in QUEUE_PARAM_FIELD
    for r in itsm_builtin_reports():
        keys = [p.key for p in r.definition.parameters]
        if r.definition.source == "config_items":
            assert keys == [], r.key
        else:
            assert keys == ["queues"], r.key
            assert r.definition.parameters[0].field == QUEUE_PARAM_FIELD[r.definition.source]
