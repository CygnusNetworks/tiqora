"""The five list-like core providers (ticket list, escalations, reminders, calendar agenda,
team presence) against the shared dashboard world, on both dialects (band 49710-49749, 49800+).

Time-sensitive rows are seeded relative to the real clock, never a fixed date.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from tests._dashboard_helpers import (
    GROUP_1,
    GROUP_2,
    TICKETS,
    USER_A,
    USER_B,
    USER_C,
    Env,
    FakeRedis,
    open_env,
)
from tiqora.api.v1.agents import list_online_agents_for, online_key
from tiqora.dashboard.providers import DASHBOARD_PROVIDERS, core_providers
from tiqora.dashboard.providers.calendar_agenda import agenda_window
from tiqora.dashboard.runner import run_widget
from tiqora.domain.timezones import USER_TIME_ZONE_KEY, resolve_user_time_zone
from tiqora.extensions.registry import Registry
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    async with open_env(request.getfixturevalue(request.param)) as e:
        yield e


@dataclass
class W:
    id: str
    type_key: str
    settings: dict[str, Any] = field(default_factory=dict)
    type_version: int = 1


def registry() -> Registry:
    reg = Registry()
    for p in core_providers():
        reg.add(DASHBOARD_PROVIDERS, p)
    reg.freeze()
    return reg


def tickets(preset: str, **extra: Any) -> W:
    return W(
        "t",
        "core.ticket-list",
        settings={"source": {"kind": "preset", "preset": preset}, **extra},
    )


async def data_of(env: Env, uid: int, widget: W, **kw: Any) -> dict[str, Any]:
    out = await run_widget(env.factory, registry(), uid, widget, **kw)
    assert out is not None and out.status == "ok", out
    assert out.data is not None
    return out.data


def ids_of(data: dict[str, Any]) -> list[int]:
    return [i["id"] for i in data["items"]]


def ticket_ids(data: dict[str, Any]) -> list[int]:
    return [x["ticket"]["id"] for x in data["items"]]


def test_core_providers_cover_the_six_data_widgets() -> None:
    assert sorted(p.key for p in core_providers()) == [
        "core.calendar-agenda",
        "core.counter",
        "core.escalations",
        "core.reminders",
        "core.team-presence",
        "core.ticket-list",
    ]


# --- ticket list -------------------------------------------------------------------------


async def test_ticket_list_preset_matches_todays_dashboard_queries(env: Env) -> None:
    from tiqora.domain.ticket_service import TicketService

    data = await data_of(
        env,
        USER_A,
        tickets("mine_open", sort="age", order="desc", limit=8),
    )
    async with env.session() as s:
        expected = await TicketService(s).list_tickets(
            USER_A, owner_id=USER_A, state_type="open", sort="age", order="desc", limit=8
        )
    assert ids_of(data) == [i.id for i in expected.items] != []
    assert data["total"] == expected.total == 4  # "open" = every not-closed state


@pytest.mark.parametrize(
    ("preset", "key"),
    [("mine_new", "a_new"), ("unowned_new", "unowned_new"), ("escalated", "a_escalated")],
)
async def test_ticket_list_presets_return_the_seeded_ticket(
    env: Env, preset: str, key: str
) -> None:
    ids = ids_of(await data_of(env, USER_A, tickets(preset)))
    assert TICKETS[key] in ids


async def test_ticket_list_limit_is_applied_and_total_is_not(env: Env) -> None:
    data = await data_of(env, USER_A, tickets("mine_open", limit=1))
    assert len(data["items"]) == 1 and data["total"] == 4  # A owns four not-closed tickets


async def test_shared_dashboard_data_runs_in_viewer_scope(env: Env) -> None:
    """A's shared dashboard (visible to GROUP_2 members) lists B's tickets for B, never A's."""
    widget = {
        "id": "dddddddd",
        "type_key": "core.ticket-list",
        "settings": {"source": {"kind": "preset", "preset": "mine_open"}},
        "layout": {"lg": {"x": 0, "y": 0, "w": 6, "h": 5}},
    }
    from tiqora.dashboard.schemas import WidgetIn

    d = await env.make_personal(
        USER_A,
        name="dash497 liste",
        widgets=[WidgetIn.model_validate(widget)],
        share_group_ids=[GROUP_2],
    )
    r_b = await env.as_user(USER_B).get(f"/api/v1/dashboards/{d.id}/data")
    assert r_b.status_code == 200
    assert [i["id"] for i in r_b.json()["widgets"]["dddddddd"]["data"]["items"]] == [
        TICKETS["b_open"]
    ]
    r_a = await env.as_user(USER_A).get(f"/api/v1/dashboards/{d.id}/data")
    assert set(ids_of(r_a.json()["widgets"]["dddddddd"]["data"])) == {
        TICKETS["a_open"],
        TICKETS["a_new"],
        TICKETS["a_escalated"],
        TICKETS["a_pending"],
    }


