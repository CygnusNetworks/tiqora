"""Dashboard-wide filters and pinned widgets in the store and the API (both dialects).

Seed band 61200-61599 (see ``_dashboard3_helpers``; names contain ``dash3``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import ValidationError

from tests._dashboard3_helpers import (
    ADMIN,
    GROUP_2,
    Q1,
    Q2,
    ROLE_A,
    A,
    B,
    Env3,
    open_env3,
)
from tiqora.dashboard import start, store
from tiqora.dashboard.filters import DashboardFilters
from tiqora.dashboard.schemas import (
    DashboardCreateIn,
    DashboardFiltersIn,
    DashboardOut,
    DashboardSaveIn,
    WidgetIn,
    WidgetPreviewIn,
)

pytestmark = pytest.mark.db


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env3]:
    async with open_env3(request.getfixturevalue(request.param)) as e:
        yield e


def _w(i: int, *, pinned: bool = False) -> dict[str, Any]:
    return {
        "id": f"dash3wid{i:05d}",
        "type_key": "core.notes",
        "type_version": 1,
        "title_override": None,
        "settings": {"markdown": ""},
        "layout": {"lg": {"x": 0, "y": i, "w": 4, "h": 2}},
        "pinned": pinned,
    }


def _put_body(d: DashboardOut, **over: Any) -> dict[str, Any]:
    body = {
        "name": d.name,
        "icon": d.icon,
        "version": d.version,
        "widgets": [w.model_dump(mode="json") for w in d.widgets],
    }
    return body | over


async def test_filters_round_trip_and_bump_version(env: Env3) -> None:
    d = await env.create_personal(owner=A)
    assert d.filters == DashboardFilters() and d.template_update is None
    out = await env.patch_filters(
        A, d.id, {"queue_ids": [Q1, Q1], "time_range": "30d"}, version=d.version
    )
    assert out.filters.queue_ids == [Q1] and out.version == d.version + 1
    assert (await env.get(A, d.id)).filters.time_range == "30d"
    assert json.loads(env.stored_filters(d.id)) == {"queue_ids": [Q1], "time_range": "30d"}
    r = await env.as_user(A).get(f"/api/v1/dashboards/{d.id}")
    assert r.json()["filters"] == {"queue_ids": [Q1], "time_range": "30d"}


async def test_filters_stale_version_is_409_and_nothing_changes(env: Env3) -> None:
    d = await env.create_personal(owner=A)
    first = await env.patch_filters(A, d.id, {"queue_ids": [Q1]}, version=d.version)
    r = await env.patch_filters_raw(A, d.id, {"queue_ids": [Q2]}, version=d.version)  # stale
    assert r.status_code == 409
    assert r.json()["detail"]["current_version"] == first.version
    again = await env.get(A, d.id)
    assert again.filters.queue_ids == [Q1] and again.version == first.version


async def test_filters_read_only_viewer_403_invisible_404(env: Env3) -> None:
    d = await env.create_personal(owner=A)
    env.share(d.id, GROUP_2)  # B has ``ro`` on GROUP_2
    forbidden = await env.patch_filters_raw(B, d.id, {"queue_ids": [Q2]}, version=d.version)
    assert forbidden.status_code == 403
    assert (await env.get(A, d.id)).filters == DashboardFilters()

    private = await env.create_personal(owner=A, name="dash3 private")
    hidden = await env.patch_filters_raw(B, private.id, {}, version=private.version)
    missing = await env.patch_filters_raw(B, 2**40, {}, version=1)
    assert hidden.status_code == 404 and hidden.json() == missing.json()


async def test_filters_are_validated(env: Env3) -> None:
    d = await env.create_personal(owner=A)
    url = f"/api/v1/dashboards/{d.id}/filters"
    client = env.as_user(A)
    for bad in (
        {"filters": {"queue_ids": list(range(1, 52))}, "version": 1},
        {"filters": {"time_range": "forever"}, "version": 1},
        {"filters": {"queue_ids": [0]}, "version": 1},
        {"filters": {}, "version": 0},
        {"filters": {"nope": 1}, "version": 1},
        {"version": 1},
    ):
        assert (await client.patch(url, json=bad)).status_code == 422, bad
    assert (await env.get(A, d.id)).version == d.version


async def test_filters_on_a_template_need_the_admin(env: Env3) -> None:
    created = await env.as_user(ADMIN).post(
        "/api/v1/admin/dashboard-templates",
        json={"name": "dash3 tpl", "role_ids": [ROLE_A], "locked": True},
    )
    assert created.status_code in (200, 201), created.text
    tpl = created.json()
    ok = await env.patch_filters(ADMIN, tpl["id"], {"queue_ids": [Q1]}, version=tpl["version"])
    assert ok.filters.queue_ids == [Q1]
    viewer = await env.patch_filters_raw(A, tpl["id"], {}, version=ok.version)
    assert viewer.status_code == 403
    assert (await env.get(A, tpl["id"])).filters.queue_ids == [Q1]  # locked: visible to the role


async def test_put_without_filters_keeps_them_put_with_filters_replaces(env: Env3) -> None:
    d = await env.create_personal(owner=A)
    d = await env.patch_filters(A, d.id, {"queue_ids": [Q1], "time_range": "7d"}, version=d.version)
    client = env.as_user(A)
    kept = await client.put(f"/api/v1/dashboards/{d.id}", json=_put_body(d))
    assert kept.status_code == 200
    assert kept.json()["filters"] == {"queue_ids": [Q1], "time_range": "7d"}
    again = DashboardOut.model_validate(kept.json())
    replaced = await client.put(
        f"/api/v1/dashboards/{d.id}",
        json=_put_body(again, filters={"queue_ids": [Q2], "time_range": None}),
    )
    assert replaced.status_code == 200
    assert replaced.json()["filters"] == {"queue_ids": [Q2], "time_range": None}
    assert replaced.json()["version"] == again.version + 1
    cleared = await client.put(
        f"/api/v1/dashboards/{d.id}",
        json=_put_body(DashboardOut.model_validate(replaced.json()), filters={}),
    )
    assert cleared.json()["filters"] == {"queue_ids": [], "time_range": None}


async def test_pinned_round_trips_through_put_and_copy(env: Env3) -> None:
    d = await env.create_personal(owner=A)
    client = env.as_user(A)
    r = await client.put(
        f"/api/v1/dashboards/{d.id}",
        json=_put_body(d, widgets=[_w(1, pinned=True), _w(2)]),
    )
    assert r.status_code == 200, r.text
    assert [w["pinned"] for w in r.json()["widgets"]] == [True, False]
    assert [w.pinned for w in (await env.get(A, d.id)).widgets] == [True, False]
    saved = DashboardOut.model_validate(r.json())
    await env.patch_filters(A, d.id, {"queue_ids": [Q1]}, version=saved.version)

    copied = await client.post("/api/v1/dashboards", json={"name": "dash3 copy", "copy_from": d.id})
    assert copied.status_code == 201
    assert [w["pinned"] for w in copied.json()["widgets"]] == [True, False]
    assert copied.json()["filters"] == {"queue_ids": [Q1], "time_range": None}


async def test_template_copy_on_first_visit_keeps_pinned_and_filters(env: Env3) -> None:
    tpl = await env.as_user(ADMIN).post(
        "/api/v1/admin/dashboard-templates",
        json={"name": "dash3 start", "role_ids": [ROLE_A], "locked": False},
    )
    tid, ver = tpl.json()["id"], tpl.json()["version"]
    body = {
        "name": "dash3 start",
        "icon": None,
        "version": ver,
        "widgets": [_w(1, pinned=True)],
        "filters": {"queue_ids": [Q1], "time_range": "90d"},
    }
    saved = await env.as_user(ADMIN).put(f"/api/v1/dashboards/{tid}", json=body)
    assert saved.status_code == 200, saved.text
    out, created = await env.run(start.ensure_start, A)
    assert created is True and out.source_template_id == tid
    assert out.filters == DashboardFilters(queue_ids=[Q1], time_range="90d")
    assert [w.pinned for w in out.widgets] == [True]


async def test_broken_filters_json_reads_as_empty(env: Env3) -> None:
    d = await env.create_personal(owner=A)
    for raw in ('{"queue_ids": "x"}', "not json", "[]", ""):
        env.set_stored_filters(d.id, raw)
        r = await env.as_user(A).get(f"/api/v1/dashboards/{d.id}")
        assert r.status_code == 200, raw
        assert r.json()["filters"] == {"queue_ids": [], "time_range": None}
        assert (await env.get(A, d.id)).filters == DashboardFilters()
    # a broken value is replaced by the next valid write
    out = await env.patch_filters(A, d.id, {"queue_ids": [Q2]}, version=d.version)
    assert out.filters.queue_ids == [Q2]


async def test_preview_accepts_filters(env: Env3) -> None:
    url = "/api/v1/dashboard-widget-types/core.counter/data"
    settings = {"source": {"kind": "preset", "preset": "mine_open"}}
    client = env.as_user(A)
    r = await client.post(
        url, json={"settings": settings, "filters": {"queue_ids": [Q1], "time_range": "30d"}}
    )
    assert r.status_code == 200 and r.json()["status"] == "ok"
    bad = await client.post(url, json={"settings": settings, "filters": {"queue_ids": [0]}})
    assert bad.status_code == 422


def test_widget_in_pinned_defaults_false_and_save_in_filters_optional() -> None:
    assert WidgetIn.model_validate(_w(1)).pinned is False
    raw = _w(1)
    del raw["pinned"]
    assert WidgetIn.model_validate(raw).pinned is False
    save = DashboardSaveIn(name="x", version=1, widgets=[])
    assert save.filters is None
    assert WidgetPreviewIn().filters is None
    assert DashboardCreateIn(name="x").copy_from is None
    body = DashboardFiltersIn(filters=DashboardFilters(queue_ids=[3, 1, 3]), version=2)
    assert body.filters.queue_ids == [1, 3]
    with pytest.raises(ValidationError):
        DashboardFiltersIn.model_validate({"filters": {}, "version": 1, "extra": 1})


async def test_store_set_filters_directly(env: Env3) -> None:
    d = await env.create_personal(owner=A)
    out = await env.run(
        store.set_filters, d.id, A, DashboardFilters(queue_ids=[Q1]), d.version, admin=False
    )
    assert out.version == d.version + 1 and out.filters.queue_ids == [Q1]
    with pytest.raises(store.DashboardConflict) as exc:
        await env.run(store.set_filters, d.id, A, DashboardFilters(), d.version, admin=False)
    assert exc.value.current_version == out.version
