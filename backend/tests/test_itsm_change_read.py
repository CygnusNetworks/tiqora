"""Change read contract (``itsm.change.read``): list, my tasks, calendar, PSA and the agent API
(both dialects).

Seed band 947300-947399: services 947330-947331, CI ids 947340-947341 (impact rows only, no CI
rows needed). Tickets are created by the change service (autoincrement, rolled back with the
world). Users, queues and the world are the shared ones of ``tests._change_fixtures`` (``Q_B`` is
out of reach of ``U_FULL``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update

from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    G_B,
    Q_A,
    Q_B,
    U_FULL,
    U_MANAGER,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tests._cmdb_helpers import make_ci, make_class
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.cmdb.links import LinkEnd, LinkService
from tiqora.cmdb.seed import seed_defaults
from tiqora.cmdb.service import system_access
from tiqora.config import Settings
from tiqora.db.tiqora.models import TiqoraSettings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change import read as read_module
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.freeze import FreezeIn, create_freeze
from tiqora.itsm.change.impact import recompute_impact
from tiqora.itsm.change.models import TiqoraItsmChange, TiqoraItsmChangeImpact
from tiqora.itsm.change.read import (
    LIST_SCAN_CAP,
    ChangeListItem,
    ChangePage,
    ChangeQuery,
    ChangeRef,
    ChangeTaskRef,
    active_freezes,
    calendar,
    change_refs_for_tickets,
    list_changes,
    my_tasks,
    phase_counts,
    service_availability,
    task_refs_for_tickets,
    upcoming_changes,
)
from tiqora.itsm.change.service import ChangeCreateIn, create_change
from tiqora.itsm.change.tasks import TaskIn, add_task
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

D = datetime(2026, 11, 10)
WIN = (D.replace(hour=8), D.replace(hour=12))
SVC_A, SVC_B, SVC_C = 947330, 947331, 947332
RANGE = (D - timedelta(days=1), D + timedelta(days=2))


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        for sid, name in ((SVC_A, "chg94-service-a"), (SVC_B, "chg94-service-b")):
            await world.session.execute(
                text(
                    "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                    " change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": sid, "n": name, "t": datetime(2026, 10, 1)},
            )
        for key in ("ro", "rw"):  # U_MANAGER reads Q_A and Q_B, U_FULL only Q_A
            await world.session.execute(
                text(
                    "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                    " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
                ),
                {"u": U_MANAGER, "g": G_B, "k": key, "t": datetime(2026, 10, 1)},
            )
        yield world


async def _change(
    w: ChangeWorld,
    *,
    title: str = "Switch-Tausch",
    queue: int = Q_A,
    change_type: str = "normal",
    phase: str = "scheduled",
    window: tuple[datetime, datetime] | None = WIN,
    services: tuple[int, ...] = (),
    cis: tuple[int, ...] = (),
    creator: int = U_FULL,
    owner: int | None = None,
) -> int:
    data = ChangeCreateIn.model_validate(
        {"title": title, "change_type": change_type, "queue_id": Q_A}
    )
    view, _ = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=data,
        user_id=U_FULL,
        session_factory=w.factory,
    )
    tid = view.record.ticket_id
    if queue != Q_A:  # the creator cannot create there; move the ticket out of reach afterwards
        await w.session.execute(
            text("UPDATE ticket SET queue_id = :q WHERE id = :t"), {"q": queue, "t": tid}
        )
    if owner is not None:
        await w.session.execute(
            text("UPDATE ticket SET user_id = :o WHERE id = :t"), {"o": owner, "t": tid}
        )
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.phase = phase
    row.created_by = creator
    if window is not None:
        row.planned_start, row.planned_end = window
    await w.session.execute(
        text("DELETE FROM tiqora_itsm_change_impact WHERE ticket_id = :t"), {"t": tid}
    )
    w.session.add_all(
        [
            TiqoraItsmChangeImpact(ticket_id=tid, kind="service", ref_key=str(s), direct=True)
            for s in services
        ]
        + [
            TiqoraItsmChangeImpact(ticket_id=tid, kind="ci", ref_key=str(c), direct=True)
            for c in cis
        ]
    )
    await w.session.flush()
    return tid


async def _real_cis(w: ChangeWorld, *names: str) -> list[int]:
    """CIs of a class in group ``G_A`` (readable for ``U_FULL``): the ``ci_id`` filter only
    matches CIs the caller can read."""
    await seed_defaults(w.session)
    class_id = await make_class(w.session, key="chg94srv", group_id=G_A)
    return [await make_ci(w.session, class_id, n, user_id=U_FULL) for n in names]


async def _set_mode(w: ChangeWorld, mode: str) -> None:
    await w.session.execute(
        update(TiqoraSettings).where(TiqoraSettings.key == "itsm.mode").values(value=mode)
    )


async def _ids(w: ChangeWorld, query: ChangeQuery, *, user: int = U_FULL) -> list[int]:
    page = await list_changes(w.session, query, user_id=user)
    return [i.ticket_id for i in page.items]


async def test_list_filters_phase_type_window_service_ci(w: ChangeWorld) -> None:
    CI_A, CI_B = await _real_cis(w, "srv-a.example.org", "srv-b.example.org")
    a = await _change(w, phase="scheduled", services=(SVC_A,), cis=(CI_A,))
    b = await _change(
        w,
        change_type="emergency",
        phase="assessment",
        window=(WIN[0] + timedelta(days=1), WIN[1] + timedelta(days=1)),
        services=(SVC_B,),
        cis=(CI_B,),
    )
    c = await _change(w, phase="draft", window=None)
    assert sorted(await _ids(w, ChangeQuery())) == [a, b, c]
    assert await _ids(w, ChangeQuery(phases=("scheduled",))) == [a]
    assert await _ids(w, ChangeQuery(phases=("scheduled", "draft"), order="planned_start")) == [
        a,
        c,
    ]
    assert await _ids(w, ChangeQuery(change_types=("emergency",))) == [b]
    assert await _ids(w, ChangeQuery(window_start=WIN[0], window_end=WIN[1])) == [a]
    # half-open: a change that ends where the window starts is not in it
    assert await _ids(w, ChangeQuery(window_start=WIN[1], window_end=WIN[1] + timedelta(1))) == [b]
    assert await _ids(w, ChangeQuery(service_id=SVC_B)) == [b]
    assert await _ids(w, ChangeQuery(ci_id=CI_A)) == [a]
    assert await _ids(w, ChangeQuery(service_id=SVC_A, ci_id=CI_B)) == []
    paged = await list_changes(w.session, ChangeQuery(limit=2, offset=2), user_id=U_FULL)
    assert (len(paged.items), paged.total, paged.truncated) == (1, 3, False)


async def test_list_text_search_tn_title(w: ChangeWorld) -> None:
    a = await _change(w, title="Router 100%_x tauschen")
    b = await _change(w, title="Switch tauschen")
    tn_a = (await list_changes(w.session, ChangeQuery(), user_id=U_FULL)).items
    tn = next(i.tn for i in tn_a if i.ticket_id == a)
    assert sorted(await _ids(w, ChangeQuery(text="tauschen"))) == [a, b]
    assert await _ids(w, ChangeQuery(text="  SWITCH ")) == [b]
    assert await _ids(w, ChangeQuery(text=tn)) == [a]
    # wildcard characters are plain text
    assert await _ids(w, ChangeQuery(text="%")) == [a]
    assert await _ids(w, ChangeQuery(text="_x")) == [a]
    assert await _ids(w, ChangeQuery(text="gibt-es-nicht")) == []


async def test_list_mine_owned_created(w: ChangeWorld) -> None:
    a = await _change(w)  # created and owned by U_FULL
    b = await _change(w, creator=1, owner=U_FULL)  # created by the admin, owned by U_FULL
    c = await _change(w, owner=U_MANAGER)  # created by U_FULL, owned by someone else
    assert sorted(await _ids(w, ChangeQuery(mine="owned"))) == [a, b]
    assert sorted(await _ids(w, ChangeQuery(mine="created"))) == [a, c]
    assert sorted(await _ids(w, ChangeQuery())) == [a, b, c]
    items = (await list_changes(w.session, ChangeQuery(), user_id=U_FULL)).items
    assert {i.ticket_id: i.owner_id for i in items} == {a: U_FULL, b: U_FULL, c: U_MANAGER}


async def test_list_truncated_flag_at_scan_cap(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert LIST_SCAN_CAP == 5000
    ids = [await _change(w) for _ in range(3)]
    monkeypatch.setattr(read_module, "LIST_SCAN_CAP", 2)
    page = await list_changes(w.session, ChangeQuery(), user_id=U_FULL)
    assert page.truncated is True
    assert sorted(i.ticket_id for i in page.items) == sorted(ids)[1:]  # the newest two
    monkeypatch.setattr(read_module, "LIST_SCAN_CAP", 3)
    exact = await list_changes(w.session, ChangeQuery(), user_id=U_FULL)
    assert exact.truncated is False and exact.total == 3


async def test_list_omits_unreadable_and_effective_risk(w: ChangeWorld) -> None:
    mine = await _change(w, services=(SVC_A,))
    hidden = await _change(w, queue=Q_B)
    row = await w.session.get(TiqoraItsmChange, mine)
    assert row is not None
    row.risk_level, row.risk_override_level = "low", "high"
    await w.session.flush()
    page = await list_changes(w.session, ChangeQuery(), user_id=U_FULL)
    assert [i.ticket_id for i in page.items] == [mine] and page.total == 1
    assert page.items[0].risk_level == "high"
    assert page.items[0].queue_id == Q_A
    assert sorted(await _ids(w, ChangeQuery(), user=U_MANAGER)) == [mine, hidden]


async def test_calendar_omits_unreadable_change(w: ChangeWorld) -> None:
    shown = await _change(w, services=(SVC_A,))
    hidden = await _change(w, queue=Q_B, services=(SVC_B,))
    gone = await _change(w, phase="cancelled")
    cal = await calendar(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1])
    assert [c.item.ticket_id for c in cal.changes] == [shown]
    assert gone not in [c.item.ticket_id for c in cal.changes]
    admin = await calendar(w.session, user_id=U_MANAGER, start=RANGE[0], end=RANGE[1])
    assert sorted(c.item.ticket_id for c in admin.changes) == [shown, hidden]
    other_day = await calendar(
        w.session, user_id=U_FULL, start=D + timedelta(days=3), end=D + timedelta(days=4)
    )
    assert other_day.changes == ()
    only_b = await calendar(
        w.session, user_id=U_MANAGER, start=RANGE[0], end=RANGE[1], service_ids=(SVC_B,)
    )
    assert [c.item.ticket_id for c in only_b.changes] == [hidden]


async def test_calendar_range_cap_422(w: ChangeWorld) -> None:
    start = D
    with pytest.raises(ChangeError) as err:
        await calendar(w.session, user_id=U_FULL, start=start, end=start + timedelta(days=93))
    assert (err.value.error_code, err.value.http_status) == ("range_too_large", 422)
    with pytest.raises(ChangeError) as back:
        await calendar(w.session, user_id=U_FULL, start=start, end=start)
    assert back.value.error_code == "range_invalid"
    ok = await calendar(w.session, user_id=U_FULL, start=start, end=start + timedelta(days=92))
    assert ok.changes == ()
    with pytest.raises(ChangeError):
        await service_availability(
            w.session, user_id=U_FULL, start=start, end=start + timedelta(days=93)
        )


async def test_calendar_contains_freezes_and_conflict_flags(w: ChangeWorld) -> None:
    a = await _change(w, services=(SVC_A,))
    b = await _change(w, services=(SVC_A,), window=(WIN[0] + timedelta(hours=1), WIN[1]))
    clear = await _change(
        w,
        services=(SVC_B,),
        window=(WIN[0] + timedelta(days=1), WIN[1] + timedelta(days=1)),
    )
    await _change(
        w,
        queue=Q_B,
        services=(SVC_B,),
        window=(WIN[0] + timedelta(days=1, hours=1), WIN[1] + timedelta(days=1)),
    )
    await create_freeze(
        w.session,
        data=FreezeIn.model_validate(
            {
                "name": "chg94-freeze",
                "starts_at": (WIN[0] - timedelta(hours=1)).isoformat() + "Z",
                "ends_at": (WIN[0] + timedelta(hours=1)).isoformat() + "Z",
                "scope": "services",
                "service_ids": [SVC_A],
            }
        ),
        user_id=U_MANAGER,
    )
    cal = await calendar(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1] + timedelta(1))
    flags = {c.item.ticket_id: (c.conflict_severity, c.conflict_kinds) for c in cal.changes}
    assert set(flags) == {a, b, clear}
    assert flags[a] == ("block", ("change_overlap", "freeze"))
    assert flags[b] == ("warn", ("change_overlap",))  # starts where the freeze ends
    # overlaps a change the caller cannot read: flagged, nothing else is revealed
    assert flags[clear] == ("warn", ("change_overlap",))
    assert [(f.name, f.scope, f.service_ids) for f in cal.freezes] == [
        ("chg94-freeze", "services", frozenset({SVC_A}))
    ]
    assert cal.truncated is False and cal.freezes_truncated is False
    assert [c.item.ticket_id for c in cal.changes] == [a, b, clear]  # by planned start
    assert [f.freeze_id for f in await active_freezes(w.session, start=RANGE[0], end=RANGE[1])] == [
        cal.freezes[0].freeze_id
    ]


async def test_calendar_conflict_pass_is_batched(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One impact load and one freeze expansion for the whole range, however many changes."""
    for _ in range(4):
        await _change(w, services=(SVC_A,))
    loads: list[int] = []
    real = read_module.impact_sets

    async def counting(session: Any, ids: Any) -> Any:
        loads.append(len(list(ids)))
        return await real(session, ids)

    monkeypatch.setattr(read_module, "impact_sets", counting)
    cal = await calendar(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1])
    assert len(cal.changes) == 4 and loads == [4]
    assert all(c.conflict_kinds == ("change_overlap",) for c in cal.changes)


