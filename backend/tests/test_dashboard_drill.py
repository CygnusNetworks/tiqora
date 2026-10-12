"""Drill-down: widget -> exact ticket list, preset filter parity, report points (both dialects).

Seed band 61410-61449 (names contain ``dash3``; the world of ``_dashboard3_helpers`` supplies
users A (``ro`` Q1), B (``ro`` Q2) and the queues):

- 61410-61421  preset tickets (every state type, owners A / unassigned, escalation epochs
  past / future / unset, one AI handoff row)
- 61430-61433  report tickets with history / article ids 61440-61443 (facts are rebuilt):
  two recent ones in Q1 (3 and 20 days old), one very old one in Q1 (60 days), one in Q2
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from tests._dashboard3_helpers import GROUP_2, Q1, Q2, A, B, Env3, open_env3
from tiqora.dashboard.drill import point_filter
from tiqora.dashboard.filters import DashboardFilters
from tiqora.dashboard.providers.presets import TICKET_PRESETS, preset_filter, preset_kwargs
from tiqora.dashboard.providers.report import (
    DrillNotPossible,
    drillable_dimensions,
    map_report_filter,
)
from tiqora.dashboard.schemas import DashboardOut
from tiqora.domain.ticket_service import TicketService
from tiqora.extensions import get_registry
from tiqora.filters import Filter
from tiqora.filters.tickets import compile_ticket_filter, ticket_query_scope
from tiqora.jobs.service import utc_now
from tiqora.reporting.compiler import RunParams
from tiqora.reporting.definition import ReportDefinition
from tiqora.reporting.facts_store import rebuild_ticket_facts
from tiqora.reporting.rights import ReportRights
from tiqora.reporting.saved import SavedReportIn, create_saved_report
from tiqora.reporting.sql import ReportQueryTimeout
from tiqora.saved_searches.store import SavedSearchData, create_search

pytestmark = pytest.mark.db

LO, HI = 61410, 61449
BAND = {"lo": LO, "hi": HI}
NO_LIST = "Für dieses Widget gibt es keine Ticketliste."
NO_POINT_LIST = "Für diesen Wert gibt es keine Ticketliste."
#: key -> (ticket id, queue, owner, state name, until offset or 0, escalation offset or 0)
PRESET_TICKETS: dict[str, tuple[int, int, int, str, int, int]] = {
    "new_a": (61410, Q1, A, "new", 0, 0),
    "new_none": (61411, Q1, 1, "new", 0, 0),
    "open_past": (61412, Q1, A, "open", 0, -3600),
    "open_soon": (61413, Q1, A, "open", 0, 7200),
    "open_none": (61414, Q1, 1, "open", 0, 0),
    "rem_soon": (61415, Q1, A, "pending reminder", 1800, 0),
    "rem_late": (61416, Q1, A, "pending reminder", 100 * 3600, 0),
    "rem_over": (61417, Q1, A, "pending reminder", -600, 0),
    "auto_soon": (61418, Q1, A, "pending auto close+", 600, 0),
    "closed": (61419, Q1, A, "closed successful", 0, 0),
    "merged": (61420, Q1, A, "merged", 0, 0),
    "ai": (61421, Q1, A, "open", 0, 0),
    "foreign": (61422, Q2, B, "open", 0, -3600),
}
T_AI = PRESET_TICKETS["ai"][0]
T_RECENT_Q1, T_OLD_Q1, T_ANCIENT_Q1, T_RECENT_Q2 = 61430, 61431, 61432, 61433
REPORT_TICKETS = (T_RECENT_Q1, T_OLD_Q1, T_ANCIENT_Q1, T_RECENT_Q2)
_WIPE = (
    "DELETE FROM tiqora_job_file WHERE job_id IN"
    " (SELECT id FROM tiqora_job WHERE kind IN ('core.report_run', 'core.report_export'))",
    "DELETE FROM tiqora_job WHERE kind IN ('core.report_run', 'core.report_export')",
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE name LIKE 'dash3%')",
    "DELETE FROM tiqora_report WHERE name LIKE 'dash3%'",
    "DELETE FROM tiqora_report_ticket_event WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_ticket_fact WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_dirty_ticket WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_ai_ticket_state WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_saved_search WHERE owner_user_id BETWEEN 61210 AND 61219",
    "DELETE FROM ticket_history WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM article WHERE ticket_id BETWEEN :lo AND :hi",
    "DELETE FROM ticket WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_state",
)


def _sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt), BAND)
    engine.dispose()


def _ticket_insert(conn: Any, **p: Any) -> None:
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
        p,
    )


def _seed(sync_url: str) -> None:
    _sql(sync_url, _WIPE)
    now = int(time.time())
    noon = utc_now().replace(hour=12, minute=0, second=0, microsecond=0)
    fixed = noon - timedelta(days=100)  # outside every report window used below
    engine = create_engine(sync_url)
    with engine.begin() as conn:

        def state_id(name: str) -> int:
            return int(
                conn.execute(
                    text("SELECT id FROM ticket_state WHERE name = :n"), {"n": name}
                ).scalar_one()
            )

        for key, (tid, queue, owner, state, until, esc) in PRESET_TICKETS.items():
            _ticket_insert(
                conn,
                id=tid,
                tn=f"20261012{tid}",
                title=f"dash3 {key}",
                q=queue,
                u=owner,
                s=state_id(state),
                until=now + until if until else 0,
                esc=now + esc if esc else 0,
                t=fixed,
            )
        conn.execute(
            text(
                "INSERT INTO tiqora_ai_ticket_state (ticket_id, ai_escalated_at) VALUES (:t, :at)"
            ),
            {"t": T_AI, "at": utc_now()},
        )
        h_new = int(
            conn.execute(
                text("SELECT id FROM ticket_history_type WHERE name = 'NewTicket'")
            ).scalar_one()
        )
        sender = int(
            conn.execute(
                text("SELECT id FROM article_sender_type WHERE name = 'customer'")
            ).scalar_one()
        )
        chan = int(
            conn.execute(
                text("SELECT id FROM communication_channel WHERE name = 'Email'")
            ).scalar_one()
        )
        new = state_id("new")
        plan = (
            (T_RECENT_Q1, Q1, noon - timedelta(days=3)),
            (T_OLD_Q1, Q1, noon - timedelta(days=20)),
            (T_ANCIENT_Q1, Q1, noon - timedelta(days=60)),
            (T_RECENT_Q2, Q2, noon - timedelta(days=3)),
        )
        for n, (tid, queue, at) in enumerate(plan):
            _ticket_insert(
                conn, id=tid, tn=f"20261012{tid}", title=f"dash3 {tid}", q=queue, u=1,
                s=new, until=0, esc=0, t=at,
            )  # fmt: skip
            conn.execute(
                text(
                    "INSERT INTO ticket_history (id, name, history_type_id, ticket_id,"
                    " article_id, type_id, queue_id, owner_id, priority_id, state_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, :ht, :tid, NULL, 1, :q, 1, 3, :s, :t, 1, :t, 1)"
                ),
                {"id": LO + 30 + n, "n": f"%%20261012{tid}%%dash3-queue-{queue}%%3 normal%%new",
                 "ht": h_new, "tid": tid, "q": queue, "s": new, "t": at},
            )  # fmt: skip
            conn.execute(
                text(
                    "INSERT INTO article (id, ticket_id, article_sender_type_id,"
                    " communication_channel_id, is_visible_for_customer,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :tid, :s, :c, 1, :t, 1, :t, 1)"
                ),
                {"id": LO + 30 + n, "tid": tid, "s": sender, "c": chan, "t": at},
            )
    engine.dispose()


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env3]:
    async with open_env3(request.getfixturevalue(request.param)) as e:
        _seed(e.sync_url)
        async with e.factory() as s:
            await rebuild_ticket_facts(s, REPORT_TICKETS, now=utc_now())
            await s.commit()
        try:
            yield e
        finally:
            _sql(e.sync_url, _WIPE)


# --- helpers -------------------------------------------------------------------------------


def tid(key: str) -> int:
    return PRESET_TICKETS[key][0]


def widget(
    wid: str,
    type_key: str,
    settings: dict[str, Any],
    *,
    pinned: bool = False,
    title: str | None = None,
) -> dict[str, Any]:
    return {
        "id": wid,
        "type_key": type_key,
        "type_version": 1,
        "title_override": title,
        "settings": settings,
        "layout": {"lg": {"x": 0, "y": 0, "w": 4, "h": 2}},
        "pinned": pinned,
    }


def counter(wid: str, preset: str, *, pinned: bool = False) -> dict[str, Any]:
    return widget(
        wid, "core.counter", {"source": {"kind": "preset", "preset": preset}}, pinned=pinned
    )


def ticket_list(wid: str, source: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return widget(wid, "core.ticket-list", {"source": source, "limit": 50}, **kw)


def report_widget(wid: str, report_key: str, **kw: Any) -> dict[str, Any]:
    return widget(wid, "core.report", {"report_key": report_key}, **kw)


async def board(
    env: Env3,
    uid: int,
    widgets: list[dict[str, Any]],
    *,
    queue_ids: list[int] | None = None,
) -> DashboardOut:
    for i, w in enumerate(widgets):
        w["layout"]["lg"]["y"] = i * 2
    d = await env.create_personal(owner=uid)
    r = await env.as_user(uid).put(
        f"/api/v1/dashboards/{d.id}",
        json={"name": d.name, "icon": d.icon, "version": d.version, "widgets": widgets},
    )
    assert r.status_code == 200, r.text
    d = DashboardOut.model_validate(r.json())
    if queue_ids is not None:
        d = await env.patch_filters(uid, d.id, {"queue_ids": queue_ids}, version=d.version)
    return d


async def drill(env: Env3, uid: int, d: DashboardOut, wid: str, **params: Any) -> Any:
    return await env.as_user(uid).get(
        f"/api/v1/dashboards/{d.id}/widgets/{wid}/tickets", params=params
    )


async def drill_ok(env: Env3, uid: int, d: DashboardOut, wid: str, **params: Any) -> dict[str, Any]:
    r = await drill(env, uid, d, wid, **params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def widget_data(env: Env3, uid: int, d: DashboardOut, wid: str) -> dict[str, Any]:
    r = await env.as_user(uid).get(f"/api/v1/dashboards/{d.id}/widgets/{wid}/data")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok", r.text
    data: dict[str, Any] = r.json()["data"]
    return data


def ids(body: dict[str, Any]) -> list[int]:
    return [i["id"] for i in body["items"]]


async def ids_via_list_tickets(env: Env3, uid: int, kwargs: dict[str, Any]) -> set[int]:
    async with env.factory() as s:
        page = await TicketService(s).list_tickets(uid, limit=200, **kwargs)
        return {i.id for i in page.items}


async def ids_via_compiled_filter(env: Env3, uid: int, flt: Filter) -> set[int]:
    async with env.factory() as s:
        scope = await ticket_query_scope(s, uid)
        stmt = await compile_ticket_filter(s, flt, scope)
        if stmt is None:
            return set()
        return {t.id for t in (await s.execute(stmt)).scalars()}


async def save_report(env: Env3, name: str, definition: dict[str, Any], *, owner: int = A) -> int:
    rights = ReportRights(view=True, create=True, share=True, admin=False)
    body = SavedReportIn(
        name=name,
        definition=ReportDefinition.model_validate(definition),
        visibility="private",
        group_ids=[],
    )
    saved = await env.run(create_saved_report, owner, rights, body, registry=get_registry())
    return int(saved.id)


# --- pure: report point -> ticket filter ---------------------------------------------------

QUEUE_DEF = ReportDefinition.model_validate(
    {
        "source": "tickets",
        "dimensions": [{"key": "queue"}],
        "measures": [{"key": "tickets"}],
        "time_range": {"kind": "relative", "unit": "day", "count": 30, "include_current": True},
    }
)
NOW = datetime(2026, 10, 12, 10, 0)


def _dump(flt: Filter) -> dict[str, Any]:
    return flt.model_dump(by_alias=True, exclude_none=True)


def test_point_filter_ands_window_report_filter_narrowing_and_point() -> None:
    params = RunParams(
        time_zone="Europe/Berlin",
        extra_filter=Filter.model_validate({"where": {"field": "queue", "op": "in", "value": [5]}}),
    )
    flt = point_filter(QUEUE_DEF, params, {"queue": 7}, now=NOW)
    assert _dump(flt) == {
        "version": 1,
        "where": {
            "all": [
                {
                    "field": "created",
                    "op": "between",
                    "value": {"from": "2026-09-12T22:00:00", "to": "2026-10-12T22:00:00"},
                },
                {"field": "queue_id", "op": "in", "value": [5]},
                {"field": "queue_id", "op": "eq", "value": 7},
            ]
        },
    }
    assert _dump(point_filter(QUEUE_DEF, params, None, now=NOW))["where"]["all"][-1] == {
        "field": "queue_id",
        "op": "in",
        "value": [5],
    }


def test_point_filter_date_bucket_follows_the_report_zone_across_dst() -> None:
    defn = ReportDefinition.model_validate(
        {
            "source": "tickets",
            "dimensions": [{"key": "created", "grain": "week"}],
            "measures": [{"key": "tickets"}],
            "time_range": {"kind": "relative", "unit": "week", "count": 12},
        }
    )
    params = RunParams(time_zone="Europe/Berlin")
    flt = point_filter(defn, params, {"created": "2026-10-26"}, now=NOW)
    assert _dump(flt)["where"]["all"][-1] == {
        "field": "created",
        "op": "between",
        "value": {"from": "2026-10-25T23:00:00", "to": "2026-11-01T23:00:00"},
    }
    for bad in ("2026-10-27", "x", 5):  # not a Monday, not a date, not a string
        with pytest.raises(DrillNotPossible):
            point_filter(defn, params, {"created": bad}, now=NOW)


def test_point_filter_refuses_what_has_no_ticket_field() -> None:
    params = RunParams(time_zone="UTC")

    def defn(**over: Any) -> ReportDefinition:
        base: dict[str, Any] = {
            "source": "tickets",
            "dimensions": [{"key": "queue"}],
            "measures": [{"key": "tickets"}],
        }
        return ReportDefinition.model_validate(base | over)

    for bad in (
        defn(dimensions=[{"key": "owner"}]),  # person dimension
        defn(dimensions=[{"key": "queue"}, {"key": "customer"}]),
        defn(filter={"where": {"field": "merged", "op": "is_true"}}),  # filter-only flag
        defn(filter={"where": {"field": "owner", "op": "eq", "value": 3}}),
        defn(time_field="closed"),
        defn(dimensions=[{"key": "created_hour"}]),
    ):
        with pytest.raises(DrillNotPossible):
            point_filter(bad, params, None, now=NOW)
    events = ReportDefinition.model_validate(
        {
            "source": "ticket_events",
            "dimensions": [{"key": "channel"}],
            "measures": [{"key": "events"}],
        }
    )
    with pytest.raises(DrillNotPossible):
        point_filter(events, params, None, now=NOW)
    with pytest.raises(DrillNotPossible):  # a point that names no dimension of the report
        point_filter(defn(), params, {"state": 1}, now=NOW)
    with pytest.raises(DrillNotPossible):
        point_filter(defn(), params, {"queue": True}, now=NOW)


def test_point_filter_open_tickets_lists_open_states_only() -> None:
    defn = ReportDefinition.model_validate(
        {
            "source": "open_tickets",
            "dimensions": [{"key": "state_type"}],
            "measures": [{"key": "tickets"}],
        }
    )
    flt = _dump(point_filter(defn, RunParams(time_zone="UTC"), {"state_type": "open"}, now=NOW))
    nodes = flt["where"]["all"]
    assert {"field": "state_type", "op": "eq", "value": "open"} in nodes
    assert any(
        n["field"] == "state_type" and n["op"] == "in" and "new" in n["value"] for n in nodes
    )
    with pytest.raises(DrillNotPossible):  # "closed" is not a state type of this source
        point_filter(defn, RunParams(time_zone="UTC"), {"state_type": "closed"}, now=NOW)


def test_map_report_filter_keeps_the_tree_shape() -> None:
    flt = Filter.model_validate(
        {
            "where": {
                "any": [
                    {"field": "queue", "op": "in", "value": [1, 2]},
                    {"not": {"field": "state_type", "op": "eq", "value": "closed"}},
                ]
            }
        }
    )
    assert _dump(map_report_filter(flt)) == {  # type: ignore[arg-type]
        "version": 1,
        "where": {
            "any": [
                {"field": "queue_id", "op": "in", "value": [1, 2]},
                {"not": {"field": "state_type", "op": "eq", "value": "closed"}},
            ]
        },
    }
    assert map_report_filter(None) is None


def test_drillable_dimensions_need_every_part_to_map() -> None:
    defn = ReportDefinition.model_validate(
        {
            "source": "tickets",
            "dimensions": [{"key": "queue", "choices": ["owner", "type"]}],
            "measures": [{"key": "tickets"}],
        }
    )
    assert drillable_dimensions(defn) == ["queue"]
    assert drillable_dimensions(defn, {0: "type"}) == ["type"]
    assert drillable_dimensions(defn, {0: "owner"}) == []  # a person dimension chosen
    assert drillable_dimensions(defn, {0: "nope"}) == ["queue"]  # unknown choice ignored
    assert drillable_dimensions(QUEUE_DEF.model_copy(update={"time_field": "closed"})) == []


# --- preset parity -------------------------------------------------------------------------


@pytest.mark.parametrize("preset", list(TICKET_PRESETS))
async def test_preset_filter_parity_per_preset(env: Env3, preset: str) -> None:
    flt = preset_filter(preset, A)
    assert flt is not None
    via_kwargs = await ids_via_list_tickets(env, A, preset_kwargs(preset, A))
    via_filter = await ids_via_compiled_filter(env, A, flt)
    assert via_kwargs, f"{preset}: the seed must make the comparison meaningful"
    assert via_filter == via_kwargs


async def test_preset_filter_unknown_preset_is_none() -> None:
    assert preset_filter("nope", A) is None


# --- widget lists --------------------------------------------------------------------------


@pytest.mark.parametrize("pinned", [False, True], ids=["following", "pinned"])
@pytest.mark.parametrize("queues", [None, [Q1], [Q2]], ids=["nofilter", "q1", "q2"])
@pytest.mark.parametrize("preset", list(TICKET_PRESETS))
async def test_drill_total_equals_counter_value(
    env: Env3, preset: str, queues: list[int] | None, pinned: bool
) -> None:
    d = await board(env, A, [counter("dash3wid00001", preset, pinned=pinned)], queue_ids=queues)
    value = (await widget_data(env, A, d, "dash3wid00001"))["value"]
    body = await drill_ok(env, A, d, "dash3wid00001")
    assert body["total"] == value
    assert body["total"] == len(body["items"])
    if queues == [Q2] and not pinned:
        assert value == 0  # A cannot read Q2: a filter never widens
    else:
        assert value > 0
    assert body["filter"] is not None
    flt = Filter.model_validate(body["filter"])
    assert len(await ids_via_compiled_filter(env, A, flt)) == value


async def test_drill_pages_and_sorts(env: Env3) -> None:
    d = await board(
        env, A, [ticket_list("dash3wid00001", {"kind": "preset", "preset": "mine_open"})]
    )
    full = await drill_ok(env, A, d, "dash3wid00001", sort="tn", order="asc", limit=50)
    assert full["total"] == 8 and ids(full) == sorted(ids(full))  # tn follows the seeded ids
    first = await drill_ok(env, A, d, "dash3wid00001", sort="tn", order="asc", limit=2, offset=0)
    third = await drill_ok(env, A, d, "dash3wid00001", sort="tn", order="asc", limit=2, offset=2)
    assert ids(first) == ids(full)[:2] and ids(third) == ids(full)[2:4]
    assert first["total"] == third["total"] == 8
    desc = await drill_ok(env, A, d, "dash3wid00001", sort="tn", order="desc", limit=50)
    assert ids(desc) == ids(full)[::-1]
    for bad in ({"limit": 51}, {"limit": 0}, {"offset": -1}, {"order": "up"}, {"sort": "nope"}):
        r = await drill(env, A, d, "dash3wid00001", **bad)
        assert r.status_code == 422, bad


async def test_drill_escalations_and_reminders_totals_match_widget(env: Env3) -> None:
    d = await board(
        env,
        A,
        [
            widget("dash3wid00001", "core.escalations", {"limit": 1}),
            widget("dash3wid00002", "core.reminders", {"limit": 1}),
        ],
    )
    esc_widget = await widget_data(env, A, d, "dash3wid00001")
    esc = await drill_ok(env, A, d, "dash3wid00001")
    assert esc["total"] == esc_widget["total"] == 2
    assert set(ids(esc)) == {tid("open_past"), tid("open_soon")}
    assert ids(esc)[0] == tid("open_past")  # nearest deadline first
    assert esc["filter"] is not None

    rem_widget = await widget_data(env, A, d, "dash3wid00002")  # limit 1: total still counts all
    rem = await drill_ok(env, A, d, "dash3wid00002")
    due = {tid("rem_soon"), tid("auto_soon"), tid("rem_over")}  # rem_late is beyond 24 h
    assert rem["total"] == rem_widget["total"] == 3
    assert set(ids(rem)) == due
    assert ids(rem)[0] == tid("rem_over")  # soonest first, overdue included
    assert len(rem_widget["items"]) == 1

    # the dashboard queue filter narrows both the same way, a pinned widget ignores it
    await env.patch_filters(A, d.id, {"queue_ids": [Q2]}, version=(await env.get(A, d.id)).version)
    for wid in ("dash3wid00001", "dash3wid00002"):
        assert (await drill_ok(env, A, d, wid))["total"] == 0
        assert (await widget_data(env, A, d, wid))["total"] == 0


async def test_drill_saved_search_widget_offers_its_criteria_anded_with_queue_filter(
    env: Env3,
) -> None:
    criteria = {"version": 1, "where": {"field": "owner_id", "op": "eq", "value": A}}
    async with env.factory() as s:
        search = await create_search(
            s,
            A,
            SavedSearchData(
                name="dash3 meine",
                criteria=Filter.model_validate(criteria),
                include_archived=False,
                sort_by="age",
                sort_order="desc",
                group_ids=(),
            ),
        )
        await s.commit()
    source = {"kind": "saved_search", "id": search.id}
    d = await board(env, A, [ticket_list("dash3wid00001", source)], queue_ids=[Q1])
    body = await drill_ok(env, A, d, "dash3wid00001")
    assert body["title"] == "dash3 meine"
    assert body["filter"] == {
        "version": 1,
        "where": {"all": [criteria["where"], {"field": "queue_id", "op": "in", "value": [Q1]}]},
    }
    assert body["total"] == (await widget_data(env, A, d, "dash3wid00001"))["total"] == 10
    assert tid("foreign") not in ids(body)
    # an own filter source behaves the same
    own = await board(
        env, A, [ticket_list("dash3wid00011", {"kind": "filter", "filter": criteria})]
    )
    plain = await drill_ok(env, A, own, "dash3wid00011")
    assert plain["filter"] == criteria and plain["total"] == body["total"]
    # a viewer who cannot see the saved search gets the German 403, never A's tickets
    env.share(d.id, GROUP_2)  # B reads the dashboard but not A's private search
    r = await drill(env, B, d, "dash3wid00001")
    assert r.status_code == 403


# --- report points -------------------------------------------------------------------------


async def test_drill_point_is_rebuilt_server_side(env: Env3) -> None:
    d = await board(env, A, [report_widget("dash3wid00001", "builtin.volume_by_queue")])
    data = await widget_data(env, A, d, "dash3wid00001")
    assert data["drillable_dimensions"] == ["queue"]

    q1 = await drill_ok(env, A, d, "dash3wid00001", point=json.dumps({"queue": Q1}))
    assert set(ids(q1)) == {T_RECENT_Q1, T_OLD_Q1}  # the 60-day-old ticket is outside the window
    assert q1["total"] == 2 and q1["note"] is not None
    assert q1["title"].endswith(f"· Queue dash3-queue-{Q1}")
    assert Filter.model_validate(q1["filter"]).where is not None

    # a queue A cannot read: an empty list in the viewer's scope, not a 403
    q2 = await drill(env, A, d, "dash3wid00001", point=json.dumps({"queue": Q2}))
    assert q2.status_code == 200 and q2.json()["items"] == [] and q2.json()["total"] == 0
    # the whole report population
    whole = await drill_ok(env, A, d, "dash3wid00001")
    assert set(ids(whole)) == {T_RECENT_Q1, T_OLD_Q1}

    # B sees his own queue's ticket only
    other = await board(env, B, [report_widget("dash3wid00031", "builtin.volume_by_queue")])
    assert ids(await drill_ok(env, B, other, "dash3wid00031", point=json.dumps({"queue": Q2}))) == [
        T_RECENT_Q2
    ]


async def test_drill_report_honours_dashboard_filters_and_pinning(env: Env3) -> None:
    widgets = [
        report_widget("dash3wid00001", "builtin.volume_by_queue"),
        report_widget("dash3wid00002", "builtin.volume_by_queue", pinned=True),
    ]
    d = await board(env, A, widgets, queue_ids=[Q2])  # A cannot read Q2: the filter only narrows
    assert (await drill_ok(env, A, d, "dash3wid00001"))["total"] == 0
    assert (await drill_ok(env, A, d, "dash3wid00002"))["total"] == 2
    await env.patch_filters(
        A, d.id, {"queue_ids": [], "time_range": "7d"}, version=(await env.get(A, d.id)).version
    )
    narrowed = await drill_ok(env, A, d, "dash3wid00001")
    assert ids(narrowed) == [T_RECENT_Q1]  # last 7 days
    assert (await drill_ok(env, A, d, "dash3wid00002"))["total"] == 2


async def test_drill_date_bucket_point(env: Env3) -> None:
    rid = await save_report(
        env,
        "dash3 by day",
        {
            "source": "tickets",
            "dimensions": [{"key": "created", "grain": "day"}],
            "measures": [{"key": "tickets"}],
            "time_zone": "UTC",
            "time_range": {"kind": "relative", "unit": "day", "count": 30, "include_current": True},
        },
    )
    d = await board(env, A, [report_widget("dash3wid00001", str(rid))])
    assert (await widget_data(env, A, d, "dash3wid00001"))["drillable_dimensions"] == ["created"]
    day = (utc_now().replace(hour=12) - timedelta(days=3)).date().isoformat()
    body = await drill_ok(env, A, d, "dash3wid00001", point=json.dumps({"created": day}))
    assert ids(body) == [T_RECENT_Q1]


async def test_person_dimension_and_foreign_source_not_drillable(env: Env3) -> None:
    events = {
        "source": "ticket_events",
        "dimensions": [{"key": "channel"}],
        "measures": [{"key": "events"}],
    }
    by_customer = {
        "source": "tickets",
        "dimensions": [{"key": "customer"}],
        "measures": [{"key": "tickets"}],
    }
    ev_id = await save_report(env, "dash3 events", events)
    cu_id = await save_report(env, "dash3 customers", by_customer)
    d = await board(
        env,
        A,
        [
            report_widget("dash3wid00001", "builtin.sla_by_customer"),
            report_widget("dash3wid00002", str(ev_id)),
            report_widget("dash3wid00003", str(cu_id)),
        ],
    )
    for wid, point in (
        ("dash3wid00001", {"customer": "Beispiel GmbH"}),
        ("dash3wid00002", {"channel": "email"}),
        ("dash3wid00003", {"customer": "Beispiel GmbH"}),
    ):
        data = await widget_data(env, A, d, wid)
        assert data["drillable_dimensions"] == []
        r = await drill(env, A, d, wid, point=json.dumps(point))
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == {"message": NO_POINT_LIST, "path": "point"}
        whole = await drill(env, A, d, wid)
        assert whole.status_code == 422 and whole.json()["detail"]["message"] == NO_POINT_LIST


async def test_point_size_and_shape_bounds(env: Env3) -> None:
    d = await board(
        env,
        A,
        [
            report_widget("dash3wid00001", "builtin.volume_by_queue"),
            counter("dash3wid00002", "mine_open"),
        ],
    )
    for bad in (
        json.dumps({"a": 1, "b": 2, "c": 3, "d": 4}),  # more than three keys
        json.dumps({"queue": "x" * 600}),  # more than 500 bytes
        json.dumps([1]),  # not an object
        json.dumps({}),
        "not json",
        json.dumps(5),
    ):
        r = await drill(env, A, d, "dash3wid00001", point=bad)
        assert r.status_code == 422, bad
    wrong_type = await drill(env, A, d, "dash3wid00001", point=json.dumps({"queue": "Q1"}))
    assert wrong_type.status_code == 422
    unknown = await drill(env, A, d, "dash3wid00001", point=json.dumps({"state": 1}))
    assert unknown.status_code == 422
    # a point on a widget without dimensions
    on_counter = await drill(env, A, d, "dash3wid00002", point=json.dumps({"queue": Q1}))
    assert on_counter.status_code == 422


async def test_drill_on_invisible_dashboard_or_foreign_widget_404(env: Env3) -> None:
    d = await board(env, A, [counter("dash3wid00001", "mine_open")])
    other = await board(env, A, [counter("dash3wid00009", "mine_new")])
    hidden = await drill(env, B, d, "dash3wid00001")
    assert hidden.status_code == 404
    missing = await env.as_user(B).get(f"/api/v1/dashboards/{2**40}/widgets/dash3wid00001/tickets")
    assert missing.status_code == 404 and missing.json() == hidden.json()
    assert (await drill(env, A, d, "dash3wid00009")).status_code == 404  # on another dashboard
    assert (await drill(env, A, other, "dash3wid00001")).status_code == 404


async def test_widgets_without_a_ticket_list_are_422(env: Env3) -> None:
    d = await board(
        env,
        A,
        [
            widget("dash3wid00001", "core.notes", {"markdown": "x"}),
            widget("dash3wid00002", "core.queue-overview", {}),
        ],
    )
    for wid in ("dash3wid00001", "dash3wid00002"):
        r = await drill(env, A, d, wid)
        assert r.status_code == 422
        assert r.json()["detail"] == {"message": NO_LIST, "path": "widget_id"}


async def test_drill_timeout_is_a_german_503(env: Env3, monkeypatch: pytest.MonkeyPatch) -> None:
    d = await board(env, A, [counter("dash3wid00001", "mine_open")])

    @asynccontextmanager
    async def boom(session: Any, seconds: int) -> AsyncIterator[None]:
        raise ReportQueryTimeout("statement exceeded")
        yield

    monkeypatch.setattr("tiqora.api.v1.dashboards.statement_timeout", boom)
    r = await drill(env, A, d, "dash3wid00001")
    assert r.status_code == 503
    assert r.json()["detail"] == {
        "message": "Die Liste braucht zu lange. Bitte grenzen Sie den Filter ein."
    }


async def test_drill_is_read_only(env: Env3) -> None:
    d = await board(env, A, [counter("dash3wid00001", "mine_open")], queue_ids=[Q1])
    before = await env.get(A, d.id)
    await drill_ok(env, A, d, "dash3wid00001")
    after = await env.get(A, d.id)
    assert after.version == before.version and after.filters == DashboardFilters(queue_ids=[Q1])


def test_drill_sort_keys_are_the_ticket_services_sort_columns() -> None:
    """The drill route checks ``sort`` against a public list; it must stay the service's."""
    from tiqora.api.v1.dashboards import DRILL_SORT_KEYS

    assert set(TicketService._SORT_COLUMNS) == DRILL_SORT_KEYS
