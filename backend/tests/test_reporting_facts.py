from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from tiqora.reporting.facts import (
    CHANNEL_KEYS,
    FACT_VERSION,
    AiFlags,
    FactInputs,
    compute_fact,
)
from tiqora.reporting.history_events import ArticleRow, HistoryRow, TicketRow, derive_events
from tiqora.reporting.working_time import EscalationTargets, WorkingCalendar

T0 = datetime(2024, 6, 3, 7, 0)  # Monday 09:00 Europe/Berlin
NOW = datetime(2024, 6, 20, 12, 0)
STATE_TYPES = {1: "new", 4: "open", 2: "closed", 6: "pending reminder", 9: "merged"}
WH_CAL = WorkingCalendar(
    name=None,
    working_hours={d: list(range(8, 17)) for d in ("Mon", "Tue", "Wed", "Thu", "Fri")}
    | {"Sat": [], "Sun": []},
    vacation_days={},
    vacation_days_once={},
    time_zone="Europe/Berlin",
)
NO_HOURS = WorkingCalendar(
    name=None, working_hours={}, vacation_days={}, vacation_days_once={}, time_zone="Europe/Berlin"
)
DEFAULT_TARGETS = EscalationTargets(60, 120, 480, None)


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
    at: datetime | None = None,
) -> HistoryRow:
    return HistoryRow(
        id=id,
        history_type=type,
        name="",
        article_id=None,
        queue_id=queue,
        state_id=state,
        priority_id=3,
        owner_id=owner,
        type_id=1,
        create_time=at if at is not None else T0 + timedelta(minutes=minutes),
        create_by=2,
    )


def art(
    id: int,
    sender: str,
    visible: bool,
    minutes: int,
    *,
    channel: str = "Email",
    by: int = 2,
) -> ArticleRow:
    return ArticleRow(
        id=id,
        sender_type=sender,
        visible=visible,
        channel=channel,
        create_time=T0 + timedelta(minutes=minutes),
        create_by=by,
    )


def inputs(
    history: list[HistoryRow],
    articles: list[ArticleRow],
    *,
    targets: EscalationTargets = DEFAULT_TARGETS,
    calendar: WorkingCalendar = WH_CAL,
    ticket: TicketRow | None = None,
    accounted: Decimal = Decimal("0"),
    ai: AiFlags | None = None,
    process: str | None = None,
) -> FactInputs:
    tk = ticket if ticket is not None else t()
    return FactInputs(
        ticket=tk,
        history=history,
        articles=articles,
        events=derive_events(tk, history, articles, STATE_TYPES),
        state_types=STATE_TYPES,
        accounted_time=accounted,
        targets=targets,
        calendar=calendar,
        storage_tz="UTC",
        ai=ai if ai is not None else AiFlags(),
        process_entity_id=process,
        now=NOW,
    )


def test_first_response_and_breach_in_working_minutes() -> None:
    arts = [art(1, "customer", True, 0), art(2, "agent", False, 30), art(3, "agent", True, 90)]
    f = compute_fact(inputs([h(1, "NewTicket", 0)], arts))
    assert f.first_response_at == T0 + timedelta(minutes=90)
    assert (f.first_response_minutes, f.first_response_working_minutes) == (90, 90)
    assert f.first_response_target_minutes == 60 and f.first_response_breached is True
    assert f.first_response_due_at == T0 + timedelta(minutes=60)
    assert f.fact_version == FACT_VERSION and f.built_at == NOW


def test_first_response_within_target_not_breached() -> None:
    arts = [art(1, "customer", True, 0), art(2, "agent", True, 45)]
    f = compute_fact(inputs([h(1, "NewTicket", 0)], arts))
    assert f.first_response_breached is False


def test_open_unanswered_ticket_leaves_breach_undecided() -> None:
    f = compute_fact(inputs([h(1, "NewTicket", 0)], [art(1, "customer", True, 0)]))
    assert f.first_response_breached is None and f.first_response_due_at is not None
    assert f.first_response_at is None and f.first_response_minutes is None


