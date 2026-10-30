"""Znuny ITSM stats: CI and change statistics (plan Task 11). Pure, no database."""

from __future__ import annotations

from dataclasses import replace

from tests.test_znuny_itsm_stats_inventory import itsm_fixture
from tests.test_znuny_stats_map_matrix import LOOKUPS, _stat, el
from tiqora.filters import AllOf, Condition
from tiqora.reporting.definition import RelativeTimeRange
from tiqora.reporting.znuny_stats.map_itsm import (
    CHANGE,
    CHANGE_HISTORY,
    CHANGES_INCIDENTS,
    CHANGES_PER_CI_CLASS,
    CONFIG_ITEM,
    RFC_REQUESTER,
    ZNUNY_CHANGE_STATES,
)
from tiqora.reporting.znuny_stats.mapper import map_stat

TIQORA = replace(
    LOOKUPS,
    itsm_mode="tiqora",
    cmdb_class_by_znuny_id={7: 70},
    cmdb_catalog_by_znuny_id={21: 210},
    cmdb_class_ids_by_name={"computer": 71},
    change_state_names={31: "approved"},
)
_CI = {"object_module": CONFIG_ITEM}
_CHANGE = {"object_module": CHANGE}
_MODE_B_CI = (
    "Die Tiqora-CMDB ist nicht aktiv; der Bericht zeigt erst Daten, "
    "wenn Tiqora das ITSM führt (Modus B)."
)
_MODE_B_CHANGE = "Tiqora-Changes gibt es nur, wenn Tiqora das ITSM führt (Modus B)."
_CIP = (
    "Kategorie, Auswirkung und Priorität aus Znuny gibt es bei Tiqora-Changes nicht; "
    "Tiqora bewertet das Risiko."
)
_MAPPED = "Znuny-Status wurden auf Tiqora-Phasen abgebildet."
_HISTORY_OBJECT = (
    "Znuny listet die Nummern der Changes, die im Zeitraum in diesen Status wechselten; "
    "Tiqora zählt sie."
)
_NO_PERIOD = "Ohne Zeitraum zeigt Tiqora die letzten 30 Tage."


def _conditions(where: object) -> list[Condition]:
    if isinstance(where, AllOf):
        return [c for c in where.all if isinstance(c, Condition)]
    assert isinstance(where, Condition)
    return [where]


def _filter(m) -> list[tuple[str, str, object]]:
    assert m.definition is not None
    if m.definition.filter is None:
        return []
    return [(c.field, c.op, c.value) for c in _conditions(m.definition.filter.where)]


def _issues(m, severity: str | None = None) -> set[tuple[str, str]]:
    return {(i.element, i.reason) for i in m.issues if severity in (None, i.severity)}


def _fixture(name: str, lookups=TIQORA):
    m = map_stat(itsm_fixture(name), lookups, is_admin=True)
    assert m.target == "report" and m.definition is not None
    return m


def test_config_item_100_state_quirk() -> None:
    m = _fixture("ITSMStats-100-ITSMConfigItem.xml")
    d = m.definition
    assert d.source == "config_items"
    assert [x.key for x in d.dimensions] == ["class", "deployment_state"]
    assert [x.key for x in d.measures] == ["config_items"]
    assert d.time_range is None and d.time_field is None
    assert (
        "UseAsValueSeries.StateIDs",
        "Das Element StateIDs gibt es im CI-Modul nicht mehr; "
        "es wird als Verwendungsstatus übernommen.",
    ) in _issues(m, "approximated")
    assert ("UseAsRestriction", _NO_PERIOD) not in _issues(m)
    assert not _issues(m, "dropped")


