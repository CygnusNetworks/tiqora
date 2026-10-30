"""Change tasks as child tickets, the task job and the task API (both dialects).

Seed band 946400-946499 (``Q_TASK`` is a second queue in the same group as ``Q_A``); users, queues
and the world come from ``tests._change_fixtures``. The world is one rolled-back session: routes
and the job commit into it with ``flush``. The ticket-number counter commits in its own session
and is removed by the world fixture, so the job test routes ``ticket_create_number`` to the real
factory instead of the shared session.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    G_B,
    Q_A,
    Q_B,
    SERVICE,
    U_CREATE,
    U_FULL,
    U_MANAGER,
    U_NONE,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change import tasks as change_tasks
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeTask,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.service import ChangeCreateIn, create_change
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION
from tiqora.itsm.change.tasks import (
    ITSM_CHANGE_TASKS_JOB,
    ChangeTasksPayload,
    TaskIn,
    TaskUpdateIn,
    TaskView,
    add_task,
    enqueue_change_tasks,
    list_tasks,
    reorder_tasks,
    run_change_tasks_job,
    task_state_counts,
    update_task,
)
from tiqora.jobs.context import JobContext
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig
from tiqora.znuny.ticket_number import ticket_create_number

pytestmark = pytest.mark.db

Q_TASK = 946420
PLANNED_START = datetime(2026, 11, 7, 20, 0, 0)


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        await world.session.execute(
            text(
                "INSERT INTO queue (id, name, group_id, system_address_id, salutation_id,"
                " signature_id, follow_up_id, follow_up_lock, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:id, 'chg94-task-queue', :g, 1, 1, 1, 1, 0, 1,"
                " :t, 1, :t, 1)"
            ),
            {"id": Q_TASK, "g": G_A, "t": datetime(2026, 10, 1)},
        )
        yield world


async def _change(w: ChangeWorld, **fields: object) -> int:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A, **fields}
    )
    view, _ = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=data,
        user_id=U_FULL,
        session_factory=w.factory,
    )
    return view.record.ticket_id


async def _add(
    w: ChangeWorld,
    change: int,
    *,
    user_id: int = U_FULL,
    key: str | None = None,
    **fields: Any,
) -> TaskView:
    return await add_task(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        change_ticket_id=change,
        data=TaskIn.model_validate({"title": "Switch tauschen", **fields}),
        user_id=user_id,
        template_task_key=key,
        session_factory=w.factory,
    )


async def _settings(w: ChangeWorld, **values: object) -> None:
    await save_section_values(
        w.session, ITSM_CHANGE_SECTION, dict(values), secret_key=APP_SETTINGS.secret_key
    )


async def _scalar(w: ChangeWorld, sql: str, **params: object) -> Any:
    return (await w.session.execute(text(sql), params)).scalar_one()


async def _task_ticket_count(w: ChangeWorld) -> int:
    return int(
        await _scalar(
            w,
            "SELECT count(*) FROM ticket t JOIN ticket_type ty ON ty.id = t.type_id"
            " WHERE ty.name = 'Change::Task'",
        )
    )


async def _ids(w: ChangeWorld, change: int, user_id: int = U_FULL) -> list[int]:
    return [
        t.ticket_id for t in await list_tasks(w.session, change_ticket_id=change, user_id=user_id)
    ]


async def _make_reader_of_both_queues(w: ChangeWorld, user_id: int = 1) -> None:
    for gid in (G_A, G_B):
        await w.session.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time, create_by,"
                " change_time, change_by) VALUES (:u, :g, 'ro', :t, 1, :t, 1)"
            ),
            {"u": user_id, "g": gid, "t": datetime(2026, 10, 1)},
        )


async def test_add_task_creates_child_ticket_and_link(w: ChangeWorld) -> None:
    change = await _change(w)
    task = await _add(
        w,
        change,
        title="Switch <b>tauschen</b>",
        task_kind="backout",
        body="Schritt 1\nSchritt 2",
        planned_start=PLANNED_START,
        planned_end=datetime(2026, 11, 7, 22, 0, 0),
        planned_effort_minutes=90,
    )

    assert (task.title, task.task_kind, task.position, task.planned_effort_minutes) == (
        "Switch <b>tauschen</b>",
        "backout",
        0,
        90,
    )
    assert task.planned_start == PLANNED_START and task.finished is False
    row = (
        await w.session.execute(
            text(
                "SELECT ty.name, st.name, t.queue_id, t.user_id FROM ticket t"
                " JOIN ticket_type ty ON ty.id = t.type_id"
                " JOIN ticket_state st ON st.id = t.ticket_state_id WHERE t.id = :t"
            ),
            {"t": task.ticket_id},
        )
    ).one()
    assert tuple(row) == ("Change::Task", "new", Q_A, U_FULL)

    # change = parent, task = child, ParentChild; history on both ends
    link = (
        await w.session.execute(
            text(
                "SELECT lt.name, lr.source_key, lr.target_key FROM link_relation lr"
                " JOIN link_type lt ON lt.id = lr.type_id WHERE lr.target_key = :t"
            ),
            {"t": str(task.ticket_id)},
        )
    ).all()
    assert [tuple(r) for r in link] == [("ParentChild", str(change), str(task.ticket_id))]
    for ticket_id in (change, task.ticket_id):
        assert (
            await _scalar(
                w,
                "SELECT count(*) FROM ticket_history h JOIN ticket_history_type ht"
                " ON ht.id = h.history_type_id WHERE ht.name = 'TicketLinkAdd'"
                " AND h.ticket_id = :t",
                t=ticket_id,
            )
            == 1
        )

    # the body is one internal note, escaped
    article = (
        await w.session.execute(
            text(
                "SELECT a.is_visible_for_customer, m.a_body FROM article a"
                " JOIN article_data_mime m ON m.article_id = a.id WHERE a.ticket_id = :t"
            ),
            {"t": task.ticket_id},
        )
    ).one()
    assert (bool(article[0]), article[1]) == (False, "Schritt 1<br>Schritt 2")

    events = (
        await w.session.execute(
            select(TiqoraItsmChangeEvent)
            .where(
                TiqoraItsmChangeEvent.ticket_id == change,
                TiqoraItsmChangeEvent.event == "task_added",
            )
            .order_by(TiqoraItsmChangeEvent.id)
        )
    ).scalars()
    assert [json.loads(e.detail or "{}") for e in events] == [
        {"task_ticket_id": task.ticket_id, "task_kind": "backout"}
    ]


async def test_task_queue_and_owner_rules(w: ChangeWorld) -> None:
    change = await _change(w)

    # no setting, no input: queue of the change, owner = the creating agent
    default = await _add(w, change)
    assert (default.queue_id, default.owner_id) == (Q_A, U_FULL)

    # setting task_queue_id wins over the change's queue ...
    await _settings(w, task_queue_id=Q_TASK)
    from_setting = await _add(w, change, title="Zweite")
    assert from_setting.queue_id == Q_TASK
    # ... and the task's own queue over the setting; an explicit owner is taken
    explicit = await _add(w, change, title="Dritte", queue_id=Q_A, owner_id=U_MANAGER)
    assert (explicit.queue_id, explicit.owner_id) == (Q_A, U_MANAGER)

    before = await _task_ticket_count(w)
    for bad_owner in (U_NONE, 946499):  # no rw on the task queue / no such user
        with pytest.raises(ChangeError) as owner_err:
            await _add(w, change, title="Vierte", owner_id=bad_owner)
        assert (owner_err.value.error_code, owner_err.value.status_code) == ("owner_invalid", 422)
    with pytest.raises(ChangeError) as queue_err:  # Q_B: the creator holds no ``create`` there
        await _add(w, change, title="Fuenfte", queue_id=Q_B)
    assert (queue_err.value.error_code, queue_err.value.status_code) == ("forbidden", 403)
    with pytest.raises(ChangeError) as missing_queue:
        await _add(w, change, title="Sechste", queue_id=946498)
    assert missing_queue.value.error_code == "queue_not_found"
    assert await _task_ticket_count(w) == before  # nothing half-written

    # rw on the change is required, ``create`` alone is not enough
    with pytest.raises(ChangeError) as no_rw:
        await _add(w, change, user_id=U_CREATE)
    assert (no_rw.value.error_code, no_rw.value.status_code) == ("forbidden", 403)
    with pytest.raises(ChangeError) as hidden:
        await _add(w, change, user_id=U_NONE)
    assert hidden.value.status_code == 404


async def test_task_customer_copied(w: ChangeWorld) -> None:
    change = await _change(w, service_id=SERVICE)
    await w.session.execute(
        text(
            "UPDATE ticket SET customer_id = 'example', customer_user_id = 'erika@example.org'"
            " WHERE id = :t"
        ),
        {"t": change},
    )
    task = await _add(w, change)
    row = (
        await w.session.execute(
            text(
                "SELECT customer_id, customer_user_id, service_id, ticket_priority_id"
                " FROM ticket WHERE id IN (:c, :t) ORDER BY id"
            ),
            {"c": change, "t": task.ticket_id},
        )
    ).all()
    assert tuple(row[0]) == tuple(row[1]) and row[1][:3] == (
        "example",
        "erika@example.org",
        SERVICE,
    )


async def test_max_tasks_enforced(w: ChangeWorld) -> None:
    change = await _change(w)
    await _settings(w, max_tasks=2)
    first = await _add(w, change, key="a")
    await _add(w, change, key="b")
    with pytest.raises(ChangeError) as full:
        await _add(w, change, key="c")
    assert (full.value.error_code, full.value.status_code) == ("max_tasks_reached", 409)
    # an existing template task is returned, it does not count as a new one
    assert (await _add(w, change, key="a")).ticket_id == first.ticket_id
    assert len(await _ids(w, change)) == 2


async def test_template_task_key_idempotent(w: ChangeWorld) -> None:
    change = await _change(w)
    first = await _add(w, change, key="switch_tauschen")
    again = await _add(w, change, key="switch_tauschen", title="Anderer Titel")
    other = await _add(w, change, key="switch_pruefen")
    manual = await _add(w, change, title="Ohne Schluessel")
    manual_two = await _add(w, change, title="Ohne Schluessel")  # no key: always a new task

    assert again.ticket_id == first.ticket_id and again.title == first.title
    assert len({first.ticket_id, other.ticket_id, manual.ticket_id, manual_two.ticket_id}) == 4
    assert await _task_ticket_count(w) == 4
    assert (
        await w.session.scalar(
            select(func.count())
            .select_from(TiqoraItsmChangeTask)
            .where(TiqoraItsmChangeTask.change_ticket_id == change)
        )
        == 4
    )


async def test_list_tasks_omits_unreadable_task_ticket(w: ChangeWorld) -> None:
    change = await _change(w)
    visible = await _add(w, change, title="Sichtbar")
    hidden = await _add(w, change, title="Verborgen")
    await w.session.execute(
        text("UPDATE ticket SET queue_id = :q WHERE id = :t"), {"q": Q_B, "t": hidden.ticket_id}
    )

    assert await _ids(w, change) == [visible.ticket_id]
    await _make_reader_of_both_queues(w)
    assert await _ids(w, change, user_id=1) == [visible.ticket_id, hidden.ticket_id]
    # an unreadable task cannot be touched either, and the answer does not say it exists
    with pytest.raises(ChangeError) as err:
        await update_task(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            change_ticket_id=change,
            task_ticket_id=hidden.ticket_id,
            data=TaskUpdateIn(task_kind="decision"),
            user_id=U_FULL,
            session_factory=w.factory,
        )
    assert (err.value.error_code, err.value.status_code) == ("task_not_found", 404)
    # the phase guard counts every task, readable or not
    assert (await task_state_counts(w.session, change)) == (2, 2)


async def test_reorder_validates_ids(w: ChangeWorld) -> None:
    change = await _change(w)
    a = await _add(w, change, title="A")
    b = await _add(w, change, title="B")
    c = await _add(w, change, title="C")
    other = await _change(w, title="Anderer Change")
    foreign = await _add(w, other, title="Fremd")

    async def reorder(order: list[int]) -> list[int]:
        got = await reorder_tasks(
            w.session, APP_SETTINGS, change_ticket_id=change, order=order, user_id=U_FULL
        )
        return [t.ticket_id for t in got]

    assert await reorder([c.ticket_id, a.ticket_id, b.ticket_id]) == [
        c.ticket_id,
        a.ticket_id,
        b.ticket_id,
    ]
    assert [
        t.position for t in await list_tasks(w.session, change_ticket_id=change, user_id=U_FULL)
    ] == [
        0,
        1,
        2,
    ]
    for bad in (
        [a.ticket_id, b.ticket_id],  # incomplete
        [a.ticket_id, a.ticket_id, b.ticket_id, c.ticket_id],  # duplicate
        [a.ticket_id, b.ticket_id, foreign.ticket_id],  # task of another change
        [a.ticket_id, b.ticket_id, c.ticket_id, 946497],  # unknown id
    ):
        with pytest.raises(ChangeError) as err:
            await reorder(bad)
        assert (err.value.error_code, err.value.status_code) == ("order_invalid", 422)
    assert await _ids(w, change) == [c.ticket_id, a.ticket_id, b.ticket_id]  # unchanged

    # a task the caller cannot read keeps its slot; the visible ones move around it
    await w.session.execute(
        text("UPDATE ticket SET queue_id = :q WHERE id = :t"), {"q": Q_B, "t": a.ticket_id}
    )
    assert await reorder([b.ticket_id, c.ticket_id]) == [b.ticket_id, c.ticket_id]
    await _make_reader_of_both_queues(w)
    assert await _ids(w, change, user_id=1) == [b.ticket_id, a.ticket_id, c.ticket_id]
    with pytest.raises(ChangeError):  # the hidden one is not part of the caller's list
        await reorder([b.ticket_id, c.ticket_id, a.ticket_id])


async def test_insert_at_position_renumbers(w: ChangeWorld) -> None:
    change = await _change(w)
    a = await _add(w, change, title="A")
    b = await _add(w, change, title="B")
    mid = await _add(w, change, title="Mitte", position=1)
    far = await _add(w, change, title="Ende", position=99)
    tasks = await list_tasks(w.session, change_ticket_id=change, user_id=U_FULL)
    assert [(t.ticket_id, t.position) for t in tasks] == [
        (a.ticket_id, 0),
        (mid.ticket_id, 1),
        (b.ticket_id, 2),
        (far.ticket_id, 3),
    ]


async def test_update_task_fields_and_owner(w: ChangeWorld) -> None:
    change = await _change(w)
    task = await _add(w, change)

    async def update(**changes: Any) -> TaskView:
        return await update_task(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            change_ticket_id=change,
            task_ticket_id=task.ticket_id,
            data=TaskUpdateIn.model_validate(changes),
            user_id=U_FULL,
            session_factory=w.factory,
        )

    got = await update(
        task_kind="pir",
        planned_effort_minutes=45,
        planned_start=PLANNED_START,
        planned_end=datetime(2026, 11, 7, 21, 0, 0),
        owner_id=U_MANAGER,
    )
    assert (got.task_kind, got.planned_effort_minutes, got.owner_id) == ("pir", 45, U_MANAGER)
    assert got.planned_end == datetime(2026, 11, 7, 21, 0, 0)
    # only what is sent changes; null clears a time
    cleared = await update(planned_end=None)
    assert (cleared.planned_end, cleared.planned_start, cleared.task_kind) == (
        None,
        PLANNED_START,
        "pir",
    )
    with pytest.raises(ChangeError) as err:
        await update(planned_end=datetime(2026, 11, 7, 19, 0, 0))
    assert err.value.error_code == "invalid_window"

    # A-M3: the tasks of a closed or cancelled change are read-only like the change
    record = await w.session.get(TiqoraItsmChange, change)
    assert record is not None
    for final in ("closed", "cancelled"):
        record.phase = final
        await w.session.flush()
        with pytest.raises(ChangeError) as err:
            await update(task_kind="workorder")
        assert (err.value.error_code, err.value.http_status) == ("change_final", 409)
    stored = await w.session.get(TiqoraItsmChangeTask, task.ticket_id)
    assert stored is not None and stored.task_kind == "pir"


async def test_task_state_counts(w: ChangeWorld) -> None:
    change = await _change(w)
    assert await task_state_counts(w.session, change) == (0, 0)
    tasks = [await _add(w, change, title=f"T{i}") for i in range(4)]
    assert await task_state_counts(w.session, change) == (4, 4)

    # closed, removed and merged state types count as finished
    for task, state in zip(tasks[:3], ("closed successful", "removed", "merged"), strict=True):
        await w.session.execute(
            text(
                "UPDATE ticket SET ticket_state_id = (SELECT id FROM ticket_state WHERE name = :s)"
                " WHERE id = :t"
            ),
            {"s": state, "t": task.ticket_id},
        )
    assert await task_state_counts(w.session, change) == (1, 4)
    listed = {
        t.ticket_id: t.finished
        for t in await list_tasks(w.session, change_ticket_id=change, user_id=U_FULL)
    }
    assert listed == {
        tasks[0].ticket_id: True,
        tasks[1].ticket_id: True,
        tasks[2].ticket_id: True,
        tasks[3].ticket_id: False,
    }


# --- the job ---------------------------------------------------------------------------------


class _Factory:
    """``session_factory`` handing out the test's one session and never closing it."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def __call__(self) -> _Factory:
        return self

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Crash(BaseException):
    """Stands in for a killed worker: not an ``Exception``, so no handler may swallow it."""


