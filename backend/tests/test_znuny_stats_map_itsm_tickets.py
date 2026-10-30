"""Znuny ITSM stats: first-level solution rate and ITSM solution time (plan Task 10). Pure."""

from __future__ import annotations

from dataclasses import replace

from tests.test_znuny_itsm_stats_inventory import itsm_fixture
from tests.test_znuny_stats_map_matrix import LOOKUPS, _stat, el
from tiqora.filters import AllOf, Condition
from tiqora.reporting.definition import RelativeTimeRange
from tiqora.reporting.znuny_stats.map_itsm import FIRST_LEVEL, SOLUTION_AVERAGE
from tiqora.reporting.znuny_stats.mapper import map_stat
from tiqora.reporting.znuny_stats.mapping_common import (
    FieldMap,
    ImportIssue,
    file_ids_unreliable,
    resolve_values,
)

_NO_PERIOD = "Ohne Zeitraum zeigt Tiqora die letzten 30 Tage."
_IDS_AS_STORED = "IDs wie in Znuny hinterlegt – bitte Typ und Status prüfen."
_FLS_OBJECT = (
    "Znuny zeigt die Anzahl der im ersten Kontakt gelösten Tickets; Tiqora zeigt dazu die Quote."
)
_SOLUTION_OBJECT = (
    "Znuny zählt die Zeit in den Status von Ticket::ViewableStateType; "
    "Tiqora die Zeit zwischen Erstellen bzw. Wiedereröffnen und Schließen."
)
_FLS = {"object_module": FIRST_LEVEL}
_SOL = {"object_module": SOLUTION_AVERAGE}


def _conditions(where: object) -> list[Condition]:
    if isinstance(where, AllOf):
        return [c for c in where.all if isinstance(c, Condition)]
    assert isinstance(where, Condition)
    return [where]


def _issues(m, severity: str | None = None) -> set[tuple[str, str]]:
    return {(i.element, i.reason) for i in m.issues if severity in (None, i.severity)}


def test_existing_lookups_fixture_still_valid() -> None:
    assert LOOKUPS.itsm_mode == "off"
    assert LOOKUPS.cmdb_class_by_znuny_id == {}
    assert LOOKUPS.cmdb_catalog_by_znuny_id == {}
    assert LOOKUPS.cmdb_class_ids_by_name == {}
    assert LOOKUPS.change_state_names == {}


def test_first_level_db_stat_200() -> None:
    stat = _stat(
        el("x", "TypeIDs", "2", "4", "3"),
        el("series", "PriorityIDs"),
        el("restriction", "StateIDs", "2", "10"),
        **_FLS,
    )
    m = map_stat(stat, LOOKUPS, is_admin=False)
    assert m.target == "report" and m.definition is not None
    d = m.definition
    assert d.source == "tickets"
    assert [x.key for x in d.dimensions] == ["type", "priority"]
    assert [x.key for x in d.measures] == ["first_level_solved", "first_level_solution_rate"]
    assert d.filter is not None
    conds = _conditions(d.filter.where)
    assert [(c.field, c.op, c.value) for c in conds] == [
        ("type", "in", [2, 4, 3]),
        ("state", "in", [2, 10]),
    ]
    assert d.parameters == []
    approx = _issues(m, "approximated")
    assert ("UseAsXvalue.TypeIDs", _IDS_AS_STORED) in approx
    assert ("UseAsRestriction.StateIDs", _IDS_AS_STORED) in approx
    assert ("Object", _FLS_OBJECT) in approx
    assert ("UseAsRestriction", _NO_PERIOD) in approx
    assert not _issues(m, "dropped")


def test_first_level_default_closed_state() -> None:
    m = map_stat(_stat(el("x", "TypeIDs", "2"), **_FLS), LOOKUPS, is_admin=False)
    assert m.definition is not None and m.definition.filter is not None
    conds = _conditions(m.definition.filter.where)
    assert [(c.field, c.op, c.value) for c in conds] == [
        ("type", "in", [2]),
        ("state_type", "in", ["closed"]),
    ]


def test_first_level_file_adds_closed_default() -> None:
    m = map_stat(
        itsm_fixture("ITSMStats-200-ITSMTicketFirstLevelSolutionRate.xml"),
        LOOKUPS,
        is_admin=False,
    )
    assert m.target == "report" and m.definition is not None
    d = m.definition
    assert sorted(p.field for p in d.parameters) == ["state", "type"]
    assert (
        "UseAsRestriction.StateIDs",
        "Der Bericht zählt immer nur geschlossene Tickets; "
        "wählen Sie einen offenen Status, bleibt das Ergebnis leer.",
    ) in _issues(m, "approximated")
    assert all(p.default is None and p.op == "in" for p in d.parameters)
    assert (
        "UseAsRestriction.StateIDs",
        "IDs aus einer Exportdatei lassen sich nicht zuordnen. "
        "Solange Sie keine Auswahl treffen, zählt der Bericht alle Werte.",
    ) in (_issues(m, "approximated"))
    assert d.filter is not None
    conds = _conditions(d.filter.where)
    assert [(c.field, c.op, c.value) for c in conds] == [("state_type", "in", ["closed"])]


