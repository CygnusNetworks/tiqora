"""Znuny stats mapping: accounted time, solution/response time, ticket list (plan Task 4)."""

from __future__ import annotations

from tests.test_znuny_stats_map_matrix import FIX, LOOKUPS, _stat, el
from tiqora.reporting.znuny_stats.map_more import stat_to_profile_values
from tiqora.reporting.znuny_stats.mapper import map_stat
from tiqora.reporting.znuny_stats.model import stat_from_xml_hash
from tiqora.reporting.znuny_stats.xml_hash import xml_file_to_hash

ACCOUNTED = "Kernel::System::Stats::Dynamic::TicketAccountedTime"
SOLUTION = "Kernel::System::Stats::Dynamic::TicketSolutionResponseTime"
TICKET_LIST = "Kernel::System::Stats::Dynamic::TicketList"


def _file(name: str):  # type: ignore[no-untyped-def]
    return stat_from_xml_hash(
        xml_file_to_hash((FIX / name).read_bytes()), origin="file", stat_id=None
    )


def test_kinds_of_reporting_partial() -> None:
    m = map_stat(
        _stat(
            el("x", "KindsOfReporting", "TotalTime", "NumberOfTickets", "TicketMinTime"),
            el("series", "AccountedByAgent", "52005"),
            el(
                "restriction",
                "ArticleAccountedTime",
                TimeStart="2026-09-01 00:00:00",
                TimeStop="2026-09-30 23:59:59",
            ),
            el("restriction", "StateIDs", "4"),
            object_module=ACCOUNTED,
        ),
        LOOKUPS,
        is_admin=False,
    )
    d = m.definition
    assert d is not None
    assert d.source == "accounted_time"
    assert [x.key for x in d.measures] == ["time_units", "tickets"]
    assert [x.key for x in d.dimensions] == ["agent"] and d.time_field == "booked"
    assert {(i.element, i.value) for i in m.issues if i.severity == "dropped"} >= {
        ("UseAsXvalue.KindsOfReporting", "TicketMinTime"),
        ("UseAsRestriction.StateIDs", "4"),
    }
    reasons = {i.value: i.reason for i in m.issues}
    assert reasons["4"] == "Wird bei der Zeiterfassung nicht unterstützt."
    assert reasons["TicketMinTime"] == "Kennzahl gibt es in Tiqora nicht."


def test_kinds_none_mappable_is_none() -> None:
    m = map_stat(
        _stat(el("x", "KindsOfReporting", "ArticleAverage"), object_module=ACCOUNTED),
        LOOKUPS,
        is_admin=False,
    )
    assert m.target == "none" and m.definition is None
    assert any(i.value == "ArticleAverage" and i.severity == "dropped" for i in m.issues)


def test_accounted_time_fixture() -> None:
    m = map_stat(_file("accounted_time.xml"), LOOKUPS, is_admin=False)
    assert m.target == "report" and m.definition is not None
    assert [x.key for x in m.definition.measures] == ["time_units", "tickets"]


def test_solution_response_measures() -> None:
    m = map_stat(
        _stat(
            el(
                "x",
                "KindsOfReporting",
                "SolutionAverageAllOver",
                "NumberOfTicketsAllOver",
                "SolutionWorkingTimeAverage",
                "ResponseWorkingTimeAverage",
                "ResponseMinTime",
                "SolutionAverage",
            ),
            el("series", "PriorityIDs", "3"),
            object_module=SOLUTION,
        ),
        LOOKUPS,
        is_admin=False,
    )
    assert m.definition is not None
    assert [x.key for x in m.definition.measures] == [
        "solution_clock_avg",
        "closed_tickets",
        "solution_avg",
        "first_response_avg",
    ]
    assert [x.key for x in m.definition.dimensions] == ["priority"]
    reasons = {i.value: i.reason for i in m.issues}
    assert "Median" in reasons["ResponseMinTime"] and "Eskalation" in reasons["SolutionAverage"]


def test_solution_response_fixture_uses_closed_period() -> None:
    m = map_stat(_file("solution_response.xml"), LOOKUPS, is_admin=False)
    d = m.definition
    assert d is not None and d.time_field == "closed" and d.time_range is not None
    assert [x.key for x in d.measures] == ["closed_tickets", "solution_clock_avg"]
    reasons = {i.value: i.reason for i in m.issues}
    assert "Median" in reasons["SolutionMaxTimeWorkingTime"]
    assert "Median" in reasons["SolutionMinTime"]


