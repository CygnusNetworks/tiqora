"""Which report dimension keys name a person (or text that can name one).

One classification for both outward paths (dashboard wallboard links and, later, scheduled
report mails). A dimension whose values are people, customers or ticket titles must not leave
the building unredacted; every registered source dimension key is in exactly one of the two
sets (a test enumerates ``REPORT_SOURCES`` and fails on an unclassified key).
"""

from __future__ import annotations

from typing import Final

__all__ = ["NON_PERSON_DIMENSIONS", "PERSON_DIMENSIONS"]

#: values are users, customer companies / customer users, or ticket titles (``ticket``)
PERSON_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {"owner", "responsible", "actor", "agent", "customer", "customer_user", "ticket"}
)

NON_PERSON_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {
        "queue",
        "queue_at_create",
        "from_queue",
        "to_queue",
        "ticket_queue",
        "state",
        "from_state",
        "to_state",
        "ticket_state",
        "state_type",
        "from_state_type",
        "to_state_type",
        "priority",
        "ticket_priority",
        "type",
        "ticket_type",
        "service",
        "sla",
        "channel",
        "process",
        "kind",
        "created",
        "closed",
        "first_response",
        "created_hour",
        "created_weekday",
        "occurred",
        "occurred_hour",
        "occurred_weekday",
        "snapshot",
        "booked",
        "booked_weekday",
    }
)