def test_closed_without_response_after_due_is_breached() -> None:
    hist = [h(1, "NewTicket", 0), h(2, "StateUpdate", 120, state=2)]
    f = compute_fact(inputs(hist, []))
    assert f.first_response_breached is True and f.solution_breached is False


def test_closed_without_response_before_due_is_not_breached() -> None:
    hist = [h(1, "NewTicket", 0), h(2, "StateUpdate", 30, state=2)]
    assert compute_fact(inputs(hist, [])).first_response_breached is False


def test_reopen_twice_counts_and_last_close() -> None:
    hist = [
        h(1, "NewTicket", 0),
        h(2, "StateUpdate", 10, state=2),
        h(3, "StateUpdate", 20, state=4),
        h(4, "StateUpdate", 30, state=2),
        h(5, "StateUpdate", 40, state=4),
        h(6, "StateUpdate", 50, state=2),
    ]
    f = compute_fact(inputs(hist, []))
    assert (f.close_count, f.reopen_count) == (3, 2)
    assert f.first_closed_at == T0 + timedelta(minutes=10)
    assert f.closed_at == T0 + timedelta(minutes=50)
    assert f.solution_minutes == 50


def test_unclosed_ticket_has_no_solution_figures() -> None:
    f = compute_fact(inputs([h(1, "NewTicket", 0)], []))
    assert f.close_count == 0 and f.closed_at is None and f.first_closed_at is None
    assert f.solution_minutes is None and f.solution_working_minutes is None
    assert f.solution_breached is None and f.solution_due_at == T0 + timedelta(minutes=480)


def test_solution_working_minutes_skip_night() -> None:
    # created Mon 16:00 Berlin (14:00 UTC), closed Tue 09:00 Berlin (07:00 UTC) -> 2 working hours
    created = datetime(2024, 6, 3, 14, 0)
    closed = datetime(2024, 6, 4, 7, 0)
    hist = [h(1, "NewTicket", 0, at=created), h(2, "StateUpdate", 0, state=2, at=closed)]
    f = compute_fact(inputs(hist, [], ticket=t(create_time=created)))
    assert f.solution_minutes == 17 * 60
    assert f.solution_working_minutes == 120
    assert f.solution_breached is False  # 120 working minutes <= 480


def test_no_working_hours_gives_empty_working_minutes() -> None:
    f = compute_fact(inputs([h(1, "NewTicket", 0)], [art(3, "agent", True, 90)], calendar=NO_HOURS))
    assert f.first_response_working_minutes is None and f.first_response_minutes == 90
    assert f.first_response_breached is True  # wall clock 90 > 60
    assert f.first_response_due_at == T0 + timedelta(minutes=60)


def test_zero_targets_mean_no_sla() -> None:
    f = compute_fact(
        inputs(
            [h(1, "NewTicket", 0), h(2, "StateUpdate", 600, state=2)],
            [art(1, "agent", True, 500)],
            targets=EscalationTargets(0, 0, 0, None),
        )
    )
    assert (
        f.first_response_target_minutes,
        f.first_response_due_at,
        f.first_response_breached,
    ) == (None, None, None)
    assert (f.solution_target_minutes, f.solution_due_at, f.solution_breached) == (None, None, None)
    assert f.update_target_minutes is None and f.update_breach_count == 0
    assert f.first_response_minutes == 500  # the measured figures stay


def test_update_breach_count() -> None:
    arts = [
        art(1, "customer", True, 0),
        art(2, "agent", True, 30),  # waited 30 <= 120
        art(3, "customer", True, 60),
        art(4, "customer", True, 70),  # same wait, does not restart it
        art(5, "agent", True, 300),  # waited 240 > 120 -> 1
        art(6, "customer", True, 400),  # still waiting, not counted
    ]
    f = compute_fact(inputs([h(1, "NewTicket", 0)], arts))
    assert f.update_breach_count == 1 and f.update_target_minutes == 120


def test_internal_note_does_not_end_the_wait() -> None:
    arts = [art(1, "customer", True, 0), art(2, "agent", False, 10), art(3, "agent", True, 200)]
    assert compute_fact(inputs([h(1, "NewTicket", 0)], arts)).update_breach_count == 1