async def test_calendar_tasks(w: ChangeWorld) -> None:
    change = await _change(w, services=(SVC_A,))
    other = await _change(w, services=(SVC_B,))
    inside = await add_task(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        change_ticket_id=change,
        data=TaskIn.model_validate(
            {
                "title": "Switch tauschen",
                "owner_id": U_FULL,
                "planned_start": WIN[0],
                "planned_end": WIN[1],
            }
        ),
        user_id=U_FULL,
        session_factory=w.factory,
    )
    await add_task(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        change_ticket_id=other,
        data=TaskIn.model_validate(
            {"title": "Kabel prüfen", "owner_id": U_FULL, "planned_start": WIN[0]}
        ),
        user_id=U_FULL,
        session_factory=w.factory,
    )
    cal = await calendar(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1])
    assert len(cal.tasks) == 2
    task = next(t for t in cal.tasks if t.ticket_id == inside.ticket_id)
    assert (task.change_ticket_id, task.owner_id) == (change, U_FULL)
    assert task.change_tn is not None
    filtered = await calendar(
        w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1], service_ids=(SVC_A,)
    )
    assert [t.ticket_id for t in filtered.tasks] == [inside.ticket_id]
    bare = await calendar(
        w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1], include_tasks=False
    )
    assert bare.tasks == ()


