"""Ticket presets of counters and ticket lists (D6 rule 5).

Each preset maps to the ``TicketService`` filter arguments for viewer ``uid`` — the same
arguments ``count_dashboard_summary`` uses, so counters and lists agree with the numbers of
the old home page. ``escalated`` is the SLA breach only (``escalating_within=0``), not the list
filter ``escalated=True``, which also includes the AI handoff.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from tiqora.domain.ticket_service import UNASSIGNED_OWNER_ID
from tiqora.filters import Filter
from tiqora.filters.ticket_params import TicketListParams, filter_from_list_params

TICKET_PRESETS: Final[dict[str, Callable[[int], dict[str, Any]]]] = {
    "mine_open": lambda uid: {"state_type": "open", "owner_id": uid},
    "mine_new": lambda uid: {"state_type": "new", "owner_id": uid},
    "unowned_new": lambda uid: {"state_type": "new", "owner_id": UNASSIGNED_OWNER_ID},
    "escalated": lambda uid: {"state_type": "open", "escalating_within": 0},
    "ai_escalated": lambda uid: {"state_type": "open", "ai_escalated": True},
}


def preset_kwargs(preset: str, user_id: int) -> dict[str, Any]:
    """Filter arguments of ``preset`` for viewer ``user_id``; ``KeyError`` when unknown."""
    return TICKET_PRESETS[preset](user_id)


def preset_filter(preset: str, user_id: int) -> Filter | None:
    """The wave-0 filter that selects exactly what ``preset`` lists for ``user_id``.

    Derived from the preset's ``TicketService`` arguments through the same bridge the ticket
    list parity test pins (``filter_from_list_params``), so list, counter and a saved search
    agree. ``None`` only for an unknown preset: all five core presets are expressible.
    """
    if preset not in TICKET_PRESETS:
        return None
    return filter_from_list_params(TicketListParams(**preset_kwargs(preset, user_id)))
