"""What the Znuny ITSM packages add to a core Znuny database (data only, no logic).

Source: the survey of the golden ITSM peers (``tests/golden/itsm_survey.py``, fixtures in
``backend/tests/fixtures/znuny-itsm/<profile>/initial_insert.sql``). Task 15 (``setup``) creates
exactly these rows when a site without Znuny ITSM switches ITSM on; ``test_itsm_survey_fixtures``
compares every entry with the fixture rows. Row ids are install order dependent and therefore not
part of the data; ``config`` is the dynamic field config as Znuny stores it (YAML) after
loading with ``yes``/``no`` kept as strings (Znuny writes ``No``/``Yes`` as plain scalars).

Both surveyed lines (Znuny 6.5, 7.3) ship identical rows here; the profile argument keeps the
door open for a version that differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROFILES = ("itsm-6", "itsm-7")


@dataclass(frozen=True)
class SeedTicketType:
    name: str


@dataclass(frozen=True)
class SeedTicketState:
    name: str
    type_name: str  # ticket_state_type.name of the Znuny core ("closed")
    comments: str


@dataclass(frozen=True)
class SeedHistoryType:
    name: str


@dataclass(frozen=True)
class SeedGroup:
    name: str
    comments: str


@dataclass(frozen=True)
class SeedDynamicField:
    name: str
    label: str
    field_type: str
    object_type: str
    field_order: int
    config: dict[str, Any]


@dataclass(frozen=True)
class SeedSet:
    ticket_types: tuple[SeedTicketType, ...]
    ticket_states: tuple[SeedTicketState, ...]
    ticket_history_types: tuple[SeedHistoryType, ...]
    groups: tuple[SeedGroup, ...]
    dynamic_fields: tuple[SeedDynamicField, ...]


_FIVE = ("1 very low", "2 low", "3 normal", "4 high", "5 very high")


def _datetime(default: int, years_in_future: int, years_in_past: int) -> dict[str, Any]:
    return {
        "DefaultValue": default,
        "Link": "",
        "YearsInFuture": years_in_future,
        "YearsInPast": years_in_past,
        "YearsPeriod": 1,
    }


def _dropdown(default: str, values: tuple[str, ...], *, none: int) -> dict[str, Any]:
    return {
        "DefaultValue": default,
        "Link": "",
        "PossibleNone": none,
        "PossibleValues": {value: value for value in values},
        "TranslatableValues": 1,
    }


_SEED = SeedSet(
    ticket_types=tuple(
        SeedTicketType(name)
        for name in ("Incident", "Incident::Major", "ServiceRequest", "Problem", "RfC")
    ),
    ticket_states=(
        SeedTicketState("closed with workaround", "closed", "Ticket is closed with workaround."),
    ),
    ticket_history_types=(SeedHistoryType("CILinkAdd"), SeedHistoryType("CILinkDelete")),
    groups=(
        SeedGroup("itsm-service", "Group for ITSM Service mask access in the agent interface."),
        SeedGroup(
            "itsm-configitem", "Group for ITSM ConfigItem mask access in the agent interface."
        ),
        SeedGroup("itsm-change", "Group for ITSM Change mask access in the agent interface."),
        SeedGroup("itsm-change-builder", "Group for ITSM Change Builders."),
        SeedGroup("itsm-change-manager", "Group for ITSM Change Managers."),
    ),
    dynamic_fields=(
        SeedDynamicField(
            "ITSMCriticality",
            "Criticality",
            "Dropdown",
            "Ticket",
            2,
            _dropdown("", _FIVE, none=1),
        ),
        SeedDynamicField(
            "ITSMImpact",
            "Impact",
            "Dropdown",
            "Ticket",
            3,
            _dropdown("3 normal", _FIVE, none=1),
        ),
        SeedDynamicField(
            "ITSMReviewRequired",
            "Review Required",
            "Dropdown",
            "Ticket",
            4,
            _dropdown("No", ("No", "Yes"), none=0),
        ),
        SeedDynamicField(
            "ITSMDecisionResult",
            "Decision Result",
            "Dropdown",
            "Ticket",
            5,
            _dropdown(
                "Pending",
                ("Approved", "Pending", "Postponed", "Pre-approved", "Rejected"),
                none=1,
            ),
        ),
        SeedDynamicField(
            "ITSMRepairStartTime",
            "Repair Start Time",
            "DateTime",
            "Ticket",
            6,
            _datetime(0, 5, 5),
        ),
        SeedDynamicField(
            "ITSMRecoveryStartTime",
            "Recovery Start Time",
            "DateTime",
            "Ticket",
            7,
            _datetime(0, 5, 5),
        ),
        SeedDynamicField(
            "ITSMDecisionDate", "Decision Date", "DateTime", "Ticket", 8, _datetime(0, 5, 5)
        ),
        SeedDynamicField(
            "ITSMDueDate", "Due Date", "DateTime", "Ticket", 9, _datetime(259200, 1, 9)
        ),
    ),
)


def for_profile(profile: str) -> SeedSet:
    """The seed rows of a schema profile (``itsm-6`` = Znuny 6.5, ``itsm-7`` = Znuny 7.3)."""
    if profile not in PROFILES:
        raise ValueError(f"unknown ITSM schema profile {profile!r}")
    return _SEED
