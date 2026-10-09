from __future__ import annotations

from datetime import datetime, timedelta

from tiqora.domain.history_render import split_history_name
from tiqora.reporting.history_events import (
    EVENT_KINDS,
    ArticleRow,
    HistoryRow,
    TicketRow,
    derive_events,
)

T0 = datetime(2024, 6, 3, 8, 0)
PA_NAME = "%%FieldName%%ProcessManagementActivityID%%Value%%A2%%OldValue%%A1"
STATE_TYPES = {1: "new", 4: "open", 2: "closed", 6: "pending reminder", 9: "merged"}


def t(**overrides: object) -> TicketRow:
    base: dict[str, object] = {
        "id": 1,
        "tn": "2024060300001",
        "create_time": T0,
        "create_by": 2,
        "queue_id": 1,
        "state_id": 1,
        "priority_id": 3,
        "type_id": 1,
        "service_id": None,
        "sla_id": None,
        "user_id": 1,
        "responsible_user_id": None,
        "customer_id": None,
        "customer_user_id": None,
        "archive_flag": 0,
    }
    base.update(overrides)
    return TicketRow(**base)  # type: ignore[arg-type]


def h(
    id: int,
    type: str,
    minutes: int,
    *,
    queue: int = 1,
    state: int = 1,
    owner: int = 1,
    prio: int = 3,
    type_id: int = 1,
    name: str = "%%",
    by: int = 2,
) -> HistoryRow:
    return HistoryRow(
        id,
        type,
        name,
        None,
        queue,
        state,
        prio,
        owner,
        type_id,
        T0 + timedelta(minutes=minutes),
        by,
    )


def kinds(events: list) -> list[str]:  # type: ignore[type-arg]
    return [e.kind for e in events]


def test_split_history_name_public_and_alias() -> None:
    from tiqora.domain import history_render

    assert split_history_name("%%a%%b%%") == ["a", "b", ""]
    assert history_render._split is split_history_name


def test_no_history_rows_builds_from_ticket_row() -> None:
    ev = derive_events(t(state_id=4, queue_id=7), [], [], STATE_TYPES)
    assert kinds(ev) == ["create"] and ev[0].to_id == 7 and ev[0].to_value == "open"


def test_created_closed_emits_close() -> None:
    ev = derive_events(t(), [h(1, "NewTicket", 0, state=2)], [], STATE_TYPES)
    assert kinds(ev) == ["create", "close"]


def test_reopen_twice_counts_and_last_close() -> None:
    hist = [
        h(1, "NewTicket", 0, state=1),
        h(2, "StateUpdate", 10, state=2),
        h(3, "StateUpdate", 20, state=4),
        h(4, "StateUpdate", 30, state=2),
        h(5, "StateUpdate", 40, state=4),
        h(6, "StateUpdate", 50, state=2),
    ]
    ev = derive_events(t(), hist, [], STATE_TYPES)
    assert kinds(ev).count("close") == 3 and kinds(ev).count("reopen") == 2
    state_ev = [e for e in ev if e.kind == "state"]
    assert state_ev[0].from_value == "new" and state_ev[0].to_value == "closed"
    assert state_ev[0].from_duration_seconds == 600 and state_ev[1].from_duration_seconds == 600


def test_queue_pingpong_events_and_durations() -> None:
    hist = [h(1, "NewTicket", 0, queue=1), h(2, "Move", 5, queue=2), h(3, "Move", 65, queue=1)]
    q = [e for e in derive_events(t(), hist, [], STATE_TYPES) if e.kind == "queue"]
    assert [(e.from_id, e.to_id, e.from_duration_seconds, e.queue_id) for e in q] == [
        (1, 2, 300, 2),
        (2, 1, 3600, 1),
    ]


def test_owner_priority_type_by_column_diff_regardless_of_history_type() -> None:
    hist = [h(1, "NewTicket", 0), h(2, "AddNote", 1, owner=5, prio=4, type_id=2)]
    assert sorted(kinds(derive_events(t(), hist, [], STATE_TYPES))) == [
        "create",
        "owner",
        "priority",
        "type",
    ]


def test_service_sla_responsible_from_names() -> None:
    hist = [
        h(1, "NewTicket", 0),
        h(2, "ServiceUpdate", 1, name="%%IT%%12%%%%"),
        h(3, "SLAUpdate", 2, name="%%Gold%%3%%Silver%%2"),
        h(4, "ResponsibleUpdate", 3, name="%%anna%%8"),
        h(5, "ResponsibleUpdate", 4, name="%%bert%%9"),
        h(6, "ServiceUpdate", 5, name="%%broken"),
    ]
    ev = {(e.kind, e.from_id, e.to_id) for e in derive_events(t(), hist, [], STATE_TYPES)}
    assert ("service", None, 12) in ev and ("sla", 2, 3) in ev
    assert ("responsible", None, 8) in ev and ("responsible", 8, 9) in ev
    assert len([x for x in ev if x[0] == "service"]) == 1  # unparseable name skipped