async def _template(w: ChangeWorld, keys: list[str]) -> int:
    row = TiqoraItsmChangeTemplate(
        name=f"chg94-tpl-{len(keys)}",
        change_type="normal",
        defaults="{}",
        tasks=json.dumps(
            [
                {
                    "key": key,
                    "title": f"Aufgabe {key}",
                    "offset_minutes": 30 * i,
                    "duration_minutes": 20,
                    "body": "Anleitung",
                    "position": i,
                    **({"task_kind": "backout"} if key == keys[-1] else {}),
                }
                for i, key in enumerate(keys)
            ]
        ),
        created_at=datetime(2026, 10, 1),
        created_by=1,
        updated_at=datetime(2026, 10, 1),
        updated_by=1,
    )
    w.session.add(row)
    await w.session.flush()
    return row.id


def _ctx(w: ChangeWorld, user_id: int = U_FULL) -> JobContext:
    return JobContext(
        job_id=946401,
        kind=ITSM_CHANGE_TASKS_JOB.kind,
        created_by=user_id,
        settings=Settings(environment="test"),
        session_factory=_Factory(w.session),  # type: ignore[arg-type]
        attempt=1,
        max_attempts=2,
    )


@pytest.fixture
def real_ticket_numbers(w: ChangeWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    """The counter needs its own sessions; the job's factory is the shared one."""

    async def number(_factory: Any, sysconfig: Any) -> str:
        return await ticket_create_number(w.factory, sysconfig)

    monkeypatch.setattr("tiqora.domain.ticket_write_service.ticket_create_number", number)


async def test_tasks_job_creates_all_and_resumes(
    w: ChangeWorld, real_ticket_numbers: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    change = await _change(w, planned_start=PLANNED_START)
    keys = ["backup", "umbauen", "pruefen", "zurueck"]
    payload = ChangeTasksPayload(
        change_ticket_id=change, template_id=await _template(w, keys), user_id=U_FULL
    )

    real_add = change_tasks._add_task
    calls = 0

    async def crash_on_third(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise _Crash
        return await real_add(*args, **kwargs)

    monkeypatch.setattr(change_tasks, "_add_task", crash_on_third)
    with pytest.raises(_Crash):
        await run_change_tasks_job(_ctx(w), payload)
    assert await _task_ticket_count(w) == 2  # interrupted after two

    monkeypatch.setattr(change_tasks, "_add_task", real_add)  # the requeued claim
    result = await run_change_tasks_job(_ctx(w), payload)
    assert result == {"created": 2, "existing": 2, "failed": []}
    again = await run_change_tasks_job(_ctx(w), payload)
    assert again == {"created": 0, "existing": 4, "failed": []}

    assert await _task_ticket_count(w) == 4  # no duplicates
    tasks = await list_tasks(w.session, change_ticket_id=change, user_id=U_FULL)
    assert [t.template_task_key for t in tasks] == keys
    assert [t.position for t in tasks] == [0, 1, 2, 3]
    assert [t.title for t in tasks][:2] == ["Aufgabe backup", "Aufgabe umbauen"]
    assert [t.task_kind for t in tasks] == ["workorder", "workorder", "workorder", "backout"]
    # offsets are relative to the change's planned start; duration gives the end
    assert tasks[1].planned_start == datetime(2026, 11, 7, 20, 30)
    assert tasks[1].planned_end == datetime(2026, 11, 7, 20, 50)
    assert tasks[1].planned_effort_minutes == 20


async def test_tasks_job_rechecks_rw(w: ChangeWorld, real_ticket_numbers: None) -> None:
    change = await _change(w)
    template_id = await _template(w, ["eins", "zwei"])
    # the creator lost ``rw`` between enqueue and run
    await w.session.execute(
        text("DELETE FROM group_user WHERE user_id = :u AND permission_key = 'rw'"),
        {"u": U_FULL},
    )
    result = await run_change_tasks_job(
        _ctx(w),
        ChangeTasksPayload(change_ticket_id=change, template_id=template_id, user_id=U_FULL),
    )
    assert result["created"] == 0 and result["existing"] == 0
    assert result["failed"] == [
        {"key": "eins", "code": "forbidden"},
        {"key": "zwei", "code": "forbidden"},
    ]
    assert await _task_ticket_count(w) == 0


async def test_tasks_job_reports_vanished_template(w: ChangeWorld) -> None:
    from tiqora.jobs.context import JobFailed

    change = await _change(w)
    with pytest.raises(JobFailed):
        await run_change_tasks_job(
            _ctx(w), ChangeTasksPayload(change_ticket_id=change, template_id=946499, user_id=U_FULL)
        )


async def test_enqueue_change_tasks_queues_the_job(w: ChangeWorld) -> None:
    change = await _change(w)
    job = await enqueue_change_tasks(
        w.session, change_ticket_id=change, template_id=7, user_id=U_FULL
    )
    assert job.kind == "core.itsm_change_tasks" and job.status == "queued"
    assert json.loads(job.payload) == {
        "change_ticket_id": change,
        "template_id": 7,
        "user_id": U_FULL,
    }
    same = await enqueue_change_tasks(
        w.session, change_ticket_id=change, template_id=7, user_id=U_FULL
    )
    assert same.id == job.id  # one active job per change


# --- the API ---------------------------------------------------------------------------------


@pytest.fixture
async def client(w: ChangeWorld) -> AsyncIterator[AsyncClient]:
    async def _db() -> Any:
        yield w.session

    user = AuthenticatedUser(
        id=U_FULL,
        login="chg94-full",
        first_name="Erika",
        last_name="Beispiel",
        auth_method="session",
    )
    app = create_app(Settings(environment="test"))
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_session_factory_dep] = lambda: w.factory
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_tasks_api_roundtrip(w: ChangeWorld, client: AsyncClient) -> None:
    change = await _change(w)
    base = f"/api/v1/itsm/changes/{change}/tasks"

    first = await client.post(
        base, json={"title": "Erste", "task_kind": "workorder", "planned_effort_minutes": 30}
    )
    assert first.status_code == 201, first.text
    second = await client.post(base, json={"title": "Zweite", "task_kind": "decision"})
    assert second.status_code == 201, second.text
    a, b = first.json(), second.json()
    assert (a["position"], b["position"], a["tn"] != b["tn"]) == (0, 1, True)
    assert a["state"] == "new" and a["finished"] is False and a["owner_id"] == U_FULL

    listed = await client.get(base)
    assert [t["ticket_id"] for t in listed.json()] == [a["ticket_id"], b["ticket_id"]]

    patched = await client.patch(f"{base}/{b['ticket_id']}", json={"task_kind": "pir"})
    assert patched.status_code == 200 and patched.json()["task_kind"] == "pir"

    moved = await client.put(f"{base}/order", json={"order": [b["ticket_id"], a["ticket_id"]]})
    assert moved.status_code == 200
    assert [t["ticket_id"] for t in moved.json()] == [b["ticket_id"], a["ticket_id"]]

    bad = await client.put(f"{base}/order", json={"order": [a["ticket_id"]]})
    assert (bad.status_code, bad.json()["detail"]["code"]) == (422, "order_invalid")
    unknown = await client.post("/api/v1/itsm/changes/946498/tasks", json={"title": "Verwaist"})
    assert unknown.status_code == 404
    invalid = await client.post(base, json={"title": "", "task_kind": "other"})
    assert invalid.status_code == 422
