from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tiqora.plugins.events import (
    ARTICLE_HISTORY_TYPES,
    HISTORY_EVENT_MAP,
    EventSubscriber,
    GapTracker,
    HistoryRow,
    events_from_history,
)

T = datetime(2026, 10, 10, 9, 0)

#: ticket_history_type names (ids 1-52) from
#: backend/src/tiqora/bootstrap/schema/initial_insert.mysql.sql (Znuny 6.0-7.3)
ZNUNY_HISTORY_TYPES = frozenset(
    {
        "NewTicket", "FollowUp", "SendAutoReject", "SendAutoReply", "SendAutoFollowUp", "Forward",
        "Bounce", "SendAnswer", "SendAgentNotification", "SendCustomerNotification", "EmailAgent",
        "EmailCustomer", "PhoneCallAgent", "PhoneCallCustomer", "AddNote", "Move", "Lock", "Unlock",
        "Remove", "TimeAccounting", "CustomerUpdate", "PriorityUpdate", "OwnerUpdate",
        "LoopProtection", "Misc", "SetPendingTime", "StateUpdate", "TicketDynamicFieldUpdate",
        "WebRequestCustomer", "TicketLinkAdd", "TicketLinkDelete", "SystemRequest", "Merged",
        "ResponsibleUpdate", "Subscribe", "Unsubscribe", "TypeUpdate", "ServiceUpdate", "SLAUpdate",
        "ArchiveFlagUpdate", "EscalationSolutionTimeStop", "EscalationResponseTimeStart",
        "EscalationUpdateTimeStart", "EscalationSolutionTimeStart",
        "EscalationResponseTimeNotifyBefore", "EscalationUpdateTimeNotifyBefore",
        "EscalationSolutionTimeNotifyBefore", "EscalationResponseTimeStop",
        "EscalationUpdateTimeStop", "TitleUpdate", "EmailResend", "Bulk",
    }
)  # fmt: skip


async def _noop(*_a: object) -> None:
    return None


def _row(
    i: int,
    htype: str,
    *,
    ticket: int = 1,
    article: int | None = None,
    name: str = "%%",
    at: datetime = T,
) -> HistoryRow:
    return HistoryRow(
        id=i,
        history_type=htype,
        ticket_id=ticket,
        article_id=article,
        name=name,
        create_by=1,
        create_time=at,
    )


def test_reference_list_matches_seed_file() -> None:
    sql = Path("src/tiqora/bootstrap/schema/initial_insert.mysql.sql").read_text()
    seeded = re.findall(r"INSERT INTO ticket_history_type[^;]*?\(\d+, '(\w+)'", sql)
    assert len(seeded) == 52
    assert frozenset(seeded) == ZNUNY_HISTORY_TYPES


def test_map_only_uses_real_znuny_history_types() -> None:
    assert set(HISTORY_EVENT_MAP) <= ZNUNY_HISTORY_TYPES
    assert ARTICLE_HISTORY_TYPES <= ZNUNY_HISTORY_TYPES


@pytest.mark.parametrize(
    ("htype", "article", "name", "event"),
    [
        ("NewTicket", None, "%%", "TicketCreate"),
        ("StateUpdate", None, "%%new%%open", "TicketStateUpdate"),
        ("Move", None, "%%Raw%%2%%Misc%%3", "TicketQueueUpdate"),
        ("Lock", None, "%%lock", "TicketLockUpdate"),
        ("Unlock", None, "%%unlock", "TicketLockUpdate"),
        (
            "TicketDynamicFieldUpdate",
            None,
            "%%FieldName%%Vertrag%%Value%%A%%OldValue%%",
            "TicketDynamicFieldUpdate_Vertrag",
        ),
        ("TicketDynamicFieldUpdate", None, "garbled", "HistoryAdd"),
        ("TicketDynamicFieldUpdate", None, "%%FieldName%%%%Value%%A", "HistoryAdd"),
        ("AddNote", 77, "%%Notiz", "ArticleCreate"),
        ("AddNote", None, "%%Notiz", "HistoryAdd"),
        ("EmailCustomer", 78, "%%x", "ArticleCreate"),
        ("Misc", None, "%%x", "HistoryAdd"),
        ("TicketLinkAdd", None, "%%x", "HistoryAdd"),
    ],
)
def test_single_row_mapping(htype: str, article: int | None, name: str, event: str) -> None:
    (ev,) = events_from_history([_row(1, htype, article=article, name=name)])
    assert ev.event == event and ev.history_type == htype and ev.article_id == article


def test_article_id_zero_is_none() -> None:
    (ev,) = events_from_history([_row(1, "AddNote", article=0)])
    assert ev.article_id is None and ev.event == "HistoryAdd"


