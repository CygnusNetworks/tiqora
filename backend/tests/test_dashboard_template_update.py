"""Template update merge (E8) in the store and the API (both dialects).

Seed band 61380-61409 is reserved for this module; it needs no numeric ids of its own
(templates and copies are autoincrement rows named ``dash3 ...``, widget ids are
``dash3t...`` strings) and runs in the shared world of ``_dashboard3_helpers``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests._dashboard3_helpers import ADMIN, GROUP_2, ROLE_A, A, B, Env3, open_env3
from tiqora.dashboard import start, store, template_update
from tiqora.dashboard.filters import DashboardFilters
from tiqora.dashboard.layout import Rect
from tiqora.dashboard.schemas import (
    DashboardCreateIn,
    DashboardOut,
    DashboardSaveIn,
    DashboardTemplateIn,
    WidgetIn,
    WidgetLayouts,
)

pytestmark = pytest.mark.db


@pytest.fixture(params=["mariadb_znuny_url", "postgres_znuny_url"])
async def env(request: pytest.FixtureRequest) -> AsyncIterator[Env3]:
    async with open_env3(request.getfixturevalue(request.param)) as e:
        yield e


def _notes(wid: str, *, y: int = 100, text: str = "") -> WidgetIn:
    return WidgetIn(
        id=wid,
        type_key="core.notes",
        settings={"markdown": text},
        layout=WidgetLayouts(lg=Rect(x=0, y=y, w=4, h=2)),
    )


def _save_in(d: DashboardOut, extra: list[WidgetIn], *, drop: frozenset[str] = frozenset()) -> Any:
    keep = [WidgetIn.model_validate(w.model_dump()) for w in d.widgets if w.id not in drop]
    return DashboardSaveIn(name=d.name, icon=d.icon, version=d.version, widgets=[*keep, *extra])


async def _template(env: Env3, *, locked: bool = False) -> DashboardOut:
    created = await env.run(
        store.create_template,
        ADMIN,
        DashboardTemplateIn(name="dash3 tpl", role_ids=[ROLE_A], locked=locked),
    )
    return await env.get(ADMIN, created.id)


async def _template_add(env: Env3, tpl_id: int, *new: WidgetIn) -> DashboardOut:
    cur = await env.get(ADMIN, tpl_id)
    return await env.run(  # type: ignore[no-any-return]
        store.save_dashboard, tpl_id, ADMIN, _save_in(cur, list(new)), admin=True
    )


async def _copy(env: Env3) -> DashboardOut:
    out, created = await env.run(start.ensure_start, A)
    assert created
    return out  # type: ignore[no-any-return]


async def _save_copy(env: Env3, d: DashboardOut, extra: list[WidgetIn], **kw: Any) -> DashboardOut:
    return await env.run(  # type: ignore[no-any-return]
        store.save_dashboard, d.id, A, _save_in(d, extra, **kw), admin=False
    )


def _rows(env: Env3, dashboard_id: int) -> list[tuple[Any, ...]]:
    return [
        tuple(r)
        for r in env.sql(
            "SELECT id, type_key, type_version, title_override, settings, layout, position,"
            " pinned, template_widget_id, added_in_version FROM tiqora_dashboard_widget"
            " WHERE dashboard_id = :d ORDER BY position, id",
            d=dashboard_id,
        )
    ]


def _link(env: Env3, dashboard_id: int) -> tuple[Any, Any, Any]:
    r = env.sql(
        "SELECT source_template_id, source_template_version, version FROM tiqora_dashboard"
        " WHERE id = :d",
        d=dashboard_id,
    )[0]
    return r[0], r[1], r[2]


async def _api_get(env: Env3, user: int, d: int) -> DashboardOut:
    r = await env.as_user(user).get(f"/api/v1/dashboards/{d}")
    assert r.status_code == 200, r.text
    return DashboardOut.model_validate(r.json())


def _tu(env: Env3, user: int, d: int) -> Any:
    return env.as_user(user).get(f"/api/v1/dashboards/{d}/template-update")


def _post(env: Env3, user: int, d: int, action: str, version: int) -> Any:
    return env.as_user(user).post(
        f"/api/v1/dashboards/{d}/template-update", json={"action": action, "version": version}
    )


async def test_first_visit_copy_records_template_widget_ids(env: Env3) -> None:
    tpl = await _template(env)
    tpl = await env.run(
        store.set_filters,
        tpl.id,
        ADMIN,
        DashboardFilters(queue_ids=[1]),
        tpl.version,
        admin=True,
    )
    copy = await _copy(env)
    assert _link(env, copy.id)[:2] == (tpl.id, tpl.version)
    tpl_ids = {w.id for w in tpl.widgets}
    rows = _rows(env, copy.id)
    assert len(rows) == len(tpl.widgets)
    assert {r[8] for r in rows} == tpl_ids
    assert all(r[0] not in tpl_ids and r[9] is None for r in rows)
    assert copy.filters.queue_ids == [1]  # ruling: a template copy carries the template filters

    # a duplicate of the template (admin) is linked the same way and keeps the filters
    dup = await env.run(
        store.create_dashboard,
        ADMIN,
        DashboardCreateIn(name="dash3 dup", copy_from=tpl.id),
        admin=True,
    )
    assert _link(env, dup.id)[:2] == (tpl.id, tpl.version)
    assert {r[8] for r in _rows(env, dup.id)} == tpl_ids and dup.filters.queue_ids == [1]
    # a copy of a personal dashboard carries no template link
    plain = await env.run(
        store.create_dashboard,
        A,
        DashboardCreateIn(name="dash3 plain", copy_from=copy.id),
        admin=False,
    )
    assert _link(env, plain.id)[:2] == (None, None)
    assert {r[8] for r in _rows(env, plain.id)} == {None}
    assert plain.filters.queue_ids == [1]


async def test_template_save_records_added_in_version(env: Env3) -> None:
    tpl = await _template(env)
    saved = await _template_add(env, tpl.id, _notes("dash3tnew00001"))
    rows = {r[0]: r for r in _rows(env, tpl.id)}
    assert rows["dash3tnew00001"][9] == saved.version == 2
    assert all(r[9] is None for wid, r in rows.items() if wid != "dash3tnew00001")
    again = await env.run(  # re-saving keeps the original version of an existing widget
        store.save_dashboard, tpl.id, ADMIN, _save_in(saved, []), admin=True
    )
    assert again.version == 3
    assert {r[0]: r[9] for r in _rows(env, tpl.id)}["dash3tnew00001"] == 2


async def test_widgets_added_to_template_are_offered(env: Env3) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    assert (await _api_get(env, A, copy.id)).template_update is None
    assert (await _tu(env, A, copy.id)).status_code == 404
    await _template_add(env, tpl.id, _notes("dash3tnew00001", text="Neu"))

    got = await _api_get(env, A, copy.id)
    hint = got.template_update
    assert hint is not None and hint.template_id == tpl.id and hint.new_widget_count == 1
    assert hint.template_version == 2 and hint.template_name == "dash3 tpl"

    r = await _tu(env, A, copy.id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["template_version"] == 2 and len(body["widgets"]) == 1
    w = body["widgets"][0]
    assert w["id"] != "dash3tnew00001" and w["settings"] == {"markdown": "Neu"}
    assert w["layout"]["md"] is None
    lg = w["layout"]["lg"]
    for other in got.widgets:  # placed in a free spot of the copy
        o = other.layout.lg
        assert not (
            lg["x"] < o.x + o.w
            and o.x < lg["x"] + lg["w"]
            and lg["y"] < o.y + o.h
            and o.y < lg["y"] + lg["h"]
        )
    assert len(_rows(env, copy.id)) == len(copy.widgets)  # the preview writes nothing


async def test_merge_appends_only_new_template_widgets(env: Env3) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    await _template_add(env, tpl.id, _notes("dash3tnew00001", text="eins"))
    r = await _post(env, A, copy.id, "merge", copy.version)
    assert r.status_code == 200, r.text
    out = DashboardOut.model_validate(r.json())
    assert len(out.widgets) == len(copy.widgets) + 1
    assert out.version == copy.version + 1 and out.template_update is None
    rows = _rows(env, copy.id)
    last = rows[-1]
    assert last[8] == "dash3tnew00001" and last[0] != "dash3tnew00001"
    assert last[6] == len(copy.widgets) and json.loads(last[4]) == {"markdown": "eins"}
    assert _link(env, copy.id) == (tpl.id, 2, copy.version + 1)
    assert (await _api_get(env, A, copy.id)).template_update is None
    assert (await _tu(env, A, copy.id)).status_code == 404  # nothing left: not offered twice

    await _template_add(env, tpl.id, _notes("dash3tnew00002", text="zwei", y=200))
    again = (await _api_get(env, A, copy.id)).template_update
    assert again is not None and again.new_widget_count == 1  # only the newest one
    widgets = (await _tu(env, A, copy.id)).json()["widgets"]
    assert [w["settings"] for w in widgets] == [{"markdown": "zwei"}]


async def test_merge_never_moves_or_changes_user_widgets(env: Env3) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    moved = [
        w.model_copy(
            update={
                "layout": WidgetLayouts(
                    lg=Rect(x=w.layout.lg.x, y=w.layout.lg.y + 40, w=w.layout.lg.w, h=w.layout.lg.h)
                )
            }
        )
        for w in copy.widgets
    ]
    edited = copy.model_copy(update={"widgets": moved})
    mine = _notes("dash3tmine0001", y=300, text="meins")
    copy = await _save_copy(env, edited, [mine])
    before = _rows(env, copy.id)
    await _template_add(env, tpl.id, _notes("dash3tnew00001"))
    r = await _post(env, A, copy.id, "merge", copy.version)
    assert r.status_code == 200, r.text
    after = _rows(env, copy.id)
    assert after[: len(before)] == before  # byte-identical: id, settings, layout, position
    assert len(after) == len(before) + 1 and after[-1][8] == "dash3tnew00001"
    assert after[-1][6] == max(b[6] for b in before) + 1


async def test_removed_copy_widget_is_not_offered_again(env: Env3) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    gone = copy.widgets[0].id
    gone_tpl = next(r[8] for r in _rows(env, copy.id) if r[0] == gone)
    copy = await _save_copy(env, copy, [], drop=frozenset({gone}))
    assert gone_tpl in {w.id for w in tpl.widgets}
    # the template changes (a new widget) but the removed one is older than the copy
    await _template_add(env, tpl.id, _notes("dash3tnew00001"))
    hint = (await _api_get(env, A, copy.id)).template_update
    assert hint is not None and hint.new_widget_count == 1
    r = await _post(env, A, copy.id, "merge", copy.version)
    assert r.status_code == 200
    assert gone_tpl not in {row[8] for row in _rows(env, copy.id)}


async def test_dismiss_hides_until_next_template_change(env: Env3) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    await _template_add(env, tpl.id, _notes("dash3tnew00001", text="x"))
    before = _rows(env, copy.id)
    r = await _post(env, A, copy.id, "dismiss", copy.version)
    assert r.status_code == 200, r.text
    assert _rows(env, copy.id) == before  # nothing added
    assert _link(env, copy.id) == (tpl.id, 2, copy.version + 1)
    assert (await _api_get(env, A, copy.id)).template_update is None
    assert (await _tu(env, A, copy.id)).status_code == 404

    await _template_add(env, tpl.id, _notes("dash3tnew00002", text="y", y=200))
    hint = (await _api_get(env, A, copy.id)).template_update
    assert hint is not None and hint.new_widget_count == 1 and hint.template_version == 3
    assert [w["settings"] for w in (await _tu(env, A, copy.id)).json()["widgets"]] == [
        {"markdown": "y"}
    ]


async def test_stale_version_409_and_widget_limit_422(env: Env3) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    await _template_add(env, tpl.id, _notes("dash3tnew00001"))
    stale = await _post(env, A, copy.id, "merge", copy.version + 5)
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == copy.version
    assert _rows(env, copy.id) and len(_rows(env, copy.id)) == len(copy.widgets)

    full = await _save_copy(
        env,
        copy,
        [_notes(f"dash3tfull{i:05d}", y=100 + i * 2) for i in range(40 - len(copy.widgets))],
    )
    assert len(full.widgets) == 40
    before = (_rows(env, copy.id), _link(env, copy.id))
    r = await _post(env, A, copy.id, "merge", full.version)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {
        "message": "Das Dashboard hätte dann mehr als 40 Widgets.",
        "path": "widgets",
    }
    assert (_rows(env, copy.id), _link(env, copy.id)) == before  # nothing changed
    # dismissing is still possible when the copy is full
    assert (await _post(env, A, copy.id, "dismiss", full.version)).status_code == 200


async def test_deleted_locked_or_unassigned_template_offers_nothing(env: Env3) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    await _template_add(env, tpl.id, _notes("dash3tnew00001"))

    async def offered() -> bool:
        has_hint = (await _api_get(env, A, copy.id)).template_update is not None
        status = (await _tu(env, A, copy.id)).status_code
        assert status == (200 if has_hint else 404)
        return has_hint

    assert await offered()
    # A loses role R
    env.sql("DELETE FROM role_user WHERE user_id = :u AND role_id = :r", u=A, r=ROLE_A)
    assert not await offered()
    env.sql(
        "INSERT INTO role_user (user_id, role_id, create_time, create_by, change_time, change_by)"
        " VALUES (:u, :r, '2026-10-01 08:00:00', 1, '2026-10-01 08:00:00', 1)",
        u=A,
        r=ROLE_A,
    )
    assert await offered()
    # the template is locked (it is then a tab, not a source)
    await env.run(
        store.update_template,
        tpl.id,
        DashboardTemplateIn(name="dash3 tpl", role_ids=[ROLE_A], locked=True),
    )
    assert not await offered()
    await env.run(
        store.update_template,
        tpl.id,
        DashboardTemplateIn(name="dash3 tpl", role_ids=[ROLE_A], locked=False),
    )
    assert await offered()
    # the template is deleted: the copy lives on, nothing is offered, the merge is 404
    await env.run(store.delete_template, tpl.id)
    assert not await offered()
    assert (await _post(env, A, copy.id, "merge", copy.version)).status_code == 404
    assert (await _post(env, A, copy.id, "dismiss", copy.version)).status_code == 404


async def test_only_owner_sees_hint(env: Env3) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    await _template_add(env, tpl.id, _notes("dash3tnew00001"))
    env.share(copy.id, GROUP_2)  # B has ``ro`` on GROUP_2: sees the dashboard, cannot edit
    seen = await _api_get(env, B, copy.id)
    assert seen.template_update is None and seen.can_edit is False
    assert (await _tu(env, B, copy.id)).status_code == 404
    assert (await _post(env, B, copy.id, "merge", copy.version)).status_code == 404
    assert _link(env, copy.id)[1] == 1 and len(_rows(env, copy.id)) == len(copy.widgets)
    # not even an admin gets another user's offer
    assert (await env.as_user(ADMIN).get(f"/api/v1/dashboards/{copy.id}")).status_code == 404
    assert (await _tu(env, ADMIN, copy.id)).status_code == 404


async def test_data_paths_do_no_template_read(env: Env3, monkeypatch: pytest.MonkeyPatch) -> None:
    tpl = await _template(env)
    copy = await _copy(env)
    await _template_add(env, tpl.id, _notes("dash3tnew00001"))
    calls: list[int] = []
    real = template_update.update_hint

    async def spy(session: Any, dashboard_id: int, viewer: int) -> Any:
        calls.append(dashboard_id)
        return await real(session, dashboard_id, viewer)

    monkeypatch.setattr(template_update, "update_hint", spy)
    client = env.as_user(A)
    wid = copy.widgets[0].id
    assert (await client.get(f"/api/v1/dashboards/{copy.id}/data")).status_code == 200
    assert (await client.get(f"/api/v1/dashboards/{copy.id}/widgets/{wid}/data")).status_code == 200
    assert calls == []
    assert (await _api_get(env, A, copy.id)).template_update is not None
    assert calls == [copy.id]
