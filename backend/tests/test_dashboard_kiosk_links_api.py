"""HTTP tests for ``/api/v1/admin/dashboard-kiosk-links`` (both dialects, band 61450-61469).

The world (users, queues, cleanup of ``tiqora_dashboard_kiosk_*``) is ``_dashboard3_helpers``.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
import structlog.testing

from tests._dashboard3_helpers import ADMIN, A, B, Env3, open_env3
from tiqora.jobs.service import utc_now

pytestmark = pytest.mark.db

URL = "/api/v1/admin/dashboard-kiosk-links"


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env3]:
    async with open_env3(request.getfixturevalue(request.param)) as e:
        yield e


async def _board(env: Env3, owner: int = ADMIN, name: str = "dash3 wall") -> int:
    return (await env.create_personal(owner=owner, name=name)).id


def _body(dashboard_ids: list[int], **over: Any) -> dict[str, Any]:
    return {"name": "dash3 Lobby", "dashboard_ids": dashboard_ids, **over}


async def _create(env: Env3, dashboard_ids: list[int], **over: Any) -> dict[str, Any]:
    r = await env.as_user(ADMIN).post(URL, json=_body(dashboard_ids, **over))
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def _row(env: Env3, link_id: int) -> Any:
    rows = env.sql(
        "SELECT token_hash, token_hint, created_by, rotate_seconds, show_details, expires_at,"
        " revoked_at FROM tiqora_dashboard_kiosk_token WHERE id = :i",
        i=link_id,
    )
    assert len(rows) == 1
    return rows[0]


async def test_token_stored_hashed_and_shown_once(env: Env3) -> None:
    d1, d2 = await _board(env, name="dash3 one"), await _board(env, name="dash3 two")
    out = await _create(env, [d2, d1], rotate_seconds=30, show_details=True)
    token = out["token"]
    row = _row(env, out["id"])
    assert row[0] == hashlib.sha256(token.encode()).hexdigest() and token not in row[0]
    assert len(token) >= 43 and row[1] == token[-4:] == out["token_hint"]
    assert row[2] == ADMIN and row[3] == 30 and bool(row[4]) is True
    assert [d["id"] for d in out["dashboards"]] == [d2, d1], "rotation order kept"
    assert all(d["visible"] for d in out["dashboards"]) and out["active"] is True
    assert out["created_by_name"] == "Ada Admin" and out["expires_at"] > out["created_at"]
    listed = await env.as_user(ADMIN).get(URL)
    assert listed.status_code == 200
    assert "token" not in listed.json()[0] and token not in listed.text
    assert listed.json()[0]["token_hint"] == token[-4:]
    # defaults: 60 s rotation, no details, 90 days
    default = await _create(env, [d1])
    assert default["rotate_seconds"] == 60 and default["show_details"] is False
    days = (_row(env, default["id"])[5] - utc_now().replace(tzinfo=None)) / timedelta(days=1)
    assert 89.9 < days < 90.1
    # two links never share a secret
    assert default["token"] != token


async def test_only_admins_and_only_visible_dashboards(env: Env3) -> None:
    mine = await _board(env)
    foreign = await _board(env, owner=A, name="dash3 personal of A")
    agent = env.as_user(A)
    for method, url, body in (
        ("GET", URL, None),
        ("POST", URL, _body([mine])),
        ("POST", f"{URL}/1/revoke", None),
        ("DELETE", f"{URL}/1", None),
    ):
        assert (await agent.request(method, url, json=body)).status_code == 403, method
    assert (await env.client.__class__.request(env.client, "GET", URL)).status_code == 403
    # another user's personal dashboard is not visible to the admin either
    r = await env.as_user(ADMIN).post(URL, json=_body([mine, foreign]))
    assert r.status_code == 404, r.text
    assert (
        env.sql("SELECT COUNT(*) FROM tiqora_dashboard_kiosk_token WHERE created_by = :a", a=ADMIN)[
            0
        ][0]
        == 0
    )
    assert (await env.as_user(ADMIN).post(URL, json=_body([999_999_999]))).status_code == 404


async def test_bounds(env: Env3) -> None:
    ids = [await _board(env, name=f"dash3 b{i}") for i in range(2)]
    admin = env.as_user(ADMIN)
    for over in (
        {"dashboard_ids": []},
        {"dashboard_ids": list(range(1, 12))},
        {"dashboard_ids": [ids[0], ids[0]]},
        {"rotate_seconds": 14},
        {"rotate_seconds": 901},
        {"expires_in_days": 0},
        {"expires_in_days": 366},
        {"name": " "},
        {"unknown": 1},
    ):
        r = await admin.post(URL, json={**_body([ids[0]]), **over})
        assert r.status_code == 422, (over, r.text)
    for ok in ({"rotate_seconds": 15}, {"rotate_seconds": 900}, {"expires_in_days": 365}):
        assert (await admin.post(URL, json={**_body([ids[0]]), **ok})).status_code == 201


async def test_revoke_is_idempotent_and_delete_needs_revoked(env: Env3) -> None:
    link = await _create(env, [await _board(env)])
    admin = env.as_user(ADMIN)
    dash = link["dashboards"][0]["id"]
    # an active link cannot be deleted
    r = await admin.delete(f"{URL}/{link['id']}")
    assert (
        r.status_code == 422
        and r.json()["detail"]["message"] == "Bitte den Link zuerst widerrufen."
    )
    first = await admin.post(f"{URL}/{link['id']}/revoke")
    assert (
        first.status_code == 200 and first.json()["revoked_at"] and first.json()["active"] is False
    )
    second = await admin.post(f"{URL}/{link['id']}/revoke")
    assert second.status_code == 200 and second.json()["revoked_at"] == first.json()["revoked_at"]
    assert (await admin.delete(f"{URL}/{link['id']}")).status_code == 204
    assert (
        env.sql("SELECT COUNT(*) FROM tiqora_dashboard_kiosk_token WHERE id = :i", i=link["id"])[0][
            0
        ]
        == 0
    )
    assert (
        env.sql(
            "SELECT COUNT(*) FROM tiqora_dashboard_kiosk_item WHERE token_id = :i", i=link["id"]
        )[0][0]
        == 0
    )
    assert env.sql("SELECT COUNT(*) FROM tiqora_dashboard WHERE id = :i", i=dash)[0][0] == 1
    for method in ("POST", "DELETE"):
        url = f"{URL}/{link['id']}" + ("/revoke" if method == "POST" else "")
        assert (await admin.request(method, url)).status_code == 404


async def test_expired_link_can_be_deleted_and_listing_puts_active_first(env: Env3) -> None:
    board = await _board(env)
    old = await _create(env, [board], name="dash3 old")
    revoked = await _create(env, [board], name="dash3 revoked")
    await _create(env, [board], name="dash3 live")
    env.sql(
        "UPDATE tiqora_dashboard_kiosk_token SET expires_at = :e WHERE id = :i",
        e=utc_now().replace(tzinfo=None) - timedelta(days=1),
        i=old["id"],
    )
    admin = env.as_user(ADMIN)
    await admin.post(f"{URL}/{revoked['id']}/revoke")
    rows = (await admin.get(URL)).json()
    mine = [r for r in rows if r["name"].startswith("dash3")]
    assert [r["name"] for r in mine][0] == "dash3 live"
    assert {r["name"]: r["active"] for r in mine} == {
        "dash3 live": True,
        "dash3 revoked": False,
        "dash3 old": False,
    }
    assert (await admin.delete(f"{URL}/{old['id']}")).status_code == 204


async def test_dashboard_the_creator_lost_is_listed_as_not_visible(env: Env3) -> None:
    board = await _board(env)
    link = await _create(env, [board])
    env.sql("DELETE FROM tiqora_dashboard_kiosk_item WHERE token_id = :i", i=link["id"])
    other = (await env.create_personal(owner=B, name="dash3 b private")).id
    env.sql(
        "INSERT INTO tiqora_dashboard_kiosk_item (token_id, dashboard_id, position)"
        " VALUES (:t, :d, 0)",
        t=link["id"],
        d=other,
    )
    out = (await env.as_user(ADMIN).get(URL)).json()
    mine = next(r for r in out if r["id"] == link["id"])
    assert mine["dashboards"] == [{"id": other, "name": "dash3 b private", "visible": False}]


async def test_token_never_logged(env: Env3, caplog: pytest.LogCaptureFixture) -> None:
    board = await _board(env)
    with structlog.testing.capture_logs() as events, caplog.at_level("DEBUG"):
        link = await _create(env, [board])
        await env.as_user(ADMIN).get(URL)
        await env.as_user(ADMIN).post(f"{URL}/{link['id']}/revoke")
    token = link["token"]
    assert any(e.get("link_id") == link["id"] for e in events), "events carry the link id"
    assert not any(token in repr(e) for e in events)
    assert token not in caplog.text