def test_queue_pingpong() -> None:
    hist = [h(1, "NewTicket", 0, queue=1), h(2, "Move", 5, queue=2), h(3, "Move", 10, queue=1)]
    f = compute_fact(inputs(hist, [], ticket=t(queue_id=1)))
    assert f.queue_move_count == 2 and f.queue_pingpong is True and f.queue_id_at_create == 1
    g = compute_fact(
        inputs([h(1, "NewTicket", 0, queue=1), h(2, "Move", 5, queue=2)], [], ticket=t(queue_id=2))
    )
    assert g.queue_pingpong is False and g.queue_move_count == 1
    assert (g.queue_id, g.queue_id_at_create) == (2, 1)


def test_owner_changes_counted() -> None:
    hist = [
        h(1, "NewTicket", 0, owner=1),
        h(2, "OwnerUpdate", 5, owner=7),
        h(3, "Move", 6, owner=7),
    ]
    assert compute_fact(inputs(hist, [])).owner_change_count == 1


def test_channel_counts_touches_accounted() -> None:
    arts = [
        art(1, "customer", True, 0, channel="Telegram"),
        art(2, "agent", True, 5, by=5),
        art(3, "agent", True, 6, by=6),
        art(4, "agent", False, 7, by=5),
    ]
    f = compute_fact(inputs([h(1, "NewTicket", 0)], arts, accounted=Decimal("1.50")))
    assert f.channel == "telegram"
    assert (f.article_in_count, f.article_out_count, f.note_count) == (1, 2, 1)
    assert f.agent_touch_count == 2 and f.accounted_time == Decimal("1.50")
    assert f.last_event_at == T0 + timedelta(minutes=7) and f.last_history_id == 1


def test_merged_flag_and_unknown_channel() -> None:
    f = compute_fact(
        inputs(
            [h(1, "NewTicket", 0)],
            [art(1, "customer", True, 0, channel="Fax")],
            ticket=t(state_id=9, archive_flag=1),
        )
    )
    assert f.merged is True and f.state_type == "merged" and f.archived is True
    assert f.channel == "fax"
    plain = compute_fact(inputs([h(1, "NewTicket", 0)], []))
    assert plain.channel == "email" and plain.merged is False and plain.archived is False
    assert plain.last_history_id == 1


def test_channel_keys_map() -> None:
    assert CHANNEL_KEYS["Chat"] == "webchat" and CHANNEL_KEYS["SMS"] == "sms"
    long = compute_fact(
        inputs([h(1, "NewTicket", 0)], [art(1, "customer", True, 0, channel="X" * 40)])
    )
    assert long.channel == "x" * 30


def test_ticket_attributes_and_ai_flags_pass_through() -> None:
    tk = t(
        id=77,
        sla_id=4,
        service_id=5,
        responsible_user_id=9,
        customer_id="acme",
        customer_user_id="bob@acme.test",
        type_id=None,
    )
    ai = AiFlags(True, False, True, None, False)
    f = compute_fact(inputs([], [], ticket=tk, ai=ai, process="Process-1"))
    assert (f.ticket_id, f.sla_id, f.service_id, f.responsible_id) == (77, 4, 5, 9)
    assert (f.customer_id, f.customer_user, f.type_id) == ("acme", "bob@acme.test", None)
    assert (f.ai_triaged, f.ai_triage_queue_applied, f.ai_auto_replied) == (True, False, True)
    assert (f.ai_draft_used, f.ai_handed_off) == (None, False)
    assert f.process_entity_id == "Process-1"
    assert f.last_history_id == 0 and f.last_event_at == T0 and f.created_by == 2
    assert f.calendar_name is None and f.owner_id == 1


def test_as_insert_dict_has_every_column() -> None:
    from tiqora.db.tiqora.models import TiqoraReportTicketFact

    cols = {c.name for c in TiqoraReportTicketFact.__table__.columns}
    assert set(compute_fact(inputs([h(1, "NewTicket", 0)], [])).as_insert_dict()) == cols