# --- escalations -------------------------------------------------------------------------


async def test_escalations_hide_foreign_queue_tickets(env: Env) -> None:
    a = await data_of(env, USER_A, W("e", "core.escalations", settings={"within_hours": 0}))
    b = await data_of(env, USER_B, W("e", "core.escalations", settings={"within_hours": 0}))
    assert TICKETS["a_escalated"] in ticket_ids(a)
    assert b["items"] == [] and b["total"] == 0 and b["oldest_epoch"] is None


async def test_escalations_sorted_oldest_first_with_epoch_and_kind(env: Env) -> None:
    now = int(time.time())
    env.set_escalation(TICKETS["a_open"], solution=now - 7200)  # older breach than now - 3600
    data = await data_of(env, USER_A, W("e", "core.escalations", settings={"within_hours": 0}))
    epochs = [x["epoch"] for x in data["items"]]
    assert len(epochs) == 2 and epochs == sorted(epochs) and data["oldest_epoch"] == epochs[0]
    assert ticket_ids(data)[0] == TICKETS["a_open"] and epochs[0] == now - 7200
    assert {x["kind"] for x in data["items"]} <= {"solution", "escalation"}
    assert data["total"] == 2


async def test_escalations_within_hours_includes_upcoming(env: Env) -> None:
    env.set_escalation(TICKETS["a_open"], solution=int(time.time()) + 1800)  # in 30 minutes
    none = await data_of(env, USER_A, W("e", "core.escalations", settings={"within_hours": 0}))
    hour = await data_of(env, USER_A, W("e", "core.escalations", settings={"within_hours": 1}))
    assert TICKETS["a_open"] not in ticket_ids(none)
    assert TICKETS["a_open"] in ticket_ids(hour)
    upcoming = next(x for x in hour["items"] if x["ticket"]["id"] == TICKETS["a_open"])
    assert upcoming["epoch"] > time.time()


# --- reminders ---------------------------------------------------------------------------


async def test_reminders_window_and_overdue_flag(env: Env) -> None:
    data = await data_of(env, USER_A, W("r", "core.reminders", settings={"window_hours": 24}))
    item = next(i for i in data["items"] if i["ticket"]["id"] == TICKETS["a_pending"])
    assert item["overdue"] is False and item["until"] > time.time()
    assert data["total"] == 1
    short = await data_of(env, USER_A, W("r", "core.reminders", settings={"window_hours": 1}))
    assert short["items"] == [] and short["total"] == 0  # until = now + 2h


async def test_reminders_past_due_is_flagged_overdue(env: Env) -> None:
    env._sql_write(
        "UPDATE ticket SET until_time = :u WHERE id = :i",
        u=int(time.time()) - 600,
        i=TICKETS["a_pending"],
    )
    data = await data_of(env, USER_A, W("r", "core.reminders"))
    (item,) = data["items"]
    assert item["ticket"]["id"] == TICKETS["a_pending"] and item["overdue"] is True


# --- calendar agenda ---------------------------------------------------------------------


async def zone_of(env: Env, uid: int) -> str:
    async with env.session() as s:
        return await resolve_user_time_zone(s, SysConfig(s), uid)


async def test_calendar_agenda_uses_the_viewers_calendars_and_today_in_their_zone(
    env: Env,
) -> None:
    start, end = agenda_window(env.now, await zone_of(env, USER_A), 1)
    env.make_appointment(
        calendar_group=GROUP_1, title="dash497 heute", start=start + timedelta(hours=2)
    )
    env.make_appointment(
        calendar_group=GROUP_1, title="dash497 morgen", start=end + timedelta(hours=2)
    )
    a = await data_of(
        env, USER_A, W("k", "core.calendar-agenda", settings={"days": 1, "limit": 6}), now=env.now
    )
    assert [i["title"] for i in a["items"]] == ["dash497 heute"]  # tomorrow is outside days=1
    b = await data_of(env, USER_B, W("k", "core.calendar-agenda"), now=env.now)
    assert b["items"] == []  # B has no ro on GROUP_1's calendar