async def test_service_availability_rows(w: ChangeWorld) -> None:
    a = await _change(w, services=(SVC_A,))
    await _change(w, services=(SVC_A,), window=(WIN[0] + timedelta(hours=2), WIN[1] + timedelta(1)))
    await _change(w, services=(SVC_B,), phase="draft")  # a draft occupies nothing
    await _change(w, queue=Q_B, services=(SVC_B,))  # unreadable
    await create_freeze(
        w.session,
        data=FreezeIn.model_validate(
            {
                "name": "chg94-alle",
                "starts_at": WIN[0].isoformat() + "Z",
                "ends_at": WIN[1].isoformat() + "Z",
            }
        ),
        user_id=U_MANAGER,
    )
    rows = await service_availability(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1])
    assert [r.service_id for r in rows] == [SVC_A]
    row = rows[0]
    assert row.service_name == "chg94-service-a" and row.change_count == 2
    # 08:00-12:00 and 10:00 (day 1) - 12:00 (day 2) overlap by two hours: the union runs from
    # 08:00 on day 1 to 12:00 on day 2
    assert row.minutes_affected == 28 * 60
    kinds = [(w_.kind, w_.ref_id if w_.kind == "change" else None) for w_ in row.windows]
    assert ("change", a) in kinds and any(k == "freeze" for k, _ in kinds)
    assert all(w_.direct is True for w_ in row.windows if w_.kind == "change")
    admin = await service_availability(w.session, user_id=U_MANAGER, start=RANGE[0], end=RANGE[1])
    assert sorted(r.service_id for r in admin) == [SVC_A, SVC_B]