def test_config_item_db_ids_mapped() -> None:
    stat = _stat(
        el("x", "ClassIDs", "7", "8"),
        el("restriction", "InciStateIDs", "21"),
        **_CI,
    )
    m = map_stat(stat, TIQORA, is_admin=False)
    assert m.target == "report" and m.definition is not None
    assert [x.key for x in m.definition.dimensions] == ["class"]
    assert _filter(m) == [("class", "in", [70]), ("incident_state", "in", [210])]
    assert (
        "UseAsXvalue.ClassIDs",
        "Diese CI-Klasse wurde nicht nach Tiqora übernommen.",
    ) in _issues(m, "dropped")


def test_config_item_xml_attribute_dropped() -> None:
    stat = _stat(el("x", "ClassIDs", "7"), el("restriction", "XML::7::Vendor", "x"), **_CI)
    m = map_stat(stat, TIQORA, is_admin=False)
    assert (
        "UseAsRestriction.XML::7::Vendor",
        "CI-Attribute stehen in Berichten noch nicht zur Verfügung.",
    ) in _issues(m, "dropped")
    assert _filter(m) == [("class", "in", [70])]


def test_config_item_created_period_is_kept() -> None:
    m = _fixture("ITSMStats-101-ITSMConfigItem.xml")
    d = m.definition
    assert [(x.key, x.grain) for x in d.dimensions] == [("created", "day"), ("class", None)]
    assert d.time_field == "created"
    assert d.time_range == RelativeTimeRange(unit="month", count=1, include_current=False)


def test_change_400_status_mapping() -> None:
    m = _fixture("ITSMStats-400-ITSMChangeManagement.xml")
    d = m.definition
    assert d.source == "changes"
    assert [(x.key, x.grain) for x in d.dimensions] == [("created", "day"), ("change_status", None)]
    assert [x.key for x in d.measures] == ["changes"]
    assert d.time_field == "created"
    assert d.filter is None
    (param,) = d.parameters
    assert (param.field, param.op) == ("change_status", "in")
    assert param.default == [
        "scheduled",
        "cancelled",
        "closed_failed",
        "closed_backed_out",
        "implementing",
        "approval",
        "rejected",
        "submitted",
        "assessment",
        "closed_successful",
    ]
    assert (
        "UseAsValueSeries.StateIDs",
        "Das Element StateIDs heißt im Change-Modul ChangeStateIDs; "
        "es wird als Change-Status übernommen.",
    ) in _issues(m, "approximated")
    assert any(reason == _MAPPED for _, reason in _issues(m, "approximated"))
    assert not _issues(m, "dropped")


def test_change_state_table_matches_assumption_8() -> None:
    assert ZNUNY_CHANGE_STATES == {
        "requested": ("submitted", "assessment"),
        "pending approval": ("approval",),
        "rejected": ("rejected",),
        "approved": ("scheduled",),
        "in progress": ("implementing",),
        "pending pir": ("review",),
        "successful": ("closed_successful",),
        "failed": ("closed_failed", "closed_backed_out"),
        "canceled": ("cancelled",),
        "retracted": ("cancelled",),
    }


def test_change_db_ids_through_general_catalog() -> None:
    stat = _stat(el("restriction", "ChangeStateIDs", "31", "99"), **_CHANGE)
    m = map_stat(stat, TIQORA, is_admin=False)
    assert m.target == "report"
    assert _filter(m) == [("change_status", "in", ["scheduled"])]
    assert ("UseAsRestriction.ChangeStateIDs", "Diesen Change-Status gibt es in Tiqora nicht.") in (
        _issues(m, "dropped")
    )


def test_change_unknown_state_name_dropped() -> None:
    stat = _stat(el("restriction", "ChangeStateIDs", "requested", "bogus"), **_CHANGE)
    m = map_stat(stat, TIQORA, is_admin=False)
    assert _filter(m) == [("change_status", "in", ["submitted", "assessment"])]
    assert ("UseAsRestriction.ChangeStateIDs", "Diesen Change-Status gibt es in Tiqora nicht.") in (
        _issues(m, "dropped")
    )


