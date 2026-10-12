"""HTTP tests for the public ``/api/v1/kiosk`` wallboard API (both dialects, band 61470-61519).

The world (users, queues, cleanup of ``tiqora_dashboard_kiosk_*``) is ``_dashboard3_helpers``;
Redis is the shared in-memory ``tests/_fake_redis.FakeRedis`` (pipeline, SET NX, TTL).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from tests._dashboard3_helpers import ADMIN, Env3, open_env3
from tests._fake_redis import FakeRedis
from tiqora.api.deps import get_app_settings
from tiqora.config import Settings
from tiqora.dashboard import kiosk
from tiqora.dashboard.schemas import WidgetDataOut
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

ADMIN_URL = "/api/v1/admin/dashboard-kiosk-links"
BOARD = "/api/v1/kiosk/board"
HDR = "X-Tiqora-Kiosk"
INVALID = {"detail": {"message": "Dieser Wallboard-Link ist ungültig oder abgelaufen."}}


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env3]:
    async with open_env3(request.getfixturevalue(request.param)) as e:
        e.app.state.redis = FakeRedis()
        yield e


def _wid(d: int, n: int) -> str:
    return f"dash3k{d}w{n}"


def _widgets(d: int) -> list[dict[str, Any]]:
    layout = {"lg": {"x": 0, "y": 0, "w": 4, "h": 2}}
    return [
        {
            "id": _wid(d, 1),
            "type_key": "core.counter",
            "type_version": 1,
            "title_override": "Kunde Beispiel GmbH",
            "settings": {"source": {"preset": "mine_open"}, "tone": "danger"},
            "layout": layout,
        },
        {
            "id": _wid(d, 2),
            "type_key": "core.notes",
            "type_version": 1,
            "settings": {"markdown": "Notiz fuer anna@example.org"},
            "layout": {"lg": {"x": 4, "y": 0, "w": 4, "h": 2}},
        },
        {
            "id": _wid(d, 3),
            "type_key": "core.recent-views",
            "type_version": 1,
            "settings": {},
            "layout": {"lg": {"x": 8, "y": 0, "w": 4, "h": 2}},
        },
    ]


async def _board(env: Env3, name: str = "dash3 wall") -> int:
    d = await env.create_personal(owner=ADMIN, name=name)
    r = await env.as_user(ADMIN).put(
        f"/api/v1/dashboards/{d.id}",
        json={"name": name, "icon": None, "version": d.version, "widgets": _widgets(d.id)},
    )
    assert r.status_code == 200, r.text
    return d.id


async def _link(env: Env3, ids: list[int], **over: Any) -> dict[str, Any]:
    r = await env.as_user(ADMIN).post(
        ADMIN_URL, json={"name": "dash3 Lobby", "dashboard_ids": ids, **over}
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _anon(env: Env3, ip: str = "203.0.113.5") -> AsyncClient:
    """A client without session/auth override semantics: the kiosk router ignores them."""
    return AsyncClient(
        transport=ASGITransport(app=env.app, client=(ip, 4711)), base_url="http://test"
    )


def _data(dashboard_id: int) -> str:
    return f"/api/v1/kiosk/dashboards/{dashboard_id}/data"


async def _get(env: Env3, url: str, token: str | None, ip: str = "203.0.113.5") -> Any:
    async with _anon(env, ip) as c:
        return await c.get(url, headers={HDR: token} if token is not None else {})


async def test_board_lists_visible_dashboards_in_order(env: Env3) -> None:
    d1, d2 = await _board(env, "dash3 one"), await _board(env, "dash3 two")
    link = await _link(env, [d2, d1], rotate_seconds=45)
    r = await _get(env, BOARD, link["token"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["id"] for d in body["dashboards"]] == [d2, d1]
    assert body["name"] == "dash3 Lobby" and body["rotate_seconds"] == 45
    assert body["refresh_seconds"] == kiosk.KIOSK_REFRESH_SECONDS
    assert body["show_details"] is False and body["server_time"]
    ids = {w["id"] for w in body["dashboards"][0]["widgets"]}
    assert ids == {_wid(d2, 1), _wid(d2, 2), _wid(d2, 3)}
    # a dashboard the creator can no longer see is skipped, not an error
    env.sql("DELETE FROM tiqora_dashboard_widget WHERE dashboard_id = :d", d=d2)
    env.sql("DELETE FROM tiqora_dashboard WHERE id = :d", d=d2)
    env.sql("DELETE FROM tiqora_dashboard_kiosk_item WHERE dashboard_id = :d", d=d2)
    after = await _get(env, BOARD, link["token"])
    assert [d["id"] for d in after.json()["dashboards"]] == [d1]


async def test_board_widgets_carry_only_whitelisted_configuration(env: Env3) -> None:
    d = await _board(env)
    plain = (await _get(env, BOARD, (await _link(env, [d]))["token"])).json()
    by_id = {w["id"]: w for w in plain["dashboards"][0]["widgets"]}
    counter = by_id[_wid(d, 1)]
    assert counter["settings"] == {"tone": "danger", "preset": "mine_open"}, "preset name only"
    assert counter["title_override"] is None
    assert by_id[_wid(d, 2)]["settings"] == {}, "notes only with details"
    assert "example.org" not in json.dumps(plain) and "Beispiel GmbH" not in json.dumps(plain)
    detailed = (await _get(env, BOARD, (await _link(env, [d], show_details=True))["token"])).json()
    by_id = {w["id"]: w for w in detailed["dashboards"][0]["widgets"]}
    assert by_id[_wid(d, 1)]["title_override"] == "Kunde Beispiel GmbH"
    assert by_id[_wid(d, 1)]["settings"] == {"tone": "danger", "preset": "mine_open"}
    # notes and links never render on a wallboard (recorded deviation): their text never leaves
    assert by_id[_wid(d, 2)]["settings"] == {}
    assert "example.org" not in json.dumps(detailed)


async def test_invalid_states_are_indistinguishable(env: Env3) -> None:
    d = await _board(env)
    unknown = await _get(env, BOARD, "x" * 43)
    assert unknown.status_code == 404 and unknown.json() == INVALID
    ok = await _link(env, [d])
    assert (await _get(env, BOARD, ok["token"])).status_code == 200
    seen: list[tuple[int, Any]] = [(unknown.status_code, unknown.json())]

    async def broken(setup: str, undo: str, **p: Any) -> None:
        link = await _link(env, [d])
        env.sql(setup, i=link["id"], u=ADMIN, **p)
        for url in (BOARD, _data(d)):
            r = await _get(env, url, link["token"])
            seen.append((r.status_code, r.json()))
        env.sql(undo, i=link["id"], u=ADMIN, **p)

    await broken(
        "UPDATE tiqora_dashboard_kiosk_token SET expires_at = :t WHERE id = :i",
        "UPDATE tiqora_dashboard_kiosk_token SET expires_at = :t2 WHERE id = :i",
        t=utc_now() - timedelta(seconds=5),
        t2=utc_now() + timedelta(days=1),
    )
    await broken(
        "UPDATE tiqora_dashboard_kiosk_token SET revoked_at = :t WHERE id = :i",
        "UPDATE tiqora_dashboard_kiosk_token SET revoked_at = NULL WHERE id = :i",
        t=utc_now(),
    )
    await broken(
        "UPDATE users SET valid_id = 2 WHERE id = :u", "UPDATE users SET valid_id = 1 WHERE id = :u"
    )
    # creator is no longer an admin
    rows = env.sql(
        "SELECT group_id, permission_key FROM group_user WHERE user_id = :u"
        " AND group_id = (SELECT id FROM permission_groups WHERE name = 'admin')",
        u=ADMIN,
    )
    assert rows
    token = str((await _link(env, [d]))["token"])
    env.sql(
        "DELETE FROM group_user WHERE user_id = :u"
        " AND group_id = (SELECT id FROM permission_groups WHERE name = 'admin')",
        u=ADMIN,
    )
    try:
        for url in (BOARD, _data(d)):
            r = await _get(env, url, token)
            seen.append((r.status_code, r.json()))
    finally:
        env.sql(
            "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
            " change_time, change_by) VALUES (:u,"
            " (SELECT id FROM permission_groups WHERE name = 'admin'), :k, :t, 1, :t, 1)",
            u=ADMIN,
            k=rows[0][1],
            t=utc_now(),
        )
    # no dashboard visible any more
    gone = await _link(env, [d])
    env.sql("DELETE FROM tiqora_dashboard_kiosk_item WHERE token_id = :i", i=gone["id"])
    for url in (BOARD, _data(d)):
        r = await _get(env, url, gone["token"])
        seen.append((r.status_code, r.json()))
    # missing header
    missing = await _get(env, BOARD, None)
    seen.append((missing.status_code, missing.json()))
    assert len(seen) == 1 + 6 + 2 + 2 + 1
    assert all(item == (404, INVALID) for item in seen), seen


async def test_revoked_link_stops_on_next_poll_despite_cache(env: Env3) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    first = await _get(env, _data(d), link["token"])
    assert first.status_code == 200, first.text
    cached = [k for k in await env.app.state.redis.keys("*") if ":data:" in k]
    assert cached, "the first data call filled the cache"
    revoked = await env.as_user(ADMIN).post(f"{ADMIN_URL}/{link['id']}/revoke")
    assert revoked.status_code == 200
    again = await _get(env, _data(d), link["token"])
    assert again.status_code == 404 and again.json() == INVALID


async def test_data_runs_as_creator_and_never_writes_counter_history(env: Env3) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    r = await _get(env, _data(d), link["token"])
    assert r.status_code == 200, r.text
    widgets = r.json()["widgets"]
    counter = widgets[_wid(d, 1)]
    assert counter["status"] == "ok" and counter["data"]["value"] == 0
    assert _wid(d, 2) not in widgets, "notes render from settings"
    assert widgets[_wid(d, 3)] == {
        "status": "forbidden",
        "data": None,
        "error": kiosk.MSG_HIDDEN,
    }
    rows = env.sql(
        "SELECT COUNT(*) FROM tiqora_dashboard_counter_sample WHERE user_id = :u", u=ADMIN
    )
    assert rows[0][0] == 0
    # the same dashboard in the creator's own session does write history
    ok = await env.as_user(ADMIN).get(f"/api/v1/dashboards/{d}/data")
    assert ok.status_code == 200
    assert (
        env.sql("SELECT COUNT(*) FROM tiqora_dashboard_counter_sample WHERE user_id = :u", u=ADMIN)[
            0
        ][0]
        == 1
    )


async def test_data_for_dashboard_not_on_link_404(env: Env3) -> None:
    d1, d2 = await _board(env, "dash3 on"), await _board(env, "dash3 off")
    link = await _link(env, [d1])
    r = await _get(env, _data(d2), link["token"])
    assert r.status_code == 404 and r.json() == INVALID
    assert (await _get(env, _data(d1), link["token"])).status_code == 200


async def test_miss_rate_limit_per_ip_hash(env: Env3) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    for _ in range(30):
        assert (await _get(env, BOARD, "nope" * 10)).status_code == 404
    over = await _get(env, BOARD, "nope" * 10)
    assert over.status_code == 429 and int(over.headers["Retry-After"]) >= 1
    # the same IP with a valid token and another IP are both unaffected
    assert (await _get(env, BOARD, link["token"])).status_code == 200
    assert (await _get(env, BOARD, "nope" * 10, ip="203.0.113.77")).status_code == 404
    keys = await env.app.state.redis.keys("*")
    assert keys and not [k for k in keys if "203.0.113" in k], "only IP hashes are keys"


async def test_ip_backstop_blocks_after_240_requests(env: Env3) -> None:
    redis: FakeRedis = env.app.state.redis
    ip_hash_key = None
    await _get(env, BOARD, "x" * 43)
    keys = [k for k in await redis.keys("*") if ":kiosk_ip:" in k]
    assert len(keys) == 1
    ip_hash_key = keys[0]
    await redis.set(ip_hash_key, "240", ex=60)
    r = await _get(env, BOARD, "x" * 43)
    assert r.status_code == 429 and "Retry-After" in r.headers


async def test_redis_down_fails_closed(env: Env3) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    env.app.state.redis.fail = True
    for url in (BOARD, _data(d)):
        r = await _get(env, url, link["token"])
        assert r.status_code == 503
        assert r.headers["Cache-Control"] == "no-store"


async def test_headers_no_store_and_no_referrer_and_no_cookie_needed(env: Env3) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    for url, token, code in (
        (BOARD, link["token"], 200),
        (_data(d), link["token"], 200),
        (BOARD, "bad", 404),
        (BOARD, None, 404),
    ):
        r = await _get(env, url, token)
        assert r.status_code == code, (url, r.text)
        assert r.headers["Cache-Control"] == "no-store", url
        assert r.headers["Referrer-Policy"] == "no-referrer", url
    # cookies and bearer tokens are ignored: they neither help nor are required
    async with _anon(env) as c:
        c.cookies.set("tiqora_session", "whatever")
        r = await c.get(BOARD, headers={"Authorization": "Bearer abc"})
        assert r.status_code == 404 and r.json() == INVALID
        ok = await c.get(BOARD, headers={HDR: link["token"], "Authorization": "Bearer abc"})
        assert ok.status_code == 200


async def test_last_used_at_updated_at_most_every_ten_minutes(env: Env3) -> None:
    d = await _board(env)
    link = await _link(env, [d])

    def last_used() -> Any:
        return env.sql(
            "SELECT last_used_at FROM tiqora_dashboard_kiosk_token WHERE id = :i", i=link["id"]
        )[0][0]

    assert last_used() is None
    await _get(env, BOARD, link["token"])
    first = last_used()
    assert first is not None
    await _get(env, BOARD, link["token"])
    assert last_used() == first, "a second poll inside ten minutes changes nothing"
    old = utc_now() - timedelta(minutes=11)
    env.sql(
        "UPDATE tiqora_dashboard_kiosk_token SET last_used_at = :t WHERE id = :i",
        t=old,
        i=link["id"],
    )
    await _get(env, BOARD, link["token"])
    assert last_used() > old + timedelta(minutes=10)


async def test_cache_lock_single_computation(env: Env3, monkeypatch: pytest.MonkeyPatch) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    calls: list[list[str]] = []

    async def slow_batch(factory: Any, registry: Any, user_id: int, rows: Any, **kw: Any) -> Any:
        calls.append([w.id for w in rows])
        assert kw["kiosk"] is True and user_id == ADMIN
        await asyncio.sleep(0.6)
        return {w.id: WidgetDataOut(status="ok", data={"value": 7}) for w in rows}

    monkeypatch.setattr(kiosk, "run_batch", slow_batch)
    results = await asyncio.gather(*(_get(env, _data(d), link["token"]) for _ in range(3)))
    assert [r.status_code for r in results] == [200, 200, 200]
    assert len(calls) == 1 and calls[0] == [_wid(d, 1)], "recent-views never runs"
    assert all(r.json()["widgets"][_wid(d, 1)]["data"]["value"] == 7 for r in results)
    # served from the cache: still one computation
    assert (await _get(env, _data(d), link["token"])).status_code == 200
    assert len(calls) == 1


async def test_pending_is_cached_briefly_and_replaced_by_the_last_good_value(
    env: Env3, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    answers = [
        WidgetDataOut(status="ok", data={"value": 3}),
        WidgetDataOut(status="pending"),
    ]

    async def batch(factory: Any, registry: Any, user_id: int, rows: Any, **kw: Any) -> Any:
        return {w.id: answers[0] for w in rows}

    monkeypatch.setattr(kiosk, "run_batch", batch)
    redis: FakeRedis = env.app.state.redis
    first = await _get(env, _data(d), link["token"])
    assert first.json()["widgets"][_wid(d, 1)]["data"]["value"] == 3
    data_key = next(k for k in await redis.keys("*") if ":data:" in k)
    assert await redis.ttl(data_key) > kiosk.PENDING_CACHE_TTL_SECONDS
    redis.advance(kiosk.CACHE_TTL_SECONDS + 1)
    answers.reverse()  # next computation: pending
    second = await _get(env, _data(d), link["token"])
    widget = second.json()["widgets"][_wid(d, 1)]
    assert widget["status"] == "ok" and widget["data"]["value"] == 3, "stale value, not a hole"
    assert 0 < await redis.ttl(data_key) <= kiosk.PENDING_CACHE_TTL_SECONDS


async def test_trusted_proxies_give_separate_buckets_per_forwarded_client(env: Env3) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    settings = Settings(environment="test", TIQORA_TRUSTED_PROXIES="203.0.113.5")  # type: ignore[call-arg]
    env.app.dependency_overrides[get_app_settings] = lambda: settings
    for _ in range(30):
        r = await _get_xff(env, "198.51.100.1")
        assert r.status_code == 404
    assert (await _get_xff(env, "198.51.100.1")).status_code == 429
    assert (await _get_xff(env, "198.51.100.2")).status_code == 404
    ok = await _get_xff(env, "198.51.100.1", token=link["token"])
    assert ok.status_code == 200


async def _get_xff(env: Env3, xff: str, token: str = "nope" * 10) -> Any:
    async with _anon(env) as c:
        return await c.get(BOARD, headers={HDR: token, "X-Forwarded-For": xff})


async def test_malformed_requests_get_the_same_404(env: Env3) -> None:
    """No FastAPI 422 (other body, no no-store): an id below 1 and an oversized header."""
    d = await _board(env)
    link = await _link(env, [d])
    for url, token in (
        (_data(0), link["token"]),
        (_data(-3), link["token"]),
        ("/api/v1/kiosk/dashboards/abc/data", link["token"]),
        ("/api/v1/kiosk/dashboards/99999999999999999999999/data", link["token"]),
        (BOARD, "x" * 1500),
        (_data(d), "x" * 1500),
    ):
        r = await _get(env, url, token)
        assert r.status_code == 404 and r.json() == INVALID, (url, r.text)
        assert r.headers["Cache-Control"] == "no-store"
        assert r.headers["Referrer-Policy"] == "no-referrer"
    assert (await _get(env, _data(d), link["token"])).status_code == 200


async def test_lock_release_never_deletes_another_requests_lock(
    env: Env3, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = await _board(env)
    link = await _link(env, [d])
    redis: FakeRedis = env.app.state.redis

    async def slow_batch(factory: Any, registry: Any, user_id: int, rows: Any, **kw: Any) -> Any:
        # the lock expired meanwhile and another request took it
        lock_key = next(k for k in await redis.keys("*") if ":lock:" in k)
        await redis.set(lock_key, "someone-else", ex=kiosk.LOCK_TTL_SECONDS)
        return {w.id: WidgetDataOut(status="ok", data={"value": 1}) for w in rows}

    monkeypatch.setattr(kiosk, "run_batch", slow_batch)
    assert (await _get(env, _data(d), link["token"])).status_code == 200
    locks = [k for k in await redis.keys("*") if ":lock:" in k]
    assert len(locks) == 1 and await redis.get(locks[0]) == "someone-else"


def test_last_good_values_are_short_lived() -> None:
    """A last good value stands in for ``pending`` without a stale marker: at most 30 min."""
    assert kiosk.LAST_GOOD_TTL_SECONDS <= 30 * 60