async def _task(w: ChangeWorld, change: int, title: str, owner: int = U_FULL) -> int:
    view = await add_task(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        change_ticket_id=change,
        data=TaskIn.model_validate({"title": title, "owner_id": owner}),
        user_id=U_FULL,
        session_factory=w.factory,
    )
    return view.ticket_id


async def test_my_tasks_open_only(w: ChangeWorld) -> None:
    live = await _change(w)
    closed_change = await _change(w)
    mine = await _task(w, live, "Offen")
    done = await _task(w, live, "Fertig")
    theirs = await _task(w, live, "Fremd", owner=U_MANAGER)
    on_closed = await _task(w, closed_change, "Alt")
    closing = await w.session.get(TiqoraItsmChange, closed_change)
    assert closing is not None
    closing.phase = "closed"
    await w.session.flush()
    await w.session.execute(
        text(
            "UPDATE ticket SET ticket_state_id = (SELECT id FROM ticket_state WHERE name ="
            " 'closed successful') WHERE id = :t"
        ),
        {"t": done},
    )
    items = await my_tasks(w.session, user_id=U_FULL)
    assert [i.ticket_id for i in items] == [mine]
    first = items[0]
    assert (first.change_ticket_id, first.owner_id, first.finished) == (live, U_FULL, False)
    assert first.change_tn is not None and first.task_kind == "workorder"
    everything = await my_tasks(w.session, user_id=U_FULL, open_only=False)
    assert sorted(i.ticket_id for i in everything) == sorted([mine, done, on_closed])
    assert theirs not in [i.ticket_id for i in everything]
    assert [i.ticket_id for i in await my_tasks(w.session, user_id=U_MANAGER)] == [theirs]
    assert await my_tasks(w.session, user_id=U_FULL, limit=0) == []


