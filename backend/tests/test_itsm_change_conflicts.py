"""Change conflicts: the pure detection and the loader/API on stored data (both dialects).

Seed band 946800-946899: services 946830-946831, CI ids 946840-946841 (impact rows only, no CI
rows needed for the loader; the API test creates one). Tickets are created by the change service
(autoincrement, rolled back with the world). Users, queues and the world are the shared ones of
``tests._change_fixtures`` (``Q_B`` is out of reach of ``U_FULL``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests._change_fixtures import (
    APP_SETTINGS,
    Q_A,
    Q_B,
    SERVICE,
    U_FULL,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change import conflicts as conflicts_module
from tiqora.itsm.change.conflicts import (
    CONFLICT_CANDIDATE_CAP,
    OtherChange,
    TaskWindow,
    detect_conflicts,
    load_conflicts,
    read_conflicts,
)
from tiqora.itsm.change.freeze import FreezeIn, FreezeOccurrence, FreezeOccurrences, create_freeze
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeImpact,
    TiqoraItsmChangeTask,
)
from tiqora.itsm.change.service import ChangeCreateIn, create_change
from tiqora.znuny.sysconfig import SysConfig

D = datetime(2026, 11, 10)
WIN = (D.replace(hour=8), D.replace(hour=12))
SVC_A, SVC_B = 946830, 946831
CI_A, CI_B = 946840, 946841


def _freeze(
    fid: int = 1,
    *,
    start: datetime = WIN[0],
    end: datetime = WIN[1],
    enforcement: str = "block",
    allow_emergency: bool = True,
    scope: str = "all",
    services: frozenset[int] = frozenset(),
) -> FreezeOccurrence:
    return FreezeOccurrence(
        freeze_id=fid,
        name="chg94-freeze",
        enforcement=enforcement,  # type: ignore[arg-type]
        allow_emergency=allow_emergency,
        scope=scope,  # type: ignore[arg-type]
        service_ids=services,
        start=start,
        end=end,
    )


def _detect(
    *,
    change_type: str = "normal",
    window: tuple[datetime, datetime] | None = WIN,
    services: frozenset[int] = frozenset({SVC_A}),
    cis: frozenset[int] = frozenset(),
    freezes: list[FreezeOccurrence] | None = None,
    others: list[OtherChange] | None = None,
    tasks: list[TaskWindow] | None = None,
) -> list[Any]:
    return detect_conflicts(
        change_type=change_type,
        window=window,
        services=services,
        cis=cis,
        freezes=freezes or [],
        others=others or [],
        tasks=tasks or [],
    )


def test_freeze_global_overlap_block() -> None:
    got = _detect(
        freezes=[_freeze(7, start=WIN[0] - timedelta(hours=1), end=WIN[0] + timedelta(1))]
    )
    assert [(c.kind, c.severity, c.ref) for c in got] == [("freeze", "block", "freeze:7")]
    warn = _detect(freezes=[_freeze(8, enforcement="warn")])
    assert [(c.kind, c.severity) for c in warn] == [("freeze", "warn")]
    far = _detect(
        freezes=[_freeze(9, start=WIN[1] + timedelta(hours=2), end=WIN[1] + timedelta(3))]
    )
    assert far == []


def test_freeze_service_scope_matches_impacted_services() -> None:
    scoped = _freeze(1, scope="services", services=frozenset({SVC_A, SVC_B}))
    got = _detect(freezes=[scoped], services=frozenset({SVC_A}))
    assert [(c.kind, c.shared_services) for c in got] == [("freeze", (SVC_A,))]
    assert _detect(freezes=[scoped], services=frozenset({946899})) == []
    assert _detect(freezes=[scoped], services=frozenset()) == []


def test_emergency_downgrades_block_when_allowed() -> None:
    allowed = _detect(change_type="emergency", freezes=[_freeze(1, allow_emergency=True)])
    assert [c.severity for c in allowed] == ["warn"]
    refused = _detect(change_type="emergency", freezes=[_freeze(1, allow_emergency=False)])
    assert [c.severity for c in refused] == ["block"]
    normal = _detect(change_type="normal", freezes=[_freeze(1, allow_emergency=True)])
    assert [c.severity for c in normal] == ["block"]


def test_touching_windows_do_not_overlap() -> None:
    before = _freeze(1, start=WIN[0] - timedelta(hours=2), end=WIN[0])
    after = _freeze(2, start=WIN[1], end=WIN[1] + timedelta(hours=2))
    other_touching = OtherChange(
        946801, "scheduled", WIN[1], WIN[1] + timedelta(hours=1), frozenset({SVC_A}), frozenset()
    )
    assert _detect(freezes=[before, after], others=[other_touching]) == []


def test_change_overlap_needs_shared_ci_or_service() -> None:
    def other(tid: int, services: set[int], cis: set[int], phase: str = "scheduled") -> OtherChange:
        return OtherChange(
            tid, phase, WIN[0] + timedelta(hours=1), WIN[1], frozenset(services), frozenset(cis)
        )

    got = _detect(
        services=frozenset({SVC_A}),
        cis=frozenset({CI_A}),
        others=[
            other(946802, {SVC_A}, set()),
            other(946803, set(), {CI_A}),
            other(946804, {SVC_B}, {CI_B}),  # nothing shared
            other(946805, {SVC_A}, {CI_A}, phase="draft"),  # not a phase that can collide
        ],
    )
    assert [(c.kind, c.severity, c.ref) for c in got] == [
        ("change_overlap", "warn", "change:946802"),
        ("change_overlap", "warn", "change:946803"),
    ]
    assert got[0].shared_services == (SVC_A,) and got[0].shared_cis == ()
    assert got[1].shared_services == () and got[1].shared_cis == (CI_A,)


def test_task_outside_window_and_no_window() -> None:
    inside = TaskWindow(946810, WIN[0], WIN[1])
    late = TaskWindow(946811, WIN[0], WIN[1] + timedelta(hours=1))
    early_start = TaskWindow(946812, WIN[0] - timedelta(minutes=1), None)
    unplanned = TaskWindow(946813, None, None)
    got = _detect(tasks=[inside, late, early_start, unplanned])
    assert [(c.kind, c.severity, c.ref) for c in got] == [
        ("task_outside", "warn", "task:946811"),
        ("task_outside", "warn", "task:946812"),
    ]
    none = _detect(window=None, tasks=[late], freezes=[_freeze()])
    assert [(c.kind, c.severity, c.ref) for c in none] == [("no_window", "info", None)]
    backwards = _detect(window=(WIN[1], WIN[0]))
    assert [c.kind for c in backwards] == ["no_window"]


# --- loader and API ----------------------------------------------------------------------------


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        await world.session.execute(
            text(
                "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                " change_by) VALUES (:id, 'chg94-service-b', 1, :t, 1, :t, 1)"
            ),
            {"id": SVC_A, "t": datetime(2026, 10, 1)},
        )
        yield world


async def _change(
    w: ChangeWorld,
    *,
    queue: int = Q_A,
    change_type: str = "normal",
    phase: str = "scheduled",
    window: tuple[datetime, datetime] | None = WIN,
    services: tuple[int, ...] = (SVC_A,),
    cis: tuple[int, ...] = (),
) -> int:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": change_type, "queue_id": Q_A}
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
    if queue != Q_A:  # U_FULL cannot create there; move the ticket out of reach afterwards
        await w.session.execute(
            text("UPDATE ticket SET queue_id = :q WHERE id = :t"), {"q": queue, "t": tid}
        )
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.phase = phase
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


async def test_load_conflicts_uses_stored_impact(w: ChangeWorld) -> None:
    mine = await _change(w, phase="assessment", services=(SVC_A,), cis=(CI_A,))
    sharing = await _change(w, services=(), cis=(CI_A,))
    await _change(w, services=(SERVICE,), cis=(CI_B,))  # overlaps in time, shares nothing
    await _change(w, services=(SVC_A,), phase="draft")  # shares, but a draft cannot collide
    await _change(
        w, services=(SVC_A,), window=(WIN[1], WIN[1] + timedelta(hours=1))
    )  # touches only
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
        user_id=1,
    )
    got = await load_conflicts(w.session, ticket_id=mine)
    assert [(c.kind, c.severity) for c in got] == [
        ("freeze", "block"),
        ("change_overlap", "warn"),
    ]
    assert got[1].ref == f"change:{sharing}" and got[1].shared_cis == (CI_A,)
    assert got[0].shared_services == (SVC_A,)

    # an empty stored impact set shares nothing, whatever the other changes touch
    bare = await _change(w, phase="assessment", services=(), cis=())
    assert [c.kind for c in await load_conflicts(w.session, ticket_id=bare)] == []
    unplanned = await _change(w, window=None)
    assert [c.kind for c in await load_conflicts(w.session, ticket_id=unplanned)] == ["no_window"]


async def test_load_conflicts_candidate_cap(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert CONFLICT_CANDIDATE_CAP == 500
    monkeypatch.setattr(conflicts_module, "CONFLICT_CANDIDATE_CAP", 2)
    mine = await _change(w, phase="assessment")
    others = [await _change(w) for _ in range(3)]
    got = await load_conflicts(w.session, ticket_id=mine)
    overlaps = [c.ref for c in got if c.kind == "change_overlap"]
    assert overlaps == [f"change:{t}" for t in others[:2]]  # the lowest ids, deterministic
    assert [c.kind for c in got].count("overlap_truncated") == 1
    assert got[-1].severity == "warn" and got[-1].ref is None

    monkeypatch.setattr(conflicts_module, "CONFLICT_CANDIDATE_CAP", 3)
    exact = await load_conflicts(w.session, ticket_id=mine)
    assert len([c for c in exact if c.kind == "change_overlap"]) == 3
    assert "overlap_truncated" not in [c.kind for c in exact]


async def test_truncated_freezes_are_reported_not_passed(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cut(*_a: Any, **_k: Any) -> FreezeOccurrences:
        return FreezeOccurrences([], True)

    monkeypatch.setattr(conflicts_module, "occurrences", cut)
    mine = await _change(w, phase="assessment")
    got = await load_conflicts(w.session, ticket_id=mine)
    assert [(c.kind, c.severity, c.ref) for c in got] == [("freeze_truncated", "warn", None)]
    assert (got[0].start, got[0].end) == WIN


async def test_conflict_with_unreadable_change_is_hidden(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    mine = await _change(w, phase="assessment", services=(SVC_A,))
    secret = await _change(w, queue=Q_B, services=(SVC_A,))
    visible = await _change(w, services=(SVC_A,))
    hidden_task = await _change(w, queue=Q_B, phase="draft", window=None)
    shown_task = await _change(w, phase="draft", window=None)
    for pos, tid in enumerate((hidden_task, shown_task)):
        w.session.add(
            TiqoraItsmChangeTask(
                ticket_id=tid,
                change_ticket_id=mine,
                position=pos,
                task_kind="task",
                planned_start=WIN[0],
                planned_end=WIN[1] + timedelta(hours=3),
                created_at=D,
                created_by=1,
            )
        )
    await w.session.flush()

    system = await load_conflicts(w.session, ticket_id=mine)
    assert sorted(c.ref or "" for c in system) == sorted(
        [f"change:{secret}", f"change:{visible}", f"task:{hidden_task}", f"task:{shown_task}"]
    )
    seen = await load_conflicts(w.session, ticket_id=mine, user_id=U_FULL)
    assert sorted((c.ref or "", c.hidden) for c in seen) == sorted(
        [("", True), (f"change:{visible}", False), (f"task:{shown_task}", False)]
    )
    hidden = next(c for c in seen if c.hidden)
    assert (hidden.kind, hidden.start, hidden.end) == ("change_overlap", WIN[0], WIN[1])

    async with make_client(U_FULL, "chg94-full") as client:
        got = await client.get(f"/api/v1/itsm/changes/{mine}/conflicts")
        assert got.status_code == 200, got.text
        body = got.json()
        assert f"change:{secret}" not in got.text and f"task:{hidden_task}" not in got.text
        assert sorted(c["ref"] or "" for c in body) == sorted(
            ["", f"change:{visible}", f"task:{shown_task}"]
        )
        item = next(c for c in body if c["hidden"])
        assert item["ref"] is None and item["kind"] == "change_overlap"
        assert (item["shared_services"], item["shared_cis"]) == ([], [])
        assert item["shared_service_count"] == 1 and item["shared_ci_count"] == 0
        assert item["start"].startswith("2026-11-10T08:00:00")
        shown = next(c for c in body if c["ref"] == f"change:{visible}")
        assert shown["shared_services"] == [SVC_A] and shown["hidden"] is False
        # no ro on the change ticket itself: not found
        assert (await client.get(f"/api/v1/itsm/changes/{secret}/conflicts")).status_code == 404


async def test_read_conflicts_names_only_readable_shared_cis(w: ChangeWorld) -> None:
    mine = await _change(w, phase="assessment", services=(), cis=())
    out = await read_conflicts(
        w.session, APP_SETTINGS, ticket_id=mine, user_id=U_FULL, sysconfig=SysConfig(w.session)
    )
    assert out == []
