"""Saved-search and own-filter sources of ticket lists and counters (Task 17, both dialects).

World of ``_dashboard_helpers`` (band 49710-49749, dynamic field 49910): A sees QUEUE_1, B sees
QUEUE_2. Providers must compute as the VIEWER; the save path checks what the saver can see.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests._dashboard_helpers import (
    GROUP_2,
    QUEUE_1,
    TICKETS,
    USER_A,
    USER_B,
    Env,
    open_env,
)
from tiqora.dashboard.providers import DASHBOARD_PROVIDERS, core_providers
from tiqora.dashboard.runner import run_widget
from tiqora.dashboard.settings_models import validate_widget_settings
from tiqora.dashboard.store import validate_source_settings
from tiqora.domain.ticket_service import TicketService
from tiqora.extensions.registry import Registry

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


def queue_filter(queue_id: int) -> dict[str, Any]:
    return {"version": 1, "where": {"field": "queue_id", "op": "eq", "value": queue_id}}


def saved(sid: int) -> dict[str, Any]:
    return {"kind": "saved_search", "id": sid}


def own_open_filter(uid: int) -> dict[str, Any]:
    return {
        "version": 1,
        "where": {
            "all": [
                {"field": "owner_id", "op": "eq", "value": uid},
                {"field": "state_type", "op": "eq", "value": "open"},
            ]
        },
    }


def listing(source: dict[str, Any], **extra: Any) -> W:
    return W("t", "core.ticket-list", settings={"source": source, **extra})


def counter(source: dict[str, Any]) -> W:
    return W("c", "core.counter", settings={"source": source})


async def run(env: Env, uid: int, widget: W) -> Any:
    out = await run_widget(env.factory, registry(), uid, widget)
    assert out is not None
    return out


def widget_json(source: dict[str, Any], type_key: str = "core.ticket-list") -> dict[str, Any]:
    return {
        "id": "src00000001",
        "type_key": type_key,
        "type_version": 1,
        "title_override": None,
        "settings": {"source": source},
        "layout": {"lg": {"x": 0, "y": 0, "w": 4, "h": 3}},
    }


async def save_body_with(env: Env, uid: int, source: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    d = await env.make_personal(uid, name="dash497 sources")
    body = {"name": d.name, "icon": d.icon, "version": d.version, "widgets": [widget_json(source)]}
    return f"/api/v1/dashboards/{d.id}", body


async def test_ticket_list_by_saved_search_runs_in_viewer_scope(env: Env) -> None:
    # shared with B's group, but B cannot read QUEUE_1: B's tile must stay empty
    sid = await env.make_saved_search(
        USER_A, criteria=queue_filter(QUEUE_1), shared_groups=[GROUP_2]
    )
    theirs = await run(env, USER_B, listing(saved(sid)))
    assert theirs.status == "ok"
    assert theirs.data["items"] == [] and theirs.data["total"] == 0
    mine = await run(env, USER_A, listing(saved(sid)))
    assert mine.status == "ok"
    assert TICKETS["a_open"] in [i["id"] for i in mine.data["items"]]
    assert TICKETS["b_open"] not in [i["id"] for i in mine.data["items"]]


async def test_saved_search_counter_runs_in_viewer_scope(env: Env) -> None:
    sid = await env.make_saved_search(
        USER_A, criteria=queue_filter(QUEUE_1), shared_groups=[GROUP_2]
    )
    assert (await run(env, USER_B, counter(saved(sid)))).data["value"] == 0
    assert (await run(env, USER_A, counter(saved(sid)))).data["value"] == 5


async def test_deleted_or_unshared_saved_search_is_a_forbidden_tile_with_a_message(
    env: Env,
) -> None:
    private = await env.make_saved_search(USER_A, criteria=queue_filter(QUEUE_1))
    msg = "Die Suche gibt es nicht mehr oder ist für Sie nicht freigegeben."
    for widget in (listing(saved(private)), counter(saved(private))):
        out = await run(env, USER_B, widget)
        assert out.status == "forbidden" and out.error == msg
    gone = await run(env, USER_A, listing(saved(private + 100000)))
    assert gone.status == "forbidden" and gone.error == msg


async def test_broken_saved_search_criteria_is_a_message_not_a_500(env: Env) -> None:
    sid = await env.make_saved_search(USER_A, criteria_raw='{"all": "kaputt"}')
    out = await run(env, USER_A, listing(saved(sid)))
    assert out.status == "forbidden" and out.error == "Diese Suche kann nicht mehr gelesen werden."


async def test_inline_filter_list_and_counter_agree(env: Env) -> None:
    src = {"kind": "filter", "filter": own_open_filter(USER_A)}
    lst = await run(env, USER_A, listing(src, limit=50))
    cnt = await run(env, USER_A, counter(src))
    assert lst.status == "ok" and cnt.status == "ok"
    assert cnt.data["value"] == lst.data["total"] == 2
    assert {i["id"] for i in lst.data["items"]} == {TICKETS["a_open"], TICKETS["a_escalated"]}


async def test_inline_filter_cannot_widen_the_viewers_scope(env: Env) -> None:
    src = {"kind": "filter", "filter": queue_filter(QUEUE_1)}
    out = await run(env, USER_B, listing(src))
    assert out.status == "ok" and out.data["total"] == 0 and out.data["items"] == []


async def test_field_removed_after_saving_is_reported_not_raised(env: Env) -> None:
    env.make_dynamic_field()
    flt = {"version": 1, "where": {"field": "df.dash497Df", "op": "eq", "value": "x"}}
    src = {"kind": "filter", "filter": flt}
    async with env.session() as s:
        await validate_source_settings(
            s, USER_A, "core.ticket-list", {"source": src}, path="settings"
        )
    assert (await run(env, USER_A, listing(src))).status == "ok"  # valid while the field is
    env.invalidate_dynamic_field()
    for widget in (listing(src), counter(src)):
        out = await run(env, USER_A, widget)
        assert out.status == "forbidden" and out.error == "Der Filter ist nicht mehr gültig."


async def test_save_rejects_an_invalid_inline_filter_with_a_path(env: Env) -> None:
    bad = {"version": 1, "where": {"field": "nope", "op": "eq", "value": 1}}
    url, body = await save_body_with(env, USER_A, {"kind": "filter", "filter": bad})
    r = await env.as_user(USER_A).put(url, json=body)
    assert r.status_code == 422
    assert r.json()["detail"]["path"].startswith("widgets[0].settings.source.filter")
    malformed = {"kind": "filter", "filter": {"all": "kaputt"}}
    r = await env.as_user(USER_A).put(url, json={**body, "widgets": [widget_json(malformed)]})
    assert r.status_code == 422
    assert r.json()["detail"]["path"].startswith("widgets[0].settings.source.filter")


async def test_save_accepts_a_valid_filter_and_a_visible_search(env: Env) -> None:
    sid = await env.make_saved_search(USER_A, criteria=queue_filter(QUEUE_1))
    url, body = await save_body_with(env, USER_A, saved(sid))
    r = await env.as_user(USER_A).put(url, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["widgets"][0]["settings"]["source"] == saved(sid)
    flt = {"kind": "filter", "filter": own_open_filter(USER_A)}
    r = await env.as_user(USER_A).put(
        url, json={**body, "version": r.json()["version"], "widgets": [widget_json(flt)]}
    )
    assert r.status_code == 200, r.text


async def test_save_rejects_a_saved_search_the_saver_cannot_see(env: Env) -> None:
    sid = await env.make_saved_search(USER_A, criteria=queue_filter(QUEUE_1))  # private to A
    url, body = await save_body_with(env, USER_B, saved(sid))
    r = await env.as_user(USER_B).put(url, json=body)
    assert r.status_code == 422
    assert r.json()["detail"]["path"] == "widgets[0].settings.source.id"
    assert r.json()["detail"]["message"] == "Die gespeicherte Suche gibt es nicht."


async def test_patch_checks_the_source_too(env: Env) -> None:
    sid = await env.make_saved_search(USER_A, criteria=queue_filter(QUEUE_1))  # private to A
    url, body = await save_body_with(env, USER_B, {"kind": "preset", "preset": "mine_open"})
    saved_ok = await env.as_user(USER_B).put(url, json=body)
    assert saved_ok.status_code == 200, saved_ok.text
    r = await env.as_user(USER_B).patch(
        f"{url}/widgets/src00000001/settings",
        json={"settings": {"source": saved(sid)}, "version": saved_ok.json()["version"]},
    )
    assert r.status_code == 422 and r.json()["detail"]["path"] == "settings.source.id"


async def test_counter_history_key_for_searches(env: Env) -> None:
    sid = await env.make_saved_search(USER_A, criteria=queue_filter(QUEUE_1))
    await run(env, USER_A, counter(saved(sid)))
    await run(env, USER_A, counter({"kind": "filter", "filter": queue_filter(QUEUE_1)}))
    keys = await env.sample_keys(USER_A)
    assert f"search:{sid}" in keys
    assert any(re.fullmatch(r"filter:[0-9a-f]{16}", k) for k in keys)


async def test_presets_still_work_after_the_union_is_widened(env: Env) -> None:
    got = validate_widget_settings(
        "core.ticket-list", {"source": {"kind": "preset", "preset": "mine_new"}}
    )
    assert got["source"] == {"kind": "preset", "preset": "mine_new"}
    assert validate_widget_settings("core.counter", {})["source"]["kind"] == "preset"
    out = await run(env, USER_A, counter({"kind": "preset", "preset": "mine_open"}))
    async with env.session() as s:
        assert (
            out.data["value"] == (await TicketService(s).count_dashboard_summary(USER_A))["my_open"]
        )
