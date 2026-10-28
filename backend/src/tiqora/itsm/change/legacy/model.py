"""A Znuny change as Tiqora shows it (design I16; field names are a contract of the UI and X2).

Times are naive UTC as Znuny stores them, ``None`` for "not set" (never the sentinel
``9999-01-01 00:00:00``). Texts are the stripped ``*_plain`` columns. Catalog values and users are
names/logins, not ids. ``workorder_number`` 0 means the change itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

CabKind = Literal["user", "customer_user"]


@dataclass(frozen=True, slots=True)
class LegacyWorkOrder:
    number: int
    title: str | None
    work_order_type: str | None
    state: str | None
    agent_login: str | None
    planned_start: datetime | None
    planned_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    planned_effort: float
    accounted_time: float
    instruction: str | None
    report: str | None
    #: dynamic fields of object type ``ITSMWorkOrder``: name -> values
    dynamic_fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LegacyLink:
    #: the other end: ``Ticket``, ``ITSMConfigItem``, ``Service``, …
    object: str
    key: str
    link_type: str
    #: the change's (or work order's) side of the stored relation
    direction: Literal["source", "target"]
    #: 0 = linked to the change; CIs and services hang off work orders (survey Q1)
    workorder_number: int


@dataclass(frozen=True, slots=True)
class LegacyHistoryEntry:
    created_at: datetime
    user_login: str | None
    #: ``change_history_type.name`` (``ChangeUpdate``, ``WorkOrderAdd``, …)
    history_type: str
    field: str | None
    #: raw ``content_old`` / ``content_new`` (ids for ``*ID`` fields, ``%%``-joined lists)
    old: str | None
    new: str | None
    workorder_number: int
    #: ``old`` / ``new`` for display: catalog ids as names, user ids as logins, CAB lists
    #: comma-separated; everything else (and anything unresolvable) as the raw value
    old_display: str | None = None
    new_display: str | None = None


@dataclass(frozen=True, slots=True)
class LegacyChange:
    znuny_change_id: int
    number: str
    title: str | None
    state: str | None
    category: str | None
    impact: str | None
    priority: str | None
    manager_login: str | None
    builder_login: str | None
    requested_time: datetime | None
    #: derived from the work orders exactly like Znuny's ``ChangeGet`` (survey Q2)
    planned_start: datetime | None
    planned_end: datetime | None
    actual_start: datetime | None
    actual_end: datetime | None
    created_at: datetime
    changed_at: datetime
    description: str | None
    justification: str | None
    #: agents by login first (user id order), then customer users by login
    cab: tuple[tuple[CabKind, str], ...]
    workorders: tuple[LegacyWorkOrder, ...]
    history: tuple[LegacyHistoryEntry, ...]
    #: dynamic fields of object type ``ITSMChange``: name -> values
    dynamic_fields: Mapping[str, tuple[str, ...]]
    links: tuple[LegacyLink, ...]


@dataclass(frozen=True, slots=True)
class LegacyChangeSummary:
    znuny_change_id: int
    number: str
    title: str | None
    state: str | None
    planned_start: datetime | None
    planned_end: datetime | None
    workorder_count: int
    changed_at: datetime