def test_merge_and_process_activity() -> None:
    hist = [
        h(1, "NewTicket", 0),
        h(2, "TicketDynamicFieldUpdate", 1, name=PA_NAME),
        h(3, "TicketDynamicFieldUpdate", 2, name="%%FieldName%%Kunde%%Value%%x%%OldValue%%"),
        h(4, "Merged", 3, state=9, name="%%T100%%100%%T200%%200"),
    ]
    ev = derive_events(t(), hist, [], STATE_TYPES)
    pa = [e for e in ev if e.kind == "process_activity"]
    assert [(e.from_value, e.to_value) for e in pa] == [("A1", "A2")]
    assert "merge" in kinds(ev) and "close" not in kinds(ev)  # merged is not closed
    merge = next(e for e in ev if e.kind == "merge")
    assert merge.to_value == "T100%%100%%T200%%200"


def test_article_events_and_queue_at_time() -> None:
    hist = [h(1, "NewTicket", 0, queue=1), h(2, "Move", 30, queue=2)]
    arts = [
        ArticleRow(10, "customer", True, "Email", T0 + timedelta(minutes=1), 99),
        ArticleRow(11, "agent", True, "Email", T0 + timedelta(minutes=40), 5),
        ArticleRow(12, "agent", False, "Internal", T0 + timedelta(minutes=41), 5),
        ArticleRow(13, "system", False, "Internal", T0 + timedelta(minutes=42), 1),
    ]
    ev = [e for e in derive_events(t(), hist, arts, STATE_TYPES) if e.article_id]
    assert [(e.kind, e.queue_id, e.actor_user_id) for e in ev] == [
        ("customer_message", 1, 99),
        ("reply", 2, 5),
        ("note", 2, 5),
    ]


def test_output_is_time_ordered() -> None:
    hist = [
        h(1, "NewTicket", 0),
        h(2, "Move", 20, queue=2),
        h(3, "StateUpdate", 40, queue=2, state=2),
    ]
    arts = [
        ArticleRow(30, "agent", True, "Email", T0 + timedelta(minutes=50), 5),
        ArticleRow(31, "customer", True, "Email", T0 + timedelta(minutes=10), 99),
        ArticleRow(32, "agent", False, "Internal", T0 + timedelta(minutes=30), 5),
    ]
    ev = derive_events(t(), hist, arts, STATE_TYPES)
    times = [e.occurred_at for e in ev]
    assert times == sorted(times)
    assert [e.kind for e in ev] == [
        "create",
        "customer_message",
        "queue",
        "note",
        "state",
        "close",
        "reply",
    ]
    assert all(e.kind in EVENT_KINDS for e in ev)


def test_first_row_not_newticket_still_parses_names() -> None:
    hist = [
        h(1, "ServiceUpdate", 0, name="%%IT%%12%%%%"),
        h(2, "Merged", 1, name="%%T100%%100%%T200%%200"),
        h(3, "Move", 2, queue=2),
    ]
    ev = derive_events(t(), hist, [], STATE_TYPES)
    assert kinds(ev) == ["create", "service", "merge", "queue"]
    svc = next(e for e in ev if e.kind == "service")
    assert (svc.from_id, svc.to_id, svc.history_id) == (None, 12, 1)
    # the first row's own columns are the baseline, not a diff
    assert [e.kind for e in ev if e.kind in ("queue", "state")] == ["queue"]


def test_same_create_time_ordered_by_history_id() -> None:
    hist = [
        h(3, "Move", 5, queue=3),
        h(1, "NewTicket", 0, queue=1),
        h(2, "Move", 5, queue=2),
    ]
    q = [e for e in derive_events(t(), hist, [], STATE_TYPES) if e.kind == "queue"]
    assert [(e.history_id, e.from_id, e.to_id) for e in q] == [(2, 1, 2), (3, 2, 3)]


def test_empty_old_process_activity_value_is_none() -> None:
    name = "%%FieldName%%ProcessManagementActivityID%%Value%%A1%%OldValue%%"
    ev = derive_events(
        t(), [h(1, "NewTicket", 0), h(2, "TicketDynamicFieldUpdate", 1, name=name)], [], STATE_TYPES
    )
    pa = [e for e in ev if e.kind == "process_activity"]
    assert [(e.from_value, e.to_value) for e in pa] == [(None, "A1")]
    empty_new = "%%FieldName%%ProcessManagementActivityID%%Value%%%%OldValue%%A1"
    ev = derive_events(
        t(),
        [h(1, "NewTicket", 0), h(2, "TicketDynamicFieldUpdate", 1, name=empty_new)],
        [],
        STATE_TYPES,
    )
    assert [(e.from_value, e.to_value) for e in ev if e.kind == "process_activity"] == [
        ("A1", None)
    ]


def test_garbled_names_emit_no_event() -> None:
    hist = [
        h(1, "NewTicket", 0),
        h(2, "ServiceUpdate", 1, name="%%a%%b"),
        h(3, "SLAUpdate", 2, name="%%x%%y%%z%%w"),
        h(4, "ResponsibleUpdate", 3, name="%%anna%%notanumber"),
        h(5, "Merged", 4, name="%%"),
        h(6, "TicketDynamicFieldUpdate", 5, name="%%FieldName%%ProcessManagementActivityID"),
        h(7, "ServiceUpdate", 6, name=""),
    ]
    assert kinds(derive_events(t(), hist, [], STATE_TYPES)) == ["create"]