def test_event_carries_row_fields() -> None:
    (ev,) = events_from_history([_row(9, "AddNote", ticket=4, article=5, name="%%n")])
    assert (ev.id, ev.ticket_id, ev.name, ev.create_by, ev.create_time) == (9, 4, "%%n", 1, T)


def test_create_time_service_type_sla_rows_dropped() -> None:
    rows = [
        _row(1, "NewTicket"),
        _row(2, "TypeUpdate"),
        _row(3, "ServiceUpdate"),
        _row(4, "SLAUpdate"),
        _row(5, "TypeUpdate", at=T + timedelta(minutes=1)),
        _row(6, "ServiceUpdate", ticket=2),
    ]
    assert [(e.id, e.event) for e in events_from_history(rows)] == [
        (1, "TicketCreate"),
        (5, "TicketTypeUpdate"),
        (6, "TicketServiceUpdate"),
    ]


@pytest.mark.parametrize(
    ("events", "event", "ok"),
    [
        ({"*"}, "HistoryAdd", True),
        ({"TicketCreate"}, "TicketCreate", True),
        ({"TicketCreate"}, "TicketStateUpdate", False),
        ({"TicketDynamicFieldUpdate_*"}, "TicketDynamicFieldUpdate_Vertrag", True),
        ({"TicketDynamicFieldUpdate_Vertrag"}, "TicketDynamicFieldUpdate_Andere", False),
    ],
)
def test_subscriber_matches(events: set[str], event: str, ok: bool) -> None:
    sub = EventSubscriber(
        key="org.example.hello:xx",
        plugin_id="org.example.hello",
        events=frozenset(events),
        handler=_noop,
        timeout_seconds=5,
    )
    assert sub.matches(event) is ok


def test_gap_tracker_contiguous() -> None:
    g = GapTracker(hold_seconds=60)
    assert g.horizon(10, [11, 12, 13], now=0.0) == 13


def test_gap_tracker_holds_then_skips() -> None:
    g = GapTracker(hold_seconds=60)
    assert g.horizon(10, [11, 13, 14], now=0.0) == 11
    assert g.horizon(11, [13, 14], now=59.0) == 11
    assert g.horizon(11, [13, 14], now=60.0) == 14


def test_gap_tracker_late_row_fills_gap() -> None:
    g = GapTracker(hold_seconds=60)
    assert g.horizon(10, [11, 13], now=0.0) == 11
    assert g.horizon(11, [12, 13], now=5.0) == 13
    assert g.pending_gaps() == {}


def test_gap_tracker_step() -> None:
    g = GapTracker(hold_seconds=60, step=2)
    assert g.horizon(10, [12, 14, 18], now=0.0) == 14
    assert g.pending_gaps() == {16: 0.0}


def test_gap_tracker_empty_and_forgets() -> None:
    g = GapTracker(hold_seconds=1)
    assert g.horizon(10, [], now=0.0) == 10
    g.horizon(10, [12], now=0.0)
    assert g.pending_gaps() == {11: 0.0}
    assert g.horizon(10, [12], now=2.0) == 12
    assert g.pending_gaps() == {}


def test_gap_tracker_unsorted_duplicate_ids_and_per_row_cursor() -> None:
    g = GapTracker(hold_seconds=60)
    assert g.horizon(10, [13, 11, 11, 12], now=0.0) == 13
    # the dispatcher may persist a cursor after each matching event: any prefix horizon is stable
    assert g.horizon(11, [12, 13], now=1.0) == 13


def test_gap_tracker_second_gap_gets_own_clock() -> None:
    g = GapTracker(hold_seconds=10)
    assert g.horizon(10, [12, 14], now=0.0) == 10
    # the first gap is skipped at t=10; the second one is only first seen now and is held
    assert g.horizon(10, [12, 14], now=10.0) == 12
    assert g.pending_gaps() == {13: 10.0}


def test_gap_tracker_offset_ignores_foreign_ids() -> None:
    g = GapTracker(hold_seconds=60, step=2, offset=0)
    # odd ids belong to another writer: present, never gaps
    assert g.horizon(10, [11, 12, 13, 14], now=0.0) == 14
    assert g.pending_gaps() == {}


def test_gap_tracker_offset_only_foreign_ids_still_advances() -> None:
    g = GapTracker(hold_seconds=60, step=2, offset=0)
    assert g.horizon(10, [11], now=0.0) == 11


def test_gap_tracker_offset_gap_between_own_ids() -> None:
    g = GapTracker(hold_seconds=60, step=2, offset=0)
    assert g.horizon(10, [11, 14], now=0.0) == 11
    assert g.pending_gaps() == {12: 0.0}