async def test_phase_counts_and_upcoming(w: ChangeWorld) -> None:
    sched = await _change(w, phase="scheduled")
    running = await _change(w, phase="implementing", window=(WIN[0] - timedelta(hours=3), WIN[0]))
    await _change(
        w, phase="scheduled", window=(WIN[0] + timedelta(days=30), WIN[1] + timedelta(30))
    )
    await _change(w, phase="draft", window=None)
    await _change(w, phase="scheduled", queue=Q_B)
    counts = await phase_counts(w.session, user_id=U_FULL)
    assert counts["scheduled"] == 2 and counts["implementing"] == 1 and counts["draft"] == 1
    assert counts["closed"] == 0 and set(counts) >= {"approval", "review", "rejected"}
    only_q = await phase_counts(w.session, user_id=U_MANAGER, queue_ids=[Q_B])
    assert only_q["scheduled"] == 1 and sum(only_q.values()) == 1
    got = await upcoming_changes(
        w.session, user_id=U_FULL, start=WIN[0] - timedelta(days=1), end=WIN[1] + timedelta(1)
    )
    assert [i.ticket_id for i in got] == [running, sched]
    assert (
        await upcoming_changes(
            w.session, user_id=U_FULL, start=WIN[0] - timedelta(days=1), end=WIN[1], limit=1
        )
    )[0].ticket_id == running


async def test_refs_for_tickets(w: ChangeWorld) -> None:
    change = await _change(w, change_type="emergency", phase="approval")
    task = await _task(w, change, "Aufgabe")
    refs = await change_refs_for_tickets(w.session, [change, task, 947399])
    assert refs == {change: ChangeRef(change, "emergency", "approval", None, WIN[0], WIN[1])}
    trefs = await task_refs_for_tickets(w.session, [change, task], user_id=U_FULL)
    assert set(trefs) == {task} and trefs[task].change_ticket_id == change
    assert trefs[task].change_tn is not None and trefs[task].task_kind == "workorder"
    unnamed = await task_refs_for_tickets(w.session, [task])
    assert unnamed[task].change_tn is None
    blind = await task_refs_for_tickets(w.session, [task], user_id=U_MANAGER + 1000)
    assert blind[task].change_tn is None


@pytest.mark.parametrize("mode", ["off", "znuny_readthrough"])
async def test_read_empty_outside_mode_b(w: ChangeWorld, mode: str) -> None:
    change = await _change(w, services=(SVC_A,))
    task = await _task(w, change, "Aufgabe")
    assert await _ids(w, ChangeQuery()) == [change]
    await _set_mode(w, mode)
    assert await list_changes(w.session, ChangeQuery(), user_id=U_FULL) == ChangePage((), 0, False)
    assert await upcoming_changes(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1]) == []
    assert all(v == 0 for v in (await phase_counts(w.session, user_id=U_FULL)).values())
    assert await my_tasks(w.session, user_id=U_FULL) == []
    cal = await calendar(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1])
    assert (cal.changes, cal.tasks, cal.freezes) == ((), (), ())
    assert await service_availability(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1]) == []
    assert await active_freezes(w.session, start=RANGE[0], end=RANGE[1]) == []
    assert await change_refs_for_tickets(w.session, [change]) == {}
    assert await task_refs_for_tickets(w.session, [task]) == {}


def _names(cls: type) -> list[str]:
    return [f.name for f in dataclasses.fields(cls)]