def test_change_file_numeric_state_ids_become_parameter() -> None:
    stat = _stat(el("restriction", "ChangeStateIDs", "31"), origin="file", **_CHANGE)
    m = map_stat(stat, TIQORA, is_admin=False)
    assert m.definition is not None
    assert [(p.field, p.default) for p in m.definition.parameters] == [("change_status", None)]
    assert _filter(m) == []


def test_change_401_category_dropped() -> None:
    m = _fixture("ITSMStats-401-ITSMChangeManagement.xml")
    d = m.definition
    assert [(x.key, x.grain) for x in d.dimensions] == [("created", "day")]
    assert d.parameters == []
    assert ("UseAsValueSeries.CategoryIDs", _CIP) in _issues(m, "dropped")


def test_change_history_402_403() -> None:
    for name, value in (
        ("ITSMStats-402-ITSMChangeManagement.xml", "rejected"),
        ("ITSMStats-403-ITSMChangeManagement.xml", "cancelled"),
    ):
        m = _fixture(name)
        d = m.definition
        assert d.source == "change_events"
        assert [(x.key, x.grain) for x in d.dimensions] == [("occurred", "day"), ("to_phase", None)]
        assert [x.key for x in d.measures] == ["changes"]
        assert d.time_field == "occurred"
        assert _filter(m) == [("to_phase", "in", [value])]
        assert ("Object", _HISTORY_OBJECT) in _issues(m, "approximated")
        assert not _issues(m, "dropped")


def test_change_history_fixed_closed_states_become_closed() -> None:
    stat = _stat(
        el("restriction", "NewValues", "successful", "failed", "canceled", "retracted"),
        object_module=CHANGE_HISTORY,
    )
    m = map_stat(stat, TIQORA, is_admin=False)
    assert _filter(m) == [("to_phase", "in", ["closed", "cancelled"])]
    assert (
        "UseAsRestriction.NewValues",
        "„successful“ und „failed“ fallen in Tiqora in die Phase „Abgeschlossen“; "
        "der Bericht zählt beide Ergebnisse.",
    ) in _issues(m, "approximated")


def test_change_history_shipped_selection_has_no_collapse_issue() -> None:
    for name in (
        "ITSMStats-402-ITSMChangeManagement.xml",
        "ITSMStats-403-ITSMChangeManagement.xml",
    ):
        assert not any("beide Ergebnisse" in r for _, r in _issues(_fixture(name)))


def test_changes_incidents_404() -> None:
    m = _fixture("ITSMStats-404-ITSMChangeManagement.xml")
    d = m.definition
    assert d.source == "tickets"
    assert [(x.key, x.grain) for x in d.dimensions] == [("created", "day"), ("type", None)]
    assert [x.key for x in d.measures] == ["tickets"]
    assert d.time_field == "created"
    assert d.parameters == [] and d.filter is None
    assert (
        "UseAsValueSeries.Object",
        "Tiqora zeigt alle Ticket-Typen; Changes sind Tickets vom Typ „Change“.",
    ) in _issues(m, "approximated")
    assert not any("gültige Nummer" in reason for _, reason in _issues(m))
    assert not any("Modus B" in reason for _, reason in _issues(m))
    assert CHANGES_INCIDENTS in itsm_fixture("ITSMStats-404-ITSMChangeManagement.xml").object_module


def test_rfc_requester_405() -> None:
    m = _fixture("ITSMStats-405-ITSMChangeManagement.xml")
    d = m.definition
    assert d.source == "changes"
    assert [(x.key, x.grain) for x in d.dimensions] == [
        ("created", "day"),
        ("customer_user", None),
    ]
    assert [x.key for x in d.measures] == ["changes"]
    assert (
        "Object",
        "Znuny zählt Tickets der RfC-Typen je Agent oder Kunde; "
        "Tiqora zählt Changes je Kundenbenutzer.",
    ) in _issues(m, "approximated")
    assert RFC_REQUESTER in itsm_fixture("ITSMStats-405-ITSMChangeManagement.xml").object_module