def test_service_removal_keeps_old_id() -> None:
    hist = [h(1, "NewTicket", 0), h(2, "ServiceUpdate", 1, name="%%%%%%IT%%12")]
    svc = [e for e in derive_events(t(), hist, [], STATE_TYPES) if e.kind == "service"]
    assert [(e.from_id, e.to_id) for e in svc] == [(12, None)]


def test_none_name_is_treated_as_empty() -> None:
    bad = HistoryRow(2, "Merged", None, None, 1, 1, 3, 1, 1, T0 + timedelta(minutes=1), 2)  # type: ignore[arg-type]
    assert kinds(derive_events(t(), [h(1, "NewTicket", 0), bad], [], STATE_TYPES)) == ["create"]


def test_unknown_state_id_keeps_state_event_without_close_or_reopen() -> None:
    hist = [
        h(1, "NewTicket", 0, state=1),
        h(2, "StateUpdate", 1, state=99),
        h(3, "StateUpdate", 2, state=2),
        h(4, "StateUpdate", 3, state=99),
    ]
    ev = derive_events(t(), hist, [], STATE_TYPES)
    st = [(e.from_value, e.to_value) for e in ev if e.kind == "state"]
    assert st == [("new", None), (None, "closed"), ("closed", None)]
    assert "close" not in kinds(ev) and "reopen" not in kinds(ev)


def test_merged_to_closed_counts_as_close() -> None:
    hist = [h(1, "NewTicket", 0, state=9), h(2, "StateUpdate", 1, state=2)]
    ev = derive_events(t(), hist, [], STATE_TYPES)
    assert kinds(ev) == ["create", "state", "close"]
    assert "close" not in kinds(
        derive_events(t(), [h(1, "NewTicket", 0, state=9)], [], STATE_TYPES)
    )


def _moved(
    id: int, type: str, minutes: int, *, merged_at: int, article: int, **snap: int
) -> HistoryRow:
    """A row TicketMerge moved over: article-linked, change_time stamped at the merge."""
    from dataclasses import replace

    row = h(id, type, minutes, **snap)  # type: ignore[arg-type]
    return replace(row, article_id=article, change_time=T0 + timedelta(minutes=merged_at))


def test_merge_moved_rows_drive_no_attribute_events() -> None:
    """C1: rows of a merged ticket carry ITS snapshot; they must not fake moves/closes."""
    hist = [
        h(1, "NewTicket", 0, queue=10, state=1, owner=5),
        # the merged ticket's note (queue 20, closed, owner 7), moved at minute 30
        _moved(2, "AddNote", 10, merged_at=30, article=900, queue=20, state=2, owner=7),
        # the merged ticket's close-with-note: a StateUpdate WITH an article id
        _moved(3, "StateUpdate", 12, merged_at=30, article=901, queue=20, state=2, owner=7),
        h(4, "Merged", 30, queue=10, state=1, owner=5, name="%%2024060300002%%2%%T%%1"),
        h(5, "StateUpdate", 40, queue=10, state=4, owner=5),
    ]
    articles = [ArticleRow(900, "agent", False, "Internal", T0 + timedelta(minutes=10), 7)]
    ev = derive_events(t(queue_id=10, user_id=5), hist, articles, STATE_TYPES)
    assert kinds(ev) == ["create", "note", "merge", "state"]
    state = next(e for e in ev if e.kind == "state")
    assert (state.from_value, state.to_value, state.history_id) == ("new", "open", 5)
    # the moved article is attributed to this ticket's queue, not the merged one's
    note = next(e for e in ev if e.kind == "note")
    assert note.queue_id == 10


def test_merge_moved_row_as_oldest_row_is_no_baseline() -> None:
    """A merged ticket older than the main one: its rows never become the baseline."""
    hist = [
        _moved(1, "AddNote", -60, merged_at=30, article=900, queue=20, state=2),
        h(2, "NewTicket", 0, queue=10, state=1),
        h(3, "Move", 5, queue=11, state=1),
    ]
    ev = derive_events(t(queue_id=10), hist, [], STATE_TYPES)
    assert kinds(ev) == ["create", "queue"]
    create, queue = ev
    assert create.to_id == 10 and create.to_value == "new"
    assert (queue.from_id, queue.to_id) == (10, 11)


def test_state_update_with_article_still_closes() -> None:
    """Own rows with an article id (TicketStateSet passes ArticleID) keep driving the diff."""
    from dataclasses import replace

    close = replace(
        h(2, "StateUpdate", 10, state=2), article_id=55, change_time=T0 + timedelta(minutes=10)
    )
    ev = derive_events(t(), [h(1, "NewTicket", 0), close], [], STATE_TYPES)
    assert kinds(ev) == ["create", "state", "close"]