async def test_calendar_agenda_day_starts_in_the_users_zone(env: Env) -> None:
    zone = "Pacific/Kiritimati"  # UTC+14, no DST: its day never lines up with UTC's
    env._sql_write(
        "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
        " VALUES (:u, :k, :v)",
        u=USER_A, k=USER_TIME_ZONE_KEY, v=zone,
    )  # fmt: skip
    assert await zone_of(env, USER_A) == zone
    start, end = agenda_window(env.now, zone, 1)
    env.make_appointment(
        calendar_group=GROUP_1, title="dash497 vorher", start=start - timedelta(hours=2)
    )
    env.make_appointment(
        calendar_group=GROUP_1, title="dash497 drin", start=start + timedelta(hours=1)
    )
    env.make_appointment(
        calendar_group=GROUP_1, title="dash497 danach", start=end + timedelta(hours=1)
    )
    out = await data_of(env, USER_A, W("k", "core.calendar-agenda"), now=env.now)
    assert [i["title"] for i in out["items"]] == ["dash497 drin"]


async def test_calendar_agenda_days_and_limit(env: Env) -> None:
    start, _ = agenda_window(env.now, await zone_of(env, USER_A), 1)
    for day in range(3):
        env.make_appointment(
            calendar_group=GROUP_1,
            title=f"dash497 tag{day}",
            start=start + timedelta(days=day, hours=1),
        )
    two = await data_of(
        env, USER_A, W("k", "core.calendar-agenda", settings={"days": 2}), now=env.now
    )
    assert [i["title"] for i in two["items"]] == ["dash497 tag0", "dash497 tag1"]
    one = await data_of(
        env, USER_A, W("k", "core.calendar-agenda", settings={"days": 3, "limit": 1}), now=env.now
    )
    assert [i["title"] for i in one["items"]] == ["dash497 tag0"]


def test_agenda_window_is_the_zone_day_in_naive_utc() -> None:
    start, end = agenda_window(datetime(2026, 1, 15, 23, 30), "Europe/Berlin", 2)
    # 23:30 UTC is already the 16th in Berlin (UTC+1)
    assert start == datetime(2026, 1, 15, 23, 0) and end == datetime(2026, 1, 17, 23, 0)


# --- team presence -----------------------------------------------------------------------


async def test_team_presence_lists_online_agents_own_first(env: Env) -> None:
    redis = FakeRedis({online_key(USER_A): "{}", online_key(USER_B): "{}"})
    out = await data_of(env, USER_B, W("p", "core.team-presence"), redis=redis)  # type: ignore[arg-type]
    assert [a["id"] for a in out["online"]] == [USER_B, USER_A] and out["total_online"] == 2
    capped = await data_of(
        env,
        USER_B,
        W("p", "core.team-presence", settings={"limit": 1}),
        redis=redis,  # type: ignore[arg-type]
    )
    assert [a["id"] for a in capped["online"]] == [USER_B] and capped["total_online"] == 2


async def test_team_presence_without_redis_is_empty_not_an_error(env: Env) -> None:
    out = await run_widget(
        env.factory, registry(), USER_A, W("p", "core.team-presence"), redis=None
    )
    assert out is not None and out.status == "ok" and out.data == {"online": [], "total_online": 0}


async def test_online_route_and_helper_return_the_same_list(env: Env) -> None:
    from tiqora.api.deps import get_redis

    redis = FakeRedis({online_key(USER_A): '{"avatar_url": null}'})
    env.app.dependency_overrides[get_redis] = lambda: redis
    try:
        async with env.session() as s:
            helper = [a.model_dump(mode="json") for a in await list_online_agents_for(redis, s)]  # type: ignore[arg-type]
        resp = await env.as_user(USER_A).get("/api/v1/agents/online")
    finally:
        env.app.dependency_overrides.pop(get_redis, None)
    assert resp.status_code == 200
    assert helper == resp.json() and [a["id"] for a in helper] == [USER_A]


# --- review fixes -----------------------------------------------------------------------