def test_read_contract_field_names() -> None:
    """X2: the dataclass field names (and their order) are fixed; additions only."""
    assert _names(ChangeListItem) == [
        "ticket_id",
        "tn",
        "title",
        "queue_id",
        "owner_id",
        "change_type",
        "phase",
        "outcome",
        "risk_level",
        "planned_start",
        "planned_end",
        "service_ids",
        "updated_at",
    ]
    assert _names(ChangeQuery) == [
        "text",
        "phases",
        "change_types",
        "service_id",
        "ci_id",
        "window_start",
        "window_end",
        "mine",
        "order",
        "limit",
        "offset",
    ]
    assert _names(ChangePage) == ["items", "total", "truncated"]
    assert _names(ChangeRef) == [
        "ticket_id",
        "change_type",
        "phase",
        "risk_level",
        "planned_start",
        "planned_end",
    ]
    assert _names(ChangeTaskRef) == [
        "change_ticket_id",
        "change_tn",
        "position",
        "task_kind",
        "planned_start",
        "planned_end",
    ]
    assert _names(read_module.TaskListItem)[:3] == ["ticket_id", "tn", "title"]
    assert _names(read_module.ServiceAvailabilityRow)[:2] == ["service_id", "service_name"]
    assert _names(read_module.ChangeCalendar)[:5] == ["start", "end", "changes", "tasks", "freezes"]


# --- API ---------------------------------------------------------------------------------------


@pytest.fixture
def make_client(w: ChangeWorld) -> Callable[[int, str], AsyncClient]:
    async def _db() -> Any:
        yield w.session

    def build(user_id: int, login: str) -> AsyncClient:
        user = AuthenticatedUser(
            id=user_id, login=login, first_name="Erika", last_name="Beispiel", auth_method="session"
        )
        app = create_app(Settings(environment="test"))
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_factory_dep] = lambda: w.factory
        app.dependency_overrides[get_current_user] = lambda: user
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    return build


