"""HTTP tests for ``/api/v1/dashboards*`` and ``/api/v1/dashboard-widget-types`` (both dialects).

Seed band 49710-49749 (see ``_dashboard_helpers``; names contain ``dash497``); every row is
removed again. ``env.client`` acts as the user chosen with ``env.as_user`` (default USER_A).
The data routes belong to Task 5, the share route to Task 16, the admin routes to Task 7.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests._dashboard_helpers import ROLE_1, USER_A, USER_B, USER_C, Env, open_env

pytestmark = pytest.mark.db

NOT_FOUND = {"detail": "Dashboard nicht gefunden."}


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    async with open_env(request.getfixturevalue(request.param)) as e:
        yield e


def _w(i: int, type_key: str = "core.notes", settings: dict[str, Any] | None = None) -> dict:
    return {
        "id": f"wid{i:07d}",
        "type_key": type_key,
        "type_version": 1,
        "title_override": None,
        "settings": settings if settings is not None else {"markdown": ""},
        "layout": {"lg": {"x": 0, "y": i, "w": 4, "h": 2}},
    }


def _save_body(d: dict[str, Any], **over: Any) -> dict[str, Any]:
    return {
        "name": d["name"],
        "icon": d["icon"],
        "version": d["version"],
        "widgets": d["widgets"],
        **over,
    }


async def _start(env: Env) -> dict[str, Any]:
    r = await env.client.post("/api/v1/dashboards/ensure-start")
    assert r.status_code == 201
    return r.json()  # type: ignore[no-any-return]


async def test_list_requires_login_and_returns_tabs(env: Env) -> None:
    assert (await env.anon.get("/api/v1/dashboards")).status_code == 401
    r = await env.client.get("/api/v1/dashboards")
    assert r.status_code == 200 and r.json() == []  # nothing until ensure-start
    d = await _start(env)
    tabs = (await env.client.get("/api/v1/dashboards")).json()
    assert [t["id"] for t in tabs] == [d["id"]]
    assert "widgets" not in tabs[0]


async def test_ensure_start_201_then_200_with_widgets(env: Env) -> None:
    first = await env.client.post("/api/v1/dashboards/ensure-start")
    second = await env.client.post("/api/v1/dashboards/ensure-start")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"] and len(first.json()["widgets"]) == 11
    assert first.json()["can_edit"] is True and first.json()["is_owner"] is True
    assert (await env.anon.post("/api/v1/dashboards/ensure-start")).status_code == 401


async def test_put_roundtrip_and_409(env: Env) -> None:
    d = await _start(env)
    body = _save_body(d, name="dash497 Neu", icon=None, widgets=d["widgets"][:2])
    url = f"/api/v1/dashboards/{d['id']}"
    ok = await env.client.put(url, json=body)
    assert ok.status_code == 200 and ok.json()["version"] == d["version"] + 1
    assert len(ok.json()["widgets"]) == 2 and ok.json()["name"] == "dash497 Neu"
    stale = await env.client.put(url, json=body)
    assert (
        stale.status_code == 409 and stale.json()["detail"]["current_version"] == d["version"] + 1
    )
    assert stale.json()["detail"]["message"] == "Dieses Dashboard wurde inzwischen geändert."


async def test_put_422_has_german_message_and_path(env: Env) -> None:
    d = await _start(env)
    bad_widget = _w(1, "core.ticket-list", {"limit": 99})
    r = await env.client.put(
        f"/api/v1/dashboards/{d['id']}", json=_save_body(d, widgets=[bad_widget])
    )
    detail = r.json()["detail"]
    assert r.status_code == 422 and detail["path"] == "widgets[0].settings.limit"
    assert detail["message"] == "Der Wert muss zwischen 1 und 50 liegen."


async def test_put_rejects_extra_fields_and_too_many_widgets(env: Env) -> None:
    d = await _start(env)
    url = f"/api/v1/dashboards/{d['id']}"
    body = _save_body(d)
    assert (await env.client.put(url, json={**body, "owner_user_id": 1})).status_code == 422
    many = [_w(i) for i in range(41)]
    assert (await env.client.put(url, json={**body, "widgets": many})).status_code == 422
    dup = [_w(1), _w(1)]
    assert (await env.client.put(url, json={**body, "widgets": dup})).status_code == 422
    # nothing was saved by the rejected requests
    assert (await env.client.get(url)).json()["version"] == d["version"]


async def test_invisible_dashboard_is_404_on_every_route(env: Env) -> None:
    d = await _start(env)  # owned by USER_A
    wid = d["widgets"][0]["id"]
    env.as_user(USER_C)
    calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("get", "", None),
        ("put", "", _save_body(d)),
        ("delete", "", None),
        (
            "patch",
            f"/widgets/{wid}/settings",
            {"settings": d["widgets"][0]["settings"], "version": d["version"]},
        ),
    ]
    for method, path, payload in calls:
        kw = {"json": payload} if payload is not None else {}
        r = await getattr(env.client, method)(f"/api/v1/dashboards/{d['id']}{path}", **kw)
        gone = await getattr(env.client, method)(f"/api/v1/dashboards/987654321{path}", **kw)
        assert r.status_code == 404, (method, r.status_code)
        assert r.json() == gone.json() == NOT_FOUND, method
    cp = await env.client.post(
        "/api/v1/dashboards", json={"name": "dash497 x", "copy_from": d["id"]}
    )
    assert cp.status_code == 404 and cp.json() == NOT_FOUND
    env.as_user(USER_A)
    assert (await env.client.get(f"/api/v1/dashboards/{d['id']}")).status_code == 200


async def test_locked_template_is_403_on_put_and_delete_for_a_role_member(env: Env) -> None:
    tpl = await env.make_template(name="dash497 Pflicht", role_ids=[ROLE_1], locked=True)
    env.as_user(USER_A)
    got = (await env.client.get(f"/api/v1/dashboards/{tpl.id}")).json()
    assert got["can_edit"] is False and got["locked"] is True
    put = await env.client.put(f"/api/v1/dashboards/{tpl.id}", json=_save_body(got, name="x"))
    assert (
        put.status_code == 403
        and put.json()["detail"] == "Dieses Dashboard kannst du nicht bearbeiten."
    )
    assert (await env.client.delete(f"/api/v1/dashboards/{tpl.id}")).status_code == 403
    assert (await env.client.get(f"/api/v1/dashboards/{tpl.id}")).json()[
        "name"
    ] == "dash497 Pflicht"


async def test_save_keeps_unknown_widget_types(env: Env) -> None:
    d = (await env.client.post("/api/v1/dashboards", json={"name": "dash497 plug"})).json()
    weather = {
        "id": "wwwwwwww",
        "type_key": "org.example.weather",
        "type_version": 3,
        "title_override": None,
        "settings": {"city": "Bonn"},
        "layout": {"lg": {"x": 0, "y": 0, "w": 4, "h": 2}},
    }
    url = f"/api/v1/dashboards/{d['id']}"
    first = await env.client.put(url, json=_save_body(d, widgets=[weather]))
    w0 = first.json()["widgets"][0]
    assert first.status_code == 200 and w0["settings"] == {"city": "Bonn"}
    assert w0["type_version"] == 3
    got = (await env.client.get(url)).json()
    again = await env.client.put(url, json=_save_body(got))
    assert again.status_code == 200
    assert again.json()["widgets"][0]["type_key"] == "org.example.weather"
    assert again.json()["widgets"][0]["settings"] == {"city": "Bonn"}


async def test_patch_settings_edits_one_note(env: Env) -> None:
    d = (await env.client.post("/api/v1/dashboards", json={"name": "dash497 note"})).json()
    note = _w(1, "core.notes", {"markdown": ""}) | {"id": "nnnnnnnn"}
    url = f"/api/v1/dashboards/{d['id']}"
    d = (await env.client.put(url, json=_save_body(d, widgets=[note]))).json()
    purl = f"{url}/widgets/nnnnnnnn/settings"
    ok = await env.client.patch(
        purl, json={"settings": {"markdown": "Hallo"}, "version": d["version"]}
    )
    assert ok.status_code == 200 and ok.json()["settings"] == {"markdown": "Hallo"}
    assert (await env.client.get(url)).json()["version"] == d["version"] + 1
    bad = await env.client.patch(
        purl, json={"settings": {"markdown": "a" * 6000}, "version": d["version"] + 1}
    )
    assert bad.status_code == 422 and bad.json()["detail"]["path"] == "settings.markdown"
    stale = await env.client.patch(
        purl, json={"settings": {"markdown": "x"}, "version": d["version"]}
    )
    assert (
        stale.status_code == 409 and stale.json()["detail"]["current_version"] == d["version"] + 1
    )
    unknown = await env.client.patch(
        f"{url}/widgets/zzzzzzzz/settings", json={"settings": {}, "version": d["version"] + 1}
    )
    assert unknown.status_code == 404


async def test_order_and_create_copy_and_delete(env: Env) -> None:
    a = (await env.client.post("/api/v1/dashboards", json={"name": "dash497 A"})).json()
    r = await env.client.post(
        "/api/v1/dashboards", json={"name": "dash497 B", "copy_from": a["id"]}
    )
    b = r.json()
    assert r.status_code == 201 and b["id"] != a["id"]
    assert (
        await env.client.put("/api/v1/dashboards/order", json={"ids": [b["id"], a["id"]]})
    ).status_code == 204
    assert [t["id"] for t in (await env.client.get("/api/v1/dashboards")).json()][:2] == [
        b["id"],
        a["id"],
    ]
    assert (await env.client.delete(f"/api/v1/dashboards/{a['id']}")).status_code == 204
    gone = await env.client.get(f"/api/v1/dashboards/{a['id']}")
    assert gone.status_code == 404 and gone.json() == NOT_FOUND


async def test_order_rejects_foreign_and_duplicate_ids_and_last_delete(env: Env) -> None:
    mine = (await env.client.post("/api/v1/dashboards", json={"name": "dash497 solo"})).json()
    foreign = await env.client.put(
        "/api/v1/dashboards/order", json={"ids": [mine["id"], env.foreign_dashboard_id]}
    )
    assert foreign.status_code == 403
    dup = await env.client.put("/api/v1/dashboards/order", json={"ids": [mine["id"], mine["id"]]})
    assert dup.status_code == 422 and dup.json()["detail"]["path"] == "ids"
    last = await env.client.delete(f"/api/v1/dashboards/{mine['id']}")
    assert last.status_code == 422
    assert last.json()["detail"] == {
        "message": "Das letzte Dashboard bleibt bestehen.",
        "path": "id",
    }


async def test_other_users_keep_their_own_start(env: Env) -> None:
    a = await _start(env)
    env.as_user(USER_B)
    b = await _start(env)
    assert a["id"] != b["id"]
    assert [t["id"] for t in (await env.client.get("/api/v1/dashboards")).json()] == [b["id"]]


async def test_widget_types_lists_ten_core_keys(env: Env) -> None:
    r = await env.client.get("/api/v1/dashboard-widget-types")
    assert r.status_code == 200
    types = r.json()
    keys = [t["key"] for t in types]
    assert keys == sorted(keys) and len(keys) == 10 and "core.ticket-list" in keys
    assert all(t["version"] == 1 and isinstance(t["has_data"], bool) for t in types)
    assert (await env.anon.get("/api/v1/dashboard-widget-types")).status_code == 401


async def test_openapi_has_no_schema_name_clash(env: Env) -> None:
    schemas = (await env.client.get("/openapi.json")).json()["components"]["schemas"]
    # the ticket KPI model keeps its name next to the new dashboard models
    assert {"DashboardOut", "DashboardTabOut", "DashboardSummary"} <= set(schemas)
    assert "widgets" not in schemas["DashboardSummary"]["properties"]
    assert "widgets" in schemas["DashboardOut"]["properties"]


async def test_cookie_writes_need_a_same_origin_header(env: Env) -> None:
    """The global CSRF middleware covers the new unsafe routes (no per-route duplicate)."""
    d = await _start(env)
    cookies = {"tiqora_session": "x"}
    forged = await env.client.put(
        f"/api/v1/dashboards/{d['id']}",
        json=_save_body(d),
        cookies=cookies,
        headers={"Origin": "https://evil.example.org"},
    )
    assert forged.status_code == 403 and forged.json() == {"detail": "CSRF origin check failed"}
    same = await env.client.put(
        f"/api/v1/dashboards/{d['id']}",
        json=_save_body(d),
        cookies=cookies,
        headers={"Origin": "http://test"},
    )
    assert same.status_code == 200
