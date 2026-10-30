"""Every shipped ITSM statistic maps as the parity checklist says (plan Task 11).

``PARITY`` is the checklist of the plan (row per file: target, source, dimension keys, measure
keys); Task 13 reuses it for the statistics the golden peers installed. Pure, no database.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

import pytest

from tests.test_znuny_itsm_stats_inventory import ITSM_FIX, itsm_fixture
from tests.test_znuny_stats_map_matrix import LOOKUPS
from tiqora.reporting.znuny_stats.mapper import map_stat

#: file name -> (target, source, dimension keys, measure keys)
PARITY: Final[dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]]] = {
    "ITSMStats-001-Ticket.xml": ("report", "tickets", ("type", "priority"), ("tickets",)),
    "ITSMStats-002-Ticket.xml": ("report", "tickets", ("type", "state"), ("tickets",)),
    "ITSMStats-003-Ticket.xml": ("report", "tickets", ("type", "queue"), ("tickets",)),
    "ITSMStats-004-Ticket.xml": ("report", "tickets", ("type", "service"), ("tickets",)),
    "ITSMStats-005-Ticket.xml": ("report", "tickets", ("created", "type"), ("tickets",)),
    "ITSMStats-006-Ticket.xml": ("report", "tickets", ("created", "priority"), ("tickets",)),
    "ITSMStats-007-Ticket.xml": ("report", "tickets", ("created", "state"), ("tickets",)),
    "ITSMStats-008-Ticket.xml": ("report", "tickets", ("created", "queue"), ("tickets",)),
    "ITSMStats-009-Ticket.xml": ("report", "tickets", ("created", "service"), ("tickets",)),
    "ITSMStats-010-Ticket.xml": ("report", "tickets", ("type", "priority"), ("tickets",)),
    "ITSMStats-011-Ticket.xml": ("report", "tickets", ("type", "state"), ("tickets",)),
    "ITSMStats-012-Ticket.xml": ("report", "tickets", ("type", "queue"), ("tickets",)),
    "ITSMStats-013-Ticket.xml": ("report", "tickets", ("type", "service"), ("tickets",)),
    "ITSMStats-014-Ticket.xml": ("report", "tickets", ("type", "priority"), ("tickets",)),
    "ITSMStats-015-Ticket.xml": ("report", "tickets", ("type", "queue"), ("tickets",)),
    "ITSMStats-016-Ticket.xml": ("report", "tickets", ("type", "service"), ("tickets",)),
    "ITSMStats-100-ITSMConfigItem.xml": (
        "report",
        "config_items",
        ("class", "deployment_state"),
        ("config_items",),
    ),
    "ITSMStats-101-ITSMConfigItem.xml": (
        "report",
        "config_items",
        ("created", "class"),
        ("config_items",),
    ),
    "ITSMStats-102-ITSMConfigItem.xml": (
        "report",
        "config_items",
        ("created", "deployment_state"),
        ("config_items",),
    ),
    "ITSMStats-103-ITSMConfigItem.xml": (
        "report",
        "config_items",
        ("class", "deployment_state"),
        ("config_items",),
    ),
    "ITSMStats-200-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("type", "priority"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-201-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("type", "queue"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-202-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("type", "service"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-203-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("created", "type"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-204-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("created", "priority"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-205-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("created", "queue"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-206-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("created", "service"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-207-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("type", "priority"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-208-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("type", "queue"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-209-ITSMTicketFirstLevelSolutionRate.xml": (
        "report",
        "tickets",
        ("type", "service"),
        ("first_level_solved", "first_level_solution_rate"),
    ),
    "ITSMStats-300-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("type", "priority"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-301-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("type", "queue"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-302-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("type", "service"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-303-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("created", "type"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-304-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("created", "priority"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-305-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("created", "queue"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-306-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("created", "service"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-307-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("type", "priority"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-308-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("type", "queue"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-309-ITSMTicketSolutionTimeAverage.xml": (
        "report",
        "tickets",
        ("type", "service"),
        ("itsm_solution_avg",),
    ),
    "ITSMStats-400-ITSMChangeManagement.xml": (
        "report",
        "changes",
        ("created", "change_status"),
        ("changes",),
    ),
    "ITSMStats-401-ITSMChangeManagement.xml": ("report", "changes", ("created",), ("changes",)),
    "ITSMStats-402-ITSMChangeManagement.xml": (
        "report",
        "change_events",
        ("occurred", "to_phase"),
        ("changes",),
    ),
    "ITSMStats-403-ITSMChangeManagement.xml": (
        "report",
        "change_events",
        ("occurred", "to_phase"),
        ("changes",),
    ),
    "ITSMStats-404-ITSMChangeManagement.xml": (
        "report",
        "tickets",
        ("created", "type"),
        ("tickets",),
    ),
    "ITSMStats-405-ITSMChangeManagement.xml": (
        "report",
        "changes",
        ("created", "customer_user"),
        ("changes",),
    ),
    "ITSMStats-406-ITSMChangeManagement.xml": ("report", "ci_tickets", ("ci_class",), ("changes",)),
}

_LOOKUPS = replace(
    LOOKUPS,
    itsm_mode="tiqora",
    cmdb_class_by_znuny_id={7: 70},
    cmdb_catalog_by_znuny_id={21: 210},
    cmdb_class_ids_by_name={"computer": 71},
)


@pytest.mark.parametrize("name", sorted(PARITY))
def test_every_shipped_itsm_stat_maps_as_the_checklist(name: str) -> None:
    m = map_stat(itsm_fixture(name), _LOOKUPS, is_admin=True)
    assert m.definition is not None
    got = (
        m.target,
        m.definition.source,
        tuple(d.key for d in m.definition.dimensions),
        tuple(x.key for x in m.definition.measures),
    )
    assert got == PARITY[name]


def test_parity_covers_all_47() -> None:
    assert len(PARITY) == 47
    assert set(PARITY) == {p.name for p in ITSM_FIX.glob("ITSMStats-*.xml")}