def test_first_level_file_state_ids_become_parameter() -> None:
    # F4: Import/Export wrappers keep ids in these files, so nothing is looked up by name
    lookups = replace(LOOKUPS, state_ids_by_name={"2": 99})
    m = map_stat(
        itsm_fixture("ITSMStats-200-ITSMTicketFirstLevelSolutionRate.xml"),
        lookups,
        is_admin=False,
    )
    assert m.definition is not None
    assert sorted(p.field for p in m.definition.parameters) == ["state", "type"]
    assert m.definition.filter is not None
    assert [c.field for c in _conditions(m.definition.filter.where)] == ["state_type"]
    assert not [i for i in m.issues if "gibt es in diesem System nicht" in i.reason]
    assert not _issues(m, "dropped")


def test_first_level_file_203() -> None:
    m = map_stat(
        itsm_fixture("ITSMStats-203-ITSMTicketFirstLevelSolutionRate.xml"),
        LOOKUPS,
        is_admin=False,
    )
    assert m.target == "report" and m.definition is not None
    d = m.definition
    assert [(x.key, x.grain) for x in d.dimensions] == [("created", "day"), ("type", None)]
    assert d.time_field == "created"
    assert d.time_range == RelativeTimeRange(unit="month", count=1)
    assert "type" in {p.field for p in d.parameters}
    assert ("Object", _FLS_OBJECT) in _issues(m, "approximated")
    assert ("UseAsRestriction", _NO_PERIOD) not in _issues(m)


def test_first_level_drops_text_elements() -> None:
    m = map_stat(
        _stat(el("x", "TypeIDs", "2"), el("restriction", "Subject", "x"), **_FLS),
        LOOKUPS,
        is_admin=False,
    )
    assert ("UseAsRestriction.Subject", "Wird in Berichten nicht unterstützt.") in _issues(
        m, "dropped"
    )
    assert m.definition is not None


def test_solution_average_300_and_307() -> None:
    for number, name in ((300, "ITSMStats-300"), (307, "ITSMStats-307")):
        m = map_stat(
            itsm_fixture(f"{name}-ITSMTicketSolutionTimeAverage.xml"), LOOKUPS, is_admin=False
        )
        assert m.target == "report" and m.definition is not None, number
        d = m.definition
        assert d.source == "tickets"
        assert [x.key for x in d.measures] == ["itsm_solution_avg"]
        assert ("Object", _SOLUTION_OBJECT) in _issues(m, "approximated")
        assert d.filter is not None
        assert ("state_type", "in", ["closed"]) in [
            (c.field, c.op, c.value) for c in _conditions(d.filter.where)
        ]
        if number == 307:
            assert d.time_field == "created"
            assert d.time_range == RelativeTimeRange(unit="month", count=1)
        else:
            assert d.time_range is None
            assert ("UseAsRestriction", _NO_PERIOD) in _issues(m)


def test_cmdb_value_kinds_resolve() -> None:
    lookups = replace(
        LOOKUPS,
        cmdb_class_by_znuny_id={7: 31},
        cmdb_catalog_by_znuny_id={21: 41},
        cmdb_class_ids_by_name={"computer": 31},
    )
    stat = _stat(origin="db")
    issues: list[ImportIssue] = []
    got = resolve_values(
        el("restriction", "ClassIDs", "7", "8", "Computer", "Beamer"),
        FieldMap("class", "cmdb_class"),
        stat,
        lookups,
        issues,
    )
    assert got == [31, 31]
    assert {i.reason for i in issues} == {
        "Diese CI-Klasse wurde nicht nach Tiqora übernommen.",
        "„Beamer“ gibt es in diesem System nicht.",
    }
    issues = []
    got = resolve_values(
        el("restriction", "DeplStateIDs", "21", "22"),
        FieldMap("deployment_state", "cmdb_catalog"),
        stat,
        lookups,
        issues,
    )
    assert got == [41]
    assert [i.reason for i in issues] == ["Diesen Status gibt es in der Tiqora-CMDB nicht."]


def test_file_ids_unreliable_names_in_file_flag() -> None:
    file_stat = _stat(origin="file")
    queue = el("restriction", "QueueIDs", "Raw")
    fmap = FieldMap("queue", "id")
    assert not file_ids_unreliable(file_stat, queue, fmap)
    assert file_ids_unreliable(file_stat, queue, fmap, names_in_file=False)
    assert not file_ids_unreliable(_stat(origin="db"), queue, fmap, names_in_file=False)
    klass = el("restriction", "ClassIDs", "7")
    assert file_ids_unreliable(file_stat, klass, FieldMap("class", "cmdb_class"))
    names = el("restriction", "ClassIDs", "Computer")
    assert not file_ids_unreliable(file_stat, names, FieldMap("class", "cmdb_class"))
