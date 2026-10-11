"""Znuny search-profile -> Tiqora filter mapping (pure, no database)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from tiqora.filters.catalog import FilterError, validate_filter
from tiqora.filters.tickets import TICKET_FILTER_FIELDS, _df_spec, _DynamicFieldInfo
from tiqora.saved_searches.znuny_import import (
    ARCHIVE_REASON,
    DF_TYPE_REASON,
    FULLTEXT_REASON,
    FUTURE_REASON,
    INVALID_DATE_REASON,
    INVALID_VALUE_REASON,
    NO_TIME_FIELD_REASON,
    OPERATOR_REASON,
    SLOT_ORDER_REASON,
    UNKNOWN_DF_REASON,
    UNSUPPORTED_REASON,
    MappedProfile,
    ZnunyLookups,
    map_search_profile,
)

LOOKUPS = ZnunyLookups(
    state_type_names={1: "new", 2: "open", 3: "closed"},
    queue_names={10: "Support", 11: "Support::Level 2", 12: "Support::Level 2::Netz", 13: "Sales"},
    lock_names={1: "unlock", 2: "lock", 3: "tmp_lock"},
    dynamic_fields={
        "Area": "Dropdown",
        "Note": "Text",
        "Vip": "Checkbox",
        "Due": "Date",
        "Ref": "ReferenceDF",
    },
    time_zone="Europe/Berlin",
)


def where(m: MappedProfile) -> Any:
    return m.criteria.model_dump(by_alias=True, exclude_none=True).get("where")


def _map(values: dict[str, Any], **kw: Any) -> MappedProfile:
    return map_search_profile("P", values, LOOKUPS, **kw)


def _reported(m: MappedProfile) -> list[tuple[str, str]]:
    return [(u.key, u.reason) for u in m.unmapped]


def test_id_lists_and_state_types() -> None:
    m = _map(
        {
            "QueueIDs": ["13"],
            "StateTypeIDs": ["1", "2"],
            "OwnerIDs": ["5"],
            "PriorityIDs": ["3"],
            "WatchUserIDs": ["7"],
        }
    )
    assert where(m) == {
        "all": [  # order = rows of the D9 mapping table
            {"field": "queue_id", "op": "in", "value": [13]},
            {"field": "priority_id", "op": "in", "value": [3]},
            {"field": "owner_id", "op": "in", "value": [5]},
            {"field": "state_type", "op": "in", "value": ["new", "open"]},
            {"field": "watched_by", "op": "in", "value": [7]},
        ]
    }
    assert m.unmapped == ()


def test_unknown_queue_ids_are_kept() -> None:
    assert where(_map({"QueueIDs": ["999"]})) == {"field": "queue_id", "op": "in", "value": [999]}


@pytest.mark.parametrize(
    ("queue", "expected"),
    [
        ("10", [10, 11, 12]),  # nesting: every level below the parent
        ("11", [11, 12]),
        ("12", [12]),
        ("13", [13]),
    ],
)
def test_sub_queues_expand_by_name(queue: str, expected: list[int]) -> None:
    m = _map({"QueueIDs": [queue], "UseSubQueues": "1"})
    assert where(m) == {"field": "queue_id", "op": "in", "value": expected}


def test_sub_queues_flag_is_perl_truthy() -> None:
    off = {"field": "queue_id", "op": "in", "value": [11]}
    assert where(_map({"QueueIDs": ["11"], "UseSubQueues": "0"})) == off
    assert where(_map({"QueueIDs": ["11"], "UseSubQueues": ""})) == off
    assert where(_map({"QueueIDs": ["11"]})) == off
    # truthy but not "1" (Perl): still expands
    assert where(_map({"QueueIDs": ["11"], "UseSubQueues": "yes"}))["value"] == [11, 12]


def test_sub_queues_merge_several_parents_without_duplicates() -> None:
    m = _map({"QueueIDs": ["11", "12", "13"], "UseSubQueues": "1"})
    assert where(m)["value"] == [11, 12, 13]


@pytest.mark.parametrize("key", ["QueueIDs", "OwnerIDs", "WatchUserIDs", "StateTypeIDs", "LockIDs"])
def test_non_integer_id_value_rejects_the_key(key: str) -> None:
    m = _map({key: ["4", "abc"], "PriorityIDs": ["3"]})
    assert where(m) == {"field": "priority_id", "op": "in", "value": [3]}
    assert _reported(m) == [(key, INVALID_VALUE_REASON)]
    assert m.unmapped[0].value == "4, abc"


def test_lock_ids() -> None:
    assert where(_map({"LockIDs": ["2", "3"]})) == {"field": "locked", "op": "is_true"}
    assert where(_map({"LockIDs": ["1"]})) == {"field": "locked", "op": "is_false"}
    assert where(_map({"LockIDs": ["1", "2"]})) is None
    m = _map({"LockIDs": ["77"]})
    assert where(m) is None and [u.key for u in m.unmapped] == ["LockIDs"]


@pytest.mark.parametrize(
    ("value", "cond"),
    [
        ("2026101012345", {"field": "tn", "op": "eq", "value": "2026101012345"}),
        ("2026*", {"field": "tn", "op": "starts_with", "value": "2026"}),
        ("*1234*", {"field": "tn", "op": "contains", "value": "1234"}),
    ],
)
def test_ticket_number_pattern_rule(value: str, cond: dict[str, Any]) -> None:
    assert where(_map({"TicketNumber": value})) == cond


@pytest.mark.parametrize("value", ["*abc", "a*b", "20*6*", "(1)", "1 && 2", "!1", '"1"', "1 - 2"])
def test_ticket_number_unmappable_patterns(value: str) -> None:
    m = _map({"TicketNumber": value})
    assert where(m) is None
    assert _reported(m) == [("TicketNumber", OPERATOR_REASON)]


def test_ticket_number_list_is_any() -> None:
    m = _map({"TicketNumber": ["2026*", "55"]})
    assert where(m) == {
        "any": [
            {"field": "tn", "op": "starts_with", "value": "2026"},
            {"field": "tn", "op": "eq", "value": "55"},
        ]
    }


def test_title_words_are_all_contains() -> None:
    assert where(_map({"Title": "Drucker  Etage"})) == {
        "all": [
            {"field": "title", "op": "contains", "value": "Drucker"},
            {"field": "title", "op": "contains", "value": "Etage"},
        ]
    }
    assert where(_map({"Title": "*Drucker*"})) == {
        "field": "title",
        "op": "contains",
        "value": "Drucker",
    }


def test_customer_raw_wins_over_pattern_key() -> None:
    m = _map({"CustomerIDRaw": "ACME*", "CustomerID": "other*"})
    assert where(m) == {"field": "customer_id", "op": "eq", "value": "ACME*"}
    assert m.unmapped == ()


def test_customer_pattern_and_lists() -> None:
    m = _map({"CustomerID": "acme*", "CustomerUserLoginRaw": ["a@example.org", "b@example.org"]})
    assert where(m) == {
        "all": [
            {"field": "customer_user_id", "op": "in", "value": ["a@example.org", "b@example.org"]},
            {"field": "customer_id", "op": "starts_with", "value": "acme"},
        ]
    }


def test_archive_flags_for_admin() -> None:
    m = _map({"SearchInArchive": "ArchivedTickets"}, is_admin=True)
    assert m.include_archived and where(m) == {"field": "archived", "op": "is_true"}
    assert m.unmapped == ()
    m = _map({"SearchInArchive": "AllTickets"}, is_admin=True)
    assert m.include_archived and where(m) is None and m.unmapped == ()
    m = _map({"SearchInArchive": "NotArchivedTickets"}, is_admin=True)
    assert not m.include_archived and where(m) is None and m.unmapped == ()


@pytest.mark.parametrize("mode", ["ArchivedTickets", "AllTickets"])
def test_archive_flags_for_non_admin_are_reported(mode: str) -> None:
    for kw in ({}, {"is_admin": False}):
        m = _map({"SearchInArchive": mode, "QueueIDs": ["13"]}, **kw)
        assert not m.include_archived
        assert where(m) == {"field": "queue_id", "op": "in", "value": [13]}
        assert _reported(m) == [("SearchInArchive", ARCHIVE_REASON)]
    m = _map({"SearchInArchive": "NotArchivedTickets"}, is_admin=False)
    assert m.unmapped == () and not m.include_archived


def test_time_point_last_and_before_with_month_as_30_days() -> None:
    m = _map(
        {
            "TimeSearchType": "TimePoint",
            "TicketCreateTimePoint": "2",
            "TicketCreateTimePointStart": "Last",
            "TicketCreateTimePointFormat": "month",
        }
    )
    assert where(m) == {
        "field": "created",
        "op": "within_last",
        "value": {"amount": 60, "unit": "day"},
    }
    m = _map(
        {
            "ChangeTimeSearchType": "TimePoint",
            "TicketChangeTimePoint": "1",
            "TicketChangeTimePointStart": "Before",
            "TicketChangeTimePointFormat": "year",
        }
    )
    assert where(m) == {
        "field": "changed",
        "op": "older_than",
        "value": {"amount": 365, "unit": "day"},
    }
    assert m.unmapped == ()


def test_pending_time_point_last_maps() -> None:
    m = _map(
        {
            "PendingTimeSearchType": "TimePoint",
            "TicketPendingTimePoint": "3",
            "TicketPendingTimePointStart": "Last",
            "TicketPendingTimePointFormat": "hour",
        }
    )
    assert where(m) == {
        "field": "pending_until",
        "op": "within_last",
        "value": {"amount": 3, "unit": "hour"},
    }


def test_time_slot_is_local_day_bounds_in_utc() -> None:
    m = _map(
        {
            "TimeSearchType": "TimeSlot",
            "TicketCreateTimeStartYear": "2026",
            "TicketCreateTimeStartMonth": "7",
            "TicketCreateTimeStartDay": "1",
            "TicketCreateTimeStopYear": "2026",
            "TicketCreateTimeStopMonth": "07",
            "TicketCreateTimeStopDay": "31",
        }
    )
    assert where(m) == {
        "field": "created",
        "op": "between",
        "value": {"from": "2026-06-30T22:00:00", "to": "2026-07-31T22:00:00"},
    }  # CEST = UTC+2
    assert m.unmapped == ()


def test_time_slot_in_winter_uses_utc_plus_one() -> None:
    m = _map(
        {
            "ChangeTimeSearchType": "TimeSlot",
            "TicketChangeTimeStartYear": "2026",
            "TicketChangeTimeStartMonth": "1",
            "TicketChangeTimeStartDay": "10",
            "TicketChangeTimeStopYear": "2026",
            "TicketChangeTimeStopMonth": "1",
            "TicketChangeTimeStopDay": "10",
        }
    )
    assert where(m)["value"] == {"from": "2026-01-09T23:00:00", "to": "2026-01-10T23:00:00"}


# (profile values, reported key, reason). Future time points report the AMOUNT key,
# rejected / unmappable time fields and broken slots the "...SearchType" key (D9).
_TIME_PROBLEMS = [
    pytest.param(
        {
            "PendingTimeSearchType": "TimePoint",
            "TicketPendingTimePoint": "1",
            "TicketPendingTimePointStart": "Next",
            "TicketPendingTimePointFormat": "day",
        },
        "TicketPendingTimePoint",
        FUTURE_REASON,
        id="future-next",
    ),
    pytest.param(
        {
            "TimeSearchType": "TimePoint",
            "TicketCreateTimePoint": "1",
            "TicketCreateTimePointStart": "After",
            "TicketCreateTimePointFormat": "week",
        },
        "TicketCreateTimePoint",
        FUTURE_REASON,
        id="future-after",
    ),
    pytest.param(
        {
            "TimeSearchType": "TimePoint",
            "TicketCreateTimePoint": "0",
            "TicketCreateTimePointStart": "Last",
            "TicketCreateTimePointFormat": "day",
        },
        "TicketCreateTimePoint",
        INVALID_VALUE_REASON,
        id="amount-zero",
    ),
    pytest.param(
        {
            "ChangeTimeSearchType": "TimePoint",
            "TicketChangeTimePoint": "x",
            "TicketChangeTimePointStart": "Last",
            "TicketChangeTimePointFormat": "day",
        },
        "TicketChangeTimePoint",
        INVALID_VALUE_REASON,
        id="amount-not-a-number",
    ),
    pytest.param(
        {
            "TimeSearchType": "TimePoint",
            "TicketCreateTimePoint": "1",
            "TicketCreateTimePointStart": "Last",
            "TicketCreateTimePointFormat": "decade",
        },
        "TicketCreateTimePoint",
        INVALID_VALUE_REASON,
        id="unknown-unit",
    ),
    pytest.param(
        {
            "TimeSearchType": "TimeSlot",
            "TicketCreateTimeStartYear": "2026",
            "TicketCreateTimeStartMonth": "2",
            "TicketCreateTimeStartDay": "30",
            "TicketCreateTimeStopYear": "2026",
            "TicketCreateTimeStopMonth": "3",
            "TicketCreateTimeStopDay": "1",
        },
        "TimeSearchType",
        INVALID_DATE_REASON,
        id="invalid-date-feb-30",
    ),
    pytest.param(
        {
            "PendingTimeSearchType": "TimeSlot",
            "TicketPendingTimeStartYear": "2026",
            "TicketPendingTimeStartMonth": "13",
            "TicketPendingTimeStartDay": "1",
            "TicketPendingTimeStopYear": "2026",
            "TicketPendingTimeStopMonth": "12",
            "TicketPendingTimeStopDay": "1",
        },
        "PendingTimeSearchType",
        INVALID_DATE_REASON,
        id="invalid-date-month-13",
    ),
    pytest.param(
        {
            "TimeSearchType": "TimeSlot",
            "TicketCreateTimeStartYear": "2026",
            "TicketCreateTimeStartMonth": "x",
            "TicketCreateTimeStartDay": "1",
            "TicketCreateTimeStopYear": "2026",
            "TicketCreateTimeStopMonth": "7",
            "TicketCreateTimeStopDay": "1",
        },
        "TimeSearchType",
        INVALID_DATE_REASON,
        id="date-not-a-number",
    ),
    pytest.param(
        {
            "CloseTimeSearchType": "TimePoint",
            "TicketCloseTimePoint": "1",
            "TicketCloseTimePointStart": "Last",
            "TicketCloseTimePointFormat": "day",
        },
        "CloseTimeSearchType",
        NO_TIME_FIELD_REASON,
        id="no-close-time-field",
    ),
    pytest.param(
        {"EscalationTimeSearchType": "TimeSlot", "TicketEscalationTimeStartYear": "2026"},
        "EscalationTimeSearchType",
        NO_TIME_FIELD_REASON,
        id="no-escalation-time-field",
    ),
    pytest.param(
        {"TimeSearchType": "Sometime"},
        "TimeSearchType",
        UNSUPPORTED_REASON,
        id="unknown-search-type",
    ),
]


@pytest.mark.parametrize(("values", "key", "reason"), _TIME_PROBLEMS)
def test_time_problems_report_one_key_and_add_no_condition(
    values: dict[str, Any], key: str, reason: str
) -> None:
    m = _map(values)
    assert where(m) is None
    assert _reported(m) == [(key, reason)]


def test_time_search_type_none_and_blank_sub_keys_are_silent() -> None:
    m = _map(
        {
            "TimeSearchType": "None",
            "TicketCreateTimePoint": "",
            "TicketCreateTimePointStart": "Last",
            "TicketCreateTimePointFormat": "day",
            "CloseTimeSearchType": "",
            "TicketCloseTimeStartYear": "2026",
        }
    )
    assert where(m) is None and m.unmapped == ()


def test_dynamic_fields_by_type() -> None:
    m = _map(
        {
            "Search_DynamicField_Area": ["Nord", "Süd"],
            "Search_DynamicField_Note": "*VIP*",
            "Search_DynamicField_Vip": ["1"],
        }
    )
    assert where(m) == {
        "all": [
            {"field": "df.Area", "op": "in", "value": ["Nord", "Süd"]},
            {"field": "df.Note", "op": "contains", "value": "VIP"},
            {"field": "df.Vip", "op": "is_true"},
        ]
    }
    assert where(_map({"Search_DynamicField_Vip": ["1", "-1"]})) is None
    assert where(_map({"Search_DynamicField_Vip": ["-1"]})) == {"field": "df.Vip", "op": "is_false"}
    m = _map({"Search_DynamicField_Vip": ["2"]})
    assert _reported(m) == [("Search_DynamicField_Vip", INVALID_VALUE_REASON)]


def test_dynamic_text_operators_are_reported() -> None:
    m = _map({"Search_DynamicField_Note": "a || b"})
    assert where(m) is None
    assert _reported(m) == [("Search_DynamicField_Note", OPERATOR_REASON)]


def test_dynamic_date_accepts_swapped_format_and_start() -> None:
    a = _map(
        {
            "Search_DynamicField_DueTimePoint": "1",
            "Search_DynamicField_DueTimePointFormat": "week",
            "Search_DynamicField_DueTimePointStart": "Before",
            "Search_DynamicField_DueTimePointValue": "2",
        }
    )
    b = _map(
        {
            "Search_DynamicField_DueTimePoint": "1",
            "Search_DynamicField_DueTimePointFormat": "Before",
            "Search_DynamicField_DueTimePointStart": "week",
            "Search_DynamicField_DueTimePointValue": "2",
        }
    )
    expected = {"field": "df.Due", "op": "older_than", "value": {"amount": 2, "unit": "week"}}
    assert where(a) == expected and where(b) == expected
    assert a.unmapped == () and b.unmapped == ()


def test_dynamic_date_flag_is_perl_truthy() -> None:
    m = _map(
        {
            "Search_DynamicField_DueTimePoint": "0",
            "Search_DynamicField_DueTimePointFormat": "week",
            "Search_DynamicField_DueTimePointStart": "Last",
            "Search_DynamicField_DueTimePointValue": "2",
        }
    )
    assert where(m) is None and m.unmapped == ()


def test_dynamic_date_future_reports_the_flag_key() -> None:
    m = _map(
        {
            "Search_DynamicField_DueTimePoint": "1",
            "Search_DynamicField_DueTimePointFormat": "day",
            "Search_DynamicField_DueTimePointStart": "Next",
            "Search_DynamicField_DueTimePointValue": "2",
        }
    )
    assert where(m) is None
    assert _reported(m) == [("Search_DynamicField_DueTimePoint", FUTURE_REASON)]


def test_dynamic_date_slot_is_local_to_utc() -> None:
    p = "Search_DynamicField_DueTimeSlot"
    m = _map(
        {
            p: "1",
            f"{p}StartYear": "2026",
            f"{p}StartMonth": "7",
            f"{p}StartDay": "1",
            f"{p}StartHour": "12",
            f"{p}StartMinute": "30",
            f"{p}StartSecond": "0",
            f"{p}StopYear": "2026",
            f"{p}StopMonth": "7",
            f"{p}StopDay": "1",
        }
    )
    assert where(m) == {
        "field": "df.Due",
        "op": "between",
        "value": {"from": "2026-07-01T10:30:00", "to": "2026-07-01T22:00:00"},
    }
    assert m.unmapped == ()


def test_dynamic_date_slot_invalid_date_reports_the_flag_key() -> None:
    p = "Search_DynamicField_DueTimeSlot"
    m = _map(
        {
            p: "1",
            f"{p}StartYear": "2026",
            f"{p}StartMonth": "2",
            f"{p}StartDay": "31",
            f"{p}StopYear": "2026",
            f"{p}StopMonth": "3",
            f"{p}StopDay": "1",
        }
    )
    assert where(m) is None
    assert _reported(m) == [(p, INVALID_DATE_REASON)]


def test_unsupported_dynamic_field_type_is_one_unmapped_key_not_a_failure() -> None:
    m = _map(
        {
            "QueueIDs": ["13"],
            "Search_DynamicField_Ref": "5",
            "Search_DynamicField_RefTimePoint": "1",
            "Search_DynamicField_Area": ["Nord"],
        }
    )
    assert where(m) == {
        "all": [
            {"field": "queue_id", "op": "in", "value": [13]},
            {"field": "df.Area", "op": "in", "value": ["Nord"]},
        ]
    }
    assert _reported(m) == [("Search_DynamicField_Ref", DF_TYPE_REASON)]
    # an empty value for such a field is not reported
    assert _map({"Search_DynamicField_Ref": ""}).unmapped == ()


def test_dynamic_field_name_is_not_a_prefix_match_for_plain_types() -> None:
    m = _map({"Search_DynamicField_AreaCode": "x"})
    assert _reported(m) == [("Search_DynamicField_AreaCode", UNKNOWN_DF_REASON)]


def test_every_unmappable_key_is_reported() -> None:
    m = _map(
        {
            "Fulltext": "drucker",
            "Subject": "x",
            "From": "a@example.org",
            "CreatedQueueIDs": ["1"],
            "TicketNumber": "123 || 456",
            "Title": "a && b",
            "StateTypeIDs": ["99"],
            "Search_DynamicField_Gone": "x",
            "CloseTimeSearchType": "TimePoint",
            "TicketCloseTimePoint": "1",
            "TicketCloseTimePointStart": "Last",
            "TicketCloseTimePointFormat": "day",
            "Frobnicate": "1",
            "ShownAttributes": ["LabelTitle"],
            "ResultForm": "Normal",
        }
    )
    reasons = {u.key: u.reason for u in m.unmapped}
    assert reasons == {
        "Fulltext": FULLTEXT_REASON,
        "Subject": FULLTEXT_REASON,
        "From": FULLTEXT_REASON,
        "CreatedQueueIDs": UNSUPPORTED_REASON,
        "TicketNumber": OPERATOR_REASON,
        "Title": OPERATOR_REASON,
        "StateTypeIDs": "Statustyp unbekannt.",
        "Search_DynamicField_Gone": UNKNOWN_DF_REASON,
        "CloseTimeSearchType": NO_TIME_FIELD_REASON,
        "Frobnicate": UNSUPPORTED_REASON,
    }
    assert len(m.unmapped) == len(reasons)  # each key exactly once
    assert where(m) is None  # UI-only keys ignored, nothing invented


def test_article_and_mime_keys_are_fulltext() -> None:
    m = _map({"ArticleSenderType": "1", "MIMEBase_Foo": "x", "Body": "y"})
    assert {u.reason for u in m.unmapped} == {FULLTEXT_REASON}
    assert len(m.unmapped) == 3


def test_unmapped_value_is_shortened_to_200_chars() -> None:
    m = _map({"Frobnicate": "x" * 500})
    assert len(m.unmapped[0].value) == 200 and m.unmapped[0].value.endswith("…")
    m = _map({"Frobnicate": ["a", "b"]})
    assert m.unmapped[0].value == "a, b"


def test_empty_values_are_skipped_silently() -> None:
    m = _map({"Title": "", "QueueIDs": [], "TicketNumber": "*", "Fulltext": "", "Frobnicate": " "})
    assert where(m) is None and m.unmapped == ()


def test_result_passes_wave0_validation() -> None:
    m = _map(
        {
            "QueueIDs": ["13"],
            "TimeSearchType": "TimePoint",
            "TicketCreateTimePoint": "3",
            "TicketCreateTimePointStart": "Last",
            "TicketCreateTimePointFormat": "week",
        }
    )
    validate_filter(m.criteria, TICKET_FILTER_FIELDS.get)


def test_dynamic_field_conditions_pass_validation_with_df_specs() -> None:
    specs = {
        f"df.{name}": _df_spec(_DynamicFieldInfo(i, name, name, ftype))
        for i, (name, ftype) in enumerate(
            {"Area": "Dropdown", "Note": "Text", "Vip": "Checkbox", "Due": "Date"}.items()
        )
    }
    resolve = lambda key: TICKET_FILTER_FIELDS.get(key) or specs.get(key)  # noqa: E731
    m = _map(
        {
            "Search_DynamicField_Area": ["Nord"],
            "Search_DynamicField_Note": "a*",
            "Search_DynamicField_Vip": ["1"],
            "Search_DynamicField_DueTimePoint": "1",
            "Search_DynamicField_DueTimePointFormat": "month",
            "Search_DynamicField_DueTimePointStart": "Last",
            "Search_DynamicField_DueTimePointValue": "2",
            "TimeSearchType": "TimeSlot",
            "TicketCreateTimeStartYear": "2026",
            "TicketCreateTimeStartMonth": "7",
            "TicketCreateTimeStartDay": "1",
            "TicketCreateTimeStopYear": "2026",
            "TicketCreateTimeStopMonth": "7",
            "TicketCreateTimeStopDay": "31",
        }
    )
    assert m.unmapped == ()
    validate_filter(m.criteria, resolve)


def test_invalid_mapped_value_is_caught_by_validation_not_the_mapper() -> None:
    m = _map(
        {
            "TimeSearchType": "TimePoint",
            "TicketCreateTimePoint": "99999",
            "TicketCreateTimePointStart": "Last",
            "TicketCreateTimePointFormat": "year",
        }
    )
    assert m.unmapped == ()
    with pytest.raises(FilterError):
        validate_filter(m.criteria, TICKET_FILTER_FIELDS.get)


def _slot(prefix: str, **parts: str) -> dict[str, str]:
    return {f"{prefix}{k}": v for k, v in parts.items()}


def test_archive_reason_wording() -> None:
    assert ARCHIVE_REASON == "Archivierte Tickets sehen nur Admins."


def test_one_sided_time_slots_map_to_after_and_before() -> None:
    start = _slot("TicketCreateTime", StartYear="2026", StartMonth="7", StartDay="1")
    m = _map({"TimeSearchType": "TimeSlot", **start})
    assert where(m) == {"field": "created", "op": "after", "value": "2026-06-30T21:59:59"}
    assert m.unmapped == ()
    stop = _slot("TicketCreateTime", StopYear="2026", StopMonth="7", StopDay="31")
    m = _map({"TimeSearchType": "TimeSlot", **stop})
    assert where(m) == {"field": "created", "op": "before", "value": "2026-07-31T22:00:00"}
    assert m.unmapped == ()
    validate_filter(m.criteria, TICKET_FILTER_FIELDS.get)


def test_slot_with_partial_date_is_invalid_and_shows_the_values() -> None:
    m = _map(
        {
            "TimeSearchType": "TimeSlot",
            **_slot("TicketCreateTime", StartYear="2026", StartMonth="2", StartDay="30"),
            **_slot("TicketCreateTime", StopYear="2026", StopMonth="3", StopDay="1"),
        }
    )
    assert where(m) is None
    assert [(u.key, u.value, u.reason) for u in m.unmapped] == [
        ("TimeSearchType", "Beginn 2026-2-30; Ende 2026-3-1", INVALID_DATE_REASON)
    ]
    m = _map(
        {"TimeSearchType": "TimeSlot", **_slot("TicketCreateTime", StartYear="2026", StartDay="3")}
    )
    assert m.unmapped[0].reason == INVALID_DATE_REASON
    assert m.unmapped[0].value == "Beginn 2026-?-3"


def test_slot_start_after_stop_has_its_own_reason() -> None:
    m = _map(
        {
            "TimeSearchType": "TimeSlot",
            **_slot("TicketCreateTime", StartYear="2026", StartMonth="8", StartDay="1"),
            **_slot("TicketCreateTime", StopYear="2026", StopMonth="7", StopDay="1"),
        }
    )
    assert where(m) is None
    assert [(u.key, u.reason) for u in m.unmapped] == [("TimeSearchType", SLOT_ORDER_REASON)]
    assert SLOT_ORDER_REASON != INVALID_DATE_REASON
    assert "2026-8-1" in m.unmapped[0].value and "2026-7-1" in m.unmapped[0].value


def test_dynamic_slot_one_sided_and_inverted() -> None:
    p = "Search_DynamicField_DueTimeSlot"
    m = _map({p: "1", **_slot(p, StartYear="2026", StartMonth="7", StartDay="1")})
    assert where(m) == {"field": "df.Due", "op": "after", "value": "2026-06-30T21:59:59"}
    m = _map(
        {
            p: "1",
            **_slot(p, StartYear="2026", StartMonth="7", StartDay="2"),
            **_slot(p, StopYear="2026", StopMonth="7", StopDay="1"),
        }
    )
    assert _reported(m) == [(p, SLOT_ORDER_REASON)]


def test_dynamic_slot_stop_blank_parts_are_zero_once_a_time_is_given() -> None:
    p = "Search_DynamicField_DueTimeSlot"
    base = {p: "1", **_slot(p, StopYear="2026", StopMonth="7", StopDay="1")}
    # whole day when no time of day is given
    m = _map(base)
    assert where(m)["value"] == "2026-07-01T22:00:00"
    # an explicit hour: blank minute/second are 00, not 59 (12:00:00 local = 10:00:00 UTC,
    # exclusive bound one second later)
    m = _map({**base, f"{p}StopHour": "12"})
    assert where(m)["value"] == "2026-07-01T10:00:01"
    m = _map({**base, f"{p}StopHour": "12", f"{p}StopMinute": "30"})
    assert where(m)["value"] == "2026-07-01T10:30:01"


def test_invalid_time_zone_falls_back_to_utc() -> None:
    lookups = ZnunyLookups({}, {}, {}, {}, time_zone="Mars/Olympus")
    m = map_search_profile(
        "P",
        {
            "TimeSearchType": "TimeSlot",
            **_slot("TicketCreateTime", StartYear="2026", StartMonth="7", StartDay="1"),
        },
        lookups,
    )
    assert where(m) == {"field": "created", "op": "after", "value": "2026-06-30T23:59:59"}


def _holds(cond: dict[str, Any], value: datetime) -> bool:
    """Engine semantics (filters/predicates.py datetime_predicate): after = strict >,
    before = strict <, between = [from, to)."""
    parse = datetime.fromisoformat
    if cond["op"] == "after":
        return value > parse(cond["value"])
    if cond["op"] == "before":
        return value < parse(cond["value"])
    assert cond["op"] == "between"
    return parse(cond["value"]["from"]) <= value < parse(cond["value"]["to"])


def test_slot_bounds_are_inclusive_like_znuny() -> None:
    # local 2026-07-01 .. 2026-07-31 (CEST): UTC 06-30 22:00:00 .. 07-31 21:59:59 inclusive
    first, last = datetime(2026, 6, 30, 22, 0, 0), datetime(2026, 7, 31, 21, 59, 59)
    one = timedelta(seconds=1)
    start = _slot("TicketCreateTime", StartYear="2026", StartMonth="7", StartDay="1")
    stop = _slot("TicketCreateTime", StopYear="2026", StopMonth="7", StopDay="31")
    both = where(_map({"TimeSearchType": "TimeSlot", **start, **stop}))
    only_start = where(_map({"TimeSearchType": "TimeSlot", **start}))
    only_stop = where(_map({"TimeSearchType": "TimeSlot", **stop}))
    assert both["value"]["to"] == "2026-07-31T22:00:00"
    assert both["value"]["from"] == "2026-06-30T22:00:00"
    assert only_start["value"] == "2026-06-30T21:59:59"
    assert only_stop["value"] == "2026-07-31T22:00:00"
    for cond in (both, only_start):
        assert _holds(cond, first) and not _holds(cond, first - one)
    for cond in (both, only_stop):
        assert _holds(cond, last) and not _holds(cond, last + one)


def test_dynamic_slot_bounds_are_inclusive_like_znuny() -> None:
    p = "Search_DynamicField_DueTimeSlot"
    full = {
        p: "1",
        **_slot(p, StartYear="2026", StartMonth="7", StartDay="1", StartHour="8"),
        **_slot(p, StopYear="2026", StopMonth="7", StopDay="1", StopHour="9", StopMinute="15"),
    }
    cond = where(_map(full))  # local 08:00:00 .. 09:15:00 = UTC 06:00:00 .. 07:15:00
    assert _holds(cond, datetime(2026, 7, 1, 6, 0, 0))
    assert not _holds(cond, datetime(2026, 7, 1, 5, 59, 59))
    assert _holds(cond, datetime(2026, 7, 1, 7, 15, 0))
    assert not _holds(cond, datetime(2026, 7, 1, 7, 15, 1))
