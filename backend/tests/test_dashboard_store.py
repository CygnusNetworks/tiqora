"""Dashboard store: visibility, rights, first-visit start, optimistic lock (both dialects).

Seed band 49710-49749 (see ``_dashboard_helpers``); every row is removed again.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from tests._dashboard_helpers import (
    GROUP_2,
    ROLE_1,
    ROLE_2,
    USER_A,
    USER_B,
    USER_C,
    Env,
    open_env,
)
from tiqora.dashboard import start
from tiqora.dashboard.defaults import default_widgets
from tiqora.dashboard.layout import Rect
from tiqora.dashboard.schemas import DashboardCreateIn, WidgetIn, WidgetLayouts
from tiqora.dashboard.start import ensure_start, is_race_error
from tiqora.dashboard.store import (
    DashboardConflict,
    DashboardForbidden,
    DashboardInvalid,
    DashboardNotFound,
    user_role_ids,
)

pytestmark = pytest.mark.db


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env]:
    async with open_env(request.getfixturevalue(request.param)) as e:
        yield e


def w(
    wid: str,
    type_key: str,
    settings: dict[str, Any] | None = None,
    lg: tuple[int, int, int, int] = (0, 0, 6, 2),
    md: tuple[int, int, int, int] | None = None,
) -> WidgetIn:
    x, y, ww, h = lg
    return WidgetIn(
        id=wid,
        type_key=type_key,
        settings=settings or {},
        layout=WidgetLayouts(
            lg=Rect(x=x, y=y, w=ww, h=h),
            md=Rect(x=md[0], y=md[1], w=md[2], h=md[3]) if md else None,
        ),
    )


def counter_widget(preset: str) -> WidgetIn:
    return w(uuid.uuid4().hex, "core.counter", {"source": {"kind": "preset", "preset": preset}})


# ------------------------------------------------------------------ first visit


async def test_ensure_start_creates_the_default_once(env: Env) -> None:
    async with env.session() as s:
        d1, created1 = await ensure_start(s, USER_A)
        d2, created2 = await ensure_start(s, USER_A)
    assert (created1, created2) == (True, False) and d1.id == d2.id
    assert d1.name == "Mein Start" and d1.kind == "personal" and len(d1.widgets) == 11
    assert [x.type_key for x in d1.widgets] == [x.type_key for x in default_widgets()]
    assert d1.is_owner and d1.can_edit and d1.can_share and d1.version == 1


async def test_concurrent_first_visits_create_exactly_one(env: Env) -> None:
    results = await asyncio.gather(*(env.run(ensure_start, USER_B) for _ in range(4)))
    assert sum(1 for _, created in results if created) == 1
    assert len({d.id for d, _ in results}) == 1
    assert len(await env.count_dashboards(USER_B)) == 1
    assert len(await env.widget_ids(results[0][0].id)) == 11


async def test_unlocked_role_template_is_copied_at_first_visit(env: Env) -> None:
    tpl = await env.make_template(
        name="dash497 Support",
        role_ids=[ROLE_1],
        locked=False,
        widgets=[counter_widget("mine_new")],
    )
    async with env.session() as s:
        d, created = await ensure_start(s, USER_A)
    assert created and d.source_template_id == tpl.id
    assert [x.type_key for x in d.widgets] == ["core.counter"]
    assert d.widgets[0].id != tpl.widgets[0].id  # copied widgets get new ids
    assert d.widgets[0].settings == tpl.widgets[0].settings


async def test_template_priority_is_sort_then_id(env: Env) -> None:
    low = await env.make_template(name="dash497 low", role_ids=[ROLE_1], locked=False, sort=1)
    await env.make_template(name="dash497 high", role_ids=[ROLE_1], locked=False, sort=5)
    async with env.session() as s:
        d, _ = await ensure_start(s, USER_A)
    assert d.source_template_id == low.id  # sort 1 beats sort 5


async def test_template_of_a_deleted_role_applies_to_nobody(env: Env) -> None:
    await env.make_template(
        name="dash497 tot",
        role_ids=[ROLE_2],  # seeded with valid_id = 2; USER_C is its member
        locked=False,
        widgets=[counter_widget("mine_new")],
    )
    async with env.session() as s:
        d, _ = await ensure_start(s, USER_C)
        assert ROLE_2 not in await user_role_ids(s, USER_C)
    assert d.source_template_id is None and len(d.widgets) == 11  # built-in default


async def test_only_a_locked_template_still_gets_mein_start(env: Env) -> None:
    tpl = await env.make_template(name="dash497 Pflicht", role_ids=[ROLE_1], locked=True)
    assert [t.id for t in await env.list(USER_A)] == [tpl.id]  # the only tab
    async with env.session() as s:
        d, created = await ensure_start(s, USER_A)
    assert created and d.name == "Mein Start" and d.source_template_id is None
    assert len(d.widgets) == 11
    tabs = await env.list(USER_A)
    assert [t.kind for t in tabs] == ["personal", "template"]  # own first, template kept


# ------------------------------------------------------------------ visibility


async def test_locked_template_is_listed_read_only_for_role_members(env: Env) -> None:
    tpl = await env.make_template(name="dash497 Pflicht", role_ids=[ROLE_1], locked=True)
    tabs = await env.list(USER_A)
    assert [t.id for t in tabs if t.kind == "template"] == [tpl.id]
    t = next(t for t in tabs if t.id == tpl.id)
    assert (t.can_edit, t.locked, t.is_owner) == (False, True, False)
    assert all(t.kind != "template" for t in await env.list(USER_C))
    with pytest.raises(DashboardNotFound):
        await env.get(tpl.id, USER_C)
    with pytest.raises(DashboardForbidden):
        await env.save(tpl.id, USER_A, env.body_of(tpl))


async def test_unlocked_template_is_invisible_but_admin_reads_and_edits_it(env: Env) -> None:
    from tiqora.dashboard import store

    tpl = await env.make_template(name="dash497 frei", role_ids=[ROLE_1], locked=False)
    assert all(t.id != tpl.id for t in await env.list(USER_A))
    with pytest.raises(DashboardNotFound):
        await env.get(tpl.id, USER_A)
    seen = await env.run(store.get_dashboard, tpl.id, 1, admin=True)
    assert seen.can_edit
    saved = await env.run(store.save_dashboard, tpl.id, 1, env.body_of(tpl), admin=True)
    assert saved.version == 2


async def test_shared_dashboard_is_visible_to_group_members_read_only(env: Env) -> None:
    d = await env.make_personal(USER_A, name="dash497 Team", share_group_ids=[GROUP_2])
    tab = next(t for t in await env.list(USER_B) if t.id == d.id)
    assert tab.kind == "shared" and tab.can_edit is False and tab.can_share is False
    assert tab.owner_name == "Anna Alpha" and tab.group_ids == []  # viewers learn no group ids
    assert all(t.id != d.id for t in await env.list(USER_C))
    own = next(t for t in await env.list(USER_A) if t.id == d.id)
    assert own.is_owner and own.group_ids == [GROUP_2] and own.owner_name is None
    with pytest.raises(DashboardForbidden):
        await env.save(d.id, USER_B, env.body_of(d))


async def test_invisible_dashboards_look_like_missing_ones(env: Env) -> None:
    from tiqora.dashboard import store

    shared = await env.make_personal(USER_A, name="dash497 nur B", share_group_ids=[GROUP_2])
    missing = 2_000_000_000
    for did in (shared.id, env.foreign_dashboard_id, missing):
        calls = [
            env.get(did, USER_C),
            env.save(did, USER_C, env.body_of(shared)),
            env.patch(did, "nnnnnnnn", USER_C, {"markdown": "x"}, version=1),
            env.delete(did, USER_C),
            env.create(USER_C, DashboardCreateIn(name="x", copy_from=did)),
        ]
        for call in calls:
            with pytest.raises(DashboardNotFound):
                await call
    # a visible-but-foreign dashboard is the only case that answers differently
    with pytest.raises(DashboardForbidden):
        await env.delete(shared.id, USER_B)
    assert store is not None


# ------------------------------------------------------------------ save and lock


async def test_save_with_stale_version_is_409(env: Env) -> None:
    d = await env.make_personal(USER_A, name="dash497 a")
    body = env.body_of(d)
    await env.save(d.id, USER_A, body)  # version 1 -> 2
    with pytest.raises(DashboardConflict) as e:
        await env.save(d.id, USER_A, body)  # still sends version 1
    assert e.value.current_version == 2


async def test_concurrent_saves_with_the_same_version_let_one_win(env: Env) -> None:
    d = await env.make_personal(USER_A, name="dash497 race")
    body = env.body_of(d)
    results = await asyncio.gather(
        env.save(d.id, USER_A, body), env.save(d.id, USER_A, body), return_exceptions=True
    )
    conflicts = [r for r in results if isinstance(r, DashboardConflict)]
    assert len(conflicts) == 1 and len(results) == 2
    winner = next(r for r in results if not isinstance(r, BaseException))
    assert conflicts[0].current_version == winner.version == 2  # not a stale snapshot
    assert (await env.get(d.id, USER_A)).version == 2


async def test_save_replaces_widgets_and_orders_by_list_index(env: Env) -> None:
    d = await env.make_personal(
        USER_A,
        name="dash497 b",
        widgets=[w("aaaaaaaa", "core.counter"), w("bbbbbbbb", "core.links")],
    )
    out = await env.save(
        d.id,
        USER_A,
        env.body_of(d, widgets=[w("bbbbbbbb", "core.links"), w("cccccccc", "core.notes")]),
    )
    assert [x.id for x in out.widgets] == ["bbbbbbbb", "cccccccc"] and out.version == 2
    assert await env.widget_ids(d.id) == {"bbbbbbbb", "cccccccc"}  # aaaaaaaa deleted


async def test_save_keeps_unknown_widget_types(env: Env) -> None:
    d = await env.make_personal(USER_A, name="dash497 u")
    out = await env.save(
        d.id,
        USER_A,
        env.body_of(d, widgets=[w("dddddddd", "org.example.weather", {"city": "Bonn"})]),
    )
    assert out.widgets[0].type_key == "org.example.weather"
    assert out.widgets[0].settings == {"city": "Bonn"}
    again = await env.save(d.id, USER_A, env.body_of(out))
    assert again.widgets[0].settings == {"city": "Bonn"}


async def test_save_applies_settings_defaults_for_core_types(env: Env) -> None:
    d = await env.make_personal(USER_A, name="dash497 defaults")
    out = await env.save(
        d.id, USER_A, env.body_of(d, widgets=[w("hhhhhhhh", "core.notes", {"markdown": "Hi"})])
    )
    assert out.widgets[0].settings == {"markdown": "Hi"}
    assert (await env.get(d.id, USER_A)).widgets[0].settings == {"markdown": "Hi"}


async def test_save_normalises_hostile_layout(env: Env) -> None:
    d = await env.make_personal(USER_A, name="dash497 lay")
    out = await env.save(
        d.id,
        USER_A,
        env.body_of(
            d,
            widgets=[
                w("eeeeeeee", "core.counter", lg=(10, 0, 6, 2), md=(6, 0, 6, 2)),
                w("ffffffff", "core.counter", lg=(6, 0, 6, 2)),
            ],
        ),
    )
    assert out.widgets[0].layout.lg == Rect(x=6, y=0, w=6, h=2)
    assert out.widgets[1].layout.lg.y == 2
    assert out.widgets[0].layout.md == Rect(x=2, y=0, w=6, h=2)  # 8-column clamp
    assert out.widgets[1].layout.md is None
    stored = await env.get(d.id, USER_A)
    assert stored.widgets[0].layout == out.widgets[0].layout


async def test_layout_that_is_too_tall_is_invalid(env: Env) -> None:
    d = await env.make_personal(USER_A, name="dash497 tall")
    with pytest.raises(DashboardInvalid) as e:
        await env.save(
            d.id,
            USER_A,
            env.body_of(d, widgets=[w("iiiiiiii", "core.counter", lg=(0, 990, 6, 24))]),
        )
    assert e.value.message == "Das Layout ist zu hoch." and e.value.path == "widgets"


async def test_widget_id_of_another_dashboard_is_rejected(env: Env) -> None:
    await env.make_personal(USER_A, name="dash497 one", widgets=[w("aaaaaaaa", "core.counter")])
    d2 = await env.make_personal(USER_A, name="dash497 two")
    with pytest.raises(DashboardInvalid) as e:
        await env.save(d2.id, USER_A, env.body_of(d2, widgets=[w("aaaaaaaa", "core.counter")]))
    assert e.value.path == "widgets[0].id"
    assert (await env.get(d2.id, USER_A)).version == 1  # nothing changed


async def test_invalid_core_settings_report_a_path(env: Env) -> None:
    d = await env.make_personal(USER_A, name="dash497 bad")
    with pytest.raises(DashboardInvalid) as e:
        await env.save(
            d.id,
            USER_A,
            env.body_of(d, widgets=[w("gggggggg", "core.ticket-list", {"limit": 99})]),
        )
    assert e.value.path == "widgets[0].settings.limit"
    assert (await env.get(d.id, USER_A)).version == 1


async def test_patch_widget_settings_checks_rights_and_version(env: Env) -> None:
    d = await env.make_personal(
        USER_A,
        name="dash497 n",
        widgets=[w("nnnnnnnn", "core.notes", {"markdown": "alt"})],
        share_group_ids=[GROUP_2],
    )
    out = await env.patch(d.id, "nnnnnnnn", USER_A, {"markdown": "neu"}, version=d.version)
    assert out.settings == {"markdown": "neu"}
    assert (await env.get(d.id, USER_A)).version == d.version + 1
    with pytest.raises(DashboardConflict):
        await env.patch(d.id, "nnnnnnnn", USER_A, {"markdown": "x"}, version=d.version)
    with pytest.raises(DashboardForbidden):
        await env.patch(d.id, "nnnnnnnn", USER_B, {"markdown": "x"}, version=d.version + 1)
    with pytest.raises(DashboardInvalid) as e:
        await env.patch(d.id, "nnnnnnnn", USER_A, {"markdown": "a" * 5001}, version=d.version + 1)
    assert e.value.path == "settings.markdown"
    assert (await env.get(d.id, USER_A)).widgets[0].settings == {"markdown": "neu"}
    other = await env.make_personal(USER_A, name="dash497 n2")
    with pytest.raises(DashboardNotFound):  # widget of another dashboard
        await env.patch(other.id, "nnnnnnnn", USER_A, {"markdown": "x"}, version=1)


# ------------------------------------------------------------------ limits, copy, delete


async def test_dashboard_limit_and_order(env: Env) -> None:
    ids = [(await env.make_personal(USER_C, name=f"dash497 c{i}")).id for i in range(20)]
    with pytest.raises(DashboardInvalid) as e:
        await env.make_personal(USER_C, name="dash497 c20")
    assert "höchstens 20" in e.value.message
    await env.set_order(USER_C, list(reversed(ids)))
    assert [t.id for t in await env.list(USER_C)] == list(reversed(ids))
    with pytest.raises(DashboardForbidden):
        await env.set_order(USER_C, [ids[0], env.foreign_dashboard_id])


async def test_copy_from_visible_dashboard_gets_new_widget_ids(env: Env) -> None:
    src = await env.make_personal(
        USER_A,
        name="dash497 src",
        widgets=[w("ssssssss", "core.links", {"items": []})],
        share_group_ids=[GROUP_2],
    )
    cp = await env.create(USER_B, DashboardCreateIn(name="dash497 Kopie", copy_from=src.id))
    assert cp.kind == "personal" and cp.is_owner and cp.id != src.id
    assert [x.type_key for x in cp.widgets] == ["core.links"]
    assert cp.widgets[0].id != "ssssssss" and cp.widgets[0].settings == src.widgets[0].settings
    with pytest.raises(DashboardNotFound):
        await env.create(USER_C, DashboardCreateIn(name="x", copy_from=src.id))


async def test_delete_removes_widgets_and_hides_for_others(env: Env) -> None:
    await env.make_personal(USER_A, name="dash497 keep")
    d = await env.make_personal(
        USER_A, name="dash497 del", widgets=[w("dddddddd", "core.links")], share_group_ids=[GROUP_2]
    )
    with pytest.raises(DashboardForbidden):
        await env.delete(d.id, USER_B)
    await env.delete(d.id, USER_A)
    assert await env.widget_ids(d.id) == set()
    assert await env.count_rows("tiqora_dashboard_group", dashboard_id=d.id) == 0
    assert all(t.id != d.id for t in await env.list(USER_B))
    with pytest.raises(DashboardNotFound):
        await env.get(d.id, USER_A)


async def test_the_last_dashboard_cannot_be_deleted(env: Env) -> None:
    only = await env.make_personal(USER_A, name="dash497 only")
    with pytest.raises(DashboardInvalid) as e:
        await env.delete(only.id, USER_A)
    assert e.value.message == "Das letzte Dashboard bleibt bestehen." and e.value.path == "id"
    assert (await env.get(only.id, USER_A)).id == only.id
    await env.make_personal(USER_A, name="dash497 second")
    await env.delete(only.id, USER_A)
    # a locked template tab does not count as an owned dashboard
    await env.make_template(name="dash497 Pflicht", role_ids=[ROLE_1], locked=True)
    (left,) = [t for t in await env.list(USER_A) if t.is_owner]
    with pytest.raises(DashboardInvalid):
        await env.delete(left.id, USER_A)


# ------------------------------------------------------------------ races


async def test_two_creates_at_19_let_exactly_one_through(env: Env) -> None:
    for i in range(19):
        await env.make_personal(USER_C, name=f"dash497 r{i}")
    results = await asyncio.gather(
        env.create(USER_C, DashboardCreateIn(name="dash497 x1")),
        env.create(USER_C, DashboardCreateIn(name="dash497 x2")),
        return_exceptions=True,
    )
    assert sum(isinstance(r, DashboardInvalid) for r in results) == 1
    assert len(await env.count_dashboards(USER_C)) == 20


async def test_two_deletes_cannot_remove_the_last_two_dashboards(env: Env) -> None:
    a = await env.make_personal(USER_C, name="dash497 d1")
    b = await env.make_personal(USER_C, name="dash497 d2")
    results = await asyncio.gather(
        env.delete(a.id, USER_C), env.delete(b.id, USER_C), return_exceptions=True
    )
    assert sum(isinstance(r, DashboardInvalid) for r in results) == 1
    assert len(await env.count_dashboards(USER_C)) == 1


def _db_error(orig: BaseException) -> DBAPIError:
    return OperationalError("stmt", {}, orig)


def test_race_errors_are_classified() -> None:
    class Pg(Exception):
        sqlstate = "40P01"

    assert is_race_error(IntegrityError("s", {}, Exception("dup")))
    assert is_race_error(_db_error(Exception(1213, "Deadlock found")))
    assert is_race_error(_db_error(Exception(1205, "Lock wait timeout")))
    assert is_race_error(_db_error(Pg()))
    assert not is_race_error(_db_error(Exception(2013, "Lost connection")))
    assert not is_race_error(_db_error(Exception("boom")))


async def _fail_once(
    env: Env, monkeypatch: pytest.MonkeyPatch, error: DBAPIError
) -> tuple[int, Any]:
    calls = 0
    real = start.default_widgets

    def flaky(*args: Any, **kw: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error
        return real(*args, **kw)

    monkeypatch.setattr(start, "default_widgets", flaky)
    try:
        result = await env.run(ensure_start, USER_B)
    except DBAPIError as exc:
        result = exc
    return calls, result


async def test_ensure_start_retries_a_deadlock(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, result = await _fail_once(env, monkeypatch, _db_error(Exception(1213, "Deadlock")))
    assert calls == 2 and result[1] is True


async def test_ensure_start_does_not_retry_other_errors(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, result = await _fail_once(env, monkeypatch, _db_error(Exception(2013, "gone")))
    assert calls == 1 and isinstance(result, DBAPIError)
    assert await env.count_dashboards(USER_B) == []
