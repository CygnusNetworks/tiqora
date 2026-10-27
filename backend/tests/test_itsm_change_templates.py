"""Change templates: validation, admin CRUD, defaults, standard templates, instantiate.

Seed band 946500-946599 (``CAB`` 946510, risk question ``chg94-rq``); users, queues and the world
come from ``tests._change_fixtures``. Both dialects. Rows the services write (templates, tickets,
jobs) live in the rolled-back world session.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from tests._change_fixtures import (
    APP_SETTINGS,
    Q_A,
    Q_B,
    SERVICE,
    SERVICE_B,
    U_FULL,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
)
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.models import (
    TiqoraItsmCab,
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeRiskAnswer,
    TiqoraItsmChangeRiskQuestion,
    TiqoraItsmChangeTemplate,
)
from tiqora.itsm.change.service import (
    ChangeCreateIn,
    ChangeUpdateIn,
    create_change,
    update_change,
)
from tiqora.itsm.change.settings import ITSM_CHANGE_SECTION
from tiqora.itsm.change.templates import (
    TemplateDefaults,
    TemplateIn,
    TemplateTask,
    apply_template_defaults,
    create_template,
    delete_template,
    expand_tasks,
    get_template,
    list_templates,
    update_template,
    validate_template,
)
from tiqora.settings_sections.store import save_section_values
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

CAB = 946510
START = datetime(2026, 11, 7, 20, 0, 0)
ADMIN_ID = 1


# --- pure ------------------------------------------------------------------------------------


def _task(key: str, **fields: Any) -> TemplateTask:
    return TemplateTask.model_validate({"key": key, "title": f"Aufgabe {key}", **fields})


def test_expand_tasks_offsets_and_order() -> None:
    tasks = [
        _task("c", position=2, offset_minutes=120, duration_minutes=30),
        _task("a", position=0, offset_minutes=-60, duration_minutes=15, task_kind="backout"),
        _task("b", position=0, offset_minutes=0),
    ]
    out = expand_tasks(tasks, START)
    # position first, ties keep list order; the result is renumbered 0..n-1
    assert [(t.title, t.position) for t in out] == [
        ("Aufgabe a", 0),
        ("Aufgabe b", 1),
        ("Aufgabe c", 2),
    ]
    assert out[0].planned_start == datetime(2026, 11, 7, 19, 0)
    assert out[0].planned_end == datetime(2026, 11, 7, 19, 15)
    assert out[0].task_kind == "backout" and out[0].planned_effort_minutes == 15
    assert (out[1].planned_start, out[1].planned_end) == (START, None)
    assert out[2].planned_end == datetime(2026, 11, 7, 22, 30)
    # without a planned start no task gets times
    bare = expand_tasks(tasks, None)
    assert all(t.planned_start is None and t.planned_end is None for t in bare)


def test_template_validation() -> None:
    ok = [_task("a"), _task("b")]
    validate_template(change_type="normal", is_standard=False, tasks=ok, max_tasks=5)
    validate_template(change_type="standard", is_standard=True, tasks=ok, max_tasks=2)

    def code(**kw: Any) -> str:
        args: dict[str, Any] = {
            "change_type": "normal",
            "is_standard": False,
            "tasks": ok,
            "max_tasks": 5,
        }
        args.update(kw)
        with pytest.raises(ChangeError) as exc:
            validate_template(**args)
        assert exc.value.http_status == 422
        return exc.value.error_code

    assert code(tasks=[_task("a"), _task("a")]) == "template_duplicate_task_key"
    assert code(tasks=ok, max_tasks=1) == "template_too_many_tasks"
    assert code(change_type="normal", is_standard=True) == "template_standard_type"
    assert code(change_type="emergency", is_standard=True) == "template_standard_type"
    # the key pattern is the model's own
    with pytest.raises(ValueError):
        TemplateTask.model_validate({"key": "Bad Key", "title": "x"})


# --- the world -------------------------------------------------------------------------------


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        now = datetime(2026, 10, 1)
        world.session.add(
            TiqoraItsmCab(
                id=CAB,
                name="chg94-templates-cab",
                rule="any",
                valid=True,
                created_at=now,
                created_by=1,
                updated_at=now,
                updated_by=1,
            )
        )
        await world.session.flush()
        yield world


async def _question(
    w: ChangeWorld, key: str = "chg94-rq", *, valid: bool = True
) -> TiqoraItsmChangeRiskQuestion:
    now = datetime(2026, 10, 1)
    row = TiqoraItsmChangeRiskQuestion(
        question_key=key,
        question="Gibt es einen Rückfallplan?",
        answers=json.dumps(
            [
                {"key": "yes", "label": "Ja", "points": 0},
                {"key": "no", "label": "Nein", "points": 8},
            ]
        ),
        position=1,
        valid=valid,
        created_at=now,
        created_by=1,
        updated_at=now,
        updated_by=1,
    )
    w.session.add(row)
    await w.session.flush()
    return row


def _in(**fields: Any) -> TemplateIn:
    values: dict[str, Any] = {"name": "chg94 Switch-Tausch", "change_type": "normal", **fields}
    return TemplateIn.model_validate(values)


async def _make(w: ChangeWorld, **fields: Any) -> int:
    view = await create_template(w.session, APP_SETTINGS, data=_in(**fields), user_id=ADMIN_ID)
    return view.id


async def _create(w: ChangeWorld, **fields: Any) -> tuple[int, int | None]:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": "normal", "queue_id": Q_A, **fields}
    )
    view, job_id = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=data,
        user_id=U_FULL,
        session_factory=w.factory,
    )
    return view.record.ticket_id, job_id


# --- service: CRUD ---------------------------------------------------------------------------


async def test_template_service_roundtrip_and_rules(w: ChangeWorld) -> None:
    await _question(w)
    tid = await _make(
        w,
        description="Standardablauf",
        defaults={"queue_id": Q_A, "service_ids": [SERVICE], "risk_answers": {"chg94-rq": "yes"}},
        tasks=[{"key": "prep", "title": "Vorbereiten", "offset_minutes": -30}],
    )
    view = await get_template(w.session, tid)
    assert (view.name, view.change_type, view.valid, view.broken) == (
        "chg94 Switch-Tausch",
        "normal",
        True,
        False,
    )
    assert view.defaults.service_ids == [SERVICE]
    assert [t.key for t in view.tasks] == ["prep"]

    with pytest.raises(ChangeError) as dup:  # names are unique (case as stored)
        await _make(w)
    assert (dup.value.error_code, dup.value.http_status) == ("template_name_exists", 409)

    updated = await update_template(
        w.session,
        APP_SETTINGS,
        template_id=tid,
        data=_in(name="chg94 Umbenannt", valid=False),
        user_id=ADMIN_ID,
    )
    assert (updated.name, updated.valid, updated.updated_by) == ("chg94 Umbenannt", False, ADMIN_ID)
    assert [t.id for t in await list_templates(w.session, include_invalid=True)] == [tid]
    assert await list_templates(w.session, include_invalid=False) == []

    with pytest.raises(ChangeError) as missing:
        await update_template(
            w.session, APP_SETTINGS, template_id=946599, data=_in(), user_id=ADMIN_ID
        )
    assert (missing.value.error_code, missing.value.http_status) == ("template_not_found", 404)


async def test_template_save_refuses_unknown_references(w: ChangeWorld) -> None:
    await _question(w)
    cases: list[tuple[dict[str, Any], str]] = [
        ({"defaults": {"queue_id": 946599}}, "template_queue_not_found"),
        ({"defaults": {"service_ids": [946599]}}, "template_service_not_found"),
        ({"defaults": {"cab_id": 946599}}, "template_cab_not_found"),
        ({"defaults": {"priority_id": 946599}}, "template_priority_not_found"),
        ({"defaults": {"risk_answers": {"chg94-rq": "maybe"}}}, "template_risk_answer_invalid"),
        ({"defaults": {"risk_answers": {"nope": "yes"}}}, "template_risk_answer_invalid"),
        (
            {"tasks": [{"key": "a", "title": "x", "queue_id": 946599}]},
            "template_queue_not_found",
        ),
        (
            {"change_type": "normal", "is_standard": True},
            "template_standard_type",
        ),
    ]
    for fields, expected in cases:
        with pytest.raises(ChangeError) as exc:
            await _make(w, **fields)
        assert exc.value.error_code == expected, fields

    await save_section_values(
        w.session, ITSM_CHANGE_SECTION, {"max_tasks": 1}, secret_key=APP_SETTINGS.secret_key
    )
    with pytest.raises(ChangeError) as many:
        await _make(w, tasks=[{"key": "a", "title": "x"}, {"key": "b", "title": "y"}])
    assert many.value.error_code == "template_too_many_tasks"


# --- API -------------------------------------------------------------------------------------


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


async def test_admin_crud_and_non_admin_forbidden(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    base = "/api/v1/admin/itsm/change/templates"
    body = {
        "name": "chg94 Rollout",
        "change_type": "normal",
        "defaults": {"queue_id": Q_A, "duration_minutes": 60},
        "tasks": [{"key": "prep", "title": "Vorbereiten", "body": "<b>x</b>"}],
    }
    async with make_client(U_FULL, "chg94-full") as agent:
        for call in (
            agent.get(base),
            agent.post(base, json=body),
            agent.put(f"{base}/1", json=body),
            agent.delete(f"{base}/1"),
        ):
            assert (await call).status_code == 403

    async with make_client(ADMIN_ID, "root@localhost") as admin:
        created = await admin.post(base, json=body)
        assert created.status_code == 201, created.text
        tpl = created.json()
        assert tpl["name"] == "chg94 Rollout" and tpl["valid"] is True
        assert tpl["tasks"][0]["body"] == "<b>x</b>"  # plain text stays text
        assert tpl["defaults"]["duration_minutes"] == 60

        again = await admin.post(base, json=body)
        assert again.status_code == 409 and again.json()["detail"]["code"] == "template_name_exists"

        bad = await admin.post(base, json={**body, "name": "chg94 b", "is_standard": True})
        assert bad.status_code == 422
        assert bad.json()["detail"]["code"] == "template_standard_type"

        got = await admin.get(f"{base}/{tpl['id']}")
        assert got.status_code == 200 and got.json()["id"] == tpl["id"]
        listed = await admin.get(base)
        assert [t["id"] for t in listed.json()] == [tpl["id"]]

        put = await admin.put(
            f"{base}/{tpl['id']}", json={**body, "name": "chg94 Rollout 2", "valid": False}
        )
        assert put.status_code == 200 and put.json()["valid"] is False
        assert (await admin.get(f"{base}/946599")).status_code == 404

    # agents see valid templates only, as summaries without task bodies
    async with make_client(U_FULL, "chg94-full") as agent:
        summaries = await agent.get("/api/v1/itsm/change-templates")
        assert summaries.status_code == 200 and summaries.json() == []

    async with make_client(ADMIN_ID, "root@localhost") as admin:
        await admin.put(f"{base}/{tpl['id']}", json={**body, "name": "chg94 Rollout 2"})
    async with make_client(U_FULL, "chg94-full") as agent:
        summaries = (await agent.get("/api/v1/itsm/change-templates")).json()
        assert [(s["id"], s["task_count"], s["is_standard"]) for s in summaries] == [
            (tpl["id"], 1, False)
        ]
        assert "tasks" not in summaries[0]
        assert summaries[0]["defaults"]["queue_id"] == Q_A

    async with make_client(ADMIN_ID, "root@localhost") as admin:
        assert (await admin.delete(f"{base}/{tpl['id']}")).status_code == 204
        assert (await admin.delete(f"{base}/{tpl['id']}")).status_code == 404


# --- instantiate -----------------------------------------------------------------------------


async def test_create_from_template_prefills_and_enqueues_tasks(w: ChangeWorld) -> None:
    await _question(w)
    tid = await _make(
        w,
        defaults={
            "queue_id": Q_A,
            "service_ids": [SERVICE, SERVICE_B],
            "cab_id": CAB,
            "reason": "Geplanter Austausch",
            "implementation_plan": "Switch tauschen",
            "backout_plan": "Alten Switch einbauen",
            "test_plan": "Ping-Test",
            "duration_minutes": 90,
            "portal_visible": True,
            "risk_answers": {"chg94-rq": "no"},
        },
        tasks=[
            {"key": "prep", "title": "Vorbereiten", "offset_minutes": -30},
            {"key": "swap", "title": "Tauschen", "duration_minutes": 60},
        ],
    )
    # no queue in the input: the template decides; explicit reason wins over the default
    ticket_id, job_id = await _create(
        w,
        queue_id=None,
        template_id=tid,
        planned_start=START,
        reason="Eigener Grund",
    )
    row = (
        await w.session.execute(
            select(TiqoraItsmChange).where(TiqoraItsmChange.ticket_id == ticket_id)
        )
    ).scalar_one()
    assert row.reason == "Eigener Grund"
    assert (row.implementation_plan, row.backout_plan, row.test_plan) == (
        "Switch tauschen",
        "Alten Switch einbauen",
        "Ping-Test",
    )
    assert (row.cab_id, row.template_id, row.portal_visible) == (CAB, tid, True)
    assert row.planned_end == datetime(2026, 11, 7, 21, 30)  # start + duration
    queue_id = (
        await w.session.execute(text("SELECT queue_id FROM ticket WHERE id = :i"), {"i": ticket_id})
    ).scalar_one()
    assert queue_id == Q_A
    extra = (
        await w.session.execute(
            text("SELECT service_id FROM tiqora_itsm_change_service WHERE ticket_id = :i"),
            {"i": ticket_id},
        )
    ).scalars()
    assert sorted(extra) == [SERVICE_B]  # the first service is the ticket's own
    service_id = (
        await w.session.execute(
            text("SELECT service_id FROM ticket WHERE id = :i"), {"i": ticket_id}
        )
    ).scalar_one()
    assert service_id == SERVICE
    answers = (
        (
            await w.session.execute(
                select(TiqoraItsmChangeRiskAnswer).where(
                    TiqoraItsmChangeRiskAnswer.ticket_id == ticket_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(a.question_key, a.answer_key, a.points, a.answered_by) for a in answers] == [
        ("chg94-rq", "no", 8, U_FULL)
    ]

    # the tasks are a job, not part of the request
    assert job_id is not None
    job = (
        await w.session.execute(
            text("SELECT kind, status, payload FROM tiqora_job WHERE id = :i"), {"i": job_id}
        )
    ).one()
    assert (job[0], job[1]) == ("core.itsm_change_tasks", "queued")
    assert json.loads(job[2]) == {
        "change_ticket_id": ticket_id,
        "template_id": tid,
        "user_id": U_FULL,
    }
    count = (
        await w.session.execute(
            text("SELECT count(*) FROM tiqora_itsm_change_task WHERE change_ticket_id = :i"),
            {"i": ticket_id},
        )
    ).scalar_one()
    assert count == 0


async def test_explicit_input_wins_over_template_defaults(w: ChangeWorld) -> None:
    defaults = TemplateDefaults.model_validate(
        {
            "queue_id": Q_A,
            "service_ids": [SERVICE],
            "cab_id": CAB,
            "reason": "Vorlage",
            "duration_minutes": 60,
        }
    )
    base = ChangeCreateIn.model_validate(
        {"title": "x", "change_type": "normal", "queue_id": Q_B, "reason": "Eigen"}
    )
    tpl = _view_stub(defaults)
    out = apply_template_defaults(base, tpl)
    assert (out.queue_id, out.reason) == (Q_B, "Eigen")  # explicit
    # not given: the template's; its first service becomes the ticket's own
    assert (out.cab_id, out.service_id, out.service_ids) == (CAB, SERVICE, [])
    # a window with both ends keeps both; only a start gets the duration
    both = ChangeCreateIn.model_validate(
        {
            "title": "x",
            "change_type": "normal",
            "queue_id": Q_A,
            "planned_start": START,
            "planned_end": datetime(2026, 11, 7, 21, 0),
        }
    )
    assert apply_template_defaults(both, tpl).planned_end == datetime(2026, 11, 7, 21, 0)
    # an explicit empty service list is an explicit choice
    none = ChangeCreateIn.model_validate(
        {"title": "x", "change_type": "normal", "queue_id": Q_A, "service_ids": []}
    )
    assert apply_template_defaults(none, tpl).service_ids == []


def _view_stub(defaults: TemplateDefaults) -> Any:
    from tiqora.itsm.change.templates import TemplateView

    return TemplateView(
        id=1,
        name="stub",
        description=None,
        change_type="normal",
        is_standard=False,
        valid=True,
        broken=False,
        defaults=defaults,
        tasks=[],
        created_at=datetime(2026, 10, 1),
        created_by=1,
        updated_at=datetime(2026, 10, 1),
        updated_by=1,
    )


async def test_standard_change_requires_standard_template(w: ChangeWorld) -> None:
    normal = await _make(w)
    standard = await _make(
        w,
        name="chg94 Standard",
        change_type="standard",
        is_standard=True,
        tasks=[{"key": "run", "title": "Ausführen"}],
    )
    inactive = await _make(w, name="chg94 Alt", change_type="standard", is_standard=True)
    await update_template(
        w.session,
        APP_SETTINGS,
        template_id=inactive,
        data=_in(name="chg94 Alt", change_type="standard", is_standard=True, valid=False),
        user_id=ADMIN_ID,
    )
    with pytest.raises(ChangeError) as plain:
        await _create(w, change_type="standard", template_id=normal)
    assert plain.value.error_code == "standard_needs_template"
    with pytest.raises(ChangeError) as off:
        await _create(w, change_type="standard", template_id=inactive)
    assert off.value.error_code == "template_not_found"
    ticket_id, job_id = await _create(w, change_type="standard", template_id=standard)
    assert job_id is not None
    row = await w.session.get(TiqoraItsmChange, ticket_id)
    assert row is not None and (row.change_type, row.template_id) == ("standard", standard)


async def test_stale_risk_answers_skipped_with_warning(
    w: ChangeWorld, caplog: pytest.LogCaptureFixture
) -> None:
    question = await _question(w)
    tid = await _make(
        w,
        defaults={"service_ids": [SERVICE], "risk_answers": {"chg94-rq": "no"}, "cab_id": CAB},
    )
    # the world moves on after the template was saved
    question.valid = False
    cab = await w.session.get(TiqoraItsmCab, CAB)
    assert cab is not None
    cab.valid = False
    await w.session.execute(text("UPDATE service SET valid_id = 2 WHERE id = :i"), {"i": SERVICE})
    await w.session.flush()

    with caplog.at_level(logging.WARNING):
        ticket_id, job_id = await _create(w, template_id=tid)
    assert job_id is None  # a template without tasks queues nothing
    answers = (
        (
            await w.session.execute(
                select(TiqoraItsmChangeRiskAnswer).where(
                    TiqoraItsmChangeRiskAnswer.ticket_id == ticket_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert answers == []
    row = await w.session.get(TiqoraItsmChange, ticket_id)
    assert row is not None and row.cab_id is None
    event = (
        await w.session.execute(
            select(TiqoraItsmChangeEvent)
            .where(TiqoraItsmChangeEvent.ticket_id == ticket_id)
            .where(TiqoraItsmChangeEvent.event == "created")
        )
    ).scalar_one()
    detail = json.loads(event.detail or "{}")
    assert sorted(detail["template_warnings"]) == [
        f"cab_not_found:{CAB}",
        "risk_answer_stale:chg94-rq",
        f"service_not_found:{SERVICE}",
    ]
    assert any(r.getMessage() == "change_template_stale_defaults" for r in caplog.records)


async def test_delete_template_keeps_changes(w: ChangeWorld) -> None:
    tid = await _make(w)
    ticket_id, _ = await _create(w, template_id=tid)
    await delete_template(w.session, template_id=tid)
    row = (
        await w.session.execute(
            select(TiqoraItsmChange)
            .where(TiqoraItsmChange.ticket_id == ticket_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert row.template_id is None
    assert await w.session.get(TiqoraItsmChangeTemplate, tid) is None
    with pytest.raises(ChangeError) as gone:
        await delete_template(w.session, template_id=tid)
    assert (gone.value.error_code, gone.value.http_status) == ("template_not_found", 404)


async def test_patch_change_type_checks_template_type(w: ChangeWorld) -> None:
    normal = await _make(w)
    ticket_id, _ = await _create(w, template_id=normal)
    with pytest.raises(ChangeError) as mismatch:
        await update_change(
            w.session,
            APP_SETTINGS,
            SysConfig(w.session),
            ticket_id=ticket_id,
            data=ChangeUpdateIn(change_type="emergency"),
            user_id=U_FULL,
        )
    assert (mismatch.value.error_code, mismatch.value.http_status) == (
        "template_type_mismatch",
        422,
    )
    row = await w.session.get(TiqoraItsmChange, ticket_id)
    assert row is not None and row.change_type == "normal"
    # the same type is no change at all
    await update_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=ticket_id,
        data=ChangeUpdateIn(change_type="normal", reason="ok"),
        user_id=U_FULL,
    )
    # without a template (or after it was deleted) the type may move
    await delete_template(w.session, template_id=normal)
    await w.session.refresh(row)
    view = await update_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        ticket_id=ticket_id,
        data=ChangeUpdateIn(change_type="emergency"),
        user_id=U_FULL,
    )
    assert view.record.change_type == "emergency"
