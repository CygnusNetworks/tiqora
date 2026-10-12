"""Runner and phase-1 providers honour the dashboard's queue filter (both dialects).

Seed band 61240-61279 (tickets, one extra queue 61217 "Q1b" in A's group); the world of
``_dashboard3_helpers`` supplies users A (ro Q1 + Q1b), B (ro Q2 only) and the queues.
Time-sensitive rows are seeded relative to the real clock.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from tests._dashboard3_helpers import GROUP_1, GROUP_2, Q1, Q2, A, B, Env3, open_env3
from tiqora.dashboard.filters import DashboardFilters, filters_key
from tiqora.dashboard.providers import DASHBOARD_PROVIDERS, core_providers
from tiqora.dashboard.runner import run_batch, run_widget
from tiqora.domain.ticket_service import TicketService
from tiqora.extensions.registry import Registry
from tiqora.filters import Filter
from tiqora.saved_searches.store import SavedSearchData, create_search

pytestmark = pytest.mark.db

Q1B = 61217
T_BASE = 61240
#: key -> (ticket id, queue, owner, state name, until_time offset, escalation offset or None)
SEED: dict[str, tuple[int, int, int, str, int, int | None]] = {
    "a1": (T_BASE + 0, Q1, A, "open", 0, 3600),
    "a2": (T_BASE + 1, Q1, A, "open", 0, None),
    "a3": (T_BASE + 2, Q1B, A, "open", 0, 7200),
    "a_new": (T_BASE + 3, Q1, A, "new", 0, None),
    "a_rem1": (T_BASE + 4, Q1, A, "pending reminder", 1800, None),
    "a_rem2": (T_BASE + 5, Q1B, A, "pending reminder", 3600, None),
    "b1": (T_BASE + 6, Q2, B, "open", 0, 1800),
}


@dataclass
class W:
    id: str
    type_key: str
    settings: dict[str, Any] = field(default_factory=dict)
    type_version: int = 1
    pinned: bool = False


def registry() -> Registry:
    reg = Registry()
    for p in core_providers():
        reg.add(DASHBOARD_PROVIDERS, p)
    reg.freeze()
    return reg


def _tid(key: str) -> int:
    return SEED[key][0]


def _seed(sync_url: str) -> None:
    _cleanup(sync_url)
    t = datetime(2026, 10, 1, 8, 0)
    now = int(time.time())
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id,"
                " create_time, create_by, change_time, change_by)"
                " VALUES (:id, 'dash3-queue-q1b', :g, 1, 1, 1, 1, 0, 1, :t, 1, :t, 1)"
            ),
            {"id": Q1B, "g": GROUP_1, "t": t},
        )
        states = {
            name: int(
                conn.execute(
                    text("SELECT id FROM ticket_state WHERE name = :n"), {"n": name}
                ).scalar_one()
            )
            for name in ("new", "open", "pending reminder")
        }
        for key, (tid, queue, owner, st, until, esc) in SEED.items():
            conn.execute(
                text(
                    "INSERT INTO ticket (id, tn, title, queue_id, ticket_lock_id, type_id,"
                    " user_id, responsible_user_id, ticket_priority_id, ticket_state_id,"
                    " customer_id, customer_user_id, timeout, until_time, escalation_time,"
                    " escalation_update_time, escalation_response_time,"
                    " escalation_solution_time, archive_flag,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tn, :title, :q, 1, 1, :u, 1, 3, :s, NULL, NULL,"
                    " 0, :until, :esc, 0, 0, :esc, 0, :t, 1, :t, 1)"
                ),
                {
                    "id": tid,
                    "tn": f"2026101061{tid}",
                    "title": f"dash3 {key}",
                    "q": queue,
                    "u": owner,
                    "s": states[st],
                    "until": now + until if until else 0,
                    "esc": now + esc if esc else 0,
                    "t": t,
                },
            )
    engine.dispose()


def _cleanup(sync_url: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ticket WHERE id BETWEEN 61240 AND 61279"))
        conn.execute(
            text("DELETE FROM tiqora_saved_search WHERE owner_user_id BETWEEN 61210 AND 61219")
        )
        conn.execute(text("DELETE FROM queue WHERE id = :q"), {"q": Q1B})
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env3]:
    async with open_env3(request.getfixturevalue(request.param)) as e:
        _seed(e.sync_url)
        try:
            yield e
        finally:
            _cleanup(e.sync_url)


def counter(preset: str = "mine_open", *, pinned: bool = False, wid: str = "c") -> W:
    return W(
        wid,
        "core.counter",
        settings={"source": {"kind": "preset", "preset": preset}},
        pinned=pinned,
    )


def listing(preset: str = "mine_open", *, pinned: bool = False) -> W:
    return W(
        "l",
        "core.ticket-list",
        settings={"source": {"kind": "preset", "preset": preset}, "limit": 50},
        pinned=pinned,
    )


async def data_of(env: Env3, uid: int, widget: W, flt: DashboardFilters | None = None) -> Any:
    out = await run_widget(env.factory, registry(), uid, widget, filters=flt)
    assert out is not None and out.status == "ok", out
    assert out.data is not None
    return out.data


def fq(*queue_ids: int) -> DashboardFilters:
    return DashboardFilters(queue_ids=list(queue_ids))


async def test_counter_and_list_narrow_to_filter_queues(env: Env3) -> None:
    # "open" is every not-closed state: open, new and pending
    assert (await data_of(env, A, counter()))["value"] == 6
    c = await data_of(env, A, counter(), fq(Q1))
    lst = await data_of(env, A, listing(), fq(Q1))
    assert c["value"] == lst["total"] == 4
    assert {i["id"] for i in lst["items"]} == {_tid(k) for k in ("a1", "a2", "a_new", "a_rem1")}
    assert {i["queue_id"] for i in lst["items"]} == {Q1}
    both = await data_of(env, A, counter(), fq(Q1, Q1B))
    assert both["value"] == 6


async def test_escalations_and_reminders_narrow(env: Env3) -> None:
    esc = W("e", "core.escalations", settings={"within_hours": 24, "limit": 10})
    rem = W("r", "core.reminders", settings={"window_hours": 24, "limit": 10})
    all_esc = await data_of(env, A, esc)
    assert {x["ticket"]["id"] for x in all_esc["items"]} == {_tid("a1"), _tid("a3")}
    narrowed = await data_of(env, A, esc, fq(Q1))
    assert [x["ticket"]["id"] for x in narrowed["items"]] == [_tid("a1")]
    assert narrowed["total"] == 1
    assert {x["ticket"]["id"] for x in (await data_of(env, A, rem))["items"]} == {
        _tid("a_rem1"),
        _tid("a_rem2"),
    }
    only = await data_of(env, A, rem, fq(Q1))
    assert [x["ticket"]["id"] for x in only["items"]] == [_tid("a_rem1")]


async def test_filter_queue_outside_scope_yields_nothing(env: Env3) -> None:
    flt = fq(Q2)  # A has no ro on Q2
    assert (await data_of(env, A, counter(), flt))["value"] == 0
    lst = await data_of(env, A, listing(), flt)
    assert lst["items"] == [] and lst["total"] == 0
    esc = W("e", "core.escalations", settings={"within_hours": 24, "limit": 10})
    assert (await data_of(env, A, esc, flt))["items"] == []
    rem = W("r", "core.reminders", settings={"window_hours": 24, "limit": 10})
    assert (await data_of(env, A, rem, flt))["items"] == []


def _widget_json(wid: str, type_key: str, settings: dict[str, Any], *, pinned: bool = False) -> Any:
    return {
        "id": wid,
        "type_key": type_key,
        "type_version": 1,
        "title_override": None,
        "settings": settings,
        "layout": {"lg": {"x": 0, "y": 0, "w": 3, "h": 2}},
        "pinned": pinned,
    }


async def test_shared_dashboard_filter_runs_in_viewer_scope(env: Env3) -> None:
    """A shares a dashboard filtered to [Q1, Q2]; each viewer only sees what they may."""
    src = {"kind": "filter", "filter": {"version": 1}}  # every ticket in the viewer's scope
    d = await env.create_personal(owner=A)
    put = await env.as_user(A).put(
        f"/api/v1/dashboards/{d.id}",
        json={
            "name": d.name,
            "icon": d.icon,
            "version": d.version,
            "widgets": [
                _widget_json("dash3wid00001", "core.counter", {"source": src}),
                _widget_json("dash3wid00002", "core.counter", {"source": src}, pinned=True),
            ],
        },
    )
    assert put.status_code == 200, put.text
    saved = await env.patch_filters(A, d.id, {"queue_ids": [Q1, Q2]}, version=put.json()["version"])
    env.share(saved.id, GROUP_2)

    r_a = await env.as_user(A).get(f"/api/v1/dashboards/{d.id}/data")
    assert r_a.status_code == 200
    wa = r_a.json()["widgets"]
    assert wa["dash3wid00001"]["data"]["value"] == 4  # Q1 only (A has no ro on Q2)
    assert wa["dash3wid00002"]["data"]["value"] == 6  # pinned: unfiltered, A's whole scope
    r_b = await env.as_user(B).get(f"/api/v1/dashboards/{d.id}/data")
    wb = r_b.json()["widgets"]
    assert wb["dash3wid00001"]["data"]["value"] == 1  # Q2 only
    assert wb["dash3wid00002"]["data"]["value"] == 1
    one = await env.as_user(A).get(f"/api/v1/dashboards/{d.id}/widgets/dash3wid00001/data")
    assert one.json()["data"]["value"] == 4  # the single-widget path filters too


async def test_pinned_widget_ignores_filters(env: Env3) -> None:
    flt = fq(Q1)
    assert (await data_of(env, A, counter(pinned=True), flt))["value"] == 6
    assert (await data_of(env, A, listing(pinned=True), flt))["total"] == 6
    out = await run_batch(
        env.factory,
        registry(),
        A,
        [counter(wid="x"), counter(pinned=True, wid="y")],
        filters=flt,
    )
    assert out["x"].data is not None and out["x"].data["value"] == 4
    assert out["y"].data is not None and out["y"].data["value"] == 6


async def test_saved_search_and_own_filter_sources_are_narrowed(env: Env3) -> None:
    open_filter = {"version": 1, "where": {"field": "owner_id", "op": "eq", "value": A}}
    async with env.factory() as s:
        search = await create_search(
            s,
            A,
            SavedSearchData(
                name="dash3 search",
                criteria=Filter.model_validate(open_filter),
                include_archived=False,
                sort_by="age",
                sort_order="desc",
                group_ids=(),
            ),
        )
        await s.commit()
    for source in (
        {"kind": "saved_search", "id": search.id},
        {"kind": "filter", "filter": open_filter},
    ):
        c = W("c", "core.counter", settings={"source": source})
        lst = W("l", "core.ticket-list", settings={"source": source, "limit": 50})
        assert (await data_of(env, A, c))["value"] == 6
        assert (await data_of(env, A, c, fq(Q1)))["value"] == 4
        narrowed = await data_of(env, A, lst, fq(Q1))
        assert narrowed["total"] == 4
        assert {i["queue_id"] for i in narrowed["items"]} == {Q1}
        outside = await data_of(env, A, c, fq(Q2))
        assert outside["value"] == 0


async def test_filtered_counter_history_is_separate(env: Env3) -> None:
    await data_of(env, A, counter())
    await data_of(env, A, counter(), fq(Q1))
    rows = env.sql(
        "SELECT counter_key, value FROM tiqora_dashboard_counter_sample"
        " WHERE user_id = :u ORDER BY counter_key",
        u=A,
    )
    suffix = filters_key(fq(Q1))
    assert suffix is not None
    assert [(r[0], r[1]) for r in rows] == [
        ("preset:mine_open", 6),
        (f"preset:mine_open|{suffix}", 4),
    ]


async def test_empty_filters_keep_phase1_parity(env: Env3) -> None:
    async with env.factory() as s:
        summary = await TicketService(s).count_dashboard_summary(A)
    for flt in (None, DashboardFilters(), DashboardFilters(time_range="7d")):
        for preset, key in (
            ("mine_open", "my_open"),
            ("mine_new", "my_new"),
            ("unowned_new", "unowned_new"),
            ("escalated", "escalated"),
            ("ai_escalated", "ai_escalated"),
        ):
            got = (await data_of(env, A, counter(preset), flt))["value"]
            assert got == summary[key], (preset, flt)
    keys = {r[0] for r in env.sql("SELECT counter_key FROM tiqora_dashboard_counter_sample")}
    assert all("|" not in k for k in keys if k.startswith("preset:"))


async def test_count_by_queue_matches_per_queue_counts(env: Env3) -> None:
    async with env.factory() as s:
        svc = TicketService(s)
        by_queue = await svc.count_by_queue(A, state_type="open", owner_id=A)
        assert by_queue == {Q1: 4, Q1B: 2}
        for q, n in by_queue.items():
            assert await svc.count_filtered(A, state_type="open", owner_id=A, queue_ids=[q]) == n
        assert sum(by_queue.values()) == await svc.count_filtered(A, state_type="open", owner_id=A)
        assert await svc.count_by_queue(B, state_type="open") == {Q2: 1}
        assert await svc.count_by_queue(A, state_type="open", queue_ids=[Q2]) == {}
        assert await svc.count_by_queue(A, state_type="open", queue_ids=[Q1]) == {Q1: 4}


async def test_preview_applies_the_dashboard_filters(env: Env3) -> None:
    """The settings drawer's preview counts like the saved tile on a filtered dashboard."""
    url = "/api/v1/dashboard-widget-types/core.counter/data"
    settings = {"source": {"kind": "preset", "preset": "mine_open"}}
    client = env.as_user(A)
    plain = await client.post(url, json={"settings": settings})
    assert plain.status_code == 200 and plain.json()["data"]["value"] == 6
    narrowed = await client.post(
        url, json={"settings": settings, "filters": {"queue_ids": [Q1], "time_range": None}}
    )
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["status"] == "ok" and narrowed.json()["data"]["value"] == 4
    lst = await client.post(
        "/api/v1/dashboard-widget-types/core.ticket-list/data",
        json={"settings": settings | {"limit": 50}, "filters": {"queue_ids": [Q1B]}},
    )
    assert lst.status_code == 200, lst.text
    assert {i["queue_id"] for i in lst.json()["data"]["items"]} == {Q1B}
