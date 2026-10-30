"""Change risk: questionnaire, answers, core checks, evaluation, override, API (both dialects).

Seed band 946900-946999: services 946930-946932, change ticket ids 946950-946959 (closed changes
of the failed-history check, no ticket rows needed: the record references the ticket by value).
Change tickets are created by the change service, incident tickets are bare Znuny rows (both
autoincrement, rolled back with the world). Users, queues and the world are the shared ones of
``tests._change_fixtures`` (``Q_B`` is out of reach of ``U_FULL``).
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import AsyncIterator, Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from tests._change_fixtures import (
    APP_SETTINGS,
    G_A,
    G_B,
    Q_A,
    Q_B,
    U_FULL,
    U_MANAGER,
    U_NONE,
    U_RO,
    URL_FIXTURES,
    ChangeWorld,
    change_world,
    make_change,
)
from tests._cmdb_helpers import make_ci, make_class
from tiqora.api.app import create_app
from tiqora.api.deps import get_current_user, get_db, get_session_factory_dep
from tiqora.cmdb.seed import seed_defaults
from tiqora.config import Settings
from tiqora.domain.auth import AuthenticatedUser
from tiqora.extensions import Registry, set_registry_for_tests
from tiqora.extensions.core import register_core
from tiqora.itsm.change import conflicts as conflicts_module
from tiqora.itsm.change.errors import ChangeError
from tiqora.itsm.change.freeze import FreezeIn, FreezeOccurrences, create_freeze
from tiqora.itsm.change.models import (
    TiqoraItsmChange,
    TiqoraItsmChangeEvent,
    TiqoraItsmChangeImpact,
)
from tiqora.itsm.change.risk import (
    CHANGE_RISK_CHECKS,
    ChangeRiskQuestionIn,
    RiskCheck,
    RiskEvaluation,
    RiskFinding,
    RiskInput,
    core_risk_checks,
    create_question,
    delete_question,
    evaluate_risk,
    insert_examples,
    list_questions,
    override_level,
    questionnaire_complete,
    read_findings,
    reorder_questions,
    risk_input,
    score_level,
    set_answers,
    update_question,
)
from tiqora.itsm.change.service import ChangeCreateIn, create_change
from tiqora.itsm.change.settings import ItsmChangeSettings
from tiqora.itsm.models import TiqoraItsmServiceMeta
from tiqora.jobs.service import utc_now
from tiqora.znuny.sysconfig import SysConfig

pytestmark = pytest.mark.db

SVC_A, SVC_B, SVC_C = 946930, 946931, 946932
CLOSED_1, CLOSED_2, CLOSED_3 = 946950, 946951, 946952
WIN = (datetime(2026, 11, 10, 8), datetime(2026, 11, 10, 12))
ADMIN_ID = 1
ADMIN_QUESTIONS = "/api/v1/admin/itsm/change/risk-questions"
TICKET_NO_BASE = 946920


@pytest.fixture(params=URL_FIXTURES)
async def w(request: pytest.FixtureRequest) -> AsyncIterator[ChangeWorld]:
    async with change_world(request, str(request.param)) as world:
        await seed_defaults(world.session)
        for sid, name in ((SVC_A, "a"), (SVC_B, "b"), (SVC_C, "c")):
            await world.session.execute(
                text(
                    "INSERT INTO service (id, name, valid_id, create_time, create_by, change_time,"
                    " change_by) VALUES (:id, :n, 1, :t, 1, :t, 1)"
                ),
                {"id": sid, "n": f"chg94-risk-service-{name}", "t": datetime(2026, 10, 1)},
            )
        yield world


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


# --- helpers ---------------------------------------------------------------------------------


async def _change(
    w: ChangeWorld,
    *,
    queue: int = Q_A,
    change_type: str = "normal",
    phase: str = "assessment",
    window: tuple[datetime, datetime] | None = None,
    services: Sequence[int] = (),
    cis: Sequence[int] = (),
    impacted_services: Sequence[int] = (),
    impacted_cis: Sequence[int] = (),
    backout: str | None = "Zurück auf Stand 1",
) -> int:
    data = ChangeCreateIn.model_validate(
        {"title": "Switch-Tausch", "change_type": change_type, "queue_id": Q_A}
    )
    if change_type == "standard":
        raise AssertionError("standard changes need a template; not needed here")
    view, _ = await create_change(
        w.session,
        APP_SETTINGS,
        SysConfig(w.session),
        data=data,
        user_id=U_FULL,
        session_factory=w.factory,
    )
    tid = view.record.ticket_id
    if queue != Q_A:
        await w.session.execute(
            text("UPDATE ticket SET queue_id = :q WHERE id = :t"), {"q": queue, "t": tid}
        )
    row = await w.session.get(TiqoraItsmChange, tid)
    assert row is not None
    row.phase = phase
    row.backout_plan = backout
    if window is not None:
        row.planned_start, row.planned_end = window
    await w.session.execute(
        text("DELETE FROM tiqora_itsm_change_impact WHERE ticket_id = :t"), {"t": tid}
    )
    rows = [
        (kind, ids, direct)
        for kind, ids, direct in (
            ("service", services, True),
            ("ci", cis, True),
            ("service", impacted_services, False),
            ("ci", impacted_cis, False),
        )
    ]
    w.session.add_all(
        TiqoraItsmChangeImpact(ticket_id=tid, kind=kind, ref_key=str(i), direct=direct)
        for kind, ids, direct in rows
        for i in ids
    )
    await w.session.flush()
    return tid


async def _ticket(
    w: ChangeWorld,
    *,
    queue_id: int,
    type_name: str,
    state: str,
    service_id: int | None,
    created: datetime | None = None,
) -> int:
    """A bare Znuny ticket row (auto id) for the incident cases."""
    tn = f"{TICKET_NO_BASE}{time.time_ns()}"
    type_id = (
        await w.session.execute(
            text("SELECT id FROM ticket_type WHERE name = :n"), {"n": type_name}
        )
    ).scalar_one_or_none()
    if type_id is None:
        type_id = (
            int((await w.session.execute(text("SELECT MAX(id) FROM ticket_type"))).scalar_one()) + 1
        )
        await w.session.execute(
            text(
                "INSERT INTO ticket_type (id, name, valid_id, create_time, create_by,"
                " change_time, change_by) VALUES (:i, :n, 1, :t, 1, :t, 1)"
            ),
            {"i": type_id, "n": type_name, "t": datetime(2026, 10, 1)},
        )
    state_id = (
        await w.session.execute(text("SELECT id FROM ticket_state WHERE name = :n"), {"n": state})
    ).scalar_one()
    when = created or utc_now()
    await w.session.execute(
        text(
            "INSERT INTO ticket (tn, title, queue_id, ticket_lock_id, type_id, service_id,"
            " user_id, responsible_user_id, ticket_priority_id, ticket_state_id, timeout,"
            " until_time, escalation_time, escalation_update_time, escalation_response_time,"
            " escalation_solution_time, archive_flag, create_time, create_by, change_time,"
            " change_by) VALUES (:tn, :title, :q, 1, :ty, :svc, 1, 1, 3, :s, 0, 0, 0, 0, 0,"
            " 0, 0, :t, 1, :t, 1)"
        ),
        {
            "tn": tn,
            "title": f"chg94 incident {tn}",
            "q": queue_id,
            "ty": type_id,
            "svc": service_id,
            "s": state_id,
            "t": when,
        },
    )
    return int(
        (
            await w.session.execute(text("SELECT id FROM ticket WHERE tn = :tn"), {"tn": tn})
        ).scalar_one()
    )


async def _meta(w: ChangeWorld, service_id: int, criticality: str) -> None:
    w.session.add(
        TiqoraItsmServiceMeta(
            service_id=service_id,
            criticality=criticality,
            change_time=datetime(2026, 10, 1),
            change_by=1,
        )
    )
    await w.session.flush()


async def _ci(w: ChangeWorld, name: str, *, group_id: int, incident: str = "operational") -> int:
    key = f"chg94risk{group_id}"
    class_id = (
        await w.session.execute(
            text("SELECT id FROM tiqora_cmdb_class WHERE class_key = :k"), {"k": key}
        )
    ).scalar_one_or_none() or await make_class(w.session, key=key, group_id=group_id)
    return await make_ci(w.session, int(class_id), name, user_id=U_FULL, incident=incident)


async def _row(w: ChangeWorld, ticket_id: int) -> TiqoraItsmChange:
    row = await w.session.get(TiqoraItsmChange, ticket_id)
    assert row is not None
    await w.session.refresh(row)
    return row


async def _events(w: ChangeWorld, ticket_id: int, event: str) -> list[TiqoraItsmChangeEvent]:
    got = await w.session.execute(
        select(TiqoraItsmChangeEvent)
        .where(TiqoraItsmChangeEvent.ticket_id == ticket_id, TiqoraItsmChangeEvent.event == event)
        .order_by(TiqoraItsmChangeEvent.id)
    )
    return list(got.scalars())


def _question(key: str = "q1", **fields: Any) -> ChangeRiskQuestionIn:
    body: dict[str, Any] = {
        "question_key": key,
        "question": "Ist ein Ausfall zu erwarten?",
        "answers": [
            {"key": "no", "label": "Nein", "points": 0},
            {"key": "yes", "label": "Ja", "points": 4},
        ],
        **fields,
    }
    return ChangeRiskQuestionIn.model_validate(body)


async def _check(w: ChangeWorld, tid: int, name: str) -> list[RiskFinding]:
    inp = await risk_input(w.session, APP_SETTINGS, ticket_id=tid)
    check = next(c for c in core_risk_checks() if c.key == f"core.{name}")
    return list(await check.run(w.session, inp))


async def _code(coro: Any) -> tuple[str, int]:
    with pytest.raises(ChangeError) as exc:
        await coro
    return exc.value.error_code, exc.value.http_status


# --- pure ------------------------------------------------------------------------------------


def test_score_level_thresholds() -> None:
    cfg = ItsmChangeSettings()
    assert (cfg.risk_medium_from, cfg.risk_high_from, cfg.risk_very_high_from) == (10, 20, 35)
    got = {s: score_level(s, cfg) for s in (0, 9, 10, 19, 20, 34, 35, 400)}
    assert got == {
        0: "low",
        9: "low",
        10: "medium",
        19: "medium",
        20: "high",
        34: "high",
        35: "very_high",
        400: "very_high",
    }
    custom = ItsmChangeSettings(risk_medium_from=2, risk_high_from=3, risk_very_high_from=4)
    assert [score_level(s, custom) for s in (1, 2, 3, 4)] == ["low", "medium", "high", "very_high"]


def test_risk_question_input_is_validated() -> None:
    with pytest.raises(ValueError):
        _question(answers=[{"key": "only", "label": "Eine", "points": 0}])
    with pytest.raises(ValueError):
        _question(
            answers=[
                {"key": "a", "label": "A", "points": 1},
                {"key": "a", "label": "B", "points": 2},
            ]
        )
    with pytest.raises(ValueError):
        _question("Bad Key")
    with pytest.raises(ValueError):
        _question(question="   ")


# --- questionnaire ---------------------------------------------------------------------------


async def test_question_crud_and_examples_insert_if_absent(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    mine = await create_question(
        w.session, data=_question("downtime", question="Eigene Frage"), user_id=1
    )
    other = await create_question(w.session, data=_question("zzz"), user_id=1)
    assert (mine.position, other.position) == (0, 1)
    assert await _code(create_question(w.session, data=_question("zzz"), user_id=1)) == (
        "risk_question_key_exists",
        409,
    )

    created = await insert_examples(w.session, user_id=1)
    assert created == ["backout_tested", "users_affected", "experience"]
    again = await insert_examples(w.session, user_id=1)
    assert again == []
    listed = await list_questions(w.session)
    assert [q.question_key for q in listed] == [
        "downtime",
        "zzz",
        "backout_tested",
        "users_affected",
        "experience",
    ]
    assert listed[0].question == "Eigene Frage"  # an existing key is never overwritten

    changed = _question("zzz", question="Neu formuliert", valid=False, help="Hilfe")
    updated = await update_question(w.session, question_id=other.id, data=changed, user_id=1)
    assert (updated.question, updated.valid, updated.help) == ("Neu formuliert", False, "Hilfe")
    assert await _code(
        update_question(w.session, question_id=other.id, data=_question("other"), user_id=1)
    ) == ("risk_question_key_immutable", 422)
    assert await _code(update_question(w.session, question_id=946999, data=changed, user_id=1)) == (
        "risk_question_not_found",
        404,
    )
    assert [q.question_key for q in await list_questions(w.session, include_invalid=False)] == [
        "downtime",
        "backout_tested",
        "users_affected",
        "experience",
    ]

    ids = [q.id for q in listed]
    reordered = await reorder_questions(w.session, order=list(reversed(ids)))
    assert [q.id for q in reordered] == list(reversed(ids))
    assert [q.id for q in await list_questions(w.session)] == list(reversed(ids))
    assert await _code(reorder_questions(w.session, order=ids[:-1])) == (
        "risk_question_order_mismatch",
        422,
    )

    await delete_question(w.session, question_id=other.id)
    assert "zzz" not in [q.question_key for q in await list_questions(w.session)]
    assert await _code(delete_question(w.session, question_id=other.id)) == (
        "risk_question_not_found",
        404,
    )

    # the same through the admin API (admins only; the fixed paths win over ``{id}``)
    async with make_client(U_FULL, "chg94-full") as agent:
        assert (await agent.get(ADMIN_QUESTIONS)).status_code == 403
        assert (await agent.post(f"{ADMIN_QUESTIONS}/examples")).status_code == 403
    async with make_client(ADMIN_ID, "root@localhost") as admin:
        got = await admin.get(ADMIN_QUESTIONS)
        assert got.status_code == 200
        ordered = [q["id"] for q in got.json()]
        moved = await admin.put(f"{ADMIN_QUESTIONS}/order", json={"order": ordered[::-1]})
        assert moved.status_code == 200
        assert [q["id"] for q in moved.json()] == ordered[::-1]
        bad = await admin.put(f"{ADMIN_QUESTIONS}/order", json={"order": ordered[:1]})
        assert (
            bad.status_code == 422
            and bad.json()["detail"]["code"] == "risk_question_order_mismatch"
        )
        ex = await admin.post(f"{ADMIN_QUESTIONS}/examples")
        assert ex.status_code == 201 and ex.json() == {"created": []}
        post = await admin.post(
            ADMIN_QUESTIONS,
            json={
                "question_key": "chg94_api",
                "question": "Wie viele Standorte?",
                "answers": [
                    {"key": "one", "label": "Einer", "points": 0},
                    {"key": "many", "label": "Mehrere", "points": 3},
                ],
            },
        )
        assert post.status_code == 201, post.text
        qid = post.json()["id"]
        assert post.json()["valid"] is True
        dup = await admin.post(
            ADMIN_QUESTIONS, json={**post.json(), "answers": post.json()["answers"]}
        )
        assert dup.status_code == 409
        put = await admin.put(
            f"{ADMIN_QUESTIONS}/{qid}",
            json={**post.json(), "question": "Wie viele Standorte sind es?"},
        )
        assert put.status_code == 200 and put.json()["question"].endswith("sind es?")
        assert (
            await admin.put(f"{ADMIN_QUESTIONS}/{qid}", json={**post.json(), "question_key": "x"})
        ).status_code == 422
        assert (await admin.delete(f"{ADMIN_QUESTIONS}/{qid}")).status_code == 204
        assert (await admin.delete(f"{ADMIN_QUESTIONS}/{qid}")).status_code == 404


async def test_answers_validated_against_questions(w: ChangeWorld) -> None:
    await create_question(w.session, data=_question("q1"), user_id=1)
    await create_question(
        w.session,
        data=_question(
            "q2",
            answers=[
                {"key": "a", "label": "A", "points": 2},
                {"key": "b", "label": "B", "points": -1},
            ],
        ),
        user_id=1,
    )
    off = await create_question(w.session, data=_question("q3", valid=False), user_id=1)
    assert off.valid is False
    tid = await _change(w)

    async def put(answers: dict[str, str], user: int = U_FULL) -> Any:
        return await set_answers(
            w.session, APP_SETTINGS, ticket_id=tid, answers=answers, user_id=user
        )

    assert await _code(put({"nope": "yes"})) == ("risk_question_unknown", 422)
    assert await _code(put({"q3": "yes"})) == ("risk_question_unknown", 422)  # invalid question
    assert await _code(put({"q1": "maybe"})) == ("risk_answer_unknown", 422)
    assert await _code(put({"q1": "a"})) == ("risk_answer_unknown", 422)  # another question's key
    assert await _code(put({"q1": "yes"}, U_RO)) == ("forbidden", 403)
    assert await _code(put({"q1": "yes"}, U_NONE)) == ("ticket_not_found", 404)
    assert await questionnaire_complete(w.session, ticket_id=tid) is False

    ev = await put({"q1": "yes"})
    assert isinstance(ev, RiskEvaluation)
    assert await questionnaire_complete(w.session, ticket_id=tid) is False
    assert (await risk_input(w.session, APP_SETTINGS, ticket_id=tid)).answer_points == 4

    await put({"q1": "yes", "q2": "b"})
    inp = await risk_input(w.session, APP_SETTINGS, ticket_id=tid)
    assert dict(inp.answers) == {"q1": "yes", "q2": "b"} and inp.answer_points == 3
    assert await questionnaire_complete(w.session, ticket_id=tid) is True

    # a PUT replaces the set: the question left out is unanswered again
    await put({"q2": "a"})
    inp = await risk_input(w.session, APP_SETTINGS, ticket_id=tid)
    assert dict(inp.answers) == {"q2": "a"} and inp.answer_points == 2
    assert await questionnaire_complete(w.session, ticket_id=tid) is False

    # points follow the current definition; an answer whose option vanished does not count
    await update_question(
        w.session,
        question_id=(await list_questions(w.session))[1].id,
        data=_question(
            "q2",
            answers=[
                {"key": "a", "label": "A", "points": 9},
                {"key": "c", "label": "C", "points": 0},
            ],
        ),
        user_id=1,
    )
    assert (await risk_input(w.session, APP_SETTINGS, ticket_id=tid)).answer_points == 9
    await update_question(
        w.session,
        question_id=(await list_questions(w.session))[1].id,
        data=_question(
            "q2",
            answers=[
                {"key": "c", "label": "C", "points": 1},
                {"key": "d", "label": "D", "points": 0},
            ],
        ),
        user_id=1,
    )
    assert dict((await risk_input(w.session, APP_SETTINGS, ticket_id=tid)).answers) == {}

    # A-M10: a question with an answer is not deleted (the score of the change came from it);
    # the admin retires it instead
    answered = next(q for q in await list_questions(w.session) if q.question_key == "q2")
    assert await _code(delete_question(w.session, question_id=answered.id)) == (
        "risk_question_in_use",
        409,
    )
    await update_question(
        w.session,
        question_id=answered.id,
        data=_question(
            "q2",
            valid=False,
            answers=[
                {"key": "c", "label": "C", "points": 1},
                {"key": "d", "label": "D", "points": 0},
            ],
        ),
        user_id=1,
    )

    # no valid question at all: nothing to answer, so the questionnaire is complete
    for q in await list_questions(w.session):
        if q.id != answered.id:
            await delete_question(w.session, question_id=q.id)
    assert await questionnaire_complete(w.session, ticket_id=tid) is True

    row = await _row(w, tid)
    row.phase = "closed"
    await w.session.flush()
    assert await _code(put({})) == ("change_final", 409)


# --- core checks -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "check",
    [
        "service_criticality",
        "ci_incident",
        "open_incidents",
        "overlap",
        "freeze",
        "failed_history",
        "emergency",
        "missing_backout",
    ],
)
async def test_core_check(check: str, w: ChangeWorld) -> None:
    cfg = ItsmChangeSettings()
    if check == "service_criticality":
        await _meta(w, SVC_A, "4 high")
        await _meta(w, SVC_B, "2 low")
        tid = await _change(w, services=[SVC_B], impacted_services=[SVC_A])
        (f,) = await _check(w, tid, check)
        assert (f.check_key, f.severity, f.points) == (
            "core.service_criticality",
            "warn",
            (4 - 2) * cfg.weight_service_criticality,
        )
        assert dict(f.params) == {"criticality": 4, "services": 1}
        assert f.refs == (f"service:{SVC_A}",)
        assert f.message_key == "itsm.change.risk.findings.service_criticality"
        await _meta(w, SVC_C, "5 very high")
        tid2 = await _change(w, services=[SVC_C, SVC_A])
        (g,) = await _check(w, tid2, check)
        assert g.points == 3 * cfg.weight_service_criticality and dict(g.params)["services"] == 2
        # medium (3) counts once, low (2) not at all
        await w.session.execute(
            text(
                "UPDATE tiqora_itsm_service_meta SET criticality = '3 normal'"
                " WHERE service_id <> :b"
            ),
            {"b": SVC_B},
        )
        (h,) = await _check(w, tid2, check)
        assert h.points == cfg.weight_service_criticality
        assert await _check(w, await _change(w, services=[SVC_B]), check) == []
        assert await _check(w, await _change(w), check) == []
    elif check == "ci_incident":
        bad = await _ci(w, "srv-db-01.example.org", group_id=G_A, incident="incident")
        ok = await _ci(w, "srv-db-02.example.org", group_id=G_A)
        tid = await _change(w, cis=[ok], impacted_cis=[bad])
        (f,) = await _check(w, tid, check)
        assert (f.severity, f.points) == ("warn", cfg.weight_ci_incident)
        assert dict(f.params) == {"incident": 1, "warning": 0}
        assert f.refs == (f"ci:{bad}",)
        assert await _check(w, await _change(w, cis=[ok]), check) == []
        # the derived state counts: a CI warned by propagation scores as well
        warn = await w.session.execute(
            text(
                "SELECT id FROM tiqora_cmdb_catalog WHERE functionality = 'warning'"
                " AND catalog_class = 'ITSM::Core::IncidentState'"
            )
        )
        await w.session.execute(
            text("UPDATE tiqora_cmdb_ci SET cur_incident_state_id = :s WHERE id = :i"),
            {"s": warn.scalar_one(), "i": ok},
        )
        (g,) = await _check(w, await _change(w, cis=[ok]), check)
        assert dict(g.params) == {"incident": 0, "warning": 1}
    elif check == "open_incidents":
        tid = await _change(w, services=[SVC_A], impacted_services=[SVC_B])
        mk = _ticket
        recent = await mk(w, queue_id=Q_A, type_name="Incident", state="open", service_id=SVC_A)
        major = await mk(
            w, queue_id=Q_A, type_name="Incident::Major", state="new", service_id=SVC_B
        )
        await mk(  # too old
            w,
            queue_id=Q_A,
            type_name="Incident",
            state="open",
            service_id=SVC_A,
            created=utc_now() - timedelta(days=cfg.incident_lookback_days + 2),
        )
        await mk(w, queue_id=Q_A, type_name="Incident", state="closed successful", service_id=SVC_A)
        await mk(w, queue_id=Q_A, type_name="Unclassified", state="open", service_id=SVC_A)
        await mk(w, queue_id=Q_A, type_name="Incident", state="open", service_id=SVC_C)
        (f,) = await _check(w, tid, check)
        assert (f.severity, f.points) == ("warn", cfg.weight_open_incidents)
        assert dict(f.params) == {"count": 2, "lookback_days": cfg.incident_lookback_days}
        assert f.refs == (f"ticket:{major}", f"ticket:{recent}")
        assert await _check(w, await _change(w, services=[SVC_C]), check) != []
        assert await _check(w, await _change(w, services=[946999]), check) == []
    elif check == "overlap":
        tid = await _change(w, services=[SVC_A], window=WIN)
        other = await _change(w, services=[SVC_A], window=WIN, phase="scheduled")
        await _change(w, services=[SVC_B], window=WIN, phase="scheduled")  # shares nothing
        await _change(w, services=[SVC_A], window=WIN, phase="draft")  # cannot collide
        (f,) = await _check(w, tid, check)
        assert (f.severity, f.points) == ("warn", cfg.weight_overlap)
        assert dict(f.params) == {"count": 1, "truncated": 0}
        assert f.refs == (f"ticket:{other}",)
        assert await _check(w, await _change(w, services=[SVC_C], window=WIN), check) == []
    elif check == "freeze":
        tid = await _change(w, services=[SVC_A], window=WIN)
        assert await _check(w, tid, check) == []
        for enforcement, severity in (("warn", "warn"), ("block", "block")):
            window = await create_freeze(
                w.session,
                data=FreezeIn.model_validate(
                    {
                        "name": "chg94-freeze",
                        "starts_at": (WIN[0] - timedelta(hours=1)).isoformat() + "Z",
                        "ends_at": (WIN[0] + timedelta(hours=1)).isoformat() + "Z",
                        "enforcement": enforcement,
                    }
                ),
                user_id=1,
            )
            (f,) = await _check(w, tid, check)
            assert (f.severity, f.points) == (severity, cfg.weight_freeze)
            assert dict(f.params)["count"] == (1 if enforcement == "warn" else 2)
            assert f"freeze:{window.id}" in f.refs
        assert dict(f.params) == {"count": 2, "blocking": 1, "truncated": 0}
    elif check == "failed_history":
        tid = await _change(w, services=[SVC_A], cis=[])
        now = utc_now()
        old = {"approval_round": 0}
        rows = (
            (CLOSED_1, "failed", now - timedelta(days=10), SVC_A, True),
            (CLOSED_2, "backed_out", now - timedelta(days=170), SVC_A, True),
            (
                CLOSED_3,
                "failed",
                now - timedelta(days=cfg.failed_change_lookback_days + 5),
                SVC_A,
                True,
            ),
        )
        for ticket_id, outcome, ended, service, direct in rows:
            await make_change(
                w.session, ticket_id, phase="closed", outcome=outcome, actual_end=ended, **old
            )
            w.session.add(
                TiqoraItsmChangeImpact(
                    ticket_id=ticket_id, kind="service", ref_key=str(service), direct=direct
                )
            )
        await make_change(w.session, 946953, phase="closed", outcome="successful", actual_end=now)
        await make_change(w.session, 946954, phase="closed", outcome="failed", actual_end=now)
        w.session.add_all(
            [
                TiqoraItsmChangeImpact(
                    ticket_id=946953, kind="service", ref_key=str(SVC_A), direct=True
                ),
                # reached by propagation only: not "sharing a direct service"
                TiqoraItsmChangeImpact(
                    ticket_id=946954, kind="service", ref_key=str(SVC_A), direct=False
                ),
            ]
        )
        await w.session.flush()
        (f,) = await _check(w, tid, check)
        assert (f.severity, f.points) == ("warn", cfg.weight_failed_history)
        assert dict(f.params) == {"count": 2, "lookback_days": cfg.failed_change_lookback_days}
        assert f.refs == (f"ticket:{CLOSED_2}", f"ticket:{CLOSED_1}")
        assert await _check(w, await _change(w, services=[SVC_C]), check) == []
    elif check == "emergency":
        (f,) = await _check(w, await _change(w, change_type="emergency"), check)
        assert (f.severity, f.points) == ("info", cfg.weight_emergency)
        assert await _check(w, await _change(w), check) == []
    else:
        tid = await _change(w, backout=None)
        (f,) = await _check(w, tid, check)
        assert (f.severity, f.points) == ("warn", cfg.weight_missing_backout)
        assert await _check(w, await _change(w, backout="  "), check) != []
        assert await _check(w, await _change(w, backout="Plan B"), check) == []
        (e,) = await _check(w, await _change(w, change_type="emergency", backout=None), check)
        assert e.points == cfg.weight_missing_backout
        # standard changes come from a template and need no back-out plan
        row = await _row(w, tid)
        row.change_type = "standard"
        await w.session.flush()
        assert await _check(w, tid, check) == []


async def test_overlap_truncated_still_scores(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(conflicts_module, "CONFLICT_CANDIDATE_CAP", 1)
    tid = await _change(w, services=[SVC_A], window=WIN)
    for _ in range(2):
        await _change(w, services=[SVC_A], window=WIN, phase="scheduled")
    (f,) = await _check(w, tid, "overlap")
    assert dict(f.params) == {"count": 1, "truncated": 1}
    monkeypatch.setattr(conflicts_module, "CONFLICT_CANDIDATE_CAP", 0)
    only_cut = await _check(w, tid, "overlap")
    assert [dict(x.params)["truncated"] for x in only_cut] == [
        1
    ]  # nothing examined is still a finding


async def test_truncated_freezes_block_the_change(
    w: ChangeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def cut(*_a: Any, **_k: Any) -> FreezeOccurrences:
        return FreezeOccurrences([], True)

    monkeypatch.setattr(conflicts_module, "occurrences", cut)
    tid = await _change(w, services=[SVC_A], window=WIN)
    ev = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    (f,) = [x for x in ev.findings if x.check_key == "core.freeze"]
    assert f.severity == "block"  # fail closed: freeze windows that were not all examined
    assert f.message_key.endswith("freeze_truncated")
    assert dict(f.params)["truncated"] == 1
    assert ev.blocked is True


# --- evaluation ------------------------------------------------------------------------------


async def test_evaluate_stores_score_level_findings(w: ChangeWorld) -> None:
    await create_question(w.session, data=_question("q1"), user_id=1)
    tid = await _change(w, change_type="emergency", backout=None)
    ev = await set_answers(
        w.session, APP_SETTINGS, ticket_id=tid, answers={"q1": "yes"}, user_id=U_FULL
    )
    # answer 4 + emergency 5 + missing back-out 3
    assert (ev.score, ev.level, ev.effective_level) == (12, "medium", "medium")
    assert [(f.check_key, f.points) for f in ev.findings] == [
        ("core.emergency", 5),
        ("core.missing_backout", 3),
    ]
    assert ev.blocked is False and ev.evaluated_at is not None

    row = await _row(w, tid)
    assert (row.risk_score, row.risk_level) == (12, "medium")
    assert row.risk_evaluated_at is not None and row.risk_override_level is None
    stored = row.risk_findings or ""
    assert '"core.emergency"' in stored and '"message_key"' in stored

    row.backout_plan = "Zurück auf Stand 1"
    await w.session.flush()
    again = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert (again.score, again.level) == (9, "low")
    assert [f.check_key for f in again.findings] == ["core.emergency"]
    assert (await _row(w, tid)).risk_score == 9

    # points can not go below zero, however mitigating the answers are
    await create_question(
        w.session,
        data=_question(
            "q2",
            answers=[
                {"key": "x", "label": "X", "points": -50},
                {"key": "y", "label": "Y", "points": 0},
            ],
        ),
        user_id=1,
    )
    low = await set_answers(
        w.session, APP_SETTINGS, ticket_id=tid, answers={"q1": "no", "q2": "x"}, user_id=U_FULL
    )
    assert low.score == 0 and low.level == "low"

    closed = await _change(w, phase="closed")
    assert await _code(evaluate_risk(w.session, APP_SETTINGS, ticket_id=closed, user_id=1)) == (
        "change_final",
        409,
    )


async def test_override_requires_manager_and_reason(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    tid = await _change(w, change_type="emergency")  # computed: 5 -> low
    await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)

    async def over(user: int, level: str | None, reason: str) -> Any:
        return await override_level(
            w.session, APP_SETTINGS, ticket_id=tid, level=level, reason=reason, user_id=user
        )

    assert await _code(over(U_FULL, "high", "weil es so ist")) == ("forbidden", 403)
    assert await _code(over(U_NONE, "high", "weil es so ist")) == ("ticket_not_found", 404)
    assert await _code(over(U_MANAGER, "high", "")) == ("risk_override_reason_required", 422)
    assert await _code(over(U_MANAGER, "high", "   ")) == ("risk_override_reason_required", 422)
    assert await _code(over(U_MANAGER, "extreme", "ok")) == ("risk_level_invalid", 422)
    assert await _code(over(U_MANAGER, "high", "x" * 1001)) == (
        "risk_override_reason_too_long",
        422,
    )
    assert (await _row(w, tid)).risk_override_level is None
    assert await _events(w, tid, "risk_override") == []

    ev = await over(U_MANAGER, "very_high", "Kritischer Kunde")
    assert (ev.level, ev.override_level, ev.effective_level) == ("low", "very_high", "very_high")
    row = await _row(w, tid)
    assert (row.risk_override_level, row.risk_override_reason, row.risk_override_by) == (
        "very_high",
        "Kritischer Kunde",
        U_MANAGER,
    )
    (event,) = await _events(w, tid, "risk_override")
    assert event.created_by == U_MANAGER
    assert '"reason": "Kritischer Kunde"' in (event.detail or "")

    # the override survives a new evaluation, which only moves the computed level
    again = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert (again.level, again.effective_level) == ("low", "very_high")

    cleared = await over(U_MANAGER, None, "Doch nicht kritisch")
    assert (cleared.override_level, cleared.effective_level) == (None, "low")
    row = await _row(w, tid)
    assert (row.risk_override_reason, row.risk_override_by) == (None, None)
    assert len(await _events(w, tid, "risk_override")) == 2

    async with make_client(U_FULL, "chg94-full") as agent:
        denied = await agent.put(
            f"/api/v1/itsm/changes/{tid}/risk/override", json={"level": "high", "reason": "x"}
        )
        assert denied.status_code == 403 and denied.json()["detail"]["code"] == "forbidden"
        assert (await agent.get(f"/api/v1/itsm/changes/{tid}/risk")).json()["can_override"] is False
    async with make_client(U_MANAGER, "chg94-manager") as manager:
        url = f"/api/v1/itsm/changes/{tid}/risk/override"
        missing = await manager.put(url, json={"level": "high"})
        assert missing.status_code == 422
        blank = await manager.put(url, json={"level": "high", "reason": " "})
        assert blank.status_code == 422
        assert blank.json()["detail"]["code"] == "risk_override_reason_required"
        ok = await manager.put(url, json={"level": "high", "reason": "Freigabe durch Leitung"})
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert (body["override_level"], body["effective_level"], body["can_override"]) == (
            "high",
            "high",
            True,
        )
        assert body["override_reason"] == "Freigabe durch Leitung"


# --- extension points ------------------------------------------------------------------------


def _with_checks(request: pytest.FixtureRequest, *checks: RiskCheck) -> None:
    fresh = Registry()
    register_core(fresh)
    for check in checks:
        fresh.add(CHANGE_RISK_CHECKS, check)
    fresh.freeze()
    set_registry_for_tests(fresh)
    request.addfinalizer(lambda: set_registry_for_tests(None))


async def test_extension_check_is_called(w: ChangeWorld, request: pytest.FixtureRequest) -> None:
    seen: list[RiskInput] = []

    async def extra(_session: Any, inp: RiskInput) -> list[RiskFinding]:
        seen.append(inp)
        return [
            RiskFinding(
                "test.extra", "block", 7, "itsm.change.risk.findings.test", {"n": 1}, ("ticket:1",)
            ),
            RiskFinding("", "bogus", -5, "itsm.change.risk.findings.test2", {}),  # type: ignore[arg-type]
        ]

    async def boom(session: Any, _inp: RiskInput) -> list[RiskFinding]:
        await session.execute(text("SELECT * FROM chg94_no_such_table"))  # aborts a PostgreSQL txn
        return []

    async def after(_session: Any, _inp: RiskInput) -> list[RiskFinding]:
        return [RiskFinding("test.after", "info", 1, "itsm.change.risk.findings.after", {})]

    _with_checks(
        request,
        RiskCheck("test.extra", extra),
        RiskCheck("test.boom", boom),
        RiskCheck("test.after", after),
    )
    tid = await _change(w, change_type="emergency")
    ev = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)

    assert len(seen) == 1 and seen[0].ticket_id == tid and seen[0].change_type == "emergency"
    keys = [f.check_key for f in ev.findings]
    # the eight core checks come first, then the extensions in registration order
    assert keys == ["core.emergency", "test.extra", "test.extra", "test.boom", "test.after"]
    block, clamped, failed, last = ev.findings[1:]
    assert (block.severity, block.points, block.refs) == ("block", 7, ("ticket:1",))
    assert (clamped.severity, clamped.points) == ("warn", 0)  # unknown severity / negative points
    assert (failed.severity, failed.points) == ("warn", 0) and failed.message_key.endswith(
        "check_failed"
    )
    assert last.points == 1
    assert ev.score == 5 + 7 + 0 + 0 + 1 and ev.blocked is True
    # the savepoint kept the transaction usable after the failing statement
    assert (await _row(w, tid)).risk_score == ev.score


async def test_risk_input_contract_fields(w: ChangeWorld) -> None:
    assert [f.name for f in dataclasses.fields(RiskFinding)] == [
        "check_key",
        "severity",
        "points",
        "message_key",
        "params",
        "refs",
    ]
    assert [f.name for f in dataclasses.fields(RiskInput)] == [
        "ticket_id",
        "change_type",
        "phase",
        "planned_start",
        "planned_end",
        "direct_ci_ids",
        "direct_service_ids",
        "impacted_ci_ids",
        "impacted_service_ids",
        "impact_truncated",
        "conflicts",
        "answers",
        "answer_points",
        "has_backout_plan",
        "settings",
        "incident_types",
    ]
    assert [f.name for f in dataclasses.fields(RiskCheck)] == ["key", "run"]
    assert CHANGE_RISK_CHECKS.key == "itsm.change_risk_checks"
    assert [c.key for c in core_risk_checks()] == [
        "core.service_criticality",
        "core.ci_incident",
        "core.open_incidents",
        "core.overlap",
        "core.freeze",
        "core.failed_history",
        "core.emergency",
        "core.missing_backout",
    ]

    await create_question(w.session, data=_question("q1"), user_id=1)
    ci_a = await _ci(w, "srv-app-01.example.org", group_id=G_A)
    ci_b = await _ci(w, "srv-app-02.example.org", group_id=G_A)
    tid = await _change(
        w,
        services=[SVC_A],
        impacted_services=[SVC_B],
        cis=[ci_a],
        impacted_cis=[ci_b],
        window=WIN,
        backout=None,
    )
    await set_answers(w.session, APP_SETTINGS, ticket_id=tid, answers={"q1": "yes"}, user_id=U_FULL)
    row = await _row(w, tid)
    row.impact_truncated = True
    await w.session.flush()

    inp = await risk_input(w.session, APP_SETTINGS, ticket_id=tid)
    assert inp.ticket_id == tid and inp.change_type == "normal" and inp.phase == "assessment"
    assert (inp.planned_start, inp.planned_end) == WIN
    assert inp.direct_service_ids == frozenset({SVC_A})
    assert inp.impacted_service_ids == frozenset({SVC_B})
    assert inp.direct_ci_ids == frozenset({ci_a}) and inp.impacted_ci_ids == frozenset({ci_b})
    assert inp.impact_truncated is True and inp.has_backout_plan is False
    assert dict(inp.answers) == {"q1": "yes"} and inp.answer_points == 4
    assert isinstance(inp.settings, ItsmChangeSettings)
    assert inp.conflicts == () and inp.incident_types == ("Incident", "Incident::Major")
    with pytest.raises(dataclasses.FrozenInstanceError):
        inp.phase = "closed"  # type: ignore[misc]


# --- API and visibility ----------------------------------------------------------------------


async def test_risk_api_roundtrip(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    await create_question(w.session, data=_question("q1", help="Hinweis"), user_id=1)
    await create_question(w.session, data=_question("q2", valid=False), user_id=1)
    tid = await _change(w, change_type="emergency", backout=None)
    base = f"/api/v1/itsm/changes/{tid}/risk"
    async with make_client(U_FULL, "chg94-full") as agent:
        first = await agent.get(base)
        assert first.status_code == 200, first.text
        body = first.json()
        assert [q["question_key"] for q in body["questions"]] == ["q1"]  # valid ones only
        assert body["questions"][0]["help"] == "Hinweis"
        assert (body["score"], body["level"], body["evaluated_at"], body["findings"]) == (
            None,
            None,
            None,
            [],
        )
        assert body["thresholds"] == {"medium_from": 10, "high_from": 20, "very_high_from": 35}
        assert body["questionnaire_complete"] is False
        assert (body["can_answer"], body["can_override"], body["blocked"]) == (True, False, False)

        put = await agent.put(f"{base}/answers", json={"answers": {"q1": "yes"}})
        assert put.status_code == 200, put.text
        out = put.json()
        assert (out["score"], out["level"], out["answer_points"]) == (12, "medium", 4)
        assert out["answers"] == {"q1": "yes"} and out["questionnaire_complete"] is True
        assert [f["check_key"] for f in out["findings"]] == [
            "core.emergency",
            "core.missing_backout",
        ]
        assert out["findings"][0]["hidden_ref_count"] == 0 and out["evaluated_at"].endswith(
            "+00:00"
        )

        bad = await agent.put(f"{base}/answers", json={"answers": {"q1": "maybe"}})
        assert bad.status_code == 422 and bad.json()["detail"]["code"] == "risk_answer_unknown"

        ev = await agent.post(f"{base}/evaluate")
        assert ev.status_code == 200 and ev.json()["score"] == 12

    async with make_client(U_RO, "chg94-ro") as reader:
        got = await reader.get(base)
        assert got.status_code == 200 and got.json()["can_answer"] is False
        assert (await reader.put(f"{base}/answers", json={"answers": {}})).status_code == 403
        assert (await reader.post(f"{base}/evaluate")).status_code == 403
    async with make_client(U_NONE, "chg94-none") as nobody:
        for call in (
            nobody.get(base),
            nobody.put(f"{base}/answers", json={"answers": {}}),
            nobody.post(f"{base}/evaluate"),
            nobody.put(f"{base}/override", json={"level": "low", "reason": "x"}),
        ):
            assert (await call).status_code == 404

    row = await _row(w, tid)
    row.phase = "cancelled"
    await w.session.flush()
    async with make_client(U_MANAGER, "chg94-manager") as manager:
        done = await manager.get(base)
        assert (done.json()["can_answer"], done.json()["can_override"]) == (False, False)
        assert (await manager.post(f"{base}/evaluate")).status_code == 409


async def test_risk_finding_lists_only_readable_incidents(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    await _meta(w, SVC_A, "4 high")
    tid = await _change(w, services=[SVC_A], window=WIN, change_type="emergency")
    visible = await _ticket(w, queue_id=Q_A, type_name="Incident", state="open", service_id=SVC_A)
    hidden = await _ticket(w, queue_id=Q_B, type_name="Incident", state="open", service_id=SVC_A)
    # a colliding change in a queue the caller cannot read
    secret_change = await _change(w, services=[SVC_A], window=WIN, phase="scheduled", queue=Q_B)
    open_cis = await _ci(w, "srv-db-09.example.org", group_id=G_A, incident="incident")
    secret_ci = await _ci(w, "srv-db-10.example.org", group_id=G_B, incident="incident")
    w.session.add_all(
        [
            TiqoraItsmChangeImpact(ticket_id=tid, kind="ci", ref_key=str(open_cis), direct=False),
            TiqoraItsmChangeImpact(ticket_id=tid, kind="ci", ref_key=str(secret_ci), direct=False),
        ]
    )
    await w.session.flush()
    await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)

    # the stored findings are complete: guards see everything
    stored = (await _row(w, tid)).risk_findings or ""
    assert f"ticket:{hidden}" in stored and f"ticket:{secret_change}" in stored
    assert f"ci:{secret_ci}" in stored

    out = await read_findings(w.session, ticket_id=tid, user_id=U_FULL)
    by_key = {f.check_key: f for f in out}
    incidents = by_key["core.open_incidents"]
    assert incidents.refs == [f"ticket:{visible}"] and incidents.hidden_ref_count == 1
    overlap = by_key["core.overlap"]
    assert overlap.refs == [] and overlap.hidden_ref_count == 1
    cis = by_key["core.ci_incident"]
    assert cis.refs == [f"ci:{open_cis}"] and cis.hidden_ref_count == 1
    assert by_key["core.service_criticality"].refs == [f"service:{SVC_A}"]  # linked directly

    async with make_client(U_FULL, "chg94-full") as agent:
        res = await agent.get(f"/api/v1/itsm/changes/{tid}/risk")
        assert res.status_code == 200
        text_body = res.text.replace(" ", "")
        for leaked in (f"ticket:{hidden}", f"ticket:{secret_change}", f"ci:{secret_ci}"):
            assert leaked not in text_body
        assert f"ticket:{visible}" in text_body
        counted = {f["check_key"]: f for f in res.json()["findings"]}
        assert counted["core.open_incidents"]["params"]["count"] == 2  # a number, not an id
    # once the caller may read the other queue and class, the same references appear
    for key in ("ro", "rw"):
        await w.session.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
            ),
            {"u": U_FULL, "g": G_B, "k": key, "t": datetime(2026, 10, 1)},
        )
    out = await read_findings(w.session, ticket_id=tid, user_id=U_FULL)
    full = {f.check_key: f for f in out}
    assert set(full["core.open_incidents"].refs) == {f"ticket:{visible}", f"ticket:{hidden}"}
    assert full["core.overlap"].refs == [f"ticket:{secret_change}"]
    assert set(full["core.ci_incident"].refs) == {f"ci:{open_cis}", f"ci:{secret_ci}"}
    assert full["core.open_incidents"].hidden_ref_count == 0

    # no read access to the change itself: nothing at all
    assert await _code(read_findings(w.session, ticket_id=tid, user_id=U_NONE)) == (
        "ticket_not_found",
        404,
    )


# --- review round 1 ----------------------------------------------------------------------------


async def test_incomplete_flag_for_failed_check_and_truncated_impact(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient], request: pytest.FixtureRequest
) -> None:
    tid = await _change(w)
    ev = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert (ev.level, ev.incomplete, ev.blocked) == ("low", False, False)

    row = await _row(w, tid)
    row.impact_truncated = True
    await w.session.flush()
    cut = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert cut.incomplete is True and cut.level == "low" and cut.blocked is False
    async with make_client(U_FULL, "chg94-full") as agent:
        body = (await agent.get(f"/api/v1/itsm/changes/{tid}/risk")).json()
        assert body["incomplete"] is True
    row.impact_truncated = False
    await w.session.flush()

    async def boom(_session: Any, _inp: RiskInput) -> list[RiskFinding]:
        raise RuntimeError("check broke")

    _with_checks(request, RiskCheck("test.boom", boom))
    failed = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert failed.incomplete is True and failed.blocked is False and failed.level == "low"
    async with make_client(U_FULL, "chg94-full") as agent:
        assert (await agent.get(f"/api/v1/itsm/changes/{tid}/risk")).json()["incomplete"] is True
    set_registry_for_tests(None)
    ok = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert ok.incomplete is False


async def test_extension_findings_are_bounded(
    w: ChangeWorld, request: pytest.FixtureRequest
) -> None:
    async def noisy(_session: Any, _inp: RiskInput) -> list[RiskFinding]:
        refs = tuple(f"ticket:{i}" for i in range(50)) + ("Titel mit Text", "ticket:abc", "x:1")
        params = {f"p{i}": i for i in range(30)} | {"text": "freier Text", "flag": True}
        one = RiskFinding("test.noisy", "info", 1, "m" * 500, params, refs)
        return [one] * 40

    _with_checks(request, RiskCheck("test.noisy", noisy))
    tid = await _change(w)
    ev = await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    found = [f for f in ev.findings if f.check_key == "test.noisy"]
    assert len(found) == 20
    first = found[0]
    assert first.refs == tuple(f"ticket:{i}" for i in range(10))
    assert len(first.params) == 10 and all(isinstance(v, int) for v in first.params.values())
    assert "text" not in first.params and "flag" not in first.params
    assert len(first.message_key) == 200


async def test_failed_history_is_limited(w: ChangeWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    from tiqora.itsm.change import risk as risk_module

    monkeypatch.setattr(risk_module, "FAILED_HISTORY_CAP", 2)
    tid = await _change(w, services=[SVC_A])
    for n in range(4):
        ticket_id = 946955 + n
        await make_change(
            w.session, ticket_id, phase="closed", outcome="failed", actual_end=utc_now()
        )
        w.session.add(
            TiqoraItsmChangeImpact(
                ticket_id=ticket_id, kind="service", ref_key=str(SVC_A), direct=True
            )
        )
    await w.session.flush()
    (f,) = await _check(w, tid, "failed_history")
    assert dict(f.params)["count"] == 2
    assert f.refs == ("ticket:946958", "ticket:946957")  # the newest ones


async def test_service_behind_unreadable_ci_is_not_named(
    w: ChangeWorld, make_client: Callable[[int, str], AsyncClient]
) -> None:
    await _meta(w, SVC_B, "5 very high")
    secret_ci = await _ci(w, "srv-db-11.example.org", group_id=G_B)
    tid = await _change(w, services=[SVC_A], impacted_services=[SVC_B], impacted_cis=[secret_ci])
    await evaluate_risk(w.session, APP_SETTINGS, ticket_id=tid, user_id=U_FULL)
    assert f"service:{SVC_B}" in ((await _row(w, tid)).risk_findings or "")

    (crit,) = [
        f
        for f in await read_findings(w.session, ticket_id=tid, user_id=U_FULL)
        if f.check_key == "core.service_criticality"
    ]
    assert crit.refs == [] and crit.hidden_ref_count == 1 and crit.params["criticality"] == 5
    async with make_client(U_FULL, "chg94-full") as agent:
        res = await agent.get(f"/api/v1/itsm/changes/{tid}/risk")
        assert f"service:{SVC_B}" not in res.text.replace(" ", "")

    # access to the class that holds the CI is not enough alone to name a service the CI
    # does not reach; with the class readable and the CI linked to the service it is named
    from tiqora.cmdb.links import LinkEnd, LinkService
    from tiqora.cmdb.service import system_access

    for key in ("ro", "rw"):
        await w.session.execute(
            text(
                "INSERT INTO group_user (user_id, group_id, permission_key, create_time,"
                " create_by, change_time, change_by) VALUES (:u, :g, :k, :t, 1, :t, 1)"
            ),
            {"u": U_FULL, "g": G_B, "k": key, "t": datetime(2026, 10, 1)},
        )
    await LinkService(w.session).add(
        system_access(U_FULL),
        source=LinkEnd("service", str(SVC_B)),
        target=LinkEnd("ci", str(secret_ci)),
        link_type_key="DependsOn",
        enforce_permissions=False,
    )
    (crit,) = [
        f
        for f in await read_findings(w.session, ticket_id=tid, user_id=U_FULL)
        if f.check_key == "core.service_criticality"
    ]
    assert crit.refs == [f"service:{SVC_B}"] and crit.hidden_ref_count == 0


async def test_answers_to_invalid_questions_survive_a_put(w: ChangeWorld) -> None:
    q1 = await create_question(w.session, data=_question("q1"), user_id=1)
    q2 = await create_question(w.session, data=_question("q2"), user_id=1)
    tid = await _change(w)
    await set_answers(
        w.session, APP_SETTINGS, ticket_id=tid, answers={"q1": "yes", "q2": "yes"}, user_id=U_FULL
    )
    await update_question(
        w.session, question_id=q2.id, data=_question("q2", valid=False), user_id=1
    )
    ev = await set_answers(
        w.session, APP_SETTINGS, ticket_id=tid, answers={"q1": "no"}, user_id=U_FULL
    )
    assert ev.score == 0  # the invalid question's answer is ignored in scoring
    kept = (
        (
            await w.session.execute(
                text(
                    "SELECT answer_key FROM tiqora_itsm_change_risk_answer WHERE ticket_id = :t"
                    " AND question_key = 'q2'"
                ),
                {"t": tid},
            )
        )
        .scalars()
        .all()
    )
    assert kept == ["yes"]
    # a question the PUT leaves out while it is valid is still cleared
    await set_answers(w.session, APP_SETTINGS, ticket_id=tid, answers={}, user_id=U_FULL)
    left = (
        (
            await w.session.execute(
                text(
                    "SELECT question_key FROM tiqora_itsm_change_risk_answer WHERE ticket_id = :t"
                ),
                {"t": tid},
            )
        )
        .scalars()
        .all()
    )
    assert left == ["q2"]
    # valid again: the old answer counts again
    await update_question(w.session, question_id=q2.id, data=_question("q2"), user_id=1)
    inp = await risk_input(w.session, APP_SETTINGS, ticket_id=tid)
    assert dict(inp.answers) == {"q2": "yes"} and inp.answer_points == 4
    assert q1.id != q2.id