def test_ticket_list_becomes_saved_search() -> None:
    stat = _stat(
        el("restriction", "QueueIDs", "3"),
        el("restriction", "CreateTime", TimeRelativeUnit="Month", TimeRelativeCount="2"),
        el("restriction", "MIMEBase_Subject", "Rechnung"),
        el("restriction", "DynamicField_Kundenwert", "A"),
        el("x", "TicketAttributes", "TicketNumber", "Title"),
        el("series", "OrderBy", "Created"),
        el("series", "SortSequence", "Up"),
        el("restriction", "Limit", "100"),
        object_module=TICKET_LIST,
    )
    values, _issues = stat_to_profile_values(stat, LOOKUPS)
    assert values["QueueIDs"] == ["3"]
    assert (
        values["TimeSearchType"],
        values["TicketCreateTimePoint"],
        values["TicketCreateTimePointStart"],
        values["TicketCreateTimePointFormat"],
    ) == ("TimePoint", "2", "Last", "month")
    assert values["Subject"] == "Rechnung" and "DynamicField_Kundenwert" not in values
    m = map_stat(stat, LOOKUPS, is_admin=False)
    assert m.target == "saved_search" and m.definition is None and m.search is not None
    assert (m.search.sort_by, m.search.sort_order) == ("created", "asc")
    elements = {i.element for i in m.issues}
    assert {
        "UseAsRestriction.Subject",
        "UseAsRestriction.DynamicField_Kundenwert",
        "UseAsXvalue.TicketAttributes",
        "UseAsRestriction.Limit",
    } <= elements


def test_ticket_list_absolute_slot_and_halfyear() -> None:
    stat = _stat(
        el(
            "restriction",
            "ChangeTime",
            TimeStart="2026-01-01 00:00:00",
            TimeStop="2026-06-30 23:59:59",
        ),
        el("restriction", "PendingUntilTime", TimeRelativeUnit="HalfYear", TimeRelativeCount="1"),
        object_module=TICKET_LIST,
    )
    values, _ = stat_to_profile_values(stat, LOOKUPS)
    assert values["ChangeTimeSearchType"] == "TimeSlot"
    assert (values["TicketChangeTimeStartYear"], values["TicketChangeTimeStopMonth"]) == (
        "2026",
        "06",
    )
    assert (values["TicketPendingTimePoint"], values["TicketPendingTimePointFormat"]) == (
        "6",
        "month",
    )


def test_ticket_list_unknown_order_and_unrestricted() -> None:
    m = map_stat(
        _stat(el("series", "OrderBy", "Queue"), object_module=TICKET_LIST),
        LOOKUPS,
        is_admin=False,
    )
    assert m.search is not None and (m.search.sort_by, m.search.sort_order) == ("age", "desc")
    reasons = [i.reason for i in m.issues]
    assert any("Sortierung nach Queue gibt es nicht" in r for r in reasons)
    assert any(r.startswith("Ohne Einschränkung") for r in reasons)


def test_ticket_list_upcoming_is_rolling_window() -> None:
    stat = _stat(
        el(
            "restriction",
            "CreateTime",
            TimeRelativeUnit="Quarter",
            TimeRelativeCount="1",
            TimeRelativeUpcomingCount="2",
        ),
        object_module=TICKET_LIST,
    )
    values, issues = stat_to_profile_values(stat, LOOKUPS)
    assert (values["TicketCreateTimePoint"], values["TicketCreateTimePointFormat"]) == (
        "3",
        "month",
    )
    reasons = {i.reason for i in issues}
    assert "Als gleitender Zeitraum der letzten 3 Monate übernommen." in reasons
    assert "Zukünftige Zeiträume entfallen." in reasons


def test_sample_ticket_list_file() -> None:  # Znuny's own sample: QueueIDs "Junk" by name, Fixed=0
    m = map_stat(_file("Stats.TestTicketList.en.xml"), LOOKUPS, is_admin=False)
    assert m.target == "saved_search" and m.search is not None
    assert m.search.criteria.model_dump()["where"] == {
        "field": "queue_id",
        "op": "in",
        "value": [3],
    }
    assert any(
        i.severity == "approximated" and "zur Laufzeit änderbar" in i.reason for i in m.issues
    )


def test_ticket_list_file_foreign_ids_are_dropped() -> None:
    stat = _stat(
        el("restriction", "ServiceIDs", "7"), origin="file", stat_id=None, object_module=TICKET_LIST
    )
    values, issues = stat_to_profile_values(stat, LOOKUPS)
    assert "ServiceIDs" not in values
    assert any(
        i.severity == "dropped" and i.value == "7" and "Exportdatei" in i.reason for i in issues
    )
