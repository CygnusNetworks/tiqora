"""Znuny ITSM statistics inventory (wave 5 ITSM stats, Task 1).

The 47 statistic definitions of ITSMServiceLevelManagement (branch rel-7_3, identical in
rel-6_5) live verbatim in fixtures/znuny_stats/itsm; the later importer tests build on
them through itsm_fixture(). Pure tests, no database.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Final

import pytest

from tiqora.reporting.znuny_stats.model import ZnunyStat, stat_from_xml_hash
from tiqora.reporting.znuny_stats.xml_hash import xml_file_to_hash

ITSM_FIX = Path(__file__).parent / "fixtures" / "znuny_stats" / "itsm"
MODULE_PREFIX: Final = "Kernel::System::Stats::Dynamic::"

# statistic title per file, as shipped (survey of the plan)
TITLES: Final = {
    "ITSMStats-001-Ticket.xml": (
        "Total number of all tickets ever created per Ticket-Type and Priority"
    ),
    "ITSMStats-002-Ticket.xml": (
        "Total number of all tickets ever created per Ticket-Type and State"
    ),
    "ITSMStats-003-Ticket.xml": (
        "Total number of all tickets ever created per Ticket-Type and Queue"
    ),
    "ITSMStats-004-Ticket.xml": (
        "Total number of all tickets ever created per Ticket-Type and Service"
    ),
    "ITSMStats-005-Ticket.xml": (
        "Monthly overview of all tickets created in the last month per Ticket-Type"
    ),
    "ITSMStats-006-Ticket.xml": (
        "Monthly overview of all tickets created in the last month per Priority"
    ),
    "ITSMStats-007-Ticket.xml": (
        "Monthly overview of all tickets created in the last month per State"
    ),
    "ITSMStats-008-Ticket.xml": (
        "Monthly overview of all tickets created in the last month per Queue"
    ),
    "ITSMStats-009-Ticket.xml": (
        "Monthly overview of all tickets created in the last month per Service"
    ),
    "ITSMStats-010-Ticket.xml": (
        "Number of tickets created in a specific time period per Ticket-Type and Priority"
    ),
    "ITSMStats-011-Ticket.xml": (
        "Number of tickets created in a specific time period per Ticket-Type and State"
    ),
    "ITSMStats-012-Ticket.xml": (
        "Number of tickets created in a specific time period per Ticket-Type and Queue"
    ),
    "ITSMStats-013-Ticket.xml": (
        "Number of tickets created in a specific time period per Ticket-Type and Service"
    ),
    "ITSMStats-014-Ticket.xml": ("Number of currently open tickets per Ticket-Type and Priority"),
    "ITSMStats-015-Ticket.xml": ("Number of currently open tickets per Ticket-Type and Queue"),
    "ITSMStats-016-Ticket.xml": ("Number of currently open tickets per Ticket-Type and Service"),
    "ITSMStats-100-ITSMConfigItem.xml": (
        "Total number of all config items ever created per Class and State"
    ),
    "ITSMStats-101-ITSMConfigItem.xml": (
        "Monthly overview of all config items created in the last month per Class"
    ),
    "ITSMStats-102-ITSMConfigItem.xml": (
        "Monthly overview of all config items created in the last month per State"
    ),
    "ITSMStats-103-ITSMConfigItem.xml": (
        "Number of config items created in a specific time period per Class and State"
    ),
    "ITSMStats-200-ITSMTicketFirstLevelSolutionRate.xml": (
        "First level solution rate for all tickets ever created per Ticket-Type and Priority"
    ),
    "ITSMStats-201-ITSMTicketFirstLevelSolutionRate.xml": (
        "First level solution rate for all tickets ever created per Ticket-Type and Queue"
    ),
    "ITSMStats-202-ITSMTicketFirstLevelSolutionRate.xml": (
        "First level solution rate for all tickets ever created per Ticket-Type and Service"
    ),
    "ITSMStats-203-ITSMTicketFirstLevelSolutionRate.xml": (
        "Monthly overview of first level solution rate per Ticket-Type in the last month"
    ),
    "ITSMStats-204-ITSMTicketFirstLevelSolutionRate.xml": (
        "Monthly overview of first level solution rate per Priority in the last month"
    ),
    "ITSMStats-205-ITSMTicketFirstLevelSolutionRate.xml": (
        "Monthly overview of first level solution rate per Queue in the last month"
    ),
    "ITSMStats-206-ITSMTicketFirstLevelSolutionRate.xml": (
        "Monthly overview of first level solution rate per Service in the last month"
    ),
    "ITSMStats-207-ITSMTicketFirstLevelSolutionRate.xml": (
        "First level solution rate for all tickets created in a specific time period "
        "per Ticket-Type and Priority"
    ),
    "ITSMStats-208-ITSMTicketFirstLevelSolutionRate.xml": (
        "First level solution rate for all tickets created in a specific time period "
        "per Ticket-Type and Queue"
    ),
    "ITSMStats-209-ITSMTicketFirstLevelSolutionRate.xml": (
        "First level solution rate for all tickets created in a specific time period "
        "per Ticket-Type and Service"
    ),
    "ITSMStats-300-ITSMTicketSolutionTimeAverage.xml": (
        "Average solution time for all tickets ever created per Ticket-Type and Priority"
    ),
    "ITSMStats-301-ITSMTicketSolutionTimeAverage.xml": (
        "Average solution time for all tickets ever created per Ticket-Type and Queue"
    ),
    "ITSMStats-302-ITSMTicketSolutionTimeAverage.xml": (
        "Average solution time for all tickets ever created per Ticket-Type and Service"
    ),
    "ITSMStats-303-ITSMTicketSolutionTimeAverage.xml": (
        "Monthly overview of the average solution time per Ticket-Type in the last month"
    ),
    "ITSMStats-304-ITSMTicketSolutionTimeAverage.xml": (
        "Monthly overview of the average solution time per Priority in the last month"
    ),
    "ITSMStats-305-ITSMTicketSolutionTimeAverage.xml": (
        "Monthly overview of the average solution time per Queue in the last month"
    ),
    "ITSMStats-306-ITSMTicketSolutionTimeAverage.xml": (
        "Monthly overview of the average solution time per Service in the last month"
    ),
    "ITSMStats-307-ITSMTicketSolutionTimeAverage.xml": (
        "Average solution time of tickets created in the last month per Ticket-Type and Priority"
    ),
    "ITSMStats-308-ITSMTicketSolutionTimeAverage.xml": (
        "Average solution time of tickets created in the last month per Ticket-Type and Queue"
    ),
    "ITSMStats-309-ITSMTicketSolutionTimeAverage.xml": (
        "Average solution time of tickets created in the last month per Ticket-Type and Service"
    ),
    "ITSMStats-400-ITSMChangeManagement.xml": ("ITSMChange Count Changes"),
    "ITSMStats-401-ITSMChangeManagement.xml": ("ITSMChange Changes per Category"),
    "ITSMStats-402-ITSMChangeManagement.xml": ("ITSMChange rejected changes"),
    "ITSMStats-403-ITSMChangeManagement.xml": ("ITSMChange retracted changes"),
    "ITSMStats-404-ITSMChangeManagement.xml": ("Relation Tickets (by types) ↔ Changes"),
    "ITSMStats-405-ITSMChangeManagement.xml": ("RfCs per Requester"),
    "ITSMStats-406-ITSMChangeManagement.xml": ("Changes per config item class"),
}


# statistics per object module (the file name carries "ITSMChangeManagement" for all of 400-406)
PER_OBJECT: Final = {
    "Ticket": 16,
    "ITSMConfigItem": 4,
    "ITSMTicketFirstLevelSolutionRate": 10,
    "ITSMTicketSolutionTimeAverage": 10,
    "ITSMChangeManagement": 2,
    "ITSMChangeManagementHistory": 2,
    "ITSMChangeManagementChangesIncidents": 1,
    "ITSMChangeManagementRfcRequester": 1,
    "ITSMChangeManagementChangesPerCIClasses": 1,
}


def itsm_fixture(name: str) -> ZnunyStat:
    """Parse one shipped ITSM statistic file (name with or without the directory)."""
    return stat_from_xml_hash(
        xml_file_to_hash((ITSM_FIX / name).read_bytes()), origin="file", stat_id=None
    )


def _by_number(number: int) -> ZnunyStat:
    (path,) = ITSM_FIX.glob(f"ITSMStats-{number:03d}-*.xml")
    return itsm_fixture(path.name)


def _element(stat: ZnunyStat, use: str, element: str):
    (found,) = [e for e in stat.elements if e.use == use and e.element == element]
    return found


def test_inventory_has_47_files() -> None:
    names = sorted(p.name for p in ITSM_FIX.glob("ITSMStats-*.xml"))
    assert len(names) == 47
    assert names == sorted(TITLES)
    modules = Counter(itsm_fixture(n).object_module.removeprefix(MODULE_PREFIX) for n in names)
    assert dict(modules) == PER_OBJECT


@pytest.mark.parametrize("name", sorted(p.name for p in ITSM_FIX.glob("ITSMStats-*.xml")))
def test_every_file_parses(name: str) -> None:
    stat = itsm_fixture(name)
    assert stat.object_module.startswith(MODULE_PREFIX)
    assert stat.stat_type == "dynamic"
    assert stat.title == TITLES[name]
    assert stat.elements


def test_survey_quirks_are_present() -> None:
    # 100/102/103 use "StateIDs", which the ITSMConfigItem module does not define
    assert _element(_by_number(100), "series", "StateIDs")
    assert _element(_by_number(102), "series", "StateIDs")
    assert _element(_by_number(103), "series", "StateIDs")
    # 400 selects the nine change state names under "StateIDs" and does not fix them
    states = _element(_by_number(400), "series", "StateIDs")
    assert states.fixed is False
    assert states.selected == (
        "approved",
        "canceled",
        "failed",
        "in progress",
        "pending approval",
        "rejected",
        "requested",
        "retracted",
        "successful",
    )
    # 200: type ids as written in the file, restricted to the closed states 2 and 10
    assert _element(_by_number(200), "x", "TypeIDs").selected == ("2", "4", "3")
    assert _element(_by_number(200), "restriction", "StateIDs").selected == ("2", "10")
