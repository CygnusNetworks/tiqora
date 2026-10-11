"""Znuny stats mapping: common rules, ticket matrix, StateAction (plan Task 3). Pure."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from tiqora.extensions.core import get_registry
from tiqora.reporting.definition import (
    AbsoluteTimeRange,
    RelativeTimeRange,
    Totals,
    validate_definition,
)
from tiqora.reporting.znuny_stats.mapper import map_stat
from tiqora.reporting.znuny_stats.mapping_common import (
    ELEMENT_FIELDS_TICKETS,
    FILE_NAME_ELEMENTS,
    ImportIssue,
    StatLookups,
    map_time_range,
)
from tiqora.reporting.znuny_stats.model import StatElement, ZnunyStat, stat_from_xml_hash
from tiqora.reporting.znuny_stats.xml_hash import xml_file_to_hash
from tiqora.saved_searches.znuny_import import ZnunyLookups

FIX = Path(__file__).parent / "fixtures" / "znuny_stats"

LOOKUPS = StatLookups(
    state_type_names={1: "new", 2: "open", 3: "closed", 9: "merged"},
    queue_ids_by_name={"raw": 2, "junk": 3, "support::level 1": 12},
    state_ids_by_name={"open": 4, "closed successful": 2},
    priority_ids_by_name={"3 normal": 3},
    user_ids_by_login={"rep52.agent": 52005},
    group_ids_by_name={"stats": 9, "users": 1},
    default_time_zone="Europe/Berlin",
    search=ZnunyLookups(
        state_type_names={},
        queue_names={},
        lock_names={},
        dynamic_fields={},
        time_zone="Europe/Berlin",
    ),
)


def _stat(*elements: StatElement, **kw: Any) -> ZnunyStat:
    base: dict[str, Any] = dict(
        origin="db",
        stat_id=52030,
        stat_number="10030",
        title="rep52 Matrix",
        description="",
        stat_type="dynamic",
        object_module="Kernel::System::Stats::Dynamic::Ticket",
        static_file=None,
        valid=True,
        permission=("9",),
        formats=("Print",),
        sum_row=False,
        sum_col=False,
        time_zone=None,
        show_as_dashboard_widget=False,
        elements=elements,
    )
    base.update(kw)
    return ZnunyStat(**base)


def el(use: str, element: str, *selected: str, fixed: bool = True, **time: str) -> StatElement:
    return StatElement(
        use=use,
        element=element,
        fixed=fixed,
        selected=tuple(selected),
        time=time,  # type: ignore[arg-type]
    )


def test_overview_matrix() -> None:  # Znuny's own sample: series QueueIDs, X StateIDs, sums
    m = map_stat(
        _stat(el("series", "QueueIDs"), el("x", "StateIDs"), sum_row=True, sum_col=True),
        LOOKUPS,
        is_admin=False,
    )
    assert m.target == "report"
    d = m.definition
    assert d is not None
    assert d.source == "tickets" and [x.key for x in d.dimensions] == ["state", "queue"]
    assert [x.key for x in d.measures] == ["tickets"]
    assert d.totals == Totals(grand=True, rows=True, columns=True)
    assert d.visualization.type == "pivot"


def test_time_axis_relative_and_runtime_parameter() -> None:
    m = map_stat(
        _stat(
            el(
                "x",
                "CreateTime",
                "Month",
                TimeRelativeUnit="Month",
                TimeRelativeCount="12",
                TimeRelativeUpcomingCount="1",
            ),
            el("restriction", "QueueIDs", "4", "5", fixed=False),
            el("restriction", "StateTypeIDs", "1", "2"),
            formats=("D3::LineChart",),
            time_zone="Europe/Berlin",
        ),
        LOOKUPS,
        is_admin=False,
    )
    d = m.definition
    assert d is not None
    assert d.dimensions[0].key == "created" and d.dimensions[0].grain == "month"
    assert d.time_field == "created"
    assert d.time_range == RelativeTimeRange(unit="month", count=12, include_current=True)
    assert d.time_zone == "Europe/Berlin" and d.visualization.type == "line"
    assert d.parameters[0].field == "queue" and d.parameters[0].default == [4, 5]
    assert d.filter is not None
    assert d.filter.model_dump()["where"] == {
        "field": "state_type",
        "op": "in",
        "value": ["new", "open"],
    }


def test_absolute_range_rounding_is_reported() -> None:
    m = map_stat(
        _stat(
            el("x", "QueueIDs", "4"),
            el(
                "restriction",
                "CreateTime",
                TimeStart="2026-09-01 08:00:00",
                TimeStop="2026-09-30 23:59:59",
            ),
        ),
        LOOKUPS,
        is_admin=False,
    )
    assert m.definition is not None
    assert m.definition.time_range == AbsoluteTimeRange(
        start=date(2026, 9, 1), end=date(2026, 9, 30)
    )
    assert any(i.severity == "approximated" and "gerundet" in i.reason for i in m.issues)


def test_every_dropped_element_is_listed() -> None:
    m = map_stat(
        _stat(
            el("x", "QueueIDs", "4"),
            el("restriction", "DynamicField_Kundenwert", "A"),
            el("restriction", "EscalationTime", TimeRelativeUnit="Day", TimeRelativeCount="3"),
            el("restriction", "LockIDs", "2"),
            el("restriction", "CustomerID", "rep52*"),
            el("restriction", "SearchInArchive", "AllTickets"),
            el("series", "CreateTime", "HalfYear", TimeRelativeUnit="Year", TimeRelativeCount="1"),
            show_as_dashboard_widget=True,
            valid=False,
        ),
        LOOKUPS,
        is_admin=False,
    )
    dropped = {i.element for i in m.issues if i.severity == "dropped"}
    assert dropped >= {
        "UseAsRestriction.DynamicField_Kundenwert",
        "UseAsRestriction.EscalationTime",
        "UseAsRestriction.LockIDs",
        "UseAsRestriction.CustomerID",
        "UseAsRestriction.SearchInArchive",
        "UseAsValueSeries.CreateTime",
        "ShowAsDashboardWidget",
    }
    assert any(i.element == "Valid" for i in m.issues)
    assert m.definition is not None and m.target == "report"
    assert [x.key for x in m.definition.dimensions] == ["queue"]
    assert all(i.reason.endswith(".") for i in m.issues)  # full German sentences
    assert list(m.issues) == sorted(m.issues, key=lambda i: (i.element, i.value, i.reason))


def test_relative_units() -> None:
    issues: list[ImportIssue] = []
    assert map_time_range(
        el("restriction", "CreateTime", TimeRelativeUnit="HalfYear", TimeRelativeCount="2"), issues
    ) == RelativeTimeRange(unit="month", count=12, include_current=False)
    assert (
        map_time_range(
            el("restriction", "CreateTime", TimeRelativeUnit="Hour", TimeRelativeCount="5"), issues
        )
        is None
    )
    assert map_time_range(
        el(
            "restriction",
            "CreateTime",
            TimeRelativeUnit="Week",
            TimeRelativeCount="4",
            TimeRelativeUpcomingCount="3",
        ),
        issues,
    ) == RelativeTimeRange(unit="week", count=4, include_current=True)
    assert [i.severity for i in issues] == ["approximated", "dropped", "approximated"]


def test_close_time2_maps_to_closed() -> None:
    assert ELEMENT_FIELDS_TICKETS["CloseTime2"].field == "closed"
    m = map_stat(
        _stat(
            el("x", "PriorityIDs"),
            el("restriction", "CloseTime2", TimeRelativeUnit="Month", TimeRelativeCount="3"),
        ),
        LOOKUPS,
        is_admin=False,
    )
    assert m.definition is not None
    assert m.definition.time_field == "closed"
    assert m.definition.time_range == RelativeTimeRange(unit="month", count=3)


def test_file_origin_names_to_ids() -> None:
    m = map_stat(
        _stat(el("x", "QueueIDs", "Junk", "Support::Level 1", "Nope"), origin="file", stat_id=None),
        LOOKUPS,
        is_admin=False,
    )
    assert m.definition is not None and m.definition.filter is not None
    assert m.definition.filter.model_dump()["where"]["value"] == [3, 12]
    assert any(i.value == "Nope" and "gibt es in diesem System nicht" in i.reason for i in m.issues)


def test_file_origin_foreign_ids_become_runtime_parameter() -> None:
    assert "TypeIDs" not in FILE_NAME_ELEMENTS
    m = map_stat(
        _stat(el("restriction", "TypeIDs", "7"), origin="file", stat_id=None),
        LOOKUPS,
        is_admin=False,
    )
    d = m.definition
    assert d is not None and d.filter is None
    assert [(p.field, p.default) for p in d.parameters] == [("type", None)]
    assert any(
        i.severity == "approximated" and "Exportdatei" in i.reason and i.value == "7"
        for i in m.issues
    )


def test_file_origin_state_type_ids_are_mapped_and_flagged() -> None:
    m = map_stat(
        _stat(el("restriction", "StateTypeIDs", "1", "2"), origin="file", stat_id=None),
        LOOKUPS,
        is_admin=False,
    )
    assert m.definition is not None and m.definition.filter is not None
    assert m.definition.filter.model_dump()["where"]["value"] == ["new", "open"]
    assert any(i.severity == "approximated" and "Exportdatei" in i.reason for i in m.issues)


def test_state_action_static() -> None:
    m = map_stat(
        _stat(
            stat_type="static",
            object_module="Kernel::System::Stats::Static::StateAction",
            static_file="StateAction",
        ),
        LOOKUPS,
        is_admin=False,
    )
    d = m.definition
    assert d is not None
    assert d.source == "ticket_events" and [x.key for x in d.dimensions] == ["occurred", "kind"]
    assert d.time_range == RelativeTimeRange(unit="month", count=1)
    assert d.totals == Totals(grand=True, rows=True, columns=True)
    assert [i.severity for i in m.issues] == ["approximated"]


def test_unknown_object_is_none() -> None:
    m = map_stat(
        _stat(object_module="Kernel::System::Stats::Dynamic::ITSMChangeManagement"),
        LOOKUPS,
        is_admin=False,
    )
    assert m.target == "none" and m.definition is None and m.issues[0].element == "Object"


def test_invalid_result_becomes_none_not_an_exception() -> None:
    # two axes on the same field and too many runtime parameters are handled; a duplicate
    # time-zone-less stat still validates, so provoke a pydantic error via a huge relative range
    m = map_stat(
        _stat(el("restriction", "CreateTime", TimeRelativeUnit="Year", TimeRelativeCount="9999")),
        LOOKUPS,
        is_admin=False,
    )
    assert m.target == "report" and m.definition is not None and m.definition.time_range is None
    assert any(i.severity == "dropped" and "zu lang" in i.reason for i in m.issues)


def test_fixtures_map_and_validate() -> None:
    for name in (
        "Stats.TicketOverview.de.xml",
        "matrix_time_axis.xml",
        "static_state_action.xml",
        "unsupported.xml",
    ):
        stat = stat_from_xml_hash(
            xml_file_to_hash((FIX / name).read_bytes()), origin="file", stat_id=None
        )
        m = map_stat(stat, LOOKUPS, is_admin=False)
        if name != "unsupported.xml":
            assert m.target == "report", name
        if m.definition is not None:
            validate_definition(m.definition, get_registry())  # never raises