def test_changes_per_ci_class_406() -> None:
    m = _fixture("ITSMStats-406-ITSMChangeManagement.xml")
    d = m.definition
    assert d.source == "ci_tickets"
    assert [x.key for x in d.dimensions] == ["ci_class"]
    assert [x.key for x in d.measures] == ["changes"]
    assert d.time_field == "created"
    assert d.time_range == RelativeTimeRange(unit="month", count=1, include_current=False)
    fixed = [(c.field, c.op) for c in _conditions(d.filter.where)] if d.filter else []
    assert ("is_change", "is_true") in fixed
    by_field = {p.field: p for p in d.parameters}
    assert by_field["ci_class"].default == [71]
    assert "ci_incident_state" in by_field
    assert (
        "UseAsValueSeries.CIClassIDs",
        "„Hardware“ gibt es in diesem System nicht.",
    ) in _issues(m, "dropped")
    assert len([1 for _, r in _issues(m, "dropped") if "gibt es in diesem System nicht" in r]) == 4
    assert ("UseAsXvalue.CategoryIDs", _CIP) in _issues(m, "dropped")
    state_issues = [i for i in m.issues if i.element == "UseAsRestriction.CIStateIDs"]
    assert [(i.severity, i.reason) for i in state_issues] == [
        (
            "approximated",
            "Die Statusnamen aus Znuny lassen sich nicht zuordnen. "
            "Solange Sie keine Auswahl treffen, zählt der Bericht alle Vorfallsstatus.",
        )
    ]
    assert by_field["ci_incident_state"].default is None
    assert by_field["ci_incident_state"].op == "in"
    assert all(c[0] != "ci_incident_state" for c in _filter(m))
    assert not any("gültige Nummer" in reason for _, reason in _issues(m))
    assert (
        CHANGES_PER_CI_CLASS in itsm_fixture("ITSMStats-406-ITSMChangeManagement.xml").object_module
    )


def test_changes_per_ci_class_db_state_ids_mapped() -> None:
    stat = _stat(
        el("x", "CIClassIDs", "7"),
        el("restriction", "CIStateIDs", "21", "22"),
        object_module=CHANGES_PER_CI_CLASS,
    )
    m = map_stat(stat, TIQORA, is_admin=False)
    assert m.definition is not None
    assert ("ci_incident_state", "in", [210]) in _filter(m)
    assert ("is_change", "is_true", None) in _filter(m)
    assert ("ci_class", "in", [70]) in _filter(m)
    assert (
        "UseAsRestriction.CIStateIDs",
        "Diesen Status gibt es in der Tiqora-CMDB nicht.",
    ) in _issues(m, "dropped")


def test_mode_a_issue_on_ci_and_change_stats() -> None:
    mode_a = replace(TIQORA, itsm_mode="znuny_readthrough")
    ci = _fixture("ITSMStats-100-ITSMConfigItem.xml", mode_a)
    assert ("Object", _MODE_B_CI) in _issues(ci, "approximated")
    change = _fixture("ITSMStats-400-ITSMChangeManagement.xml", mode_a)
    assert ("Object", _MODE_B_CHANGE) in _issues(change, "approximated")
    for name in (
        "ITSMStats-402-ITSMChangeManagement.xml",
        "ITSMStats-405-ITSMChangeManagement.xml",
        "ITSMStats-406-ITSMChangeManagement.xml",
    ):
        assert ("Object", _MODE_B_CHANGE) in _issues(_fixture(name, mode_a), "approximated")
    # Mode B and the ticket based statistic carry no such issue
    assert ("Object", _MODE_B_CI) not in _issues(_fixture("ITSMStats-100-ITSMConfigItem.xml"))
    ticket_based = _fixture("ITSMStats-404-ITSMChangeManagement.xml", mode_a)
    assert ("Object", _MODE_B_CHANGE) not in _issues(ticket_based)
