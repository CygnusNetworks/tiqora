"""Pure tests for the TicketSearch time helpers (``create_time_settings``, ``time_filters``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from tiqora.api.compat.search_filters import create_time_settings, time_filters

NOW = datetime(2026, 3, 10, 12, 0, 0)


def test_time_point_last_two_days() -> None:
    out = create_time_settings(
        {
            "ChangeTimeSearchType": "TimePoint",
            "TicketChangeTimePoint": "2",
            "TicketChangeTimePointFormat": "day",
            "TicketChangeTimePointStart": "Last",
        },
        now=NOW,
    )
    assert out["TicketChangeTimeNewerMinutes"] == 2 * 24 * 60
    assert "TicketChangeTimeOlderMinutes" not in out


def test_time_point_before_is_older_minutes() -> None:
    out = create_time_settings(
        {
            "CloseTimeSearchType": "TimePoint",
            "TicketCloseTimePoint": "3",
            "TicketCloseTimePointFormat": "week",
            "TicketCloseTimePointStart": "Before",
        },
        now=NOW,
    )
    assert out["TicketCloseTimeOlderMinutes"] == 3 * 60 * 24 * 7


def test_time_point_needs_all_three_parts() -> None:
    out = create_time_settings(
        {
            "LastChangeTimeSearchType": "TimePoint",
            "TicketLastChangeTimePoint": "2",
            "TicketLastChangeTimePointFormat": "day",
        },
        now=NOW,
    )
    assert "TicketLastChangeTimeNewerMinutes" not in out
    assert "TicketLastChangeTimeOlderMinutes" not in out


def test_time_slot_to_dates() -> None:
    out = create_time_settings(
        {
            "ChangeTimeSearchType": "TimeSlot",
            "TicketChangeTimeStartYear": "2026",
            "TicketChangeTimeStartMonth": "3",
            "TicketChangeTimeStartDay": "5",
            "TicketChangeTimeStopYear": "2026",
            "TicketChangeTimeStopMonth": "4",
            "TicketChangeTimeStopDay": "9",
        },
        now=NOW,
    )
    assert out["TicketChangeTimeNewerDate"] == "2026-03-05 00:00:00"
    assert out["TicketChangeTimeOlderDate"] == "2026-04-09 23:59:59"


def test_time_slot_missing_day_gives_znuny_zero_padded_invalid_date() -> None:
    # Znuny's sprintf("%02d") turns a missing day into "00" (a true value), so the
    # unusable date reaches the core search, which then returns nothing.
    out = create_time_settings(
        {
            "ChangeTimeSearchType": "TimeSlot",
            "TicketChangeTimeStartYear": "2026",
            "TicketChangeTimeStartMonth": "3",
        },
        now=NOW,
    )
    assert out["TicketChangeTimeNewerDate"] == "2026-03-00 00:00:00"
    conditions: list[str] = []
    assert time_filters(out, conditions, {}, [], now=NOW) is False


def test_create_time_settings_does_not_mutate_input() -> None:
    data: dict[str, Any] = {"ChangeTimeSearchType": "TimeSlot"}
    create_time_settings(data, now=NOW)
    assert data == {"ChangeTimeSearchType": "TimeSlot"}


def test_invalid_date_string_aborts() -> None:
    assert time_filters({"TicketChangeTimeNewerDate": "yesterday"}, [], {}, [], now=NOW) is False
    assert (
        time_filters({"TicketCreateTimeOlderDate": "2026-13-01 00:00:00"}, [], {}, [], now=NOW)
        is False
    )


def test_newer_date_in_the_future_aborts() -> None:
    assert (
        time_filters({"TicketCreateTimeNewerDate": "2026-03-11 00:00:00"}, [], {}, [], now=NOW)
        is False
    )


def test_newer_after_older_aborts() -> None:
    data = {
        "TicketCloseTimeNewerDate": "2026-03-05 00:00:00",
        "TicketCloseTimeOlderDate": "2026-03-01 00:00:00",
    }
    assert time_filters(data, [], {}, [], now=NOW) is False


def test_create_window_binds_datetimes() -> None:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    ok = time_filters(
        {"TicketCreateTimeNewerMinutes": "60", "TicketCreateTimeOlderDate": "2026-03-10 11:00:00"},
        conditions,
        params,
        [],
        now=NOW,
    )
    assert ok is True
    assert conditions == ["t.create_time >= :tw1", "t.create_time <= :tw2"]
    assert params == {
        "tw1": datetime(2026, 3, 10, 11, 0, 0),
        "tw2": datetime(2026, 3, 10, 11, 0, 0),
    }


def test_escalation_excludes_zero_and_uses_epoch() -> None:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    assert time_filters(
        {"TicketEscalationResponseTimeOlderMinutes": "0"}, conditions, params, [], now=NOW
    )
    assert conditions == [
        "t.escalation_response_time != 0",
        "t.escalation_response_time <= :tw1",
    ]
    assert params == {"tw1": 1773144000}  # 2026-03-10 12:00:00 UTC


def test_change_bounds_get_their_own_history_join() -> None:
    conditions: list[str] = []
    joins: list[str] = []
    assert time_filters(
        {
            "TicketChangeTimeNewerDate": "2026-03-01 00:00:00",
            "TicketChangeTimeOlderDate": "2026-03-09 00:00:00",
        },
        conditions,
        {},
        joins,
        now=NOW,
    )
    assert len(joins) == 2
    assert joins[0] != joins[1]
    assert conditions == ["thw1.create_time <= :tw1", "thw2.create_time >= :tw2"]


def test_close_uses_new_ticket_and_state_update_with_closed_states() -> None:
    conditions: list[str] = []
    joins: list[str] = []
    assert time_filters({"TicketCloseTimeNewerMinutes": "30"}, conditions, {}, joins, now=NOW)
    assert len(joins) == 1
    assert "'NewTicket', 'StateUpdate'" in conditions[0]
    assert "tst.name = 'closed'" in conditions[0]
    assert conditions[1] == "thw1.create_time >= :tw1"


def test_pending_is_restricted_to_pending_state_types() -> None:
    conditions: list[str] = []
    params: dict[str, Any] = {}
    assert time_filters({"TicketPendingTimeOlderMinutes": "5"}, conditions, params, [], now=NOW)
    assert "'pending reminder', 'pending auto'" in conditions[0]
    assert conditions[1] == "t.until_time <= :tw1"
    assert params == {"tw1": 1773144000 - 300}


def test_no_time_params_adds_nothing() -> None:
    conditions: list[str] = []
    assert time_filters({"Title": "x"}, conditions, {}, [], now=NOW)
    assert conditions == []
