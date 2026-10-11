"""Pure tests of the event data builder (``tiqora.gi.events``, G9)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from tiqora.gi.events import event_data
from tiqora.plugins.events import TicketEvent


def _event(
    event: str,
    name: str = "",
    *,
    ticket_id: int = 5,
    article_id: int | None = None,
    create_by: int = 7,
) -> TicketEvent:
    return TicketEvent(
        id=1,
        event=event,
        history_type="X",
        ticket_id=ticket_id,
        article_id=article_id,
        name=name,
        create_by=create_by,
        create_time=datetime(2026, 1, 1),
    )


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (_event("TicketCreate", "%%tn"), {"TicketID": 5}),
        (_event("TicketStateUpdate", "%%new%%open%%"), {"TicketID": 5}),
        (_event("TicketQueueUpdate", "%%Raw%%1%%Misc%%2"), {"TicketID": 5}),
        (_event("ArticleCreate", article_id=9), {"TicketID": 5, "ArticleID": 9}),
        (_event("TicketAccountTime", "%%1", article_id=9), {"TicketID": 5, "ArticleID": 9}),
        (_event("TicketAccountTime", "%%1"), {"TicketID": 5}),
        (
            _event("TicketDynamicFieldUpdate_GiTag", "%%FieldName%%GiTag%%Value%%a%%OldValue%%b"),
            {"TicketID": 5, "FieldName": "GiTag", "Value": "a", "OldValue": "b", "UserID": 7},
        ),
        (
            _event("TicketDynamicFieldUpdate_GiTag", "%%FieldName%%GiTag%%Value%%a%%OldValue%%"),
            {"TicketID": 5, "FieldName": "GiTag", "Value": "a", "OldValue": "", "UserID": 7},
        ),
        (
            _event("TicketDynamicFieldUpdate_GiTag", "%%FieldName%%GiTag"),
            {"TicketID": 5, "FieldName": "GiTag", "UserID": 7},
        ),
        (
            _event("TicketMerge", "%%2026010110000001%%11%%2026010110000002%%12", ticket_id=12),
            {"TicketID": 11, "MainTicketID": 12},
        ),
        (_event("TicketMerge", "garbage"), {"TicketID": 5}),
    ],
)
def test_event_data(event: TicketEvent, expected: dict[str, Any]) -> None:
    assert event_data(event) == expected


def test_old_ticket_data_is_never_present() -> None:
    for name in ("TicketStateUpdate", "TicketQueueUpdate", "TicketSLAUpdate", "ArticleCreate"):
        assert "OldTicketData" not in event_data(_event(name, article_id=3))