async def test_api_list_calendar_and_tasks(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    (CI_A,) = await _real_cis(w, "srv-a.example.org")
    change = await _change(w, title="Switch-Tausch", services=(SVC_A,), cis=(CI_A,))
    await _change(w, queue=Q_B, services=(SVC_A,))
    task = await _task(w, change, "Aufgabe")
    window = {"start": RANGE[0].isoformat() + "Z", "end": RANGE[1].isoformat() + "Z"}
    async with make_client(U_FULL, "chg94-full") as api:
        status = await api.get("/api/v1/itsm/changes/status")
        assert status.status_code == 200 and status.json()["available"] is True
        listed = await api.get(
            "/api/v1/itsm/changes",
            params={"q": "switch", "phase": ["scheduled"], "service_id": SVC_A, "ci_id": CI_A},
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert (body["total"], body["truncated"]) == (1, False)
        item = body["items"][0]
        assert item["ticket_id"] == change and item["phase"] == "scheduled"
        assert item["owner_id"] == U_FULL and item["service_ids"] == []
        assert item["planned_start"].startswith("2026-11-10T08:00:00")
        assert (await api.get("/api/v1/itsm/changes", params={"limit": 0})).status_code == 422
        assert (await api.get("/api/v1/itsm/changes", params={"phase": "bogus"})).status_code == 422

        mine = await api.get("/api/v1/itsm/change-tasks/mine")
        assert [t["ticket_id"] for t in mine.json()] == [task]
        assert mine.json()[0]["change_tn"] is not None

        cal = await api.get("/api/v1/itsm/change-calendar", params=window)
        assert cal.status_code == 200, cal.text
        assert [c["ticket_id"] for c in cal.json()["changes"]] == [change]
        assert cal.json()["changes"][0]["conflict_severity"] == "warn"  # unreadable overlap
        filtered = await api.get(
            "/api/v1/itsm/change-calendar", params={**window, "service_id": [SVC_B]}
        )
        assert filtered.json()["changes"] == []
        too_long = await api.get(
            "/api/v1/itsm/change-calendar",
            params={"start": D.isoformat() + "Z", "end": (D + timedelta(93)).isoformat() + "Z"},
        )
        assert too_long.status_code == 422
        assert too_long.json()["detail"]["code"] == "range_too_large"

        psa = await api.get("/api/v1/itsm/change-calendar/services", params=window)
        assert psa.status_code == 200, psa.text
        assert [r["service_id"] for r in psa.json()] == [SVC_A]
        assert psa.json()[0]["windows"][0]["kind"] == "change"
        assert (
            await api.get(
                "/api/v1/itsm/change-calendar/services",
                params={"start": window["end"], "end": window["start"]},
            )
        ).status_code == 422


async def test_api_mode_a_is_refused(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    await _set_mode(w, "znuny_readthrough")
    async with make_client(U_FULL, "chg94-full") as api:
        for path in (
            "/api/v1/itsm/changes",
            "/api/v1/itsm/change-tasks/mine",
        ):
            assert (await api.get(path)).status_code == 409
        window = {"start": RANGE[0].isoformat() + "Z", "end": RANGE[1].isoformat() + "Z"}
        assert (await api.get("/api/v1/itsm/change-calendar", params=window)).status_code == 409


# --- review fixes -------------------------------------------------------------------------------


async def _hidden_path_change(w: ChangeWorld) -> tuple[int, int]:
    """A scheduled change whose only path to ``SVC_C`` runs through a CI of group ``G_B``
    (unreadable for ``U_FULL``, readable for ``U_MANAGER``); returns ``(change, hidden CI)``."""
    await seed_defaults(w.session)
    await w.session.execute(
        text(
            "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
            " change_by) VALUES (:id, 'chg94-service-c', 1, :t, 1, :t, 1)"
        ),
        {"id": SVC_C, "t": datetime(2026, 10, 1)},
    )
    change = await _change(w, services=(SVC_A,))
    class_id = await make_class(w.session, key="chg94vault", group_id=G_B)
    secret = await make_ci(w.session, class_id, "srv-vault-01.example.org", user_id=U_FULL)
    link = LinkService(w.session)
    await link.add(
        system_access(U_FULL),
        source=LinkEnd("ci", str(secret)),
        target=LinkEnd("ticket", str(change)),
        link_type_key="RelevantTo",
        enforce_permissions=False,
    )
    await link.add(
        system_access(U_FULL),
        source=LinkEnd("service", str(SVC_C)),
        target=LinkEnd("ci", str(secret)),
        link_type_key="DependsOn",
        enforce_permissions=False,
    )
    await recompute_impact(w.session, ticket_id=change)
    stored = (
        await w.session.execute(
            text(
                "SELECT ref_key FROM tiqora_itsm_change_impact WHERE ticket_id = :t"
                " AND kind = 'service'"
            ),
            {"t": change},
        )
    ).all()
    assert {int(r[0]) for r in stored} == {SVC_C}  # the ticket has no service of its own
    return change, secret


async def test_hidden_ci_path_does_not_reveal_service(w: ChangeWorld) -> None:
    change, secret = await _hidden_path_change(w)
    # the manager reads the CI: the service is visible everywhere
    seen = await service_availability(w.session, user_id=U_MANAGER, start=RANGE[0], end=RANGE[1])
    assert [r.service_id for r in seen] == [SVC_C]
    assert await _ids(w, ChangeQuery(service_id=SVC_C), user=U_MANAGER) == [change]
    cal = await calendar(
        w.session, user_id=U_MANAGER, start=RANGE[0], end=RANGE[1], service_ids=(SVC_C,)
    )
    assert [c.item.ticket_id for c in cal.changes] == [change]
    assert await _ids(w, ChangeQuery(ci_id=secret), user=U_MANAGER) == [change]
    # U_FULL cannot read the CI: no PSA window, no service match, no CI match
    assert await service_availability(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1]) == []
    assert await _ids(w, ChangeQuery(service_id=SVC_C)) == []
    hidden_cal = await calendar(
        w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1], service_ids=(SVC_C,)
    )
    assert hidden_cal.changes == ()
    assert await _ids(w, ChangeQuery(ci_id=secret)) == []
    # the change itself stays listed and its conflict flags still count the hidden path
    assert await _ids(w, ChangeQuery()) == [change]


async def test_text_search_finds_old_change_beyond_scan_cap(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    oldest = await _change(w, title="Uralt Router")
    for i in range(3):
        await _change(w, title=f"Neuer Switch {i}")
    monkeypatch.setattr(read_module, "LIST_SCAN_CAP", 2)
    plain = await list_changes(w.session, ChangeQuery(), user_id=U_FULL)
    assert plain.truncated is True and oldest not in [i.ticket_id for i in plain.items]
    monkeypatch.setattr(read_module, "LIST_SCAN_CAP", 5000)
    tn = next(
        i.tn
        for i in (await list_changes(w.session, ChangeQuery(), user_id=U_FULL)).items
        if i.ticket_id == oldest
    )
    monkeypatch.setattr(read_module, "LIST_SCAN_CAP", 2)
    by_tn = await list_changes(w.session, ChangeQuery(text=tn), user_id=U_FULL)
    assert [i.ticket_id for i in by_tn.items] == [oldest] and by_tn.truncated is False
    by_title = await list_changes(w.session, ChangeQuery(text="uralt"), user_id=U_FULL)
    assert [i.ticket_id for i in by_title.items] == [oldest] and by_title.truncated is False
    many = await list_changes(w.session, ChangeQuery(text="switch"), user_id=U_FULL)
    assert many.truncated is True and len(many.items) == 2  # the search itself exceeds the cap


async def test_calendar_flags_match_read_conflicts_across_the_boundary(w: ChangeWorld) -> None:
    from tiqora.itsm.change.conflicts import load_conflicts

    day = D.replace(hour=0)
    # the change crosses the start of the requested range
    crossing = await _change(
        w, services=(SVC_A,), window=(day - timedelta(hours=2), day + timedelta(hours=2))
    )
    before = await _change(
        w, services=(SVC_A,), window=(day - timedelta(hours=3), day - timedelta(hours=1))
    )
    await create_freeze(
        w.session,
        data=FreezeIn.model_validate(
            {
                "name": "chg94-vorher",
                "starts_at": (day - timedelta(hours=3)).isoformat() + "Z",
                "ends_at": (day - timedelta(hours=1)).isoformat() + "Z",
            }
        ),
        user_id=1,
    )
    real = await load_conflicts(w.session, ticket_id=crossing, user_id=U_FULL)
    assert {c.kind for c in real} == {"freeze", "change_overlap"}
    cal = await calendar(w.session, user_id=U_FULL, start=day, end=day + timedelta(days=1))
    flags = {c.item.ticket_id: c for c in cal.changes}
    assert set(flags) == {crossing}  # `before` ends before the range
    assert flags[crossing].conflict_kinds == ("change_overlap", "freeze")
    assert flags[crossing].conflict_severity == "block"
    assert cal.freezes == ()  # the freeze itself lies outside the range
    assert before not in flags


async def test_tasks_of_off_calendar_and_rejected_changes(w: ChangeWorld) -> None:
    live = await _change(w)
    parked = await _change(w)
    live_task = await _task(w, live, "Offen")
    parked_task = await _task(w, parked, "Geparkt")
    row = await w.session.get(TiqoraItsmChange, parked)
    assert row is not None
    row.phase = "rejected"
    await w.session.flush()
    for ticket in (live_task, parked_task):
        await w.session.execute(
            text("UPDATE tiqora_itsm_change_task SET planned_start = :s WHERE ticket_id = :t"),
            {"s": WIN[0], "t": ticket},
        )
    cal = await calendar(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1])
    assert [t.ticket_id for t in cal.tasks] == [live_task]
    assert [t.ticket_id for t in await my_tasks(w.session, user_id=U_FULL)] == [live_task]
    everything = await my_tasks(w.session, user_id=U_FULL, open_only=False)
    assert sorted(t.ticket_id for t in everything) == sorted([live_task, parked_task])


async def test_my_tasks_owner_filter_precedes_the_scan_cap(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    change = await _change(w)
    mine = await _task(w, change, "Meine")
    for i in range(3):
        await _task(w, change, f"Fremd {i}", owner=U_MANAGER)
    monkeypatch.setattr(read_module, "LIST_SCAN_CAP", 1)
    assert [t.ticket_id for t in await my_tasks(w.session, user_id=U_FULL)] == [mine]


async def test_range_is_checked_after_the_mode(w: ChangeWorld) -> None:
    await _set_mode(w, "znuny_readthrough")
    too_long = D + timedelta(days=200)
    cal = await calendar(w.session, user_id=U_FULL, start=D, end=too_long)
    assert cal.changes == ()
    assert await service_availability(w.session, user_id=U_FULL, start=too_long, end=D) == []


async def test_api_services_route_mode_gates(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    window = {"start": RANGE[0].isoformat() + "Z", "end": RANGE[1].isoformat() + "Z"}
    async with make_client(U_FULL, "chg94-full") as api:
        await _set_mode(w, "znuny_readthrough")
        res = await api.get("/api/v1/itsm/change-calendar/services", params=window)
        assert res.status_code == 409
        await _set_mode(w, "off")
        for path in ("/api/v1/itsm/changes", "/api/v1/itsm/change-calendar/services"):
            assert (await api.get(path, params=window)).status_code == 404


async def test_calendar_cap_keeps_dated_changes_before_open_start(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NULL planned starts sort last on both dialects, so the scan cap drops them first."""
    x = await _change(w)
    y = await _change(w, window=(WIN[0] + timedelta(hours=1), WIN[1]))
    z = await _change(w)
    row = await w.session.get(TiqoraItsmChange, z)
    assert row is not None
    row.planned_start = None
    await w.session.flush()
    monkeypatch.setattr(read_module, "LIST_SCAN_CAP", 2)
    cal = await calendar(w.session, user_id=U_FULL, start=RANGE[0], end=RANGE[1])
    assert [c.item.ticket_id for c in cal.changes] == [x, y] and cal.truncated is True