async def test_reminders_scan_finds_a_due_reminder_behind_older_pending_tickets(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scan is ordered by due time, so older-created far-future reminders cannot hide it."""
    from tiqora.dashboard.providers import reminders

    monkeypatch.setattr(reminders, "_SCAN_LIMIT", 2)
    far = int(time.time()) + 90 * 86400
    for i in range(3):  # created before a_pending, due in three months
        env._sql_write(
            "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, timeout, until_time,"
            " escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) SELECT :id, :tn, :ti, queue_id, ticket_lock_id, type_id, user_id,"
            " responsible_user_id, ticket_priority_id, ticket_state_id, 0, :u, 0, 0, 0, 0, 0,"
            " :t, 1, :t, 1 FROM ticket WHERE id = :src",
            id=49736 + i, tn=f"20261010497{36 + i}", ti=f"dash497 far{i}", u=far + i,
            t=datetime(2026, 9, 1), src=TICKETS["a_pending"],
        )  # fmt: skip
    data = await data_of(env, USER_A, W("r", "core.reminders", settings={"window_hours": 24}))
    assert ticket_ids(data) == [TICKETS["a_pending"]] and data["total"] == 1


async def test_escalations_total_matches_the_listed_items(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tiqora.dashboard.providers import escalations

    widget = W("e", "core.escalations", settings={"within_hours": 72})
    full = await data_of(env, USER_A, widget)
    assert full["total"] == len(full["items"]) >= 1
    # a listed row without any deadline above 0 is skipped and must leave the total too
    real = escalations.nearest_deadline
    skip = full["items"][0]["ticket"]["id"]
    monkeypatch.setattr(
        escalations, "nearest_deadline", lambda t: None if t.id == skip else real(t)
    )
    out = await data_of(env, USER_A, widget)
    assert out["total"] == len(out["items"]) == full["total"] - 1


async def test_all_day_appointments_belong_to_their_date_in_a_far_zone(env: Env) -> None:
    zone = "Pacific/Pago_Pago"  # UTC-11: local date differs from the UTC date for half a day
    env._sql_write(
        "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
        " VALUES (:u, :k, :v)",
        u=USER_A, k=USER_TIME_ZONE_KEY, v=zone,
    )  # fmt: skip
    from tiqora.dashboard.providers.calendar_agenda import start_day

    today = datetime.combine(start_day(env.now, zone), datetime.min.time())
    for title, day in (
        ("dash497 gestern", today - timedelta(days=1)),
        ("dash497 heute", today),
        ("dash497 morgen", today + timedelta(days=1)),
    ):
        env.make_appointment(calendar_group=GROUP_1, title=title, start=day, hours=24, all_day=True)
    out = await data_of(env, USER_A, W("k", "core.calendar-agenda"), now=env.now)
    assert [i["title"] for i in out["items"]] == ["dash497 heute"]


# --- cross-provider ----------------------------------------------------------------------


async def test_user_without_any_queue_gets_empty_states_not_errors(env: Env) -> None:
    reg = registry()
    for key, empty in (
        ("core.ticket-list", {"items": [], "total": 0}),
        ("core.escalations", {"items": [], "total": 0, "oldest_epoch": None}),
        ("core.reminders", {"items": [], "total": 0}),
        ("core.calendar-agenda", {"items": []}),
    ):
        out = await run_widget(env.factory, reg, USER_C, W("x", key))
        assert out is not None and out.status == "ok" and out.data == empty, key
    counter = await run_widget(env.factory, reg, USER_C, W("x", "core.counter"))
    assert counter is not None and counter.status == "ok"
    assert counter.data is not None and counter.data["value"] == 0


async def test_default_dashboard_batch_returns_data_for_every_provider_widget(env: Env) -> None:
    resp = await env.as_user(USER_A).post("/api/v1/dashboards/ensure-start")
    d = resp.json()
    r = await env.client.get(f"/api/v1/dashboards/{d['id']}/data")
    assert r.status_code == 200
    data = r.json()["widgets"]
    provider_types = {"core.counter", "core.escalations", "core.ticket-list", "core.reminders"}
    assert set(data) == {w["id"] for w in d["widgets"] if w["type_key"] in provider_types}
    assert len(data) == 9  # 4 counters, escalations, 3 lists, reminders
    assert all(x["status"] == "ok" for x in data.values())  # recent-views, queue-overview: frontend
