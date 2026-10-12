"""HTTP tests for ``/api/v1/report-schedules`` and the schedule store (both dialects).

Band 52120-52199 (names contain ``rep52``): agents 52120 ``rep52.owner`` (``reporting.access =
all_agents``, no ``stats`` right), 52121 ``rep52.sharer`` (``rw`` on ``stats``, ``ro`` on group
52150), 52122 ``rep52.colleague`` (``ro`` on 52150, member of role 52160, has an e-mail address),
52123 ``rep52.noemail`` (``ro`` on 52150, no address), 52124 ``rep52.invalid`` (valid until a
test invalidates it); groups 52150 and 52151 (no band agent is a member of 52151); role 52160.
Every row this module writes is deleted again (schedules, saved reports, ``reporting.*``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db
from tiqora.api.v1.reports import get_report_cache
from tiqora.config import Settings
from tiqora.db.tiqora.base import TiqoraBase
from tiqora.domain.api_key_scopes import path_to_area
from tiqora.domain.auth import AuthenticatedUser
from tiqora.reporting.schedules import load_schedule, resolve_recipients
from tiqora.reporting.settings import REPORTING_SECTION, ReportingSettings
from tiqora.settings_sections.store import save_section_values

pytestmark = pytest.mark.db

LO, HI = 52120, 52199
OWNER, SHARER, COLLEAGUE, NOEMAIL, INVALID = 52120, 52121, 52122, 52123, 52124
G50, G51, ROLE = 52150, 52151, 52160
LOGINS = {
    OWNER: "rep52.owner",
    SHARER: "rep52.sharer",
    COLLEAGUE: "rep52.colleague",
    NOEMAIL: "rep52.noemail",
    INVALID: "rep52.invalid",
}
NAMES = {
    OWNER: ("Olga", "Owner"),
    SHARER: ("Sven", "Sharer"),
    COLLEAGUE: ("Carla", "Colleague"),
    NOEMAIL: ("Nora", "Noemail"),
    INVALID: ("Ida", "Invalid"),
}
EMAILS = {
    OWNER: "rep52.owner@example.org",
    SHARER: "rep52.sharer@example.org",
    COLLEAGUE: "Rep52.Colleague@Example.org",
}
TICKETS_DEF = {
    "source": "tickets",
    "dimensions": [{"key": "queue"}],
    "measures": [{"key": "tickets"}],
}
BASE: dict[str, Any] = {
    "name": "rep52 Wochenbericht",
    "items": [{"report_key": "builtin.volume_by_queue"}],
    "format": "pdf",
    "recurrence": {"kind": "weekly", "time": "07:00", "weekdays": [1]},
    "time_zone": "Europe/Berlin",
    "recipients": {"user_ids": []},
}
_BAND = {"lo": LO, "hi": HI}
_RESET = (
    "DELETE FROM tiqora_report_schedule_recipient WHERE schedule_id IN"
    " (SELECT id FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_schedule_item WHERE schedule_id IN"
    " (SELECT id FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report_schedule WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_report_group WHERE report_id IN"
    " (SELECT id FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi)",
    "DELETE FROM tiqora_report WHERE owner_user_id BETWEEN :lo AND :hi",
    "DELETE FROM tiqora_settings WHERE {key} LIKE 'reporting.%'",
    "UPDATE users SET valid_id = 1 WHERE id = 52124",
)
_CLEANUP = (
    *_RESET,
    "DELETE FROM user_preferences WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM role_user WHERE role_id BETWEEN :lo AND :hi",
    "DELETE FROM roles WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM group_user WHERE user_id BETWEEN :lo AND :hi",
    "DELETE FROM permission_groups WHERE id BETWEEN :lo AND :hi",
    "DELETE FROM users WHERE id BETWEEN :lo AND :hi",
)
_URLS: set[str] = set()
_SEEDED: set[str] = set()


def _to_async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("mysql+pymysql://"):
        return sync_url.replace("mysql+pymysql://", "mysql+aiomysql://", 1)
    return sync_url


def _run_sql(sync_url: str, statements: tuple[str, ...]) -> None:
    engine = create_engine(sync_url)
    TiqoraBase.metadata.create_all(engine)
    key = "`key`" if engine.dialect.name == "mysql" else '"key"'
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt.replace("{key}", key)), _BAND)
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _delete_seeded_rows() -> Generator[None, None, None]:
    yield
    for url in _URLS:
        _run_sql(url, _CLEANUP)
    _URLS.clear()
    _SEEDED.clear()


def user(uid: int) -> AuthenticatedUser:
    login = "root@localhost" if uid == 1 else LOGINS[uid]
    first, last = ("Admin", "OTRS") if uid == 1 else NAMES[uid]
    return AuthenticatedUser(
        id=uid, login=login, first_name=first, last_name=last, auth_method="session"
    )


def _seed(sync_url: str) -> None:
    t0 = datetime(2026, 10, 1)
    engine = create_engine(sync_url)
    stamp = {"t": t0}
    with engine.begin() as conn:
        stats = conn.execute(
            text("SELECT id FROM permission_groups WHERE name = 'stats' AND valid_id = 1")
        ).scalar_one()
        for uid, login in LOGINS.items():
            first, last = NAMES[uid]
            conn.execute(
                text(
                    "INSERT INTO users (id, login, pw, first_name, last_name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :l, 'x', :f, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": uid, "l": login, "f": first, "n": last, **stamp},
            )
        for uid, address in EMAILS.items():
            conn.execute(
                text(
                    "INSERT INTO user_preferences (user_id, preferences_key, preferences_value)"
                    " VALUES (:u, 'UserEmail', :v)"
                ),
                {"u": uid, "v": address.encode()},
            )
        for gid in (G50, G51):
            conn.execute(
                text(
                    "INSERT INTO permission_groups (id, name, valid_id,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": gid, "n": f"rep52-group-{gid}", **stamp},
            )
        conn.execute(
            text(
                "INSERT INTO roles (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'rep52-role', 1, :t, 1, :t, 1)"
            ),
            {"id": ROLE, **stamp},
        )
        conn.execute(
            text(
                "INSERT INTO role_user (user_id, role_id, create_time, create_by,"
                " change_time, change_by) VALUES (:u, :r, :t, 1, :t, 1)"
            ),
            {"u": COLLEAGUE, "r": ROLE, **stamp},
        )
        for uid, gid, key in (
            (SHARER, stats, "rw"),
            (SHARER, G50, "ro"),
            (COLLEAGUE, G50, "ro"),
            (NOEMAIL, G50, "ro"),
        ):
            conn.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key,"
                    " create_time, create_by, change_time, change_by)"
                    " VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": uid, "g": gid, "k": key, **stamp},
            )
    engine.dispose()


class _MemoryCache:
    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, ex: int) -> None:
        return None


class Env:
    def __init__(
        self, sync_url: str, factory: async_sessionmaker[AsyncSession], client: AsyncClient
    ) -> None:
        self.sync_url = sync_url
        self.factory = factory
        self.client = client
        self.user = user(OWNER)

    async def setting(self, **values: Any) -> None:
        async with self.factory() as s:
            await save_section_values(
                s, REPORTING_SECTION, values, secret_key=Settings(environment="test").secret_key
            )
            await s.commit()

    def sql(self, *statements: str) -> None:
        _run_sql(self.sync_url, statements)

    async def create(self, **changes: Any) -> dict[str, Any]:
        resp = await self.client.post("/api/v1/report-schedules", json=body(**changes))
        assert resp.status_code == 201, resp.text
        return resp.json()  # type: ignore[no-any-return]


def body(**changes: Any) -> dict[str, Any]:
    return {**BASE, **changes}


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    sync_url: str = request.getfixturevalue(request.param)
    _URLS.add(sync_url)
    engine = create_async_engine(_to_async_url(sync_url))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    if sync_url not in _SEEDED:
        _run_sql(sync_url, _CLEANUP)
        _seed(sync_url)
        _SEEDED.add(sync_url)
    _run_sql(sync_url, _RESET)

    async def _db() -> Any:
        async with factory() as session:
            yield session

    app = create_app(Settings(environment="test"))
    holder: list[Env] = []
    app.dependency_overrides[get_current_user] = lambda: holder[0].user
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_report_cache] = _MemoryCache
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            holder.append(Env(sync_url, factory, c))
            yield holder[0]
    finally:
        await engine.dispose()
        _run_sql(sync_url, _RESET)


# ---------------------------------------------------------------- create, rights


async def test_create_self_only_without_share_right(env: Env) -> None:
    env.user = user(OWNER)
    resp = await env.client.post(
        "/api/v1/report-schedules", json=body(recipients={"user_ids": [OWNER]})
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["next_run_at"] is not None and out["recurrence_text"].startswith("Jeden Montag")
    assert out["recipients"][0]["label"] == "Olga Owner" and out["recipients"][0]["problem"] is None
    assert out["owner"] == {"user_id": OWNER, "name": "Olga Owner"}
    assert out["is_owner"] is True and out["can_edit"] is True and out["version"] == 1
    assert out["items"][0]["available"] is True and out["items"][0]["title_key"]
    resp = await env.client.post(
        "/api/v1/report-schedules", json=body(recipients={"user_ids": [COLLEAGUE]})
    )
    assert resp.status_code == 403 and "Berechtigung" in resp.json()["detail"]["message"]
    nobody = await env.client.post("/api/v1/report-schedules", json=body())
    assert nobody.status_code == 422 and nobody.json()["detail"]["path"] == "recipients"


async def test_groups_roles_emails_rules(env: Env) -> None:
    env.user = user(SHARER)
    ok = await env.client.post(
        "/api/v1/report-schedules",
        json=body(recipients={"group_ids": [G50], "role_ids": [ROLE]}),
    )
    assert ok.status_code == 201, ok.text
    kinds = {(r["kind"], r["label"]) for r in ok.json()["recipients"]}
    assert kinds == {("group", f"rep52-group-{G50}"), ("role", "rep52-role")}
    bad_group = await env.client.post(
        "/api/v1/report-schedules", json=body(recipients={"group_ids": [G51]})
    )
    assert bad_group.status_code == 422  # the owner is not a member of 52151
    assert bad_group.json()["detail"]["path"] == "recipients.group_ids"
    bad_role = await env.client.post(
        "/api/v1/report-schedules", json=body(recipients={"role_ids": [ROLE + 1]})
    )
    assert bad_role.status_code == 422
    ext = await env.client.post(
        "/api/v1/report-schedules", json=body(recipients={"emails": ["boss@example.org"]})
    )
    assert ext.status_code == 422 and ext.json()["detail"]["path"] == "recipients.emails"
    await env.setting(schedule_external_recipients=True)
    ext2 = await env.client.post(
        "/api/v1/report-schedules",
        json=body(recipients={"emails": [" Boss@Example.org ", "boss@example.org"]}),
    )
    assert ext2.status_code == 201, ext2.text
    assert [r["email"] for r in ext2.json()["recipients"]] == ["boss@example.org"]
    for bad in ("no-at", "a@b@c", "x <y@example.org>", "a@example.org, b@example.org"):
        r = await env.client.post(
            "/api/v1/report-schedules", json=body(recipients={"emails": [bad]})
        )
        assert r.status_code == 422, bad


async def test_external_emails_need_the_share_right_too(env: Env) -> None:
    await env.setting(schedule_external_recipients=True)
    env.user = user(OWNER)
    resp = await env.client.post(
        "/api/v1/report-schedules", json=body(recipients={"emails": ["boss@example.org"]})
    )
    assert resp.status_code == 403
    assert "Berechtigung" in resp.json()["detail"]["message"]


async def test_items_must_resolve_for_owner(env: Env) -> None:
    env.user = user(SHARER)
    made = await env.client.post(
        "/api/v1/reports", json={"name": "rep52 privat", "definition": TICKETS_DEF}
    )
    assert made.status_code == 201, made.text
    key = made.json()["key"]
    env.user = user(OWNER)
    resp = await env.client.post(
        "/api/v1/report-schedules",
        json=body(items=[{"report_key": key}], recipients={"user_ids": [OWNER]}),
    )
    assert resp.status_code == 422 and resp.json()["detail"]["path"] == "items.0.report_key"
    resp = await env.client.post(
        "/api/v1/report-schedules",
        json=body(
            items=[
                {
                    "report_key": "builtin.volume_by_queue",
                    "overrides": {"parameters": {"nope": [1]}},
                }
            ],
            recipients={"user_ids": [OWNER]},
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["path"].startswith("items.0.overrides")
    ok = await env.client.post(
        "/api/v1/report-schedules",
        json=body(
            items=[
                {
                    "report_key": "builtin.volume_by_queue",
                    "overrides": {
                        "parameters": {"queues": [1]},
                        "time_range": {"kind": "relative", "unit": "day", "count": 7},
                    },
                }
            ],
            recipients={"user_ids": [OWNER]},
        ),
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["items"][0]["overrides"]["parameters"] == {"queues": [1]}


async def test_item_turns_unavailable_when_the_report_is_deleted(env: Env) -> None:
    env.user = user(OWNER)
    made = await env.client.post(
        "/api/v1/reports", json={"name": "rep52 eigener", "definition": TICKETS_DEF}
    )
    key = made.json()["key"]
    sched = await env.create(items=[{"report_key": key}], recipients={"user_ids": [OWNER]})
    assert sched["items"][0]["title"] == "rep52 eigener" and sched["items"][0]["available"]
    assert (await env.client.delete(f"/api/v1/reports/{key}")).status_code == 204
    out = (await env.client.get(f"/api/v1/report-schedules/{sched['id']}")).json()
    assert out["items"][0]["available"] is False
    assert "gelöscht" in out["items"][0]["problem"]


async def test_name_is_one_line_and_blank_is_rejected(env: Env) -> None:
    env.user = user(OWNER)
    out = await env.create(
        name="rep52 a\r\nBcc: x", subject="Betreff\nzwei", recipients={"user_ids": [OWNER]}
    )
    assert out["name"] == "rep52 a Bcc: x" and out["subject"] == "Betreff zwei"
    blank = await env.client.post(
        "/api/v1/report-schedules", json=body(name="  \n ", recipients={"user_ids": [OWNER]})
    )
    assert blank.status_code == 422


async def test_unknown_time_zone_and_default_zone(env: Env) -> None:
    env.user = user(OWNER)
    bad = await env.client.post(
        "/api/v1/report-schedules",
        json=body(time_zone="Mars/Base", recipients={"user_ids": [OWNER]}),
    )
    assert bad.status_code == 422 and bad.json()["detail"]["path"] == "time_zone"
    payload = body(recipients={"user_ids": [OWNER]})
    payload.pop("time_zone")
    made = await env.client.post("/api/v1/report-schedules", json=payload)
    assert made.status_code == 201 and made.json()["time_zone"]


# ---------------------------------------------------------------- visibility, update


async def test_visibility_owner_admin_only(env: Env) -> None:
    env.user = user(OWNER)
    sid = (await env.create(recipients={"user_ids": [OWNER]}))["id"]
    env.user = user(SHARER)
    assert (await env.client.get(f"/api/v1/report-schedules/{sid}")).status_code == 404
    assert (await env.client.delete(f"/api/v1/report-schedules/{sid}")).status_code == 404
    assert sid not in [s["id"] for s in (await env.client.get("/api/v1/report-schedules")).json()]
    assert (await env.client.get("/api/v1/report-schedules?all=true")).status_code == 403
    env.user = user(1)
    assert (await env.client.get(f"/api/v1/report-schedules/{sid}")).status_code == 200
    assert sid not in [s["id"] for s in (await env.client.get("/api/v1/report-schedules")).json()]
    listed = (await env.client.get("/api/v1/report-schedules?all=true")).json()
    row = next(s for s in listed if s["id"] == sid)
    assert row["owner"]["name"] == "Olga Owner" and row["is_owner"] is False
    assert row["can_edit"] is True and "recipients" not in row
    assert row["items"][0]["title_key"] == "reports.builtin.volume_by_queue.title"


async def test_update_version_conflict_and_next_run(env: Env) -> None:
    env.user = user(OWNER)
    s = await env.create(recipients={"user_ids": [OWNER]})
    upd = body(recipients={"user_ids": [OWNER]}, recurrence={"kind": "daily", "time": "06:00"})
    upd["version"] = 1
    first = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=upd)
    assert first.status_code == 200, first.text
    s2 = first.json()
    assert s2["version"] == 2 and s2["next_run_at"] != s["next_run_at"]
    assert s2["recurrence_text"].startswith("Täglich")
    stale = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=upd)
    assert stale.status_code == 409 and stale.json()["detail"]["current_version"] == 2
    paused = (
        await env.client.put(
            f"/api/v1/report-schedules/{s['id']}", json={**upd, "active": False, "version": 2}
        )
    ).json()
    assert paused["active"] is False and paused["next_run_at"] is None
    resumed = (
        await env.client.put(
            f"/api/v1/report-schedules/{s['id']}", json={**upd, "active": True, "version": 3}
        )
    ).json()
    assert resumed["next_run_at"] is not None and resumed["failure_count"] == 0


async def test_update_keeps_next_run_when_the_schedule_is_unchanged(env: Env) -> None:
    env.user = user(OWNER)
    s = await env.create(recipients={"user_ids": [OWNER]})
    upd = body(recipients={"user_ids": [OWNER]}, name="rep52 umbenannt", version=1)
    out = (await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=upd)).json()
    assert out["name"] == "rep52 umbenannt" and out["next_run_at"] == s["next_run_at"]


async def test_update_reactivation_clears_pause_state(env: Env) -> None:
    env.user = user(OWNER)
    s = await env.create(recipients={"user_ids": [OWNER]})
    env.sql(
        f"UPDATE tiqora_report_schedule SET active = false,"
        " next_run_at = NULL, failure_count = 3, paused_reason = 'Mehrfach fehlgeschlagen'"
        f" WHERE id = {s['id']}"
    )
    paused = (await env.client.get(f"/api/v1/report-schedules/{s['id']}")).json()
    assert paused["paused_reason"] == "Mehrfach fehlgeschlagen" and paused["failure_count"] == 3
    upd = body(recipients={"user_ids": [OWNER]}, version=1)
    out = (await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=upd)).json()
    assert out["active"] is True and out["paused_reason"] is None
    assert out["failure_count"] == 0 and out["next_run_at"] is not None


async def test_saving_while_paused_keeps_the_pause_reason(env: Env) -> None:
    env.user = user(OWNER)
    s = await env.create(recipients={"user_ids": [OWNER]})
    env.sql(
        f"UPDATE tiqora_report_schedule SET active = false, next_run_at = NULL,"
        f" failure_count = 3, paused_reason = 'Mehrfach fehlgeschlagen' WHERE id = {s['id']}"
    )
    upd = body(recipients={"user_ids": [OWNER]}, name="rep52 neu", active=False, version=1)
    resp = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=upd)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["active"] is False and out["name"] == "rep52 neu"
    assert out["paused_reason"] == "Mehrfach fehlgeschlagen" and out["failure_count"] == 3


async def test_pausing_works_when_a_stored_recipient_became_invalid(env: Env) -> None:
    await env.setting(schedule_external_recipients=True)
    env.user = user(SHARER)
    s = await env.create(recipients={"user_ids": [SHARER], "emails": ["boss@example.org"]})
    await env.setting(schedule_external_recipients=False)
    full = (await env.client.get(f"/api/v1/report-schedules/{s['id']}")).json()
    stored = {
        "user_ids": [r["id"] for r in full["recipients"] if r["kind"] == "user"],
        "emails": [r["email"] for r in full["recipients"] if r["kind"] == "email"],
    }
    pause = body(recipients=stored, active=False, version=full["version"])
    paused = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=pause)
    assert paused.status_code == 200, paused.text
    assert paused.json()["active"] is False and paused.json()["next_run_at"] is None
    assert [r["email"] for r in paused.json()["recipients"] if r["kind"] == "email"] == [
        "boss@example.org"
    ]
    # resuming, or changing the recipients while paused, is checked as before
    resume = body(recipients=stored, active=True, version=paused.json()["version"])
    refused = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=resume)
    assert refused.status_code == 422 and refused.json()["detail"]["path"] == "recipients.emails"
    widen = body(
        recipients={**stored, "emails": ["boss@example.org", "other@example.org"]},
        active=False,
        version=paused.json()["version"],
    )
    refused = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=widen)
    assert refused.status_code == 422


async def test_huge_ids_and_versions_are_422(env: Env) -> None:
    env.user = user(SHARER)
    for field in ("user_ids", "group_ids", "role_ids"):
        resp = await env.client.post(
            "/api/v1/report-schedules", json=body(recipients={field: [2**31]})
        )
        assert resp.status_code == 422, (field, resp.text)
    s = await env.create(recipients={"user_ids": [SHARER]})
    resp = await env.client.put(
        f"/api/v1/report-schedules/{s['id']}",
        json=body(recipients={"user_ids": [SHARER]}, version=2**31),
    )
    assert resp.status_code == 422, resp.text


async def test_list_rows_carry_the_time_zone(env: Env) -> None:
    env.user = user(OWNER)
    s = await env.create(recipients={"user_ids": [OWNER]}, time_zone="America/New_York")
    rows = (await env.client.get("/api/v1/report-schedules")).json()
    assert [r["time_zone"] for r in rows if r["id"] == s["id"]] == ["America/New_York"]


async def test_admin_edit_is_judged_by_the_owners_rights(env: Env) -> None:
    env.user = user(OWNER)
    s = await env.create(recipients={"user_ids": [OWNER]})
    env.user = user(1)
    keep = body(recipients={"user_ids": [OWNER]}, name="rep52 vom Admin", version=1)
    ok = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=keep)
    assert ok.status_code == 200, ok.text
    assert ok.json()["owner"]["user_id"] == OWNER and ok.json()["name"] == "rep52 vom Admin"
    widen = body(recipients={"user_ids": [OWNER, COLLEAGUE]}, version=2)
    refused = await env.client.put(f"/api/v1/report-schedules/{s['id']}", json=widen)
    assert refused.status_code == 403  # the owner has no share right, the admin's does not count


async def test_recipient_problems_are_shown(env: Env) -> None:
    env.user = user(SHARER)
    s = await env.create(recipients={"user_ids": [NOEMAIL, INVALID, COLLEAGUE]})
    env.sql("UPDATE users SET valid_id = 2 WHERE id = 52124")
    out = (await env.client.get(f"/api/v1/report-schedules/{s['id']}")).json()
    problems = {r["id"]: r["problem"] for r in out["recipients"]}
    assert "E-Mail" in problems[NOEMAIL]
    assert problems[INVALID] == "Ungültig."
    assert problems[COLLEAGUE] is None
    invalid_user = await env.client.post(
        "/api/v1/report-schedules", json=body(recipients={"user_ids": [INVALID]})
    )
    assert invalid_user.status_code == 422
    assert invalid_user.json()["detail"]["path"] == "recipients.user_ids"


async def test_duplicate_recipients_are_stored_once(env: Env) -> None:
    env.user = user(SHARER)
    out = await env.create(recipients={"user_ids": [COLLEAGUE, COLLEAGUE], "group_ids": [G50, G50]})
    assert [(r["kind"], r["id"]) for r in out["recipients"]] == [
        ("user", COLLEAGUE),
        ("group", G50),
    ]


async def test_preview_recurrence(env: Env) -> None:
    env.user = user(OWNER)
    out = (
        await env.client.post(
            "/api/v1/report-schedules/preview-recurrence",
            json={"recurrence": {"kind": "daily", "time": "07:00"}, "time_zone": "Europe/Berlin"},
        )
    ).json()
    assert len(out["next_runs"]) == 5 and out["text"] == "Täglich um 07:00"
    assert out["next_runs"] == sorted(out["next_runs"])
    english = await env.client.post(
        "/api/v1/report-schedules/preview-recurrence",
        json={"recurrence": {"kind": "daily", "time": "07:00"}, "language": "en"},
    )
    assert english.status_code == 200, english.text
    assert english.json()["text"] == "Daily at 07:00"
    bad = await env.client.post(
        "/api/v1/report-schedules/preview-recurrence",
        json={"recurrence": {"kind": "daily", "time": "07:00"}, "time_zone": "Mars/Base"},
    )
    assert bad.status_code == 422
    broken = await env.client.post(
        "/api/v1/report-schedules/preview-recurrence",
        json={"recurrence": {"kind": "weekly", "time": "07:00"}},
    )
    assert broken.status_code == 422


async def test_limit_and_delete(env: Env) -> None:
    env.user = user(OWNER)
    first_id = None
    for n in range(50):
        made = await env.client.post(
            "/api/v1/report-schedules",
            json=body(name=f"rep52 plan {n}", recipients={"user_ids": [OWNER]}),
        )
        assert made.status_code == 201, made.text
        first_id = first_id or made.json()["id"]
    over = await env.client.post(
        "/api/v1/report-schedules",
        json=body(name="rep52 plan 51", recipients={"user_ids": [OWNER]}),
    )
    assert over.status_code == 422 and over.json()["detail"]["path"] == "name"
    assert (await env.client.delete(f"/api/v1/report-schedules/{first_id}")).status_code == 204
    assert (await env.client.get(f"/api/v1/report-schedules/{first_id}")).status_code == 404
    again = await env.client.post(
        "/api/v1/report-schedules",
        json=body(name="rep52 plan 51", recipients={"user_ids": [OWNER]}),
    )
    assert again.status_code == 201
    rows = await env.client.get("/api/v1/report-schedules")
    assert len(rows.json()) == 50


async def test_access_stats_group_needs_the_create_right(env: Env) -> None:
    await env.setting(access="stats_group")
    env.user = user(OWNER)  # no group rights at all
    assert (await env.client.get("/api/v1/report-schedules")).status_code == 403
    env.user = user(SHARER)  # rw on stats
    assert (await env.client.get("/api/v1/report-schedules")).status_code == 200


async def test_me_flag_and_api_key_scope(env: Env) -> None:
    env.user = user(OWNER)
    assert (await env.client.get("/api/v1/auth/me")).json()["report_external_recipients"] is False
    await env.setting(schedule_external_recipients=True)
    assert (await env.client.get("/api/v1/auth/me")).json()["report_external_recipients"] is True
    assert path_to_area("/api/v1/report-schedules/5") == "stats"
    assert path_to_area("/api/v1/report-schedules") == "stats"
    assert path_to_area("/api/v1/report-runs/5/files/1") == "stats"


# ---------------------------------------------------------------- recipient resolution


async def _resolve(env: Env, schedule_id: int, *, owner_only: bool = False, **setting: Any) -> Any:
    reporting = ReportingSettings(**setting)
    async with env.factory() as session:
        schedule = await load_schedule(session, schedule_id)
        assert schedule is not None
        return await resolve_recipients(session, schedule, reporting, owner_only=owner_only)


async def test_resolve_recipients_expands_groups_and_roles(env: Env) -> None:
    env.user = user(SHARER)
    s = await env.create(
        recipients={"user_ids": [OWNER, INVALID], "group_ids": [G50], "role_ids": [ROLE]}
    )
    env.sql("UPDATE users SET valid_id = 2 WHERE id = 52124")
    got = await _resolve(env, s["id"])
    # group 52150: sharer, colleague (also via the role) and noemail (no address)
    assert got.addresses == (
        "rep52.colleague@example.org",
        "rep52.owner@example.org",
        "rep52.sharer@example.org",
    )
    assert "1 Empfänger ohne E-Mail-Adresse" in got.problems
    assert any("ungültig" in line and "Ida Invalid" in line for line in got.problems)
    assert not any("@" in line for line in got.problems)
    only_owner = await _resolve(env, s["id"], owner_only=True)
    assert only_owner.addresses == ("rep52.sharer@example.org",)  # the schedule owner
    assert only_owner.problems == ()


async def test_resolve_recipients_names_direct_users_without_address(env: Env) -> None:
    env.user = user(SHARER)
    s = await env.create(recipients={"user_ids": [NOEMAIL, COLLEAGUE]})
    got = await _resolve(env, s["id"])
    assert got.addresses == ("rep52.colleague@example.org",)
    assert got.problems == ("Nora Noemail: keine E-Mail-Adresse",)


async def test_resolve_recipients_externals_and_cap(env: Env) -> None:
    await env.setting(schedule_external_recipients=True)
    env.user = user(SHARER)
    s = await env.create(
        recipients={"user_ids": [COLLEAGUE], "emails": ["b@example.org", "A@example.org"]}
    )
    got = await _resolve(env, s["id"], schedule_external_recipients=True)
    assert got.addresses == ("a@example.org", "b@example.org", "rep52.colleague@example.org")
    capped = await _resolve(
        env, s["id"], schedule_external_recipients=True, schedule_max_recipients=2
    )
    assert capped.addresses == ("a@example.org", "b@example.org")
    assert capped.problems == ("1 Empfänger über dem Limit",)
    off = await _resolve(env, s["id"], schedule_external_recipients=False)
    assert off.addresses == ("rep52.colleague@example.org",)
    assert off.problems == ("Externe Empfänger sind ausgeschaltet.",)
