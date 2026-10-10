"""HTTP tests for ``/api/v1/admin/dashboard-templates`` (both dialects).

Seed bands: 49710-49749 via ``_dashboard_helpers`` (names contain ``dash497``) plus the extra
valid role 49870 (own band 49870-49889), removed again by the fixture.
``env.admin`` is the Znuny admin, ``env.as_user(...)`` an ordinary agent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests._dashboard_helpers import ROLE_1, ROLE_2, USER_A, USER_B, Env, open_env
from tiqora.domain.api_key_scopes import path_to_area, scopes_allow

pytestmark = pytest.mark.db

URL = "/api/v1/admin/dashboard-templates"
ROLE_EXTRA = 49870  # a second VALID role (ROLE_2 of the world is invalid)


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    async with open_env(request.getfixturevalue(request.param)) as e:
        e._sql_write("DELETE FROM roles WHERE id = :i", i=ROLE_EXTRA)
        e._sql_write(
            "INSERT INTO roles (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:i, 'dash497-role-extra', 1, :t, 1, :t, 1)",
            i=ROLE_EXTRA,
            t=e.now,
        )
        try:
            yield e
        finally:
            e._sql_write("DELETE FROM roles WHERE id = :i", i=ROLE_EXTRA)


def _props(name: str = "dash497 t", **over: Any) -> dict[str, Any]:
    return {"name": name, "role_ids": [ROLE_1], "locked": False, "sort": 0, **over}


async def _create(env: Env, **over: Any) -> dict[str, Any]:
    r = await env.admin.post(URL, json=_props(**over))
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_templates_are_admin_only(env: Env) -> None:
    tpl = await _create(env)
    user = env.as_user(USER_A)
    for method, url, body in (
        ("GET", URL, None),
        ("POST", URL, _props()),
        ("PUT", f"{URL}/{tpl['id']}", _props()),
        ("DELETE", f"{URL}/{tpl['id']}", None),
    ):
        r = await user.request(method, url, json=body)
        assert r.status_code == 403, (method, r.text)
    assert (await env.anon.get(URL)).status_code == 401
    # nothing happened
    assert await env.count_rows("tiqora_dashboard", id=tpl["id"]) == 1


async def test_create_template_starts_with_default_widgets_and_roles(env: Env) -> None:
    r = await env.admin.post(URL, json=_props("dash497 Support"))
    assert r.status_code == 201
    body = r.json()
    assert body["widget_count"] == 11 and body["role_ids"] == [ROLE_1]
    assert body["kind"] == "template" and body["version"] == 1
    assert await env.count_rows("tiqora_dashboard_widget", dashboard_id=body["id"]) == 11
    listed = (await env.admin.get(URL)).json()
    assert [t["id"] for t in listed if t["name"] == "dash497 Support"] == [body["id"]]


async def test_list_is_ordered_by_sort_then_id(env: Env) -> None:
    b = await _create(env, name="dash497 b", sort=5)
    a = await _create(env, name="dash497 a", sort=1)
    c = await _create(env, name="dash497 c", sort=5)
    ours = [t["id"] for t in (await env.admin.get(URL)).json() if t["name"].startswith("dash497")]
    assert ours == [a["id"], b["id"], c["id"]]


async def test_unknown_role_is_422(env: Env) -> None:
    r = await env.admin.post(URL, json=_props("dash497 x", role_ids=[999999]))
    assert r.status_code == 422
    assert r.json()["detail"]["path"] == "role_ids"
    assert "gibt es nicht" in r.json()["detail"]["message"]
    assert await env.count_rows("tiqora_dashboard", name="dash497 x") == 0


async def test_invalid_role_is_422_on_create_and_update(env: Env) -> None:
    r = await env.admin.post(URL, json=_props("dash497 y", role_ids=[ROLE_2]))
    assert r.status_code == 422 and r.json()["detail"]["path"] == "role_ids"
    tpl = await _create(env)
    r = await env.admin.put(
        f"{URL}/{tpl['id']}", json=_props(role_ids=[ROLE_1, ROLE_2]), params={"version": 1}
    )
    assert r.status_code == 422 and r.json()["detail"]["path"] == "role_ids"
    got = (await env.admin.get(URL)).json()
    assert next(t for t in got if t["id"] == tpl["id"])["role_ids"] == [ROLE_1]


async def test_update_properties_and_delete_cascades(env: Env) -> None:
    tpl = await _create(env)
    upd = await env.admin.put(
        f"{URL}/{tpl['id']}",
        json=_props("dash497 t2", role_ids=[ROLE_1, ROLE_EXTRA], locked=True, sort=3),
        params={"version": 1},
    )
    assert upd.status_code == 200
    out = upd.json()
    assert (out["name"], out["locked"], out["sort"]) == ("dash497 t2", True, 3)
    assert sorted(out["role_ids"]) == [ROLE_1, ROLE_EXTRA]
    assert out["version"] == 2 and out["widget_count"] == 11

    copy = await env.make_copy_of_template(USER_A, tpl["id"])
    assert await env.count_rows("tiqora_dashboard_widget", dashboard_id=tpl["id"]) == 11
    assert await env.count_rows("tiqora_dashboard_role", dashboard_id=tpl["id"]) == 2
    assert (await env.admin.delete(f"{URL}/{tpl['id']}")).status_code == 204
    assert await env.count_rows("tiqora_dashboard", id=tpl["id"]) == 0
    assert await env.count_rows("tiqora_dashboard_widget", dashboard_id=tpl["id"]) == 0
    assert await env.count_rows("tiqora_dashboard_role", dashboard_id=tpl["id"]) == 0
    survivor = await env.get(copy.id, USER_A)  # the copy lives on, with its widgets
    assert survivor.source_template_id == tpl["id"] and len(survivor.widgets) == 11
    assert (await env.admin.delete(f"{URL}/{tpl['id']}")).status_code == 404


async def test_update_unknown_or_non_template_is_404(env: Env) -> None:
    assert (
        await env.admin.put(f"{URL}/99999999", json=_props(), params={"version": 1})
    ).status_code == 404
    # a personal dashboard is not a template, even for an admin
    assert (
        await env.admin.put(
            f"{URL}/{env.foreign_dashboard_id}", json=_props(), params={"version": 1}
        )
    ).status_code == 404
    assert (await env.admin.delete(f"{URL}/{env.foreign_dashboard_id}")).status_code == 404
    assert await env.count_rows("tiqora_dashboard", id=env.foreign_dashboard_id) == 1


async def test_update_with_stale_version_is_409(env: Env) -> None:
    tpl = await _create(env)
    ok = await env.admin.put(f"{URL}/{tpl['id']}", json=_props("dash497 n1"), params={"version": 1})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = await env.admin.put(
        f"{URL}/{tpl['id']}", json=_props("dash497 n2"), params={"version": 1}
    )
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2
    listed = (await env.admin.get(URL)).json()
    assert next(t for t in listed if t["id"] == tpl["id"])["name"] == "dash497 n1"


async def test_admin_saves_template_layout_through_dashboards_put(env: Env) -> None:
    tpl = await _create(env)
    got = (await env.admin.get(f"/api/v1/dashboards/{tpl['id']}")).json()  # unlocked: by id
    ok = await env.admin.put(
        f"/api/v1/dashboards/{tpl['id']}",
        json={
            "name": got["name"],
            "icon": None,
            "version": got["version"],
            "widgets": got["widgets"][:3],
        },
    )
    assert ok.status_code == 200 and len(ok.json()["widgets"]) == 3
    listed = (await env.admin.get(URL)).json()
    assert next(t for t in listed if t["id"] == tpl["id"])["widget_count"] == 3


async def test_locking_a_template_makes_it_a_read_only_tab_for_role_members(env: Env) -> None:
    tpl = await _create(env)
    member = env.as_user(USER_A)
    tabs = (await member.get("/api/v1/dashboards")).json()
    assert [t for t in tabs if t["kind"] == "template"] == []  # unlocked: not a tab
    assert (await member.get(f"/api/v1/dashboards/{tpl['id']}")).status_code == 404

    r = await env.admin.put(f"{URL}/{tpl['id']}", json=_props(locked=True), params={"version": 1})
    assert r.status_code == 200
    tabs = (await member.get("/api/v1/dashboards")).json()
    assert [t["id"] for t in tabs if t["kind"] == "template"] == [tpl["id"]]
    assert next(t for t in tabs if t["id"] == tpl["id"])["can_edit"] is False

    got = (await member.get(f"/api/v1/dashboards/{tpl['id']}")).json()
    body = {"name": "hijack", "icon": None, "version": got["version"], "widgets": got["widgets"]}
    assert (await member.put(f"/api/v1/dashboards/{tpl['id']}", json=body)).status_code == 403
    assert (await member.delete(f"/api/v1/dashboards/{tpl['id']}")).status_code == 403
    assert await env.count_rows("tiqora_dashboard", id=tpl["id"], name=tpl["name"]) == 1


async def test_template_reaches_only_current_role_members(env: Env) -> None:
    tpl = await _create(env, locked=True)
    other = env.as_user(USER_B)  # B is no member of ROLE_1
    assert [
        t for t in (await other.get("/api/v1/dashboards")).json() if t["kind"] == "template"
    ] == []
    assert (await other.get(f"/api/v1/dashboards/{tpl['id']}")).status_code == 404
    # moving the template to another role takes it away from A
    await env.admin.put(
        f"{URL}/{tpl['id']}", json=_props(locked=True, role_ids=[ROLE_EXTRA]), params={"version": 1}
    )
    mine = env.as_user(USER_A)
    assert [
        t for t in (await mine.get("/api/v1/dashboards")).json() if t["kind"] == "template"
    ] == []


async def test_admin_api_key_area(env: Env) -> None:
    assert path_to_area(URL) == "admin" and path_to_area(f"{URL}/1") == "admin"
    assert not scopes_allow(frozenset({"tickets:rw"}), method="GET", path=URL)
    assert not scopes_allow(frozenset({"admin:ro"}), method="POST", path=URL)
    assert scopes_allow(frozenset({"admin:rw"}), method="POST", path=URL)


async def test_update_without_version_is_422_and_changes_nothing(env: Env) -> None:
    tpl = await _create(env)
    r = await env.admin.put(f"{URL}/{tpl['id']}", json=_props("dash497 nope"))
    assert r.status_code == 422
    assert await env.count_rows("tiqora_dashboard", id=tpl["id"], name=tpl["name"]) == 1
    assert await env.count_rows("tiqora_dashboard", id=tpl["id"], version=1) == 1


async def test_list_reports_roles_and_widget_counts_per_template(env: Env) -> None:
    a = await _create(env, name="dash497 la", role_ids=[ROLE_1, ROLE_EXTRA], sort=1)
    b = await _create(env, name="dash497 lb", role_ids=[], sort=2)
    got = {t["id"]: t for t in (await env.admin.get(URL)).json()}
    assert sorted(got[a["id"]]["role_ids"]) == [ROLE_1, ROLE_EXTRA]
    assert got[b["id"]]["role_ids"] == [] and got[b["id"]]["widget_count"] == 11
